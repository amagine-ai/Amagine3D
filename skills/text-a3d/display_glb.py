"""Shared physical-plus-display-only GLB composition and readback."""

from __future__ import annotations

from hashlib import sha256
import math
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np
import trimesh
from trimesh.visual import TextureVisuals
from trimesh.visual.material import PBRMaterial

from display_normals import mesh_with_display_normals
from shape_consistency import ConsistencyError, load_artifact


HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


class DisplayGlbError(RuntimeError):
    """Raised when display composition or readback is not trustworthy."""


def appearance(
    base_color: str,
    *,
    metallic: float = 0.0,
    roughness: float = 0.58,
) -> dict[str, Any]:
    if not isinstance(base_color, str) or not HEX_COLOR.fullmatch(base_color):
        raise DisplayGlbError("base color must be #RRGGBB")
    values = {"metallic": metallic, "roughness": roughness}
    for name, value in values.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise DisplayGlbError(f"{name} must be between 0 and 1")
    return {
        "baseColor": base_color.upper(),
        "metallic": float(metallic),
        "roughness": float(roughness),
    }


def load_display_component(
    node: dict[str, Any], base_dir: str | Path
) -> tuple[str, trimesh.Trimesh, dict[str, Any], dict[str, Any]]:
    """Load one display-only node and return its bound source evidence."""

    root = Path(base_dir).resolve()
    parameters = node["recipe"]["parameters"]
    raw_source = parameters["sourceMesh"]
    if isinstance(raw_source, str):
        source = {"path": raw_source, "scale": 1.0}
    elif isinstance(raw_source, dict):
        source = dict(raw_source)
    else:
        raise DisplayGlbError(
            f"display component {node['id']!r} sourceMesh must be a path or object"
        )
    if source.get("scale", 1.0) != 1.0:
        raise DisplayGlbError(
            f"display component {node['id']!r} source scale must equal 1"
        )
    raw_path = source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise DisplayGlbError(
            f"display component {node['id']!r} source path is required"
        )
    source_path = Path(raw_path)
    if not source_path.is_absolute():
        source_path = (root / source_path).resolve()
    try:
        observed_digest = sha256(source_path.read_bytes()).hexdigest()
    except OSError as error:
        raise DisplayGlbError(
            f"display component {node['id']!r} source cannot be read: {error}"
        ) from error
    declared_digest = source.get("sha256")
    if declared_digest is not None and declared_digest != observed_digest:
        raise DisplayGlbError(
            f"display component {node['id']!r} source sha256 does not match"
        )
    source["path"] = str(source_path)
    source["scale"] = 1.0
    source["sha256"] = observed_digest
    try:
        mesh = load_artifact(source, root)
    except (ConsistencyError, OSError, TypeError, ValueError) as error:
        raise DisplayGlbError(
            f"display component {node['id']!r} source cannot be loaded: {error}"
        ) from error
    mesh.metadata["amagine3d"] = {
        "role": "display-only",
        "physicalFeatureRef": node["physicalFeatureRef"],
    }
    visual = parameters["appearance"]
    return (
        node["id"],
        mesh,
        appearance(
            visual["baseColor"],
            metallic=visual.get("metallic", 0.0),
            roughness=visual.get("roughness", 0.58),
        ),
        source,
    )


def load_display_components(
    scene_data: dict[str, Any], scene_path: str | Path
) -> list[tuple[str, trimesh.Trimesh, dict[str, Any]]]:
    """Load all scene-declared visual components without manufacturing them."""

    base_dir = Path(scene_path).resolve().parent
    return [
        load_display_component(node, base_dir)[:3]
        for node in scene_data.get("nodes", [])
        if isinstance(node, dict) and node.get("role") == "display-only"
    ]


def _material(node_name: str, visual: dict[str, Any]) -> PBRMaterial:
    normalized = appearance(
        visual["baseColor"],
        metallic=visual.get("metallic", 0.0),
        roughness=visual.get("roughness", 0.58),
    )
    color = normalized["baseColor"]
    rgba = [int(color[index : index + 2], 16) for index in (1, 3, 5)] + [255]
    return PBRMaterial(
        name=f"{node_name}-material",
        baseColorFactor=rgba,
        metallicFactor=normalized["metallic"],
        roughnessFactor=normalized["roughness"],
    )


