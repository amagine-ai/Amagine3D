"""Preview with a3d draft --intent, then compile; a BRep loft owns the shell."""
import os
from pathlib import Path

from build123d import Plane, Pos, RectangleRounded, extrude, loft
import numpy as np
from brep_measurements import measure_section
from build_session import BuildSession

# Millimetres. Each station independently controls z, width, depth, corner
# radius, centre x and centre y. Keep their rounded-rectangle edge ordering.
# Use a few meaningful stations instead of sampling and joining mesh rings.
STATIONS = (
    (0.0, 88.0, 66.0, 10.0, 0.0, 0.0),
    (12.0, 96.0, 76.0, 12.0, 0.0, 0.0),
    (30.0, 100.0, 80.0, 14.0, 0.0, 0.0),
    (62.0, 100.0, 78.0, 14.0, 0.0, -1.0),
    (75.0, 90.0, 72.0, 12.0, 1.0, -1.0),
    (90.0, 82.0, 66.0, 10.0, 2.0, -1.0),
)
WALL_INSET, FLOOR, CUTTER_OVERSHOOT = 3.0, 3.0, 1.0
FOOT_HEIGHT = 4.0  # Positive outer-foot extrusion; 0 disables it, independently of FLOOR.
# Calibration targets mirror the brief; the intent owns final acceptance.
TOP_PLANE_Z, TOP_OUTER_WIDTH = 90.0, 82.0
TARGET_ENVELOPE = (100.0, 80.0, 90.0)
RULED = True  # Stable, slightly faceted shoulders; smooth lofts need new checks.
HEIGHT = STATIONS[-1][0]
assert STATIONS[0][0] == 0 and 0 < FLOOR < HEIGHT
assert WALL_INSET > 0 and CUTTER_OVERSHOOT > 0
assert 0 <= FOOT_HEIGHT < HEIGHT
assert all(a[0] < b[0] for a, b in zip(STATIONS, STATIONS[1:]))


def section(station, inset=0.0):
    z, width, depth, radius, cx, cy = station
    assert 0 < radius - inset < min(width, depth) / 2 - inset
    return Pos(cx, cy, z) * RectangleRounded(
        width - 2 * inset, depth - 2 * inset, radius - inset
    )


def station_at(z, stations):
    """Interpolate the ruled section controls at the cavity's floor plane."""
    for lower, upper in zip(stations, stations[1:]):
        if lower[0] <= z <= upper[0]:
            fraction = (z - lower[0]) / (upper[0] - lower[0])
            return tuple(a + fraction * (b - a) for a, b in zip(lower, upper))
    raise ValueError(f"No outer section at z={z}")


def build_geometry(controls=None):
    """Build the complete shape with fresh evidence, without exporting it.

    Controls are middle width (z=30 and z=62), middle depth (z=30), and top
    profile width. Save calibrated values into these STATIONS entries.
    """
    stations = [list(station) for station in STATIONS]
    if controls is not None:
        middle_width, middle_depth, top_width = map(float, controls)
        stations[2][1] = stations[3][1] = middle_width
        stations[2][2], stations[-1][1] = middle_depth, top_width
    outer = loft([section(station) for station in stations], ruled=RULED)
    if FOOT_HEIGHT:
        outer = outer.fuse(extrude(section(stations[0]), amount=FOOT_HEIGHT))
    # Keep the same cavity and floor for every trial and the final export.
    inner_stations = [station_at(FLOOR, stations)]
    inner_stations += [station for station in stations if station[0] > FLOOR]
    inner_stations += [(HEIGHT + CUTTER_OVERSHOOT, *stations[-1][1:])]
    cavity = loft([section(station, WALL_INSET) for station in inner_stations], ruled=RULED)
    intent_path = os.environ.get("AMAGINE3D_INTENT_PATH") or Path(__file__).with_name("surface_shell_intent.json")
    build = BuildSession(__file__, intent_path=intent_path)
    build.add("shell-surface", outer)
    build.cut("shell-cavity", cavity)
    # Any finishing belongs here, before both measurements and public export.
    shell = build.part("surface-shell")
    assert len(shell.solids()) == 1 and shell.is_valid
    assert shell.is_inside((0, 0, FLOOR / 2)), "The base must remain closed"
    assert not shell.is_inside((0, 0, FLOOR + 0.1)), "The cavity must reach its floor"
    assert not shell.is_inside((*stations[-1][4:], HEIGHT - 0.1)), "The top must remain open"
    return build


def measure_finished(controls):
    """Call from a separate ordinary Python calibration script after intent exists.

    Failed probes inside managed draft/compile would leave run diagnostics.
    This callback measures actual BRep; it does not export or accept a candidate.
    """
    shape = build_geometry(controls).part("surface-shell")
    top = measure_section(shape, Plane.XY.offset(TOP_PLANE_Z))
    assert top["outer_envelope"] is not None, "The required top section is missing"
    bounds = shape.bounding_box().size
    return np.array([bounds.X, bounds.Y, top["outer_envelope"]["width_u_mm"]])


def main():
    build_geometry().export()


if __name__ == "__main__":
    main()
