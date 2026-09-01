from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skills" / "text-a3d" / "interface_recipes.py"


def load_module():
    spec = importlib.util.spec_from_file_location("a3d_interface_recipes", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recipes = load_module()


def is_valid(shape) -> bool:
    value = shape.is_valid
    return bool(value() if callable(value) else value)


class InterfaceRecipeTests(unittest.TestCase):
    def test_collar_socket_derives_both_sides_from_one_profile(self) -> None:
        pair = recipes.collar_socket(
            interface_id="housing-base",
            width_mm=76,
            depth_mm=60,
            radius_mm=6,
            engagement_mm=8,
            radial_clearance_mm=0.4,
            collar_wall_mm=1.6,
        )
        self.assertTrue(is_valid(pair.male))
        self.assertEqual(len(pair.male.solids()), 1)
        self.assertTrue(is_valid(pair.female_cutter))
        fit = pair.evidence["fit"]
        self.assertAlmostEqual(fit["socket_width_mm"], 76.8)
        self.assertAlmostEqual(fit["socket_depth_mm"], 60.8)

    def test_inset_pocket_derives_clearance_and_depth(self) -> None:
        pair = recipes.inset_pocket(
            interface_id="housing-screen",
            width_mm=56,
            height_mm=40,
            radius_mm=8,
            insert_thickness_mm=1.4,
            side_clearance_mm=0.35,
            axial_clearance_mm=0.1,
            requested_proud_mm=0.05,
        )
        self.assertTrue(is_valid(pair.male))
        self.assertTrue(is_valid(pair.female_cutter))
        self.assertAlmostEqual(pair.evidence["fit"]["pocket_depth_mm"], 1.45)
        box = pair.female_cutter.bounding_box()
        self.assertAlmostEqual(box.max.X - box.min.X, 56.7, places=5)
        self.assertAlmostEqual(box.max.Y - box.min.Y, 40.7, places=5)

    def test_retained_slider_is_one_body_and_guide_is_derived(self) -> None:
        pair = recipes.retained_slider(
            interface_id="base-button",
            cap_diameter_mm=6.2,
            cap_thickness_mm=1.4,
            stem_diameter_mm=3.0,
            stem_length_mm=2.8,
            flange_diameter_mm=5.2,
            flange_thickness_mm=1.2,
            radial_clearance_mm=0.35,
            guide_depth_mm=3.0,
        )
        self.assertTrue(is_valid(pair.male))
        self.assertEqual(len(pair.male.solids()), 1)
        self.assertAlmostEqual(pair.evidence["fit"]["guide_diameter_mm"], 3.7)
        guide_box = pair.female_cutter.bounding_box()
        self.assertAlmostEqual(guide_box.max.X - guide_box.min.X, 3.7, places=5)

    def test_invalid_flange_is_rejected(self) -> None:
        with self.assertRaises(recipes.InterfaceRecipeError):
            recipes.retained_slider(
                interface_id="base-button",
                cap_diameter_mm=6,
                cap_thickness_mm=1,
                stem_diameter_mm=4,
                stem_length_mm=3,
                flange_diameter_mm=4,
                flange_thickness_mm=1,
                radial_clearance_mm=0.3,
                guide_depth_mm=3,
            )

    def test_pin_socket_derives_radial_and_axial_clearance(self) -> None:
        pair = recipes.pin_socket(
            interface_id="cover-pin",
            pin_diameter_mm=3.0,
            engagement_mm=5.0,
            radial_clearance_mm=0.2,
            axial_clearance_mm=0.25,
        )
        self.assertTrue(is_valid(pair.male))
        self.assertTrue(is_valid(pair.female_cutter))
        self.assertAlmostEqual(pair.evidence["fit"]["socket_diameter_mm"], 3.4)
        self.assertAlmostEqual(pair.evidence["fit"]["socket_depth_mm"], 5.25)

    def test_hinge_pin_returns_one_printable_pin_and_shared_bore(self) -> None:
        pair = recipes.hinge_pin(
            interface_id="lid-hinge",
            pin_diameter_mm=2.8,
            span_mm=18.0,
            radial_clearance_mm=0.2,
            axial_clearance_mm=0.4,
        )
        self.assertTrue(is_valid(pair.male))
        self.assertEqual(len(pair.male.solids()), 1)
        self.assertTrue(is_valid(pair.female_cutter))
        self.assertAlmostEqual(pair.evidence["fit"]["bore_diameter_mm"], 3.2)
        self.assertEqual(pair.evidence["mobility"]["type"], "rotational")


if __name__ == "__main__":
    unittest.main()
