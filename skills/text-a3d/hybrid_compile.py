"""Compile source meshes in a semantic scene into physical mesh artifacts.

This is intentionally a small mesh compiler, not a universal CAD kernel.  It
loads ``recipe.parameters.sourceMesh`` for ordinary physical nodes and can
materialize tightly scoped shared build123d recipe outputs such as one aligned
self-tapping screw group. It evaluates semantic union/subtract operations with
Manifold and emits STL, part-colored 3MF, and a PBR physical GLB. It never
rescales geometry and never manufactures STEP; a part whose representation
master is B-rep keeps its STEP in the build123d pipeline and uses this compiler
only for mesh derivatives.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable
from itertools import permutations, product
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import trimesh
import lib3mf
from build123d import import_step
from trimesh.visual import TextureVisuals
from trimesh.visual.material import PBRMaterial

try:
    import manifold3d  # noqa: F401
except ModuleNotFoundError as error:  # pragma: no cover - environment failure
    manifold3d = None
    MANIFOLD_IMPORT_ERROR = error
else:
    MANIFOLD_IMPORT_ERROR = None

from interface_recipes import self_tapping_screw_pair
from build_manifest import (
    BUILD_SCHEMA,
    artifact_record as manifest_artifact_record,
    digest_file,
    digest_json,
    matrix_list,
    new_run_id,
    rigid_matrix_errors,
    semantic_assembly_record,
    utc_timestamp,
    validate_manifest,
    write_json_atomic,
)
from plate_layout import PlateLayoutError, pack_bboxes
from intent_contract import validate_color_regions
from material_plan import (
    build_material_plan,
    source_binding,
    validate_material_sources,
)
from scene_contract import SELF_TAPPING_RECIPE_KIND, validate as validate_scene
from shape_consistency import compare_manifest, compare_meshes, load_artifact
from self_tapping_geometry import (
    SelfTappingGeometryError,
    require_self_tapping_geometry,
)


REPORT_SCHEMA = BUILD_SCHEMA
COLOR_3MF_SCHEMA = "evidence-material-3mf/v1"
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MATERIAL_NS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
MODEL_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
DEFAULT_PALETTE = (
    "#5B8FF9",
    "#61DDAA",
    "#65789B",
    "#F6BD16",
    "#7262FD",
    "#78D3F8",
    "#9661BC",
    "#F6903D",
)


class CompileError(RuntimeError):
    """An actionable semantic-scene compilation failure."""


def _json_write(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload)


def _digest(path: Path) -> str:
    return digest_file(path)


def _artifact_record(path: Path) -> dict[str, str]:
    """Return the file-reference shape consumed by PI/server discovery."""

    return manifest_artifact_record(path)


def _model_name(intent: dict[str, Any]) -> str:
    """Use the immutable intent's validated build name without inference."""

    candidate = intent.get("part")
    if isinstance(candidate, str) and MODEL_NAME.fullmatch(candidate):
        return candidate
    raise CompileError("intent.part must be a lowercase filename-safe model name")


