"""The compiler runs this source; edit these dimensions for the intended part."""
from build123d import Align, Box, Cylinder, Pos
from build_session import BuildSession
from cad_helpers import checked_fillet

WIDTH, DEPTH, HEIGHT, EDGE_RADIUS = 40.0, 28.0, 10.0, 2.0
POCKET_DIAMETER, POCKET_DEPTH, CUTTER_OVERSHOOT = 12.0, 6.0, 1.0
FLOOR_Z = HEIGHT - POCKET_DEPTH
ALIGN = (Align.CENTER, Align.CENTER, Align.MIN)
build = BuildSession(__file__)
build.add("body", Box(WIDTH, DEPTH, HEIGHT, align=ALIGN))
build.finish("simple-brep", lambda body: checked_fillet(
    body, body.edges(), EDGE_RADIUS, "body-rounding",
))
cutter = Pos(0, 0, FLOOR_Z) * Cylinder(POCKET_DIAMETER / 2, POCKET_DEPTH + CUTTER_OVERSHOOT, align=ALIGN)
build.cut("pocket", cutter)
build.export()
