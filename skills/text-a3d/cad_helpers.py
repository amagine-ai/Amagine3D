"""Fail-closed build runtime for evidence-driven single-material CAD.

Generated part scripts use this module to make failed booleans and silent
finish degradation observable. Exports carry hashes that tie geometry back to
the source and intent contract used in the current run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Callable, Iterable

from build123d import Unit, chamfer, export_step, export_stl, fillet


class BuildInvariantError(RuntimeError):
    """Raised when a requested modeling operation did not actually happen."""


_EVENTS: list[dict] = []
_FEATURES: dict[str, dict] = {}


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


def observe(shape, feature_id: str, role: str = "feature") -> None:
    """Capture evidence before a feature disappears into a boolean result."""
    if feature_id in _FEATURES:
        raise BuildInvariantError(f"duplicate feature id: {feature_id}")
    _FEATURES[feature_id] = {"role": role, **_stats(shape)}


def checked_cut(body, tool, feature_id: str, min_removed_mm3: float = 0.001):
    """Subtract a tool and fail if it misses or produces an invalid result."""
    before = float(body.volume)
    try:
        result = body - tool
    except Exception as error:
        raise BuildInvariantError(f"cut {feature_id!r} failed: {error}") from error
    removed = before - float(result.volume)
    _EVENTS.append({
        "id": feature_id,
        "kind": "cut",
        "removed_mm3": round(removed, 6),
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
):
    return _finish(shape, selector, radius_mm, feature_id, "fillet", allow_reduce)


def checked_chamfer(
    shape,
    selector: Iterable | Callable,
    length_mm: float,
    feature_id: str,
    *,
    allow_reduce: bool = False,
):
    return _finish(shape, selector, length_mm, feature_id, "chamfer", allow_reduce)


def export_part(
    shape,
    name: str,
    out_dir: str = ".",
    *,
    intent_path: str | None = None,
    source_path: str | None = None,
) -> dict:
    """Export STEP/STL plus a provenance-rich build record."""
    stats = _stats(shape)
    if not stats["valid"] or stats["solid_count"] != 1:
        raise BuildInvariantError(
            f"final shape must be one valid solid, got {stats['solid_count']}"
        )

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    step_path = output / f"{name}.step"
    stl_path = output / f"{name}.stl"
    report_path = output / f"{name}_report.json"
    export_step(shape, str(step_path), unit=Unit.MM)
    export_stl(shape, str(stl_path), tolerance=0.01, angular_tolerance=0.1)

    source = Path(source_path or sys.argv[0]).resolve()
    intent = Path(intent_path).resolve() if intent_path else None
    report = {
        "artifacts": {
            "step": {"path": str(step_path.resolve()), "sha256": _digest(step_path)},
            "stl": {"path": str(stl_path.resolve()), "sha256": _digest(stl_path)},
        },
        "built_at": datetime.now(timezone.utc).isoformat(),
        "events": list(_EVENTS),
        "features": dict(_FEATURES),
        "intent": (
            {"path": str(intent), "sha256": _digest(intent)}
            if intent and intent.is_file()
            else None
        ),
        "part": name,
        "schema": "evidence-cad-build/v2",
        "shape": stats,
        "source": (
            {"path": str(source), "sha256": _digest(source)}
            if source.is_file()
            else None
        ),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


# Old sources remain editable; new sources use the fail-closed names above.
safe_cut = checked_cut
safe_fillet = checked_fillet
safe_chamfer = checked_chamfer
measure = observe
finalize = export_part
