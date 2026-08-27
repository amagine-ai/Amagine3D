"""Strict region-assembly runtime for multi-color printable CAD."""

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

import numpy as np
import trimesh

from build123d import (
    Color,
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


_export_3mf = _load_local_module(
    "_text_a3d_color_export_3mf_for_cad_helpers",
    "export_3mf.py",
)
_intent_contract = _load_local_module(
    "_text_a3d_color_intent_contract_for_cad_helpers",
    "intent_contract.py",
)
write_color_archive = _export_3mf.write_color_archive
validate_coordinate_system = _intent_contract.validate_coordinate_system


class RegionInvariantError(RuntimeError):
    pass


_FEATURES: dict[str, dict] = {}
_EVENTS: list[dict] = []
_PARAMETERS: dict[str, dict] = {}
_REGION_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
PRINT_PACKAGE_MODES = {"co_print_body", "separate_parts"}


def _parameter_overrides() -> dict:
    raw = os.environ.get("AMAGINE3D_PARAMETER_OVERRIDES", "{}").strip() or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RegionInvariantError("invalid parameter override payload") from error
    if not isinstance(value, dict):
        raise RegionInvariantError("parameter overrides must be an object")
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
    if not _PARAMETER_ID.fullmatch(parameter_id) or parameter_id in _PARAMETERS:
        raise RegionInvariantError(f"invalid or duplicate parameter id: {parameter_id!r}")
    numbers = (default, min_value, max_value, step)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numbers):
        raise RegionInvariantError(f"parameter {parameter_id!r} must be numeric")
    if any(not math.isfinite(value) for value in numbers):
        raise RegionInvariantError(f"parameter {parameter_id!r} must be finite")
    if min_value > max_value or not min_value <= default <= max_value or step <= 0:
        raise RegionInvariantError(f"parameter {parameter_id!r} has invalid bounds")
    overrides = _parameter_overrides()
    value = overrides.get(parameter_id, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegionInvariantError(f"parameter {parameter_id!r} override must be numeric")
    if isinstance(default, int) and not isinstance(value, int):
        raise RegionInvariantError(f"parameter {parameter_id!r} override must be an integer")
    if not math.isfinite(value) or not min_value <= value <= max_value:
        raise RegionInvariantError(f"parameter {parameter_id!r} override is out of bounds")
    quotient = (value - min_value) / step
    if not math.isclose(quotient, round(quotient), abs_tol=1e-8):
        raise RegionInvariantError(f"parameter {parameter_id!r} override does not align with step")
    feature_ids = list(affects)
    if any(not isinstance(feature_id, str) or not feature_id for feature_id in feature_ids):
        raise RegionInvariantError(f"parameter {parameter_id!r} has invalid feature IDs")
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


def _shape_record(shape) -> dict:
    bounds = shape.bounding_box()
    return {
        "bbox_mm": {
            "min": [round(bounds.min.X, 4), round(bounds.min.Y, 4), round(bounds.min.Z, 4)],
            "max": [round(bounds.max.X, 4), round(bounds.max.Y, 4), round(bounds.max.Z, 4)],
            "size": [
                round(bounds.max.X - bounds.min.X, 4),
                round(bounds.max.Y - bounds.min.Y, 4),
                round(bounds.max.Z - bounds.min.Z, 4),
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


def _print_transform(shape) -> tuple[float, float, float]:
    box = shape.bounding_box()
    return (-box.min.X, -box.min.Y, -box.min.Z)


def _profile_from_intent(intent_data: dict, intent_path: Path) -> dict | None:
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
        "scale": scale,
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
            "support_mean_clearance_mm": float("inf"),
            "support_volume_proxy_mm3": float("inf"),
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
    footprint_area = max(float((bounds[1, 0] - bounds[0, 0]) * (bounds[1, 1] - bounds[0, 1])), 1e-9)
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
    risky_clearance = np.maximum(
        triangles[:, :, 2].mean(axis=1) - minimum_z,
        0.0,
    )
    support_volume_proxy = float((areas[risky] * risky_clearance[risky]).sum())
    support_mean_clearance = (
        support_volume_proxy / overhang_area
        if overhang_area > 1e-9
        else 0.0
    )
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
        "support_mean_clearance_mm": round(support_mean_clearance, 5),
        "support_volume_proxy_mm3": round(support_volume_proxy, 5),
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
        scale_fit = _uniform_scale_to_fit_profile(dimensions, profile)
        scale = (
            float(scale_fit["scale"])
            if scale_fit.get("available") is True
            else 1.0
        )
        if not math.isfinite(scale) or scale <= 0:
            scale = 1.0
        scaled = rotated.scale(scale) if scale < 1.0 - 1e-9 else rotated
        scaled_box = scaled.bounding_box()
        print_dimensions = [
            float(scaled_box.max.X - scaled_box.min.X),
            float(scaled_box.max.Y - scaled_box.min.Y),
            float(scaled_box.max.Z - scaled_box.min.Z),
        ]
        bed_fits, bed = _footprint_fits(
            print_dimensions[0], print_dimensions[1], profile
        )
        height_fits = (
            True
            if not isinstance(height_limit, (int, float)) or isinstance(height_limit, bool)
            else print_dimensions[2] <= float(height_limit) + 1e-9
        )
        fits = bed_fits and height_fits
        translate = [
            round(float(-scaled_box.min.X), 5),
            round(float(-scaled_box.min.Y), 5),
            round(float(-scaled_box.min.Z), 5),
        ]
        placed = _translate(scaled, *translate)
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
            "eligible_after_uniform_scale": bool(fits),
            "fits_profile": bool(fits),
            "height_fits": bool(height_fits),
            "orientation_metrics": metrics,
            "name": name,
            "preference": preference,
            "print_dimensions_mm": [round(value, 5) for value in print_dimensions],
            "protected_contact_face_penalty": protected_penalty,
            "requires_uniform_scale": bool(
                scale_fit.get("available") is True
                and not scale_fit.get("fits_without_scaling", True)
            ),
            "rotate_degrees_xyz": [round(value, 5) for value in rotation],
            "score": [
                0 if fits else 1,
                round(float(metrics["support_volume_proxy_mm3"]), 5),
                round(float(metrics["overhang_area_mm2"]), 5),
                no_contact_penalty,
                center_penalty,
                round(float(metrics["stability_offset_ratio"]), 8),
                round(-float(metrics["contact_area_mm2"]), 5),
                protected_penalty,
                round(max(0.0, 1.0 - scale), 8),
                round(print_dimensions[2], 5),
                preference,
            ],
            "scale_to_apply": scale,
            "translate_mm": translate,
            "uniform_scale_to_fit_profile": scale_fit,
        })
    return results


def _select_print_orientation(
    shape,
    profile: dict | None,
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
        "strategy": "scaled-support-contact-appearance-score",
    }


def _apply_print_orientation(shape, orientation: dict):
    selected = orientation["selected"]
    rotated = _rotate(shape, *selected["rotate_degrees_xyz"])
    scale = float(selected.get("scale_to_apply", 1.0) or 1.0)
    scaled = rotated.scale(scale) if scale < 1.0 - 1e-9 else rotated
    return _translate(scaled, *selected["translate_mm"])


def observe(shape, feature_id: str, role: str = "feature") -> None:
    if feature_id in _FEATURES:
        raise RegionInvariantError(f"duplicate feature id: {feature_id}")
    _FEATURES[feature_id] = {"role": role, **_shape_record(shape)}


def checked_cut(body, tool, feature_id: str, min_removed_mm3: float = 0.001):
    before = float(body.volume)
    tool_stats = _shape_record(tool)
    try:
        result = body - tool
    except Exception as error:
        raise RegionInvariantError(f"cut {feature_id!r} failed: {error}") from error
    removed = before - float(result.volume)
    _EVENTS.append({
        "id": feature_id,
        "kind": "cut",
        "removed_mm3": round(removed, 6),
        "tool": tool_stats,
    })
    if removed < min_removed_mm3:
        raise RegionInvariantError(f"cut {feature_id!r} missed the parent solid")
    if not _valid(result):
        raise RegionInvariantError(f"cut {feature_id!r} produced invalid geometry")
    return result


def _checked_finish(shape, selector, size_mm: float, feature_id: str, kind: str):
    edges = list(selector(shape) if callable(selector) else selector)
    if not edges:
        raise RegionInvariantError(f"{kind} {feature_id!r} selected no edges")
    try:
        result = (
            fillet(edges, radius=size_mm)
            if kind == "fillet"
            else chamfer(edges, length=size_mm)
        )
    except Exception as error:
        raise RegionInvariantError(f"{kind} {feature_id!r} failed: {error}") from error
    if not _valid(result):
        raise RegionInvariantError(f"{kind} {feature_id!r} produced invalid geometry")
    _EVENTS.append({
        "actual_mm": round(size_mm, 6),
        "degraded": False,
        "id": feature_id,
        "kind": kind,
        "requested_mm": size_mm,
    })
    return result


def checked_fillet(shape, selector, radius_mm: float, feature_id: str):
    return _checked_finish(shape, selector, radius_mm, feature_id, "fillet")


def checked_chamfer(shape, selector, length_mm: float, feature_id: str):
    return _checked_finish(shape, selector, length_mm, feature_id, "chamfer")


def _rgb(value: str) -> tuple[float, float, float]:
    if not _HEX.fullmatch(value):
        raise RegionInvariantError(f"invalid region color: {value}")
    channels = tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))
    return channels  # type: ignore[return-value]


