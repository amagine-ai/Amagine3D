"""Compile through a3d; all placement derives from one viewing-face and cover datum."""
import json
import os
from pathlib import Path

from build123d import Align, Box, Pos
from authoring import paired_interface, write_scene
from cad_helpers import checked_cut, export_assembly, observe
from geometry_binding import bind_brep_feature, bind_display_component
from installation_check import bind_installation_check
from interface_recipes import collar_socket, self_tapping_screw_pair

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", ROOT))
INTENT = os.environ["AMAGINE3D_INTENT_PATH"]
SCENE = os.environ.get("AMAGINE3D_SCENE_PATH", str(ROOT / "installed_module_scene.json"))
P = json.loads((ROOT / "installed_module_parameters.json").read_text())
ALIGN = (Align.CENTER, Align.CENTER, Align.MIN)

# The viewing face is top +Z. The module inserts from bottom -Z with the cover absent.
COVER_Z = P["cover_thickness"]
TOP_Z = P["height"]
SEAT_Z = TOP_Z - P["viewing_land"]
MODULE_BOTTOM_Z = SEAT_Z - P["module_thickness"]
COVER_DATUM = Pos(0, 0, COVER_Z)
MODULE = Pos(0, 0, MODULE_BOTTOM_Z)
window = Pos(0, 0, SEAT_Z - 0.1) * Box(P["window_width"], P["window_depth"], P["viewing_land"] + 1.1, align=ALIGN)
module = MODULE * Box(P["module_width"], P["module_depth"], P["module_thickness"], align=ALIGN)
cavity = Pos(0, 0, COVER_Z - 1) * Box(P["module_width"] + 2*P["module_side_gap"], P["module_depth"] + 2*P["module_side_gap"], SEAT_Z - COVER_Z + 1, align=ALIGN)
outer = COVER_DATUM * Box(P["width"], P["depth"], TOP_Z - COVER_Z, align=ALIGN)
observe(outer, "frame-body", role="solid", part_name="frame")
frame = checked_cut(outer, cavity, "module-space", part_name="frame")
frame = checked_cut(frame, window, "viewing-window", part_name="frame")

locator = collar_socket(
    interface_id="cover-location", width_mm=P["locator_width"], depth_mm=P["locator_depth"],
    radius_mm=P["locator_radius"], engagement_mm=P["locator_engagement"],
    radial_clearance_mm=P["locator_radial_gap"], collar_wall_mm=P["locator_wall"], axial_overshoot_mm=0.3,
)
collar, socket = COVER_DATUM * locator.male, COVER_DATUM * locator.female_cutter
frame = checked_cut(frame, socket, "frame-locator", part_name="frame")
base = Box(P["width"], P["depth"], COVER_Z, align=ALIGN)
pad_height = MODULE_BOTTOM_Z - P["retainer_gap"] - COVER_Z
pads = [Pos(sign*(P["module_width"]-P["retainer_width"])/2, 0, COVER_Z) * Box(P["retainer_width"], P["retainer_depth"], pad_height, align=ALIGN) for sign in (-1, 1)]
retainers = pads[0] + pads[1]
cover = base + collar + retainers
observe(base, "cover-body", role="solid", part_name="cover")
observe(collar, "cover-collar", role="solid", part_name="cover")
observe(retainers, "module-retainers", role="solid", part_name="cover")

def bound(feature, shape, role):
    return bind_brep_feature(node_id=feature + "-node", feature_id=feature, shape=shape,
                             role=role, path=OUT / f"installed-module-{feature}.stl")

