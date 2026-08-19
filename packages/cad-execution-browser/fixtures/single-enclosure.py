"""Golden single-color sensor enclosure tray."""
from build123d import Align, Box, Pos
from amagine_cad import observe_feature, publish_model, subtract_checked

WIDTH = 64.0
DEPTH = 42.0
HEIGHT = 18.0
WALL = 2.4
FLOOR = 2.0
PORT_W = 12.0
PORT_H = 7.0

body = Box(WIDTH, DEPTH, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN))
interior = Pos(0, 0, FLOOR) * Box(
    WIDTH - 2 * WALL,
    DEPTH - 2 * WALL,
    HEIGHT,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
body = subtract_checked(body, interior, "tray-cavity")
port = Pos(WIDTH / 2 - WALL - 1, 0, FLOOR + PORT_H / 2) * Box(
    2 * WALL + 2,
    PORT_W,
    PORT_H,
    align=(Align.CENTER, Align.CENTER, Align.CENTER),
)
body = subtract_checked(body, port, "side-connector")
observe_feature(body, "sensor-enclosure", "body")

if __name__ == "__main__":
    publish_model(body, "single-enclosure", out_dir="cad_out")
