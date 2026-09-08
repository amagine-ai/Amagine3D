"""Copy into the current workspace, then run: a3d draft installed_module_draft.py.

Provisional installed module: inspect the shared envelope, cavity, viewing
opening and bottom entry before specifying retention, closure or fasteners.
Orange is a purchased-component envelope, not an additional printed part.
"""
from build123d import Align, Box, Pos
from cad_draft import export_draft

MODULE_W, MODULE_D, MODULE_H = 40.0, 28.0, 8.0
SIDE_GAP, WALL, VIEWING_LAND = 0.5, 3.0, 2.0
COVER_H, HEIGHT = 3.0, 18.0
WINDOW_W, WINDOW_D = 34.0, 22.0
ALIGN = (Align.CENTER, Align.CENTER, Align.MIN)
WIDTH, DEPTH = MODULE_W + 2 * (SIDE_GAP + WALL), MODULE_D + 2 * (SIDE_GAP + WALL)
SEAT_Z = HEIGHT - VIEWING_LAND

outer = Pos(0, 0, COVER_H) * Box(WIDTH, DEPTH, HEIGHT - COVER_H, align=ALIGN)
cavity = Pos(0, 0, COVER_H - 1) * Box(MODULE_W + 2 * SIDE_GAP, MODULE_D + 2 * SIDE_GAP,
                                     SEAT_Z - COVER_H + 1, align=ALIGN)
window = Pos(0, 0, SEAT_Z - 0.1) * Box(WINDOW_W, WINDOW_D, VIEWING_LAND + 1.1, align=ALIGN)
frame = outer - cavity - window
module = Pos(0, 0, SEAT_Z - MODULE_H) * Box(MODULE_W, MODULE_D, MODULE_H, align=ALIGN)
# Explode the provisional cover downwards to expose the bottom installation path.
# Closure and retention are still unresolved; this is not an assembly acceptance.
cover = Pos(0, 0, -8) * Box(WIDTH, DEPTH, COVER_H, align=ALIGN)
export_draft({"frame": frame, "cover-exploded": cover}, references={"module-envelope": module})
