"""Paired, parameter-driven connector geometry for generated assemblies.

Each recipe derives the receiving geometry from the retained geometry.  This
keeps male/female dimensions, clearances, and evidence tied to one source
instead of asking a model to maintain two independent sets of numbers.

Shapes are created in a local +Z assembly frame.  Callers may place the whole
pair with the same rigid build123d Location/Pos/Rot transform.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from build123d import Align, Circle, Cylinder, Pos, RectangleRounded, extrude


class InterfaceRecipeError(ValueError):
    """Raised when an interface recipe cannot produce coherent geometry."""


@dataclass(frozen=True)
class InterfacePair:
    """One retained solid and its derived receiving cutter."""

    male: Any
    female_cutter: Any
    evidence: dict[str, Any]


@dataclass(frozen=True)
class SelfTappingScrewPair:
    """One aligned cover bore, receiver pilot, and printable screw boss."""

    clearance_cutter: Any
    pilot_cutter: Any
    receiver_boss: Any
    evidence: dict[str, Any]


def _positive(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InterfaceRecipeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise InterfaceRecipeError(f"{name} must be finite and positive")
    return result


def _non_negative(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InterfaceRecipeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise InterfaceRecipeError(f"{name} must be finite and non-negative")
    return result


def _rounded_prism(width: float, depth: float, radius: float, height: float):
    width = _positive("width", width)
    depth = _positive("depth", depth)
    radius = _positive("radius", radius)
    height = _positive("height", height)
    if radius * 2 >= min(width, depth):
        raise InterfaceRecipeError(
            "rounded-rectangle radius must be smaller than half its short side"
        )
    return extrude(RectangleRounded(width, depth, radius), amount=height)


def collar_socket(
    *,
    interface_id: str,
    width_mm: float,
    depth_mm: float,
    radius_mm: float,
    engagement_mm: float,
    radial_clearance_mm: float,
    collar_wall_mm: float,
    axial_overshoot_mm: float = 1.0,
) -> InterfacePair:
    """Create a hollow locating collar and its clearance-derived socket cutter."""

    engagement = _positive("engagement_mm", engagement_mm)
    clearance = _non_negative("radial_clearance_mm", radial_clearance_mm)
    wall = _positive("collar_wall_mm", collar_wall_mm)
    overshoot = _non_negative("axial_overshoot_mm", axial_overshoot_mm)
    width = _positive("width_mm", width_mm)
    depth = _positive("depth_mm", depth_mm)
    radius = _positive("radius_mm", radius_mm)
    inner_width = width - 2 * wall
    inner_depth = depth - 2 * wall
    inner_radius = max(radius - wall, 0.05)
    if inner_width <= 0 or inner_depth <= 0:
        raise InterfaceRecipeError("collar wall consumes the mating profile")

    outer = _rounded_prism(width, depth, radius, engagement)
    inner = Pos(0, 0, -overshoot) * _rounded_prism(
        inner_width,
        inner_depth,
        inner_radius,
        engagement + 2 * overshoot,
    )
    male = outer - inner

    socket_width = width + 2 * clearance
    socket_depth = depth + 2 * clearance
    socket_radius = radius + clearance
    female_cutter = Pos(0, 0, -overshoot) * _rounded_prism(
        socket_width,
        socket_depth,
        socket_radius,
        engagement + 2 * overshoot,
    )
    return InterfacePair(
        male=male,
        female_cutter=female_cutter,
        evidence={
            "id": interface_id,
            "type": "collar-socket",
            "assembly_axis": "+Z",
            "engagement_mm": engagement,
            "fit": {
                "radial_clearance_mm": clearance,
                "socket_width_mm": socket_width,
                "socket_depth_mm": socket_depth,
            },
            "male_profile": {
                "width_mm": width,
                "depth_mm": depth,
                "radius_mm": radius,
                "wall_mm": wall,
            },
        },
    )


def inset_pocket(
    *,
    interface_id: str,
    width_mm: float,
    height_mm: float,
    radius_mm: float,
    insert_thickness_mm: float,
    side_clearance_mm: float,
    axial_clearance_mm: float = 0.0,
    requested_proud_mm: float = 0.0,
    cutter_overshoot_mm: float = 1.0,
) -> InterfacePair:
    """Create a rounded insert and a pocket derived from its outline."""

    width = _positive("width_mm", width_mm)
    height = _positive("height_mm", height_mm)
    radius = _positive("radius_mm", radius_mm)
    thickness = _positive("insert_thickness_mm", insert_thickness_mm)
    clearance = _non_negative("side_clearance_mm", side_clearance_mm)
    axial = _non_negative("axial_clearance_mm", axial_clearance_mm)
    overshoot = _non_negative("cutter_overshoot_mm", cutter_overshoot_mm)
    if not isinstance(requested_proud_mm, (int, float)) or isinstance(
        requested_proud_mm, bool
    ):
        raise InterfaceRecipeError("requested_proud_mm must be numeric")
    proud = float(requested_proud_mm)
    pocket_depth = thickness - proud + axial
    if not math.isfinite(pocket_depth) or pocket_depth <= 0:
        raise InterfaceRecipeError("derived pocket depth must be positive")

    male = _rounded_prism(width, height, radius, thickness)
    female_cutter = Pos(0, 0, -overshoot) * _rounded_prism(
        width + 2 * clearance,
        height + 2 * clearance,
        radius + clearance,
        pocket_depth + overshoot,
    )
    return InterfacePair(
        male=male,
        female_cutter=female_cutter,
        evidence={
            "id": interface_id,
            "type": "inset-pocket",
            "assembly_axis": "+Z",
            "insert": {
                "width_mm": width,
                "height_mm": height,
                "radius_mm": radius,
                "thickness_mm": thickness,
            },
            "fit": {
                "side_clearance_mm": clearance,
                "axial_clearance_mm": axial,
                "requested_proud_mm": proud,
                "pocket_depth_mm": pocket_depth,
            },
        },
    )


def retained_slider(
    *,
    interface_id: str,
    cap_diameter_mm: float,
    cap_thickness_mm: float,
    stem_diameter_mm: float,
    stem_length_mm: float,
    flange_diameter_mm: float,
    flange_thickness_mm: float,
    radial_clearance_mm: float,
    guide_depth_mm: float,
    cutter_overshoot_mm: float = 1.0,
    travel_mm: float = 0.8,
) -> InterfacePair:
    """Create one retained button solid and its clearance-derived guide bore."""

    cap_diameter = _positive("cap_diameter_mm", cap_diameter_mm)
    cap_thickness = _positive("cap_thickness_mm", cap_thickness_mm)
    stem_diameter = _positive("stem_diameter_mm", stem_diameter_mm)
    stem_length = _positive("stem_length_mm", stem_length_mm)
    flange_diameter = _positive("flange_diameter_mm", flange_diameter_mm)
    flange_thickness = _positive("flange_thickness_mm", flange_thickness_mm)
    clearance = _non_negative("radial_clearance_mm", radial_clearance_mm)
    guide_depth = _positive("guide_depth_mm", guide_depth_mm)
    overshoot = _non_negative("cutter_overshoot_mm", cutter_overshoot_mm)
    travel = _non_negative("travel_mm", travel_mm)
    if flange_diameter <= stem_diameter:
        raise InterfaceRecipeError("flange must retain the stem inside the guide")
    if cap_diameter < stem_diameter:
        raise InterfaceRecipeError("cap diameter cannot be smaller than the stem")

    overlap = min(0.05, flange_thickness * 0.1, stem_length * 0.1)
    flange = Cylinder(
        flange_diameter / 2,
        flange_thickness,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    stem = Pos(0, 0, flange_thickness - overlap) * Cylinder(
        stem_diameter / 2,
        stem_length + 2 * overlap,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    cap_z = flange_thickness + stem_length
    cap = Pos(0, 0, cap_z - overlap) * Cylinder(
        cap_diameter / 2,
        cap_thickness + overlap,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    male = flange + stem + cap

    guide_diameter = stem_diameter + 2 * clearance
    female_cutter = Pos(0, 0, -overshoot) * Cylinder(
        guide_diameter / 2,
        guide_depth + 2 * overshoot,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    return InterfacePair(
        male=male,
        female_cutter=female_cutter,
        evidence={
            "id": interface_id,
            "type": "retained-slider",
            "assembly_axis": "+Z",
            "button": {
                "cap_diameter_mm": cap_diameter,
                "cap_thickness_mm": cap_thickness,
                "stem_diameter_mm": stem_diameter,
                "stem_length_mm": stem_length,
                "flange_diameter_mm": flange_diameter,
                "flange_thickness_mm": flange_thickness,
            },
            "fit": {
                "radial_clearance_mm": clearance,
                "guide_diameter_mm": guide_diameter,
                "guide_depth_mm": guide_depth,
            },
            "mobility": {
                "type": "translational",
                "axis": "+Z",
                "limits_mm": [0.0, travel],
            },
        },
    )


def pin_socket(
    *,
    interface_id: str,
    pin_diameter_mm: float,
    engagement_mm: float,
    radial_clearance_mm: float,
    axial_clearance_mm: float = 0.2,
    cutter_overshoot_mm: float = 1.0,
) -> InterfacePair:
    """Create a simple locating pin and its clearance-derived socket cutter."""

    diameter = _positive("pin_diameter_mm", pin_diameter_mm)
    engagement = _positive("engagement_mm", engagement_mm)
    radial = _non_negative("radial_clearance_mm", radial_clearance_mm)
    axial = _non_negative("axial_clearance_mm", axial_clearance_mm)
    overshoot = _non_negative("cutter_overshoot_mm", cutter_overshoot_mm)
    socket_diameter = diameter + 2 * radial
    socket_depth = engagement + axial
    male = Cylinder(
        diameter / 2,
        engagement,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    female_cutter = Pos(0, 0, -overshoot) * Cylinder(
        socket_diameter / 2,
        socket_depth + overshoot,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    return InterfacePair(
        male=male,
        female_cutter=female_cutter,
        evidence={
            "id": interface_id,
            "type": "pin-socket",
            "assembly_axis": "+Z",
            "male": {
                "diameter_mm": diameter,
                "engagement_mm": engagement,
            },
            "fit": {
                "radial_clearance_mm": radial,
                "axial_clearance_mm": axial,
                "socket_diameter_mm": socket_diameter,
                "socket_depth_mm": socket_depth,
            },
        },
    )


def self_tapping_screw_pair(
    *,
    interface_id: str,
    axis_id: str,
    cover_thickness_mm: float,
    screw_family: str | None = None,
    nominal_diameter_mm: float = 3.0,
    clearance_diameter_mm: float = 3.4,
    pilot_diameter_mm: float = 2.6,
    boss_outer_diameter_mm: float = 7.5,
    engagement_mm: float = 6.0,
    pilot_tip_clearance_mm: float = 0.8,
    closed_end_mm: float = 1.2,
    minimum_boss_wall_mm: float = 1.8,
    minimum_root_embed_mm: float = 0.4,
    head_recess_diameter_mm: float | None = None,
    head_recess_depth_mm: float | None = None,
    minimum_cover_land_mm: float | None = None,
    cutter_overshoot_mm: float = 1.0,
) -> SelfTappingScrewPair:
    """Create one coaxial plastic self-tapping screw connection.

    The local mating plane is Z=0.  The cover occupies negative Z and the
    receiving boss grows toward positive Z.  Callers apply one rigid transform
    to all three returned shapes, so the clearance hole and blind pilot cannot
    acquire independent centers or axes.

    The M3 defaults are printable starting dimensions, not a material-agnostic
    standard.  Calibrate the pilot diameter for the selected screw, filament,
    printer, and process when those are known.
    """

    cover_thickness = _positive("cover_thickness_mm", cover_thickness_mm)
    nominal = _positive("nominal_diameter_mm", nominal_diameter_mm)
    clearance = _positive("clearance_diameter_mm", clearance_diameter_mm)
    pilot = _positive("pilot_diameter_mm", pilot_diameter_mm)
    boss_outer = _positive("boss_outer_diameter_mm", boss_outer_diameter_mm)
    engagement = _positive("engagement_mm", engagement_mm)
    tip_clearance = _positive("pilot_tip_clearance_mm", pilot_tip_clearance_mm)
    closed_end = _positive("closed_end_mm", closed_end_mm)
    minimum_wall = _positive("minimum_boss_wall_mm", minimum_boss_wall_mm)
    root_embed = _positive("minimum_root_embed_mm", minimum_root_embed_mm)
    overshoot = _positive("cutter_overshoot_mm", cutter_overshoot_mm)
    if not isinstance(interface_id, str) or not interface_id.strip():
        raise InterfaceRecipeError("interface_id must be a non-empty string")
    if not isinstance(axis_id, str) or not axis_id.strip():
        raise InterfaceRecipeError("axis_id must be a non-empty string")
    if screw_family is None:
        screw_family = f"M{nominal:g} plastic thread-forming/self-tapping"
    elif not isinstance(screw_family, str) or not screw_family.strip():
        raise InterfaceRecipeError("screw_family must be a non-empty string")
    if not pilot < nominal < clearance:
        raise InterfaceRecipeError(
            "self-tapping diameters must satisfy pilot < nominal < clearance"
        )
    boss_wall = (boss_outer - pilot) / 2
    if boss_wall < minimum_wall:
        raise InterfaceRecipeError(
            "boss_outer_diameter_mm leaves less than minimum_boss_wall_mm "
            "around the pilot"
        )

    has_recess_diameter = head_recess_diameter_mm is not None
    has_recess_depth = head_recess_depth_mm is not None
    if has_recess_diameter != has_recess_depth:
        raise InterfaceRecipeError(
            "head recess diameter and depth must be supplied together"
        )
    recess_diameter = None
    recess_depth = None
    minimum_cover_land = None
    if has_recess_diameter and has_recess_depth:
        recess_diameter = _positive(
            "head_recess_diameter_mm", head_recess_diameter_mm
        )
        recess_depth = _positive("head_recess_depth_mm", head_recess_depth_mm)
        if minimum_cover_land_mm is None:
            raise InterfaceRecipeError(
                "minimum_cover_land_mm is required with a head recess"
            )
        minimum_cover_land = _positive(
            "minimum_cover_land_mm", minimum_cover_land_mm
        )
        if recess_diameter <= clearance:
            raise InterfaceRecipeError(
                "head recess diameter must exceed the clearance diameter"
            )
        if recess_depth + minimum_cover_land > cover_thickness:
            raise InterfaceRecipeError(
                "head recess leaves less than minimum_cover_land_mm"
            )
    elif minimum_cover_land_mm is not None:
        raise InterfaceRecipeError(
            "minimum_cover_land_mm requires a head recess"
        )

    clearance_cutter = Pos(0, 0, -cover_thickness - overshoot) * Cylinder(
        clearance / 2,
        cover_thickness + 2 * overshoot,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    if recess_diameter is not None and recess_depth is not None:
        head_recess = Pos(0, 0, -cover_thickness - overshoot) * Cylinder(
            recess_diameter / 2,
            recess_depth + overshoot,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        clearance_cutter = clearance_cutter + head_recess

    pilot_depth = engagement + tip_clearance
    pilot_cutter = Pos(0, 0, -overshoot) * Cylinder(
        pilot / 2,
        pilot_depth + overshoot,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    boss_height = pilot_depth + closed_end
    if root_embed > boss_height:
        raise InterfaceRecipeError(
            "minimum_root_embed_mm cannot exceed the receiver boss height"
        )
    # The mating plane separates the two printed parts.  The receiver boss may
    # not cross into the negative-Z cover volume; its positive-Z root is fused
    # into the receiver body and verified after compilation instead.
    receiver_boss = Cylinder(
        boss_outer / 2,
        boss_height,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    under_head_cover_stack = cover_thickness - (recess_depth or 0.0)

    return SelfTappingScrewPair(
        clearance_cutter=clearance_cutter,
        pilot_cutter=pilot_cutter,
        receiver_boss=receiver_boss,
        evidence={
            "id": interface_id,
            "type": "self-tapping-screw",
            "assembly_axis": "+Z",
            "axis": {
                "id": axis_id,
                "origin_mm": [0.0, 0.0, 0.0],
                "direction": [0.0, 0.0, 1.0],
            },
            "screw": {
                "family": screw_family,
                "nominal_diameter_mm": nominal,
                "manufacturing": "purchased-hardware-excluded",
                "under_head_cover_stack_mm": under_head_cover_stack,
                "minimum_under_head_length_mm": (
                    under_head_cover_stack + engagement
                ),
                "maximum_under_head_length_mm": (
                    under_head_cover_stack + pilot_depth
                ),
            },
            "cover": {
                "thickness_mm": cover_thickness,
                "clearance_diameter_mm": clearance,
                "head_recess_diameter_mm": recess_diameter,
                "head_recess_depth_mm": recess_depth,
                **(
                    {"minimum_cover_land_mm": minimum_cover_land}
                    if minimum_cover_land is not None
                    else {}
                ),
            },
            "receiver": {
                "pilot_diameter_mm": pilot,
                "pilot_depth_mm": pilot_depth,
                "thread_engagement_mm": engagement,
                "tip_clearance_mm": tip_clearance,
                "boss_outer_diameter_mm": boss_outer,
                "boss_height_mm": boss_height,
                "boss_wall_mm": boss_wall,
                "closed_end_mm": closed_end,
                "minimum_root_embed_mm": root_embed,
            },
            "calibration": {
                "pilot_to_nominal_ratio": pilot / nominal,
                "required_when_material_or_screw_changes": True,
            },
        },
    )


def hinge_pin(
    *,
    interface_id: str,
    pin_diameter_mm: float,
    span_mm: float,
    radial_clearance_mm: float,
    axial_clearance_mm: float = 0.3,
    head_diameter_mm: float | None = None,
    head_thickness_mm: float = 1.2,
    cutter_overshoot_mm: float = 1.0,
) -> InterfacePair:
    """Create a removable hinge pin and the shared coaxial knuckle bore cutter.

    The caller builds alternating hinge knuckles around the returned bore.  One
    cutter is intentionally shared by both leaves so their axes cannot drift.
    """

    diameter = _positive("pin_diameter_mm", pin_diameter_mm)
    span = _positive("span_mm", span_mm)
    radial = _non_negative("radial_clearance_mm", radial_clearance_mm)
    axial = _non_negative("axial_clearance_mm", axial_clearance_mm)
    overshoot = _non_negative("cutter_overshoot_mm", cutter_overshoot_mm)
    head_diameter = (
        diameter * 1.7
        if head_diameter_mm is None
        else _positive("head_diameter_mm", head_diameter_mm)
    )
    head_thickness = _positive("head_thickness_mm", head_thickness_mm)
    if head_diameter <= diameter:
        raise InterfaceRecipeError("hinge pin head must be wider than the shaft")

    overlap = min(0.05, span * 0.05, head_thickness * 0.05)
    shaft = Cylinder(
        diameter / 2,
        span,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    head = Pos(0, 0, span - overlap) * Cylinder(
        head_diameter / 2,
        head_thickness + overlap,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    male = shaft + head
    bore_diameter = diameter + 2 * radial
    bore_length = span + axial
    female_cutter = Pos(0, 0, -overshoot) * Cylinder(
        bore_diameter / 2,
        bore_length + 2 * overshoot,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    return InterfacePair(
        male=male,
        female_cutter=female_cutter,
        evidence={
            "id": interface_id,
            "type": "hinge-pin",
            "assembly_axis": "+Z",
            "pin": {
                "diameter_mm": diameter,
                "span_mm": span,
                "head_diameter_mm": head_diameter,
                "head_thickness_mm": head_thickness,
            },
            "fit": {
                "radial_clearance_mm": radial,
                "axial_clearance_mm": axial,
                "bore_diameter_mm": bore_diameter,
                "bore_length_mm": bore_length,
            },
            "mobility": {
                "type": "rotational",
                "axis": "+Z",
            },
        },
    )
