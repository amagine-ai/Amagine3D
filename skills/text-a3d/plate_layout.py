"""Deterministic, scale-free print-plate layout for multipart CAD exports.

The packer deliberately operates on axis-aligned bounding boxes.  It never
rotates or scales geometry: its only output per part is a rigid translation.
This makes the layout safe to apply to the BRep objects which are later used
for both the combined STL and the multipart 3MF package.
"""

from __future__ import annotations

import math
from typing import Mapping


_EPSILON = 1e-9


class PlateLayoutError(ValueError):
    """Raised when a profile-bound single-plate layout cannot be proven."""


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


def _pack_free_rectangles(order: list[str], parts: dict, limits: dict, spacing: float) -> dict | None:
    """Reuse gaps above short parts that a shelf cursor cannot revisit.

    Reserve spacing on each footprint's +X/+Y edges, extending the bed by that
    same amount so touching a bed edge remains legal. Never rotate or scale.
    """
    x0, y0, x1, y1 = limits["bounds_mm"]
    free = [[x0, y0, x1 + spacing, y1 + spacing]]
    for ex0, ey0, ex1, ey1 in limits["excluded_bounds_mm"]:
        free = _subtract_rectangle(free, [ex0, ey0, ex1 + spacing, ey1 + spacing])
    placements = {}
    for name in order:
        width, depth, _ = parts[name]["size"]
        choices = [
            rect for rect in free
            if width + spacing <= rect[2] - rect[0] + _EPSILON
            and depth + spacing <= rect[3] - rect[1] + _EPSILON
        ]
        if not choices:
            return None
        rect = min(choices, key=lambda r: (r[1], r[0], r[2], r[3]))
        x, y = rect[:2]
        placements[name] = {
            "plate_bbox_xy_mm": [x, y, x + width, y + depth],
            "shelf": None,
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
) -> dict:
    """Pack bbox footprints on one profile-bound plate using translations only."""
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
        if width > bed_width + _EPSILON:
            failures.append(f"width {width:.5g}>{bed_width:.5g}")
        if depth > bed_depth + _EPSILON:
            failures.append(f"depth {depth:.5g}>{bed_depth:.5g}")
        if height > height_limit + _EPSILON:
            failures.append(f"height {height:.5g}>{height_limit:.5g}")
        if failures:
            oversized.append(f"{name}: " + ", ".join(failures))
    if oversized:
        raise PlateLayoutError(
            "single-plate layout failed at scale=1; part exceeds the bound "
            f"printer volume ({'; '.join(oversized)}); scaling is disabled"
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
            candidate = _pack_free_rectangles(order, parts, limits, spacing)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            raise PlateLayoutError(
                "single-plate heuristic layout failed at scale=1: no placement found "
                f"for parts [{sizes}] on usable bed {bed_width:.5g}x"
                f"{bed_depth:.5g} mm with {spacing:.5g} mm spacing; scaling is disabled. "
                "This is not proof of infeasibility. Review packing or plate grouping "
                "without changing the design dimensions."
            )

    selected = min(candidates, key=lambda item: item["score"])
    transforms = {}
    part_records = {}
    for part_name, source in parts.items():
        placement = selected["placements"][part_name]
        x0, y0, x1, y1 = placement["plate_bbox_xy_mm"]
        translate = [
            x0 - source["min"][0],
            y0 - source["min"][1],
            -source["min"][2],
        ]
        transforms[part_name] = [round(value, 5) for value in translate]
        part_records[part_name] = {
            "plate_bbox_mm": {
                "max": [round(x1, 5), round(y1, 5), round(source["size"][2], 5)],
                "min": [round(x0, 5), round(y0, 5), 0.0],
                "size": [round(value, 5) for value in source["size"]],
            },
            "shelf": placement["shelf"],
            "source_bbox_mm": {
                "max": [round(value, 5) for value in source["max"]],
                "min": [round(value, 5) for value in source["min"]],
                "size": [round(value, 5) for value in source["size"]],
            },
            "translate_mm": transforms[part_name],
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
    }
