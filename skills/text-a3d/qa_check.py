"""Evidence-oriented mesh and Bambu FDM printability audit."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import trimesh


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(
        self,
        name: str,
        passed: bool,
        observed,
        expected=None,
        *,
        category: str = "geometry",
        severity: str = "error",
        repair=None,
    ) -> None:
        if severity not in {"error", "warning"}:
            raise ValueError(f"invalid audit severity: {severity}")
        status = "pass" if passed else ("fail" if severity == "error" else "warning")
        self.checks.append({
            "blocking": severity == "error",
            "category": category,
            "name": name,
            "pass": bool(passed),
            "severity": severity,
            "status": status,
            "observed": observed,
            **({"expected": expected} if expected is not None else {}),
            **({"repair": repair} if repair is not None and not passed else {}),
        })

    def skip(
        self,
        name: str,
        reason: str,
        *,
        category: str = "printability",
        repair=None,
    ) -> None:
        self.checks.append({
            "blocking": False,
            "category": category,
            "name": name,
            "pass": False,
            "severity": "warning",
            "status": "not_evaluated",
            "observed": {"reason": reason},
            **({"repair": repair} if repair is not None else {}),
        })

    @property
    def passed(self) -> bool:
        return not any(item["status"] == "fail" for item in self.checks)

    @property
    def status(self) -> str:
        if not self.passed:
            return "fail"
        if any(item["status"] in {"warning", "not_evaluated"} for item in self.checks):
            return "pass_with_warnings"
        return "pass"


def _load_json(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_profile(path: str) -> tuple[dict, str]:
    payload = Path(path).read_bytes()
    profile = json.loads(payload)
    if profile.get("schema") != "evidence-bambu-printer-profile/v1":
        raise ValueError("unsupported printer profile schema")
    if profile.get("vendor") != "Bambu Lab":
        raise ValueError("only Bambu Lab printer profiles are supported")
    required = (
        profile.get("derived", {}).get("single_line_floor_mm"),
        profile.get("derived", {}).get("process_wall_target_mm"),
        profile.get("machine", {}).get("selected_tool", {}).get("height_mm"),
        profile.get("process", {}).get("support_threshold_angle_from_horizontal_deg"),
    )
    if not all(isinstance(value, (int, float)) and value > 0 for value in required):
        raise ValueError("printer profile has invalid derived limits")
    return profile, sha256(payload).hexdigest()


def _rectangle_bounds(polygon) -> tuple[float, float, float, float]:
    points = np.asarray(polygon, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 4:
        raise ValueError("printable polygons must contain at least four XY points")
    if not np.isfinite(points).all():
        raise ValueError("printable polygon contains non-finite coordinates")
    lower = points.min(axis=0)
    upper = points.max(axis=0)
    return float(lower[0]), float(lower[1]), float(upper[0]), float(upper[1])


def _overlaps(first, second, epsilon: float = 1e-9) -> bool:
    return not (
        first[2] <= second[0] + epsilon
        or second[2] <= first[0] + epsilon
        or first[3] <= second[1] + epsilon
        or second[3] <= first[1] + epsilon
    )


def find_bed_placement(
    width: float,
    depth: float,
    printable_polygon,
    excluded_polygons,
) -> dict | None:
    container = _rectangle_bounds(printable_polygon)
    obstacles = [_rectangle_bounds(item) for item in excluded_polygons]
    if width > container[2] - container[0] + 1e-9:
        return None
    if depth > container[3] - container[1] + 1e-9:
        return None
    x_values = {container[0], container[2] - width}
    y_values = {container[1], container[3] - depth}
    for obstacle in obstacles:
        x_values.update({obstacle[0] - width, obstacle[2]})
        y_values.update({obstacle[1] - depth, obstacle[3]})
    for x in sorted(x_values):
        for y in sorted(y_values):
            candidate = (x, y, x + width, y + depth)
            inside = (
                candidate[0] >= container[0] - 1e-9
                and candidate[1] >= container[1] - 1e-9
                and candidate[2] <= container[2] + 1e-9
                and candidate[3] <= container[3] + 1e-9
            )
            if inside and not any(_overlaps(candidate, item) for item in obstacles):
                return {
                    "bounds_mm": [round(value, 5) for value in candidate],
                    "origin_mm": [round(x, 5), round(y, 5)],
                }
    return None


def check_bed_fit(dimensions, profile: dict) -> tuple[bool, dict]:
    tool = profile["machine"]["selected_tool"]
    excluded = profile["machine"].get("excluded_polygons_mm", [])
    width, depth, height = (float(value) for value in dimensions)
    attempts = []
    for rotated, footprint in ((False, (width, depth)), (True, (depth, width))):
        placement = find_bed_placement(
            footprint[0], footprint[1], tool["polygon_mm"], excluded
        )
        attempts.append({
            "footprint_mm": [round(footprint[0], 5), round(footprint[1], 5)],
            "placement": placement,
            "rotated_xy_90deg": rotated,
        })
        if placement is not None and height <= float(tool["height_mm"]) + 1e-9:
            return True, {
                "attempts": attempts,
                "height_mm": round(height, 5),
                "selected": attempts[-1],
            }
    return False, {"attempts": attempts, "height_mm": round(height, 5)}


def _bbox_size(record: dict) -> list[float] | None:
    value = record.get("bbox_mm", {}).get("size")
    if not isinstance(value, list) or len(value) != 3:
        return None
    values = [float(item) for item in value]
    return values if all(np.isfinite(values)) else None


def feature_measurements(report: dict | None) -> list[dict]:
    if report is None:
        return []
    measurements = []
    ignored_roles = {"body", "envelope", "parent"}
    for feature_id, record in report.get("features", {}).items():
        size = _bbox_size(record)
        if size is None or record.get("role") in ignored_roles:
            continue
        positive = [value for value in size if value > 1e-9]
        if positive:
            measurements.append({
                "feature_id": feature_id,
                "kind": record.get("role", "feature"),
                "minimum_size_mm": min(positive),
                "size_mm": size,
            })
    for event in report.get("events", []):
        if event.get("kind") == "cut":
            size = _bbox_size(event.get("tool", {}))
            if size is not None:
                positive = [value for value in size if value > 1e-9]
                if positive:
                    measurements.append({
                        "feature_id": event.get("id"),
                        "kind": "cut-tool",
                        "minimum_size_mm": min(positive),
                        "size_mm": size,
                    })
        elif event.get("kind") in {"fillet", "chamfer"}:
            actual = event.get("actual_mm")
            if isinstance(actual, (int, float)) and actual > 0:
                measurements.append({
                    "feature_id": event.get("id"),
                    "kind": event["kind"],
                    "minimum_size_mm": float(actual),
                    "size_mm": [float(actual)],
                })
    return measurements


def _report_feature_bounds(report: dict | None) -> list[tuple[str, np.ndarray]]:
    if report is None:
        return []
    records = []
    for feature_id, record in report.get("features", {}).items():
        bbox = record.get("bbox_mm", {})
        if isinstance(bbox.get("min"), list) and isinstance(bbox.get("max"), list):
            records.append((feature_id, np.asarray([bbox["min"], bbox["max"]], dtype=float)))
    for event in report.get("events", []):
        bbox = event.get("tool", {}).get("bbox_mm", {})
        if isinstance(bbox.get("min"), list) and isinstance(bbox.get("max"), list):
            records.append((event.get("id", "unnamed-cut"), np.asarray([bbox["min"], bbox["max"]], dtype=float)))
    return records


def _affected_features(bounds: np.ndarray | None, report: dict | None) -> list[str]:
    if bounds is None:
        return []
    result = []
    for feature_id, feature_bounds in _report_feature_bounds(report):
        if np.all(bounds[1] >= feature_bounds[0]) and np.all(feature_bounds[1] >= bounds[0]):
            result.append(feature_id)
    return sorted(set(result))


def thickness_observation(
    mesh: trimesh.Trimesh,
    *,
    target_mm: float,
    sample_limit: int,
    report: dict | None,
) -> dict:
    triangle_centers = np.asarray(mesh.triangles_center, dtype=float)
    face_areas = np.asarray(mesh.area_faces, dtype=float)
    facets = list(mesh.facets)
    if facets:
        centers = np.asarray([
            np.average(triangle_centers[facet], axis=0, weights=face_areas[facet])
            for facet in facets
        ])
        normals = np.asarray(mesh.facets_normal, dtype=float)
        weights = np.asarray(mesh.facets_area, dtype=float)
    else:
        centers = triangle_centers
        normals = np.asarray(mesh.face_normals, dtype=float)
        weights = face_areas
    if not len(centers):
        raise ValueError("mesh has no surface regions")
    if len(centers) > sample_limit:
        small_count = max(1, sample_limit // 4)
        ordered = np.argsort(weights)
        indices = np.unique(
            np.concatenate((ordered[:small_count], ordered[-(sample_limit - small_count):]))
        )
        centers = centers[indices]
        normals = normals[indices]
        weights = weights[indices]
    values = np.asarray(
        trimesh.proximity.thickness(
            mesh, centers, normals=normals, method="max_sphere"
        ),
        dtype=float,
    )
    valid = np.isfinite(values) & (values > 1e-7)
    if not valid.any():
        raise ValueError("local thickness produced no finite positive samples")
    values = values[valid]
    points = centers[valid]
    weights = weights[valid]
    violating = values < target_mm
    risk_bounds = None
    if violating.any():
        risk_points = points[violating]
        risk_bounds = np.asarray([risk_points.min(axis=0), risk_points.max(axis=0)])
    order = np.argsort(values)
    sorted_values = values[order]
    cumulative = np.cumsum(weights[order])
    p05_index = int(np.searchsorted(cumulative, cumulative[-1] * 0.05, side="left"))
    p05 = float(sorted_values[min(p05_index, len(sorted_values) - 1)])
    return {
        "affected_feature_ids": _affected_features(risk_bounds, report),
        "minimum_mm": round(float(values.min()), 5),
        "p05_mm": round(p05, 5),
        "risk_bounds_mm": risk_bounds.round(5).tolist() if risk_bounds is not None else None,
        "sample_count": int(len(values)),
        "violating_count": int(np.count_nonzero(violating)),
        "violating_area_ratio": round(
            float(weights[violating].sum() / max(weights.sum(), 1e-12)), 6
        ),
    }


def overhang_observation(
    mesh: trimesh.Trimesh,
    *,
    threshold_deg: float,
    build_plane_tolerance: float,
    report: dict | None,
) -> dict:
    normals = np.asarray(mesh.face_normals, dtype=float)
    triangles = np.asarray(mesh.triangles, dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    minimum_z = float(mesh.bounds[0, 2])
    above_build_plane = triangles[:, :, 2].max(axis=1) > (
        minimum_z + max(build_plane_tolerance, 1e-5)
    )
    downward = normals[:, 2] < -1e-8
    slopes = np.degrees(np.arccos(np.clip(np.abs(normals[:, 2]), 0.0, 1.0)))
    risky = downward & above_build_plane & (slopes < threshold_deg)
    if not risky.any():
        return {
            "affected_feature_ids": [],
            "area_mm2": 0.0,
            "face_count": 0,
            "minimum_slope_deg": None,
            "risk_bounds_mm": None,
        }
    points = triangles[risky].reshape((-1, 3))
    risk_bounds = np.asarray([points.min(axis=0), points.max(axis=0)])
    return {
        "affected_feature_ids": _affected_features(risk_bounds, report),
        "area_mm2": round(float(areas[risky].sum()), 5),
        "face_count": int(np.count_nonzero(risky)),
        "minimum_slope_deg": round(float(slopes[risky].min()), 5),
        "risk_bounds_mm": risk_bounds.round(5).tolist(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stl")
    parser.add_argument("--profile", help="resolved profile from bambu_profile.py")
    parser.add_argument("--intent", help="validated evidence intent contract")
    parser.add_argument("--report", help="provenance build report from cad_helpers.py")
    parser.add_argument("--expect-x", type=float)
    parser.add_argument("--expect-y", type=float)
    parser.add_argument("--expect-z", type=float)
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--components", type=int, default=1)
    parser.add_argument("--expect-volume", type=float)
    parser.add_argument("--vol-tol-pct", type=float, default=10.0)
    parser.add_argument("--require-z0", action="store_true")
    parser.add_argument("--max-degenerate-ratio", type=float, default=0.001)
    parser.add_argument("--thickness-samples", type=int, default=2048)
    parser.add_argument("--out")
    args = parser.parse_args()

    try:
        profile, profile_hash = _load_profile(args.profile) if args.profile else (None, None)
        intent = _load_json(args.intent) if args.intent else None
        report = _load_json(args.report) if args.report else None
        if intent is not None:
            if profile is None:
                raise ValueError("--intent requires the resolved --profile")
            expected_hash = intent.get("printability", {}).get("profile", {}).get("sha256")
            if expected_hash != profile_hash:
                raise ValueError("printer profile does not match the intent contract hash")
    except Exception as error:
        print(json.dumps({"pass": False, "error": str(error)}, indent=2))
        return 2

    audit = Audit()
    try:
        mesh = trimesh.load(args.stl, force="mesh", process=True)
    except Exception as error:
        print(json.dumps({"pass": False, "error": str(error)}, indent=2))
        return 2

    if not isinstance(mesh, trimesh.Trimesh):
        print(json.dumps({"pass": False, "error": "STL did not load as one mesh"}, indent=2))
        return 2

    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    finite_vertices = bool(len(vertices) and np.isfinite(vertices).all())
    audit.add("finite_vertices", finite_vertices, len(vertices))
    audit.add("has_triangles", len(faces) >= 4, len(faces), ">= 4")
    audit.add("watertight", mesh.is_watertight, mesh.is_watertight, True)
    audit.add(
        "consistent_winding",
        mesh.is_winding_consistent,
        mesh.is_winding_consistent,
        True,
    )

    areas = np.asarray(mesh.area_faces)
    degenerate = int(np.count_nonzero(~np.isfinite(areas) | (areas <= 1e-10)))
    ratio = degenerate / max(len(areas), 1)
    audit.add(
        "degenerate_faces",
        ratio <= args.max_degenerate_ratio,
        {"count": degenerate, "ratio": round(ratio, 8)},
        {"max_ratio": args.max_degenerate_ratio},
    )

    components = len(mesh.split(only_watertight=False)) if len(faces) else 0
    audit.add("connected_components", components == args.components, components, args.components)
    volume = float(mesh.volume) if mesh.is_watertight and len(faces) else None
    positive_volume = volume is not None and np.isfinite(volume) and volume > 1e-6
    audit.add("positive_volume", positive_volume, volume, "> 0")

    bounds = np.asarray(mesh.bounds, dtype=float) if finite_vertices else None
    dims = bounds[1] - bounds[0] if bounds is not None and bounds.shape == (2, 3) else None
    if dims is not None:
        for index, axis in enumerate("xyz"):
            expected = getattr(args, f"expect_{axis}")
            if expected is not None:
                audit.add(
                    f"dimension_{axis}",
                    abs(float(dims[index]) - expected) <= args.tol,
                    round(float(dims[index]), 5),
                    {"value": expected, "tolerance": args.tol},
                    category="dimensions",
                )
        if args.require_z0:
            audit.add(
                "build_plane_z0",
                abs(float(bounds[0, 2])) <= args.tol,
                round(float(bounds[0, 2]), 5),
                {"value": 0.0, "tolerance": args.tol},
                category="dimensions",
            )
    elif args.require_z0 or any(
        getattr(args, f"expect_{axis}") is not None for axis in "xyz"
    ):
        audit.add(
            "dimension_metrics_available",
            False,
            None,
            "finite non-empty bounds",
            category="dimensions",
        )

    if args.expect_volume is not None and volume is not None:
        delta = abs(volume - args.expect_volume) / max(args.expect_volume, 1e-9) * 100
        audit.add(
            "volume_target",
            delta <= args.vol_tol_pct,
            {"value": round(volume, 5), "delta_percent": round(delta, 5)},
            {"value": args.expect_volume, "tolerance_percent": args.vol_tol_pct},
        )

    if profile is None:
        audit.skip(
            "printability_profile",
            "no resolved Bambu profile was supplied",
            repair="Run bambu_profile.py and rerun qa_check.py with --profile.",
        )
    elif dims is None:
        audit.skip("printability_bed_fit", "finite mesh bounds are unavailable")
    else:
        bed_passed, bed_observed = check_bed_fit(dims, profile)
        tool = profile["machine"]["selected_tool"]
        audit.add(
            "printability_bed_fit",
            bed_passed,
            bed_observed,
            {
                "excluded_polygons_mm": profile["machine"].get("excluded_polygons_mm", []),
                "printable_height_mm": tool["height_mm"],
                "printable_polygon_mm": tool["polygon_mm"],
            },
            category="printability",
            repair={
                "forbidden_actions": [
                    "Do not relax or switch the printer profile without user authority.",
                    "Do not scale user-specified dimensions merely to pass the audit.",
                ],
                "preferred_actions": [
                    "Try the reported 90 degree XY placement.",
                    "Change orientation while preserving contract dimensions.",
                    "Split the model only when the contract permits assembly.",
                    "Ask for a larger supported Bambu machine when dimensions are fixed.",
                ],
            },
        )

    if profile is not None:
        floor = float(profile["derived"]["single_line_floor_mm"])
        target = float(profile["derived"]["process_wall_target_mm"])
        measurements = feature_measurements(report)
        if report is None:
            audit.skip(
                "printability_feature_resolution",
                "no build report was supplied, so feature IDs cannot be measured",
                repair="Rerun with --report <name>_report.json.",
            )
        elif not measurements:
            audit.skip(
                "printability_feature_resolution",
                "the build report contains no measurable non-envelope features",
                repair="Observe additive features and use checked operations for cuts and finishes.",
            )
        else:
            offenders = [item for item in measurements if item["minimum_size_mm"] < floor]
            audit.add(
                "printability_feature_resolution",
                not offenders,
                {"examined": len(measurements), "offenders": offenders},
                {"minimum_single_line_mm": floor},
                category="printability",
                severity="warning",
                repair={
                    "forbidden_actions": [
                        "Do not enable thin-wall detection as the only repair.",
                        "Do not lower the selected profile floor.",
                    ],
                    "goal": f"Increase each named feature to at least {floor:g} mm.",
                    "preferred_actions": [
                        "Change the parameter tied to each reported feature ID.",
                        "Widen strokes, pins, slots, holes, chamfers, or fillets at their source.",
                    ],
                },
            )

        if positive_volume and mesh.is_watertight:
            try:
                thickness = thickness_observation(
                    mesh,
                    target_mm=target,
                    sample_limit=max(args.thickness_samples, 32),
                    report=report,
                )
                audit.add(
                    "printability_wall_thickness",
                    thickness["p05_mm"] + 1e-9 >= target,
                    thickness,
                    {
                        "process_wall_target_mm": target,
                        "single_line_floor_mm": floor,
                        "criterion": "area-weighted-p05",
                    },
                    category="printability",
                    severity="warning",
                    repair={
                        "forbidden_actions": [
                            "Do not lower the wall target or rely on slicer compensation alone."
                        ],
                        "goal": f"Raise local wall thickness to at least {target:g} mm.",
                        "preferred_actions": [
                            "Increase the named wall or shell parameter.",
                            "Thicken only the reported risk region when outer dimensions are fixed.",
                            "Remove decorative recess depth before changing user dimensions.",
                        ],
                    },
                )
                audit.add(
                    "printability_local_thin_region",
                    thickness["violating_count"] == 0,
                    {
                        "affected_feature_ids": thickness["affected_feature_ids"],
                        "minimum_mm": thickness["minimum_mm"],
                        "risk_bounds_mm": thickness["risk_bounds_mm"],
                        "sample_count": thickness["sample_count"],
                        "violating_area_ratio": thickness["violating_area_ratio"],
                        "violating_count": thickness["violating_count"],
                    },
                    {"minimum_local_wall_mm": target},
                    category="printability",
                    severity="warning",
                    repair={
                        "forbidden_actions": [
                            "Do not dismiss a named thin region because its total area is small."
                        ],
                        "goal": f"Raise every sampled local wall to at least {target:g} mm.",
                        "preferred_actions": [
                            "Repair the named feature IDs intersecting the risk bounds.",
                            "Inspect unnamed risk bounds before changing unrelated geometry.",
                        ],
                    },
                )
            except Exception as error:
                audit.skip(
                    "printability_wall_thickness",
                    str(error),
                    repair="Install the pinned CAD runtime and rerun the audit.",
                )
                audit.skip(
                    "printability_local_thin_region",
                    str(error),
                    repair="Install the pinned CAD runtime and rerun the audit.",
                )

            overhang = overhang_observation(
                mesh,
                threshold_deg=float(
                    profile["process"]["support_threshold_angle_from_horizontal_deg"]
                ),
                build_plane_tolerance=1e-4,
                report=report,
            )
            audit.add(
                "printability_overhang",
                overhang["face_count"] == 0,
                overhang,
                {
                    "angle_origin": "horizontal",
                    "support_enabled": profile["process"]["support_enabled"],
                    "support_policy": (
                        intent.get("printability", {}).get("support_policy")
                        if intent is not None
                        else None
                    ),
                    "threshold_angle_deg": profile["process"][
                        "support_threshold_angle_from_horizontal_deg"
                    ],
                },
                category="printability",
                severity="warning",
                repair={
                    "fallback": "Declare support_required; never claim support-free printability.",
                    "preferred_actions": [
                        "Reorient the build while preserving required dimensions.",
                        "Replace the underside with a slope at or above the profile threshold.",
                        "Use a chamfer, arch, teardrop opening, or permitted assembly split.",
                    ],
                },
            )
        else:
            audit.skip(
                "printability_wall_thickness",
                "requires one positive watertight volume",
            )
            audit.skip(
                "printability_local_thin_region",
                "requires one positive watertight volume",
            )
            audit.skip(
                "printability_overhang",
                "requires one positive watertight volume",
            )

    result = {
        "checks": audit.checks,
        "errors": [item["name"] for item in audit.checks if item["status"] == "fail"],
        "mesh": {
            "bounds_mm": bounds.round(5).tolist() if bounds is not None else None,
            "dimensions_mm": dims.round(5).tolist() if dims is not None else None,
            "faces": len(faces),
            "surface_area_mm2": round(float(mesh.area), 5) if len(faces) else 0.0,
            "vertices": len(vertices),
            "volume_mm3": round(volume, 5) if volume is not None else None,
        },
        "pass": audit.passed,
        "intent": str(Path(args.intent).resolve()) if args.intent else None,
        "printer_profile": (
            {
                "id": profile["id"],
                "path": str(Path(args.profile).resolve()),
                "sha256": profile_hash,
            }
            if profile is not None
            else None
        ),
        "report": str(Path(args.report).resolve()) if args.report else None,
        "schema": "evidence-mesh-audit/v3",
        "status": audit.status,
        "stl": str(Path(args.stl).resolve()),
        "warnings": [
            item["name"]
            for item in audit.checks
            if item["status"] in {"warning", "not_evaluated"}
        ],
    }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if audit.passed else 1


if __name__ == "__main__":
    sys.exit(main())
