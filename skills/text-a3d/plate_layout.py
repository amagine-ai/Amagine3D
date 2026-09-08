"""Deterministic, scale-free print-plate layout for multipart CAD exports.

The packer operates on bounding boxes in an already selected print orientation.
Optional quarter turns around the build axis preserve that orientation. Geometry
is never scaled. Planning can use proposed bounds before detailed modeling.
"""

from __future__ import annotations

import math
import argparse
import json
from pathlib import Path
from typing import Mapping


_EPSILON = 1e-9


class PlateLayoutError(ValueError):
    """Raised when a profile-bound single-plate layout cannot be proven."""

    def __init__(self, message: str, *, kind: str = "invalid-input"):
        super().__init__(message)
        self.kind = kind


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlateLayoutError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise PlateLayoutError(f"{label} must be a finite number")
    return result


def _rect_from_polygon(polygon, label: str) -> list[float]:
    try:
        points = [
            (_finite(point[0], label), _finite(point[1], label))
            for point in polygon
        ]
    except (IndexError, TypeError) as error:
        raise PlateLayoutError(f"{label} must be an XY polygon") from error
    if len(points) < 4:
        raise PlateLayoutError(f"{label} must contain a rectangular boundary")
    if points[0] == points[-1]:
        points = points[:-1]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    bounds = [min(xs), min(ys), max(xs), max(ys)]
    corners = {
        (bounds[0], bounds[1]),
        (bounds[0], bounds[3]),
        (bounds[2], bounds[1]),
        (bounds[2], bounds[3]),
    }
    if len(points) != 4 or set(points) != corners:
        raise PlateLayoutError(
            f"{label} is not an axis-aligned rectangle; bbox shelf packing "
            "cannot prove that every part stays on the bed"
        )
    if bounds[2] - bounds[0] <= _EPSILON or bounds[3] - bounds[1] <= _EPSILON:
        raise PlateLayoutError(f"{label} has no usable area")
    return bounds