def export_display_glb(
    physical_items: Iterable[tuple[str, trimesh.Trimesh, dict[str, Any]]],
    path: str | Path,
    *,
    display_items: Iterable[tuple[str, trimesh.Trimesh, dict[str, Any]]] = (),
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write and read back one GLB composed from canonical meshes."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    physical = list(physical_items)
    visual = list(display_items)
    physical_names = [name for name, _, _ in physical]
    display_names = [name for name, _, _ in visual]
    names = [*physical_names, *display_names]
    if len(names) != len(set(names)):
        raise DisplayGlbError("display GLB node names must be unique")

    scene = trimesh.Scene()
    scene.metadata.update(metadata or {})
    expected_colors: dict[str, str] = {}
    expected_components: dict[str, dict[str, Any]] = {}
    display_name_set = set(display_names)
    for node_name, mesh, visual_style in [*physical, *visual]:
        if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
            raise DisplayGlbError(f"display GLB mesh for {node_name!r} is empty")
        if not np.isfinite(mesh.vertices).all():
            raise DisplayGlbError(
                f"display GLB mesh for {node_name!r} has non-finite vertices"
            )
        display = mesh_with_display_normals(mesh)
        normalized = appearance(
            visual_style["baseColor"],
            metallic=visual_style.get("metallic", 0.0),
            roughness=visual_style.get("roughness", 0.58),
        )
        display.visual = TextureVisuals(material=_material(node_name, normalized))
        display.metadata["name"] = node_name
        component = {
            "role": "display-only" if node_name in display_name_set else "manufactured"
        }
        if node_name in display_name_set:
            reference = mesh.metadata.get("amagine3d", {}).get("physicalFeatureRef")
            if reference is not None:
                if not isinstance(reference, str) or not reference.strip():
                    raise DisplayGlbError(
                        f"display GLB node {node_name!r} physicalFeatureRef is invalid"
                    )
                component["physicalFeatureRef"] = reference
        # Mesh extras survive GLTFLoader as Mesh.userData. Classification comes
        # from the export inputs, never a name, color, or stale source metadata.
        display.metadata["amagine3d"] = component
        expected_components[node_name] = component
        scene.add_geometry(
            display,
            node_name=node_name,
            geom_name=f"{node_name}-geometry",
        )
        expected_colors[node_name] = normalized["baseColor"]

    payload = scene.export(file_type="glb", include_normals=True)
    if not isinstance(payload, bytes) or payload[:4] != b"glTF":
        raise DisplayGlbError("display GLB exporter returned an invalid payload")
    destination.write_bytes(payload)
    loaded = trimesh.load(destination, force="scene", process=False)
    if not isinstance(loaded, trimesh.Scene):
        raise DisplayGlbError("display GLB readback did not produce a scene")
    observed_names = sorted(loaded.graph.nodes_geometry)
    if observed_names != sorted(names):
        raise DisplayGlbError(
            f"display GLB node readback mismatch: expected {sorted(names)}, "
            f"got {observed_names}"
        )
    readback_colors: dict[str, str] = {}
    for node_name, expected_color in expected_colors.items():
        _, geometry_name = loaded.graph[node_name]
        geometry = loaded.geometry[geometry_name]
        if geometry.metadata.get("amagine3d") != expected_components[node_name]:
            raise DisplayGlbError(
                f"display GLB node {node_name!r} component metadata readback mismatch"
            )
        normals = geometry._cache.cache.get("vertex_normals")
        if (
            normals is None
            or normals.shape != geometry.vertices.shape
            or not np.isfinite(normals).all()
            or not np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5)
        ):
            raise DisplayGlbError(
                f"display GLB node {node_name!r} has no valid unit normals after readback"
            )
        material = getattr(geometry.visual, "material", None)
        raw_color = getattr(material, "baseColorFactor", None)
        if raw_color is None or len(raw_color) < 3:
            raise DisplayGlbError(
                f"display GLB node {node_name!r} has no PBR base color after readback"
            )
        values = np.asarray(raw_color[:3], dtype=float)
        if float(np.max(values)) <= 1.0 + 1e-9:
            values *= 255.0
        observed_color = "#" + "".join(
            f"{int(round(value)):02X}" for value in values
        )
        readback_colors[node_name] = observed_color
        if observed_color != expected_color:
            raise DisplayGlbError(
                f"display GLB node {node_name!r} color readback mismatch: "
                f"expected {expected_color}, got {observed_color}"
            )
    return {
        "displayOnlyNodeNames": sorted(display_names),
        "nodeNames": observed_names,
        "physicalNodeNames": sorted(physical_names),
        "readbackBaseColors": readback_colors,
        "sha256": sha256(destination.read_bytes()).hexdigest(),
        "verified": True,
    }
