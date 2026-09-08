"""The compiler runs this source; edit these dimensions for the intended part."""
import os
from pathlib import Path

from build123d import Align, Box, Cylinder, Pos, fillet
from authoring import write_scene
from cad_helpers import checked_cut, export_part, observe
from geometry_binding import bind_brep_feature

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("AMAGINE3D_OUTPUT_DIR", ROOT))
INTENT = os.environ["AMAGINE3D_INTENT_PATH"]
SCENE = os.environ.get("AMAGINE3D_SCENE_PATH", str(ROOT / "simple_brep_scene.json"))

WIDTH, DEPTH, HEIGHT, EDGE_RADIUS = 40.0, 28.0, 10.0, 2.0
POCKET_DIAMETER, POCKET_DEPTH, CUTTER_OVERSHOOT = 12.0, 6.0, 1.0
FLOOR_Z = HEIGHT - POCKET_DEPTH
ALIGN = (Align.CENTER, Align.CENTER, Align.MIN)
block = Box(WIDTH, DEPTH, HEIGHT, align=ALIGN)
body = fillet(block.edges(), EDGE_RADIUS)
cutter = Pos(0, 0, FLOOR_Z) * Cylinder(POCKET_DIAMETER / 2, POCKET_DEPTH + CUTTER_OVERSHOOT, align=ALIGN)
observe(body, "body", role="solid", part_name="simple-brep")
part = checked_cut(body, cutter, "pocket", part_name="simple-brep")

nodes = [bind_brep_feature(node_id=f"{feature}-node", feature_id=feature, role=role,
                           shape=shape, path=OUT / f"simple_brep-{feature}-geometry.stl")
         for feature, shape, role in (("body", body, "solid"), ("pocket", cutter, "cutter"))]
write_scene(SCENE, intent_path=INTENT,
            parts={"simple-brep": {"representationMaster": "brep", "nodes": nodes}})
export_part(part, "simple-brep", out_dir=str(OUT), intent_path=INTENT,
            scene_path=SCENE, source_path=str(Path(__file__).resolve()))
