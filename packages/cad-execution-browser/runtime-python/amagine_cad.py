"""Amagine3D geometry operations and artifact publishers.

Generated build123d programs use this small host API instead of writing files
directly. The module records deterministic diagnostics, exports controlled
artifacts, and emits one machine-readable build report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from build123d import Color, Compound, Unit, chamfer, export_step, export_stl, fillet


_ISSUES: list[str] = []
_OBSERVATIONS: dict[str, dict] = {}
_OBSERVED_SHAPES: dict[str, object] = {}
_SCALES = (1.0, 0.7, 0.45, 0.25)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _name(value: str, role: str) -> str:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{role} must be a machine-safe identifier")
    return value


def _volume(shape) -> float:
    return float(shape.volume)


def _bounds(shape) -> dict:
    box = shape.bounding_box()
    low = [float(box.min.X), float(box.min.Y), float(box.min.Z)]
    high = [float(box.max.X), float(box.max.Y), float(box.max.Z)]
    return {
        "size_x": round(high[0] - low[0], 3),
        "size_y": round(high[1] - low[1], 3),
        "size_z": round(high[2] - low[2], 3),
        "min": [round(item, 3) for item in low],
        "max": [round(item, 3) for item in high],
    }


def _shape_summary(shape, label: str, issues: list[str]) -> dict:
    summary: dict = {}
    try:
        validity = shape.is_valid
        summary["is_valid"] = bool(validity() if callable(validity) else validity)
    except Exception as exc:
        summary["is_valid"] = None
        issues.append(f"SHAPE_VALIDITY_UNKNOWN [{label}] {exc}")
    try:
        summary["solid_count"] = len(shape.solids())
    except Exception as exc:
        summary["solid_count"] = None
        issues.append(f"SOLID_COUNT_UNKNOWN [{label}] {exc}")
    try:
        summary["bbox"] = _bounds(shape)
    except Exception as exc:
        issues.append(f"BOUNDS_UNAVAILABLE [{label}] {exc}")
    try:
        summary["volume_mm3"] = round(_volume(shape), 3)
    except Exception as exc:
        issues.append(f"VOLUME_UNAVAILABLE [{label}] {exc}")
    return summary


def subtract_checked(body, cutter, label: str = "cut"):
    """Subtract a cutter and record a diagnostic when it misses or fails."""
    try:
        before = _volume(body)
        result = body - cutter
        removed = before - _volume(result)
        if removed <= 0.001:
            _ISSUES.append(f"BOOLEAN_NO_EFFECT [{label}] removed {removed:.6f} mm3")
        return result
    except Exception as exc:
        _ISSUES.append(f"BOOLEAN_FAILED [{label}] {exc}")
        return body


def _finish_checked(kind: str, operation, shape, edge_source, requested: float, label: str):
    if requested <= 0:
        _ISSUES.append(f"{kind}_FAILED [{label}] size must be positive")
        return shape
    try:
        selected = list(edge_source(shape) if callable(edge_source) else edge_source)
    except Exception as exc:
        _ISSUES.append(f"{kind}_FAILED [{label}] selector error: {exc}")
        return shape
    if not selected:
        _ISSUES.append(f"{kind}_FAILED [{label}] selector returned no edges")
        return shape

    baseline = _volume(shape)
    errors: list[str] = []
    for factor in _SCALES:
        size = requested * factor
        try:
            candidate = operation(selected, size)
            if abs(_volume(candidate) - baseline) <= 0.001:
                errors.append(f"{size:g} produced no volume change")
                continue
            if factor != 1.0:
                _ISSUES.append(
                    f"{kind}_REDUCED [{label}] requested {requested:g}, used {size:g}"
                )
            return candidate
        except Exception as exc:
            errors.append(f"{size:g}: {exc}")

    best_candidate = None
    best_delta = 0.0
    best_size = None
    for edge in selected:
        for factor in _SCALES:
            size = requested * factor
            try:
                candidate = operation([edge], size)
                delta = abs(_volume(candidate) - baseline)
                if delta <= 0.001:
                    continue
                if delta > best_delta:
                    best_candidate = candidate
                    best_delta = delta
                    best_size = size
                break
            except Exception:
                continue
    if best_candidate is not None:
        if best_size != requested:
            _ISSUES.append(
                f"{kind}_REDUCED [{label}] requested {requested:g}, used {best_size:g}"
            )
        _ISSUES.append(
            f"{kind}_PARTIAL [{label}] completed 1 of {len(selected)} selected edges"
        )
        return best_candidate
    detail = errors[-1] if errors else "operation produced no usable result"
    _ISSUES.append(f"{kind}_FAILED [{label}] {detail}")
    return shape


def round_edges_checked(shape, edges, radius: float, label: str = "round"):
    return _finish_checked(
        "ROUND", lambda selected, size: fillet(selected, radius=size),
        shape, edges, radius, label,
    )


def bevel_edges_checked(shape, edges, length: float, label: str = "bevel"):
    return _finish_checked(
        "BEVEL", lambda selected, size: chamfer(selected, length=size),
        shape, edges, length, label,
    )


def observe_feature(shape, feature_id: str, feature_type: str = "feature") -> None:
    """Capture bounds and volume while a feature still has its own identity."""
    feature_id = _name(feature_id, "feature_id")
    try:
        item = _bounds(shape)
        item["type"] = feature_type
        item["volume"] = round(_volume(shape), 3)
        _OBSERVATIONS[feature_id] = item
        _OBSERVED_SHAPES[feature_id] = shape
    except Exception as exc:
        _OBSERVATIONS[feature_id] = {"type": feature_type, "error": str(exc)}


def _apply_keep_out_checks(published_shapes: dict[str, object], report: dict) -> None:
    keep_outs: dict[str, dict] = {}
    for feature_id, observation in _OBSERVATIONS.items():
        if observation.get("type") != "keep-out":
            continue
        shape = _OBSERVED_SHAPES.get(feature_id)
        overlaps = []
        errors = []
        if shape is None:
            errors.append(str(observation.get("error") or "shape unavailable"))
        else:
            for body_id, body in published_shapes.items():
                try:
                    volume = _volume(shape & body)
                    if volume > 0.0:
                        overlaps.append({
                            "bodyId": body_id,
                            "overlapVolumeMm3": round(volume, 6),
                        })
                except Exception as exc:
                    errors.append(f"{body_id}: {exc}")
        maximum = max(
            (item["overlapVolumeMm3"] for item in overlaps), default=0.0
        )
        known = not errors
        keep_outs[feature_id] = {
            "known": known,
            "max_overlap_mm3": round(maximum, 6),
            "overlaps": overlaps,
            "errors": errors,
        }
        if errors:
            report["issues"].append(
                f"KEEP_OUT_UNKNOWN [{feature_id}] {'; '.join(errors)}"
            )
        if maximum > 0.01:
            report["issues"].append(
                f"KEEP_OUT_COLLISION [{feature_id}] {maximum:.6f} mm3"
            )
    if keep_outs:
        report["keep_outs"] = keep_outs


def _assembly_summary(items: dict[str, dict]) -> tuple[dict | None, float]:
    boxes = [item["bbox"] for item in items.values() if "bbox" in item]
    if not boxes:
        return None, 0.0
    low = [min(box["min"][axis] for box in boxes) for axis in range(3)]
    high = [max(box["max"][axis] for box in boxes) for axis in range(3)]
    bounds = {
        "size_x": round(high[0] - low[0], 3),
        "size_y": round(high[1] - low[1], 3),
        "size_z": round(high[2] - low[2], 3),
        "min": [round(value, 3) for value in low],
        "max": [round(value, 3) for value in high],
    }
    volume = round(sum(float(item.get("volume_mm3") or 0) for item in items.values()), 3)
    return bounds, volume


def _write_report(output: Path, model_id: str, report: dict) -> dict:
    report["observations"] = dict(_OBSERVATIONS)
    path = output / f"{model_id}.amagine.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return report


def publish_model(model, model_id: str, out_dir: str = "cad_out") -> dict:
    """Publish one body or a mapping of separately printable bodies."""
    model_id = _name(model_id, "model_id")
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    report: dict = {"model_id": model_id, "issues": list(_ISSUES)}

    if isinstance(model, dict):
        if not model:
            raise ValueError("publish_model requires at least one body")
        parts: dict[str, dict] = {}
        body_shapes: dict[str, object] = {}
        for body_id, shape in model.items():
            body_id = _name(body_id, "body_id")
            body_shapes[body_id] = shape
            item = _shape_summary(shape, body_id, report["issues"])
            step_path = output / f"{model_id}-{body_id}.step"
            stl_path = output / f"{model_id}-{body_id}.stl"
            export_step(shape, str(step_path), unit=Unit.MM)
            export_stl(shape, str(stl_path), tolerance=0.01, angular_tolerance=0.1)
            item.update({"step_file": str(step_path.resolve()), "stl_file": str(stl_path.resolve())})
            parts[body_id] = item
        report["parts"] = parts
        report["overlaps_mm3"] = {}
        body_ids = list(body_shapes)
        for left_index, left_id in enumerate(body_ids):
            for right_id in body_ids[left_index + 1:]:
                try:
                    overlap = _volume(body_shapes[left_id] & body_shapes[right_id])
                except Exception as exc:
                    overlap = 0.0
                    report["issues"].append(
                        f"OVERLAP_UNKNOWN [{left_id}/{right_id}] {exc}"
                    )
                report["overlaps_mm3"][f"{left_id}&{right_id}"] = round(overlap, 4)
                if overlap > 0.01:
                    report["issues"].append(
                        f"BODY_OVERLAP [{left_id}/{right_id}] {overlap:.4f} mm3"
                    )
        _apply_keep_out_checks(body_shapes, report)
        bounds, volume = _assembly_summary(parts)
        if bounds is not None:
            report["assembly_bbox"] = bounds
        report["assembly_volume_mm3"] = volume
        return _write_report(output, model_id, report)

    report.update(_shape_summary(model, model_id, report["issues"]))
    step_path = output / f"{model_id}.step"
    stl_path = output / f"{model_id}.stl"
    export_step(model, str(step_path), unit=Unit.MM)
    export_stl(model, str(stl_path), tolerance=0.01, angular_tolerance=0.1)
    report.update({"step_file": str(step_path.resolve()), "stl_file": str(stl_path.resolve())})
    _apply_keep_out_checks({model_id: model}, report)
    return _write_report(output, model_id, report)


def _rgb(color: str) -> tuple[int, int, int]:
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError(f"invalid color {color!r}; expected #RRGGBB")
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def publish_color_model(regions: dict, model_id: str, out_dir: str = "cad_out") -> dict:
    """Publish geometry-backed color regions as STL, 3MF and STEP."""
    model_id = _name(model_id, "model_id")
    if len(regions) < 2:
        raise ValueError("publish_color_model requires at least two regions")
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "model_id": model_id,
        "issues": list(_ISSUES),
        "parts": {},
        "overlaps_mm3": {},
    }
    encoded_regions: list[tuple[str, str, str]] = []
    checked: dict[str, tuple[object, str]] = {}
    for region_id, value in regions.items():
        region_id = _name(region_id, "region_id")
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValueError(f"region {region_id} must be a (shape, color) tuple")
        shape, color = value
        _rgb(color)
        checked[region_id] = (shape, color)
        item = _shape_summary(shape, region_id, report["issues"])
        item["color"] = color.lower()
        region_step_path = output / f"{model_id}-{region_id}.step"
        stl_path = output / f"{model_id}-{region_id}.stl"
        export_step(shape, str(region_step_path), unit=Unit.MM)
        export_stl(shape, str(stl_path), tolerance=0.01, angular_tolerance=0.1)
        item["step_file"] = str(region_step_path.resolve())
        item["stl_file"] = str(stl_path.resolve())
        report["parts"][region_id] = item
        encoded_regions.append((str(stl_path), color, region_id))

    region_ids = list(checked)
    for left_index, left_id in enumerate(region_ids):
        for right_id in region_ids[left_index + 1:]:
            try:
                overlap = _volume(checked[left_id][0] & checked[right_id][0])
            except Exception as exc:
                overlap = 0.0
                report["issues"].append(f"OVERLAP_UNKNOWN [{left_id}/{right_id}] {exc}")
            report["overlaps_mm3"][f"{left_id}&{right_id}"] = round(overlap, 4)
            if overlap > 0.01:
                report["issues"].append(
                    f"COLOR_OVERLAP [{left_id}/{right_id}] {overlap:.4f} mm3"
                )

    _apply_keep_out_checks(
        {region_id: shape for region_id, (shape, _color) in checked.items()},
        report,
    )

    bounds, volume = _assembly_summary(report["parts"])
    if bounds is not None:
        report["assembly_bbox"] = bounds
    report["assembly_volume_mm3"] = volume

    try:
        from amagine_three_mf import write_colored_3mf
        three_mf_path = output / f"{model_id}.3mf"
        write_colored_3mf(encoded_regions, str(three_mf_path))
        report["threemf_file"] = str(three_mf_path.resolve())
    except Exception as exc:
        report["issues"].append(f"THREE_MF_FAILED {exc}")

    try:
        children = []
        for region_id, (shape, color) in checked.items():
            red, green, blue = _rgb(color)
            shape.color = Color(red / 255, green / 255, blue / 255)
            shape.label = region_id
            children.append(shape)
        step_path = output / f"{model_id}.step"
        export_step(Compound(children=children), str(step_path), unit=Unit.MM)
        report["step_file"] = str(step_path.resolve())
    except Exception as exc:
        report["issues"].append(f"COLORED_STEP_SKIPPED {exc}")
    return _write_report(output, model_id, report)
