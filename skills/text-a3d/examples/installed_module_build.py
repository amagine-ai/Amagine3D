"""Preview with a3d draft before intent; compile this same source afterward.

P controls construction. Importing this module only exposes parameters/functions;
it does not import the CAD runtime, construct shapes, or write files.
"""
from math import isfinite

P = {
    "width": 80.0, "height": 60.0, "depth": 16.0,
    "cover_thickness": 3.0, "viewing_land": 4.0,
    "module_width": 50.0, "module_height": 30.0, "module_depth": 5.0,
    "module_side_gap": 1.0, "window_width": 44.0, "window_height": 24.0,
    "retainer_gap": 0.2, "retainer_width": 4.0, "retainer_height": 20.0,
    "locator_land": 1.0, "locator_radius": 3.0,
    "locator_wall": 1.8, "locator_engagement": 2.0, "locator_radial_gap": 0.3,
    "screw_corner_margin": 7.0,
    "show_module_reference": True,
}
P["fastening"] = {
    "screw_family": "M3 plastic thread-forming/self-tapping",
    "nominal_diameter_mm": 3.0, "clearance_diameter_mm": 3.4,
    "pilot_diameter_mm": 2.6, "boss_outer_diameter_mm": 7.5,
    "engagement_mm": 6.0, "pilot_tip_clearance_mm": 0.8,
    "closed_end_mm": 1.2, "minimum_boss_wall_mm": 1.8,
    "minimum_root_embed_mm": 0.4, "cutter_overshoot_mm": 1.0,
    "cover_thickness_mm": P["cover_thickness"],
}

def interval_box(*, x, y, z):
    """Construct a Box from explicit (lower, upper) bounds in millimeters."""
    from build123d import Align, Box, Pos

    x0, x1 = x
    y0, y1 = y
    z0, z1 = z
    if not all(isfinite(v) for v in (x0, x1, y0, y1, z0, z1)) or x1 <= x0 or y1 <= y0 or z1 <= z0:
        raise ValueError("Box intervals must have finite, strictly increasing endpoints")
    return Pos(x0, y0, z0) * Box(x1-x0, y1-y0, z1-z0, align=(Align.MIN,)*3)


