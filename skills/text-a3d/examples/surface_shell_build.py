"""Build only through a3d compile; a BRep loft owns the complete hollow shell."""
from build123d import Pos, RectangleRounded, loft
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
RULED = True  # Stable, slightly faceted shoulders; smooth lofts need new checks.
HEIGHT = STATIONS[-1][0]
assert STATIONS[0][0] == 0 and 0 < FLOOR < HEIGHT
assert WALL_INSET > 0 and CUTTER_OVERSHOOT > 0
assert all(a[0] < b[0] for a, b in zip(STATIONS, STATIONS[1:]))


def section(station, inset=0.0):
    z, width, depth, radius, cx, cy = station
    assert 0 < radius - inset < min(width, depth) / 2 - inset
    return Pos(cx, cy, z) * RectangleRounded(
        width - 2 * inset, depth - 2 * inset, radius - inset
    )


def station_at(z):
    """Interpolate the ruled section controls at the cavity's floor plane."""
    for lower, upper in zip(STATIONS, STATIONS[1:]):
        if lower[0] <= z <= upper[0]:
            fraction = (z - lower[0]) / (upper[0] - lower[0])
            return tuple(a + fraction * (b - a) for a, b in zip(lower, upper))
    raise ValueError(f"No outer section at z={z}")


outer = loft([section(station) for station in STATIONS], ruled=RULED)
# The cavity begins above the base and continues beyond the opening, leaving
# a closed FLOOR-thick base and an annular rim rather than a cap over the top.
inner_stations = [station_at(FLOOR)]
inner_stations += [station for station in STATIONS if station[0] > FLOOR]
inner_stations += [(HEIGHT + CUTTER_OVERSHOOT, *STATIONS[-1][1:])]
cavity = loft([section(station, WALL_INSET) for station in inner_stations], ruled=RULED)
build = BuildSession(__file__)
build.add("shell-surface", outer)
shell = build.cut("shell-cavity", cavity)

# Section inset is not normal wall thickness on a sloping 3D surface. Public
# compile checks wall thickness and overhangs after any station or inset edit.
assert len(shell.solids()) == 1 and shell.is_valid
assert shell.is_inside((0, 0, FLOOR / 2)), "The base must remain closed"
assert not shell.is_inside((0, 0, FLOOR + 0.1)), "The cavity must reach its floor"
assert not shell.is_inside((*STATIONS[-1][4:], HEIGHT - 0.1)), "The top must remain open"

build.export()
