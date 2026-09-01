"""Compile source meshes in a semantic scene into physical mesh artifacts.

This is intentionally a small mesh compiler, not a universal CAD kernel.  It
loads ``recipe.parameters.sourceMesh`` for physical nodes, evaluates semantic
union/subtract operations with Manifold, and emits STL, part-colored 3MF, and a
PBR physical GLB.  It never rescales geometry and never manufactures STEP; a
part whose representation master is B-rep keeps its STEP in the build123d
pipeline and uses this compiler only for mesh derivatives.
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

from scene_contract import validate as validate_scene
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
    warnings: list[str] = []
    for part_id, part in parts.items():
        body = _union(positive[part_id], part_id)
        body, removed = _difference(body, cutters[part_id], part_id)
        part_file = output_dir / f"{part_id}.stl"
        body.export(part_file, file_type="stl")
        appearance = _appearance(part)
        record = {
            **_mesh_record(body, part_file),
            "appearance": appearance,
            "cutterNodeIds": cutter_node_ids[part_id],
            "positiveNodeIds": positive_node_ids[part_id],
            "representationMaster": part["representationMaster"],
            "volumeRemovedMm3": round(removed, 9),
        }
        part_records[part_id] = record
        compiled.append((part_id, body, appearance))
        if part["representationMaster"] == "brep":
            warnings.append(
                f"part {part_id}: mesh artifacts are derivatives; STEP remains owned by build123d"
            )

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
