"""Opt-in geometric checks for an installed component and its authored path.

No board sizes, mounts, fits or construction methods are selected here. Inputs
share semantic assembly coordinates; purchased components are never exported as
manufactured parts. Contact probes establish geometry, not force or strength.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import trimesh


class InstallationCheckError(ValueError):
    def __init__(self, report: dict):
        self.report = report
        failed = [item["id"] for item in report["checks"] if not item["pass"]]
        super().__init__("Installation checks failed: " + ", ".join(failed))


def _valid(shape: Any, label: str) -> Any:
    if isinstance(shape, trimesh.Trimesh):
        if not shape.is_volume or not np.isfinite(shape.vertices).all():
            raise ValueError(f"{label} must be a finite, closed positive volume")
    else:
        valid = shape.is_valid
        if not (valid() if callable(valid) else valid) or not shape.solids() or shape.volume <= 0:
            raise ValueError(f"{label} must be a valid BRep solid or compound")
    return shape


def _mesh(shape: Any) -> trimesh.Trimesh:
    if isinstance(shape, trimesh.Trimesh):
        return shape
    from geometry_binding import shape_to_mesh
    return shape_to_mesh(shape, "installation witness")


def _overlap(left: Any, right: Any) -> float:
    if isinstance(left, trimesh.Trimesh) or isinstance(right, trimesh.Trimesh):
        intersection = trimesh.boolean.intersection([_mesh(left), _mesh(right)], engine="manifold")
    else:
        intersection = left & right
    value = 0.0 if intersection is None else float(intersection.volume)
    if not math.isfinite(value) or value < -1e-8:
        raise ValueError("installation intersection did not produce a finite positive volume")
    return max(0.0, value)


def _moved(shape: Any, displacement: np.ndarray) -> Any:
    if isinstance(shape, trimesh.Trimesh):
        result = shape.copy()
        result.apply_translation(displacement)
        return result
    from build123d import Pos
    return Pos(*displacement.tolist()) * shape


def check_installation(
    envelope: Any,
    obstacles: Mapping[str, Any],
    *,
    insertion_envelope: Any | None = None,
    supports: Mapping[str, Any] | None = None,
    retainers: Mapping[str, Any] | None = None,
    withdrawal_axis: tuple[float, float, float] = (0, 0, 1),
    contact_probe_mm: float = 0.05,
    free_travel_mm: float | None = None,
    stop_travel_mm: float | None = None,
    max_overlap_mm3: float = 0.01,
    out_path: str | Path | None = None,
) -> dict:
    """Check clearance, an optional authored swept envelope, support and stops.

    Supports must contact after a small move opposite withdrawal_axis. Retainers
    must leave free_travel_mm clear and contact at stop_travel_mm. Each named
    support/retainer is checked; group geometry when any contact in a group is
    sufficient. Omitted checks are not claimed. Write evidence before raising
    InstallationCheckError on a geometric failure. BRep and closed meshes work.
    """
    axis = np.asarray(withdrawal_axis, dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or np.linalg.norm(axis) <= 1e-12:
        raise ValueError("withdrawal_axis must be a finite nonzero vector")
    axis = axis / np.linalg.norm(axis)
    for name, value in (("contact_probe_mm", contact_probe_mm), ("max_overlap_mm3", max_overlap_mm3)):
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if retainers:
        values = (free_travel_mm, stop_travel_mm)
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
            raise ValueError("retainers require explicit finite free_travel_mm and stop_travel_mm")
        if not 0 <= free_travel_mm < stop_travel_mm:
            raise ValueError("retention requires 0 <= free_travel_mm < stop_travel_mm")
    _valid(envelope, "component envelope")
    if insertion_envelope is not None:
        _valid(insertion_envelope, "insertion envelope")
    groups = {"obstacles": obstacles, "supports": supports or {}, "retainers": retainers or {}}
    if not any(groups.values()):
        raise ValueError("at least one obstacle, support or retainer is required")
    for group, parts in groups.items():
        if not isinstance(parts, Mapping):
            raise ValueError(f"{group} must map names to physical geometry")
        for name, shape in parts.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{group} requires nonempty part names")
            _valid(shape, f"{group}.{name}")
    checks = []

    def measure(name, left, right, contact=False):
        amount = _overlap(left, right)
        passed = amount > max_overlap_mm3 if contact else amount <= max_overlap_mm3
        checks.append({
            "id": name, "observed": {"overlapMm3": amount},
            "expected": {"contact": contact, "thresholdMm3": max_overlap_mm3},
            "pass": passed,
        })

    for group, parts in groups.items():
        for name, shape in parts.items():
            measure(f"clearance:{group}:{name}", envelope, shape)
    for name, obstacle in obstacles.items():
        if insertion_envelope is not None:
            measure(f"insertion:{name}", insertion_envelope, obstacle)
    for name, support in (supports or {}).items():
        measure(f"support:{name}", _moved(envelope, -axis * contact_probe_mm), support, True)
    for name, retainer in (retainers or {}).items():
        measure(f"free-travel:{name}", _moved(envelope, axis * free_travel_mm), retainer)
        measure(f"stop:{name}", _moved(envelope, axis * stop_travel_mm), retainer, True)
    report = {
        "schema": "evidence-installation-check/v1", "coordinateFrame": "semantic",
        "pass": all(item["pass"] for item in checks), "checks": checks,
        "withdrawalAxis": axis.tolist(),
        "probes": {"contactMm": contact_probe_mm, "freeTravelMm": free_travel_mm, "stopTravelMm": stop_travel_mm},
        "scope": {"insertion": insertion_envelope is not None, "support": bool(supports), "retention": bool(retainers)},
        "limitations": "Authored component/path envelopes and contact probes; no force, deformation or electrical-function proof.",
    }
    if out_path is not None:
        Path(out_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["pass"]:
        raise InstallationCheckError(report)
    return report