def _rgb8(value: str) -> tuple[int, int, int]:
    if not _HEX.fullmatch(value):
        raise RegionInvariantError(f"invalid region color: {value}")
    return tuple(
        int(value[index:index + 2], 16) for index in (1, 3, 5)
    )  # type: ignore[return-value]


def _export_display_glb(
    items: list[tuple[str, object, str]],
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
                raise RegionInvariantError(
                    f"display GLB mesh for {label!r} is empty"
                )
            mesh.visual.face_colors = [*_rgb8(color), 255]
            mesh.metadata["name"] = label
            scene.add_geometry(mesh, geom_name=label, node_name=label)
    data = scene.export(file_type="glb")
    path.write_bytes(data if isinstance(data, bytes) else bytes(data))


def export_regions(
    regions: dict,
    name: str,
    out_dir: str = ".",
    *,
    intent_path: str,
    parent=None,
    source_path: str | None = None,
    max_coverage_error_mm3: float = 0.01,
) -> dict:
    """Validate color regions and export print, display, CAD, and report evidence."""
    if len(regions) < 2:
        raise RegionInvariantError("multi-color output requires at least two regions")
    if parent is None:
        raise RegionInvariantError(
            "export_regions requires parent= for a co-printed manufacturing body; "
            "color regions are not printable assembly parts"
        )

    output = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", out_dir))
    output.mkdir(parents=True, exist_ok=True)
    intent = Path(intent_path).resolve()
    try:
        intent_data = json.loads(intent.read_text(encoding="utf-8"))
    except Exception as error:
        raise RegionInvariantError(f"could not read intent contract: {error}") from error
    if intent_data.get("schema") != "evidence-color-intent/v3":
        raise RegionInvariantError("intent contract must use evidence-color-intent/v3")
    if intent_data.get("part") != name:
        raise RegionInvariantError("intent part does not match the export name")
    coordinate_errors = validate_coordinate_system(
        intent_data.get("coordinate_system")
    )
    if coordinate_errors:
        raise RegionInvariantError(
            "invalid coordinate system: " + "; ".join(coordinate_errors)
        )
    feature_items = intent_data.get("features", [])
    declared_feature_ids = {
        item.get("id") for item in feature_items if isinstance(item, dict)
    }
    raw_critical_features = intent_data.get("printability", {}).get(
        "critical_features"
    )
    critical_feature_ids = (
        set(raw_critical_features) if isinstance(raw_critical_features, list) else set()
    )
    if (
        not feature_items
        or len(declared_feature_ids) != len(feature_items)
        or not critical_feature_ids
        or not critical_feature_ids.issubset(declared_feature_ids)
    ):
        raise RegionInvariantError(
            "intent critical features must uniquely reference declared features"
        )
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "coordinates": {
            "assembly": ["step:assemble"],
            "display": ["glb:display"],
            "print": ["3mf", "stl"],
        },
        "events": list(_EVENTS),
        "features": dict(_FEATURES),
        "overlaps_mm3": {},
        "parameters": dict(_PARAMETERS),
        "part": name,
        "regions": {},
        "schema": "evidence-color-build/v5",
    }

    normalized: dict[str, tuple] = {}
    for region_name, pair in regions.items():
        if not _REGION_NAME.fullmatch(region_name):
            raise RegionInvariantError(f"unsafe region name: {region_name}")
        shape, color = pair
        color = color.upper()
        _rgb(color)
        record = _shape_record(shape)
        if not record["valid"]:
            raise RegionInvariantError(f"region {region_name!r} is invalid")
        normalized[region_name] = (shape, color)
        report["regions"][region_name] = {"color": color, **record}

    declared_items = intent_data.get("color_regions", [])
    declared = {
        item.get("name"): item
        for item in declared_items
        if isinstance(item, dict)
    }
    if len(declared) != len(declared_items) or set(declared) != set(normalized):
        raise RegionInvariantError(
            "intent color-region names do not uniquely match exported region names"
        )
    material_regions = []
    for region_name, (_, color) in normalized.items():
        item = declared[region_name]
        if str(item.get("hex", "")).upper() != color:
            raise RegionInvariantError(
                f"intent color for {region_name!r} does not match exported color"
            )
        material = item.get("material")
        if material is not None and not isinstance(material, dict):
            raise RegionInvariantError(
                f"intent material for {region_name!r} must be an object"
            )
        material = material or {}
        transmission = material.get("transmission", "opaque")
        if transmission not in {"opaque", "translucent", "transparent"}:
            raise RegionInvariantError(
                f"intent transmission for {region_name!r} is invalid"
            )
        filament = material.get("filament")
        if filament is not None and (
            not isinstance(filament, str) or not filament.strip()
        ):
            raise RegionInvariantError(
                f"intent filament for {region_name!r} must be a non-empty string"
            )
        material_regions.append({
            "color": color,
            "filament": filament,
            "name": region_name,
            "transmission": transmission,
        })
        continuity = item.get("continuity")
        if continuity is not None:
            report["regions"][region_name]["continuity"] = continuity

    package_mode = intent_data.get("printability", {}).get(
        "print_package_mode", "co_print_body"
    )
    if package_mode not in PRINT_PACKAGE_MODES:
        raise RegionInvariantError(
            "printability.print_package_mode must be co_print_body or separate_parts"
        )
    report["print_package_mode"] = package_mode

    names = list(normalized)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            overlap = float((normalized[left][0] & normalized[right][0]).volume)
            report["overlaps_mm3"][f"{left}&{right}"] = round(overlap, 6)
            if overlap > 0.01:
                raise RegionInvariantError(
                    f"regions {left!r} and {right!r} overlap by {overlap:.6f} mm^3"
                )

    region_shapes = [shape for shape, _ in normalized.values()]
    region_volume = sum(float(shape.volume) for shape in region_shapes)
    region_union = region_shapes[0]
    for shape in region_shapes[1:]:
        region_union = region_union + shape
    if not _valid(parent):
        raise RegionInvariantError("coverage parent is invalid")
    try:
        missing = float((parent - region_union).volume)
        outside = float((region_union - parent).volume)
    except Exception as error:
        raise RegionInvariantError(
            f"could not compare region union with parent: {error}"
        ) from error
    coverage_error = missing + outside
    report["parent_coverage"] = {
        "error_mm3": round(coverage_error, 6),
        "missing_mm3": round(missing, 6),
        "outside_mm3": round(outside, 6),
        "parent_volume_mm3": round(float(parent.volume), 6),
        "region_volume_mm3": round(region_volume, 6),
        "volume_balance_error_mm3": round(
            abs(float(parent.volume) - region_volume), 6
        ),
    }
    if coverage_error > max_coverage_error_mm3:
        raise RegionInvariantError(
            "region union does not match parent; "
            f"missing {missing:.6f} mm^3, outside {outside:.6f} mm^3"
        )

    assembly_body = parent
    assembly_record = _shape_record(assembly_body)
    if not assembly_record["valid"]:
        raise RegionInvariantError("assembly manufacturing geometry is invalid")

    profile = _profile_from_intent(intent_data, intent)
    print_orientation = _select_print_orientation(
        assembly_body,
        profile,
        intent_data=intent_data,
    )
    print_regions = {
        region_name: (_apply_print_orientation(shape, print_orientation), color)
        for region_name, (shape, color) in normalized.items()
    }
    print_body = _apply_print_orientation(assembly_body, print_orientation)
    print_body_record = _shape_record(print_body)
    if not print_body_record["valid"]:
        raise RegionInvariantError("print manufacturing geometry is invalid")

    entries = []
    artifacts = {}
    internal_region_dir = output / ".amagine3d-internal" / name
    semantic_region_dir = internal_region_dir / "semantic"
    internal_region_dir.mkdir(parents=True, exist_ok=True)
    semantic_region_dir.mkdir(parents=True, exist_ok=True)
    internal_region_meshes = {"print": {}, "semantic": {}}
    for region_name, (shape, color) in print_regions.items():
        path = internal_region_dir / f"{name}-region-{region_name}.stl"
        export_stl(shape, str(path), tolerance=0.01, angular_tolerance=0.1)
        internal_region_meshes["print"][region_name] = {
            "path": str(path.resolve()),
            "sha256": _digest(path),
        }
        entries.append((str(path), color, region_name))
    for region_name, (shape, _) in normalized.items():
        path = semantic_region_dir / f"{name}-region-{region_name}.stl"
        export_stl(shape, str(path), tolerance=0.01, angular_tolerance=0.1)
        internal_region_meshes["semantic"][region_name] = {
            "path": str(path.resolve()),
            "sha256": _digest(path),
        }

    manufacturing_path = output / f"{name}.stl"
    export_stl(
        print_body,
        str(manufacturing_path),
        tolerance=0.01,
        angular_tolerance=0.1,
    )
    artifacts["stl"] = {
        "path": str(manufacturing_path.resolve()),
        "sha256": _digest(manufacturing_path),
    }
    report["semantic"] = {"shape": assembly_record}
    report["manufacturing"] = {
        **print_body_record,
        "transform": {
            "from": "semantic",
            "rotate_degrees_xyz": print_orientation["selected"]["rotate_degrees_xyz"],
            "scale": print_orientation["selected"].get("scale_to_apply", 1.0),
            "to": "print",
            "translate_mm": print_orientation["selected"]["translate_mm"],
        },
    }
    report["print_orientation"] = print_orientation
    report["internal_region_meshes"] = internal_region_meshes

    archive_path = output / f"{name}.3mf"
    report["three_mf"] = write_color_archive(
        entries,
        str(archive_path),
        package_mode=package_mode,
        package_name=name,
    )
    artifacts["3mf"] = {
        "path": str(archive_path.resolve()),
        "sha256": _digest(archive_path),
    }

    children = []
    for region_name, (shape, color) in normalized.items():
        shape.color = Color(*_rgb(color))
        shape.label = region_name
        children.append(shape)
    assembly_shape = Compound(children=children)
    report["assembly"] = {"shape": _shape_record(assembly_shape)}
    assemble_step_path = output / f"{name}-assemble.step"
    display_glb_path = output / f"{name}-display.glb"
    export_step(assembly_shape, str(assemble_step_path), unit=Unit.MM)
    _export_display_glb(
        [
            (region_name, shape, color)
            for region_name, (shape, color) in normalized.items()
        ],
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

    material_plan_path = output / f"{name}_material-plan.json"
    material_plan = {
        "archive_encodes": ["region_name", "rgb"],
        "archive_omits": ["filament", "transmission"],
        "part": name,
        "regions": material_regions,
        "requires_manual_slicer_assignment": any(
            item["filament"]
            for item in material_regions
        ),
        "schema": "evidence-color-material-plan/v1",
    }
    material_plan_path.write_text(
        json.dumps(material_plan, indent=2) + "\n", encoding="utf-8"
    )
    artifacts["material_plan"] = {
        "path": str(material_plan_path.resolve()),
        "sha256": _digest(material_plan_path),
    }
    report["material_semantics"] = material_plan

    source = Path(source_path or sys.argv[0]).resolve()
    report["artifacts"] = artifacts
    report["source"] = (
        {"path": str(source), "sha256": _digest(source)} if source.is_file() else None
    )
    report["intent"] = {"path": str(intent), "sha256": _digest(intent)}
    report_path = output / f"{name}_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


safe_cut = checked_cut
safe_fillet = checked_fillet
safe_chamfer = checked_chamfer
measure = observe
finalize_parts = export_regions