def _vector(values: Iterable[float]) -> list[float]:
    return [round(float(value), 9) for value in values]


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _load_intent_and_profile(
    scene: dict[str, Any], base_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load content-addressed intent/profile inputs or fail before compiling."""

    intent_ref = scene["intentRef"]
    intent_path = _resolve_path(intent_ref["path"], base_dir)
    try:
        intent_bytes = intent_path.read_bytes()
        intent = json.loads(intent_bytes)
    except Exception as error:
        raise CompileError(f"intentRef cannot be read: {error}") from error
    if digest_file(intent_path) != intent_ref["sha256"]:
        raise CompileError("intentRef.sha256 changed after scene validation")

    profile_ref = intent.get("printability", {}).get("profile")
    if not isinstance(profile_ref, dict):
        raise CompileError(
            "hybrid compilation requires intent.printability.profile; "
            "unbounded or assumed print layouts are forbidden"
        )
    raw_profile_path = profile_ref.get("path")
    expected_digest = profile_ref.get("sha256")
    if not isinstance(raw_profile_path, str) or not raw_profile_path.strip():
        raise CompileError("intent.printability.profile.path is required")
    if not isinstance(expected_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_digest
    ):
        raise CompileError("intent.printability.profile.sha256 is required")
    profile_path = _resolve_path(raw_profile_path, intent_path.parent)
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise CompileError(f"printer profile cannot be read: {error}") from error
    if digest_file(profile_path) != expected_digest:
        raise CompileError("printer profile sha256 does not match intent")
    if profile.get("schema") != "evidence-bambu-printer-profile/v1":
        raise CompileError("unsupported printer profile schema")
    return (
        intent,
        {
            "path": str(intent_path),
            "sha256": digest_file(intent_path),
            "schema": intent["schema"],
        },
        profile,
        {
            "id": profile.get("id"),
            "path": str(profile_path),
            "sha256": digest_file(profile_path),
            "schema": profile["schema"],
        },
    )


def _translation_matrix(values: Iterable[float]) -> np.ndarray:
    matrix = np.eye(4, dtype=float)
    matrix[:3, 3] = np.asarray(list(values), dtype=float)
    return matrix


def _axis_aligned_rotations() -> list[np.ndarray]:
    """Return all 24 right-handed axis-aligned rotations deterministically."""

    rotations: list[np.ndarray] = []
    for axes in permutations(range(3)):
        for signs in product((-1.0, 1.0), repeat=3):
            rotation = np.zeros((3, 3), dtype=float)
            for row, source_axis in enumerate(axes):
                rotation[row, source_axis] = signs[row]
            if math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-9):
                matrix = np.eye(4, dtype=float)
                matrix[:3, :3] = rotation
                rotations.append(matrix)
    rotations.sort(key=lambda item: tuple(item[:3, :3].reshape(-1)))
    return rotations


AXIS_ALIGNED_ROTATIONS = _axis_aligned_rotations()


def _profile_extents(profile: dict[str, Any]) -> tuple[float, float, float]:
    try:
        tool = profile["machine"]["selected_tool"]
        polygon = tool["polygon_mm"]
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        return max(xs) - min(xs), max(ys) - min(ys), float(tool["height_mm"])
    except Exception as error:
        raise CompileError(f"printer profile selected tool is invalid: {error}") from error


def _contact_and_overhang(mesh: trimesh.Trimesh) -> tuple[float, float]:
    """Return conservative contact/support proxies for one oriented mesh."""

    bounds = np.asarray(mesh.bounds, dtype=float)
    tolerance = max(1e-6, float(np.max(bounds[1] - bounds[0])) * 1e-7)
    triangles = np.asarray(mesh.triangles, dtype=float)
    min_z = float(bounds[0, 2])
    on_bed = np.all(np.abs(triangles[:, :, 2] - min_z) <= tolerance, axis=1)
    contact = float(np.sum(np.asarray(mesh.area_faces)[on_bed]))
    normals = np.asarray(mesh.face_normals, dtype=float)
    unsupported = (normals[:, 2] < -math.cos(math.radians(45.0))) & ~on_bed
    overhang = float(np.sum(np.asarray(mesh.area_faces)[unsupported]))
    return contact, overhang


def _select_print_transform(
    mesh: trimesh.Trimesh,
    profile: dict[str, Any],
    part_id: str,
) -> tuple[trimesh.Trimesh, np.ndarray, dict[str, Any]]:
    """Choose a scale-free axis-aligned print pose which fits the profile."""

    bed_x, bed_y, height_limit = _profile_extents(profile)
    candidates: list[tuple[tuple[Any, ...], trimesh.Trimesh, np.ndarray, dict[str, Any]]] = []
    for rotation_index, rotation in enumerate(AXIS_ALIGNED_ROTATIONS):
        oriented = mesh.copy()
        oriented.apply_transform(rotation)
        bounds = np.asarray(oriented.bounds, dtype=float)
        size = bounds[1] - bounds[0]
        fits = (
            size[0] <= bed_x + 1e-8
            and size[1] <= bed_y + 1e-8
            and size[2] <= height_limit + 1e-8
        )
        if not fits:
            continue
        contact, overhang = _contact_and_overhang(oriented)
        origin_translation = -bounds[0]
        transform = _translation_matrix(origin_translation) @ rotation
        oriented.apply_translation(origin_translation)
        score = (
            round(overhang, 9),
            round(-contact, 9),
            round(float(size[2]), 9),
            round(float(size[0] * size[1]), 9),
            rotation_index,
        )
        candidates.append(
            (
                score,
                oriented,
                transform,
                {
                    "contactAreaMm2": round(contact, 9),
                    "dimensionsMm": _vector(size),
                    "fitsProfile": True,
                    "overhangAreaProxyMm2": round(overhang, 9),
                    "rotationIndex": rotation_index,
                    "score": list(score),
                    "strategy": "axis-aligned-support-contact-profile-fit",
                },
            )
        )
    if not candidates:
        raise CompileError(
            f"part {part_id} has no rigid axis-aligned print orientation that fits "
            f"profile volume {bed_x:g}x{bed_y:g}x{height_limit:g} mm; scaling is disabled"
        )
    _, oriented, transform, evidence = min(candidates, key=lambda item: item[0])
    errors = rigid_matrix_errors(matrix_list(transform), f"part {part_id} print transform")
    if errors:
        raise CompileError("; ".join(errors))
    return oriented, transform, evidence


def _compose_plate_layout(
    compiled: list[tuple[str, trimesh.Trimesh, dict[str, Any]]],
    profile: dict[str, Any],
    *,
    spacing_mm: float = 5.0,
) -> tuple[
    dict[str, trimesh.Trimesh],
    dict[str, trimesh.Trimesh],
    dict[str, list[list[float]]],
    dict[str, list[list[float]]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    part_print: dict[str, trimesh.Trimesh] = {}
    part_matrices: dict[str, np.ndarray] = {}
    orientation_evidence: dict[str, dict[str, Any]] = {}
    for part_id, mesh, _ in compiled:
        oriented, transform, evidence = _select_print_transform(mesh, profile, part_id)
        part_print[part_id] = oriented
        part_matrices[part_id] = transform
        orientation_evidence[part_id] = evidence
    bboxes = {
        part_id: {
            "min": _vector(mesh.bounds[0]),
            "max": _vector(mesh.bounds[1]),
        }
        for part_id, mesh in part_print.items()
    }
    try:
        layout = pack_bboxes(bboxes, profile, spacing_mm=spacing_mm)
    except PlateLayoutError as error:
        raise CompileError(str(error)) from error

    plate_print: dict[str, trimesh.Trimesh] = {}
    plate_matrices: dict[str, np.ndarray] = {}
    for part_id, mesh in part_print.items():
        translation = _translation_matrix(layout["transforms"][part_id])
        placed = mesh.copy()
        placed.apply_transform(translation)
        plate_print[part_id] = placed
        plate_matrices[part_id] = translation @ part_matrices[part_id]
    return (
        part_print,
        plate_print,
        {part_id: matrix_list(matrix) for part_id, matrix in part_matrices.items()},
        {part_id: matrix_list(matrix) for part_id, matrix in plate_matrices.items()},
        orientation_evidence,
        layout,
    )


def _mesh_result(value: Any, context: str) -> trimesh.Trimesh:
    if isinstance(value, trimesh.Scene):
        meshes = [mesh for mesh in value.geometry.values() if not mesh.is_empty]
        value = trimesh.util.concatenate(meshes) if meshes else None
    if not isinstance(value, trimesh.Trimesh) or value.is_empty:
        raise CompileError(f"{context} returned no mesh")
    if value.volume < 0 and value.is_watertight:
        value.invert()
    return value


def _require_volume(mesh: trimesh.Trimesh, context: str) -> trimesh.Trimesh:
    mesh = mesh.copy()
    if mesh.volume < 0 and mesh.is_watertight:
        mesh.invert()
    if not mesh.is_watertight:
        raise CompileError(f"{context} source mesh must be watertight")
    if not mesh.is_winding_consistent:
        raise CompileError(f"{context} source mesh winding is inconsistent")
    if not mesh.is_volume or mesh.volume <= 0:
        raise CompileError(f"{context} source mesh must be a positive volume")
    return mesh


def _validate_source_transform(spec: dict[str, Any], context: str) -> None:
    scale = spec.get("scale", 1.0)
    if (
        not isinstance(scale, (int, float))
        or isinstance(scale, bool)
        or not math.isfinite(float(scale))
        or not math.isclose(float(scale), 1.0, abs_tol=1e-12)
    ):
        raise CompileError(f"{context}.sourceMesh.scale must be 1")
    transform = spec.get("toCanonicalTransform")
    if transform is None:
        return
    matrix = np.asarray(transform, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise CompileError(
            f"{context}.sourceMesh.toCanonicalTransform must be a finite 4x4 matrix"
        )
    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-9):
        raise CompileError(
            f"{context}.sourceMesh.toCanonicalTransform must be affine"
        )
    rotation = matrix[:3, :3]
    singular = np.linalg.svd(rotation, compute_uv=False)
    if not np.allclose(singular, [1, 1, 1], atol=1e-7):
        raise CompileError(
            f"{context}.sourceMesh.toCanonicalTransform must not scale geometry"
        )
    if not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-7):
        raise CompileError(
            f"{context}.sourceMesh.toCanonicalTransform determinant must be +1"
        )


def _source_spec(node: dict[str, Any], base_dir: Path) -> tuple[dict[str, Any], Path]:
    context = f"node {node['id']}"
    source = node["recipe"]["parameters"].get("sourceMesh")
    if isinstance(source, str) and source.strip():
        spec: dict[str, Any] = {"path": source}
    elif isinstance(source, dict):
        spec = deepcopy(source)
    else:
        raise CompileError(
            f"{context}.recipe.parameters.sourceMesh must be a path or object"
        )
    raw_path = spec.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise CompileError(f"{context}.sourceMesh.path is required")
    _validate_source_transform(spec, context)
    resolved = Path(raw_path)
    if not resolved.is_absolute():
        resolved = (base_dir / resolved).resolve()
    digest = digest_file(resolved)
    declared_digest = spec.get("sha256")
    if declared_digest is not None and declared_digest != digest:
        raise CompileError(f"{context}.sourceMesh.sha256 does not match its file")
    spec["sha256"] = digest
    return spec, resolved


def _load_node_mesh(
    node: dict[str, Any],
    base_dir: Path,
) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    spec, resolved = _source_spec(node, base_dir)
    mesh = load_artifact(spec, base_dir)
    # STL stores independent triangle corners.  Merge coincident vertices to
    # recover their declared topology without changing the physical surface.
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    mesh = _require_volume(mesh, f"node {node['id']}")
    bound_spec = deepcopy(spec)
    bound_spec["path"] = str(resolved)
    bound_spec["scale"] = 1.0
    return mesh, bound_spec


def _shape_to_mesh(shape: Any, context: str) -> trimesh.Trimesh:
    """Tessellate one build123d recipe output without changing its scale."""

    try:
        vertices, faces = shape.tessellate(0.02, 0.1)
        mesh = trimesh.Trimesh(
            vertices=[[vertex.X, vertex.Y, vertex.Z] for vertex in vertices],
            faces=faces,
            process=False,
        )
    except Exception as error:
        raise CompileError(f"{context} could not be tessellated: {error}") from error
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    return _require_volume(mesh, context)


def _self_tapping_group(
    scene: dict[str, Any],
    interface_id: str,
    fastener_id: str,
) -> tuple[dict[str, trimesh.Trimesh], dict[str, Any]]:
    interface = next(
        (
            item
            for item in scene.get("interfaces", [])
            if isinstance(item, dict) and item.get("id") == interface_id
        ),
        None,
    )
    if not isinstance(interface, dict) or interface.get("kind") != "self-tapping-screw":
        raise CompileError(
            f"self-tapping recipe references unknown interface {interface_id}"
        )
    fastener = next(
        (
            item
            for item in interface.get("fasteners", [])
            if isinstance(item, dict) and item.get("id") == fastener_id
        ),
        None,
    )
    if not isinstance(fastener, dict):
        raise CompileError(
            f"self-tapping recipe references unknown fastener {fastener_id}"
        )
    cover = fastener["cover"]
    receiver = fastener["receiver"]
    pair = self_tapping_screw_pair(
        interface_id=interface_id,
        axis_id=fastener_id,
        cover_thickness_mm=cover["thicknessMm"],
        screw_family=fastener["screwFamily"],
        nominal_diameter_mm=fastener["nominalDiameterMm"],
        clearance_diameter_mm=cover["diameterMm"],
        pilot_diameter_mm=receiver["diameterMm"],
        boss_outer_diameter_mm=receiver["bossOuterDiameterMm"],
        engagement_mm=receiver["engagementMm"],
        pilot_tip_clearance_mm=receiver["tipClearanceMm"],
        closed_end_mm=receiver["closedEndMm"],
        minimum_boss_wall_mm=receiver["minimumBossWallMm"],
        minimum_root_embed_mm=receiver["minimumRootEmbedMm"],
        head_recess_diameter_mm=cover.get("headRecessDiameterMm"),
        head_recess_depth_mm=cover.get("headRecessDepthMm"),
        minimum_cover_land_mm=cover.get("minimumResidualWallMm"),
        cutter_overshoot_mm=fastener["cutterOvershootMm"],
    )
    outputs = {
        "clearance-cutter": _shape_to_mesh(
            pair.clearance_cutter,
            f"interface {interface_id} fastener {fastener_id} clearance cutter",
        ),
        "pilot-cutter": _shape_to_mesh(
            pair.pilot_cutter,
            f"interface {interface_id} fastener {fastener_id} pilot cutter",
        ),
        "receiver-boss": _shape_to_mesh(
            pair.receiver_boss,
            f"interface {interface_id} fastener {fastener_id} receiver boss",
        ),
    }
    axis = fastener["axis"]
    origin = np.asarray(axis["originMm"], dtype=float)
    direction = np.asarray(axis["direction"], dtype=float)
    transform = trimesh.geometry.align_vectors([0.0, 0.0, 1.0], direction)
    if transform is None:
        transform = np.eye(4)
    transform = np.asarray(transform, dtype=float)
    transform[:3, 3] = origin
    for mesh in outputs.values():
        mesh.apply_transform(transform)

    evidence = deepcopy(pair.evidence)
    evidence["axis"] = {
        "direction": _vector(direction),
        "id": fastener_id,
        "origin_mm": _vector(origin),
    }
    evidence["interface_id"] = interface_id
    evidence["placement_transform"] = [
        _vector(row) for row in transform
    ]
    evidence["outputs"] = {
        name: {
            "bounds_mm": {
                "max": _vector(mesh.bounds[1]),
                "min": _vector(mesh.bounds[0]),
            },
            "volume_mm3": round(float(mesh.volume), 9),
        }
        for name, mesh in outputs.items()
    }
    return outputs, evidence


def _load_self_tapping_node(
    node: dict[str, Any],
    scene: dict[str, Any],
    cache: dict[tuple[str, str], tuple[dict[str, trimesh.Trimesh], dict[str, Any]]],
) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    parameters = node["recipe"]["parameters"]
    key = (parameters["interfaceId"], parameters["fastenerId"])
    if key not in cache:
        cache[key] = _self_tapping_group(scene, *key)
    outputs, evidence = cache[key]
    return outputs[parameters["output"]].copy(), evidence


def _probe_result_volume(result: Any) -> float:
    """Accept an empty successful boolean result and return its physical volume."""

    if result is None:
        return 0.0
    if isinstance(result, (list, tuple)):
        return sum(_probe_result_volume(item) for item in result)
    if isinstance(result, trimesh.Scene):
        return sum(
            _probe_result_volume(item)
            for item in result.geometry.values()
        )
    if isinstance(result, trimesh.Trimesh):
        if result.is_empty or len(result.faces) == 0:
            return 0.0
        return abs(float(result.volume))
    raise CompileError(f"probe boolean returned unsupported result {type(result).__name__}")


def _intersection_volume(
    body: trimesh.Trimesh,
    probe: trimesh.Trimesh,
    context: str,
) -> float:
    try:
        result = trimesh.boolean.intersection(
            [body, probe],
            engine="manifold",
            check_volume=True,
        )
    except Exception as error:
        raise CompileError(f"{context} intersection probe failed: {error}") from error
    return _probe_result_volume(result)


def _missing_volume(
    witness: trimesh.Trimesh,
    body: trimesh.Trimesh,
    context: str,
) -> float:
    try:
        result = trimesh.boolean.difference(
            [witness, body],
            engine="manifold",
            check_volume=True,
        )
    except Exception as error:
        raise CompileError(f"{context} containment probe failed: {error}") from error
    return _probe_result_volume(result)


def _verify_self_tapping_geometry(
    scene: dict[str, Any],
    part_meshes: dict[str, trimesh.Trimesh],
    feature_records: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Use the shared physical witness proof and preserve compiler errors."""

    try:
        return require_self_tapping_geometry(
            scene,
            part_meshes,
            feature_records=feature_records,
        )
    except SelfTappingGeometryError as error:
        raise CompileError(str(error)) from error


def _load_display_mesh(
    node: dict[str, Any],
    base_dir: Path,
) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    """Load a display decoration without imposing manufacturing topology.

    A display component may be a non-volume screen plane, a thin glass sheet,
    or another visual-only mesh.  It shares the same canonical transform rules
    as physical geometry, but is deliberately never accepted by a boolean or a
    manufacturing exporter.
    """

    spec, resolved = _source_spec(node, base_dir)
    mesh = load_artifact(spec, base_dir)
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    if mesh.is_empty or len(mesh.faces) == 0:
        raise CompileError(f"node {node['id']} display source mesh has no faces")
    if not np.isfinite(mesh.vertices).all():
        raise CompileError(
            f"node {node['id']} display source mesh contains non-finite vertices"
        )
    bound_spec = deepcopy(spec)
    bound_spec["path"] = str(resolved)
    bound_spec["scale"] = 1.0
    return mesh, bound_spec


def _components(meshes: list[trimesh.Trimesh]) -> list[trimesh.Trimesh]:
    result: list[trimesh.Trimesh] = []
    for mesh in meshes:
        split = mesh.split(only_watertight=False)
        result.extend(_require_volume(item, "boolean component") for item in split)
    return result


def _union(meshes: list[trimesh.Trimesh], part_id: str) -> trimesh.Trimesh:
    components = _components(meshes)
    if not components:
        raise CompileError(f"part {part_id} has no positive physical mesh")
    if len(components) == 1:
        return components[0].copy()
    try:
        result = trimesh.boolean.union(
            components,
            engine="manifold",
            check_volume=True,
        )
    except Exception as error:
        raise CompileError(f"part {part_id} manifold union failed: {error}") from error
    return _require_volume(_mesh_result(result, f"part {part_id} union"), part_id)


def _difference(
    body: trimesh.Trimesh,
    cutters: list[trimesh.Trimesh],
    part_id: str,
) -> tuple[trimesh.Trimesh, float]:
    components = _components(cutters)
    if not components:
        return body, 0.0
    before = float(body.volume)
    try:
        result = trimesh.boolean.difference(
            [body, *components],
            engine="manifold",
            check_volume=True,
        )
    except Exception as error:
        raise CompileError(
            f"part {part_id} manifold difference failed: {error}"
        ) from error
    result = _require_volume(
        _mesh_result(result, f"part {part_id} difference"), part_id
    )
    removed = before - float(result.volume)
    if removed <= max(1e-6, abs(before) * 1e-12):
        raise CompileError(f"part {part_id} cutters did not remove physical volume")
    return result, removed


def _normalize_color(value: Any, part_id: str) -> str:
    if isinstance(value, str) and HEX_COLOR.fullmatch(value):
        return value.upper()
    index = int(digest_json(part_id)[:2], 16) % len(DEFAULT_PALETTE)
    return DEFAULT_PALETTE[index]


def _appearance(part: dict[str, Any]) -> dict[str, Any]:
    raw = part.get("appearance")
    appearance = raw if isinstance(raw, dict) else {}
    color = _normalize_color(
        part.get("color", appearance.get("baseColor", appearance.get("color"))),
        part["id"],
    )
    metallic = appearance.get("metallic", appearance.get("metallicFactor", 0.0))
    roughness = appearance.get(
        "roughness", appearance.get("roughnessFactor", 0.58)
    )
    for name, value in (("metallic", metallic), ("roughness", roughness)):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise CompileError(f"part {part['id']} appearance.{name} must be 0..1")
    return {
        "baseColor": color,
        "metallic": float(metallic),
        "roughness": float(roughness),
    }


def _material_catalog(
    scene: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Resolve declared materials and explicit proposed fallbacks per part."""

    catalog = {
        material["id"]: {
            **deepcopy(material),
            "color": material["color"].upper(),
            "status": "declared",
        }
        for material in scene.get("materials", [])
    }
    assignments: dict[str, str] = {}
    for part in scene["parts"]:
        material_id = part.get("materialId")
        if isinstance(material_id, str):
            assignments[part["id"]] = material_id
            continue
        material_id = f"proposed-{part['id']}"
        appearance = _appearance(part)
        catalog[material_id] = {
            "color": appearance["baseColor"],
            "filament": None,
            "id": material_id,
            "status": "proposed",
            "transmission": None,
        }
        assignments[part["id"]] = material_id
    return catalog, assignments


def _build_source_bindings(
    scene: dict[str, Any],
    intent: dict[str, Any],
    materials: dict[str, dict[str, Any]],
    part_material_ids: dict[str, str],
) -> list[dict[str, Any]]:
    """Bind mutable scene materials to immutable intent regions exactly."""

    raw_regions = intent.get("color_regions")
    scene_parts = {part["id"]: part for part in scene["parts"]}
    scene_has_regions = any(part.get("colorRegions") for part in scene_parts.values())
    if raw_regions is None:
        if scene_has_regions:
            raise CompileError(
                "scene colorRegions require matching intent.color_regions declarations"
            )
        for material in materials.values():
            material.setdefault("filament", None)
            material.setdefault("transmission", None)
            material["fieldStatus"] = {
                "color": "proposed",
                "filament": "proposed",
                "transmission": "proposed",
            }
            material["status"] = "proposed"
        return [
            source_binding(
                material=materials[part_material_ids[part_id]],
                part=part_id,
                region=None,
                scope="whole-part",
                source_id=(
                    part_material_ids[part_id]
                    if isinstance(scene_parts[part_id].get("materialId"), str)
                    else part_id
                ),
                source_kind=(
                    "scene-part-material"
                    if isinstance(scene_parts[part_id].get("materialId"), str)
                    else "scene-part-appearance"
                ),
            )
            for part_id in sorted(scene_parts)
        ]

    manufacturing = intent.get("manufacturing")
    contract_errors = validate_color_regions(
        raw_regions,
        manufacturing,
        intent.get("part") if isinstance(intent.get("part"), str) else None,
    )
    if contract_errors:
        raise CompileError(
            "invalid intent color_regions: " + "; ".join(contract_errors)
        )
    mode = manufacturing.get("mode") if isinstance(manufacturing, dict) else None
    expected_package_mode = "co_print_body" if mode == "single-part" else "separate_parts"
    package_mode = intent.get("printability", {}).get("print_package_mode")
    if package_mode != expected_package_mode:
        raise CompileError(
            f"{mode} intent colors require printability.print_package_mode "
            f"{expected_package_mode}"
        )

    intent_regions = {
        region["name"]: region
        for region in raw_regions
        if isinstance(region, dict) and isinstance(region.get("name"), str)
    }
    if len(intent_regions) != len(raw_regions):
        raise CompileError("intent color region names must be globally unique")
    by_part: dict[str, list[dict[str, Any]]] = {
        part_id: [] for part_id in scene_parts
    }
    for region in intent_regions.values():
        owner = region.get("part")
        if owner not in scene_parts:
            raise CompileError(
                f"intent color region {region['name']} references unknown physical part {owner!r}"
            )
        by_part[owner].append(region)

    material_bindings: dict[str, list[dict[str, Any]]] = {}
    binding_specs: list[tuple[dict[str, Any], str, str]] = []
    for part_id, part in scene_parts.items():
        expected = by_part[part_id]
        declared = part.get("colorRegions", [])
        if declared:
            expected_ids = {region["name"] for region in expected}
            observed_ids = {
                region.get("id")
                for region in declared
                if isinstance(region, dict)
            }
            if expected_ids != observed_ids or len(observed_ids) != len(declared):
                raise CompileError(
                    f"part {part_id} scene colorRegions IDs must exactly match "
                    f"intent regions: expected {sorted(expected_ids)}, "
                    f"observed {sorted(item for item in observed_ids if isinstance(item, str))}"
                )
            for scene_region in declared:
                region = intent_regions[scene_region["id"]]
                material_id = scene_region["materialId"]
                material_bindings.setdefault(material_id, []).append(region)
                binding_specs.append((region, material_id, "volumetric-region"))
            continue

        if not expected:
            continue
        if len(expected) != 1:
            if part.get("representationMaster") == "brep":
                raise CompileError(
                    f"BRep part {part_id} declares multiple internal intent color "
                    "regions; use the BRep color-regions backend"
                )
            raise CompileError(
                f"mesh part {part_id} declares multiple intent color regions but "
                "scene colorRegions are missing"
            )
        region = expected[0]
        material_id = part_material_ids[part_id]
        material_bindings.setdefault(material_id, []).append(region)
        binding_specs.append((region, material_id, "whole-part"))

    for material_id, material in materials.items():
        bindings = material_bindings.get(material_id, [])
        material.setdefault("filament", None)
        material.setdefault("transmission", None)
        if not bindings:
            material["fieldStatus"] = {
                "color": "proposed",
                "filament": "proposed",
                "transmission": "proposed",
            }
            material["status"] = "proposed"
            continue

        colors = {str(region["hex"]).upper() for region in bindings}
        if len(colors) != 1 or material.get("color") not in colors:
            raise CompileError(
                f"scene material {material_id} color {material.get('color')!r} "
                f"does not match intent region color(s) {sorted(colors)}"
            )
        field_status = {"color": "declared"}
        for field in ("filament", "transmission"):
            declared_values = {
                region["material"][field]
                for region in bindings
                if isinstance(region.get("material"), dict)
                and field in region["material"]
            }
            if len(declared_values) > 1:
                raise CompileError(
                    f"intent regions sharing material {material_id} disagree on {field}"
                )
            if declared_values:
                expected_value = next(iter(declared_values))
                observed_value = material.get(field)
                if observed_value is not None and observed_value != expected_value:
                    raise CompileError(
                        f"scene material {material_id}.{field} {observed_value!r} "
                        f"does not match intent {expected_value!r}"
                    )
                material[field] = expected_value
                field_status[field] = "declared"
            else:
                field_status[field] = "proposed"
        material["fieldStatus"] = field_status
        material["intentRegionIds"] = sorted(region["name"] for region in bindings)
        # The intent hex is itself an authoritative material declaration. Keep
        # optional spool/transmission choices independently proposed when the
        # user did not select them.
        material["status"] = "declared"

    bound_parts = {region["part"] for region, _, _ in binding_specs}
    for part_id in scene_parts:
        if part_id not in bound_parts:
            material_id = part_material_ids[part_id]
            if material_id in material_bindings:
                raise CompileError(
                    f"scene material {material_id} cannot be shared by intent-colored "
                    "and scene-sourced parts"
                )

    bindings = [
        source_binding(
            material=materials[material_id],
            part=region["part"],
            region=None if scope == "whole-part" else region["name"],
            scope=scope,
            source_id=region["name"],
            source_kind="intent-color-region",
        )
        for region, material_id, scope in binding_specs
    ]
    bindings.extend(
        source_binding(
            material=materials[part_material_ids[part_id]],
            part=part_id,
            region=None,
            scope="whole-part",
            source_id=(
                part_material_ids[part_id]
                if isinstance(scene_parts[part_id].get("materialId"), str)
                else part_id
            ),
            source_kind=(
                "scene-part-material"
                if isinstance(scene_parts[part_id].get("materialId"), str)
                else "scene-part-appearance"
            ),
        )
        for part_id in sorted(scene_parts)
        if part_id not in bound_parts
    )
    return bindings


def _material_appearance(material: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseColor": material["color"].upper(),
        "metallic": 0.0,
        "roughness": 0.58,
    }


def _load_color_region(
    region: dict[str, Any], base_dir: Path, part_id: str
) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    spec = region["sourceMesh"]
    normalized = {"path": spec} if isinstance(spec, str) else deepcopy(spec)
    _validate_source_transform(normalized, f"part {part_id} region {region['id']}")
    raw_path = normalized.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise CompileError(f"part {part_id} region {region['id']} sourceMesh.path is required")
    mesh = load_artifact(normalized, base_dir)
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    mesh = _require_volume(mesh, f"part {part_id} region {region['id']}")
    normalized["path"] = str(_resolve_path(raw_path, base_dir))
    normalized["scale"] = 1.0
    digest = digest_file(Path(normalized["path"]))
    declared_digest = normalized.get("sha256")
    if declared_digest is not None and declared_digest != digest:
        raise CompileError(
            f"part {part_id} region {region['id']} sourceMesh.sha256 mismatch"
        )
    normalized["sha256"] = digest
    return mesh, normalized


def _compile_color_regions(
    scene: dict[str, Any],
    base_dir: Path,
    compiled_meshes: dict[str, trimesh.Trimesh],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, Any]],
]:
    """Load and prove exact, non-overlapping volumetric mesh color regions."""

    result: dict[str, list[dict[str, Any]]] = {}
    bound_sources: dict[str, dict[str, Any]] = {}
    parts = {part["id"]: part for part in scene["parts"]}
    for part_id, body in compiled_meshes.items():
        declarations = parts[part_id].get("colorRegions", [])
        if not declarations:
            continue
        loaded: list[dict[str, Any]] = []
        for region in declarations:
            mesh, source = _load_color_region(region, base_dir, part_id)
            loaded.append(
                {
                    "id": region["id"],
                    "materialId": region["materialId"],
                    "mesh": mesh,
                    "sourceMesh": source,
                }
            )
            bound_sources[f"{part_id}/{region['id']}"] = source

        tolerance = max(1e-5, abs(float(body.volume)) * 1e-8)
        overlaps: list[str] = []
        for index, left in enumerate(loaded):
            for right in loaded[index + 1 :]:
                overlap = _intersection_volume(
                    left["mesh"],
                    right["mesh"],
                    f"part {part_id} color regions {left['id']}&{right['id']}",
                )
                if overlap > tolerance:
                    overlaps.append(
                        f"{left['id']}&{right['id']}={overlap:.9g} mm3"
                    )
        if overlaps:
            raise CompileError(
                f"part {part_id} color regions overlap: {', '.join(overlaps)}"
            )
        union = _union([item["mesh"] for item in loaded], f"{part_id}-color-regions")
        missing = _missing_volume(
            body, union, f"part {part_id} color-region coverage"
        )
        extra = _missing_volume(
            union, body, f"part {part_id} color-region containment"
        )
        if missing > tolerance or extra > tolerance:
            raise CompileError(
                f"part {part_id} color regions must exactly partition the compiled "
                f"body (missing={missing:.9g} mm3, extra={extra:.9g} mm3, "
                f"tolerance={tolerance:.9g} mm3)"
            )
        result[part_id] = loaded
    return result, bound_sources


def _verify_master_steps(
    scene: dict[str, Any], base_dir: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, trimesh.Trimesh]]:
    """Import and bind real single-solid BRep masters through OCCT."""

    records: dict[str, dict[str, Any]] = {}
    meshes: dict[str, trimesh.Trimesh] = {}
    for part in scene["parts"]:
        if part["representationMaster"] != "brep":
            continue
        artifacts = part.get("artifacts")
        spec = artifacts.get("masterStep") if isinstance(artifacts, dict) else None
        if not isinstance(spec, dict):
            raise CompileError(
                f"part {part['id']} is brep-master and must bind artifacts.masterStep; "
                "hybrid_compile will not fabricate STEP from a mesh derivative"
            )
        path = _resolve_path(spec["path"], base_dir)
        if spec.get("revision") != scene["revision"]:
            raise CompileError(
                f"part {part['id']} master STEP revision must equal scene revision"
            )
        if spec.get("scale") != 1.0:
            raise CompileError(f"part {part['id']} master STEP scale must be 1")
        try:
            observed_digest = digest_file(path)
        except OSError as error:
            raise CompileError(
                f"part {part['id']} master STEP cannot be read: {error}"
            ) from error
        if observed_digest != spec["sha256"]:
            raise CompileError(f"part {part['id']} master STEP sha256 mismatch")
        try:
            shape = import_step(str(path))
        except Exception as error:
            raise CompileError(
                f"part {part['id']} master STEP failed OCCT import: {error}"
            ) from error
        try:
            valid_value = shape.is_valid
            valid = bool(valid_value() if callable(valid_value) else valid_value)
            solid_count = len(shape.solids())
        except Exception as error:
            raise CompileError(
                f"part {part['id']} master STEP failed OCCT topology inspection: {error}"
            ) from error
        if not valid:
            raise CompileError(f"part {part['id']} master STEP is not a valid BRep")
        if solid_count != 1:
            raise CompileError(
                f"part {part['id']} master STEP must contain exactly one solid; "
                f"got {solid_count}"
            )
        mesh = _shape_to_mesh(shape, f"part {part['id']} master STEP")
        records[part["id"]] = {
            **_artifact_record(path),
            "coordinateFrame": "semantic",
            "role": "brep-master",
            "solidCount": solid_count,
            "verifiedBrep": True,
            "validator": "build123d-occt",
        }
        meshes[part["id"]] = mesh
    return records, meshes


def _compare_master_steps(
    *,
    scene: dict[str, Any],
    master_steps: dict[str, dict[str, Any]],
    step_meshes: dict[str, trimesh.Trimesh],
    compiled_meshes: dict[str, trimesh.Trimesh],
    sample_count: int,
) -> dict[str, Any]:
    """Prove each BRep master describes its final compiled semantic part."""

    parts: dict[str, dict[str, Any]] = {}
    for part_id in sorted(master_steps):
        try:
            comparison = compare_meshes(
                step_meshes[part_id],
                compiled_meshes[part_id],
                label_a=f"step:{part_id}",
                label_b=f"compiled:{part_id}",
                revision_a=scene["revision"],
                revision_b=scene["revision"],
                dimension_tolerance_mm=0.02,
                surface_p99_tolerance_mm=0.05,
                surface_max_tolerance_mm=0.1,
                sample_count=sample_count,
            )
        except Exception as error:
            raise CompileError(
                f"part {part_id} STEP consistency comparison failed: {error}"
            ) from error
        volume_delta_percent = comparison.get("volumeDeltaPercent")
        volume_pass = (
            isinstance(volume_delta_percent, (int, float))
            and not isinstance(volume_delta_percent, bool)
            and math.isfinite(float(volume_delta_percent))
            and float(volume_delta_percent) <= 1.0
        )
        comparison["checks"].append(
            {
                "name": "volume",
                "observedPercent": volume_delta_percent,
                "pass": volume_pass,
                "tolerancePercent": 1.0,
            }
        )
        comparison["pass"] = bool(comparison["pass"] and volume_pass)
        parts[part_id] = {
            "comparison": comparison,
            "pass": comparison["pass"],
            "step": deepcopy(master_steps[part_id]),
        }
        if not comparison["pass"]:
            failed = ", ".join(
                item["name"]
                for item in comparison["checks"]
                if item.get("pass") is False
            )
            raise CompileError(
                f"part {part_id} master STEP does not match its final compiled "
                f"semantic mesh; failed checks: {failed}"
            )
    return {
        "parts": parts,
        "pass": all(item["pass"] for item in parts.values()),
        "revision": scene["revision"],
        "schema": "evidence-step-consistency/v1",
    }


def _display_appearance(node: dict[str, Any]) -> dict[str, Any]:
    """Resolve an explicitly visual material without mutating physical color."""

    appearance = node["recipe"]["parameters"]["appearance"]
    color = _normalize_color(appearance["baseColor"], node["id"])
    metallic = appearance.get("metallic", 0.0)
    roughness = appearance.get("roughness", 0.58)
    for name, value in (("metallic", metallic), ("roughness", roughness)):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise CompileError(
                f"display node {node['id']} appearance.{name} must be 0..1"
            )
    return {
        "baseColor": color,
        "metallic": float(metallic),
        "roughness": float(roughness),
    }


def _pbr_material(part_id: str, appearance: dict[str, Any]) -> PBRMaterial:
    color = appearance["baseColor"]
    rgba = [int(color[index : index + 2], 16) for index in (1, 3, 5)] + [255]
    return PBRMaterial(
        name=f"{part_id}-material",
        baseColorFactor=rgba,
        metallicFactor=appearance["metallic"],
        roughnessFactor=appearance["roughness"],
    )


def _mesh_record(mesh: trimesh.Trimesh, path: Path) -> dict[str, Any]:
    bounds = np.asarray(mesh.bounds, dtype=float)
    return {
        "bodyCount": max(len(mesh.split(only_watertight=False)), 1),
        "boundsMm": {
            "max": _vector(bounds[1]),
            "min": _vector(bounds[0]),
            "size": _vector(bounds[1] - bounds[0]),
        },
        **_artifact_record(path),
        "isVolume": bool(mesh.is_volume),
        "triangles": int(len(mesh.faces)),
        "vertices": int(len(mesh.vertices)),
        "volumeMm3": round(float(mesh.volume), 9),
        "watertight": bool(mesh.is_watertight),
        "windingConsistent": bool(mesh.is_winding_consistent),
    }


def _region_display_surfaces(
    body: trimesh.Trimesh,
    regions: list[dict[str, Any]],
    part_id: str,
) -> dict[str, trimesh.Trimesh]:
    """Extract only each region's exterior faces for faithful colored preview.

    Volumetric regions contain shared internal interfaces.  Rendering those
    closed volumes directly makes display/manufacturing surface comparison see
    hidden internal faces.  Keeping only faces coincident with the compiled
    body's exterior preserves the visible color boundary without adding a
    second manufacturing body or changing the printable geometry.
    """

    diagonal = float(np.linalg.norm(np.asarray(body.extents, dtype=float)))
    tolerance = max(1e-5, diagonal * 1e-6)
    surfaces: dict[str, trimesh.Trimesh] = {}
    exterior_area = 0.0
    for region in regions:
        mesh = region["mesh"]
        centroids = np.asarray(mesh.triangles_center, dtype=float)
        try:
            _, distances, _ = trimesh.proximity.closest_point(body, centroids)
        except (ImportError, ModuleNotFoundError):
            _, distances, _ = trimesh.proximity.closest_point_naive(body, centroids)
        face_ids = np.flatnonzero(np.asarray(distances) <= tolerance)
        if not len(face_ids):
            raise CompileError(
                f"part {part_id} color region {region['id']} has no exterior faces "
                "and cannot be represented in the display GLB"
            )
        surface = mesh.submesh([face_ids], append=True, repair=False)
        if not isinstance(surface, trimesh.Trimesh) or surface.is_empty:
            raise CompileError(
                f"part {part_id} color region {region['id']} display surface is empty"
            )
        surfaces[region["id"]] = surface
        exterior_area += float(surface.area)
    area_tolerance = max(1e-5, float(body.area) * 1e-5)
    if abs(exterior_area - float(body.area)) > area_tolerance:
        raise CompileError(
            f"part {part_id} region display surfaces do not cover the compiled "
            f"exterior (region area={exterior_area:.9g} mm2, "
            f"body area={float(body.area):.9g} mm2)"
        )
    return surfaces


def _write_physical_glb(
    parts: list[tuple[str, trimesh.Trimesh, dict[str, Any]]],
    color_regions: dict[str, list[dict[str, Any]]],
    materials: dict[str, dict[str, Any]],
    display_nodes: list[tuple[str, trimesh.Trimesh, dict[str, Any]]],
    output: Path,
    revision: str,
) -> dict[str, Any]:
    scene = trimesh.Scene()
    scene.metadata.update(
        {
            "revision": revision,
            "scale": 1.0,
            "schema": "evidence-physical-display/v1",
            "units": "mm",
        }
    )
    physical_nodes_by_part: dict[str, list[str]] = {}
    expected_colors: dict[str, str] = {}
    for part_id, mesh, appearance in parts:
        regions = color_regions.get(part_id, [])
        if not regions:
            node_name = part_id
            display = mesh.copy()
            display.visual = TextureVisuals(
                material=_pbr_material(node_name, appearance)
            )
            scene.add_geometry(
                display,
                node_name=node_name,
                geom_name=f"{part_id}-geometry",
            )
            physical_nodes_by_part[part_id] = [node_name]
            expected_colors[node_name] = appearance["baseColor"].upper()
            continue

        physical_nodes_by_part[part_id] = []
        region_surfaces = _region_display_surfaces(mesh, regions, part_id)
        for region in regions:
            node_name = f"{part_id}--region--{region['id']}"
            region_appearance = _material_appearance(
                materials[region["materialId"]]
            )
            display = region_surfaces[region["id"]].copy()
            display.visual = TextureVisuals(
                material=_pbr_material(node_name, region_appearance)
            )
            scene.add_geometry(
                display,
                node_name=node_name,
                geom_name=f"{node_name}-geometry",
            )
            physical_nodes_by_part[part_id].append(node_name)
            expected_colors[node_name] = region_appearance["baseColor"].upper()
    for node_id, mesh, appearance in display_nodes:
        display = mesh.copy()
        display.visual = TextureVisuals(material=_pbr_material(node_id, appearance))
        scene.add_geometry(
            display,
            node_name=node_id,
            geom_name=f"{node_id}-display-geometry",
        )
    payload = scene.export(file_type="glb")
    if not isinstance(payload, bytes) or payload[:4] != b"glTF":
        raise CompileError("physical GLB exporter returned an invalid payload")
    output.write_bytes(payload)
    loaded = trimesh.load(output, force="scene", process=False)
    if not isinstance(loaded, trimesh.Scene):
        raise CompileError("physical GLB readback did not produce a scene")
    node_names = sorted(loaded.graph.nodes_geometry)
    expected_physical = sorted(
        node_name
        for part_nodes in physical_nodes_by_part.values()
        for node_name in part_nodes
    )
    expected_display = sorted(node_id for node_id, _, _ in display_nodes)
    expected = sorted([*expected_physical, *expected_display])
    if node_names != expected:
        raise CompileError(
            f"physical GLB node readback mismatch: expected {expected}, got {node_names}"
        )
    readback_colors: dict[str, str] = {}
    for node_name, expected_color in expected_colors.items():
        _, geometry_name = loaded.graph[node_name]
        geometry = loaded.geometry[geometry_name]
        material = getattr(geometry.visual, "material", None)
        raw_color = getattr(material, "baseColorFactor", None)
        if raw_color is None or len(raw_color) < 3:
            raise CompileError(
                f"physical GLB node {node_name} has no PBR base color after readback"
            )
        values = np.asarray(raw_color[:3], dtype=float)
        if float(np.max(values)) <= 1.0 + 1e-9:
            values = values * 255.0
        color = "#" + "".join(f"{int(round(value)):02X}" for value in values)
        readback_colors[node_name] = color
        if color != expected_color:
            raise CompileError(
                f"physical GLB node {node_name} color readback mismatch: "
                f"expected {expected_color}, got {color}"
            )
    return {
        **_artifact_record(output),
        "nodeNames": node_names,
        "physicalNodeNames": expected_physical,
        "physicalPartNodeNames": {
            part_id: list(node_names)
            for part_id, node_names in physical_nodes_by_part.items()
        },
        "readbackBaseColors": readback_colors,
        "regionNodeNames": {
            part_id: list(node_names)
            for part_id, node_names in physical_nodes_by_part.items()
            if len(node_names) > 1 or node_names[0] != part_id
        },
        "displayOnlyNodeNames": expected_display,
        "revision": revision,
        "scale": 1.0,
        "verified": True,
    }


def _append_3mf_mesh(
    resources: ET.Element,
    *,
    object_id: int,
    name: str,
    mesh: trimesh.Trimesh,
    color_index: int,
) -> dict[str, Any]:
    obj = ET.SubElement(
        resources,
        f"{{{CORE_NS}}}object",
        {
            "id": str(object_id),
            "name": name,
            "pindex": str(color_index),
            "pid": "1",
            "type": "model",
        },
    )
    mesh_element = ET.SubElement(obj, f"{{{CORE_NS}}}mesh")
    vertices = ET.SubElement(mesh_element, f"{{{CORE_NS}}}vertices")
    for vertex in np.asarray(mesh.vertices, dtype=float):
        ET.SubElement(
            vertices,
            f"{{{CORE_NS}}}vertex",
            {
                "x": format(float(vertex[0]), ".9g"),
                "y": format(float(vertex[1]), ".9g"),
                "z": format(float(vertex[2]), ".9g"),
            },
        )
    triangles = ET.SubElement(mesh_element, f"{{{CORE_NS}}}triangles")
    for face in np.asarray(mesh.faces, dtype=np.int64):
        ET.SubElement(
            triangles,
            f"{{{CORE_NS}}}triangle",
            {
                "v1": str(int(face[0])),
                "v2": str(int(face[1])),
                "v3": str(int(face[2])),
            },
        )
    return {
        "colorIndex": color_index,
        "id": object_id,
        "name": name,
        "triangles": int(len(mesh.faces)),
        "vertices": int(len(mesh.vertices)),
    }


def _write_material_3mf(
    parts: list[tuple[str, trimesh.Trimesh, str]],
    region_meshes: dict[str, list[dict[str, Any]]],
    materials: dict[str, dict[str, Any]],
    output: Path,
    *,
    package_mode: str,
) -> dict[str, Any]:
    """Write one build item per part, with component color volumes as needed."""

    if not parts:
        raise CompileError("cannot write a 3MF without physical parts")
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("m", MATERIAL_NS)
    model = ET.Element(
        f"{{{CORE_NS}}}model",
        {
            "requiredextensions": "m",
            "unit": "millimeter",
            "{http://www.w3.org/XML/1998/namespace}lang": "en-US",
        },
    )
    ET.SubElement(model, f"{{{CORE_NS}}}metadata", {"name": "Title"}).text = (
        "Amagine3D hybrid physical assembly"
    )
    ET.SubElement(
        model,
        f"{{{CORE_NS}}}metadata",
        {"name": "amagine3d:color-scope"},
    ).text = "part-or-volumetric-region-v1"
    metadata_regions = []
    for part_id, _, material_id in parts:
        regions = region_meshes.get(part_id, [])
        if regions:
            metadata_regions.extend(
                {
                    "color": materials[region["materialId"]]["color"],
                    "name": f"{part_id}/{region['id']}",
                    "part": part_id,
                    "scope": "volumetric-region",
                }
                for region in regions
            )
        else:
            metadata_regions.append(
                {
                    "color": materials[material_id]["color"],
                    "name": part_id,
                    "part": part_id,
                    "scope": "whole-part",
                }
            )
    ET.SubElement(
        model,
        f"{{{CORE_NS}}}metadata",
        {
            "name": "amagine3d-color-regions",
            "type": "application/json",
        },
    ).text = json.dumps(
        {
            "package_mode": package_mode,
            "package_name": output.stem,
            "regions": metadata_regions,
            "schema": "amagine3d-color-regions/v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    resources = ET.SubElement(model, f"{{{CORE_NS}}}resources")

    used_material_ids = list(
        dict.fromkeys(
            [material_id for _, _, material_id in parts]
            + [
                region["materialId"]
                for regions in region_meshes.values()
                for region in regions
            ]
        )
    )
    colors = [materials[material_id]["color"] for material_id in used_material_ids]
    palette = ET.SubElement(
        resources, f"{{{MATERIAL_NS}}}colorgroup", {"id": "1"}
    )
    for color in colors:
        ET.SubElement(
            palette,
            f"{{{MATERIAL_NS}}}color",
            {"color": f"{color}FF"},
        )

    object_records: list[dict[str, Any]] = []
    assignment_records: list[dict[str, Any]] = []
    object_ids: list[str] = []
    next_object_id = 2
    for part_id, mesh, material_id in parts:
        regions = region_meshes.get(part_id, [])
        if not regions:
            object_id = next_object_id
            next_object_id += 1
            object_ids.append(str(object_id))
            record = _append_3mf_mesh(
                resources,
                object_id=object_id,
                name=part_id,
                mesh=mesh,
                color_index=used_material_ids.index(material_id),
            )
            record.update(
                color=materials[material_id]["color"],
                materialId=material_id,
                part=part_id,
                scope="whole-part",
            )
            object_records.append(record)
            assignment_records.append(
                {
                    "materialId": material_id,
                    "part": part_id,
                    "region": None,
                    "scope": "whole-part",
                }
            )
            continue

        child_ids: list[int] = []
        for region in regions:
            object_id = next_object_id
            next_object_id += 1
            child_ids.append(object_id)
            region_material = region["materialId"]
            record = _append_3mf_mesh(
                resources,
                object_id=object_id,
                name=f"{part_id}/{region['id']}",
                mesh=region["mesh"],
                color_index=used_material_ids.index(region_material),
            )
            record.update(
                color=materials[region_material]["color"],
                materialId=region_material,
                part=part_id,
                region=region["id"],
                scope="volumetric-region",
            )
            object_records.append(record)
            assignment_records.append(
                {
                    "materialId": region_material,
                    "part": part_id,
                    "region": region["id"],
                    "scope": "volumetric-region",
                }
            )
        parent_id = next_object_id
        next_object_id += 1
        object_ids.append(str(parent_id))
        parent = ET.SubElement(
            resources,
            f"{{{CORE_NS}}}object",
            {"id": str(parent_id), "name": part_id, "type": "model"},
        )
        components = ET.SubElement(parent, f"{{{CORE_NS}}}components")
        for child_id in child_ids:
            ET.SubElement(
                components,
                f"{{{CORE_NS}}}component",
                {"objectid": str(child_id)},
            )
        object_records.append(
            {
                "componentObjectIds": child_ids,
                "id": parent_id,
                "name": part_id,
                "part": part_id,
                "scope": "part-components",
            }
        )

    build = ET.SubElement(model, f"{{{CORE_NS}}}build")
    for object_id in object_ids:
        ET.SubElement(build, f"{{{CORE_NS}}}item", {"objectid": object_id})
    model_bytes = ET.tostring(model, encoding="utf-8", xml_declaration=True)

    ET.register_namespace("", CONTENT_TYPES_NS)
    types = ET.Element(f"{{{CONTENT_TYPES_NS}}}Types")
    ET.SubElement(
        types,
        f"{{{CONTENT_TYPES_NS}}}Default",
        {
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
            "Extension": "rels",
        },
    )
    ET.SubElement(
        types,
        f"{{{CONTENT_TYPES_NS}}}Default",
        {
            "ContentType": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
            "Extension": "model",
        },
    )
    types_bytes = ET.tostring(types, encoding="utf-8", xml_declaration=True)

    ET.register_namespace("", REL_NS)
    relationships = ET.Element(f"{{{REL_NS}}}Relationships")
    ET.SubElement(
        relationships,
        f"{{{REL_NS}}}Relationship",
        {"Id": "rel0", "Target": "/3D/3dmodel.model", "Type": MODEL_REL},
    )
    rels_bytes = ET.tostring(
        relationships, encoding="utf-8", xml_declaration=True
    )
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types_bytes)
        archive.writestr("_rels/.rels", rels_bytes)
        archive.writestr("3D/3dmodel.model", model_bytes)

    with ZipFile(output) as archive:
        names = sorted(archive.namelist())
        readback = ET.fromstring(archive.read("3D/3dmodel.model"))
    readback_objects = [
        element
        for element in readback.iter()
        if element.tag.rsplit("}", 1)[-1] == "object"
    ]
    readback_items = [
        element
        for element in readback.iter()
        if element.tag.rsplit("}", 1)[-1] == "item"
    ]
    readback_names = [item.attrib.get("name") for item in readback_objects]
    readback_components = [
        element
        for element in readback.iter()
        if element.tag.rsplit("}", 1)[-1] == "component"
    ]
    readback_colors = [
        element.attrib.get("color")
        for element in readback.iter()
        if element.tag.rsplit("}", 1)[-1] == "color"
    ]
    objects_by_id = {
        int(element.attrib["id"]): element
        for element in readback_objects
        if element.attrib.get("id", "").isdigit()
    }
    expected_names = [record["name"] for record in object_records]
    material_bindings_verified = all(
        (
            objects_by_id.get(record["id"]) is not None
            and objects_by_id[record["id"]].attrib.get("pid") == "1"
            and objects_by_id[record["id"]].attrib.get("pindex")
            == str(record["colorIndex"])
            and colors[record["colorIndex"]] == record["color"]
        )
        for record in object_records
        if record["scope"] in {"whole-part", "volumetric-region"}
    )
    verified = (
        readback.attrib.get("unit") == "millimeter"
        and readback_colors == [f"{color}FF" for color in colors]
        and readback_names == expected_names
        and len(readback_items) == len(parts)
        and all(item.attrib.get("objectid") in object_ids for item in readback_items)
        and all(
            item.attrib.get("objectid")
            in {str(record["id"]) for record in object_records}
            for item in readback_components
        )
        and material_bindings_verified
    )
    if not verified:
        raise CompileError("colored 3MF readback verification failed")
    try:
        wrapper = lib3mf.get_wrapper()
        independent_model = wrapper.CreateModel()
        independent_model.QueryReader("3mf").ReadFromFile(str(output))
        mesh_iterator = independent_model.GetMeshObjects()
        component_iterator = independent_model.GetComponentsObjects()
        build_iterator = independent_model.GetBuildItems()
        color_iterator = independent_model.GetColorGroups()
        independent_verified = (
            independent_model.GetUnit() == lib3mf.ModelUnit.MilliMeter
            and mesh_iterator.Count()
            == len(
                [
                    record
                    for record in object_records
                    if record["scope"] in {"whole-part", "volumetric-region"}
                ]
            )
            and component_iterator.Count()
            == len(
                [record for record in object_records if record["scope"] == "part-components"]
            )
            and build_iterator.Count() == len(parts)
            and color_iterator.Count() == 1
        )
        if independent_verified and color_iterator.MoveNext():
            independent_verified = (
                color_iterator.GetCurrentColorGroup().GetCount() == len(colors)
            )
    except Exception as error:
        raise CompileError(f"lib3mf could not import the generated 3MF: {error}") from error
    if not independent_verified:
        raise CompileError("lib3mf import verification failed for generated 3MF")
    return {
        **_artifact_record(output),
        "archiveEntries": names,
        "assignments": assignment_records,
        "buildItemCount": len(readback_items),
        "colorScope": "part-or-volumetric-region",
        "objectCount": len(readback_objects),
        "objects": object_records,
        "readbackColors": readback_colors,
        "regionColoring": "volumetric-components",
        "schema": COLOR_3MF_SCHEMA,
        "unit": readback.attrib.get("unit"),
        "validator": "lib3mf",
        "verified": True,
    }


def _bind_scene(
    source: dict[str, Any],
    *,
    base_dir: Path,
    part_records: dict[str, dict[str, Any]],
    physical_glb: Path,
    combined_stl: Path,
    colored_3mf: Path,
    bound_sources: dict[str, dict[str, Any]],
    region_bound_sources: dict[str, dict[str, Any]],
    part_print_transforms: dict[str, list[list[float]]],
    physical_part_node_names: dict[str, list[str]],
    fastener_groups: dict[str, dict[str, Any]],
    fastener_geometry_checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    bound = deepcopy(source)
    raw_intent = bound["intentRef"]["path"]
    intent_path = Path(raw_intent)
    if not intent_path.is_absolute():
        intent_path = (base_dir / intent_path).resolve()
    bound["intentRef"]["path"] = str(intent_path)
    for node in bound["nodes"]:
        if node["id"] not in bound_sources:
            continue
        node["recipe"]["parameters"]["sourceMesh"] = deepcopy(
            bound_sources[node["id"]]
        )

    for part in bound["parts"]:
        part_id = part["id"]
        part_file = Path(part_records[part_id]["path"])
        source_artifacts = part.get("artifacts", {})
        artifacts = {
            "manufacturingStl": {
                "path": part_file.name,
                "revision": bound["revision"],
                "scale": 1.0,
                "toCanonicalTransform": matrix_list(
                    np.linalg.inv(np.asarray(part_print_transforms[part_id], dtype=float))
                ),
            },
            "physicalGlb": {
                "nodeNames": list(physical_part_node_names[part_id]),
                "path": physical_glb.name,
                "revision": bound["revision"],
                "scale": 1.0,
            },
        }
        if part["representationMaster"] == "brep":
            artifacts["masterStep"] = deepcopy(source_artifacts["masterStep"])
            raw_step = Path(artifacts["masterStep"]["path"])
            if not raw_step.is_absolute():
                raw_step = (base_dir / raw_step).resolve()
            artifacts["masterStep"]["path"] = str(raw_step)
        part["artifacts"] = artifacts
        for region in part.get("colorRegions", []):
            key = f"{part_id}/{region['id']}"
            region["sourceMesh"] = deepcopy(region_bound_sources[key])
    bound["hybridCompile"] = {
        "artifacts": {
            "3mf": {
                "path": colored_3mf.name,
                "sha256": _digest(colored_3mf),
            },
            "glb:display": {
                "path": physical_glb.name,
                "sha256": _digest(physical_glb),
            },
            "stl": {
                "path": combined_stl.name,
                "sha256": _digest(combined_stl),
                "parts": {
                    part_id: {
                        "path": Path(record["path"]).name,
                        "sha256": record["sha256"],
                    }
                    for part_id, record in part_records.items()
                },
            },
        },
        "autoScale": False,
        "colorScope": "part-or-volumetric-region-v1",
        "compiler": "hybrid_compile.py",
        "fastenerGeometryChecks": deepcopy(fastener_geometry_checks),
        "fastenerGroups": deepcopy(fastener_groups),
        "revision": bound["revision"],
        "scale": 1.0,
    }
    return bound


def compile_scene(
    scene: dict[str, Any],
    *,
    base_dir: Path,
    output_dir: Path,
    source_scene: Path,
    consistency_samples: int = 1024,
) -> dict[str, Any]:
    """Compile a file-bound scene and return its persisted build report."""

    if MANIFOLD_IMPORT_ERROR is not None:
        raise CompileError(
            "manifold3d is required for hybrid mesh booleans"
        ) from MANIFOLD_IMPORT_ERROR
    base_dir = base_dir.resolve()
    output_dir = output_dir.resolve()
    source_path = source_scene.resolve()
    try:
        persisted_scene = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise CompileError(f"source scene cannot be read: {error}") from error
    if persisted_scene != scene:
        raise CompileError("source scene file does not match the compiled scene payload")
    errors = validate_scene(scene, base_dir)
    if errors:
        raise CompileError("invalid semantic scene: " + "; ".join(errors))
    if consistency_samples < 32:
        raise CompileError("consistency_samples must be at least 32")
    output_dir.mkdir(parents=True, exist_ok=True)
    intent_data, intent_record, profile, profile_record = _load_intent_and_profile(
        scene, base_dir
    )
    model_name = _model_name(intent_data)
    master_steps, master_step_meshes = _verify_master_steps(scene, base_dir)
    materials, part_material_ids = _material_catalog(scene)
    source_bindings = _build_source_bindings(
        scene,
        intent_data,
        materials,
        part_material_ids,
    )

    parts = {part["id"]: part for part in scene["parts"]}
    positive: dict[str, list[trimesh.Trimesh]] = {
        part_id: [] for part_id in parts
    }
    cutters: dict[str, list[trimesh.Trimesh]] = {
        part_id: [] for part_id in parts
    }
    positive_node_ids: dict[str, list[str]] = {part_id: [] for part_id in parts}
    cutter_node_ids: dict[str, list[str]] = {part_id: [] for part_id in parts}
    bound_sources: dict[str, dict[str, Any]] = {}
    procedural_fasteners: dict[
        tuple[str, str],
        tuple[dict[str, trimesh.Trimesh], dict[str, Any]],
    ] = {}
    fastener_groups: dict[str, dict[str, Any]] = {}
    excluded_display: list[dict[str, Any]] = []
    manufacturing_exclusions: list[dict[str, Any]] = []
    included_display: list[dict[str, Any]] = []
    display_compiled: list[tuple[str, trimesh.Trimesh, dict[str, Any]]] = []
    feature_records: dict[str, dict[str, Any]] = {}

    for node in scene["nodes"]:
        role = node["role"]
        if role == "display-only":
            # display-only sources are intentionally loaded after validation
            # and are emitted solely into the display GLB.  They never reach
            # the positive/cutter collections used by boolean/STL/3MF paths.
            if node["recipe"]["kind"] == "displayComponent":
                mesh, bound_spec = _load_display_mesh(node, base_dir)
                bound_sources[node["id"]] = bound_spec
                appearance = _display_appearance(node)
                display_compiled.append((node["id"], mesh, appearance))
                display_record = {
                    "nodeId": node["id"],
                    "physicalFeatureRef": node.get("physicalFeatureRef"),
                    "includedInDisplay": True,
                    "excludedFromManufacturing": True,
                    "reason": "display-only nodes never alter physical contours",
                }
                included_display.append(display_record)
            excluded_display.append(
                {
                    "nodeId": node["id"],
                    "physicalFeatureRef": node.get("physicalFeatureRef"),
                    "reason": "display-only nodes never alter physical contours",
                }
            )
            manufacturing_exclusions.append(
                {
                    "nodeId": node["id"],
                    "physicalFeatureRef": node.get("physicalFeatureRef"),
                    "includedInDisplay": node["recipe"]["kind"]
                    == "displayComponent",
                    "excludedFromManufacturing": True,
                    "reason": "display-only nodes never alter physical contours",
                }
            )
            continue
        if node["recipe"]["kind"] == SELF_TAPPING_RECIPE_KIND:
            mesh, fastener_evidence = _load_self_tapping_node(
                node,
                scene,
                procedural_fasteners,
            )
            group_key = (
                f"{node['recipe']['parameters']['interfaceId']}/"
                f"{node['recipe']['parameters']['fastenerId']}"
            )
            fastener_groups[group_key] = fastener_evidence
        else:
            mesh, bound_spec = _load_node_mesh(node, base_dir)
            bound_sources[node["id"]] = bound_spec
        part_id = node["partId"]
        feature_id = node["featureId"]
        if feature_id in feature_records:
            raise CompileError(
                f"feature {feature_id!r} is implemented by more than one physical node"
            )
        feature_records[feature_id] = {
            "bbox_mm": {
                "max": _vector(mesh.bounds[1]),
                "min": _vector(mesh.bounds[0]),
                "size": _vector(mesh.extents),
            },
            "nodeId": node["id"],
            "part": part_id,
            "role": role,
            "volume_mm3": round(abs(float(mesh.volume)), 6),
        }
        if role in {"solid", "separate"}:
            positive[part_id].append(mesh)
            positive_node_ids[part_id].append(node["id"])
        elif role == "cutter":
            cutters[part_id].append(mesh)
            cutter_node_ids[part_id].append(node["id"])

    compiled: list[tuple[str, trimesh.Trimesh, dict[str, Any]]] = []
    part_records: dict[str, dict[str, Any]] = {}
    part_removed_volume: dict[str, float] = {}
    warnings: list[str] = []
    for part_id, part in parts.items():
        body = _union(positive[part_id], part_id)
        body, removed = _difference(body, cutters[part_id], part_id)
        body_count = len(body.split(only_watertight=False))
        if body_count != 1:
            raise CompileError(
                f"part {part_id} must compile to one fused physical body; got {body_count}"
            )
        appearance = _material_appearance(materials[part_material_ids[part_id]])
        compiled.append((part_id, body, appearance))
        part_removed_volume[part_id] = removed
        if part["representationMaster"] == "brep":
            warnings.append(
                f"part {part_id}: mesh artifacts are verified derivatives of its OCCT-imported STEP master"
            )

    step_consistency = _compare_master_steps(
        scene=scene,
        master_steps=master_steps,
        step_meshes=master_step_meshes,
        compiled_meshes={part_id: mesh for part_id, mesh, _ in compiled},
        sample_count=consistency_samples,
    )
    step_consistency_file = output_dir / f"{model_name}_step-consistency.json"
    _json_write(step_consistency_file, step_consistency)

    maximum_overlap_mm3 = 0.01
    assembly_overlaps_mm3: dict[str, float] = {}
    for index, (left_id, left_mesh, _) in enumerate(compiled):
        for right_id, right_mesh, _ in compiled[index + 1 :]:
            overlap = _intersection_volume(
                left_mesh,
                right_mesh,
                f"assembly parts {left_id}&{right_id}",
            )
            pair_id = "&".join(sorted((left_id, right_id)))
            assembly_overlaps_mm3[pair_id] = round(overlap, 9)
            if overlap > maximum_overlap_mm3:
                raise CompileError(
                    f"assembly parts {left_id!r} and {right_id!r} overlap by "
                    f"{overlap:.9g} mm3 (maximum {maximum_overlap_mm3:.9g} mm3)"
                )

    fastener_geometry_checks = _verify_self_tapping_geometry(
        scene,
        {part_id: mesh for part_id, mesh, _ in compiled},
        feature_records,
    )
    color_regions, region_bound_sources = _compile_color_regions(
        scene,
        base_dir,
        {part_id: mesh for part_id, mesh, _ in compiled},
    )
    (
        part_print_meshes,
        plate_print_meshes,
        part_print_transforms,
        plate_print_transforms,
        orientation_evidence,
        plate_layout,
    ) = _compose_plate_layout(compiled, profile)

    plate_color_regions: dict[str, list[dict[str, Any]]] = {}
    for part_id, regions in color_regions.items():
        transform = np.asarray(plate_print_transforms[part_id], dtype=float)
        plate_color_regions[part_id] = []
        for region in regions:
            mesh = region["mesh"].copy()
            mesh.apply_transform(transform)
            plate_color_regions[part_id].append({**region, "mesh": mesh})

    # Persist manufacturing artifacts only after all in-memory geometry probes
    # pass, so a rejected joint cannot leave apparently usable part files.
    for part_id, body, appearance in compiled:
        part = parts[part_id]
        part_file = output_dir / f"{part_id}.stl"
        print_mesh = part_print_meshes[part_id]
        print_mesh.export(part_file, file_type="stl")
        print_record = _mesh_record(print_mesh, part_file)
        part_records[part_id] = {
            **print_record,
            "appearance": appearance,
            "cutterNodeIds": cutter_node_ids[part_id],
            "materialId": part_material_ids[part_id],
            "orientation": orientation_evidence[part_id],
            "positiveNodeIds": positive_node_ids[part_id],
            "printTransform": part_print_transforms[part_id],
            "print": {
                "bodyCount": len(print_mesh.split(only_watertight=False)),
                "boundsMm": deepcopy(print_record["boundsMm"]),
                "isVolume": print_record["isVolume"],
                "valid": bool(
                    print_record["isVolume"]
                    and print_record["watertight"]
                    and print_record["windingConsistent"]
                ),
                "volumeMm3": print_record["volumeMm3"],
            },
            "representationMaster": part["representationMaster"],
            "semantic": {
                "bodyCount": len(body.split(only_watertight=False)),
                "boundsMm": {
                    "min": _vector(body.bounds[0]),
                    "max": _vector(body.bounds[1]),
                    "size": _vector(body.extents),
                },
                "isVolume": bool(body.is_volume),
                "valid": bool(
                    body.is_watertight
                    and body.is_winding_consistent
                    and body.is_volume
                ),
                "volumeMm3": round(float(body.volume), 9),
            },
            "volumeRemovedMm3": round(part_removed_volume[part_id], 9),
        }
        if part_id in master_steps:
            part_records[part_id]["masterStep"] = master_steps[part_id]
            part_records[part_id]["stepConsistency"] = deepcopy(
                step_consistency["parts"][part_id]
            )
        part_records[part_id]["colorRegions"] = [
            {
                "bodyCount": len(region["mesh"].split(only_watertight=False)),
                "id": region["id"],
                "isVolume": bool(region["mesh"].is_volume),
                "materialId": region["materialId"],
                "sourceMesh": region["sourceMesh"],
                "valid": bool(
                    region["mesh"].is_watertight
                    and region["mesh"].is_winding_consistent
                    and region["mesh"].is_volume
                ),
            }
            for region in color_regions.get(part_id, [])
        ]

    combined = trimesh.util.concatenate(list(plate_print_meshes.values()))
    combined_file = output_dir / f"{model_name}.stl"
    combined.export(combined_file, file_type="stl")
    physical_glb = output_dir / f"{model_name}-display.glb"
    glb_report = _write_physical_glb(
        compiled,
        color_regions,
        materials,
        display_compiled,
        physical_glb,
        scene["revision"],
    )
    package_mode = intent_data["printability"].get("print_package_mode")
    if package_mode is None:
        package_mode = (
            "co_print_body"
            if intent_data["manufacturing"]["mode"] == "single-part"
            else "separate_parts"
        )
    colored_3mf = output_dir / f"{model_name}.3mf"
    three_mf_report = _write_material_3mf(
        [
            (part_id, plate_print_meshes[part_id], part_material_ids[part_id])
            for part_id in parts
        ],
        plate_color_regions,
        materials,
        colored_3mf,
        package_mode=package_mode,
    )

    material_plan = build_material_plan(
        part=model_name,
        package_mode=package_mode,
        materials=(
            {
                field: material.get(field)
                for field in (
                    "color",
                    "fieldStatus",
                    "filament",
                    "id",
                    "status",
                    "transmission",
                )
            }
            for material in (materials[key] for key in sorted(materials))
        ),
        assignments=three_mf_report["assignments"],
        source_bindings=source_bindings,
    )
    source_errors = validate_material_sources(material_plan, intent_data, scene)
    if source_errors:
        raise CompileError(
            "invalid material provenance: " + "; ".join(source_errors)
        )
    material_plan_file = output_dir / f"{model_name}_material-plan.json"
    _json_write(material_plan_file, material_plan)

    bound = _bind_scene(
        scene,
        base_dir=base_dir,
        part_records=part_records,
        physical_glb=physical_glb,
        combined_stl=combined_file,
        colored_3mf=colored_3mf,
        bound_sources=bound_sources,
        region_bound_sources=region_bound_sources,
        part_print_transforms=part_print_transforms,
        physical_part_node_names=glb_report["physicalPartNodeNames"],
        fastener_groups=fastener_groups,
        fastener_geometry_checks=fastener_geometry_checks,
    )
    bound_errors = validate_scene(bound, output_dir)
    if bound_errors:
        raise CompileError(
            "compiler produced an invalid bound scene: " + "; ".join(bound_errors)
        )
    bound_scene = output_dir / f"{model_name}_scene_artifacts.json"
    _json_write(bound_scene, bound)

    consistency = compare_manifest(
        bound,
        base_dir=output_dir,
        dimension_tolerance_mm=0.02,
        surface_p99_tolerance_mm=0.02,
        surface_max_tolerance_mm=0.05,
        sample_count=consistency_samples,
    )
    consistency_file = output_dir / f"{model_name}_shape-consistency.json"
    _json_write(consistency_file, consistency)

    report_file = output_dir / f"{model_name}_report.json"
    scene_record = {
        **_artifact_record(source_path),
        "revision": scene["revision"],
        "schema": scene["schema"],
    }
    geometry_inputs = {
        **{
            f"node:{node_id}": {
                "path": spec["path"],
                "schema": "mesh-source/v1",
                "sha256": spec["sha256"],
            }
            for node_id, spec in bound_sources.items()
        },
        **{
            f"region:{region_id}": {
                "path": spec["path"],
                "schema": "mesh-source/v1",
                "sha256": spec["sha256"],
            }
            for region_id, spec in region_bound_sources.items()
        },
    }
    artifact_records: dict[str, dict[str, Any]] = {
        "3mf": {**three_mf_report, "coordinateFrame": "plate-print"},
        "boundScene": _artifact_record(bound_scene),
        "glb:display": {**glb_report, "coordinateFrame": "semantic"},
        "materialPlan": _artifact_record(material_plan_file),
        "shapeConsistency": _artifact_record(consistency_file),
        "stepConsistency": _artifact_record(step_consistency_file),
        "stl": {
            **_mesh_record(combined, combined_file),
            "coordinateFrame": "plate-print",
        },
    }
    for part_id, record in part_records.items():
        artifact_records[f"stl:{part_id}"] = {
            "coordinateFrame": "part-print",
            "path": record["path"],
            "sha256": record["sha256"],
        }
    for part_id, record in master_steps.items():
        artifact_records[f"step:{part_id}"] = {
            **record,
            "coordinateFrame": "semantic",
        }

    try:
        semantic_assembly = semantic_assembly_record(
            part_records, intent_record["sha256"], intent_data
        )
    except ValueError as error:
        raise CompileError(str(error)) from error

    report: dict[str, Any] = {
        "artifactMatrix": {
            "parts": {
                part_id: {
                    "glb": "required",
                    "step": (
                        "required"
                        if part["representationMaster"] == "brep"
                        else "not-applicable"
                    ),
                    "stl": "required",
                    "threeMf": "required",
                }
                for part_id, part in parts.items()
            }
        },
        "artifacts": artifact_records,
        "autoScale": False,
        "backend": "hybrid-mesh",
        "backendData": {
            "assembly": {
                "maxOverlapMm3": maximum_overlap_mm3,
                "overlapsMm3": assembly_overlaps_mm3,
            },
            "printPlate": {
                "boundsMm": deepcopy(artifact_records["stl"]["boundsMm"]),
                "layout": plate_layout,
                "valid": bool(
                    artifact_records["stl"]["isVolume"]
                    and artifact_records["stl"]["watertight"]
                    and artifact_records["stl"]["windingConsistent"]
                ),
                "volumeMm3": artifact_records["stl"]["volumeMm3"],
            },
            "printPackageMode": package_mode,
            "semanticAssembly": semantic_assembly,
            "stepConsistency": deepcopy(step_consistency),
            "threeMf": deepcopy(three_mf_report),
        },
        "builtAt": utc_timestamp(),
        "coordinateFrames": {
            "semantic": {
                "handedness": scene["coordinateSystem"]["handedness"],
                "scale": 1.0,
                "units": "mm",
                "up": scene["coordinateSystem"]["up"],
            },
            "part-print": {
                "partTransforms": part_print_transforms,
                "scale": 1.0,
                "units": "mm",
            },
            "plate-print": {
                "layout": plate_layout,
                "partTransforms": plate_print_transforms,
                "profileId": profile_record["id"],
                "scale": 1.0,
                "units": "mm",
            },
        },
        "excludedDisplayNodes": excluded_display,
        "excludedFromManufacturingNodes": manufacturing_exclusions,
        "fastenerGeometryChecks": fastener_geometry_checks,
        "fastenerGroups": fastener_groups,
        "features": feature_records,
        "includedDisplayNodes": included_display,
        "inputs": {
            "geometry": geometry_inputs,
            "intent": intent_record,
            "profile": profile_record,
            "scene": scene_record,
        },
        "materialPlan": material_plan,
        "part": model_name,
        "parts": part_records,
        "pass": bool(
            consistency["pass"]
            and step_consistency["pass"]
            and three_mf_report["verified"]
        ),
        "revision": scene["revision"],
        "runId": new_run_id(),
        "scale": 1.0,
        "schema": REPORT_SCHEMA,
        "warnings": warnings,
    }
    manifest_errors = validate_manifest(report)
    if manifest_errors:
        raise CompileError(
            "compiler produced an invalid unified build manifest: "
            + "; ".join(manifest_errors)
        )
    _json_write(report_file, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="artifact directory (recommended/default: current directory)",
    )
    parser.add_argument("--consistency-samples", type=int, default=1024)
    args = parser.parse_args(argv)
    scene_path = args.scene.resolve()
    output_dir = args.output_dir.resolve()
    try:
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        report = compile_scene(
            scene,
            base_dir=scene_path.parent,
            output_dir=output_dir,
            source_scene=scene_path,
            consistency_samples=args.consistency_samples,
        )
    except Exception as error:
        print(
            json.dumps(
                {"error": str(error), "pass": False, "schema": REPORT_SCHEMA},
                indent=2,
            )
        )
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
