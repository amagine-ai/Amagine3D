"""Strict region-assembly runtime for multi-color printable CAD."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sys

from build123d import Color, Compound, Unit, chamfer, export_step, export_stl, fillet

from export_3mf import write_color_archive


class RegionInvariantError(RuntimeError):
    pass


_FEATURES: dict[str, dict] = {}
_EVENTS: list[dict] = []
_PARAMETERS: dict[str, dict] = {}
_REGION_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_-]*$")


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
    """Validate region topology and export STLs, colored 3MF, STEP, and report."""
    if len(regions) < 2:
        raise RegionInvariantError("multi-color output requires at least two regions")

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
        "events": list(_EVENTS),
        "features": dict(_FEATURES),
        "overlaps_mm3": {},
        "parameters": dict(_PARAMETERS),
        "part": name,
        "regions": {},
        "schema": "evidence-color-build/v3",
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
        if not isinstance(material, dict):
            raise RegionInvariantError(f"intent material for {region_name!r} is missing")
        transmission = material.get("transmission")
        if transmission not in {"opaque", "translucent", "transparent"}:
            raise RegionInvariantError(
                f"intent transmission for {region_name!r} is invalid"
            )
        filament = material.get("filament")
        if transmission != "opaque" and not (
            isinstance(filament, str) and filament.strip()
        ):
            raise RegionInvariantError(
                f"non-opaque region {region_name!r} requires a filament assignment"
            )
        material_regions.append({
            "color": color,
            "filament": filament,
            "name": region_name,
            "transmission": transmission,
        })

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
    if parent is not None:
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

    entries = []
    artifacts = {}
    for region_name, (shape, color) in normalized.items():
        path = output / f"{name}-{region_name}.stl"
        export_stl(shape, str(path), tolerance=0.01, angular_tolerance=0.1)
        entries.append((str(path), color, region_name))
        artifacts[f"stl:{region_name}"] = {
            "path": str(path.resolve()), "sha256": _digest(path),
        }

    combined = parent if parent is not None else region_union
    combined_record = _shape_record(combined)
    if not combined_record["valid"]:
        raise RegionInvariantError("combined manufacturing geometry is invalid")
    combined_path = output / f"{name}-combined.stl"
    export_stl(combined, str(combined_path), tolerance=0.01, angular_tolerance=0.1)
    artifacts["stl:combined"] = {
        "path": str(combined_path.resolve()), "sha256": _digest(combined_path),
    }
    report["combined"] = combined_record

    archive_path = output / f"{name}.3mf"
    report["three_mf"] = write_color_archive(entries, str(archive_path))
    artifacts["3mf"] = {"path": str(archive_path.resolve()), "sha256": _digest(archive_path)}

    children = []
    for region_name, (shape, color) in normalized.items():
        shape.color = Color(*_rgb(color))
        shape.label = region_name
        children.append(shape)
    step_path = output / f"{name}.step"
    export_step(Compound(children=children), str(step_path), unit=Unit.MM)
    artifacts["step"] = {"path": str(step_path.resolve()), "sha256": _digest(step_path)}

    material_plan_path = output / f"{name}_material-plan.json"
    material_plan = {
        "archive_encodes": ["region_name", "rgb"],
        "archive_omits": ["filament", "transmission"],
        "part": name,
        "regions": material_regions,
        "requires_manual_slicer_assignment": any(
            item["transmission"] != "opaque" or item["filament"]
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
