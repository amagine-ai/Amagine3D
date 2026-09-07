"""Build only through a3d compile; intent is authored separately."""
import os
from pathlib import Path

from build123d import Align, Box, Cylinder, Pos
from authoring import paired_interface, write_scene
from cad_helpers import checked_cut, export_assembly, observe
from geometry_binding import bind_brep_feature

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", ROOT))
INTENT = os.environ["AMAGINE3D_INTENT_PATH"]
SCENE = os.environ.get("AMAGINE3D_SCENE_PATH", str(ROOT / "assembly_scene.json"))

# Controlling dimensions; the blind seat is both the pin datum and the floor.
WIDTH, DEPTH, HOLDER_HEIGHT = 30.0, 24.0, 6.0
SEAT_Z, PIN_DIAMETER, PIN_LENGTH = 2.0, 6.0, 8.0
RADIAL_GAP, CUTTER_OVERSHOOT = 0.2, 1.0
SOCKET_DIAMETER = PIN_DIAMETER + 2 * RADIAL_GAP
DATUM = Pos(0, 0, SEAT_Z)
ALIGN = (Align.CENTER, Align.CENTER, Align.MIN)

body = Box(WIDTH, DEPTH, HOLDER_HEIGHT, align=ALIGN)
socket = DATUM * Cylinder(SOCKET_DIAMETER / 2, HOLDER_HEIGHT - SEAT_Z + CUTTER_OVERSHOOT, align=ALIGN)
pin = DATUM * Cylinder(PIN_DIAMETER / 2, PIN_LENGTH, align=ALIGN)
observe(body, "holder-body", role="solid", part_name="holder")
holder = checked_cut(body, socket, "socket", part_name="holder")
observe(pin, "pin-body", role="solid", part_name="pin")

# Bind the same objects used above, in semantic coordinates, not substitute recipes.
def bound(feature, shape, role):
    return bind_brep_feature(
        node_id=f"{feature}-node", feature_id=feature, role=role, shape=shape,
        path=OUT / f"assembly-{feature}-geometry.stl",
    )

write_scene(
    SCENE, intent_path=INTENT,
    parts={
        "holder": {"representationMaster": "brep", "nodes": [bound("holder-body", body, "solid"), bound("socket", socket, "cutter")]},
        "pin": {"representationMaster": "brep", "nodes": [bound("pin-body", pin, "solid")]},
    },
    paired_interfaces=[paired_interface(
        id="locating-fit", kind="pin-socket",
        male_feature="pin-body", male_dimensions_mm={"diameter": PIN_DIAMETER},
        female_feature="socket", clearances_mm={"diameter": 2 * RADIAL_GAP},
    )],
)
export_assembly(
    {"holder": holder, "pin": pin}, "assembly", out_dir=str(OUT),
    intent_path=INTENT, scene_path=SCENE, source_path=str(Path(__file__).resolve()),
)