nodes = {
    "frame": [bound("frame-body", outer, "solid"), bound("module-space", cavity, "cutter"),
              bound("viewing-window", window, "cutter"), bound("frame-locator", socket, "cutter")],
    "cover": [bound("cover-body", base, "solid"), bound("cover-collar", collar, "solid"),
              bound("module-retainers", retainers, "solid")],
}
fasteners = []
for index, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1))):
    name = f"corner-{index + 1}"
    origin = [sx*P["screw_x"], sy*P["screw_y"], COVER_Z]
    datum = Pos(*origin)
    pair = self_tapping_screw_pair(interface_id="cover-fastening", axis_id=name, **P["fastening"])
    clearance, pilot, boss = f"cover-{name}", f"frame-{name}-pilot", f"frame-{name}-boss"
    frame = frame + datum * pair.receiver_boss
    observe(datum * pair.receiver_boss, boss, role="solid", part_name="frame")
    frame = checked_cut(frame, datum * pair.pilot_cutter, pilot, part_name="frame")
    cover = checked_cut(cover, datum * pair.clearance_cutter, clearance, part_name="cover")
    for part, feature, output, role in (("frame", boss, "receiver-boss", "solid"), ("frame", pilot, "pilot-cutter", "cutter"), ("cover", clearance, "clearance-cutter", "cutter")):
        nodes[part].append({"id": feature + "-node", "featureId": feature, "role": role,
                            "operation": "union" if role == "solid" else "subtract",
                            "recipe": {"kind": "selfTappingScrewPair", "parameters": {
                                "interfaceId": "cover-fastening", "fastenerId": name, "output": output}}})
    f = P["fastening"]
    fasteners.append({
        "id": name, "axis": {"originMm": origin, "direction": [0, 0, 1]},
        "screwFamily": f["screw_family"], "nominalDiameterMm": f["nominal_diameter_mm"], "cutterOvershootMm": f["cutter_overshoot_mm"],
        "cover": {"partId": "cover", "featureId": clearance, "diameterMm": f["clearance_diameter_mm"], "thicknessMm": COVER_Z},
        "receiver": {"partId": "frame", "featureId": pilot, "bossFeatureId": boss,
                     "diameterMm": f["pilot_diameter_mm"], "bossOuterDiameterMm": f["boss_outer_diameter_mm"],
                     "engagementMm": f["engagement_mm"], "closedEndMm": f["closed_end_mm"],
                     "minimumBossWallMm": f["minimum_boss_wall_mm"], "minimumRootEmbedMm": f["minimum_root_embed_mm"], "tipClearanceMm": f["pilot_tip_clearance_mm"]},
    })

# A separate visual reference is optional. The real window and installation
# geometry above are complete when it is disabled, without changing the STEP.
if P["show_module_reference"]:
    nodes["frame"].append(bind_display_component(
        node_id="purchased-module", feature_id="module-reference", physical_feature_ref="module-space",
        shape=module, path=OUT / "installed-module-reference.ply",
        appearance={"baseColor": "#163946", "roughness": 0.25, "metallic": 0.0},
    ))

# Exact linear sweep of the proposed rectangular module before the bottom cover
# is attached. Its optional visual surface never supplies these witnesses.
entry_z = -P["module_thickness"]
insertion = Pos(0, 0, entry_z) * Box(P["module_width"], P["module_depth"], SEAT_Z-entry_z, align=ALIGN)
optical_path = Pos(0, 0, SEAT_Z-0.1) * Box(P["window_width"]-0.2, P["window_depth"]-0.2, P["viewing_land"]+1.1, align=ALIGN)
installation = bind_installation_check(
    feature_id="module-space", envelope=module, out_dir=OUT / "installation-witnesses",
    obstacle_parts=["frame"], support_parts=["frame"], retainer_parts=["cover"],
    insertion_envelope=insertion, passage_envelope=optical_path, passage_parts=["frame"],
    withdrawal_axis=(0, 0, -1), contact_probe_mm=0.05,
    free_travel_mm=P["retainer_gap"]-0.05, stop_travel_mm=P["retainer_gap"]+0.05,
)
write_scene(
    SCENE, intent_path=INTENT,
    parts={name: {"representationMaster": "brep", "nodes": part_nodes} for name, part_nodes in nodes.items()},
    paired_interfaces=[paired_interface(
        id="cover-location", kind="collar-socket", male_feature="cover-collar", female_feature="frame-locator",
        male_dimensions_mm={"width": P["locator_width"], "depth": P["locator_depth"]},
        clearances_mm={"width": 2*P["locator_radial_gap"], "depth": 2*P["locator_radial_gap"]},
    )],
    interfaces=[{"id": "cover-fastening", "kind": "self-tapping-screw", "locatorInterfaceIds": ["cover-location"], "fasteners": fasteners}],
    installation_checks=[installation],
)
export_assembly({"frame": frame, "cover": cover}, "installed-module", out_dir=str(OUT),
                intent_path=INTENT, scene_path=SCENE, source_path=str(Path(__file__).resolve()))
