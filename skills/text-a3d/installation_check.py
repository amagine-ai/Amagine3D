"""Opt-in geometric checks for an installed component and its authored path.

No board sizes, mounts, fits or construction methods are selected here. Inputs
share semantic assembly coordinates; purchased components are never exported as
manufactured parts. Contact probes establish geometry, not force or strength.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from hashlib import sha256
from typing import Any, Mapping

import numpy as np
import trimesh


def bind_installation_check(
    *, feature_id: str, envelope: Any, out_dir: str | Path,
    obstacle_parts=(), support_parts=(), retainer_parts=(),
    insertion_envelope=None, passage_envelope=None, passage_parts=(),
    withdrawal_axis=(0, 0, 1), support_direction=None, contact_probe_mm=0.05,
    free_travel_mm=None, stop_travel_mm=None, max_overlap_mm3=0.01,
) -> dict:
    """Bind installation witnesses for independent compile checks of final parts.

    Declare the required aspects in the owning intent feature's installation_checks.
    Part names identify final manufactured geometry, never substitute support shapes.
    obstacle_parts are present during insertion; retainers may be installed later.
    passage_envelope is the required unobstructed volume, e.g. an optical or plug path.
    """
    import re
    from geometry_binding import shape_to_mesh, _write_stl

    if not isinstance(feature_id, str) or not re.fullmatch(r"[a-z][a-z0-9_/-]*", feature_id):
        raise ValueError("feature_id must be a semantic feature ID")
    root = Path(out_dir).resolve() / sha256(feature_id.encode()).hexdigest()[:16]

    def bound(shape, name):
        _valid(shape, name)
        mesh = shape if isinstance(shape, trimesh.Trimesh) else shape_to_mesh(shape, name)
        path = root / f"{name}.stl"
        digest = _write_stl(mesh, path)
        return {"path": str(path), "sha256": digest, "scale": 1.0}

    record = {
        "featureId": feature_id, "envelope": bound(envelope, "envelope"),
        "obstacleParts": list(obstacle_parts), "supportParts": list(support_parts),
        "retainerParts": list(retainer_parts), "passageParts": list(passage_parts),
        "withdrawalAxis": list(withdrawal_axis), "contactProbeMm": contact_probe_mm,
        "maxOverlapMm3": max_overlap_mm3,
    }
    if insertion_envelope is not None:
        record["insertionEnvelope"] = bound(insertion_envelope, "insertion")
    if support_direction is not None:
        record["supportDirection"] = list(support_direction)
    if passage_envelope is not None:
        record["passageEnvelope"] = bound(passage_envelope, "passage")
    if free_travel_mm is not None:
        record["freeTravelMm"] = free_travel_mm
    if stop_travel_mm is not None:
        record["stopTravelMm"] = stop_travel_mm
    return record


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
    # Empty or merely touching boolean results have zero volume. Trimesh also
    # computes an undefined center of mass; only the finite volume is used here.
    with np.errstate(divide="ignore", invalid="ignore"):
        value = 0.0 if intersection is None or (
            isinstance(intersection, trimesh.Trimesh) and intersection.is_empty
        ) else float(intersection.volume)
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


def _difference_volume(left: Any, right: Any) -> float:
    if isinstance(left, trimesh.Trimesh) or isinstance(right, trimesh.Trimesh):
        difference = trimesh.boolean.difference([_mesh(left), _mesh(right)], engine="manifold")
    else:
        difference = left - right
    with np.errstate(divide="ignore", invalid="ignore"):
        value = 0.0 if difference is None or (
            isinstance(difference, trimesh.Trimesh) and difference.is_empty
        ) else float(difference.volume)
    if not math.isfinite(value) or value < -1e-8:
        raise ValueError("installation difference did not produce a finite positive volume")
    return max(0.0, value)


def _swept_envelope(shape: Any, displacement: np.ndarray) -> Any:
    """Continuous linear sweep of the tessellated volume, preserving concavity.

    A translated solid occupies its original volume plus the prisms swept by
    every forward-facing boundary triangle. Union those prisms individually;
    taking a convex hull of the whole component would invent material in slots.
    """
    if not np.any(displacement):
        return shape
    mesh = _mesh(shape)
    forward = mesh.face_normals @ displacement > 0.0
    prism_faces = np.asarray([
        [0, 2, 1], [3, 4, 5],
        [0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4], [2, 0, 3], [2, 3, 5],
    ])
    pieces = [mesh]
    for triangle in mesh.triangles[forward]:
        pieces.append(trimesh.Trimesh(
            vertices=np.concatenate((triangle, triangle + displacement)),
            faces=prism_faces,
            process=False,
        ))
    swept = trimesh.boolean.union(pieces, engine="manifold")
    return _valid(swept, "continuous free-travel envelope")


def check_installation(
    envelope: Any,
    obstacles: Mapping[str, Any],
    *,
    insertion_envelope: Any | None = None,
    supports: Mapping[str, Any] | None = None,
    retainers: Mapping[str, Any] | None = None,
    withdrawal_axis: tuple[float, float, float] = (0, 0, 1),
    support_direction: tuple[float, float, float] | None = None,
    contact_probe_mm: float = 0.05,
    free_travel_mm: float | None = None,
    stop_travel_mm: float | None = None,
    max_overlap_mm3: float = 0.01,
    out_path: str | Path | None = None,
) -> dict:
    """Check clearance, an optional authored swept envelope, support and stops.

    Supports must contact after a small move in support_direction (default:
    opposite withdrawal_axis). Retainers must leave the entire linear
    free_travel_mm sweep clear and contact at stop_travel_mm. Each named
    support/retainer is checked; group geometry when any contact in a group is
    sufficient. Omitted checks are not claimed. Write evidence before raising
    InstallationCheckError on a geometric failure. BRep and closed meshes work.
    """
    axis = np.asarray(withdrawal_axis, dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or np.linalg.norm(axis) <= 1e-12:
        raise ValueError("withdrawal_axis must be a finite nonzero vector")
    axis = axis / np.linalg.norm(axis)
    support_axis = -axis if support_direction is None else np.asarray(support_direction, dtype=float)
    if support_axis.shape != (3,) or not np.isfinite(support_axis).all() or np.linalg.norm(support_axis) <= 1e-12:
        raise ValueError("support_direction must be a finite nonzero vector")
    support_axis = support_axis / np.linalg.norm(support_axis)
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
    if insertion_envelope is not None:
        outside = _difference_volume(envelope, insertion_envelope)
        checks.append({
            "id": "insertion:coverage", "observed": {"uncoveredMm3": outside},
            "expected": {"maximumUncoveredMm3": max_overlap_mm3},
            "pass": outside <= max_overlap_mm3,
        })
    for name, obstacle in obstacles.items():
        if insertion_envelope is not None:
            measure(f"insertion:{name}", insertion_envelope, obstacle)
    for name, support in (supports or {}).items():
        measure(f"support:{name}", _moved(envelope, support_axis * contact_probe_mm), support, True)
    free_sweep = _swept_envelope(envelope, axis * free_travel_mm) if retainers else None
    for name, retainer in (retainers or {}).items():
        measure(f"free-travel:{name}", free_sweep, retainer)
        measure(f"stop:{name}", _moved(envelope, axis * stop_travel_mm), retainer, True)
    report = {
        "schema": "evidence-installation-check/v1", "coordinateFrame": "semantic",
        "pass": all(item["pass"] for item in checks), "checks": checks,
        "withdrawalAxis": axis.tolist(),
        "supportDirection": support_axis.tolist(),
        "probes": {"contactMm": contact_probe_mm, "freeTravelMm": free_travel_mm, "stopTravelMm": stop_travel_mm},
        "scope": {"insertion": insertion_envelope is not None, "support": bool(supports), "retention": bool(retainers)},
        "limitations": "Authored component/path envelopes; continuous free-travel uses tessellated linear sweeps; support and stop use contact probes. No force, deformation or electrical-function proof.",
    }
    if out_path is not None:
        Path(out_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["pass"]:
        raise InstallationCheckError(report)
    return report
