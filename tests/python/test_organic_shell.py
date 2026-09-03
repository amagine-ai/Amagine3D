from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import organic_shell  # noqa: E402


def _irregular_outer(x: float, y: float, z: float) -> float:
    left = 13.0 - math.sqrt((x + 5.0) ** 2 + (y + 1.0) ** 2 + (z - 2.0) ** 2)
    right = 10.0 - math.sqrt((x - 7.0) ** 2 + (y - 2.0) ** 2 + (z + 1.0) ** 2)
    blend = 3.0
    weight = max(blend - abs(left - right), 0.0) / blend
    return max(left, right) + weight * weight * blend * 0.25


def _bottom_opening(x: float, y: float, z: float) -> float:
    return min(8.0 - abs(x), 6.0 - abs(y), 8.0 - abs(z + 13.0))


class OrganicShellTests(unittest.TestCase):
    def test_builds_one_open_irregular_body_without_mesh_repair(self) -> None:
        result = organic_shell.build_organic_shell(
            outer_sdf=_irregular_outer,
            bounds_mm=(-22, -18, -18, 22, 18, 18),
            wall_thickness_mm=2.0,
            edge_length_mm=1.0,
            cavity_strategy="open-cavity",
            opening_sdfs=[_bottom_opening],
        )

        self.assertTrue(result.mesh.is_watertight)
        self.assertTrue(result.mesh.is_winding_consistent)
        self.assertTrue(result.mesh.is_volume)
        self.assertEqual(result.evidence["physicalBodyCount"], 1)
        self.assertEqual(result.evidence["surfaceComponentCount"], 1)
        self.assertEqual(result.evidence["cavityStrategy"], "open-cavity")

    def test_builds_closed_self_supporting_roof_as_one_material_body(self) -> None:
        outer = lambda x, y, z: 14.0 - math.sqrt(x * x + y * y + z * z)
        footprint = lambda x, y: 5.0 - math.hypot(x, y)
        cavity = organic_shell.self_supporting_cavity(
            footprint_sdf=footprint,
            footprint_bounds_mm=(-8, -8, 8, 8),
            floor_z_mm=-5.0,
            roof_start_z_mm=1.0,
            closure_inset_mm=5.4,
        )

        result = organic_shell.build_organic_shell(
            outer_sdf=outer,
            bounds_mm=(-16, -16, -16, 16, 16, 16),
            wall_thickness_mm=2.0,
            edge_length_mm=1.0,
            cavity_strategy="self-supporting-cavity",
            interior_sdf=cavity,
        )

        self.assertTrue(result.mesh.is_volume)
        self.assertEqual(result.evidence["physicalBodyCount"], 1)
        self.assertEqual(result.evidence["surfaceComponentCount"], 2)
        self.assertEqual(
            result.evidence["selfSupportingRoof"][
                "roofAngleFromHorizontalDeg"
            ],
            45.0,
        )

    def test_rejects_a_flat_ceiling_before_3d_meshing(self) -> None:
        with self.assertRaisesRegex(
            organic_shell.OrganicShellError,
            "flat ceiling",
        ):
            organic_shell.self_supporting_cavity(
                footprint_sdf=lambda x, y: 5.0 - math.hypot(x, y),
                footprint_bounds_mm=(-8, -8, 8, 8),
                floor_z_mm=-5.0,
                roof_start_z_mm=1.0,
                closure_inset_mm=4.0,
            )

    def test_rejects_a_grid_too_coarse_to_resolve_the_wall(self) -> None:
        with self.assertRaisesRegex(
            organic_shell.OrganicShellError,
            "at most half wall_thickness_mm",
        ):
            organic_shell.build_organic_shell(
                outer_sdf=_irregular_outer,
                bounds_mm=(-22, -18, -18, 22, 18, 18),
                wall_thickness_mm=1.0,
                edge_length_mm=1.0,
                cavity_strategy="open-cavity",
                opening_sdfs=[_bottom_opening],
            )


if __name__ == "__main__":
    unittest.main()
