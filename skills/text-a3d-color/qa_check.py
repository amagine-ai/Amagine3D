"""Audit color-region topology, the 3MF print package, or the manufacturing mesh."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import trimesh

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from export_3mf import inspect_color_archive, load_color_archive_mesh


FACE_AXES = {
    "back": (1, "max"),
    "bottom": (2, "min"),
    "front": (1, "min"),
    "left": (0, "min"),
    "right": (0, "max"),
    "top": (2, "max"),
}
PLACED_OPENING_KINDS = {
    "cavity",
    "cutout",
    "hole",
    "port",
    "recess",
    "slot",
    "window",
}
PRINT_PACKAGE_MODES = {"co_print_body", "separate_parts"}


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
        status = "pass" if passed else ("fail" if severity == "error" else "warning")
        self.checks.append({
            "blocking": severity == "error",
            "category": category,
            "name": name,
            "observed": observed,
            "pass": bool(passed),
            "severity": severity,
            "status": status,
            **({"expected": expected} if expected is not None else {}),
            **({"repair": repair} if repair is not None and not passed else {}),
        })

    def skip(self, name: str, reason: str, *, repair=None) -> None:
        self.checks.append({
            "blocking": False,
            "category": "printability",
            "name": name,
            "observed": {"reason": reason},
            "pass": False,
            "severity": "warning",
            "status": "not_evaluated",
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


def _intent_dimensions(intent: dict | None) -> tuple[float, float, float] | None:
    if not isinstance(intent, dict):
        return None
    dimensions = intent.get("dimensions_mm")
    if not isinstance(dimensions, dict):
        return None
    values = []
    for axis in "xyz":
        record = dimensions.get(axis)
        if not isinstance(record, dict):
            return None
        value = record.get("value")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not np.isfinite(value)
            or value <= 0
        ):
            return None
        values.append(float(value))
    return tuple(values)


def _report_print_dimensions(report: dict | None) -> tuple[float, float, float] | None:
    if not isinstance(report, dict):
        return None
    value = report.get("manufacturing", {}).get("bbox_mm", {}).get("size")
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        dimensions = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return dimensions if all(np.isfinite(item) and item > 0 for item in dimensions) else None


def _digest(path: str) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


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


def find_bed_placement(width, depth, printable_polygon, excluded_polygons):
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


def _bbox_bounds(record: dict) -> np.ndarray | None:
    bbox = record.get("bbox_mm", {}) if isinstance(record, dict) else {}
    if not isinstance(bbox.get("min"), list) or not isinstance(bbox.get("max"), list):
        return None
    try:
        bounds = np.asarray([bbox["min"], bbox["max"]], dtype=float)
    except (TypeError, ValueError):
        return None
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        return None
    return bounds


def feature_measurements(report: dict | None) -> list[dict]:
    if report is None:
        return []
    measurements = []
    for feature_id, record in report.get("features", {}).items():
        size = _bbox_size(record)
        if size is None or record.get("role") in {"body", "envelope", "parent"}:
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
            if size:
                measurements.append({
                    "feature_id": event.get("id"),
                    "kind": "cut-tool",
                    "minimum_size_mm": min(value for value in size if value > 1e-9),
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


def evidence_feature_ids(report: dict | None) -> set[str]:
    """Return every stable feature ID backed by an observation or operation."""
    if report is None:
        return set()
    identifiers = {
        feature_id
        for feature_id in report.get("features", {})
        if isinstance(feature_id, str) and feature_id.strip()
    }
    identifiers.update(
        event["id"]
        for event in report.get("events", [])
        if isinstance(event, dict)
        and isinstance(event.get("id"), str)
        and event["id"].strip()
    )
    return identifiers


def print_package_mode(intent: dict | None, report: dict | None) -> str:
    value = None
    if isinstance(intent, dict):
        value = intent.get("printability", {}).get("print_package_mode")
    if value is None and isinstance(report, dict):
        value = report.get("print_package_mode")
    mode = value or "co_print_body"
    return mode if mode in PRINT_PACKAGE_MODES else "invalid"


def region_continuity_observation(intent: dict | None, report: dict | None) -> dict:
    result = {
        "examined": [],
        "offenders": [],
        "skipped": [],
    }
    if not isinstance(intent, dict) or not isinstance(report, dict):
        return result
    report_regions = report.get("regions", {})
    if not isinstance(report_regions, dict):
        result["skipped"].append({
            "reason": "build report has no region records",
            "region": None,
        })
        return result
    for region in intent.get("color_regions", []):
        if not isinstance(region, dict):
            continue
        if region.get("continuity") != "continuous-core":
            continue
        name = region.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        record = report_regions.get(name)
        if not isinstance(record, dict):
            result["offenders"].append({
                "reason": "region missing from build report",
                "region": name,
            })
            continue
        solid_count = record.get("solid_count")
        result["examined"].append({
            "region": name,
            "solid_count": solid_count,
        })
        if solid_count != 1:
            result["offenders"].append({
                "expected": 1,
                "observed": solid_count,
                "reason": "continuous-core region is split into multiple solids",
                "region": name,
            })
    return result


def _body_bounds(report: dict | None) -> np.ndarray | None:
    if report is None:
        return None
    semantic = report.get("semantic", {})
    if isinstance(semantic, dict):
        bounds = _bbox_bounds(semantic.get("shape", {}))
        if bounds is not None:
            return bounds
    assembly = report.get("assembly", {})
    if isinstance(assembly, dict):
        bounds = _bbox_bounds(assembly.get("shape", {}))
        if bounds is not None:
            return bounds
    return _bbox_bounds(report.get("manufacturing", {}))


def _feature_records(report: dict, feature_id: str) -> list[dict]:
    records = []
    for event in report.get("events", []):
        if isinstance(event, dict) and event.get("id") == feature_id:
            bounds = _bbox_bounds(event.get("tool", {})) if event.get("kind") == "cut" else None
            if bounds is not None:
                records.append({
                    "bounds": bounds,
                    "source": f"event:{event.get('kind', 'operation')}",
                })
    feature = report.get("features", {}).get(feature_id)
    if isinstance(feature, dict):
        bounds = _bbox_bounds(feature)
        if bounds is not None:
            records.append({"bounds": bounds, "source": "feature"})
    return records


def _touches_face(bounds: np.ndarray, body_bounds: np.ndarray, face: str, tolerance: float) -> bool:
    axis, side = FACE_AXES[face]
    limit = body_bounds[0, axis] if side == "min" else body_bounds[1, axis]
    return bool(bounds[0, axis] <= limit + tolerance and bounds[1, axis] >= limit - tolerance)


def _adjacent_external_faces(
    bounds: np.ndarray,
    body_bounds: np.ndarray,
    target_face: str,
    tolerance: float,
) -> list[str]:
    target_axis = FACE_AXES[target_face][0]
    return sorted(
        face
        for face, (axis, _) in FACE_AXES.items()
        if axis != target_axis and _touches_face(bounds, body_bounds, face, tolerance)
    )


def semantic_placement_observation(
    intent: dict | None,
    report: dict | None,
    *,
    tolerance: float = 0.25,
) -> dict:
    result = {
        "examined": 0,
        "offenders": [],
        "passed_feature_ids": [],
        "skipped": [],
        "tolerance_mm": tolerance,
    }
    body_bounds = _body_bounds(report)
    if intent is None or report is None:
        return result
    if body_bounds is None:
        result["skipped"].append({
            "feature_id": None,
            "reason": "body bounds are unavailable",
        })
        return result
    for feature in intent.get("features", []):
        if not isinstance(feature, dict):
            continue
        feature_id = feature.get("id")
        kind = feature.get("kind")
        face = feature.get("face")
        edge_crossing = feature.get("edge_crossing", "allowed")
        if kind not in PLACED_OPENING_KINDS:
            if face is not None or edge_crossing != "allowed":
                result["skipped"].append({
                    "feature_id": feature_id if isinstance(feature_id, str) else None,
                    "reason": "semantic outside-face checks apply only to openings",
                })
            continue
        if not isinstance(feature_id, str) or not feature_id.strip():
            continue
        if face not in FACE_AXES:
            result["skipped"].append({
                "feature_id": feature_id,
                "reason": f"face {face!r} is not an auditable outside face",
            })
            continue
        records = _feature_records(report, feature_id)
        result["examined"] += 1
        if not records:
            result["offenders"].append({
                "feature_id": feature_id,
                "reason": "no observed feature or checked cut bounds",
            })
            continue
        record_results = []
        for record in records:
            bounds = record["bounds"]
            adjacent = _adjacent_external_faces(bounds, body_bounds, face, tolerance)
            record_results.append({
                "adjacent_external_faces": adjacent,
                "bounds_mm": bounds.round(5).tolist(),
                "source": record["source"],
                "touches_declared_face": _touches_face(
                    bounds, body_bounds, face, tolerance
                ),
            })
        touches_declared = any(item["touches_declared_face"] for item in record_results)
        adjacent_faces = sorted({
            face_name
            for item in record_results
            for face_name in item["adjacent_external_faces"]
        })
        crossing_ok = (
            edge_crossing not in {"forbidden", "required"}
            or (edge_crossing == "forbidden" and not adjacent_faces)
            or (edge_crossing == "required" and bool(adjacent_faces))
        )
        if touches_declared and crossing_ok:
            result["passed_feature_ids"].append(feature_id)
        else:
            result["offenders"].append({
                "adjacent_external_faces": adjacent_faces,
                "edge_crossing": edge_crossing,
                "expected_face": face,
                "feature_id": feature_id,
                "kind": kind,
                "records": record_results,
            })
    return result


def _feature_bounds(report: dict | None) -> list[tuple[str, np.ndarray]]:
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


def _affected(bounds: np.ndarray | None, report: dict | None) -> list[str]:
    if bounds is None:
        return []
    return sorted({
        feature_id
        for feature_id, feature_bounds in _feature_bounds(report)
        if np.all(bounds[1] >= feature_bounds[0])
        and np.all(feature_bounds[1] >= bounds[0])
    })


def thickness_observation(mesh, *, target_mm: float, sample_limit: int, report):
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
        ordered = np.argsort(weights)
        small_count = max(1, sample_limit // 4)
        indices = np.unique(np.concatenate((
            ordered[:small_count], ordered[-(sample_limit - small_count):]
        )))
        centers, normals, weights = centers[indices], normals[indices], weights[indices]
    values = np.asarray(
        trimesh.proximity.thickness(mesh, centers, normals=normals, method="max_sphere"),
        dtype=float,
    )
    valid = np.isfinite(values) & (values > 1e-7)
    if not valid.any():
        raise ValueError("local thickness produced no finite positive samples")
    values, points, weights = values[valid], centers[valid], weights[valid]
    violating = values < target_mm
    risk_bounds = None
    if violating.any():
        risk_points = points[violating]
        risk_bounds = np.asarray([risk_points.min(axis=0), risk_points.max(axis=0)])
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    index = int(np.searchsorted(cumulative, cumulative[-1] * 0.05, side="left"))
    p05 = float(values[order][min(index, len(values) - 1)])
    return {
        "affected_feature_ids": _affected(risk_bounds, report),
        "minimum_mm": round(float(values.min()), 5),
        "p05_mm": round(p05, 5),
        "risk_bounds_mm": risk_bounds.round(5).tolist() if risk_bounds is not None else None,
        "sample_count": int(len(values)),
        "violating_count": int(np.count_nonzero(violating)),
        "violating_area_ratio": round(float(weights[violating].sum() / max(weights.sum(), 1e-12)), 6),
    }


def overhang_observation(mesh, *, threshold_deg: float, build_plane_tolerance: float, report):
    normals = np.asarray(mesh.face_normals, dtype=float)
    triangles = np.asarray(mesh.triangles, dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    minimum_z = float(mesh.bounds[0, 2])
    above_bed = triangles[:, :, 2].max(axis=1) > minimum_z + max(build_plane_tolerance, 1e-5)
    slopes = np.degrees(np.arccos(np.clip(np.abs(normals[:, 2]), 0.0, 1.0)))
    risky = (normals[:, 2] < -1e-8) & above_bed & (slopes < threshold_deg)
    if not risky.any():
        return {
            "affected_feature_ids": [], "area_mm2": 0.0, "face_count": 0,
            "minimum_slope_deg": None, "risk_bounds_mm": None,
        }
    points = triangles[risky].reshape((-1, 3))
    bounds = np.asarray([points.min(axis=0), points.max(axis=0)])
    return {
        "affected_feature_ids": _affected(bounds, report),
        "area_mm2": round(float(areas[risky].sum()), 5),
        "face_count": int(np.count_nonzero(risky)),
        "minimum_slope_deg": round(float(slopes[risky].min()), 5),
        "risk_bounds_mm": bounds.round(5).tolist(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--region", default="manufacturing")
    parser.add_argument("--topology-only", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--intent")
    parser.add_argument("--report")
    parser.add_argument("--expect-x", type=float)
    parser.add_argument("--expect-y", type=float)
    parser.add_argument("--expect-z", type=float)
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--components", type=int, default=1)
    parser.add_argument("--require-z0", action="store_true")
    parser.add_argument("--max-degenerate-ratio", type=float, default=0.001)
    parser.add_argument("--thickness-samples", type=int, default=2048)
    parser.add_argument("--out")
    args = parser.parse_args()
    model_path = Path(args.model)
    is_print_package = model_path.suffix.lower() == ".3mf"

    try:
        profile, profile_hash = _load_profile(args.profile) if args.profile else (None, None)
        intent = _load_json(args.intent) if args.intent else None
        report = _load_json(args.report) if args.report else None
        expected_dimensions = _report_print_dimensions(report) or _intent_dimensions(intent)
        if intent is not None:
            if profile is None:
                raise ValueError("--intent requires --profile")
            expected_hash = intent.get("printability", {}).get("profile", {}).get("sha256")
            if expected_hash != profile_hash:
                raise ValueError("printer profile does not match the intent contract hash")
        if not args.topology_only and (
            profile is None or intent is None or report is None
        ):
            raise ValueError(
                "manufacturing audit requires --profile, --intent, and --report"
            )
        if not args.topology_only:
            if intent.get("schema") != "evidence-color-intent/v3":
                raise ValueError("unsupported color intent schema; expected v3")
            if report.get("schema") != "evidence-color-build/v5":
                raise ValueError("unsupported color build report schema; expected v5")
            if report.get("part") != intent.get("part"):
                raise ValueError("build report part does not match the intent contract")
            if report.get("intent", {}).get("sha256") != _digest(args.intent):
                raise ValueError("build report is not bound to the supplied intent")
            if print_package_mode(intent, report) == "invalid":
                raise ValueError(
                    "printability.print_package_mode must be co_print_body or separate_parts"
                )
            artifacts = report.get("artifacts", {})
            if is_print_package:
                archive = artifacts.get("3mf", {})
                if archive.get("sha256") != _digest(args.model):
                    raise ValueError(
                        "build report is not bound to the supplied 3MF print package"
                    )
            else:
                manufacturing = artifacts.get("stl", artifacts.get("stl:manufacturing", {}))
                if manufacturing.get("sha256") != _digest(args.model):
                    raise ValueError(
                        "build report is not bound to the supplied manufacturing STL"
                    )
            target = intent.get("printability", {}).get("minimum_wall_target_mm")
            if not isinstance(target, (int, float)) or isinstance(target, bool) or target <= 0:
                raise ValueError("intent minimum wall target must be positive")
            critical = intent.get("printability", {}).get("critical_features")
            if not isinstance(critical, list) or not critical or not all(
                isinstance(item, str) and item.strip() for item in critical
            ):
                raise ValueError("intent critical features must be a non-empty list")
    except Exception as error:
        print(json.dumps({"error": str(error), "pass": False}, indent=2))
        return 2

    try:
        if is_print_package:
            mesh, package = load_color_archive_mesh(args.model)
        else:
            mesh = trimesh.load(args.model, force="mesh", process=True)
            package = None
    except Exception as error:
        print(json.dumps({"error": str(error), "pass": False}, indent=2))
        return 2
    if not isinstance(mesh, trimesh.Trimesh):
        print(json.dumps({"error": "model did not load as one mesh", "pass": False}, indent=2))
        return 2

    audit = Audit()
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    finite = bool(len(vertices) and np.isfinite(vertices).all())
    audit.add("finite_vertices", finite, len(vertices))
    audit.add("has_triangles", len(faces) >= 4, len(faces), ">= 4")
    bounds = np.asarray(mesh.bounds, dtype=float) if finite else None
    dims = bounds[1] - bounds[0] if bounds is not None and bounds.shape == (2, 3) else None

    if is_print_package:
        archive = package or inspect_color_archive(args.model)
        expected_regions = {
            name: item["color"].upper()
            for name, item in (report or {}).get("regions", {}).items()
            if isinstance(item, dict) and isinstance(item.get("color"), str)
        }
        region_inventory = archive.get("regions") or archive.get("objects", [])
        observed_regions = {
            item["name"]: (item["color"] or "").upper()
            for item in region_inventory
        }
        expected_package_mode = print_package_mode(intent, report)
        audit.add(
            "print_package_unit",
            str(archive.get("unit", "")).lower() == "millimeter",
            archive.get("unit"),
            "millimeter",
            category="print-package",
        )
        audit.add(
            "print_package_build_items",
            int(archive.get("build_item_count", 0)) > 0,
            archive.get("build_item_count", 0),
            "> 0",
            category="print-package",
        )
        audit.add(
            "print_package_mode",
            archive.get("package_mode") == expected_package_mode,
            archive.get("package_mode"),
            expected_package_mode,
            category="print-package",
        )
        if expected_package_mode == "co_print_body":
            build_items = archive.get("build_items", [])
            audit.add(
                "print_package_co_print_build_item",
                int(archive.get("build_item_count", 0)) == 1
                and bool(build_items)
                and build_items[0].get("object_kind") == "mesh",
                {
                    "build_item_count": archive.get("build_item_count", 0),
                    "top_level_kinds": [
                        item.get("object_kind") for item in build_items
                    ],
                },
                {
                    "build_item_count": 1,
                    "top_level_kind": "mesh",
                },
                category="print-package",
            )
        audit.add(
            "region_names",
            set(expected_regions) == set(observed_regions),
            sorted(observed_regions),
            sorted(expected_regions),
            category="print-package",
        )
        audit.add(
            "region_colors",
            expected_regions == observed_regions,
            observed_regions,
            expected_regions,
            category="print-package",
        )
        continuity = region_continuity_observation(intent, report)
        if continuity["examined"] or continuity["offenders"]:
            audit.add(
                "region_continuity",
                not continuity["offenders"],
                continuity,
                "continuous-core regions must remain one solid",
                category="printability",
                repair={
                    "goal": "Keep the region's structural core continuous and move color details to surface shells, shallow insets, raised overlays, or shallow filled grooves."
                },
            )
        if dims is not None:
            for index, axis in enumerate("xyz"):
                expected = getattr(args, f"expect_{axis}")
                if expected is None and expected_dimensions is not None:
                    expected = expected_dimensions[index]
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
            if profile is not None:
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
                        "preferred_actions": [
                            "Select a lower-support whole-package print orientation and apply the recorded uniform print scale when needed.",
                            "Use a supported larger printer only when dimensions are fixed.",
                        ],
                    },
                )
            stl_record = (report or {}).get("manufacturing", {})
            stl_size = _bbox_size(stl_record)
            if stl_size is not None:
                deltas = [abs(float(dims[index]) - stl_size[index]) for index in range(3)]
                audit.add(
                    "print_package_matches_stl_bounds",
                    max(deltas) <= args.tol,
                    {
                        "package_dimensions_mm": dims.round(5).tolist(),
                        "stl_dimensions_mm": stl_size,
                        "max_delta_mm": round(max(deltas), 5),
                    },
                    {"tolerance": args.tol},
                    category="print-package",
                )
        result = {
            "checks": audit.checks,
            "errors": [item["name"] for item in audit.checks if item["status"] == "fail"],
            "intent": str(Path(args.intent).resolve()) if args.intent else None,
            "mesh": {
                "bounds_mm": bounds.round(5).tolist() if bounds is not None else None,
                "dimensions_mm": dims.round(5).tolist() if dims is not None else None,
                "faces": len(faces),
                "surface_area_mm2": round(float(mesh.area), 5) if len(faces) else 0.0,
                "vertices": len(vertices),
            },
            "pass": audit.passed,
            "print_package": archive,
            "printer_profile": ({
                "id": profile["id"], "path": str(Path(args.profile).resolve()),
                "sha256": profile_hash,
            } if profile is not None else None),
            "report": str(Path(args.report).resolve()) if args.report else None,
            "schema": "evidence-color-print-package-audit/v1",
            "scope": "print-package",
            "status": audit.status,
            "model": str(model_path.resolve()),
            "warnings": [
                item["name"] for item in audit.checks
                if item["status"] in {"warning", "not_evaluated"}
            ],
        }
        payload = json.dumps(result, indent=2)
        if args.out:
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0 if audit.passed else 1

    audit.add("watertight", mesh.is_watertight, mesh.is_watertight, True)
    audit.add("consistent_winding", mesh.is_winding_consistent, mesh.is_winding_consistent, True)
    areas = np.asarray(mesh.area_faces)
    degenerate = int(np.count_nonzero(~np.isfinite(areas) | (areas <= 1e-10)))
    ratio = degenerate / max(len(areas), 1)
    audit.add("degenerate_faces", ratio <= args.max_degenerate_ratio,
              {"count": degenerate, "ratio": round(ratio, 8)},
              {"max_ratio": args.max_degenerate_ratio})
    components = len(mesh.split(only_watertight=False)) if len(faces) else 0
    audit.add("connected_components", components == args.components, components, args.components)
    volume = float(mesh.volume) if mesh.is_watertight and len(faces) else None
    positive_volume = volume is not None and np.isfinite(volume) and volume > 1e-6
    audit.add("positive_volume", positive_volume, volume, "> 0")

    if dims is not None:
        for index, axis in enumerate("xyz"):
            expected = getattr(args, f"expect_{axis}")
            if expected is None and expected_dimensions is not None:
                expected = expected_dimensions[index]
            if expected is not None:
                audit.add(f"dimension_{axis}", abs(float(dims[index]) - expected) <= args.tol,
                          round(float(dims[index]), 5),
                          {"value": expected, "tolerance": args.tol}, category="dimensions")
        if args.require_z0:
            audit.add("build_plane_z0", abs(float(bounds[0, 2])) <= args.tol,
                      round(float(bounds[0, 2]), 5),
                      {"value": 0.0, "tolerance": args.tol}, category="dimensions")

    if not args.topology_only and profile is not None and dims is not None:
        bed_passed, bed_observed = check_bed_fit(dims, profile)
        tool = profile["machine"]["selected_tool"]
        audit.add("printability_bed_fit", bed_passed, bed_observed, {
            "excluded_polygons_mm": profile["machine"].get("excluded_polygons_mm", []),
            "printable_height_mm": tool["height_mm"],
            "printable_polygon_mm": tool["polygon_mm"],
        }, category="printability", repair={
            "forbidden_actions": [
                "Do not switch profiles or scale fixed user dimensions to pass.",
                "Do not flatten or simplify replica geometry to improve bed fit.",
            ],
            "preferred_actions": [
                "Use the selected whole-package print orientation and recorded uniform print scale.",
                "Split only where the contract permits a real assembly interface.",
                "Use a supported larger printer only when dimensions are fixed.",
            ],
        })

        floor = float(profile["derived"]["single_line_floor_mm"])
        target = max(
            float(profile["derived"]["process_wall_target_mm"]),
            float(intent["printability"]["minimum_wall_target_mm"]),
        )
        measurements = feature_measurements(report)
        measured_ids = {item["feature_id"] for item in measurements}
        observed_ids = evidence_feature_ids(report)
        critical_ids = set(intent["printability"]["critical_features"])
        missing_critical = sorted(critical_ids - observed_ids)
        audit.add(
            "printability_critical_feature_coverage",
            not missing_critical,
            {
                "measured_feature_ids": sorted(measured_ids),
                "observed_feature_ids": sorted(observed_ids),
                "missing_feature_ids": missing_critical,
            },
            {"critical_feature_ids": sorted(critical_ids)},
            category="printability",
            repair={
                "goal": "Observe every critical feature or record its checked operation."
            },
        )
        continuity = region_continuity_observation(intent, report)
        if continuity["examined"] or continuity["offenders"]:
            audit.add(
                "region_continuity",
                not continuity["offenders"],
                continuity,
                "continuous-core regions must remain one solid",
                category="printability",
                repair={
                    "goal": "Keep the region's structural core continuous and move color details to surface shells, shallow insets, raised overlays, or shallow filled grooves."
                },
            )
        semantic = semantic_placement_observation(intent, report)
        if semantic["examined"] or semantic["offenders"]:
            audit.add(
                "semantic_feature_placement",
                not semantic["offenders"],
                semantic,
                "declared feature face and edge crossing",
                category="geometry",
                repair={
                    "goal": "Move each feature to its declared semantic face or update the intent before modeling.",
                    "preferred_actions": [
                        "Derive the cut position from named face datum variables.",
                        "Set edge_crossing to allowed or required only for intentional edge or corner features.",
                    ],
                },
            )
        if not measurements:
            audit.skip("printability_feature_resolution",
            "the v4 build report contains no measurable non-envelope features",
                       repair="Observe manufacturing-critical features and checked cut tools.")
        else:
            offenders = [item for item in measurements if item["minimum_size_mm"] < floor]
            audit.add("printability_feature_resolution", not offenders,
                      {"examined": len(measurements), "offenders": offenders},
                      {"minimum_single_line_mm": floor}, category="printability",
                      severity="warning", repair={
                          "goal": f"Increase each named feature to at least {floor:g} mm."
                      })

        if positive_volume and mesh.is_watertight:
            try:
                thickness = thickness_observation(
                    mesh, target_mm=target,
                    sample_limit=max(args.thickness_samples, 32), report=report,
                )
                audit.add("printability_wall_thickness",
                          thickness["p05_mm"] + 1e-9 >= target, thickness,
                          {"process_wall_target_mm": target,
                           "intent_wall_target_mm": intent["printability"]["minimum_wall_target_mm"],
                           "single_line_floor_mm": floor,
                           "criterion": "area-weighted-p05"},
                          category="printability", severity="warning", repair={
                              "goal": f"Raise local wall thickness to at least {target:g} mm."
                          })
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
                        "goal": f"Raise every sampled local wall to at least {target:g} mm.",
                        "preferred_actions": [
                            "Repair the named feature IDs intersecting the risk bounds.",
                            "Inspect unnamed risk bounds before changing unrelated geometry.",
                        ],
                    },
                )
            except Exception as error:
                audit.skip("printability_wall_thickness", str(error),
                           repair="Install the pinned runtime and rerun the audit.")
                audit.skip(
                    "printability_local_thin_region",
                    str(error),
                    repair="Install the pinned runtime and rerun the audit.",
                )
            overhang = overhang_observation(
                mesh,
                threshold_deg=float(profile["process"]["support_threshold_angle_from_horizontal_deg"]),
                build_plane_tolerance=1e-4,
                report=report,
            )
            audit.add("printability_overhang", overhang["face_count"] == 0, overhang, {
                "angle_origin": "horizontal",
                "support_policy": intent["printability"]["support_policy"],
                "threshold_angle_deg": profile["process"]["support_threshold_angle_from_horizontal_deg"],
            }, category="printability", severity="warning", repair={
                "fallback": "Declare supports-required if faithful geometry still needs support.",
                "preferred_actions": [
                    "Reorient the finished body without changing semantic geometry.",
                    "Split only where the contract permits a real assembly interface.",
                    "Add support-friendly geometry only when it matches the object.",
                ],
            })
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
        "intent": str(Path(args.intent).resolve()) if args.intent else None,
        "mesh": {
            "bounds_mm": bounds.round(5).tolist() if bounds is not None else None,
            "dimensions_mm": dims.round(5).tolist() if dims is not None else None,
            "faces": len(faces),
            "surface_area_mm2": round(float(mesh.area), 5) if len(faces) else 0.0,
            "vertices": len(vertices),
            "volume_mm3": round(volume, 5) if volume is not None else None,
        },
        "pass": audit.passed,
        "printer_profile": ({
            "id": profile["id"], "path": str(Path(args.profile).resolve()),
            "sha256": profile_hash,
        } if profile is not None else None),
        "region": args.region,
        "report": str(Path(args.report).resolve()) if args.report else None,
        "schema": "evidence-color-mesh-audit/v4",
        "scope": "topology" if args.topology_only else "manufacturing",
        "status": audit.status,
        "stl": str(model_path.resolve()),
        "warnings": [item["name"] for item in audit.checks
                     if item["status"] in {"warning", "not_evaluated"}],
    }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if audit.passed else 1


if __name__ == "__main__":
    sys.exit(main())
