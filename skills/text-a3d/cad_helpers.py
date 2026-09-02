"""Fail-closed BRep backend for the unified evidence-driven CAD workflow.

Generated part scripts use this module to make failed booleans and silent
finish degradation observable. Exports carry hashes that tie geometry back to
the source and intent contract used in the current run.
"""

from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Callable, Iterable

import numpy as np
import trimesh

from build123d import (
    Compound,
    Pos,
    Rot,
    Unit,
    chamfer,
    export_step,
    export_stl,
    fillet,
)


def _load_local_module(module_name: str, filename: str):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_intent_contract = _load_local_module(
    "_text_a3d_intent_contract_for_cad_helpers",
    "intent_contract.py",
)
INTENT_SCHEMA = _intent_contract.INTENT_SCHEMA
validate_coordinate_system = _intent_contract.validate_coordinate_system
validate_color_regions = _intent_contract.validate_color_regions
validate_manufacturing = _intent_contract.validate_manufacturing

_coordinate_frames = _load_local_module(
    "_text_a3d_coordinate_frames_for_cad_helpers",
    "coordinate_frames.py",
)
rigid_transform = _coordinate_frames.rigid_transform

_build_manifest = _load_local_module(
    "_text_a3d_build_manifest_for_cad_helpers",
    "build_manifest.py",
)
artifact_record = _build_manifest.artifact_record
bind_inputs = _build_manifest.bind_inputs
new_run_id = _build_manifest.new_run_id
semantic_assembly_record = _build_manifest.semantic_assembly_record
utc_timestamp = _build_manifest.utc_timestamp
validate_manifest = _build_manifest.validate_manifest
write_json_atomic = _build_manifest.write_json_atomic

_export_audit = _load_local_module(
    "_text_a3d_export_audit_for_cad_helpers",
    "export_audit.py",
)
audit_exports = _export_audit.audit_exports
ExportAuditError = _export_audit.ExportAuditError
export_geometry_record = _export_audit.geometry_record

_material_plan = _load_local_module(
    "_text_a3d_material_plan_for_cad_helpers",
    "material_plan.py",
)
build_material_plan = _material_plan.build_material_plan
material_record = _material_plan.material_record
source_binding = _material_plan.source_binding
validate_material_sources = _material_plan.validate_material_sources

_plate_layout = _load_local_module(
    "_text_a3d_plate_layout_for_cad_helpers",
    "plate_layout.py",
)
PlateLayoutError = _plate_layout.PlateLayoutError
pack_plate_bboxes = _plate_layout.pack_bboxes


class BuildInvariantError(RuntimeError):
    """Raised when a requested modeling operation did not actually happen."""


_EVENTS: list[dict] = []
_FEATURES: dict[str, dict] = {}
_PARAMETERS: dict[str, dict] = {}
_DEFERRED_ISSUES: list[dict] = []
_SOURCE_DIAGNOSTICS_SCHEMA = "evidence-cad-source-diagnostics/v1"
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_MODEL_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_DISPLAY_TINTS = (
    (155, 167, 179),
    (112, 142, 166),
    (176, 151, 118),
    (124, 158, 130),
    (168, 132, 148),
)


def _collect_source_diagnostics() -> bool:
    return os.environ.get("AMAGINE3D_SOURCE_PHASE") == "compile"


def _defer_source_issue(issue: dict, message: str) -> bool:
    if not _collect_source_diagnostics():
        raise BuildInvariantError(message)
    _DEFERRED_ISSUES.append(
        {
            "severity": "error",
            **issue,
            "message": message,
        }
    )
    return True


def _raise_deferred_source_issues() -> None:
    if not _DEFERRED_ISSUES:
        return
    issues = list(_DEFERRED_ISSUES)
    _DEFERRED_ISSUES.clear()
    print(
        json.dumps(
            {
                "issues": issues,
                "pass": False,
                "schema": _SOURCE_DIAGNOSTICS_SCHEMA,
            },
            indent=2,
        )
    )
    raise BuildInvariantError(
        f"{len(issues)} checked source operations require repair"
    )


def _export_display_glb(
    items: Iterable[tuple[str, object, tuple[int, int, int]]],
    path: Path,
) -> None:
    import trimesh

    scene = trimesh.Scene()
    with tempfile.TemporaryDirectory() as directory:
        for index, (label, shape, color) in enumerate(items):
            mesh_path = Path(directory) / f"{index}-{label}.stl"
            export_stl(
                shape, str(mesh_path), tolerance=0.01, angular_tolerance=0.1
            )
            mesh = trimesh.load(mesh_path, force="mesh", process=False)
            if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
                raise BuildInvariantError(
                    f"display GLB mesh for {label!r} is empty"
                )
            mesh.visual.face_colors = [*color, 255]
            mesh.metadata["name"] = label
            scene.add_geometry(mesh, geom_name=label, node_name=label)
    data = scene.export(file_type="glb")
    path.write_bytes(data if isinstance(data, bytes) else bytes(data))


def _parameter_overrides() -> dict:
    raw = os.environ.get("AMAGINE3D_PARAMETER_OVERRIDES", "{}").strip() or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise BuildInvariantError("invalid parameter override payload") from error
    if not isinstance(value, dict):
        raise BuildInvariantError("parameter overrides must be an object")
    return value


