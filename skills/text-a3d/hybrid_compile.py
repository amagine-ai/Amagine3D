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
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import trimesh
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
from scene_contract import SELF_TAPPING_RECIPE_KIND, validate as validate_scene
from shape_consistency import compare_manifest, load_artifact


REPORT_SCHEMA = "evidence-hybrid-mesh-build/v1"
COLOR_3MF_SCHEMA = "evidence-part-color-3mf/v1"
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
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _artifact_record(path: Path) -> dict[str, str]:
    """Return the file-reference shape consumed by PI/server discovery."""

    return {"path": str(path.resolve()), "sha256": _digest(path)}


def _model_name(
    scene: dict[str, Any],
    base_dir: Path,
    source_scene: Path | None,
) -> str:
    """Resolve the build name from immutable intent, with safe fallbacks."""

    intent_path = Path(scene["intentRef"]["path"])
    if not intent_path.is_absolute():
        intent_path = base_dir / intent_path
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        intent = {}
    candidate = intent.get("part") if isinstance(intent, dict) else None
    if isinstance(candidate, str) and MODEL_NAME.fullmatch(candidate):
        return candidate
    if source_scene is not None:
        stem = source_scene.stem
        for suffix in ("_scene", "-scene"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if MODEL_NAME.fullmatch(stem):
            return stem
    if len(scene["parts"]) == 1:
        return scene["parts"][0]["id"]
    return "hybrid-assembly"


def _vector(values: Iterable[float]) -> list[float]:
    return [round(float(value), 9) for value in values]


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
        boss_root_overlap_mm=receiver["rootOverlapMm"],
        head_recess_diameter_mm=cover.get("headRecessDiameterMm"),
        head_recess_depth_mm=cover.get("headRecessDepthMm"),
        minimum_cover_land_mm=cover.get("minimumResidualWallMm", 0.8),
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


def _radial_unit(direction: np.ndarray) -> np.ndarray:
    trial = np.asarray([1.0, 0.0, 0.0])
    if abs(float(np.dot(direction, trial))) > 0.9:
        trial = np.asarray([0.0, 1.0, 0.0])
    radial = np.cross(direction, trial)
    return radial / np.linalg.norm(radial)


def _axis_triangle_intersections(
    mesh: trimesh.Trimesh,
    origin: np.ndarray,
    direction: np.ndarray,
    *,
    tolerance: float = 1e-8,
) -> list[float]:
    """Return signed center-axis surface hits without optional ray dependencies."""

    triangles = np.asarray(mesh.triangles, dtype=float)
    if len(triangles) == 0:
        return []
    direction = np.asarray(direction, dtype=float)
    direction = direction / np.linalg.norm(direction)
    origin = np.asarray(origin, dtype=float)
    edge_a = triangles[:, 1] - triangles[:, 0]
    edge_b = triangles[:, 2] - triangles[:, 0]
    cross_direction_b = np.cross(np.broadcast_to(direction, edge_b.shape), edge_b)
    determinant = np.einsum("ij,ij->i", edge_a, cross_direction_b)
    usable = np.abs(determinant) > tolerance
    inverse = np.zeros_like(determinant)
    inverse[usable] = 1.0 / determinant[usable]
    offset = origin - triangles[:, 0]
    barycentric_a = inverse * np.einsum("ij,ij->i", offset, cross_direction_b)
    cross_offset_a = np.cross(offset, edge_a)
    barycentric_b = inverse * np.einsum(
        "ij,ij->i",
        np.broadcast_to(direction, cross_offset_a.shape),
        cross_offset_a,
    )
    distance = inverse * np.einsum("ij,ij->i", edge_b, cross_offset_a)
    hit = (
        usable
        & (barycentric_a >= -tolerance)
        & (barycentric_b >= -tolerance)
        & (barycentric_a + barycentric_b <= 1.0 + tolerance)
    )
    distances = sorted(float(item) for item in distance[hit])
    unique: list[float] = []
    for item in distances:
        if not unique or abs(item - unique[-1]) > 1e-6:
            unique.append(item)
    return unique


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


def _probe_tolerance(probe: trimesh.Trimesh) -> float:
    return max(1e-5, abs(float(probe.volume)) * 1e-9)


def _segment_cylinder(
    radius: float,
    start: np.ndarray,
    end: np.ndarray,
) -> trimesh.Trimesh:
    return trimesh.creation.cylinder(
        radius=radius,
        segment=np.asarray([start, end], dtype=float),
        sections=96,
    )


def _segment_annulus(
    inner_radius: float,
    outer_radius: float,
    start: np.ndarray,
    end: np.ndarray,
) -> trimesh.Trimesh:
    return trimesh.creation.annulus(
        r_min=inner_radius,
        r_max=outer_radius,
        segment=np.asarray([start, end], dtype=float),
        sections=96,
    )


def _verify_self_tapping_geometry(
    scene: dict[str, Any],
    part_meshes: dict[str, trimesh.Trimesh],
) -> dict[str, dict[str, Any]]:
    """Use boolean witness volumes to prove the compiled screw geometry."""

    results: dict[str, dict[str, Any]] = {}
    for interface in scene.get("interfaces", []):
        if not isinstance(interface, dict) or interface.get("kind") != "self-tapping-screw":
            continue
        interface_id = interface["id"]
        for fastener in interface.get("fasteners", []):
            fastener_id = fastener["id"]
            key = f"{interface_id}/{fastener_id}"
            axis = fastener["axis"]
            origin = np.asarray(axis["originMm"], dtype=float)
            direction = np.asarray(axis["direction"], dtype=float)
            direction = direction / np.linalg.norm(direction)
            cover = fastener["cover"]
            receiver = fastener["receiver"]
            cover_mesh = part_meshes[cover["partId"]]
            receiver_mesh = part_meshes[receiver["partId"]]
            clearance_diameter = float(cover["diameterMm"])
            pilot_diameter = float(receiver["diameterMm"])
            pilot_depth = (
                float(receiver["engagementMm"])
                + float(receiver["tipClearanceMm"])
            )
            axial_pad = 0.05
            cover_projection = np.einsum(
                "ij,j->i",
                np.asarray(cover_mesh.vertices, dtype=float) - origin,
                direction,
            )
            actual_cover_min = float(cover_projection.min())
            clearance_inset = min(0.02, clearance_diameter * 0.005)
            clearance_probe = _segment_cylinder(
                clearance_diameter / 2 - clearance_inset,
                origin + direction * (actual_cover_min - axial_pad),
                origin + direction * axial_pad,
            )
            clearance_overlap = _intersection_volume(
                cover_mesh,
                clearance_probe,
                f"self-tapping geometry {key} clearance",
            )

            pilot_inset = min(0.02, pilot_diameter * 0.005)
            pilot_axial_inset = min(0.02, pilot_depth * 0.01)
            pilot_probe = _segment_cylinder(
                pilot_diameter / 2 - pilot_inset,
                origin + direction * pilot_axial_inset,
                origin + direction * (pilot_depth - pilot_axial_inset),
            )
            pilot_overlap = _intersection_volume(
                receiver_mesh,
                pilot_probe,
                f"self-tapping geometry {key} pilot",
            )

            minimum_boss_wall = float(receiver["minimumBossWallMm"])
            boss_radial_inset = min(0.05, minimum_boss_wall * 0.05)
            boss_wall_witness = _segment_annulus(
                pilot_diameter / 2 + boss_radial_inset,
                pilot_diameter / 2 + minimum_boss_wall - boss_radial_inset,
                origin + direction * pilot_axial_inset,
                origin + direction * (pilot_depth - pilot_axial_inset),
            )
            boss_wall_missing = _missing_volume(
                boss_wall_witness,
                receiver_mesh,
                f"self-tapping geometry {key} boss wall",
            )

            closed_end = float(receiver["closedEndMm"])
            closed_axial_inset = min(0.02, closed_end * 0.05)
            closed_end_witness = _segment_cylinder(
                pilot_diameter / 2 - pilot_inset,
                origin + direction * (pilot_depth + closed_axial_inset),
                origin
                + direction * (pilot_depth + closed_end - closed_axial_inset),
            )
            closed_end_missing = _missing_volume(
                closed_end_witness,
                receiver_mesh,
                f"self-tapping geometry {key} blind end",
            )

            if "headRecessDiameterMm" in cover:
                head_diameter = float(cover["headRecessDiameterMm"])
                minimum_cover_land = float(cover["minimumResidualWallMm"])
                radial_band = (head_diameter - clearance_diameter) / 2
                land_radial_inset = min(0.02, radial_band * 0.2)
                land_axial_inset = min(0.02, minimum_cover_land * 0.05)
                cover_land_witness = _segment_annulus(
                    clearance_diameter / 2 + land_radial_inset,
                    head_diameter / 2 - land_radial_inset,
                    origin
                    - direction * (minimum_cover_land - land_axial_inset),
                    origin - direction * land_axial_inset,
                )
                cover_land_name = "head_recess_floor_is_complete"
            else:
                radial_land = 0.5
                land_radial_inset = min(0.02, radial_land * 0.05)
                land_depth = min(0.4, float(cover["thicknessMm"]) * 0.25)
                land_half_depth = min(0.05, land_depth * 0.25)
                cover_land_witness = _segment_annulus(
                    clearance_diameter / 2 + land_radial_inset,
                    clearance_diameter / 2 + radial_land - land_radial_inset,
                    origin - direction * (land_depth + land_half_depth),
                    origin - direction * (land_depth - land_half_depth),
                )
                cover_land_name = "cover_radial_land_is_complete"
            cover_land_missing = _missing_volume(
                cover_land_witness,
                cover_mesh,
                f"self-tapping geometry {key} cover land",
            )

            checks = {
                "clearance_volume_is_open": clearance_overlap
                <= _probe_tolerance(clearance_probe),
                "pilot_volume_is_open": pilot_overlap
                <= _probe_tolerance(pilot_probe),
                "receiver_boss_wall_is_complete": boss_wall_missing
                <= _probe_tolerance(boss_wall_witness),
                "pilot_closed_end_is_complete": closed_end_missing
                <= _probe_tolerance(closed_end_witness),
                cover_land_name: cover_land_missing
                <= _probe_tolerance(cover_land_witness),
            }
            clearance_axis_hits = _axis_triangle_intersections(
                cover_mesh,
                origin,
                direction,
            )
            if not all(checks.values()):
                failed = sorted(name for name, passed in checks.items() if not passed)
                raise CompileError(
                    f"self-tapping geometry {key} failed compiled probes: "
                    + ", ".join(failed)
                )
            results[key] = {
                "checks": checks,
                "pass": True,
                "clearanceAxisIntersectionDistancesMm": clearance_axis_hits,
                "measurementsMm3": {
                    "clearanceOverlap": round(clearance_overlap, 9),
                    "coverLandMissing": round(cover_land_missing, 9),
                    "pilotOverlap": round(pilot_overlap, 9),
                    "receiverBossWallMissing": round(boss_wall_missing, 9),
                    "receiverClosedEndMissing": round(closed_end_missing, 9),
                },
                "tolerancesMm3": {
                    "clearance": _probe_tolerance(clearance_probe),
                    "coverLand": _probe_tolerance(cover_land_witness),
                    "pilot": _probe_tolerance(pilot_probe),
                    "receiverBossWall": _probe_tolerance(boss_wall_witness),
                    "receiverClosedEnd": _probe_tolerance(closed_end_witness),
                },
                "witnessVolumesMm3": {
                    "clearance": round(float(clearance_probe.volume), 9),
                    "coverLand": round(float(cover_land_witness.volume), 9),
                    "pilot": round(float(pilot_probe.volume), 9),
                    "receiverBossWall": round(float(boss_wall_witness.volume), 9),
                    "receiverClosedEnd": round(float(closed_end_witness.volume), 9),
                },
            }
    return results


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
    index = sha256(part_id.encode("utf-8")).digest()[0] % len(DEFAULT_PALETTE)
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


def _display_appearance(node: dict[str, Any]) -> dict[str, Any]:
    """Resolve an explicitly visual material without mutating physical color."""

    parameters = node["recipe"]["parameters"]
    raw = parameters.get("appearance")
    appearance = raw if isinstance(raw, dict) else {}
    material = parameters.get("material")
    # Preserve the existing displayProxy convention while allowing a
    # displayComponent to declare normal PBR-like appearance fields.
    fallback_color = "#111417" if material == "dark-aperture" else None
    color = _normalize_color(
        parameters.get(
            "color",
            appearance.get("baseColor", appearance.get("color", fallback_color)),
        ),
        node["id"],
    )
    metallic = appearance.get(
        "metallic",
        appearance.get("metallicFactor", 0.0),
    )
    roughness = appearance.get(
        "roughness",
        appearance.get("roughnessFactor", 0.28 if fallback_color else 0.58),
    )
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


def _write_physical_glb(
    parts: list[tuple[str, trimesh.Trimesh, dict[str, Any]]],
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
    for part_id, mesh, appearance in parts:
        display = mesh.copy()
        display.visual = TextureVisuals(material=_pbr_material(part_id, appearance))
        scene.add_geometry(
            display,
            node_name=part_id,
            geom_name=f"{part_id}-geometry",
        )
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
    expected_physical = sorted(part_id for part_id, _, _ in parts)
    expected_display = sorted(node_id for node_id, _, _ in display_nodes)
    expected = sorted([*expected_physical, *expected_display])
    if node_names != expected:
        raise CompileError(
            f"physical GLB node readback mismatch: expected {expected}, got {node_names}"
        )
    return {
        **_artifact_record(output),
        "nodeNames": node_names,
        "physicalNodeNames": expected_physical,
        "displayOnlyNodeNames": expected_display,
        "revision": revision,
        "scale": 1.0,
        "verified": True,
    }


def _write_part_color_3mf(
    parts: list[tuple[str, trimesh.Trimesh, dict[str, Any]]],
    output: Path,
) -> dict[str, Any]:
    """Write Core 3MF objects with one object-level color per physical part."""

    if not parts:
        raise CompileError("cannot write a 3MF without physical parts")
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("m", MATERIAL_NS)
    model = ET.Element(
        f"{{{CORE_NS}}}model",
        {
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
    ).text = "part-level-v1"
    resources = ET.SubElement(model, f"{{{CORE_NS}}}resources")

    colors = list(
        dict.fromkeys(appearance["baseColor"] for _, _, appearance in parts)
    )
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
    object_ids: list[str] = []
    for index, (part_id, mesh, appearance) in enumerate(parts, start=2):
        object_id = str(index)
        object_ids.append(object_id)
        color = appearance["baseColor"]
        obj = ET.SubElement(
            resources,
            f"{{{CORE_NS}}}object",
            {
                "id": object_id,
                "name": part_id,
                "pindex": str(colors.index(color)),
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
        object_records.append(
            {
                "color": color,
                "id": int(object_id),
                "name": part_id,
                "triangles": int(len(mesh.faces)),
                "vertices": int(len(mesh.vertices)),
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
    verified = (
        readback.attrib.get("unit") == "millimeter"
        and readback_names == [record["name"] for record in object_records]
        and len(readback_items) == len(parts)
    )
    if not verified:
        raise CompileError("colored 3MF readback verification failed")
    return {
        **_artifact_record(output),
        "archiveEntries": names,
        "buildItemCount": len(readback_items),
        "colorScope": "part-level",
        "objectCount": len(readback_objects),
        "objects": object_records,
        "regionColoring": "not-supported-in-v1",
        "schema": COLOR_3MF_SCHEMA,
        "unit": readback.attrib.get("unit"),
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
        part["artifacts"] = {
            "manufacturingStl": {
                "path": part_file.name,
                "revision": bound["revision"],
                "scale": 1.0,
            },
            "physicalGlb": {
                "nodeNames": [part_id],
                "path": physical_glb.name,
                "revision": bound["revision"],
                "scale": 1.0,
            },
        }
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
        "colorScope": "part-level-v1",
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
    source_scene: Path | None = None,
    consistency_samples: int = 1024,
) -> dict[str, Any]:
    """Compile a validated scene and return its persisted build report."""

    if MANIFOLD_IMPORT_ERROR is not None:
        raise CompileError(
            "manifold3d is required for hybrid mesh booleans"
        ) from MANIFOLD_IMPORT_ERROR
    base_dir = base_dir.resolve()
    output_dir = output_dir.resolve()
    errors = validate_scene(scene, base_dir)
    if errors:
        raise CompileError("invalid semantic scene: " + "; ".join(errors))
    if consistency_samples < 32:
        raise CompileError("consistency_samples must be at least 32")
    output_dir.mkdir(parents=True, exist_ok=True)
    model_name = _model_name(scene, base_dir, source_scene)

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
        appearance = _appearance(part)
        compiled.append((part_id, body, appearance))
        part_removed_volume[part_id] = removed
        if part["representationMaster"] == "brep":
            warnings.append(
                f"part {part_id}: mesh artifacts are derivatives; STEP remains owned by build123d"
            )

    fastener_geometry_checks = _verify_self_tapping_geometry(
        scene,
        {part_id: mesh for part_id, mesh, _ in compiled},
    )

    # Persist manufacturing artifacts only after all in-memory geometry probes
    # pass, so a rejected joint cannot leave apparently usable part files.
    for part_id, body, appearance in compiled:
        part = parts[part_id]
        part_file = output_dir / f"{part_id}.stl"
        body.export(part_file, file_type="stl")
        part_records[part_id] = {
            **_mesh_record(body, part_file),
            "appearance": appearance,
            "cutterNodeIds": cutter_node_ids[part_id],
            "positiveNodeIds": positive_node_ids[part_id],
            "representationMaster": part["representationMaster"],
            "volumeRemovedMm3": round(part_removed_volume[part_id], 9),
        }

    combined = trimesh.util.concatenate([mesh for _, mesh, _ in compiled])
    combined_file = output_dir / f"{model_name}.stl"
    combined.export(combined_file, file_type="stl")
    physical_glb = output_dir / f"{model_name}-display.glb"
    glb_report = _write_physical_glb(
        compiled, display_compiled, physical_glb, scene["revision"]
    )
    colored_3mf = output_dir / f"{model_name}.3mf"
    three_mf_report = _write_part_color_3mf(compiled, colored_3mf)

    bound = _bind_scene(
        scene,
        base_dir=base_dir,
        part_records=part_records,
        physical_glb=physical_glb,
        combined_stl=combined_file,
        colored_3mf=colored_3mf,
        bound_sources=bound_sources,
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
    source_path = source_scene.resolve() if source_scene is not None else bound_scene
    report: dict[str, Any] = {
        "artifacts": {
            "3mf": three_mf_report,
            "glb:display": glb_report,
            "shapeConsistency": _artifact_record(consistency_file),
            "stl": {
                **_mesh_record(combined, combined_file),
                "parts": {
                    part_id: {
                        "path": record["path"],
                        "sha256": record["sha256"],
                    }
                    for part_id, record in part_records.items()
                },
            },
        },
        "autoScale": False,
        "boundScene": _artifact_record(bound_scene),
        "excludedDisplayNodes": excluded_display,
        "excludedFromManufacturingNodes": manufacturing_exclusions,
        "fastenerGeometryChecks": fastener_geometry_checks,
        "fastenerGroups": fastener_groups,
        "includedDisplayNodes": included_display,
        "part": model_name,
        "parts": part_records,
        "pass": bool(consistency["pass"] and three_mf_report["verified"]),
        "report": str(report_file.resolve()),
        "revision": scene["revision"],
        "scale": 1.0,
        "schema": REPORT_SCHEMA,
        "source": {
            **_artifact_record(source_path),
            "revision": scene["revision"],
            "schema": scene["schema"],
            "units": scene["units"],
        },
        "step": {
            "generated": False,
            "owner": "build123d",
            "reason": "hybrid_compile only emits physical mesh derivatives",
        },
        "warnings": warnings,
    }
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
