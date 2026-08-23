"""Strict region-assembly runtime for multi-color printable CAD."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

from build123d import Color, Compound, Unit, chamfer, export_step, export_stl, fillet

from export_3mf import write_color_archive


class RegionInvariantError(RuntimeError):
    pass


_FEATURES: dict[str, dict] = {}
_OPERATIONS: list[dict] = []
_REGION_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _valid(shape) -> bool:
    value = shape.is_valid
    return bool(value() if callable(value) else value)


def _shape_record(shape) -> dict:
    bounds = shape.bounding_box()
    return {
        "bounds_mm": {
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
    try:
        result = body - tool
    except Exception as error:
        raise RegionInvariantError(f"cut {feature_id!r} failed: {error}") from error
    removed = before - float(result.volume)
    _OPERATIONS.append({"id": feature_id, "kind": "cut", "removed_mm3": removed})
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
    _OPERATIONS.append({
        "id": feature_id, "kind": kind, "size_mm": round(size_mm, 6),
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
    parent=None,
    intent_path: str | None = None,
    source_path: str | None = None,
    max_coverage_error_mm3: float = 0.01,
) -> dict:
    """Validate region topology and export STLs, colored 3MF, STEP, and report."""
    if len(regions) < 2:
        raise RegionInvariantError("multi-color output requires at least two regions")

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "features": dict(_FEATURES),
        "operations": list(_OPERATIONS),
        "overlaps_mm3": {},
        "part": name,
        "regions": {},
        "schema": "evidence-color-build/v2",
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
    if parent is not None:
        if not _valid(parent):
            raise RegionInvariantError("coverage parent is invalid")
        region_union = region_shapes[0]
        for shape in region_shapes[1:]:
            region_union = region_union + shape
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

    source = Path(source_path or sys.argv[0]).resolve()
    intent = Path(intent_path).resolve() if intent_path else None
    report["artifacts"] = artifacts
    report["source"] = (
        {"path": str(source), "sha256": _digest(source)} if source.is_file() else None
    )
    report["intent"] = (
        {"path": str(intent), "sha256": _digest(intent)}
        if intent and intent.is_file()
        else None
    )
    report_path = output / f"{name}_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


safe_cut = checked_cut
safe_fillet = checked_fillet
safe_chamfer = checked_chamfer
measure = observe
finalize_parts = export_regions