def parameter(
    parameter_id: str,
    default: int | float,
    *,
    min_value: int | float,
    max_value: int | float,
    step: int | float,
    unit: str | None = None,
    label: str | None = None,
    label_zh: str | None = None,
    group: str | None = None,
    group_zh: str | None = None,
    affects: tuple[str, ...] | list[str] = (),
) -> int | float:
    """Declare one bounded user-adjustable driving value."""
    if not _ID_PATTERN.fullmatch(parameter_id) or parameter_id in _PARAMETERS:
        raise BuildInvariantError(f"invalid or duplicate parameter id: {parameter_id!r}")
    numbers = (default, min_value, max_value, step)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numbers):
        raise BuildInvariantError(f"parameter {parameter_id!r} must be numeric")
    if any(not math.isfinite(value) for value in numbers):
        raise BuildInvariantError(f"parameter {parameter_id!r} must be finite")
    if min_value > max_value or not min_value <= default <= max_value or step <= 0:
        raise BuildInvariantError(f"parameter {parameter_id!r} has invalid bounds")
    overrides = _parameter_overrides()
    value = overrides.get(parameter_id, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildInvariantError(f"parameter {parameter_id!r} override must be numeric")
    if isinstance(default, int) and not isinstance(value, int):
        raise BuildInvariantError(f"parameter {parameter_id!r} override must be an integer")
    if not math.isfinite(value) or not min_value <= value <= max_value:
        raise BuildInvariantError(f"parameter {parameter_id!r} override is out of bounds")
    quotient = (value - min_value) / step
    if not math.isclose(quotient, round(quotient), abs_tol=1e-8):
        raise BuildInvariantError(f"parameter {parameter_id!r} override does not align with step")
    feature_ids = list(affects)
    if any(not isinstance(feature_id, str) or not feature_id for feature_id in feature_ids):
        raise BuildInvariantError(f"parameter {parameter_id!r} has invalid feature IDs")
    descriptor = {
        "affects": feature_ids,
        "default": default,
        "group": group,
        "label": label or parameter_id,
        "maximum": max_value,
        "minimum": min_value,
        "step": step,
        "unit": unit,
        "value": value,
    }
    if isinstance(label_zh, str) and label_zh.strip():
        descriptor["label_zh"] = label_zh.strip()
    if isinstance(group_zh, str) and group_zh.strip():
        descriptor["group_zh"] = group_zh.strip()
    _PARAMETERS[parameter_id] = descriptor
    return value


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rgb_color(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def _part_color_plan(
    part_colors,
    intent_data: dict,
    part_names: set[str],
) -> tuple[dict[str, str] | None, list[dict] | None]:
    declared = intent_data.get("color_regions")
    if part_colors is None:
        if declared is not None:
            raise BuildInvariantError(
                "intent declares color_regions; pass matching part_colors to "
                "export_assembly"
            )
        return None, None
    if not isinstance(part_colors, dict):
        raise BuildInvariantError("part_colors must be a part-name to #RRGGBB object")
    if set(part_colors) != part_names:
        raise BuildInvariantError(
            "part_colors keys must exactly match exported assembly part names"
        )
    if not isinstance(declared, list):
        raise BuildInvariantError(
            "BRep assembly part_colors require matching intent color_regions"
        )
    normalized: dict[str, str] = {}
    for part_name, color in part_colors.items():
        if not isinstance(color, str) or not _HEX_COLOR.fullmatch(color):
            raise BuildInvariantError(
                f"part color for {part_name!r} must be #RRGGBB"
            )
        normalized[part_name] = color.upper()

    color_errors = validate_color_regions(
        declared,
        intent_data.get("manufacturing"),
        intent_data.get("part"),
    )
    if color_errors:
        raise BuildInvariantError(
            "invalid unified color intent: " + "; ".join(color_errors)
        )
    declared_by_name = {
        item["name"]: item
        for item in declared
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if set(declared_by_name) != part_names or any(
        declared_by_name[part_name].get("part") != part_name
        for part_name in part_names
        if part_name in declared_by_name
    ):
        raise BuildInvariantError(
            "BRep assembly part_colors require exactly one whole-part intent "
            "color region per exported part, with region name equal to its owner"
        )
    for part_name, color in normalized.items():
        if str(declared_by_name[part_name].get("hex", "")).upper() != color:
            raise BuildInvariantError(
                f"intent color for {part_name!r} does not match part_colors"
            )
    package_mode = intent_data.get("printability", {}).get("print_package_mode")
    if package_mode != "separate_parts":
        raise BuildInvariantError(
            "part-colored multipart assemblies require separate_parts 3MF output"
        )

    materials = []
    for part_name in normalized:
        declared_region = declared_by_name[part_name]
        raw_material = declared_region.get("material")
        material = raw_material if isinstance(raw_material, dict) else {}
        materials.append(
            material_record(
                part_name,
                normalized[part_name],
                filament=material.get("filament"),
                transmission=material.get("transmission"),
                color_status="declared",
                filament_status=(
                    "declared" if "filament" in material else "proposed"
                ),
                transmission_status=(
                    "declared" if "transmission" in material else "proposed"
                ),
            )
        )
    return normalized, materials


def _write_part_color_archive(entries, path: Path, name: str) -> dict:
    """Load the unified color writer lazily and through its package namespace."""
    try:
        from color import export_3mf as color_export_3mf
    except Exception as error:
        raise BuildInvariantError(
            "part-colored export requires the namespaced color.export_3mf runtime"
        ) from error
    expected = Path(__file__).resolve().parent / "color" / "export_3mf.py"
    loaded = Path(getattr(color_export_3mf, "__file__", "")).resolve()
    if loaded != expected.resolve():
        raise BuildInvariantError(
            f"color.export_3mf resolved outside the unified skill: {loaded}"
        )
    try:
        return color_export_3mf.write_color_archive(
            entries,
            str(path),
            package_mode="separate_parts",
            package_name=name,
        )
    except Exception as error:
        raise BuildInvariantError(f"could not write colored 3MF: {error}") from error


def _valid(shape) -> bool:
    value = shape.is_valid
    return bool(value() if callable(value) else value)


def _stats(shape) -> dict:
    box = shape.bounding_box()
    return {
        "bbox_mm": {
            "min": [round(box.min.X, 4), round(box.min.Y, 4), round(box.min.Z, 4)],
            "max": [round(box.max.X, 4), round(box.max.Y, 4), round(box.max.Z, 4)],
            "size": [
                round(box.max.X - box.min.X, 4),
                round(box.max.Y - box.min.Y, 4),
                round(box.max.Z - box.min.Z, 4),
            ],
        },
        "solid_count": len(shape.solids()),
        "valid": _valid(shape),
        "volume_mm3": round(float(shape.volume), 4),
    }


def _preflight_assembly_parts(parts: dict) -> dict:
    """Validate assembly part identifiers and collect one-solid statistics."""
    normalized = {}
    invalid_parts = []
    for part_name, shape in parts.items():
        if not isinstance(part_name, str) or not _ID_PATTERN.fullmatch(part_name):
            raise BuildInvariantError(f"invalid assembly part name: {part_name!r}")
        stats = _stats(shape)
        normalized[part_name] = (shape, stats)
        if not stats["valid"] or stats["solid_count"] != 1:
            invalid_parts.append(
                f"assembly part {part_name!r} must be one valid solid, "
                f"got {stats['solid_count']}"
            )
    if invalid_parts:
        # A single failure preserves the previous error text exactly. Multiple
        # failures retain that text per part while reporting them in one pass.
        raise BuildInvariantError("; ".join(invalid_parts))
    return normalized


def _intersection_volume(left, right) -> float:
    """Measure a boolean intersection, treating an explicit empty result as zero."""
    intersection = left & right
    if intersection is None:
        # build123d Shape booleans return None for a valid, empty intersection.
        return 0.0
    return float(intersection.volume)


def _manifest_geometry_record(stats: dict) -> dict:
    """Translate kernel-specific measurements into the shared build schema."""
    return {
        "bodyCount": stats["solid_count"],
        "boundsMm": stats["bbox_mm"],
        "isVolume": bool(stats["valid"] and stats["solid_count"] > 0),
        "valid": stats["valid"],
        "volumeMm3": stats["volume_mm3"],
    }


def _translate(shape, x: float, y: float, z: float):
    return Pos(x, y, z) * shape


def _rotate(shape, rx: float, ry: float, rz: float):
    return Rot(rx, ry, rz) * shape


def _print_part(shape):
    box = shape.bounding_box()
    transform = [-box.min.X, -box.min.Y, -box.min.Z]
    return _translate(shape, *transform), rigid_transform(
        "assembly-semantic",
        "part-print",
        translate_mm=[round(float(value), 5) for value in transform],
    )


def _rectangle_bounds(polygon) -> tuple[float, float, float, float] | None:
    try:
        points = [(float(x), float(y)) for x, y in polygon]
    except Exception:
        return None
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _footprint_fits(
    width: float,
    depth: float,
    profile: dict,
) -> tuple[bool, dict]:
    tool = profile.get("machine", {}).get("selected_tool", {})
    bounds = _rectangle_bounds(tool.get("polygon_mm", []))
    height_limit = tool.get("height_mm")
    if (
        bounds is None
        or not isinstance(height_limit, (int, float))
        or isinstance(height_limit, bool)
    ):
        return True, {"reason": "profile bed limits unavailable during export"}
    bed_width = bounds[2] - bounds[0]
    bed_depth = bounds[3] - bounds[1]
    fits = width <= bed_width + 1e-9 and depth <= bed_depth + 1e-9
    return bool(fits), {
        "bed_depth_mm": round(bed_depth, 5),
        "bed_width_mm": round(bed_width, 5),
        "footprint_mm": [round(width, 5), round(depth, 5)],
    }


def _uniform_scale_to_fit_profile(dimensions: list[float], profile: dict) -> dict:
    tool = profile.get("machine", {}).get("selected_tool", {})
    bounds = _rectangle_bounds(tool.get("polygon_mm", []))
    height_limit = tool.get("height_mm")
    if (
        bounds is None
        or not isinstance(height_limit, (int, float))
        or isinstance(height_limit, bool)
    ):
        return {"available": False, "reason": "profile bed limits unavailable during export"}
    bed_width = bounds[2] - bounds[0]
    bed_depth = bounds[3] - bounds[1]
    limits = [bed_width, bed_depth, float(height_limit)]
    if any(value <= 0 for value in dimensions):
        return {"available": False, "reason": "candidate dimensions are invalid"}
    scale = min(limit / dimension for limit, dimension in zip(limits, dimensions))
    scale = min(1.0, float(scale))
    return {
        "available": True,
        "dimensions_after_scale_mm": [
            round(value * scale, 5) for value in dimensions
        ],
        "fits_without_scaling": scale >= 1.0 - 1e-9,
        "scale": round(scale, 8),
    }


def _protected_faces(intent_data: dict | None) -> set[str]:
    protected = set()
    if not isinstance(intent_data, dict):
        return protected
    visual = intent_data.get("visual", {})
    if isinstance(visual, dict):
        view = visual.get("reference_view")
        if view in {"front", "back", "left", "right", "top", "bottom"}:
            protected.add(view)
    for feature in intent_data.get("features", []):
        if not isinstance(feature, dict):
            continue
        face = feature.get("face")
        kind = feature.get("kind")
        if face in {"front", "back", "left", "right", "top", "bottom"} and kind in {
            "detail",
            "logo",
            "region",
            "surface",
            "window",
        }:
            protected.add(face)
    return protected


def _bed_face_for_rotation(name: str) -> str | None:
    return {
        "identity": "bottom",
        "rotate-x-90": "front",
        "rotate-x--90": "back",
        "rotate-x-180": "top",
        "rotate-y-90": "right",
        "rotate-y--90": "left",
    }.get(name)


def _mesh_orientation_metrics(shape, *, threshold_deg: float) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        mesh_path = Path(directory) / "orientation.stl"
        export_stl(shape, str(mesh_path), tolerance=0.05, angular_tolerance=0.2)
        mesh = trimesh.load(mesh_path, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        return {
            "center_inside_contact_bounds": False,
            "contact_area_mm2": 0.0,
            "contact_area_ratio": 0.0,
            "contact_bounds_mm": None,
            "overhang_area_mm2": float("inf"),
            "stability_offset_ratio": float("inf"),
        }
    normals = np.asarray(mesh.face_normals, dtype=float)
    triangles = np.asarray(mesh.triangles, dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    bounds = np.asarray(mesh.bounds, dtype=float)
    minimum_z = float(bounds[0, 2])
    contact_mask = (
        (triangles[:, :, 2].max(axis=1) <= minimum_z + 0.08)
        & (normals[:, 2] < -0.5)
    )
    contact_area = float(areas[contact_mask].sum())
    footprint_area = max(
        float((bounds[1, 0] - bounds[0, 0]) * (bounds[1, 1] - bounds[0, 1])),
        1e-9,
    )
    contact_bounds = None
    center_inside = False
    stability_offset = float("inf")
    if contact_mask.any():
        contact_points = triangles[contact_mask][:, :, :2].reshape((-1, 2))
        lower = contact_points.min(axis=0)
        upper = contact_points.max(axis=0)
        contact_bounds = np.asarray([lower, upper], dtype=float)
        try:
            center = np.asarray(mesh.center_mass[:2], dtype=float)
            if not np.isfinite(center).all():
                raise ValueError
        except Exception:
            center = bounds[:, :2].mean(axis=0)
        center_inside = bool(
            lower[0] - 1e-9 <= center[0] <= upper[0] + 1e-9
            and lower[1] - 1e-9 <= center[1] <= upper[1] + 1e-9
        )
        contact_center = (lower + upper) / 2
        half_diagonal = max(float(np.linalg.norm((upper - lower) / 2)), 1e-9)
        stability_offset = float(np.linalg.norm(center - contact_center) / half_diagonal)
    slopes = np.degrees(np.arccos(np.clip(np.abs(normals[:, 2]), 0.0, 1.0)))
    above_build_plane = triangles[:, :, 2].max(axis=1) > minimum_z + 0.08
    risky = (normals[:, 2] < -1e-8) & above_build_plane & (slopes < threshold_deg)
    overhang_area = float(areas[risky].sum())
    return {
        "center_inside_contact_bounds": center_inside,
        "contact_area_mm2": round(contact_area, 5),
        "contact_area_ratio": round(contact_area / footprint_area, 8),
        "contact_bounds_mm": (
            contact_bounds.round(5).tolist()
            if contact_bounds is not None
            else None
        ),
        "overhang_area_mm2": round(overhang_area, 5),
        "stability_offset_ratio": round(stability_offset, 8),
    }


def _orientation_candidates(
    shape,
    profile: dict | None,
    *,
    intent_data: dict | None = None,
) -> list[dict]:
    raw_candidates = (
        ("identity", (0.0, 0.0, 0.0)),
        ("rotate-x-90", (90.0, 0.0, 0.0)),
        ("rotate-x--90", (-90.0, 0.0, 0.0)),
        ("rotate-x-180", (180.0, 0.0, 0.0)),
        ("rotate-y-90", (0.0, 90.0, 0.0)),
        ("rotate-y--90", (0.0, -90.0, 0.0)),
    )
    results = []
    height_limit = (
        profile.get("machine", {}).get("selected_tool", {}).get("height_mm")
        if isinstance(profile, dict)
        else None
    )
    threshold_deg = (
        profile.get("process", {}).get("support_threshold_angle_from_horizontal_deg")
        if isinstance(profile, dict)
        else None
    )
    if not isinstance(threshold_deg, (int, float)) or isinstance(threshold_deg, bool):
        threshold_deg = 30.0
    protected = _protected_faces(intent_data)
    for preference, (name, rotation) in enumerate(raw_candidates):
        rotated = _rotate(shape, *rotation)
        box = rotated.bounding_box()
        dimensions = [
            float(box.max.X - box.min.X),
            float(box.max.Y - box.min.Y),
            float(box.max.Z - box.min.Z),
        ]
        bed_fits, bed = _footprint_fits(dimensions[0], dimensions[1], profile)
        height_fits = (
            True
            if not isinstance(height_limit, (int, float)) or isinstance(height_limit, bool)
            else dimensions[2] <= float(height_limit) + 1e-9
        )
        fits = bed_fits and height_fits
        translate = [
            round(float(-box.min.X), 5),
            round(float(-box.min.Y), 5),
            round(float(-box.min.Z), 5),
        ]
        placed = _translate(rotated, *translate)
        metrics = _mesh_orientation_metrics(
            placed,
            threshold_deg=float(threshold_deg),
        )
        bed_face = _bed_face_for_rotation(name)
        protected_penalty = 1 if bed_face in protected else 0
        no_contact_penalty = 1 if metrics["contact_area_mm2"] <= 1e-9 else 0
        center_penalty = 0 if metrics["center_inside_contact_bounds"] else 1
        results.append({
            "bed_contact_semantic_face": bed_face,
            "bed_fit": bed,
            "dimensions_mm": [round(value, 5) for value in dimensions],
            "fits_profile": bool(fits),
            "height_fits": bool(height_fits),
            "orientation_metrics": metrics,
            "name": name,
            "preference": preference,
            "protected_contact_face_penalty": protected_penalty,
            "rotate_degrees_xyz": [round(value, 5) for value in rotation],
            "score": [
                0 if fits else 1,
                round(float(metrics["overhang_area_mm2"]), 5),
                no_contact_penalty,
                center_penalty,
                round(float(metrics["stability_offset_ratio"]), 8),
                round(-float(metrics["contact_area_mm2"]), 5),
                protected_penalty,
                round(dimensions[2], 5),
                preference,
            ],
            "translate_mm": translate,
            "uniform_scale_to_fit_profile": _uniform_scale_to_fit_profile(
                dimensions,
                profile,
            ),
        })
    return results


def _select_print_orientation(
    shape,
    profile: dict,
    *,
    intent_data: dict | None = None,
) -> dict:
    candidates = _orientation_candidates(shape, profile, intent_data=intent_data)
    selected = min(candidates, key=lambda item: item["score"])
    return {
        "candidates": candidates,
        "selected": {
            key: value
            for key, value in selected.items()
            if key != "preference"
        },
        "strategy": "lightweight-stability-support-appearance-score",
    }


def _apply_print_orientation(shape, orientation: dict):
    selected = orientation["selected"]
    rotated = _rotate(shape, *selected["rotate_degrees_xyz"])
    return _translate(rotated, *selected["translate_mm"])


def _orientation_transform(orientation: dict) -> dict:
    selected = orientation["selected"]
    return rigid_transform(
        "semantic",
        "part-print",
        rotate_degrees_xyz=selected["rotate_degrees_xyz"],
        translate_mm=selected["translate_mm"],
    )


def _print_plate(
    parts: dict[str, object],
    spacing_mm: float = 5.0,
    *,
    profile: dict,
):
    """Arrange parts using rigid translations and a profile-bound shelf pack."""
    bboxes = {}
    for part_name, shape in parts.items():
        box = shape.bounding_box()
        bboxes[part_name] = {
            "min": [float(box.min.X), float(box.min.Y), float(box.min.Z)],
            "max": [float(box.max.X), float(box.max.Y), float(box.max.Z)],
        }
    try:
        layout = pack_plate_bboxes(
            bboxes,
            profile,
            spacing_mm=spacing_mm,
        )
    except PlateLayoutError as error:
        raise BuildInvariantError(str(error)) from error

    placed = {}
    transforms = {}
    for part_name, shape in parts.items():
        translate = layout["transforms"][part_name]
        placed[part_name] = _translate(shape, *translate)
        transforms[part_name] = rigid_transform(
            "assembly-semantic",
            "plate-print",
            translate_mm=translate,
        )
    return Compound(children=list(placed.values())), placed, transforms, layout


def observe(
    shape,
    feature_id: str,
    role: str = "feature",
    *,
    part_name: str | None = None,
) -> None:
    """Capture evidence before a feature disappears into a boolean result."""
    if feature_id in _FEATURES:
        raise BuildInvariantError(f"duplicate feature id: {feature_id}")
    _FEATURES[feature_id] = {
        "role": role,
        **({"part": part_name} if part_name is not None else {}),
        **_stats(shape),
    }


def checked_cut(
    body,
    tool,
    feature_id: str,
    min_removed_mm3: float = 0.001,
    *,
    part_name: str | None = None,
):
    """Subtract a tool and fail if it misses or produces an invalid result."""
    before = float(body.volume)
    tool_stats = _stats(tool)
    try:
        result = body - tool
    except Exception as error:
        message = f"cut {feature_id!r} failed: {error}"
        if _collect_source_diagnostics():
            _defer_source_issue(
                {
                    "blockedBy": "BOOLEAN_OPERATION_FAILED",
                    "check": "checked-cut",
                    "code": "SOURCE.CHECKED_CUT_FAILED",
                    "expected": {"minimumRemovedMm3": float(min_removed_mm3)},
                    "featureId": feature_id,
                    "observed": {
                        "body": _stats(body),
                        "error": str(error),
                        "tool": tool_stats,
                    },
                    **({"partId": part_name} if part_name is not None else {}),
                    "status": "blocked",
                },
                message,
            )
            return body
        raise BuildInvariantError(message) from error
    removed = before - float(result.volume)
    _EVENTS.append({
        "id": feature_id,
        "kind": "cut",
        "removed_mm3": round(removed, 6),
        "tool": tool_stats,
        **({"part": part_name} if part_name is not None else {}),
    })
    if removed < min_removed_mm3:
        message = (
            f"cut {feature_id!r} removed {removed:.6f} mm^3; tool likely missed"
        )
        if _defer_source_issue(
            {
                "check": "checked-cut",
                "code": "SOURCE.CUT_MISSED_OWNER",
                "expected": {"minimumRemovedMm3": float(min_removed_mm3)},
                "featureId": feature_id,
                "observed": {
                    "removedMm3": round(removed, 6),
                    "tool": tool_stats,
                },
                **({"partId": part_name} if part_name is not None else {}),
            },
            message,
        ):
            return result
    if not _valid(result):
        message = f"cut {feature_id!r} produced an invalid solid"
        if _defer_source_issue(
            {
                "check": "checked-cut",
                "code": "SOURCE.CUT_INVALID_RESULT",
                "expected": {"validSolid": True},
                "featureId": feature_id,
                "observed": _stats(result),
                **({"partId": part_name} if part_name is not None else {}),
            },
            message,
        ):
            return body
    return result


def _finish(
    shape,
    selector: Iterable | Callable,
    requested: float,
    feature_id: str,
    kind: str,
    allow_reduce: bool,
    part_name: str | None,
):
    edges = list(selector(shape) if callable(selector) else selector)
    if not edges:
        message = f"{kind} {feature_id!r} selected no edges"
        if _defer_source_issue(
            {
                "check": f"checked-{kind}",
                "code": f"SOURCE.CHECKED_{kind.upper()}_FAILED",
                "expected": {"selectedEdgeCount": ">=1"},
                "featureId": feature_id,
                "observed": {"selectedEdgeCount": 0},
                **({"partId": part_name} if part_name is not None else {}),
            },
            message,
        ):
            return shape
    factors = (1.0, 0.75, 0.5, 0.25) if allow_reduce else (1.0,)
    errors: list[str] = []
    for factor in factors:
        actual = requested * factor
        try:
            result = (
                fillet(edges, radius=actual)
                if kind == "fillet"
                else chamfer(edges, length=actual)
            )
            if not _valid(result):
                raise ValueError("operation returned invalid geometry")
            _EVENTS.append({
                "actual_mm": round(actual, 6),
                "degraded": actual != requested,
                "id": feature_id,
                "kind": kind,
                "requested_mm": requested,
                **({"part": part_name} if part_name is not None else {}),
            })
            return result
        except Exception as error:
            errors.append(f"{actual:g}: {error}")
    message = (
        f"{kind} {feature_id!r} failed at requested sizes "
        f"({'; '.join(errors)})"
    )
    if _defer_source_issue(
        {
            "check": f"checked-{kind}",
            "code": f"SOURCE.CHECKED_{kind.upper()}_FAILED",
            "expected": {"requestedMm": float(requested)},
            "featureId": feature_id,
            "observed": {"attempts": errors},
            **({"partId": part_name} if part_name is not None else {}),
        },
        message,
    ):
        return shape
    raise AssertionError("unreachable")


def checked_fillet(
    shape,
    selector: Iterable | Callable,
    radius_mm: float,
    feature_id: str,
    *,
    allow_reduce: bool = False,
    part_name: str | None = None,
):
    return _finish(
        shape,
        selector,
        radius_mm,
        feature_id,
        "fillet",
        allow_reduce,
        part_name,
    )


def checked_chamfer(
    shape,
    selector: Iterable | Callable,
    length_mm: float,
    feature_id: str,
    *,
    allow_reduce: bool = False,
    part_name: str | None = None,
):
    return _finish(
        shape,
        selector,
        length_mm,
        feature_id,
        "chamfer",
        allow_reduce,
        part_name,
    )


def _validate_assembly_intent(
    intent_path: Path,
    name: str,
    part_names: set[str],
) -> dict:
    try:
        intent_data = json.loads(intent_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise BuildInvariantError(f"could not read intent contract: {error}") from error
    if not isinstance(intent_data, dict):
        raise BuildInvariantError("intent contract must contain a JSON object")
    if intent_data.get("schema") != INTENT_SCHEMA:
        raise BuildInvariantError(f"intent contract must use {INTENT_SCHEMA}")
    if intent_data.get("part") != name:
        raise BuildInvariantError("intent part does not match the export name")
    manufacturing = intent_data.get("manufacturing")
    if not isinstance(manufacturing, dict) or manufacturing.get("mode") != "multipart":
        raise BuildInvariantError(
            "export_assembly requires manufacturing.mode='multipart' in the intent"
        )
    manufacturing_errors = validate_manufacturing(manufacturing)
    if manufacturing_errors:
        raise BuildInvariantError(
            "invalid manufacturing contract: " + "; ".join(manufacturing_errors)
        )
    declared_names = {item["name"] for item in manufacturing["parts"]}
    if declared_names != part_names:
        raise BuildInvariantError(
            "intent manufacturing part names do not match exported part names"
        )
    return manufacturing


def _validate_assembly_evidence(part_names: set[str]) -> None:
    observed_parts: set[str] = set()
    for feature_id, record in _FEATURES.items():
        owner = record.get("part")
        if owner not in part_names:
            raise BuildInvariantError(
                f"assembly feature {feature_id!r} must name one exported part"
            )
        observed_parts.add(owner)
    missing = sorted(part_names - observed_parts)
    if missing:
        raise BuildInvariantError(
            f"every assembly part must have observed evidence; missing {missing}"
        )
    for event in _EVENTS:
        owner = event.get("part")
        if owner not in part_names:
            raise BuildInvariantError(
                f"assembly event {event.get('id')!r} must name one exported part"
            )


def _validate_interface_evidence(manufacturing: dict) -> None:
    required = {
        feature_id
        for interface in manufacturing.get("interfaces", [])
        if isinstance(interface, dict)
        for feature_id in interface.get("features", [])
        if isinstance(feature_id, str)
    }
    observed = set(_FEATURES) | {
        event.get("id") for event in _EVENTS if isinstance(event.get("id"), str)
    }
    missing = sorted(required - observed)
    if missing:
        raise BuildInvariantError(
            f"multipart interfaces reference unmodeled features: {missing}"
        )


def export_part(
    shape,
    name: str,
    out_dir: str = ".",
    *,
    intent_path: str,
    scene_path: str,
    source_path: str,
) -> dict:
    """Export printable STL, display GLB, assembly STEP, and build evidence."""
    stats = _stats(shape)
    if not stats["valid"] or stats["solid_count"] != 1:
        raise BuildInvariantError(
            f"final shape must be one valid solid, got {stats['solid_count']}"
        )

    output = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", out_dir))
    output.mkdir(parents=True, exist_ok=True)
    try:
        intent_data, scene_data, profile, inputs = bind_inputs(
            intent_path=intent_path,
            scene_path=scene_path,
            expected_parts={name},
            source_path=source_path,
        )
    except ValueError as error:
        raise BuildInvariantError(str(error)) from error
    if intent_data.get("part") != name:
        raise BuildInvariantError("intent part does not match the export name")
    if intent_data.get("manufacturing", {}).get("mode") != "single-part":
        raise BuildInvariantError(
            "export_part requires manufacturing.mode='single-part' in the intent"
        )
    intent_path_resolved = Path(intent_path).resolve()
    print_orientation = _select_print_orientation(
        shape,
        profile,
        intent_data=intent_data,
    )
    print_shape = _apply_print_orientation(shape, print_orientation)
    print_stats = _stats(print_shape)
    assemble_step_path = output / f"{name}.step"
    display_glb_path = output / f"{name}-display.glb"
    stl_path = output / f"{name}.stl"
    report_path = output / f"{name}_report.json"
    try:
        shape.label = name
    except Exception:
        pass
    export_step(shape, str(assemble_step_path), unit=Unit.MM)
    _export_display_glb(((name, shape, _DISPLAY_TINTS[0]),), display_glb_path)
    export_stl(print_shape, str(stl_path), tolerance=0.01, angular_tolerance=0.1)

    try:
        export_audit = audit_exports(
            stls={
                f"stl:{name}": (stl_path, export_geometry_record(print_shape)),
            },
            steps={
                f"step:{name}": (assemble_step_path, export_geometry_record(shape)),
            },
            glb=(display_glb_path, [name]),
        )
    except ExportAuditError as error:
        raise BuildInvariantError(f"export read-back audit failed: {error}") from error
    export_audit_path = output / f"{name}_export-audit.json"
    export_audit_path.write_text(
        json.dumps(export_audit, indent=2) + "\n", encoding="utf-8"
    )

    matrix = _orientation_transform(print_orientation)["matrix"]
    part_records = {
        name: {
            "print": _manifest_geometry_record(print_stats),
            "representationMaster": "brep",
            "semantic": _manifest_geometry_record(stats),
        }
    }
    try:
        semantic_assembly = semantic_assembly_record(
            part_records, inputs["intent"]["sha256"], intent_data
        )
    except ValueError as error:
        raise BuildInvariantError(str(error)) from error
    report = {
        "artifactMatrix": {
            "parts": {
                name: {"glb": "required", "step": "required", "stl": "required", "threeMf": "not-applicable"}
            }
        },
        "artifacts": {
            f"stl:{name}": artifact_record(stl_path, coordinateFrame="part-print"),
            f"step:{name}": artifact_record(
                assemble_step_path, coordinateFrame="semantic"
            ),
            "glb:display": artifact_record(
                display_glb_path, coordinateFrame="semantic"
            ),
            "exportAudit": artifact_record(export_audit_path),
        },
        "autoScale": False,
        "backend": "brep-part",
        "backendData": {
            "exportAudit": export_audit,
            "parameters": dict(_PARAMETERS),
            "printOrientation": print_orientation,
            "semanticAssembly": semantic_assembly,
        },
        "builtAt": utc_timestamp(),
        "coordinateFrames": {
            "semantic": {
                "scale": 1.0,
                "units": "mm",
                "up": scene_data["coordinateSystem"]["up"],
            },
            "part-print": {
                "partTransforms": {name: matrix},
                "scale": 1.0,
                "units": "mm",
            },
            "plate-print": {
                "partTransforms": {name: matrix},
                "scale": 1.0,
                "units": "mm",
            },
        },
        "events": [
            {**event, "part": event.get("part", name)} for event in _EVENTS
        ],
        "features": {
            feature_id: {**record, "part": record.get("part", name)}
            for feature_id, record in _FEATURES.items()
        },
        "inputs": inputs,
        "part": name,
        "parts": part_records,
        "pass": bool(
            stats["valid"] and print_stats["valid"] and export_audit["pass"]
        ),
        "revision": scene_data["revision"],
        "runId": new_run_id(),
        "scale": 1.0,
        "schema": "evidence-a3d-build/v1",
        "warnings": [],
    }
    manifest_errors = validate_manifest(report)
    if manifest_errors:
        raise BuildInvariantError(
            "exporter produced an invalid build manifest: "
            + "; ".join(manifest_errors)
        )
    write_json_atomic(report_path, report)
    _raise_deferred_source_issues()
    print(json.dumps(report, indent=2))
    return report


def export_assembly(
    parts: dict,
    name: str,
    out_dir: str = ".",
    *,
    intent_path: str,
    scene_path: str,
    source_path: str,
    max_overlap_mm3: float = 0.01,
    part_colors: dict[str, str] | None = None,
) -> dict:
    """Export a BRep-master multi-part assembly, optionally colored by part.

    Each named part must be one valid solid. The top-level STL is an arranged
    print plate, while the STEP master and display GLB keep semantic assembly
    coordinates. When ``part_colors`` is supplied, the same plate-aligned
    physical parts become a separate-parts 3MF; no export path applies scale.
    """
    if not isinstance(parts, dict) or len(parts) < 2:
        raise BuildInvariantError("export_assembly requires at least two parts")
    if not isinstance(name, str) or not _MODEL_NAME.fullmatch(name):
        raise BuildInvariantError(f"invalid assembly name: {name!r}")
    if (
        isinstance(max_overlap_mm3, bool)
        or not isinstance(max_overlap_mm3, (int, float))
        or not math.isfinite(max_overlap_mm3)
        or max_overlap_mm3 < 0
    ):
        raise BuildInvariantError("max_overlap_mm3 must be finite and non-negative")

    normalized = _preflight_assembly_parts(parts)

    try:
        intent_data, scene_data, plate_profile, inputs = bind_inputs(
            intent_path=intent_path,
            scene_path=scene_path,
            expected_parts=set(normalized),
            source_path=source_path,
        )
    except ValueError as error:
        raise BuildInvariantError(str(error)) from error
    intent_path_resolved = Path(intent_path).resolve()
    manufacturing = _validate_assembly_intent(
        intent_path_resolved, name, set(normalized)
    )
    normalized_colors, material_regions = _part_color_plan(
        part_colors,
        intent_data,
        set(normalized),
    )
    _validate_assembly_evidence(set(normalized))
    _validate_interface_evidence(manufacturing)
    output = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", out_dir))
    output.mkdir(parents=True, exist_ok=True)

    overlaps = {}
    names = list(normalized)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            pair_id = "&".join(sorted((left, right)))
            try:
                overlap = _intersection_volume(
                    normalized[left][0], normalized[right][0]
                )
            except Exception as error:
                message = (
                    f"could not compare overlap for {left!r} and {right!r}: {error}"
                )
                if _collect_source_diagnostics():
                    _defer_source_issue(
                        {
                            "blockedBy": "OVERLAP_BOOLEAN_FAILED",
                            "check": "assembly-overlap",
                            "code": "SOURCE.OVERLAP_NOT_EVALUATED",
                            "expected": {"maximumMm3": float(max_overlap_mm3)},
                            "offenderId": pair_id,
                            "observed": {"error": str(error)},
                            "status": "blocked",
                        },
                        message,
                    )
                    continue
                raise BuildInvariantError(message) from error
            overlaps[pair_id] = round(overlap, 6)
            if overlap > max_overlap_mm3:
                message = (
                    f"assembly parts {left!r} and {right!r} overlap by "
                    f"{overlap:.6f} mm^3"
                )
                if _defer_source_issue(
                    {
                        "check": "assembly-overlap",
                        "code": "SOURCE.PART_OVERLAP",
                        "expected": {"maximumMm3": float(max_overlap_mm3)},
                        "offenderId": pair_id,
                        "observed": {"overlapMm3": round(overlap, 6)},
                    },
                    message,
                ):
                    continue

    # Prove the profile-bound plate layout before writing export artifacts.
    print_plate, plate_parts, plate_transforms, plate_layout = _print_plate(
        {part_name: shape for part_name, (shape, _) in normalized.items()},
        profile=plate_profile,
    )
    print_plate_stats = _stats(print_plate)
    if not print_plate_stats["valid"]:
        raise BuildInvariantError("print plate geometry is invalid")

    artifacts = {}
    audit_stls = {}
    audit_steps = {}
    children = []
    print_parts = {}
    for part_name, (shape, stats) in normalized.items():
        print_shape, print_transform = _print_part(shape)
        path = output / f"{name}-{part_name}.stl"
        export_stl(print_shape, str(path), tolerance=0.01, angular_tolerance=0.1)
        artifacts[f"stl:{part_name}"] = {
            "path": str(path.resolve()),
            "sha256": _digest(path),
        }
        audit_stls[f"stl:{part_name}"] = (
            path,
            export_geometry_record(print_shape),
        )
        print_parts[part_name] = {
            **_stats(print_shape),
            "transform": print_transform,
        }
        step_path = output / f"{name}-{part_name}.step"
        export_step(shape, str(step_path), unit=Unit.MM)
        artifacts[f"step:{part_name}"] = {
            "path": str(step_path.resolve()),
            "sha256": _digest(step_path),
        }
        audit_steps[f"step:{part_name}"] = (
            step_path,
            export_geometry_record(shape),
        )
        try:
            shape.label = part_name
        except Exception:
            pass
        children.append(shape)

    assembly_shape = Compound(children=children)
    assembly_stats = _stats(assembly_shape)
    if not assembly_stats["valid"]:
        raise BuildInvariantError("assembly geometry is invalid")
    stl_path = output / f"{name}.stl"
    export_stl(print_plate, str(stl_path), tolerance=0.01, angular_tolerance=0.1)
    artifacts["stl"] = {
        "path": str(stl_path.resolve()),
        "sha256": _digest(stl_path),
    }
    audit_stls["stl"] = (stl_path, export_geometry_record(print_plate))

    assemble_step_path = output / f"{name}-assemble.step"
    display_glb_path = output / f"{name}-display.glb"
    export_step(assembly_shape, str(assemble_step_path), unit=Unit.MM)
    _export_display_glb(
        (
            (
                part_name,
                shape,
                (
                    _rgb_color(normalized_colors[part_name])
                    if normalized_colors is not None
                    else _DISPLAY_TINTS[index % len(_DISPLAY_TINTS)]
                ),
            )
            for index, (part_name, (shape, _)) in enumerate(normalized.items())
        ),
        display_glb_path,
    )
    artifacts["step:assembly"] = {
        "path": str(assemble_step_path.resolve()),
        "sha256": _digest(assemble_step_path),
    }
    audit_steps["step:assembly"] = (
        assemble_step_path,
        export_geometry_record(assembly_shape),
    )
    artifacts["glb:display"] = {
        "path": str(display_glb_path.resolve()),
        "sha256": _digest(display_glb_path),
    }

    color_fields = {}
    material_plan = None
    if normalized_colors is not None and material_regions is not None:
        internal_plate_dir = output / ".amagine3d-internal" / name / "plate"
        internal_plate_dir.mkdir(parents=True, exist_ok=True)
        internal_plate_meshes = {}
        entries = []
        for part_name, shape in plate_parts.items():
            path = internal_plate_dir / f"{name}-{part_name}.stl"
            export_stl(shape, str(path), tolerance=0.01, angular_tolerance=0.1)
            internal_plate_meshes[part_name] = {
                "coordinate_frame": "plate-print",
                "path": str(path.resolve()),
                "scale": 1.0,
                "sha256": _digest(path),
            }
            artifacts[f"plate-stl:{part_name}"] = {
                "coordinateFrame": "plate-print",
                "path": str(path.resolve()),
                "sha256": _digest(path),
            }
            audit_stls[f"plate-stl:{part_name}"] = (
                path,
                export_geometry_record(shape),
            )
            entries.append((str(path), normalized_colors[part_name], part_name))

        archive_path = output / f"{name}.3mf"
        three_mf = _write_part_color_archive(entries, archive_path, name)
        artifacts["3mf"] = {
            "coordinateFrame": "plate-print",
            "path": str(archive_path.resolve()),
            "scale": 1.0,
            "sha256": _digest(archive_path),
            "validator": "lib3mf",
            "verified": True,
        }

        assignments = [
            {
                "materialId": material["id"],
                "part": material["id"],
                "region": None,
                "scope": "whole-part",
            }
            for material in material_regions
        ]
        material_plan = build_material_plan(
            part=name,
            package_mode="separate_parts",
            materials=material_regions,
            assignments=assignments,
            source_bindings=[
                source_binding(
                    material=material,
                    part=material["id"],
                    region=None,
                    scope="whole-part",
                    source_id=material["id"],
                    source_kind="intent-color-region",
                )
                for material in material_regions
            ],
        )
        source_errors = validate_material_sources(
            material_plan, intent_data, scene_data
        )
        if source_errors:
            raise BuildInvariantError(
                "invalid material provenance: " + "; ".join(source_errors)
            )
        material_plan_path = output / f"{name}_material-plan.json"
        material_plan_path.write_text(
            json.dumps(material_plan, indent=2) + "\n", encoding="utf-8"
        )
        artifacts["materialPlan"] = {
            "path": str(material_plan_path.resolve()),
            "sha256": _digest(material_plan_path),
        }
        color_fields = {
            "internalPartMeshes": {"plate-print": internal_plate_meshes},
            "partColors": normalized_colors,
            "printPackageMode": "separate_parts",
            "threeMf": three_mf,
        }

    try:
        export_audit = audit_exports(
            stls=audit_stls,
            steps=audit_steps,
            glb=(display_glb_path, list(normalized)),
        )
    except ExportAuditError as error:
        raise BuildInvariantError(f"export read-back audit failed: {error}") from error
    export_audit_path = output / f"{name}_export-audit.json"
    export_audit_path.write_text(
        json.dumps(export_audit, indent=2) + "\n", encoding="utf-8"
    )
    artifacts["exportAudit"] = {
        "path": str(export_audit_path.resolve()),
        "sha256": _digest(export_audit_path),
    }

    manifest_artifacts = {}
    for key, record in artifacts.items():
        if key == "step:assembly" or key.startswith("step:") or key == "glb:display":
            frame = "semantic"
        elif key.startswith("stl:"):
            frame = "part-print"
        elif key in {"stl", "3mf"}:
            frame = "plate-print"
        else:
            frame = None
        manifest_artifacts[key] = {
            **{
                field: value
                for field, value in record.items()
                if field not in {"coordinate_frame", "scale"}
            },
            **({"coordinateFrame": frame} if frame is not None else {}),
        }
    part_print_matrices = {
        part_name: record["transform"]["matrix"]
        for part_name, record in print_parts.items()
    }
    plate_print_matrices = {
        part_name: transform["matrix"]
        for part_name, transform in plate_transforms.items()
    }
    part_records = {
        part_name: {
            "print": _manifest_geometry_record(print_parts[part_name]),
            "representationMaster": "brep",
            "semantic": _manifest_geometry_record(stats),
        }
        for part_name, (_, stats) in normalized.items()
    }
    try:
        semantic_assembly = semantic_assembly_record(
            part_records, inputs["intent"]["sha256"], intent_data
        )
    except ValueError as error:
        raise BuildInvariantError(str(error)) from error
    report = {
        "artifactMatrix": {
            "parts": {
                part_name: {
                    "glb": "required",
                    "step": "required",
                    "stl": "required",
                    "threeMf": (
                        "required" if normalized_colors is not None else "not-applicable"
                    ),
                }
                for part_name in normalized
            }
        },
        "artifacts": manifest_artifacts,
        "autoScale": False,
        "backend": "brep-assembly",
        "backendData": {
            "assembly": {
                "maxOverlapMm3": float(max_overlap_mm3),
                "shape": assembly_stats,
            },
            "overlapsMm3": overlaps,
            "exportAudit": export_audit,
            "parameters": dict(_PARAMETERS),
            "printPlate": {
                **_manifest_geometry_record(print_plate_stats),
                "layout": plate_layout,
            },
            "semanticAssembly": semantic_assembly,
            **color_fields,
        },
        "builtAt": utc_timestamp(),
        "coordinateFrames": {
            "semantic": {
                "scale": 1.0,
                "units": "mm",
                "up": scene_data["coordinateSystem"]["up"],
            },
            "part-print": {
                "partTransforms": part_print_matrices,
                "scale": 1.0,
                "units": "mm",
            },
            "plate-print": {
                "layout": plate_layout,
                "partTransforms": plate_print_matrices,
                "profileId": plate_profile.get("id"),
                "scale": 1.0,
                "units": "mm",
            },
        },
        "events": list(_EVENTS),
        "features": dict(_FEATURES),
        "inputs": inputs,
        "materialPlan": material_plan,
        "part": name,
        "parts": part_records,
        "pass": export_audit["pass"],
        "revision": scene_data["revision"],
        "runId": new_run_id(),
        "scale": 1.0,
        "schema": "evidence-a3d-build/v1",
        "warnings": [],
    }
    manifest_errors = validate_manifest(report)
    if manifest_errors:
        raise BuildInvariantError(
            "exporter produced an invalid build manifest: "
            + "; ".join(manifest_errors)
        )
    report_path = output / f"{name}_report.json"
    write_json_atomic(report_path, report)
    _raise_deferred_source_issues()
    print(json.dumps(report, indent=2))
    return report
