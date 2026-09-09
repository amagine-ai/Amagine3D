"""Backend-neutral physical proof for self-tapping screw interfaces.

The semantic contract supplies every witness dimension and axis.  This module
only measures final semantic part meshes; it never chooses a screw size, wall,
clearance, or engagement target.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import trimesh


class SelfTappingGeometryError(ValueError):
    """Raised when required self-tapping witness geometry does not pass."""


RECIPE_BOUNDS_TOLERANCE_MM = 0.05


def _axis_triangle_intersections(
    mesh: trimesh.Trimesh,
    origin: np.ndarray,
    direction: np.ndarray,
    *,
    tolerance: float = 1e-8,
) -> list[float]:
    triangles = np.asarray(mesh.triangles, dtype=float)
    if len(triangles) == 0:
        return []
    direction = np.asarray(direction, dtype=float)
    direction = direction / np.linalg.norm(direction)
    origin = np.asarray(origin, dtype=float)
    edge_a = triangles[:, 1] - triangles[:, 0]
    edge_b = triangles[:, 2] - triangles[:, 0]
    cross_direction_b = np.cross(np.broadcast_to(direction, edge_b.shape), edge_b)
    determinant = np.einsum("ij,ij->i", edge_a, cross_direction_b)
    usable = np.abs(determinant) > tolerance
    inverse = np.zeros_like(determinant)
    inverse[usable] = 1.0 / determinant[usable]
    offset = origin - triangles[:, 0]
    barycentric_a = inverse * np.einsum("ij,ij->i", offset, cross_direction_b)
    cross_offset_a = np.cross(offset, edge_a)
    barycentric_b = inverse * np.einsum(
        "ij,ij->i",
        np.broadcast_to(direction, cross_offset_a.shape),
        cross_offset_a,
    )
    distance = inverse * np.einsum("ij,ij->i", edge_b, cross_offset_a)
    hit = (
        usable
        & (barycentric_a >= -tolerance)
        & (barycentric_b >= -tolerance)
        & (barycentric_a + barycentric_b <= 1.0 + tolerance)
    )
    distances = sorted(float(item) for item in distance[hit])
    unique: list[float] = []
    for item in distances:
        if not unique or abs(item - unique[-1]) > 1e-6:
            unique.append(item)
    return unique


def _probe_result_volume(result: Any) -> float:
    if result is None:
        return 0.0
    if isinstance(result, (list, tuple)):
        return sum(_probe_result_volume(item) for item in result)
    if isinstance(result, trimesh.Scene):
        return sum(
            _probe_result_volume(item)
            for item in result.geometry.values()
        )
    if isinstance(result, trimesh.Trimesh):
        if result.is_empty or len(result.faces) == 0:
            return 0.0
        return abs(float(result.volume))
    raise SelfTappingGeometryError(
        f"probe boolean returned unsupported result {type(result).__name__}"
    )


def _intersection_volume(
    body: trimesh.Trimesh,
    probe: trimesh.Trimesh,
    context: str,
) -> float:
    try:
        result = trimesh.boolean.intersection(
            [body, probe],
            engine="manifold",
            check_volume=True,
        )
    except Exception as error:
        raise SelfTappingGeometryError(
            f"{context} intersection probe failed: {error}"
        ) from error
    return _probe_result_volume(result)


def _missing_volume(
    witness: trimesh.Trimesh,
    body: trimesh.Trimesh,
    context: str,
) -> float:
    try:
        result = trimesh.boolean.difference(
            [witness, body],
            engine="manifold",
            check_volume=True,
        )
    except Exception as error:
        raise SelfTappingGeometryError(
            f"{context} containment probe failed: {error}"
        ) from error
    return _probe_result_volume(result)


def _probe_tolerance(probe: trimesh.Trimesh) -> float:
    return max(1e-5, abs(float(probe.volume)) * 1e-9)


def _recipe_volume_tolerance(expected_mm3: float) -> float:
    """Allow exporter/tessellation noise without introducing a product target."""

    return max(0.05, abs(expected_mm3) * 0.005)


def _feature_volume(
    feature_records: dict[str, Any],
    feature_id: Any,
) -> float | None:
    if not isinstance(feature_id, str):
        return None
    record = feature_records.get(feature_id)
    if not isinstance(record, dict):
        return None
    value = record.get("volume_mm3")
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        return None
    return float(value)


def _feature_bounds(
    feature_records: dict[str, Any],
    feature_id: Any,
) -> tuple[np.ndarray, np.ndarray] | None:
    if not isinstance(feature_id, str):
        return None
    record = feature_records.get(feature_id)
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
    if (
        not np.isfinite(low).all()
        or not np.isfinite(high).all()
        or np.any(high < low)
    ):
        return None
    return low, high


def _cylinder_bounds(
    *,
    origin: np.ndarray,
    direction: np.ndarray,
    radius: float,
    axial_start: float,
    axial_end: float,
) -> tuple[np.ndarray, np.ndarray]:
    start = origin + direction * axial_start
    end = origin + direction * axial_end
    radial = radius * np.sqrt(np.maximum(0.0, 1.0 - direction**2))
    return np.minimum(start, end) - radial, np.maximum(start, end) + radial


def _union_bounds(
    *items: tuple[np.ndarray, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.min(np.stack([item[0] for item in items]), axis=0),
        np.max(np.stack([item[1] for item in items]), axis=0),
    )


def _bounds_match(
    observed: tuple[np.ndarray, np.ndarray] | None,
    expected: tuple[np.ndarray, np.ndarray],
) -> bool:
    return observed is not None and bool(
        np.allclose(
            np.concatenate(observed),
            np.concatenate(expected),
            atol=RECIPE_BOUNDS_TOLERANCE_MM,
            rtol=0.0,
        )
    )


def _bounds_record(
    bounds: tuple[np.ndarray, np.ndarray] | None,
) -> dict[str, list[float]] | None:
    if bounds is None:
        return None
    low, high = bounds
    return {
        "min": [round(float(value), 9) for value in low],
        "max": [round(float(value), 9) for value in high],
        "size": [round(float(value), 9) for value in high - low],
    }


def _segment_cylinder(
    radius: float,
    start: np.ndarray,
    end: np.ndarray,
) -> trimesh.Trimesh:
    return trimesh.creation.cylinder(
        radius=radius,
        segment=np.asarray([start, end], dtype=float),
        sections=96,
    )


def _segment_annulus(
    inner_radius: float,
    outer_radius: float,
    start: np.ndarray,
    end: np.ndarray,
) -> trimesh.Trimesh:
    return trimesh.creation.annulus(
        r_min=inner_radius,
        r_max=outer_radius,
        segment=np.asarray([start, end], dtype=float),
        sections=96,
    )


def _probe_fastener(
    interface_id: str,
    fastener: dict[str, Any],
    part_meshes: dict[str, trimesh.Trimesh],
    feature_records: dict[str, Any],
) -> dict[str, Any]:
    fastener_id = fastener["id"]
    key = f"{interface_id}/{fastener_id}"
    axis = fastener["axis"]
    origin = np.asarray(axis["originMm"], dtype=float)
    direction = np.asarray(axis["direction"], dtype=float)
    direction = direction / np.linalg.norm(direction)
    cover = fastener["cover"]
    receiver = fastener["receiver"]
    cover_mesh = part_meshes[cover["partId"]]
    receiver_mesh = part_meshes[receiver["partId"]]
    clearance_diameter = float(cover["diameterMm"])
    pilot_diameter = float(receiver["diameterMm"])
    cover_thickness = float(cover["thicknessMm"])
    cutter_overshoot = float(fastener["cutterOvershootMm"])
    pilot_depth = (
        float(receiver["engagementMm"])
        + float(receiver["tipClearanceMm"])
    )

    clearance_radius = clearance_diameter / 2
    pilot_radius = pilot_diameter / 2
    expected_clearance_tool_volume = (
        math.pi
        * clearance_radius**2
        * (cover_thickness + 2 * cutter_overshoot)
    )
    head_diameter = cover.get("headRecessDiameterMm")
    head_depth = cover.get("headRecessDepthMm")
    if head_diameter is not None and head_depth is not None:
        head_radius = float(head_diameter) / 2
        expected_clearance_tool_volume += (
            math.pi
            * (head_radius**2 - clearance_radius**2)
            * (float(head_depth) + cutter_overshoot)
        )
    expected_pilot_tool_volume = (
        math.pi
        * pilot_radius**2
        * (pilot_depth + cutter_overshoot)
    )
    expected_boss_volume = (
        math.pi
        * (float(receiver["bossOuterDiameterMm"]) / 2) ** 2
        * (pilot_depth + float(receiver["closedEndMm"]))
    )
    recipe_expected_volumes = {
        "clearanceCutter": expected_clearance_tool_volume,
        "pilotCutter": expected_pilot_tool_volume,
        "receiverBoss": expected_boss_volume,
    }
    recipe_observed_volumes = {
        "clearanceCutter": _feature_volume(
            feature_records,
            cover.get("featureId"),
        ),
        "pilotCutter": _feature_volume(
            feature_records,
            receiver.get("featureId"),
        ),
        "receiverBoss": _feature_volume(
            feature_records,
            receiver.get("bossFeatureId"),
        ),
    }
    recipe_tolerances = {
        name: _recipe_volume_tolerance(value)
        for name, value in recipe_expected_volumes.items()
    }
    expected_clearance_bounds = _cylinder_bounds(
        origin=origin,
        direction=direction,
        radius=clearance_radius,
        axial_start=-cover_thickness - cutter_overshoot,
        axial_end=cutter_overshoot,
    )
    if head_diameter is not None and head_depth is not None:
        expected_clearance_bounds = _union_bounds(
            expected_clearance_bounds,
            _cylinder_bounds(
                origin=origin,
                direction=direction,
                radius=float(head_diameter) / 2,
                axial_start=-cover_thickness - cutter_overshoot,
                axial_end=-cover_thickness + float(head_depth),
            ),
        )
    recipe_expected_bounds = {
        "clearanceCutter": expected_clearance_bounds,
        "pilotCutter": _cylinder_bounds(
            origin=origin,
            direction=direction,
            radius=pilot_radius,
            axial_start=-cutter_overshoot,
            axial_end=pilot_depth,
        ),
        "receiverBoss": _cylinder_bounds(
            origin=origin,
            direction=direction,
            radius=float(receiver["bossOuterDiameterMm"]) / 2,
            axial_start=0.0,
            axial_end=pilot_depth + float(receiver["closedEndMm"]),
        ),
    }
    recipe_observed_bounds = {
        "clearanceCutter": _feature_bounds(
            feature_records,
            cover.get("featureId"),
        ),
        "pilotCutter": _feature_bounds(
            feature_records,
            receiver.get("featureId"),
        ),
        "receiverBoss": _feature_bounds(
            feature_records,
            receiver.get("bossFeatureId"),
        ),
    }
    axial_pad = 0.05
    cover_projection = np.einsum(
        "ij,j->i",
        np.asarray(cover_mesh.vertices, dtype=float) - origin,
        direction,
    )
    actual_cover_min = float(cover_projection.min())
    clearance_inset = min(0.02, clearance_diameter * 0.005)
    clearance_probe = _segment_cylinder(
        clearance_diameter / 2 - clearance_inset,
        origin + direction * (actual_cover_min - axial_pad),
        origin + direction * axial_pad,
    )
    clearance_overlap = _intersection_volume(
        cover_mesh,
        clearance_probe,
        f"self-tapping geometry {key} clearance",
    )

    cover_witness_base_radius = max(
        clearance_radius,
        float(head_diameter) / 2 if head_diameter is not None else 0.0,
    )
    cover_witness_inner_radius = (
        cover_witness_base_radius + RECIPE_BOUNDS_TOLERANCE_MM / 2
    )
    cover_witness_outer_radius = (
        cover_witness_base_radius + RECIPE_BOUNDS_TOLERANCE_MM
    )
    cover_axial_inset = min(
        RECIPE_BOUNDS_TOLERANCE_MM / 2,
        cover_thickness * 0.05,
    )
    cover_thickness_witness = _segment_annulus(
        cover_witness_inner_radius,
        cover_witness_outer_radius,
        origin - direction * (cover_thickness - cover_axial_inset),
        origin - direction * cover_axial_inset,
    )
    cover_thickness_missing = _missing_volume(
        cover_thickness_witness,
        cover_mesh,
        f"self-tapping geometry {key} cover thickness",
    )

    pilot_inset = min(0.02, pilot_diameter * 0.005)
    pilot_axial_inset = min(0.02, pilot_depth * 0.01)
    pilot_probe = _segment_cylinder(
        pilot_diameter / 2 - pilot_inset,
        origin + direction * pilot_axial_inset,
        origin + direction * (pilot_depth - pilot_axial_inset),
    )
    pilot_overlap = _intersection_volume(
        receiver_mesh,
        pilot_probe,
        f"self-tapping geometry {key} pilot",
    )

    minimum_boss_wall = float(receiver["minimumBossWallMm"])
    boss_radial_inset = min(0.05, minimum_boss_wall * 0.05)
    boss_wall_witness = _segment_annulus(
        pilot_diameter / 2 + boss_radial_inset,
        pilot_diameter / 2 + minimum_boss_wall - boss_radial_inset,
        origin + direction * pilot_axial_inset,
        origin + direction * (pilot_depth - pilot_axial_inset),
    )
    boss_wall_missing = _missing_volume(
        boss_wall_witness,
        receiver_mesh,
        f"self-tapping geometry {key} boss wall",
    )

    root_embed = float(receiver["minimumRootEmbedMm"])
    root_axial_inset = min(0.02, root_embed * 0.05)
    root_embed_witness = _segment_annulus(
        pilot_diameter / 2 + boss_radial_inset,
        pilot_diameter / 2 + minimum_boss_wall - boss_radial_inset,
        origin + direction * root_axial_inset,
        origin + direction * (root_embed - root_axial_inset),
    )
    root_embed_missing = _missing_volume(
        root_embed_witness,
        receiver_mesh,
        f"self-tapping geometry {key} receiver root embed",
    )

    closed_end = float(receiver["closedEndMm"])
    closed_axial_inset = min(0.02, closed_end * 0.05)
    closed_end_witness = _segment_cylinder(
        pilot_diameter / 2 - pilot_inset,
        origin + direction * (pilot_depth + closed_axial_inset),
        origin + direction * (pilot_depth + closed_end - closed_axial_inset),
    )
    closed_end_missing = _missing_volume(
        closed_end_witness,
        receiver_mesh,
        f"self-tapping geometry {key} blind end",
    )

    cover_land_witness: trimesh.Trimesh | None = None
    cover_land_missing: float | None = None
    cover_land_name: str | None = None
    head_recess_probe: trimesh.Trimesh | None = None
    head_recess_overlap: float | None = None
    if "headRecessDiameterMm" in cover:
        head_diameter = float(cover["headRecessDiameterMm"])
        recess_depth = float(cover["headRecessDepthMm"])
        recess_radial_inset = min(0.02, head_diameter * 0.005)
        recess_axial_inset = min(0.02, recess_depth * 0.01)
        # Original cutter evidence can remain correct after later material edits.
        # The final cover must still leave the declared head recess volume open.
        head_recess_probe = _segment_cylinder(
            head_diameter / 2 - recess_radial_inset,
            origin - direction * (cover_thickness - recess_axial_inset),
            origin - direction * (cover_thickness - recess_depth + recess_axial_inset),
        )
        head_recess_overlap = _intersection_volume(
            cover_mesh,
            head_recess_probe,
            f"self-tapping geometry {key} head recess",
        )
        minimum_cover_land = float(cover["minimumResidualWallMm"])
        radial_band = (head_diameter - clearance_diameter) / 2
        land_radial_inset = min(0.02, radial_band * 0.2)
        land_axial_inset = min(0.02, minimum_cover_land * 0.05)
        cover_land_witness = _segment_annulus(
            clearance_diameter / 2 + land_radial_inset,
            head_diameter / 2 - land_radial_inset,
            origin - direction * (minimum_cover_land - land_axial_inset),
            origin - direction * land_axial_inset,
        )
        cover_land_name = "head_recess_floor_is_complete"
        cover_land_missing = _missing_volume(
            cover_land_witness,
            cover_mesh,
            f"self-tapping geometry {key} head recess floor",
        )

    checks = {
        "clearance_cutter_volume_matches_recipe_controls": (
            recipe_observed_volumes["clearanceCutter"] is not None
            and abs(
                recipe_observed_volumes["clearanceCutter"]
                - recipe_expected_volumes["clearanceCutter"]
            )
            <= recipe_tolerances["clearanceCutter"]
        ),
        "clearance_cutter_bounds_match_recipe_controls": _bounds_match(
            recipe_observed_bounds["clearanceCutter"],
            recipe_expected_bounds["clearanceCutter"],
        ),
        "pilot_cutter_volume_matches_recipe_controls": (
            recipe_observed_volumes["pilotCutter"] is not None
            and abs(
                recipe_observed_volumes["pilotCutter"]
                - recipe_expected_volumes["pilotCutter"]
            )
            <= recipe_tolerances["pilotCutter"]
        ),
        "pilot_cutter_bounds_match_recipe_controls": _bounds_match(
            recipe_observed_bounds["pilotCutter"],
            recipe_expected_bounds["pilotCutter"],
        ),
        "receiver_boss_volume_matches_recipe_controls": (
            recipe_observed_volumes["receiverBoss"] is not None
            and abs(
                recipe_observed_volumes["receiverBoss"]
                - recipe_expected_volumes["receiverBoss"]
            )
            <= recipe_tolerances["receiverBoss"]
        ),
        "receiver_boss_bounds_match_recipe_controls": _bounds_match(
            recipe_observed_bounds["receiverBoss"],
            recipe_expected_bounds["receiverBoss"],
        ),
        "clearance_volume_is_open": clearance_overlap
        <= _probe_tolerance(clearance_probe),
        "cover_thickness_material_is_complete": cover_thickness_missing
        <= _probe_tolerance(cover_thickness_witness),
        "pilot_volume_is_open": pilot_overlap <= _probe_tolerance(pilot_probe),
        "receiver_boss_wall_is_complete": boss_wall_missing
        <= _probe_tolerance(boss_wall_witness),
        "receiver_root_embed_is_complete": root_embed_missing
        <= _probe_tolerance(root_embed_witness),
        "pilot_closed_end_is_complete": closed_end_missing
        <= _probe_tolerance(closed_end_witness),
    }
    if (
        cover_land_name is not None
        and cover_land_missing is not None
        and cover_land_witness is not None
    ):
        checks[cover_land_name] = (
            cover_land_missing <= _probe_tolerance(cover_land_witness)
        )
    if head_recess_probe is not None and head_recess_overlap is not None:
        checks["head_recess_volume_is_open"] = (
            head_recess_overlap <= _probe_tolerance(head_recess_probe)
        )
    clearance_axis_hits = _axis_triangle_intersections(
        cover_mesh,
        origin,
        direction,
    )
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "clearanceAxisIntersectionDistancesMm": clearance_axis_hits,
        "recipeControlsMm": {
            "coverThickness": cover_thickness,
            "cutterOvershoot": cutter_overshoot,
        },
        "recipeFeatureVolumesMm3": {
            "expected": {
                key: round(value, 9)
                for key, value in recipe_expected_volumes.items()
            },
            "observed": {
                key: round(value, 9) if value is not None else None
                for key, value in recipe_observed_volumes.items()
            },
        },
        "recipeFeatureBoundsMm": {
            "expected": {
                key: _bounds_record(value)
                for key, value in recipe_expected_bounds.items()
            },
            "observed": {
                key: _bounds_record(value)
                for key, value in recipe_observed_bounds.items()
            },
        },
        "measurementsMm3": {
            "clearanceOverlap": round(clearance_overlap, 9),
            "coverThicknessMissing": round(cover_thickness_missing, 9),
            "pilotOverlap": round(pilot_overlap, 9),
            "receiverBossWallMissing": round(boss_wall_missing, 9),
            "receiverClosedEndMissing": round(closed_end_missing, 9),
            "receiverRootEmbedMissing": round(root_embed_missing, 9),
            **(
                {"headRecessFloorMissing": round(cover_land_missing, 9)}
                if cover_land_missing is not None
                else {}
            ),
            **(
                {"headRecessOverlap": round(head_recess_overlap, 9)}
                if head_recess_overlap is not None
                else {}
            ),
        },
        "tolerancesMm3": {
            "recipeFeatureVolumes": recipe_tolerances,
            "clearance": _probe_tolerance(clearance_probe),
            "coverThickness": _probe_tolerance(cover_thickness_witness),
            "pilot": _probe_tolerance(pilot_probe),
            "receiverBossWall": _probe_tolerance(boss_wall_witness),
            "receiverClosedEnd": _probe_tolerance(closed_end_witness),
            "receiverRootEmbed": _probe_tolerance(root_embed_witness),
            **(
                {"headRecessFloor": _probe_tolerance(cover_land_witness)}
                if cover_land_witness is not None
                else {}
            ),
            **(
                {"headRecess": _probe_tolerance(head_recess_probe)}
                if head_recess_probe is not None
                else {}
            ),
        },
        "witnessVolumesMm3": {
            "clearance": round(float(clearance_probe.volume), 9),
            "coverThickness": round(float(cover_thickness_witness.volume), 9),
            "pilot": round(float(pilot_probe.volume), 9),
            "receiverBossWall": round(float(boss_wall_witness.volume), 9),
            "receiverClosedEnd": round(float(closed_end_witness.volume), 9),
            "receiverRootEmbed": round(float(root_embed_witness.volume), 9),
            **(
                {"headRecessFloor": round(float(cover_land_witness.volume), 9)}
                if cover_land_witness is not None
                else {}
            ),
            **(
                {"headRecess": round(float(head_recess_probe.volume), 9)}
                if head_recess_probe is not None
                else {}
            ),
        },
    }


def audit_self_tapping_geometry(
    scene: dict[str, Any],
    part_meshes: dict[str, trimesh.Trimesh],
    *,
    feature_records: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Measure every declared screw axis and retain independent failures."""

    results: dict[str, dict[str, Any]] = {}
    for interface in scene.get("interfaces", []):
        if not (
            isinstance(interface, dict)
            and interface.get("kind") == "self-tapping-screw"
        ):
            continue
        interface_id = interface.get("id")
        if not isinstance(interface_id, str):
            continue
        for fastener in interface.get("fasteners", []):
            if not isinstance(fastener, dict) or not isinstance(
                fastener.get("id"), str
            ):
                continue
            key = f"{interface_id}/{fastener['id']}"
            try:
                results[key] = _probe_fastener(
                    interface_id,
                    fastener,
                    part_meshes,
                    feature_records,
                )
            except Exception as error:
                results[key] = {
                    "checks": {},
                    "error": str(error),
                    "pass": False,
                }
    return results


def require_self_tapping_geometry(
    scene: dict[str, Any],
    part_meshes: dict[str, trimesh.Trimesh],
    *,
    feature_records: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return proof records or raise at the first failing screw axis."""

    results = audit_self_tapping_geometry(
        scene,
        part_meshes,
        feature_records=feature_records,
    )
    for key, result in results.items():
        if result.get("pass") is True:
            continue
        failed = sorted(
            name
            for name, passed in result.get("checks", {}).items()
            if passed is not True
        )
        if failed:
            detail = "failed compiled probes: " + ", ".join(failed)
        else:
            detail = "could not evaluate compiled probes: " + str(
                result.get("error", "unknown evidence error")
            )
        raise SelfTappingGeometryError(f"self-tapping geometry {key} {detail}")
    return results
