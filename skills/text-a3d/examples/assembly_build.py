"""Build only through a3d compile; intent is authored separately."""
from build123d import Align, Box, Cylinder, Pos
from authoring import paired_interface
from build_session import BuildSession

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
build = BuildSession(__file__)
build.add("holder-body", body)
build.cut("socket", socket)
build.add("pin-body", pin)
build.export(
    paired_interfaces=[paired_interface(
        id="locating-fit", kind="pin-socket",
        male_feature="pin-body", male_dimensions_mm={"diameter": PIN_DIAMETER},
        female_feature="socket", clearances_mm={"diameter": 2 * RADIAL_GAP},
    )],
)