def main():
    from build123d import Pos, Rot
    from authoring import paired_interface
    from build_session import BuildSession
    from geometry_binding import bind_display_component
    from installation_check import bind_installation_check
    from interface_recipes import collar_socket, self_tapping_screw_pair

    build = BuildSession(__file__, part_names=("frame", "cover"))
    OUT = build.out_dir
    # The display faces -Y. Remove the +Y rear cover before inserting the module.
    # World X is width, Z is height above z=0, and Y is front-to-back depth.
    FRONT_Y, REAR_Y = -P["depth"]/2, P["depth"]/2
    COVER_Y = REAR_Y - P["cover_thickness"]
    SEAT_Y = FRONT_Y + P["viewing_land"]
    MODULE_BACK_Y = SEAT_Y + P["module_depth"]
    CENTER_Z = P["height"]/2
    SCREW_X = P["width"]/2 - P["screw_corner_margin"]
    SCREW_Z_OFFSET = CENTER_Z - P["screw_corner_margin"]
    # Recipes keep their real round screw/collar geometry: local +Z becomes -Y.
    COVER_DATUM = Pos(0, COVER_Y, CENTER_Z) * Rot(X=90)
    MODULE_X = (-P["module_width"]/2, P["module_width"]/2)
    MODULE_Z = (CENTER_Z-P["module_height"]/2, CENTER_Z+P["module_height"]/2)
    CAVITY_X = (MODULE_X[0]-P["module_side_gap"], MODULE_X[1]+P["module_side_gap"])
    CAVITY_Z = (MODULE_Z[0]-P["module_side_gap"], MODULE_Z[1]+P["module_side_gap"])
    WINDOW_X = (-P["window_width"]/2, P["window_width"]/2)
    WINDOW_Z = (CENTER_Z-P["window_height"]/2, CENTER_Z+P["window_height"]/2)
    WINDOW_Y = (FRONT_Y - 1.0, SEAT_Y + 0.1)
    window = interval_box(x=WINDOW_X, y=WINDOW_Y, z=WINDOW_Z)
    module = interval_box(x=MODULE_X, y=(SEAT_Y, MODULE_BACK_Y), z=MODULE_Z)
    cavity = interval_box(x=CAVITY_X, y=(SEAT_Y, COVER_Y + 1), z=CAVITY_Z)
    outer = interval_box(x=(-P["width"]/2, P["width"]/2), y=(FRONT_Y, COVER_Y), z=(0, P["height"]))
    build.add("frame-body", outer, part_name="frame")
    build.cut("module-space", cavity, part_name="frame")
    build.cut("viewing-window", window, part_name="frame")

    # Extend beyond the cavity into the receiving frame, with the recipe's fit.
    LOCATOR_WIDTH = CAVITY_X[1] - CAVITY_X[0] + 2*P["locator_land"]
    LOCATOR_HEIGHT = CAVITY_Z[1] - CAVITY_Z[0] + 2*P["locator_land"]
    locator = collar_socket(
        interface_id="cover-location", width_mm=LOCATOR_WIDTH, depth_mm=LOCATOR_HEIGHT,
        radius_mm=P["locator_radius"], engagement_mm=P["locator_engagement"],
        radial_clearance_mm=P["locator_radial_gap"], collar_wall_mm=P["locator_wall"], axial_overshoot_mm=0.3,
    )
    collar, socket = COVER_DATUM * locator.male, COVER_DATUM * locator.female_cutter
    build.cut("frame-locator", socket, part_name="frame")
    base = interval_box(x=(-P["width"]/2, P["width"]/2), y=(COVER_Y, REAR_Y), z=(0, P["height"]))
    pads = [interval_box(
        x=(sign*(P["module_width"]-P["retainer_width"])/2-P["retainer_width"]/2,
           sign*(P["module_width"]-P["retainer_width"])/2+P["retainer_width"]/2),
        y=(MODULE_BACK_Y + P["retainer_gap"], COVER_Y),
        z=(CENTER_Z-P["retainer_height"]/2, CENTER_Z+P["retainer_height"]/2),
    ) for sign in (-1, 1)]
    retainers = pads[0] + pads[1]
    build.add("cover-body", base, part_name="cover")
    build.add("cover-collar", collar, part_name="cover")
    build.add("module-retainers", retainers, part_name="cover")

    fasteners = []
    for index, (sx, sz) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1))):
        name = f"corner-{index + 1}"
        origin = [sx*SCREW_X, COVER_Y, CENTER_Z+sz*SCREW_Z_OFFSET]
        datum = Pos(*origin) * Rot(X=90)
        pair = self_tapping_screw_pair(interface_id="cover-fastening", axis_id=name, **P["fastening"])
        clearance, pilot, boss = f"cover-{name}", f"frame-{name}-pilot", f"frame-{name}-boss"
        # These receivers are already inside the thick frame corners. Observe the
        # existing material instead of claiming a union that adds zero volume.
        build.observe(boss, datum * pair.receiver_boss, role="solid", part_name="frame")
        build.cut(pilot, datum * pair.pilot_cutter, part_name="frame")
        build.cut(clearance, datum * pair.clearance_cutter, part_name="cover")
        f = P["fastening"]
        fasteners.append({
            "id": name, "axis": {"originMm": origin, "direction": [0, -1, 0]},
            "screwFamily": f["screw_family"], "nominalDiameterMm": f["nominal_diameter_mm"], "cutterOvershootMm": f["cutter_overshoot_mm"],
            "cover": {"partId": "cover", "featureId": clearance, "diameterMm": f["clearance_diameter_mm"], "thicknessMm": P["cover_thickness"]},
            "receiver": {"partId": "frame", "featureId": pilot, "bossFeatureId": boss,
                         "diameterMm": f["pilot_diameter_mm"], "bossOuterDiameterMm": f["boss_outer_diameter_mm"],
                         "engagementMm": f["engagement_mm"], "closedEndMm": f["closed_end_mm"],
                         "minimumBossWallMm": f["minimum_boss_wall_mm"], "minimumRootEmbedMm": f["minimum_root_embed_mm"], "tipClearanceMm": f["pilot_tip_clearance_mm"]},
        })

    # Draft needs only the completed solids and an optional component reference.
    # Defer final display/installation evidence until intent exists for compilation.
    if build.is_draft:
        return build.export(draft_references={"purchased-module": module} if P["show_module_reference"] else None)

    # A separate visual reference is optional. The real window and installation
    # geometry above are complete when it is disabled, without changing the STEP.
    display_nodes = []
    if P["show_module_reference"]:
        display_nodes.append(bind_display_component(
            node_id="purchased-module", feature_id="module-reference", physical_feature_ref="module-space",
            shape=module, path=OUT / "installed-module-reference.ply",
            appearance={"baseColor": "#163946", "roughness": 0.25, "metallic": 0.0},
        ))

    # Exact linear sweep of the proposed rectangular module before the rear cover
    # is attached. Its optional visual surface never supplies these witnesses.
    entry_y = REAR_Y + P["module_depth"]
    insertion = interval_box(x=MODULE_X, y=(SEAT_Y, entry_y), z=MODULE_Z)
    optical_path = interval_box(
        x=(WINDOW_X[0]+0.1, WINDOW_X[1]-0.1),
        y=WINDOW_Y, z=(WINDOW_Z[0]+0.1, WINDOW_Z[1]-0.1),
    )
    installation = bind_installation_check(
        feature_id="module-space", envelope=module, out_dir=OUT / "installation-witnesses",
        obstacle_parts=["frame"], support_parts=["frame"], retainer_parts=["cover"],
        insertion_envelope=insertion, passage_envelope=optical_path, passage_parts=["frame"],
        withdrawal_axis=(0, 1, 0), support_direction=(0, -1, 0), contact_probe_mm=0.05,
        free_travel_mm=P["retainer_gap"]-0.05, stop_travel_mm=P["retainer_gap"]+0.05,
    )
    build.export(
        paired_interfaces=[paired_interface(
            id="cover-location", kind="collar-socket", male_feature="cover-collar", female_feature="frame-locator",
            male_dimensions_mm={"width": LOCATOR_WIDTH, "depth": LOCATOR_HEIGHT},
            clearances_mm={"width": 2*P["locator_radial_gap"], "depth": 2*P["locator_radial_gap"]},
        )],
        interfaces=[{"id": "cover-fastening", "kind": "self-tapping-screw", "locatorInterfaceIds": ["cover-location"], "fasteners": fasteners}],
        installation_checks=[installation],
        part_options={"frame": {"nodes": display_nodes}},
    )


if __name__ == "__main__":
    main()
