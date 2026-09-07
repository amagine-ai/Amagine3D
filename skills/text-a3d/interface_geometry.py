"""Contract-driven geometry evidence for multipart interfaces.

The checker consumes existing intent targets, scene endpoint declarations,
feature observations, and final physical-part meshes.  It never chooses a fit
or inserts product dimensions.  Unsupported or unavailable evidence is
reported explicitly while independent interfaces continue to be checked.
"""

from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from capability_registry import (
    GEOMETRY_TOLERANCE_MM,
    capability_for_connection,
)
from self_tapping_geometry import audit_self_tapping_geometry


INTERFACE_AUDIT_SCHEMA = "evidence-interface-geometry/v1"
AXES = {
    "+X": (0, 1.0),
    "-X": (0, -1.0),
    "+Y": (1, 1.0),
    "-Y": (1, -1.0),
    "+Z": (2, 1.0),
    "-Z": (2, -1.0),
}


def _artifact_path(report_dir: Path, reference: Any) -> Path:
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
        raise ValueError("artifact reference is missing a path")
    path = Path(reference["path"])
    return (path if path.is_absolute() else report_dir / path).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path, force="mesh", process=False)
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise ValueError(f"interface part artifact is not a mesh: {path}")
    loaded.merge_vertices()
    loaded.remove_unreferenced_vertices()
    if len(loaded.faces) == 0 or not np.isfinite(loaded.vertices).all():
        raise ValueError(f"interface part artifact has invalid triangles: {path}")
    return loaded


def _inverse_part_transform(report: dict[str, Any], part_id: str) -> np.ndarray:
    raw = (
        report.get("coordinateFrames", {})
        .get("part-print", {})
        .get("partTransforms", {})
        .get(part_id)
    )
    matrix = np.asarray(raw, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError(f"part {part_id!r} has no finite part-print transform")
    inverse = np.linalg.inv(matrix)
    if not np.isfinite(inverse).all():
        raise ValueError(f"part {part_id!r} part-print transform is singular")
    return inverse


def _part_meshes(report: dict[str, Any], report_dir: Path) -> dict[str, trimesh.Trimesh]:
    artifacts = report.get("artifacts")
    parts = report.get("parts")
    if not isinstance(artifacts, dict) or not isinstance(parts, dict):
        return {}
    meshes: dict[str, trimesh.Trimesh] = {}
    for part_id in parts:
        reference = artifacts.get(f"stl:{part_id}")
        if not isinstance(reference, dict):
            continue
        path = _artifact_path(report_dir, reference)
        if sha256(path.read_bytes()).hexdigest() != reference.get("sha256"):
            raise ValueError(f"part {part_id!r} STL hash does not match the report")
        mesh = _load_mesh(path)
        mesh.apply_transform(_inverse_part_transform(report, part_id))
        meshes[part_id] = mesh
    return meshes


def _bbox(record: Any) -> tuple[np.ndarray, np.ndarray] | None:
    raw = record.get("bbox_mm") if isinstance(record, dict) else None
    minimum = raw.get("min") if isinstance(raw, dict) else None
    maximum = raw.get("max") if isinstance(raw, dict) else None
    if not (
        isinstance(minimum, list)
        and isinstance(maximum, list)
        and len(minimum) == len(maximum) == 3
    ):
        return None
    low = np.asarray(minimum, dtype=float)
    high = np.asarray(maximum, dtype=float)
    if not np.isfinite(low).all() or not np.isfinite(high).all() or np.any(high < low):
        return None
    return low, high


def _wall_engagement(
    receiver: trimesh.Trimesh,
    female_bounds: tuple[np.ndarray, np.ndarray],
    male_bounds: tuple[np.ndarray, np.ndarray],
    axis: int,
) -> float:
    """Union the axial spans of actual inward-facing walls in the mating region.

    Cutter overshoot and empty gaps between receiver walls contribute no length.
    Clip triangles, rather than their boxes, so a long face cannot contribute
    material outside the local feature. End caps are not guiding walls.
    """
    low, high = (values.copy() for values in female_bounds)
    center = (low + high) / 2
    radial = [index for index in range(3) if index != axis]
    low[radial] -= GEOMETRY_TOLERANCE_MM
    high[radial] += GEOMETRY_TOLERANCE_MM
    low[axis] = max(low[axis], male_bounds[0][axis])
    high[axis] = min(high[axis], male_bounds[1][axis])
    if high[axis] <= low[axis]:
        return 0.0
    intervals = []
    for triangle, normal in zip(receiver.triangles, receiver.face_normals):
        if abs(normal[axis]) >= 0.99:
            continue
        outward = triangle.mean(axis=0) - center
        if np.dot(normal[radial], outward[radial]) >= -1e-9:
            continue
        if np.any(triangle.max(axis=0) < low) or np.any(triangle.min(axis=0) > high):
            continue
        polygon = list(triangle)
        for dimension in range(3):
            for limit, sign in ((low[dimension], 1), (high[dimension], -1)):
                clipped = []
                for first, second in zip(polygon, polygon[1:] + polygon[:1]):
                    a = sign * (first[dimension] - limit)
                    b = sign * (second[dimension] - limit)
                    if a >= 0:
                        clipped.append(first)
                    if (a >= 0) != (b >= 0):
                        clipped.append(first + a / (a - b) * (second - first))
                polygon = clipped
                if not polygon:
                    break
            if not polygon:
                break
        if len(polygon) >= 3:
            points = np.asarray(polygon)
            intervals.append((float(points[:, axis].min()), float(points[:, axis].max())))
    length = 0.0
    end = -math.inf
    for start, stop in sorted(intervals):
        length += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return length


def _points_in_bounds(
    mesh: trimesh.Trimesh,
    bounds: tuple[np.ndarray, np.ndarray],
    margin: float,
    maximum: int = 4096,
) -> np.ndarray:
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=int)
    low, high = bounds
    vertex_mask = np.logical_and(
        np.all(vertices >= low - margin, axis=1),
        np.all(vertices <= high + margin, axis=1),
    )
    local_vertices = vertices[vertex_mask]
    local_faces = faces[np.any(vertex_mask[faces], axis=1)]
    chunks = [local_vertices]
    if len(local_faces):
        chunks.append(vertices[local_faces].mean(axis=1))
    points = np.vstack([chunk for chunk in chunks if len(chunk)]) if any(
        len(chunk) for chunk in chunks
    ) else np.empty((0, 3), dtype=float)
    if len(points) <= maximum:
        return points
    indices = np.linspace(0, len(points) - 1, maximum, dtype=int)
    return points[indices]


