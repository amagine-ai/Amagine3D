"""Fail-closed build runtime for evidence-driven single-material CAD.

Generated part scripts use this module to make failed booleans and silent
finish degradation observable. Exports carry hashes that tie geometry back to
the source and intent contract used in the current run.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
validate_manufacturing = _intent_contract.validate_manufacturing


class BuildInvariantError(RuntimeError):
    """Raised when a requested modeling operation did not actually happen."""


_EVENTS: list[dict] = []
_FEATURES: dict[str, dict] = {}
_PARAMETERS: dict[str, dict] = {}
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_MODEL_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_DISPLAY_TINTS = (
    (155, 167, 179),
    (112, 142, 166),
    (176, 151, 118),
    (124, 158, 130),
    (168, 132, 148),
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


def _translate(shape, x: float, y: float, z: float):
    return Pos(x, y, z) * shape


def _rotate(shape, rx: float, ry: float, rz: float):
    return Rot(rx, ry, rz) * shape


def _print_part(shape):
    box = shape.bounding_box()
    transform = [-box.min.X, -box.min.Y, -box.min.Z]
    return _translate(shape, *transform), {
        "from": "assembly",
        "to": "part-print",
        "translate_mm": [round(float(value), 5) for value in transform],
    }


def _profile_from_intent(intent_data: dict | None, intent_path: Path | None) -> dict | None:
    if intent_data is None or intent_path is None:
        return None
    reference = intent_data.get("printability", {}).get("profile", {})
    raw_path = reference.get("path") if isinstance(reference, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = intent_path.parent / path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


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
    profile: dict | None,
) -> tuple[bool, dict]:
    if profile is None:
        return True, {"reason": "no profile available during export"}
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


def _uniform_scale_to_fit_profile(dimensions: list[float], profile: dict | None) -> dict:
    if profile is None:
        return {"available": False, "reason": "no profile available during export"}
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


def _identity_print_orientation(shape) -> dict:
    box = shape.bounding_box()
    dimensions = [
        float(box.max.X - box.min.X),
        float(box.max.Y - box.min.Y),
        float(box.max.Z - box.min.Z),
    ]
    translate = [
        round(float(-box.min.X), 5),
        round(float(-box.min.Y), 5),
        round(float(-box.min.Z), 5),
    ]
    selected = {
        "bed_contact_semantic_face": "bottom",
        "bed_fit": {"reason": "no profile available during export"},
        "dimensions_mm": [round(value, 5) for value in dimensions],
        "fits_profile": True,
        "height_fits": True,
        "name": "identity",
        "protected_contact_face_penalty": 0,
        "rotate_degrees_xyz": [0.0, 0.0, 0.0],
        "score": [0, 0.0, 0, 0, 0.0, 0.0, 0, round(dimensions[2], 5), 0],
        "translate_mm": translate,
    }
    return {
        "candidates": [selected],
        "selected": selected,
        "strategy": "identity-no-profile",
    }


def _select_print_orientation(
    shape,
    profile: dict | None,
    *,
    intent_data: dict | None = None,
) -> dict:
    if profile is None:
        return _identity_print_orientation(shape)
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
    return {
        "from": "semantic",
        "rotate_degrees_xyz": selected["rotate_degrees_xyz"],
        "to": "part-print",
        "translate_mm": selected["translate_mm"],
    }


def _print_plate(parts: dict[str, object], spacing_mm: float = 5.0):
    placed = {}
    transforms = {}
    cursor = 0.0
    for part_name, shape in parts.items():
        box = shape.bounding_box()
        placed_shape = _translate(shape, cursor - box.min.X, -box.min.Y, -box.min.Z)
        placed[part_name] = placed_shape
        transforms[part_name] = {
            "from": "assembly",
            "to": "plate-print",
            "translate_mm": [
                round(float(cursor - box.min.X), 5),
                round(float(-box.min.Y), 5),
                round(float(-box.min.Z), 5),
            ],
        }
        placed_box = placed_shape.bounding_box()
        cursor = placed_box.max.X + spacing_mm
    return Compound(children=list(placed.values())), placed, transforms


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
        raise BuildInvariantError(f"cut {feature_id!r} failed: {error}") from error
    removed = before - float(result.volume)
    _EVENTS.append({
        "id": feature_id,
        "kind": "cut",
        "removed_mm3": round(removed, 6),
        "tool": tool_stats,
        **({"part": part_name} if part_name is not None else {}),
    })
    if removed < min_removed_mm3:
        raise BuildInvariantError(
            f"cut {feature_id!r} removed {removed:.6f} mm^3; tool likely missed"
        )
    if not _valid(result):
        raise BuildInvariantError(f"cut {feature_id!r} produced an invalid solid")
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
        raise BuildInvariantError(f"{kind} {feature_id!r} selected no edges")
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
    raise BuildInvariantError(
        f"{kind} {feature_id!r} failed at requested sizes ({'; '.join(errors)})"
    )


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


def _source_record(source_path: str | None) -> dict | None:
    source = Path(source_path or sys.argv[0]).resolve()
    return {"path": str(source), "sha256": _digest(source)} if source.is_file() else None


def _read_intent(intent_path: str | None) -> tuple[Path | None, dict | None]:
    if intent_path is None:
        return None, None
    intent = Path(intent_path).resolve()
    if not intent.is_file():
        raise BuildInvariantError(f"intent contract not found: {intent}")
    try:
        intent_data = json.loads(intent.read_text(encoding="utf-8"))
    except Exception as error:
        raise BuildInvariantError(f"could not read intent contract: {error}") from error
    if not isinstance(intent_data, dict) or intent_data.get("schema") != INTENT_SCHEMA:
        raise BuildInvariantError(f"intent contract must use {INTENT_SCHEMA}")
    coordinate_errors = validate_coordinate_system(
        intent_data.get("coordinate_system")
    )
    if coordinate_errors:
        raise BuildInvariantError(
            "invalid coordinate system: " + "; ".join(coordinate_errors)
        )
    return intent, intent_data


def _intent_record(intent_path: str | None) -> tuple[Path | None, dict | None]:
    intent, _ = _read_intent(intent_path)
    if intent is None:
        return None, None
    return intent, {"path": str(intent), "sha256": _digest(intent)}


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
    intent_path: str | None = None,
    source_path: str | None = None,
) -> dict:
    """Export printable STL, display GLB, assembly STEP, and build evidence."""
    stats = _stats(shape)
    if not stats["valid"] or stats["solid_count"] != 1:
        raise BuildInvariantError(
            f"final shape must be one valid solid, got {stats['solid_count']}"
        )

    output = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", out_dir))
    output.mkdir(parents=True, exist_ok=True)
    intent_path_resolved, intent_data = _read_intent(intent_path)
    intent = (
        {"path": str(intent_path_resolved), "sha256": _digest(intent_path_resolved)}
        if intent_path_resolved is not None
        else None
    )
    profile = _profile_from_intent(intent_data, intent_path_resolved)
    print_orientation = _select_print_orientation(
        shape,
        profile,
        intent_data=intent_data,
    )
    print_shape = _apply_print_orientation(shape, print_orientation)
    print_stats = _stats(print_shape)
    assemble_step_path = output / f"{name}-assemble.step"
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

    report = {
        "artifacts": {
            "stl": {"path": str(stl_path.resolve()), "sha256": _digest(stl_path)},
            "step:assemble": {
                "path": str(assemble_step_path.resolve()),
                "sha256": _digest(assemble_step_path),
            },
            "glb:display": {
                "path": str(display_glb_path.resolve()),
                "sha256": _digest(display_glb_path),
            },
        },
        "built_at": datetime.now(timezone.utc).isoformat(),
        "coordinates": {
            "print": ["stl"],
            "assembly": ["step:assemble"],
            "display": ["glb:display"],
        },
        "events": list(_EVENTS),
        "features": dict(_FEATURES),
        "intent": intent,
        "parameters": dict(_PARAMETERS),
        "part": name,
        "print": {
            **print_stats,
            "transform": _orientation_transform(print_orientation),
        },
        "print_orientation": print_orientation,
        "schema": "evidence-cad-build/v4",
        "shape": stats,
        "source": _source_record(source_path),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


def export_assembly(
    parts: dict,
    name: str,
    out_dir: str = ".",
    *,
    intent_path: str,
    source_path: str | None = None,
    max_overlap_mm3: float = 0.01,
) -> dict:
    """Export a single-material multi-part assembly.

    Each named part must be one valid solid. The top-level STL is an arranged
    print plate, while the STEP master keeps the physical assembly children.
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

    normalized = {}
    for part_name, shape in parts.items():
        if not isinstance(part_name, str) or not _ID_PATTERN.fullmatch(part_name):
            raise BuildInvariantError(f"invalid assembly part name: {part_name!r}")
        stats = _stats(shape)
        if not stats["valid"] or stats["solid_count"] != 1:
            raise BuildInvariantError(
                f"assembly part {part_name!r} must be one valid solid, "
                f"got {stats['solid_count']}"
            )
        normalized[part_name] = (shape, stats)

    intent_path_resolved, intent = _intent_record(intent_path)
    if intent_path_resolved is None or intent is None:
        raise BuildInvariantError("export_assembly requires an intent contract")
    manufacturing = _validate_assembly_intent(
        intent_path_resolved, name, set(normalized)
    )
    _validate_assembly_evidence(set(normalized))
    _validate_interface_evidence(manufacturing)
    output = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", out_dir))
    output.mkdir(parents=True, exist_ok=True)

    overlaps = {}
    names = list(normalized)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            try:
                overlap = float((normalized[left][0] & normalized[right][0]).volume)
            except Exception as error:
                raise BuildInvariantError(
                    f"could not compare overlap for {left!r} and {right!r}: {error}"
                ) from error
            pair_id = "&".join(sorted((left, right)))
            overlaps[pair_id] = round(overlap, 6)
            if overlap > max_overlap_mm3:
                raise BuildInvariantError(
                    f"assembly parts {left!r} and {right!r} overlap by "
                    f"{overlap:.6f} mm^3"
                )

    artifacts = {}
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
        print_parts[part_name] = {
            **_stats(print_shape),
            "transform": print_transform,
        }
        try:
            shape.label = part_name
        except Exception:
            pass
        children.append(shape)

    assembly_shape = Compound(children=children)
    assembly_stats = _stats(assembly_shape)
    if not assembly_stats["valid"]:
        raise BuildInvariantError("assembly geometry is invalid")
    print_plate, _, plate_transforms = _print_plate(
        {part_name: shape for part_name, (shape, _) in normalized.items()}
    )
    print_plate_stats = _stats(print_plate)
    if not print_plate_stats["valid"]:
        raise BuildInvariantError("print plate geometry is invalid")
    stl_path = output / f"{name}.stl"
    export_stl(print_plate, str(stl_path), tolerance=0.01, angular_tolerance=0.1)
    artifacts["stl"] = {
        "path": str(stl_path.resolve()),
        "sha256": _digest(stl_path),
    }

    assemble_step_path = output / f"{name}-assemble.step"
    display_glb_path = output / f"{name}-display.glb"
    export_step(assembly_shape, str(assemble_step_path), unit=Unit.MM)
    _export_display_glb(
        (
            (part_name, shape, _DISPLAY_TINTS[index % len(_DISPLAY_TINTS)])
            for index, (part_name, (shape, _)) in enumerate(normalized.items())
        ),
        display_glb_path,
    )
    artifacts["step:assemble"] = {
        "path": str(assemble_step_path.resolve()),
        "sha256": _digest(assemble_step_path),
    }
    artifacts["glb:display"] = {
        "path": str(display_glb_path.resolve()),
        "sha256": _digest(display_glb_path),
    }

    report = {
        "assembly": {
            "max_overlap_mm3": float(max_overlap_mm3),
            "shape": assembly_stats,
        },
        "artifacts": artifacts,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "coordinates": {
            "print": ["stl", *[f"stl:{part_name}" for part_name in normalized]],
            "assembly": ["step:assemble"],
            "display": ["glb:display"],
        },
        "events": list(_EVENTS),
        "features": dict(_FEATURES),
        "intent": intent,
        "manufacturing": manufacturing,
        "overlaps_mm3": overlaps,
        "parameters": dict(_PARAMETERS),
        "part": name,
        "parts": {part_name: stats for part_name, (_, stats) in normalized.items()},
        "print_parts": print_parts,
        "print_plate": {
            **print_plate_stats,
            "part_transforms": plate_transforms,
        },
        "schema": "evidence-cad-assembly-build/v3",
        "source": _source_record(source_path),
    }
    report_path = output / f"{name}_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


# Old sources remain editable; new sources use the fail-closed names above.
safe_cut = checked_cut
safe_fillet = checked_fillet
safe_chamfer = checked_chamfer
measure = observe
finalize = export_part