def _profile_limits(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise PlateLayoutError(
            "a bound printer profile is required for a proven single-plate layout"
        )
    machine = profile.get("machine")
    if not isinstance(machine, dict):
        raise PlateLayoutError("printer profile machine limits are missing")
    tool = machine.get("selected_tool")
    if not isinstance(tool, dict):
        raise PlateLayoutError("printer profile selected_tool limits are missing")
    bounds = _rect_from_polygon(
        tool.get("polygon_mm"), "printer profile selected_tool.polygon_mm"
    )
    height = _finite(
        tool.get("height_mm"), "printer profile selected_tool.height_mm"
    )
    if height <= _EPSILON:
        raise PlateLayoutError("printer profile selected_tool.height_mm must be positive")
    exclusions = []
    raw_exclusions = machine.get("excluded_polygons_mm", [])
    if not isinstance(raw_exclusions, list):
        raise PlateLayoutError("printer profile excluded_polygons_mm must be a list")
    for index, polygon in enumerate(raw_exclusions):
        exclusions.append(
            _rect_from_polygon(
                polygon,
                f"printer profile excluded_polygons_mm[{index}]",
            )
        )
    return {
        "bounds_mm": [round(value, 5) for value in bounds],
        "excluded_bounds_mm": [
            [round(value, 5) for value in exclusion]
            for exclusion in exclusions
        ],
        "height_mm": round(height, 5),
        "profile_id": profile.get("id"),
        "size_mm": [
            round(bounds[2] - bounds[0], 5),
            round(bounds[3] - bounds[1], 5),
        ],
    }


def _normalize_bboxes(bboxes: Mapping[str, Mapping]) -> dict[str, dict]:
    if not isinstance(bboxes, Mapping) or not bboxes:
        raise PlateLayoutError("at least one part bbox is required")
    normalized = {}
    for part_name, bbox in bboxes.items():
        if not isinstance(part_name, str) or not part_name:
            raise PlateLayoutError("every plate part must have a non-empty name")
        if not isinstance(bbox, Mapping):
            raise PlateLayoutError(f"part {part_name!r} bbox must be an object")
        try:
            minimum = [
                _finite(value, f"part {part_name!r} bbox minimum")
                for value in bbox["min"]
            ]
            maximum = [
                _finite(value, f"part {part_name!r} bbox maximum")
                for value in bbox["max"]
            ]
        except (KeyError, TypeError) as error:
            raise PlateLayoutError(
                f"part {part_name!r} bbox requires min/max XYZ arrays"
            ) from error
        if len(minimum) != 3 or len(maximum) != 3:
            raise PlateLayoutError(
                f"part {part_name!r} bbox requires min/max XYZ arrays"
            )
        size = [maximum[index] - minimum[index] for index in range(3)]
        if any(value <= _EPSILON for value in size):
            raise PlateLayoutError(f"part {part_name!r} bbox has no usable volume")
        normalized[part_name] = {
            "max": maximum,
            "min": minimum,
            "size": size,
        }
    return normalized


def _intersects(left: list[float], right: list[float]) -> bool:
    return (
        left[0] < right[2] - _EPSILON
        and left[2] > right[0] + _EPSILON
        and left[1] < right[3] - _EPSILON
        and left[3] > right[1] + _EPSILON
    )


def _available_x(
    cursor: float,
    y: float,
    width: float,
    depth: float,
    bed: list[float],
    exclusions: list[list[float]],
    spacing: float,
) -> float | None:
    x = max(cursor, bed[0])
    while x + width <= bed[2] + _EPSILON:
        candidate = [x, y, x + width, y + depth]
        collisions = [item for item in exclusions if _intersects(candidate, item)]
        if not collisions:
            return x
        x = max(item[2] for item in collisions) + spacing
    return None


def _orderings(parts: dict[str, dict]) -> list[list[str]]:
    strategies = (
        lambda item: (-item[1]["size"][1], -item[1]["size"][0], item[0]),
        lambda item: (-item[1]["size"][0], -item[1]["size"][1], item[0]),
        lambda item: (
            -(item[1]["size"][0] * item[1]["size"][1]),
            -max(item[1]["size"][:2]),
            item[0],
        ),
    )
    result = []
    seen = set()
    for key in strategies:
        order = tuple(name for name, _ in sorted(parts.items(), key=key))
        if order not in seen:
            result.append(list(order))
            seen.add(order)
    return result


def _pack_order(
    order: list[str],
    parts: dict[str, dict],
    limits: dict,
    spacing: float,
) -> dict | None:
    bed = [float(value) for value in limits["bounds_mm"]]
    exclusions = [
        [float(value) for value in item]
        for item in limits["excluded_bounds_mm"]
    ]
    shelves: list[dict] = []
    placements = {}
    for part_name in order:
        width, depth, _ = parts[part_name]["size"]
        selected = None
        for shelf_index, shelf in enumerate(shelves):
            if depth > shelf["height"] + _EPSILON:
                continue
            x = _available_x(
                shelf["cursor"],
                shelf["y"],
                width,
                depth,
                bed,
                exclusions,
                spacing,
            )
            if x is not None:
                selected = (shelf_index, x, shelf["y"])
                break
        if selected is None:
            minimum_y = (
                bed[1]
                if not shelves
                else max(shelf["y"] + shelf["height"] for shelf in shelves)
                + spacing
            )
            y_candidates = {minimum_y}
            y_candidates.update(
                exclusion[3] + spacing
                for exclusion in exclusions
                if exclusion[3] + spacing >= minimum_y - _EPSILON
            )
            for y in sorted(y_candidates):
                if y + depth > bed[3] + _EPSILON:
                    continue
                x = _available_x(
                    bed[0], y, width, depth, bed, exclusions, spacing
                )
                if x is None:
                    continue
                shelves.append({
                    "cursor": x,
                    "height": depth,
                    "parts": [],
                    "y": y,
                })
                selected = (len(shelves) - 1, x, y)
                break
        if selected is None:
            return None
        shelf_index, x, y = selected
        shelf = shelves[shelf_index]
        shelf["cursor"] = x + width + spacing
        shelf["parts"].append(part_name)
        placements[part_name] = {
            "plate_bbox_xy_mm": [x, y, x + width, y + depth],
            "shelf": shelf_index,
        }
    return {"order": order, "placements": placements, "shelves": shelves}


def _subtract_rectangle(free: list[list[float]], occupied: list[float]) -> list[list[float]]:
    """Keep maximal free rectangles; overlapping free regions are intentional."""
    split = []
    for rect in free:
        if not _intersects(rect, occupied):
            split.append(rect)
            continue
        x0, y0, x1, y1 = rect
        ox0, oy0, ox1, oy1 = occupied
        if x0 < ox0 < x1:
            split.append([x0, y0, ox0, y1])
        if x0 < ox1 < x1:
            split.append([ox1, y0, x1, y1])
        if y0 < oy0 < y1:
            split.append([x0, y0, x1, oy0])
        if y0 < oy1 < y1:
            split.append([x0, oy1, x1, y1])
    unique = sorted(set(tuple(rect) for rect in split))
    return [
        list(rect) for rect in unique
        if not any(
            other != rect
            and other[0] <= rect[0] and other[1] <= rect[1]
            and other[2] >= rect[2] and other[3] >= rect[3]
            for other in unique
        )
    ]


def _pack_free_rectangles(order: list[str], parts: dict, limits: dict, spacing: float, *, allow_rotation: bool = False) -> dict | None:
    """Reuse gaps above short parts that a shelf cursor cannot revisit.

    Reserve spacing on each footprint's +X/+Y edges, extending the bed by that
    same amount so touching a bed edge remains legal. Optional Z quarter turns
    preserve build contact and never alter the part's scale.
    """
    x0, y0, x1, y1 = limits["bounds_mm"]
    free = [[x0, y0, x1 + spacing, y1 + spacing]]
    for ex0, ey0, ex1, ey1 in limits["excluded_bounds_mm"]:
        free = _subtract_rectangle(free, [ex0, ey0, ex1 + spacing, ey1 + spacing])
    placements = {}
    for name in order:
        width, depth, _ = parts[name]["size"]
        poses = [(width, depth, 0)]
        if allow_rotation and abs(width - depth) > _EPSILON:
            poses.append((depth, width, 90))
        choices = [
            (rect, w, d, rotation) for rect in free for w, d, rotation in poses
            if w + spacing <= rect[2] - rect[0] + _EPSILON
            and d + spacing <= rect[3] - rect[1] + _EPSILON
        ]
        if not choices:
            return None
        rect, width, depth, rotation = min(choices, key=lambda c: (
            c[0][1], c[0][0],
            min(c[0][2] - c[0][0] - c[1], c[0][3] - c[0][1] - c[2]), c[3],
        ))
        x, y = rect[:2]
        placements[name] = {
            "plate_bbox_xy_mm": [x, y, x + width, y + depth],
            "shelf": None,
            "rotation_z_degrees": rotation,
        }
        free = _subtract_rectangle(free, [x, y, x + width + spacing, y + depth + spacing])
    return {
        "order": order,
        "placements": placements,
        "shelves": [],
        "strategy": "deterministic-bbox-maxrects",
        "score": (
            round(max(p["plate_bbox_xy_mm"][3] for p in placements.values()) - y0, 9),
            round(max(p["plate_bbox_xy_mm"][2] for p in placements.values()) - x0, 9),
            tuple(order),
        ),
    }


def pack_bboxes(
    bboxes: Mapping[str, Mapping],
    profile: dict,
    *,
    spacing_mm: float = 5.0,
    allow_rotation: bool = False,
) -> dict:
    """Prove one plate layout; optionally allow 90-degree build-axis rotations."""
    spacing = _finite(spacing_mm, "plate spacing_mm")
    if spacing < 0:
        raise PlateLayoutError("plate spacing_mm must be non-negative")
    parts = _normalize_bboxes(bboxes)
    limits = _profile_limits(profile)
    bed_width, bed_depth = limits["size_mm"]
    height_limit = limits["height_mm"]
    oversized = []
    for name, part in sorted(parts.items()):
        width, depth, height = part["size"]
        failures = []
        rotated_fits = allow_rotation and depth <= bed_width + _EPSILON and width <= bed_depth + _EPSILON
        if width > bed_width + _EPSILON and not rotated_fits:
            failures.append(f"width {width:.5g}>{bed_width:.5g}")
        if depth > bed_depth + _EPSILON and not rotated_fits:
            failures.append(f"depth {depth:.5g}>{bed_depth:.5g}")
        if height > height_limit + _EPSILON:
            failures.append(f"height {height:.5g}>{height_limit:.5g}")
        if failures:
            oversized.append(f"{name}: " + ", ".join(failures))
    if oversized:
        raise PlateLayoutError(
            "single-plate layout failed at scale=1; part exceeds the bound "
            f"printer volume ({'; '.join(oversized)}); scaling is disabled",
            kind="part-exceeds-volume",
        )

    candidates = []
    for order in _orderings(parts):
        candidate = _pack_order(order, parts, limits, spacing)
        if candidate is not None:
            used_max_x = max(
                item["plate_bbox_xy_mm"][2]
                for item in candidate["placements"].values()
            )
            used_max_y = max(
                item["plate_bbox_xy_mm"][3]
                for item in candidate["placements"].values()
            )
            bed_min_x, bed_min_y, _, _ = limits["bounds_mm"]
            candidate["score"] = (
                round(used_max_y - bed_min_y, 9),
                round(used_max_x - bed_min_x, 9),
                tuple(candidate["order"]),
            )
            candidates.append(candidate)
    if not candidates:
        sizes = ", ".join(
            f"{name}={part['size'][0]:.5g}x{part['size'][1]:.5g} mm"
            for name, part in sorted(parts.items())
        )
        for order in _orderings(parts):
            candidate = _pack_free_rectangles(order, parts, limits, spacing, allow_rotation=allow_rotation)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            raise PlateLayoutError(
                "single-plate heuristic layout failed at scale=1: no placement found "
                f"for parts [{sizes}] on usable bed {bed_width:.5g}x"
                f"{bed_depth:.5g} mm with {spacing:.5g} mm spacing; scaling is disabled. "
                "This is not proof of infeasibility. Review packing or plate grouping "
                "without changing the design dimensions.",
                kind="layout-not-found",
            )

    selected = min(candidates, key=lambda item: item["score"])
    transforms = {}
    rotations = {}
    part_records = {}
    for part_name, source in parts.items():
        placement = selected["placements"][part_name]
        x0, y0, x1, y1 = placement["plate_bbox_xy_mm"]
        rotation = placement.get("rotation_z_degrees", 0)
        rotated_min = [-source["max"][1], source["min"][0]] if rotation else source["min"][:2]
        translate = [
            x0 - rotated_min[0],
            y0 - rotated_min[1],
            -source["min"][2],
        ]
        transforms[part_name] = [round(value, 5) for value in translate]
        rotations[part_name] = [0.0, 0.0, float(rotation)]
        part_records[part_name] = {
            "plate_bbox_mm": {
                "max": [round(x1, 5), round(y1, 5), round(source["size"][2], 5)],
                "min": [round(x0, 5), round(y0, 5), 0.0],
                "size": [round(x1-x0, 5), round(y1-y0, 5), round(source["size"][2], 5)],
            },
            "shelf": placement["shelf"],
            "source_bbox_mm": {
                "max": [round(value, 5) for value in source["max"]],
                "min": [round(value, 5) for value in source["min"]],
                "size": [round(value, 5) for value in source["size"]],
            },
            "translate_mm": transforms[part_name],
            **({"rotate_degrees_xyz": rotations[part_name]} if rotation else {}),
        }

    names = sorted(part_records)
    bbox_overlaps = []
    for index, left_name in enumerate(names):
        left = part_records[left_name]["plate_bbox_mm"]
        left_xy = [left["min"][0], left["min"][1], left["max"][0], left["max"][1]]
        for right_name in names[index + 1:]:
            right = part_records[right_name]["plate_bbox_mm"]
            right_xy = [
                right["min"][0], right["min"][1], right["max"][0], right["max"][1]
            ]
            if _intersects(left_xy, right_xy):
                bbox_overlaps.append(f"{left_name}&{right_name}")
    if bbox_overlaps:
        raise PlateLayoutError(
            "internal plate packing error: overlapping bbox pairs "
            + ", ".join(bbox_overlaps)
        )

    shelves = [
        {
            "height_mm": round(item["height"], 5),
            "index": index,
            "parts": list(item["parts"]),
            "y_mm": round(item["y"], 5),
        }
        for index, item in enumerate(selected["shelves"])
    ]
    return {
        "auto_scale": False,
        "bed": limits,
        "bbox_overlaps": bbox_overlaps,
        "fits": True,
        "order": list(selected["order"]),
        "parts": part_records,
        "scale": 1.0,
        "shelves": shelves,
        "spacing_mm": round(spacing, 5),
        "strategy": selected.get("strategy", "deterministic-bbox-shelf"),
        "transforms": transforms,
        "rotations": rotations,
    }


def plan_plates(bboxes: Mapping[str, Mapping], profile: dict, *, spacing_mm: float = 5.0,
                edge_margin_mm: float = 0.0, max_plates: int = 1) -> dict:
    """Plan proposed or measured part bounds without changing design dimensions.

    A proven layout is a geometric plan, not an export or manufacturing audit.
    max_plates is an explicit manufacturing constraint; failure to find a layout
    is reported separately from a part exceeding the selected printer volume.
    """
    from copy import deepcopy
    if isinstance(max_plates, bool) or not isinstance(max_plates, int) or max_plates < 1:
        raise PlateLayoutError("max_plates must be a positive integer")
    margin = _finite(edge_margin_mm, "edge margin")
    if margin < 0:
        raise PlateLayoutError("edge_margin_mm must be non-negative")
    parts = _normalize_bboxes(bboxes)
    selected_profile = deepcopy(profile)
    limits = _profile_limits(profile)
    x0, y0, x1, y1 = limits["bounds_mm"]
    if x0 + margin >= x1 - margin or y0 + margin >= y1 - margin:
        raise PlateLayoutError("edge margin consumes the usable bed")
    selected_profile["machine"]["selected_tool"]["polygon_mm"] = [
        [x0+margin, y0+margin], [x1-margin, y0+margin],
        [x1-margin, y1-margin], [x0+margin, y1-margin],
    ]
    result = {"schema": "evidence-print-layout-plan/v1", "profileId": profile.get("id"),
              "maxPlates": max_plates, "edgeMarginMm": margin, "scale": 1.0,
              "manufacturingValidated": False, "plates": [], "issues": []}
    # Check individuals first so one oversized part is not mislabeled a packing failure.
    for name in sorted(parts):
        try:
            pack_bboxes({name: bboxes[name]}, selected_profile, spacing_mm=spacing_mm, allow_rotation=True)
        except PlateLayoutError as error:
            result["issues"].append({"kind": error.kind, "part": name, "message": str(error)})
    if result["issues"]:
        return {**result, "pass": False, "status": "part-layout-failed"}
    groups: list[dict] = []
    for name in _orderings(parts)[0]:
        for group in groups:
            try:
                pack_bboxes({**group, name: bboxes[name]}, selected_profile, spacing_mm=spacing_mm, allow_rotation=True)
            except PlateLayoutError:
                continue
            group[name] = bboxes[name]
            break
        else:
            groups.append({name: bboxes[name]})
    # Also try the whole set: different orderings can beat incremental grouping.
    try:
        whole = pack_bboxes(bboxes, selected_profile, spacing_mm=spacing_mm, allow_rotation=True)
    except PlateLayoutError:
        pass
    else:
        result["plates"] = [{"index": 1, "parts": sorted(bboxes), "layout": whole}]
        return {**result, "pass": True, "status": "layout-found"}
    result["plates"] = [
        {"index": index, "parts": sorted(group),
         "layout": pack_bboxes(group, selected_profile, spacing_mm=spacing_mm, allow_rotation=True)}
        for index, group in enumerate(groups, 1)
    ]
    fits = len(groups) <= max_plates
    if not fits:
        result["issues"].append({"kind": "layout-not-found",
            "message": f"Heuristic found a {len(groups)}-plate plan, exceeding max_plates={max_plates}; this is not proof that a better layout is impossible."})
    return {**result, "pass": fits, "status": "layout-found" if fits else "layout-not-found"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan print footprints before detailed construction; recheck measured bounds at export.")
    parser.add_argument("bounds", type=Path, help="JSON object mapping part IDs to min/max XYZ bounds")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--max-plates", type=int, default=1)
    parser.add_argument("--spacing-mm", type=float, default=5.0)
    parser.add_argument("--edge-margin-mm", type=float, default=0.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = plan_plates(json.loads(args.bounds.read_text()), json.loads(args.profile.read_text()),
                             max_plates=args.max_plates, spacing_mm=args.spacing_mm, edge_margin_mm=args.edge_margin_mm)
    except (ValueError, OSError) as error:
        result = {"schema": "evidence-print-layout-plan/v1", "pass": False, "status": "invalid-input", "issues": [{"message": str(error)}]}
    payload = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.write_text(payload)
    print(payload, end="")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