def _surface_distance(
    left: trimesh.Trimesh,
    right: trimesh.Trimesh,
    left_bounds: tuple[np.ndarray, np.ndarray],
    right_bounds: tuple[np.ndarray, np.ndarray],
    *,
    margin: float,
) -> float:
    """Measure proximity only around the declared endpoint geometry.

    A global minimum can be zero because two parts touch somewhere unrelated
    to the interface.  Source and closest points are therefore both required
    to remain inside the paired endpoint bounds (with a contract-derived
    margin).
    """

    distances: list[np.ndarray] = []
    for target, source, source_bounds, target_bounds in (
        (left, right, right_bounds, left_bounds),
        (right, left, left_bounds, right_bounds),
    ):
        points = _points_in_bounds(source, source_bounds, margin)
        if not len(points):
            continue
        try:
            closest, values, _ = trimesh.proximity.closest_point(target, points)
        except (ImportError, ModuleNotFoundError):
            closest, values, _ = trimesh.proximity.closest_point_naive(target, points)
        closest = np.asarray(closest, dtype=float)
        values = np.asarray(values, dtype=float)
        if not np.isfinite(closest).all() or not np.isfinite(values).all():
            raise ValueError("surface distance returned non-finite values")
        target_low, target_high = target_bounds
        local = np.logical_and(
            np.all(closest >= target_low - margin, axis=1),
            np.all(closest <= target_high + margin, axis=1),
        )
        if local.any():
            distances.append(values[local])
    if not distances:
        raise ValueError("no physical surface samples were found near both endpoints")
    return float(min(np.min(values) for values in distances))


