"""Fail-closed BRep backend for the unified evidence-driven CAD workflow.

Generated part scripts use this module to make failed booleans and silent
finish degradation observable. Exports carry hashes that tie geometry back to
the source and intent contract used in the current run.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import heapq
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

from cad_diagnostics import (
    SOURCE_DIAGNOSTICS_SCHEMA,
    CadDiagnosticError,
    source_diagnostics_payload,
    write_source_diagnostics,
)
from display_glb import (
    DisplayGlbError,
    appearance as display_appearance,
    export_display_glb,
    load_display_components,
)
from geometry_binding import (
    GeometryBindingError,
    export_shape_stl,
    shape_to_mesh,
)

from build123d import (
    Compound,
    Pos,
    Rot,
    Unit,
    chamfer,
    export_step,
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
distinct_scene_part_color = _material_plan.distinct_scene_part_color
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


class _EvidenceState:
    """Operation records belonging to one build, independent of other builds."""

    def __init__(self, events=None, features=None, parameters=None, issues=None):
        self.events = [] if events is None else events
        self.features = {} if features is None else features
        self.parameters = {} if parameters is None else parameters
        self.issues = [] if issues is None else issues


_ACTIVE_EVIDENCE: ContextVar[_EvidenceState | None] = ContextVar(
    "cad_build_evidence", default=None
)


def _evidence() -> _EvidenceState:
    active = _ACTIVE_EVIDENCE.get()
    if active is not None:
        return active
    # Keep legacy helpers and callers that reset the process-local records.
    return _EvidenceState(_EVENTS, _FEATURES, _PARAMETERS, _DEFERRED_ISSUES)


@contextmanager
def _evidence_scope(state: _EvidenceState):
    token = _ACTIVE_EVIDENCE.set(state)
    try:
        yield
    finally:
        _ACTIVE_EVIDENCE.reset(token)


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_MODEL_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
def _collect_source_diagnostics() -> bool:
    return os.environ.get("AMAGINE3D_SOURCE_PHASE") in {"compile", "draft"}


def _defer_source_issue(issue: dict, message: str) -> bool:
    if not _collect_source_diagnostics():
        raise BuildInvariantError(message)
    _evidence().issues.append(
        {
            "severity": "error",
            **issue,
            "message": message,
        }
    )
    if os.environ.get("AMAGINE3D_SOURCE_PHASE") == "draft":
        _raise_deferred_source_issues()
    return True


def _raise_deferred_source_issues() -> None:
    if not _evidence().issues:
        return
    issues = list(_evidence().issues)
    _evidence().issues.clear()
    write_source_diagnostics(source_diagnostics_payload(issues))
    print(
        json.dumps(
            {
                "issues": issues,
                "pass": False,
                "schema": SOURCE_DIAGNOSTICS_SCHEMA,
            },
            indent=2,
        )
    )
    raise BuildInvariantError(
        f"{len(issues)} checked source operations require repair"
    )


def _raise_source_diagnostics(
    diagnostics: Iterable[CadDiagnosticError],
    message: str,
) -> None:
    items = list(diagnostics)
    if _collect_source_diagnostics():
        write_source_diagnostics(source_diagnostics_payload(items))
        print(json.dumps(source_diagnostics_payload(items), indent=2))
    raise BuildInvariantError(message)


def _display_mesh(shape, label: str) -> trimesh.Trimesh:
    try:
        return shape_to_mesh(
            shape,
            f"display geometry {label}",
            linear_tolerance_mm=0.01,
            angular_tolerance_rad=0.1,
        )
    except GeometryBindingError as error:
        raise BuildInvariantError(str(error)) from error


def _display_style(color: tuple[int, int, int]) -> dict:
    try:
        return display_appearance(
            "#" + "".join(f"{channel:02X}" for channel in color)
        )
    except DisplayGlbError as error:
        raise BuildInvariantError(str(error)) from error


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
    if not _ID_PATTERN.fullmatch(parameter_id) or parameter_id in _evidence().parameters:
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
    _evidence().parameters[parameter_id] = descriptor
    return value


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rgb_color(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def _part_color_plan(
    part_colors,
    intent_data: dict,
    scene_data: dict,
    part_names: set[str],
) -> tuple[dict[str, str], list[dict], list[dict]]:
    declared = intent_data.get("color_regions")
    if part_colors is None and declared is None:
        scene_parts = {
            part["id"]: part
            for part in scene_data.get("parts", [])
            if isinstance(part, dict) and isinstance(part.get("id"), str)
        }
        scene_materials = {
            material["id"]: material
            for material in scene_data.get("materials", [])
            if isinstance(material, dict) and isinstance(material.get("id"), str)
        }
        if set(scene_parts) != part_names:
            raise BuildInvariantError(
                "scene parts must exactly match the exported physical parts"
            )
        normalized: dict[str, str] = {}
        material_by_id: dict[str, dict] = {}
        bindings: list[dict] = []
        used_colors = {
            str(value).upper()
            for part in scene_parts.values()
            for value in (
                part.get("color"),
                (part.get("appearance") or {}).get("baseColor")
                if isinstance(part.get("appearance"), dict)
                else None,
                (part.get("appearance") or {}).get("color")
                if isinstance(part.get("appearance"), dict)
                else None,
            )
            if isinstance(value, str) and _HEX_COLOR.fullmatch(value)
        }
        used_colors.update(
            str(material.get("color")).upper()
            for material in scene_materials.values()
            if isinstance(material.get("color"), str)
            and _HEX_COLOR.fullmatch(material["color"])
        )
        for part_name in sorted(part_names):
            scene_part = scene_parts[part_name]
            explicit_id = scene_part.get("materialId")
            if isinstance(explicit_id, str):
                raw_material = scene_materials.get(explicit_id)
                if raw_material is None:
                    raise BuildInvariantError(
                        f"scene part {part_name!r} references unknown material "
                        f"{explicit_id!r}"
                    )
                material_id = explicit_id
                color = str(raw_material.get("color", "")).upper()
                source_id = explicit_id
                source_kind = "scene-part-material"
            else:
                material_id = f"proposed-{part_name}"
                color = distinct_scene_part_color(scene_part, used_colors)
                source_id = part_name
                source_kind = "scene-part-appearance"
            material = material_record(
                material_id,
                color,
                status="proposed",
            )
            previous = material_by_id.get(material_id)
            if previous is not None and previous != material:
                raise BuildInvariantError(
                    f"scene material {material_id!r} resolves inconsistently"
                )
            material_by_id[material_id] = material
            normalized[part_name] = material["color"]
            bindings.append(
                source_binding(
                    material=material,
                    part=part_name,
                    region=None,
                    scope="whole-part",
                    source_id=source_id,
                    source_kind=source_kind,
                )
            )
        return normalized, list(material_by_id.values()), bindings
    if part_colors is None:
        if not isinstance(declared, list):
            raise BuildInvariantError("intent color_regions must be a list")
        declared_by_name = {
            item["name"]: item
            for item in declared
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if set(declared_by_name) != part_names or any(
            declared_by_name[part_name].get("part") != part_name
            for part_name in part_names
        ):
            raise BuildInvariantError(
                "internal color regions require the matching region export path"
            )
        part_colors = {
            part_name: declared_by_name[part_name].get("hex")
            for part_name in part_names
        }
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
    expected_package_mode = (
        "co_print_body" if len(part_names) == 1 else "separate_parts"
    )
    if package_mode != expected_package_mode:
        raise BuildInvariantError(
            f"declared whole-part colors require {expected_package_mode} 3MF output"
        )

    materials = []
    for part_name in normalized:
        materials.append(
            material_record(
                part_name,
                normalized[part_name],
                status="declared",
            )
        )
    bindings = [
        source_binding(
            material=material,
            part=material["id"],
            region=None,
            scope="whole-part",
            source_id=material["id"],
            source_kind="intent-color-region",
        )
        for material in materials
    ]
    return normalized, materials, bindings


def _write_part_color_archive(
    entries,
    path: Path,
    name: str,
    *,
    package_mode: str,
) -> dict:
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
            package_mode=package_mode,
            package_name=name,
        )
    except CadDiagnosticError as error:
        _raise_source_diagnostics([error], f"could not write colored 3MF: {error}")
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
    diagnostics = []
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
            diagnostics.append(
                CadDiagnosticError(
                    check="assembly-part-topology",
                    code="SOURCE.PART_NOT_SINGLE_SOLID",
                    message=invalid_parts[-1],
                    part=part_name,
                    observed=_manifest_geometry_record(stats),
                    expected={"bodyCount": 1, "valid": True},
                )
            )
    if invalid_parts:
        _raise_source_diagnostics(diagnostics, "; ".join(invalid_parts))
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
        export_shape_stl(
            shape, mesh_path, linear_tolerance_mm=0.05, angular_tolerance_rad=0.2
        )
        mesh = trimesh.load(mesh_path, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise BuildInvariantError("print orientation tessellation is empty or invalid")
    normals = np.asarray(mesh.face_normals, dtype=float)
    triangles = np.asarray(mesh.triangles, dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    bounds = np.asarray(mesh.bounds, dtype=float)
    if (
        not all(np.isfinite(values).all() for values in (normals, triangles, areas, bounds))
        or not (areas > 0).all()
    ):
        raise BuildInvariantError("print orientation tessellation is empty or invalid")
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
    stability_offset = None
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
        "stability_offset_ratio": (
            round(stability_offset, 8) if stability_offset is not None else None
        ),
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
        # Contact penalties sort first. Missing stability only ties candidates
        # that both lack measurable contact; it never describes a stable base.
        stability_score = (
            round(float(metrics["stability_offset_ratio"]), 8)
            if metrics["stability_offset_ratio"] is not None
            else 0.0
        )
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
                no_contact_penalty,
                center_penalty,
                round(float(metrics["overhang_area_mm2"]), 5),
                stability_score,
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
    """Arrange parts using rigid bed-plane turns and translations at scale one."""
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
            allow_rotation=True,
        )
    except PlateLayoutError as error:
        raise BuildInvariantError(str(error)) from error

    placed = {}
    transforms = {}
    for part_name, shape in parts.items():
        translate = layout["transforms"][part_name]
        rotation = layout["rotations"][part_name]
        placed[part_name] = _translate(_rotate(shape, *rotation), *translate)
        transforms[part_name] = rigid_transform(
            "assembly-semantic",
            "plate-print",
            rotate_degrees_xyz=rotation,
            translate_mm=translate,
        )
    return Compound(children=list(placed.values())), placed, transforms, layout


def _assembly_print_layout(parts: dict[str, object], profile: dict, *, intent_data=None):
    """Pack preferred poses first, then search stable alternatives without scaling."""
    orientations = {
        name: _select_print_orientation(shape, profile, intent_data=intent_data)
        for name, shape in parts.items()
    }
    oriented = {
        name: _apply_print_orientation(shape, orientations[name])
        for name, shape in parts.items()
    }
    try:
        return orientations, oriented, _print_plate(oriented, profile=profile)
    except BuildInvariantError as error:
        if not isinstance(error.__cause__, PlateLayoutError) or error.__cause__.kind != "layout-not-found":
            raise
        original_error = error

    names = sorted(parts)
    options = []
    for name in names:
        fitting = sorted(
            (c for c in orientations[name]["candidates"] if c["fits_profile"]),
            key=lambda c: c["score"],
        )
        if not fitting:
            raise original_error
        # A packed plate must not trade an available stable base for zero contact
        # or an unsupported center of mass. Equal footprints need only one pose.
        stability_class = fitting[0]["score"][1:3]
        unique = {}
        for candidate in fitting:
            if candidate["score"][1:3] != stability_class:
                continue
            x, y, z = candidate["dimensions_mm"]
            unique.setdefault((min(x, y), max(x, y), z), candidate)
        options.append(list(unique.values()))

    def priority(indices):
        scores = [options[i][index]["score"] for i, index in enumerate(indices)]
        return tuple(sum(values) for values in zip(*scores))

    initial = (0,) * len(names)
    queue = [(priority(initial), initial)]
    visited = {initial}
    cache = {}
    attempts = 0
    while queue and attempts < 256:
        _, indices = heapq.heappop(queue)
        attempts += 1
        trial = {
            name: {**orientations[name], "selected": {
                key: value for key, value in options[i][indices[i]].items()
                if key != "preference"
            }, "strategy": "bounded-joint-stability-support-layout"}
            for i, name in enumerate(names)
        }
        if indices != initial:  # The preferred combination already failed above.
            placed = {}
            for i, name in enumerate(names):
                key = (name, indices[i])
                if key not in cache:
                    cache[key] = _apply_print_orientation(parts[name], trial[name])
                placed[name] = cache[key]
            try:
                packed = _print_plate(placed, profile=profile)
            except BuildInvariantError as error:
                if not isinstance(error.__cause__, PlateLayoutError) or error.__cause__.kind != "layout-not-found":
                    raise
            else:
                packed[3]["orientationSearch"] = {"attempts": attempts, "limit": 256}
                return trial, placed, packed
        for i, index in enumerate(indices):
            if index + 1 >= len(options[i]):
                continue
            neighbor = indices[:i] + (index + 1,) + indices[i + 1:]
            if neighbor not in visited:
                visited.add(neighbor)
                heapq.heappush(queue, (priority(neighbor), neighbor))
    raise original_error


def observe(
    shape,
    feature_id: str,
    role: str = "feature",
    *,
    part_name: str | None = None,
) -> None:
    """Capture evidence before a feature disappears into a boolean result."""
    if feature_id in _evidence().features:
        raise BuildInvariantError(f"duplicate feature id: {feature_id}")
    _evidence().features[feature_id] = {
        "role": role,
        **({"part": part_name} if part_name is not None else {}),
        **_stats(shape),
    }


def _boolean_witness(body, operand, *, operation, part_name=None, removed_mm3=None):
    """Measure failed-operation inputs without changing their geometry or acceptance.

    OCCT measures distance between BRep surfaces: contained, overlapping solids
    can have positive surface distance. Only a zero-volume common permits calling
    that distance a gap. A touching edge also need not fuse into one solid.
    """
    witness = {"operation": operation, "coordinateFrame": "operation-input", "units": "mm",
               "ownerPartId": part_name, "components": []}
    if removed_mm3 is not None:
        witness["removedMm3"] = round(float(removed_mm3), 8)
    try:
        bodies, components = list(body.solids()), list(operand.solids())
        body_bounds, operand_bounds = _stats(body)["bbox_mm"], _stats(operand)["bbox_mm"]
        common = body & operand
        intersection = float(common.volume) if common else 0.0
        if not math.isfinite(intersection):
            raise ValueError("intersection volume is nonfinite")
        intersection = max(0.0, intersection)
        witness.update(bodyBoundsMm=body_bounds, operandBoundsMm=operand_bounds,
                       bodySolidCount=len(bodies), operandSolidCount=len(components),
                       intersectionVolumeMm3=round(intersection, 8),
                       operandInsideOwnerBounds=all(
                           body_bounds["min"][i] - 1e-7 <= operand_bounds["min"][i]
                           and operand_bounds["max"][i] <= body_bounds["max"][i] + 1e-7
                           for i in range(3)),
                       containmentBasis="axis-aligned bounds only; no prior-cut or enclosed-cavity attribution")
        for index, component in enumerate(components):
            record = {"solidIndex": index, "connectedToOwner": None, "relation": "unavailable"}
            try:
                record["boundsMm"] = _stats(component)["bbox_mm"]
                common = body & component
                volume = float(common.volume) if common else 0.0
                if not math.isfinite(volume):
                    raise ValueError("component intersection volume is nonfinite")
                volume = max(0.0, volume)
                distance, owner_point, operand_point = body.distance_to_with_closest_points(component)
                values = [float(distance), *owner_point, *operand_point]
                if not all(math.isfinite(value) for value in values):
                    raise ValueError("closest-point measurement is nonfinite")
                overlap = volume > 1e-9
                touching = distance <= 1e-7
                connected = overlap or (touching and any(
                    len((owner_solid + component).solids()) == 1 for owner_solid in bodies))
                record.update(connectedToOwner=connected,
                              relation="volume-overlap" if overlap else "touching" if touching else "disjoint",
                              intersectionVolumeMm3=round(volume, 8), surfaceDistanceMm=round(float(distance), 8),
                              gapMm=None if overlap else round(float(distance), 8),
                              ownerPointMm=[round(float(v), 8) for v in owner_point],
                              operandPointMm=[round(float(v), 8) for v in operand_point])
            except Exception as error:
                record["measurementError"] = str(error)
            witness["components"].append(record)
        witness["unconnectedComponentCount"] = sum(item["connectedToOwner"] is False for item in witness["components"])
        witness["unresolvedComponentCount"] = sum(item["connectedToOwner"] is None for item in witness["components"])
    except Exception as error:
        # Secondary measurement failure must not replace the original boolean error.
        witness["measurementError"] = str(error)
    return witness


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
    _evidence().events.append({
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
                    **({"booleanWitness": _boolean_witness(body, tool, operation="cut", part_name=part_name,
                                                           removed_mm3=removed)} if _collect_source_diagnostics() else {}),
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


def checked_union(
    body,
    addition,
    feature_id: str,
    min_added_mm3: float = 0.001,
    *,
    part_name: str | None = None,
):
    """Fuse an additive feature and require one valid, connected solid."""

    if (
        isinstance(min_added_mm3, bool)
        or not isinstance(min_added_mm3, (int, float))
        or not math.isfinite(min_added_mm3)
        or min_added_mm3 < 0
    ):
        raise BuildInvariantError("min_added_mm3 must be finite and non-negative")
    before = float(body.volume)
    addition_stats = _stats(addition)
    try:
        result = body + addition
        result_stats = _stats(result)
    except Exception as error:
        message = f"union {feature_id!r} failed: {error}"
        if _collect_source_diagnostics():
            _defer_source_issue(
                {
                    "blockedBy": "BOOLEAN_OPERATION_FAILED",
                    "check": "checked-union",
                    "code": "SOURCE.CHECKED_UNION_FAILED",
                    "expected": {"minimumAddedMm3": float(min_added_mm3)},
                    "featureId": feature_id,
                    "observed": {
                        "addition": addition_stats,
                        "body": _stats(body),
                        "error": str(error),
                    },
                    **({"partId": part_name} if part_name is not None else {}),
                    "status": "blocked",
                },
                message,
            )
            return body
        raise BuildInvariantError(message) from error
    if result_stats["solid_count"] != 1:
        message = (
            f"union {feature_id!r} produced {result_stats['solid_count']} solids; "
            "the additive feature is not connected to its owner"
        )
        if _defer_source_issue(
            {
                "check": "checked-union",
                "code": "SOURCE.UNION_DISCONNECTED",
                "expected": {"bodyCount": 1},
                "featureId": feature_id,
                "observed": {
                    "addition": addition_stats,
                    "result": _manifest_geometry_record(result_stats),
                    **({"booleanWitness": _boolean_witness(body, addition, operation="union", part_name=part_name)}
                       if _collect_source_diagnostics() else {}),
                },
                **({"partId": part_name} if part_name is not None else {}),
            },
            message,
        ):
            return body
    if not result_stats["valid"]:
        message = f"union {feature_id!r} produced an invalid solid"
        if _defer_source_issue(
            {
                "check": "checked-union",
                "code": "SOURCE.UNION_INVALID_RESULT",
                "expected": {"validSolid": True},
                "featureId": feature_id,
                "observed": _manifest_geometry_record(result_stats),
                **({"partId": part_name} if part_name is not None else {}),
            },
            message,
        ):
            return body
    added = float(result.volume) - before
    if added < min_added_mm3:
        message = (
            f"union {feature_id!r} added {added:.6f} mm^3; "
            "the additive feature had no material effect"
        )
        if _defer_source_issue(
            {
                "check": "checked-union",
                "code": "SOURCE.UNION_NO_EFFECT",
                "expected": {"minimumAddedMm3": float(min_added_mm3)},
                "featureId": feature_id,
                "observed": {
                    "addedMm3": round(added, 6),
                    "addition": addition_stats,
                },
                **({"partId": part_name} if part_name is not None else {}),
            },
            message,
        ):
            return body
    _evidence().events.append(
        {
            "added_mm3": round(added, 6),
            "id": feature_id,
            "kind": "union",
            "tool": addition_stats,
            **({"part": part_name} if part_name is not None else {}),
        }
    )
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
            _evidence().events.append({
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
    for feature_id, record in _evidence().features.items():
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
    for event in _evidence().events:
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
    observed = set(_evidence().features) | {
        event.get("id") for event in _evidence().events if isinstance(event.get("id"), str)
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
    """Export STEP/STL, appearance GLB, optional material 3MF, and evidence."""
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
    part_colors, materials, material_bindings = _part_color_plan(
        None,
        intent_data,
        scene_data,
        {name},
    )
    manufactured_color = bool(material_bindings)
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
    try:
        display_components = load_display_components(scene_data, scene_path)
        display_nodes = export_display_glb(
            (
                (
                    name,
                    _display_mesh(shape, name),
                    _display_style(_rgb_color(part_colors[name])),
                ),
            ),
            display_glb_path,
            display_items=display_components,
        )
    except DisplayGlbError as error:
        raise BuildInvariantError(str(error)) from error
    export_shape_stl(print_shape, stl_path)

    color_artifacts = {}
    color_backend_data = {}
    material_plan = None
    if manufactured_color:
        archive_path = output / f"{name}.3mf"
        three_mf = _write_part_color_archive(
            [(str(stl_path), part_colors[name], name)],
            archive_path,
            name,
            package_mode="co_print_body",
        )
        assignments = [
            {
                "materialId": binding["materialId"],
                "part": binding["part"],
                "region": binding["region"],
                "scope": binding["scope"],
            }
            for binding in material_bindings
        ]
        material_plan = build_material_plan(
            part=name,
            package_mode="co_print_body",
            materials=materials,
            assignments=assignments,
            source_bindings=material_bindings,
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
        color_artifacts = {
            "3mf": {
                **artifact_record(archive_path, coordinateFrame="plate-print"),
                "validator": "lib3mf",
                "verified": True,
            },
            "materialPlan": artifact_record(material_plan_path),
        }
        color_backend_data = {
            "partColors": part_colors,
            "printPackageMode": "co_print_body",
            "threeMf": three_mf,
        }

    try:
        export_audit = audit_exports(
            stls={
                f"stl:{name}": (stl_path, export_geometry_record(print_shape)),
            },
            steps={
                f"step:{name}": (assemble_step_path, export_geometry_record(shape)),
            },
            glb=(
                display_glb_path,
                [name, *[node_id for node_id, _, _ in display_components]],
            ),
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
                name: {
                    "glb": "required",
                    "step": "required",
                    "stl": "required",
                    "threeMf": (
                        "required" if manufactured_color else "not-applicable"
                    ),
                }
            }
        },
        "artifacts": {
            f"stl:{name}": artifact_record(stl_path, coordinateFrame="part-print"),
            f"step:{name}": artifact_record(
                assemble_step_path, coordinateFrame="semantic"
            ),
            "glb:display": {
                **artifact_record(display_glb_path, coordinateFrame="semantic"),
                **display_nodes,
            },
            **color_artifacts,
            "exportAudit": artifact_record(export_audit_path),
        },
        "autoScale": False,
        "backend": "brep-part",
        "backendData": {
            "exportAudit": export_audit,
            "parameters": dict(_evidence().parameters),
            "printOrientation": print_orientation,
            "semanticAssembly": semantic_assembly,
            **color_backend_data,
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
            {**event, "part": event.get("part", name)} for event in _evidence().events
        ],
        "features": {
            feature_id: {**record, "part": record.get("part", name)}
            for feature_id, record in _evidence().features.items()
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
        **({"materialPlan": material_plan} if material_plan is not None else {}),
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


def _packing_diagnostic_candidate(normalized, name, output, colors, inputs, scene_data, scene_path):
    """Preserve semantic geometry when manufacturing layout cannot be completed.

    This deliberately emits no successful build manifest or print package. The
    compiler verifies these current-run bindings before exposing a diagnostic.
    """
    from cpu_z_buffer import render_contact_sheet
    from render_preview import _render_inputs, DEFAULT_MATERIAL

    run_id = new_run_id()
    prefix = f"{name}-{run_id}-diagnostic"
    step_path = output / f"{prefix}.step"
    glb_path = output / f"{prefix}.glb"
    preview_path = output / f"{prefix}.png"
    assembly = Compound(children=[shape for shape, _ in normalized.values()])
    export_step(assembly, str(step_path), unit=Unit.MM)
    export_display_glb(
        ((part, _display_mesh(shape, part), _display_style(_rgb_color(colors[part])))
         for part, (shape, _) in normalized.items()),
        glb_path, display_items=load_display_components(scene_data, scene_path),
    )
    rendered = render_contact_sheet(_render_inputs(glb_path, DEFAULT_MATERIAL), 640,
                                    title="Diagnostic geometry — print layout incomplete")
    rendered.image.save(preview_path, format="PNG")
    return {"runId": run_id, "inputBindings": inputs, "manufacturingValidated": False,
            "artifacts": {kind: {"path": str(path.resolve()), "sha256": _digest(path)}
                          for kind, path in (("step", step_path), ("glb", glb_path), ("preview", preview_path))}}


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
    """Export a BRep-master multi-part assembly with whole-part color.

    Each named part must be one valid solid. The top-level STL is an arranged
    print plate, while the STEP master and display GLB keep semantic assembly
    coordinates. ``part_colors`` binds declared intent colors; otherwise scene
    appearance or stable proposed colors are used. The same plate-aligned
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
    normalized_colors, material_regions, material_bindings = _part_color_plan(
        part_colors,
        intent_data,
        scene_data,
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

    # Failed packing must not erase otherwise inspectable semantic geometry.
    try:
        print_orientations, oriented_parts, packed = _assembly_print_layout(
            {name: shape for name, (shape, _) in normalized.items()},
            plate_profile, intent_data=intent_data,
        )
        print_plate, plate_parts, plate_transforms, plate_layout = packed
    except BuildInvariantError as error:
        if not isinstance(error.__cause__, PlateLayoutError):
            raise
        issue = {"code": "SOURCE.PLATE_LAYOUT_FAILED", "check": "print-layout",
                 "severity": "error", "message": str(error),
                 "observed": {"kind": error.__cause__.kind, "scale": 1.0},
                 "repairHint": "Review orientation, packing, or a3d layout plate grouping within the bound printer; do not change design targets to silence packing failure."}
        payload = source_diagnostics_payload([*_evidence().issues, issue])
        try:
            payload["diagnosticCandidate"] = _packing_diagnostic_candidate(
                normalized, name, output, normalized_colors, inputs, scene_data, scene_path)
        except Exception as diagnostic_error:
            payload["issues"].append({"code": "SOURCE.DIAGNOSTIC_EXPORT_FAILED", "severity": "warning",
                                      "message": str(diagnostic_error), "check": "diagnostic-export"})
        write_source_diagnostics(payload)
        if _collect_source_diagnostics():
            print(json.dumps(payload, indent=2))
        raise
    for part_name, transform in plate_transforms.items():
        semantic_to_part = _orientation_transform(print_orientations[part_name])
        transform["matrix"] = (
            np.asarray(transform["matrix"]) @ np.asarray(semantic_to_part["matrix"])
        ).round(10).tolist()
    plate_layout["orientations"] = print_orientations
    plate_layout["inputFrame"] = "part-print"
    print_plate_stats = _stats(print_plate)
    if not print_plate_stats["valid"]:
        raise BuildInvariantError("print plate geometry is invalid")

    artifacts = {}
    audit_stls = {}
    audit_steps = {}
    children = []
    print_parts = {}
    for part_name, (shape, stats) in normalized.items():
        print_shape = oriented_parts[part_name]
        print_transform = _orientation_transform(print_orientations[part_name])
        path = output / f"{name}-{part_name}.stl"
        export_shape_stl(print_shape, path)
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
    export_shape_stl(print_plate, stl_path)
    artifacts["stl"] = {
        "path": str(stl_path.resolve()),
        "sha256": _digest(stl_path),
    }
    audit_stls["stl"] = (stl_path, export_geometry_record(print_plate))

    assemble_step_path = output / f"{name}-assemble.step"
    display_glb_path = output / f"{name}-display.glb"
    export_step(assembly_shape, str(assemble_step_path), unit=Unit.MM)
    try:
        display_components = load_display_components(scene_data, scene_path)
        display_nodes = export_display_glb(
            (
                (
                    part_name,
                    _display_mesh(shape, part_name),
                    _display_style(_rgb_color(normalized_colors[part_name])),
                )
                for part_name, (shape, _) in normalized.items()
            ),
            display_glb_path,
            display_items=display_components,
        )
    except DisplayGlbError as error:
        raise BuildInvariantError(str(error)) from error
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
        **display_nodes,
    }

    internal_plate_dir = output / ".amagine3d-internal" / name / "plate"
    internal_plate_dir.mkdir(parents=True, exist_ok=True)
    internal_plate_meshes = {}
    entries = []
    for part_name, shape in plate_parts.items():
        path = internal_plate_dir / f"{name}-{part_name}.stl"
        export_shape_stl(shape, path)
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
    three_mf = _write_part_color_archive(
        entries,
        archive_path,
        name,
        package_mode="separate_parts",
    )
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
            "materialId": binding["materialId"],
            "part": binding["part"],
            "region": binding["region"],
            "scope": binding["scope"],
        }
        for binding in material_bindings
    ]
    material_plan = build_material_plan(
        part=name,
        package_mode="separate_parts",
        materials=material_regions,
        assignments=assignments,
        source_bindings=material_bindings,
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
            glb=(
                display_glb_path,
                [
                    *normalized,
                    *[node_id for node_id, _, _ in display_components],
                ],
            ),
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
                    "threeMf": "required",
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
            "parameters": dict(_evidence().parameters),
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
        "events": list(_evidence().events),
        "features": dict(_evidence().features),
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