def _issue(
    *,
    code: str,
    interface_id: str,
    check: str,
    observed: Any,
    expected: Any,
    severity: str = "error",
    features: list[str] | None = None,
    offender_id: str | None = None,
    repair_hint: str,
) -> dict[str, Any]:
    return {
        "check": check,
        "code": code,
        "expected": expected,
        **({"features": features} if features else {}),
        "interfaceId": interface_id,
        **({"offenderId": offender_id} if offender_id else {}),
        "observed": observed,
        "repairHint": repair_hint,
        "severity": severity,
    }


def _self_tapping_proof(
    *,
    interface_id: str,
    implementation: dict[str, Any],
    features: list[str],
    feature_records: dict[str, Any],
    part_meshes: dict[str, trimesh.Trimesh],
    precomputed: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return one physical witness result for every declared screw axis."""

    fasteners = [
        item
        for item in implementation.get("fasteners", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    expected_keys = [f"{interface_id}/{item['id']}" for item in fasteners]
    if precomputed is None:
        proofs = audit_self_tapping_geometry(
            {"interfaces": [implementation]},
            part_meshes,
            feature_records=feature_records,
        )
    else:
        proofs = {
            key: precomputed.get(key)
            for key in expected_keys
        }

    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    if not expected_keys:
        issues.append(
            _issue(
                code="INTERFACE.EVIDENCE_MISSING",
                interface_id=interface_id,
                check="fastener-witness-volumes",
                features=features,
                observed={"fastenerIds": []},
                expected="at least one contract-bound screw axis",
                repair_hint="Declare each screw axis and its clearance, pilot, and receiver-boss features in the semantic scene.",
            )
        )
        return checks, issues

    for key in expected_keys:
        fastener_id = key.removeprefix(f"{interface_id}/")
        raw = proofs.get(key)
        proof = raw if isinstance(raw, dict) else {}
        proof_checks = proof.get("checks")
        proof_checks = proof_checks if isinstance(proof_checks, dict) else {}
        passed = (
            proof.get("pass") is True
            and bool(proof_checks)
            and all(value is True for value in proof_checks.values())
        )
        checks.append(
            {
                "check": "fastener-witness-volumes",
                "interfaceId": interface_id,
                "offenderId": fastener_id,
                "observed": proof,
                "expected": "all contract-derived screw witness volumes pass",
                "pass": passed,
                "proofCapability": "self-tapping-screw/v1",
                "status": "pass" if passed else "fail",
            }
        )
        if not passed:
            issues.append(
                _issue(
                    code="INTERFACE.FASTENER_GEOMETRY_FAILED",
                    interface_id=interface_id,
                    check="fastener-witness-volumes",
                    features=features,
                    offender_id=fastener_id,
                    observed=proof or {"evidence": "missing"},
                    expected="open clearance and pilot volumes plus complete boss wall, root embed, blind end, and any contract-declared head-recess floor",
                    repair_hint="Regenerate the named screw axis from its shared contract dimensions, then subtract both cutters and fuse the complete receiver boss into the owning parts.",
                )
            )
    return checks, issues


def _dimension_axis(field: str, axis_index: int) -> tuple[int, ...] | None:
    token = field.lower().replace("_mm", "")
    if token in {"diameter", "bore_diameter", "socket_diameter", "pin_diameter"}:
        return tuple(index for index in range(3) if index != axis_index)
    if token in {"width", "x"}:
        return (0,)
    if token in {"depth", "y"}:
        return (1,)
    if token in {"height", "z"}:
        return (2,)
    if token in {"length", "span"}:
        return (axis_index,)
    return None


def audit_interfaces(
    *,
    intent: dict[str, Any],
    scene: dict[str, Any],
    feature_records: dict[str, Any],
    part_meshes: dict[str, trimesh.Trimesh],
    self_tapping_proofs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manufacturing = intent.get("manufacturing")
    intent_items = manufacturing.get("interfaces") if isinstance(manufacturing, dict) else []
    scene_items = scene.get("interfaces")
    intent_by_id = {
        item.get("id"): item
        for item in intent_items or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    scene_by_id = {
        item.get("id"): item
        for item in scene_items or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    issues: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for interface_id in sorted(intent_by_id):
        target = intent_by_id[interface_id]
        implementation = scene_by_id.get(interface_id)
        connection = target.get("connection")
        capability = capability_for_connection(connection)
        features = [
            item for item in target.get("features", []) if isinstance(item, str)
        ]
        if not isinstance(implementation, dict) or capability is None:
            issues.append(
                _issue(
                    code="INTERFACE.EVIDENCE_MISSING",
                    interface_id=interface_id,
                    check="capability-evidence",
                    features=features,
                    observed={"sceneInterface": bool(implementation), "connection": connection},
                    expected="a supported scene interface with measurable evidence",
                    repair_hint="Declare both endpoint features in the semantic scene and use a supported generic interface recipe.",
                )
            )
            continue
        if capability["id"] == "self-tapping-screw/v1":
            proof_checks, proof_issues = _self_tapping_proof(
                interface_id=interface_id,
                implementation=implementation,
                features=features,
                feature_records=feature_records,
                part_meshes=part_meshes,
                precomputed=self_tapping_proofs,
            )
            checks.extend(proof_checks)
            issues.extend(proof_issues)
            continue
        axis = AXES.get(target.get("assembly_axis"))
        endpoints = {
            name: implementation.get(name)
            for name in ("male", "female")
            if isinstance(implementation.get(name), dict)
        }
        if axis is None or set(endpoints) != {"male", "female"}:
            issues.append(
                _issue(
                    code="INTERFACE.EVIDENCE_MISSING",
                    interface_id=interface_id,
                    check="endpoint-evidence",
                    features=features,
                    observed={"axis": target.get("assembly_axis"), "endpoints": sorted(endpoints)},
                    expected="one axis and male/female endpoints",
                    repair_hint="Bind the interface endpoints and assembly axis to the immutable intent.",
                )
            )
            continue
        axis_index, _direction = axis
        endpoint_bounds: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name, endpoint in endpoints.items():
            feature_id = endpoint.get("featureId")
            bounds = _bbox(feature_records.get(feature_id))
            if bounds is not None:
                endpoint_bounds[name] = bounds
        if set(endpoint_bounds) != {"male", "female"}:
            issues.append(
                _issue(
                    code="INTERFACE.EVIDENCE_MISSING",
                    interface_id=interface_id,
                    check="feature-bounds",
                    features=features,
                    observed={"boundedEndpoints": sorted(endpoint_bounds)},
                    expected=["male", "female"],
                    repair_hint="Observe the male solid and female cutter before boolean operations so their semantic bounds remain measurable.",
                )
            )
            continue

        male_low, male_high = endpoint_bounds["male"]
        female_low, female_high = endpoint_bounds["female"]
        male_size = male_high - male_low
        female_size = female_high - female_low
        geometry_checks = set(capability.get("geometryChecks", ()))
        declared_dimensions: dict[str, dict[str, float]] = {}
        for name, endpoint in endpoints.items():
            raw = endpoint.get("dimensionsMm")
            declared_dimensions[name] = (
                {
                    key: float(value)
                    for key, value in raw.items()
                    if isinstance(key, str)
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                }
                if isinstance(raw, dict)
                else {}
            )

        measured_dimension_count = 0
        if "declared-dimensions" in geometry_checks:
            for endpoint_name, size in (
                ("male", male_size),
                ("female", female_size),
            ):
                for field, declared in sorted(
                    declared_dimensions[endpoint_name].items()
                ):
                    axes = _dimension_axis(field, axis_index)
                    if axes is None:
                        continue
                    measured_dimension_count += 1
                    observed_values = [float(size[index]) for index in axes]
                    passed = all(
                        abs(value - declared) <= GEOMETRY_TOLERANCE_MM
                        for value in observed_values
                    )
                    checks.append(
                        {
                            "check": "declared-dimension",
                            "endpoint": endpoint_name,
                            "field": field,
                            "interfaceId": interface_id,
                            "observed": observed_values,
                            "expected": declared,
                            "pass": passed,
                            "proofCapability": capability["id"],
                            "status": "pass" if passed else "fail",
                        }
                    )
                    if not passed:
                        issues.append(
                            _issue(
                                code="INTERFACE.DIMENSION_DRIFT",
                                interface_id=interface_id,
                                check="declared-dimension",
                                features=features,
                                observed={
                                    "endpoint": endpoint_name,
                                    "field": field,
                                    "valuesMm": observed_values,
                                },
                                expected={
                                    "declaredMm": declared,
                                    "toleranceMm": GEOMETRY_TOLERANCE_MM,
                                },
                                repair_hint="Drive the endpoint feature and its scene dimensions from the same named source parameters.",
                            )
                        )
            if measured_dimension_count == 0:
                issues.append(
                    _issue(
                        code="INTERFACE.EVIDENCE_NOT_EVALUATED",
                        interface_id=interface_id,
                        check="declared-dimension",
                        features=features,
                        observed={
                            name: sorted(values)
                            for name, values in declared_dimensions.items()
                        },
                        expected="at least one measurable endpoint dimension",
                        severity="warning",
                        repair_hint="Use diameter, width, depth, height, length, or span for dimensions that should be checked against feature bounds.",
                    )
                )

        derived = endpoints["female"].get("derivedDimensionsMm")
        raw_clearance_targets = target.get("clearances_mm")
        clearance_targets = (
            {
                field: float(value)
                for field, value in raw_clearance_targets.items()
                if isinstance(field, str)
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and float(value) >= 0
            }
            if isinstance(raw_clearance_targets, dict)
            else {}
        )
        declared_clearance_fields = (
            set(derived) if isinstance(derived, dict) else set()
        )
        if "clearance" in geometry_checks and (
            not clearance_targets
            or not isinstance(raw_clearance_targets, dict)
            or len(clearance_targets) != len(raw_clearance_targets)
            or declared_clearance_fields != set(clearance_targets)
        ):
            issues.append(
                _issue(
                    code="INTERFACE.EVIDENCE_MISSING",
                    interface_id=interface_id,
                    check="clearance-contract",
                    features=features,
                    observed={
                        "intentFields": sorted(clearance_targets),
                        "sceneDerivedFields": sorted(declared_clearance_fields),
                    },
                    expected="the same non-empty named clearance dimensions in intent and scene",
                    repair_hint="Declare clearances_mm by dimension in intent and derive exactly those female dimensions in the scene.",
                )
            )
        measured_clearance_count = 0
        if "clearance" in geometry_checks and isinstance(derived, dict):
            for field, clearance_target in sorted(clearance_targets.items()):
                rule = derived.get(field)
                if not isinstance(rule, dict):
                    continue
                offset = rule.get("offsetMm")
                if (
                    not isinstance(offset, (int, float))
                    or isinstance(offset, bool)
                    or not math.isfinite(float(offset))
                    or float(offset) != clearance_target
                ):
                    issues.append(
                        _issue(
                            code="INTERFACE.CLEARANCE_CONTRACT_MISMATCH",
                            interface_id=interface_id,
                            check="clearance-contract",
                            features=features,
                            observed={"field": field, "offsetMm": offset},
                            expected={"field": field, "clearanceMm": clearance_target},
                            repair_hint="Copy each immutable clearances_mm value into the matching scene derivedDimensionsMm offset.",
                        )
                    )
                    continue
                axes = _dimension_axis(field, axis_index)
                if axes is None:
                    continue
                measured_clearance_count += 1
                observed_clearances = [
                    float(female_size[index] - male_size[index]) for index in axes
                ]
                passed = all(
                    abs(value - clearance_target) <= GEOMETRY_TOLERANCE_MM
                    for value in observed_clearances
                )
                checks.append(
                    {
                        "check": "clearance",
                        "field": field,
                        "interfaceId": interface_id,
                        "observed": observed_clearances,
                        "expected": clearance_target,
                        "pass": passed,
                        "proofCapability": capability["id"],
                        "status": "pass" if passed else "fail",
                    }
                )
                if not passed:
                    issues.append(
                        _issue(
                            code="INTERFACE.CLEARANCE_MISMATCH",
                            interface_id=interface_id,
                            check="clearance",
                            features=features,
                            observed={"field": field, "clearanceMm": observed_clearances},
                            expected={"clearanceMm": clearance_target, "toleranceMm": GEOMETRY_TOLERANCE_MM},
                            repair_hint="Derive the female feature from the male feature plus the immutable clearance instead of entering both sizes independently.",
                        )
                    )
        if "clearance" in geometry_checks and measured_clearance_count == 0:
            issues.append(
                _issue(
                    code="INTERFACE.EVIDENCE_NOT_EVALUATED",
                    interface_id=interface_id,
                    check="clearance",
                    features=features,
                    observed=(sorted(derived) if isinstance(derived, dict) else None),
                    expected="a measurable female dimension derived from its male peer",
                    severity="warning",
                    repair_hint="Expose a shared diameter, width, depth, height, length, or span dimension in both endpoints.",
                )
            )

        if "engagement" in geometry_checks:
            receiver = part_meshes.get(endpoints["female"].get("partId"))
            axial_overlap = (
                _wall_engagement(receiver, (female_low, female_high), (male_low, male_high), axis_index)
                if receiver is not None else None
            )
            engagement_target = float(target.get("engagement_mm", 0.0))
            engagement_pass = (
                axial_overlap is not None
                and axial_overlap + GEOMETRY_TOLERANCE_MM >= engagement_target
            )
            checks.append(
                {
                    "check": "engagement",
                    "interfaceId": interface_id,
                    "observed": axial_overlap,
                    "measurement": "receiver-wall-axial-coverage",
                    "expected": engagement_target,
                    "pass": engagement_pass,
                    "proofCapability": capability["id"],
                    "status": "not_evaluated" if axial_overlap is None else "pass" if engagement_pass else "fail",
                }
            )
            if axial_overlap is None:
                issues.append(_issue(
                    code="INTERFACE.EVIDENCE_NOT_EVALUATED", interface_id=interface_id,
                    check="engagement", features=features, severity="warning",
                    observed="receiver mesh unavailable", expected="physical receiving walls",
                    repair_hint="Export the receiving part mesh to measure effective engagement; cutter bounds alone are insufficient.",
                ))
            elif not engagement_pass:
                issues.append(
                    _issue(
                        code="INTERFACE.ENGAGEMENT_SHORT",
                        interface_id=interface_id,
                        check="engagement",
                        features=features,
                        observed={"engagementMm": axial_overlap},
                        expected={
                            "minimumEngagementMm": engagement_target,
                            "toleranceMm": GEOMETRY_TOLERANCE_MM,
                        },
                        repair_hint="Extend or reposition the paired features along the declared assembly axis while preserving the contracted envelope.",
                    )
                )

        if "axis-alignment" in geometry_checks:
            radial_axes = [index for index in range(3) if index != axis_index]
            male_center = (male_low + male_high) / 2
            female_center = (female_low + female_high) / 2
            center_offset = float(
                np.linalg.norm(male_center[radial_axes] - female_center[radial_axes])
            )
            transverse_clearances = [
                clearance
                for field, clearance in clearance_targets.items()
                if (
                    (dimension_axes := _dimension_axis(field, axis_index))
                    and any(index != axis_index for index in dimension_axes)
                )
            ]
            allowed_offset = max(
                transverse_clearances or [GEOMETRY_TOLERANCE_MM]
            )
            aligned = center_offset <= allowed_offset
            checks.append(
                {
                    "check": "axis-alignment",
                    "interfaceId": interface_id,
                    "observed": center_offset,
                    "expected": allowed_offset,
                    "pass": aligned,
                    "proofCapability": capability["id"],
                    "status": "pass" if aligned else "fail",
                }
            )
            if not aligned:
                issues.append(
                    _issue(
                        code="INTERFACE.AXIS_MISALIGNED",
                        interface_id=interface_id,
                        check="axis-alignment",
                        features=features,
                        observed={"radialCenterOffsetMm": center_offset},
                        expected={"maximumOffsetMm": allowed_offset},
                        repair_hint="Place both endpoint features from one shared interface frame or transform.",
                    )
                )

        part_ids = [endpoints[name].get("partId") for name in ("male", "female")]
        if (
            "assembly-proximity" in geometry_checks
            and all(isinstance(item, str) and item in part_meshes for item in part_ids)
        ):
            try:
                gap = _surface_distance(
                    part_meshes[part_ids[0]],
                    part_meshes[part_ids[1]],
                    endpoint_bounds["male"],
                    endpoint_bounds["female"],
                    margin=max(
                        clearance_targets.values(),
                        default=GEOMETRY_TOLERANCE_MM,
                    )
                    + GEOMETRY_TOLERANCE_MM,
                )
            except Exception as error:
                issues.append(
                    _issue(
                        code="INTERFACE.EVIDENCE_NOT_EVALUATED",
                        interface_id=interface_id,
                        check="assembly-proximity",
                        features=features,
                        observed=str(error),
                        expected="finite part-to-part surface distance",
                        severity="warning",
                        repair_hint="Regenerate valid part meshes and rerun the same interface proof.",
                    )
                )
            else:
                maximum_gap = max(
                    clearance_targets.values(),
                    default=GEOMETRY_TOLERANCE_MM,
                )
                proximity_pass = gap <= maximum_gap + GEOMETRY_TOLERANCE_MM
                checks.append(
                    {
                        "check": "assembly-proximity",
                        "interfaceId": interface_id,
                        "observed": gap,
                        "expected": maximum_gap,
                        "pass": proximity_pass,
                        "proofCapability": capability["id"],
                        "status": "pass" if proximity_pass else "fail",
                    }
                )
                if not proximity_pass:
                    issues.append(
                        _issue(
                            code="INTERFACE.ASSEMBLY_GAP",
                            interface_id=interface_id,
                            check="assembly-proximity",
                            features=features,
                            observed={"minimumSurfaceGapMm": gap},
                            expected={"maximumGapMm": maximum_gap, "derivedFrom": "intent.clearances_mm"},
                            repair_hint="Add or align real mating geometry so the assembled parts are within the declared fit clearance.",
                        )
                    )
                elif (
                    "support-contact-advisory" in geometry_checks
                    and gap > GEOMETRY_TOLERANCE_MM
                ):
                    issues.append(
                        _issue(
                            code="INTERFACE.SUPPORT_CONTACT_ABSENT",
                            interface_id=interface_id,
                            check="support-contact-advisory",
                            features=features,
                            observed={"minimumSurfaceGapMm": gap},
                            expected={"contactToleranceMm": GEOMETRY_TOLERANCE_MM},
                            severity="warning",
                            repair_hint="If this interface carries load, add an explicit shoulder or seating face; otherwise document that retention is provided elsewhere.",
                        )
                    )
        elif "assembly-proximity" in geometry_checks:
            issues.append(
                _issue(
                    code="INTERFACE.EVIDENCE_NOT_EVALUATED",
                    interface_id=interface_id,
                    check="assembly-proximity",
                    features=features,
                    observed={"availableParts": sorted(part_meshes), "requestedParts": part_ids},
                    expected="semantic meshes for both endpoint parts",
                    severity="warning",
                    repair_hint="Export and bind one semantic physical mesh for each interface endpoint part.",
                )
            )

    issues.sort(
        key=lambda item: (
            str(item.get("interfaceId", "")),
            str(item.get("code", "")),
            ",".join(item.get("features", [])),
        )
    )
    errors = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warning"]
    return {
        "checks": checks,
        "errors": errors,
        "issues": issues,
        "pass": not errors,
        "schema": INTERFACE_AUDIT_SCHEMA,
        "warnings": warnings,
    }


def audit_report_interfaces(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    report_dir = report_path.resolve().parent
    inputs = report.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("build report inputs are unavailable")
    intent_path = _artifact_path(report_dir, inputs.get("intent"))
    scene_path = _artifact_path(report_dir, inputs.get("scene"))
    raw_features = report.get("features")
    feature_records = dict(raw_features) if isinstance(raw_features, dict) else {}
    events = report.get("events")
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str):
            continue
        tool = event.get("tool")
        if not isinstance(tool, dict):
            continue
        feature_records.setdefault(
            event["id"],
            {
                **tool,
                **({"part": event["part"]} if isinstance(event.get("part"), str) else {}),
                "role": "cutter",
            },
        )
    return audit_interfaces(
        intent=_load_json(intent_path),
        scene=_load_json(scene_path),
        feature_records=feature_records,
        part_meshes=_part_meshes(report, report_dir),
        self_tapping_proofs=(
            report.get("fastenerGeometryChecks")
            if report.get("backend") == "hybrid-mesh"
            and isinstance(report.get("fastenerGeometryChecks"), dict)
            else None
        ),
    )
