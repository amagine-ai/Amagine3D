from __future__ import annotations

import importlib.util
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from build123d import Align, Box, Location, Pos, Vertex


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skills" / "text-a3d" / "interface_recipes.py"
sys.path.insert(0, str(MODULE_PATH.parent))
from build_session import BuildSession
from cad_helpers import BuildInvariantError


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
    @contextmanager
    def draft_session(self):
        directory = ROOT / "workspace" / "skill-validation"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="screw-bind-", dir=directory) as work:
            source = Path(work) / "fixture_build.py"
            source.write_text("# Real BuildSession draft geometry fixture.\n")
            with patch.dict(os.environ, {
                "AMAGINE3D_SOURCE_PHASE": "draft",
                "AMAGINE3D_DRAFT_DIR": work,
                "AMAGINE3D_SOURCE_DIAGNOSTICS_PATH": str(Path(work) / "diagnostics.json"),
            }, clear=True):
                yield BuildSession(source, part_names=("cover", "receiver"))
            self.assertFalse(list(Path(work).glob("*intent*")))

    @staticmethod
    def screw_pair():
        return recipes.self_tapping_screw_pair(
            interface_id="cover-joint", axis_id="oblique-screw",
            cover_thickness_mm=3.2, nominal_diameter_mm=4.0,
            clearance_diameter_mm=4.6, pilot_diameter_mm=3.3,
            boss_outer_diameter_mm=10.0, engagement_mm=7.5,
            pilot_tip_clearance_mm=1.1, closed_end_mm=1.7,
            minimum_boss_wall_mm=2.5, minimum_root_embed_mm=0.7,
            head_recess_diameter_mm=8.2, head_recess_depth_mm=1.2,
            minimum_cover_land_mm=1.4, cutter_overshoot_mm=0.6,
        )

    @staticmethod
    def screw_bodies():
        # Boss spans z=0..10.3 and overlaps this receiver root by 0.7 mm.
        return (
            Pos(0, 0, -3.2) * Box(24, 20, 3.2, align=(Align.CENTER, Align.CENTER, Align.MIN)),
            Pos(0, 0, 9.6) * Box(24, 20, 2.5, align=(Align.CENTER, Align.CENTER, Align.MIN)),
        )

    def assertSameMaterial(self, actual, expected):
        self.assertTrue(is_valid(actual))
        self.assertEqual(len(actual.solids()), len(expected.solids()))
        self.assertAlmostEqual(actual.volume, expected.volume, places=6)
        self.assertLess(abs((actual - expected).volume), 1e-6)
        self.assertLess(abs((expected - actual).volume), 1e-6)

    def test_screw_bind_places_actual_bores_and_metadata_from_one_transform(self):
        pair = self.screw_pair()
        original_evidence = deepcopy(pair.evidence)
        original_shapes = [deepcopy(shape) for shape in (
            pair.clearance_cutter, pair.pilot_cutter, pair.receiver_boss)]
        location = Location((13, -7, 19), (27, 38, -16))
        point = lambda x, y, z: (location * Vertex(x, y, z)).center()
        cover, receiver = self.screw_bodies()
        with self.draft_session() as build:
            build.add("cover-body", location * cover, part_name="cover")
            build.add("receiver-body", location * receiver, part_name="receiver")
            record = pair.bind(
                build, location=location, cover_part="cover", receiver_part="receiver",
                clearance_feature="clearance", pilot_feature="pilot", boss_feature="boss",
                boss_mode="add",
            )
            cover_result, receiver_result = build.part("cover"), build.part("receiver")
            # Independent cylinder-volume expectations include the counterbore,
            # blind pilot and real boss addition outside the existing root.
            removed_cover = math.pi * (2.3**2 * 3.2 + (4.1**2 - 2.3**2) * 1.2)
            self.assertAlmostEqual(cover_result.volume, 24 * 20 * 3.2 - removed_cover, places=5)
            self.assertAlmostEqual(
                receiver_result.volume, 24 * 20 * 2.5 + math.pi * 5**2 * 9.6 - math.pi * 1.65**2 * 8.6,
                places=5,
            )
            for shape in (cover_result, receiver_result):
                self.assertTrue(is_valid(shape))
                self.assertEqual(len(shape.solids()), 1)
            for shape, samples in (
                (cover_result, [(2.25, 0, -0.6, False), (2.35, 0, -0.6, True),
                                (4.05, 0, -2.6, False), (4.15, 0, -2.6, True),
                                (4.05, 0, -1.8, True)]),
                (receiver_result, [(1.60, 0, 4, False), (1.70, 0, 4, True),
                                   (0, 0, 8.55, False), (0, 0, 8.65, True),
                                   (4.95, 0, 4, True), (5.05, 0, 4, False)]),
            ):
                for x, y, z, material in samples:
                    with self.subTest(point=(x, y, z), material=material):
                        self.assertEqual(shape.is_inside(point(x, y, z)), material)
            self.assertEqual(record["id"], "oblique-screw")
            for actual, expected in zip(record["axis"]["originMm"], (13, -7, 19)):
                self.assertAlmostEqual(actual, expected, places=7)
            direction = point(0, 0, 1) - point(0, 0, 0)
            for actual, expected in zip(record["axis"]["direction"], direction):
                self.assertAlmostEqual(actual, expected, places=7)
            self.assertTrue(all(abs(value) > 0.1 for value in direction))
            self.assertEqual(record["screwFamily"], "M4 plastic thread-forming/self-tapping")
            self.assertEqual(record["nominalDiameterMm"], 4.0)
            self.assertEqual(record["cutterOvershootMm"], 0.6)
            self.assertEqual(record["cover"], {
                "partId": "cover", "featureId": "clearance", "diameterMm": 4.6,
                "thicknessMm": 3.2, "headRecessDiameterMm": 8.2,
                "headRecessDepthMm": 1.2, "minimumResidualWallMm": 1.4,
            })
            self.assertEqual(record["receiver"], {
                "partId": "receiver", "featureId": "pilot", "bossFeatureId": "boss",
                "diameterMm": 3.3, "bossOuterDiameterMm": 10.0, "engagementMm": 7.5,
                "closedEndMm": 1.7, "minimumBossWallMm": 2.5,
                "minimumRootEmbedMm": 0.7, "tipClearanceMm": 1.1,
            })
            for feature, owner, role in (("clearance", "cover", "cutter"),
                                          ("pilot", "receiver", "cutter"), ("boss", "receiver", "solid")):
                self.assertEqual((build._features[feature]["owner"], build._features[feature]["role"]), (owner, role))
        self.assertEqual(pair.evidence, original_evidence)
        for actual, expected in zip((pair.clearance_cutter, pair.pilot_cutter, pair.receiver_boss), original_shapes):
            self.assertSameMaterial(actual, expected)

    def test_screw_bind_observe_is_explicit_and_failed_group_restores_both_parts(self):
        pair = self.screw_pair()
        cover, root = self.screw_bodies()
        args = dict(location=Location(), cover_part="cover", receiver_part="receiver",
                    clearance_feature="clearance", pilot_feature="pilot", boss_feature="boss")
        with self.draft_session() as build:
            build.add("cover-body", cover, part_name="cover")
            contained = root + pair.receiver_boss
            build.add("receiver-body", contained, part_name="receiver")
            record = pair.bind(build, **args, boss_mode="observe")
            self.assertEqual(record["receiver"]["bossFeatureId"], "boss")
            self.assertAlmostEqual(build.part("receiver").volume, contained.volume - math.pi * 1.65**2 * 8.6, places=5)
            self.assertFalse(build.part("receiver").is_inside((0, 0, 4)))
            self.assertFalse(any(e.get("id") == "boss" and e.get("kind") == "union" for e in build._evidence.events))

        for scenario in ("redundant-add", "cover-miss", "invalid-mode", "same-part"):
            with self.subTest(scenario=scenario), self.draft_session() as build:
                build.add("cover-body", Pos(100, 0, 0) * cover if scenario == "cover-miss" else cover, part_name="cover")
                build.add("receiver-body", root + pair.receiver_boss if scenario == "redundant-add" else root, part_name="receiver")
                before_parts = {name: build.part(name) for name in ("cover", "receiver")}
                before_features, before_evidence = deepcopy(build._features), deepcopy(build._evidence)
                kwargs = {**args, "boss_mode": "invalid" if scenario == "invalid-mode" else "add"}
                if scenario == "same-part":
                    kwargs["receiver_part"] = "cover"
                output = io.StringIO()
                expected_error = recipes.InterfaceRecipeError if scenario in ("invalid-mode", "same-part") else BuildInvariantError
                with redirect_stdout(output), self.assertRaises(expected_error):
                    pair.bind(build, **kwargs)
                if scenario in ("redundant-add", "cover-miss"):
                    issue = json.loads(output.getvalue())["issues"][0]
                    self.assertEqual(issue["code"], "SOURCE.UNION_NO_EFFECT" if scenario == "redundant-add" else "SOURCE.CUT_MISSED_OWNER")
                    self.assertEqual(issue["featureId"], "boss" if scenario == "redundant-add" else "clearance")
                for name, shape in before_parts.items():
                    self.assertSameMaterial(build.part(name), shape)
                self.assertEqual(set(build._features), set(before_features))
                for name, feature in before_features.items():
                    self.assertEqual((build._features[name]["owner"], build._features[name]["role"]), (feature["owner"], feature["role"]))
                    self.assertSameMaterial(build._features[name]["shape"], feature["shape"])
                for field in ("events", "features", "parameters", "issues"):
                    self.assertEqual(getattr(build._evidence, field), getattr(before_evidence, field), field)

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

    def test_self_tapping_pair_keeps_cover_pilot_and_boss_coaxial(self) -> None:
        pair = recipes.self_tapping_screw_pair(
            interface_id="housing-base-fastener",
            axis_id="side-left",
            cover_thickness_mm=2.4,
            head_recess_diameter_mm=6.2,
            head_recess_depth_mm=1.0,
            minimum_cover_land_mm=0.8,
        )
        for shape in (
            pair.clearance_cutter,
            pair.pilot_cutter,
            pair.receiver_boss,
        ):
            self.assertTrue(is_valid(shape))
            box = shape.bounding_box()
            self.assertAlmostEqual((box.min.X + box.max.X) / 2, 0.0, places=6)
            self.assertAlmostEqual((box.min.Y + box.max.Y) / 2, 0.0, places=6)
        evidence = pair.evidence
        self.assertEqual(evidence["axis"]["id"], "side-left")
        self.assertEqual(evidence["axis"]["direction"], [0.0, 0.0, 1.0])
        self.assertAlmostEqual(evidence["cover"]["clearance_diameter_mm"], 3.4)
        self.assertAlmostEqual(evidence["receiver"]["pilot_diameter_mm"], 2.6)
        self.assertAlmostEqual(evidence["receiver"]["boss_wall_mm"], 2.45)
        self.assertAlmostEqual(
            evidence["receiver"]["minimum_root_embed_mm"], 0.4
        )
        self.assertAlmostEqual(pair.receiver_boss.bounding_box().min.Z, 0.0)
        self.assertEqual(
            evidence["screw"]["manufacturing"],
            "purchased-hardware-excluded",
        )
        self.assertEqual(
            evidence["screw"]["family"],
            "M3 plastic thread-forming/self-tapping",
        )
        self.assertAlmostEqual(
            evidence["screw"]["minimum_under_head_length_mm"],
            7.4,
        )
        self.assertAlmostEqual(
            evidence["screw"]["maximum_under_head_length_mm"],
            7.4,
        )

    def test_self_tapping_pair_has_no_implicit_cover_land_without_a_recess(self) -> None:
        pair = recipes.self_tapping_screw_pair(
            interface_id="housing-base-fastener",
            axis_id="side-left",
            cover_thickness_mm=2.4,
        )

        self.assertNotIn("minimum_cover_land_mm", pair.evidence["cover"])

    def test_recommended_screw_lengths_preserve_engagement_and_tip_gap_in_real_bores(self):
        # Cases are independent of any product dimensions or exported example.
        # Both recommended endpoints must preserve the explicitly reserved void.
        cases = (
            (4.8, 0.0, 9.2, 1.3, 3.0, 3.4, 2.6, 7.5),
            (5.4, 1.6, 8.4, 0.9, 4.0, 4.6, 3.3, 10.0),
            (3.1, 0.7, 4.6, 0.5, 2.5, 2.9, 2.1, 7.0),
        )
        for thickness, recess, engagement, gap, nominal, clearance, pilot, boss in cases:
            for location in (Location(), Location((17, -11, 23), (31, -27, 19))):
                with self.subTest(thickness=thickness, recess=recess, placement=location):
                    recess_args = dict(head_recess_diameter_mm=clearance + 2,
                                       head_recess_depth_mm=recess,
                                       minimum_cover_land_mm=1.5) if recess else {}
                    pair = recipes.self_tapping_screw_pair(
                        interface_id="independent-joint", axis_id="a", cover_thickness_mm=thickness,
                        nominal_diameter_mm=nominal, clearance_diameter_mm=clearance,
                        pilot_diameter_mm=pilot, boss_outer_diameter_mm=boss,
                        engagement_mm=engagement, pilot_tip_clearance_mm=gap,
                        closed_end_mm=2.4, **recess_args,
                    )
                    receiver = location * (pair.receiver_boss - pair.pilot_cutter)
                    cover = location * (Pos(0, 0, -thickness)
                        * Box(20, 20, thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
                        - pair.clearance_cutter)
                    point = lambda x, y, z: (location * Vertex(x, y, z)).center()
                    seat_z = -thickness + recess
                    # Verify the actual head-bearing face on the finished cover.
                    radius = clearance / 2 + 0.5
                    self.assertFalse(cover.is_inside(point(radius, 0, seat_z - 0.001)))
                    self.assertTrue(cover.is_inside(point(radius, 0, seat_z + 0.001)))
                    self.assertTrue(is_valid(receiver))
                    self.assertEqual(len(receiver.solids()), 1)
                    for endpoint in ("minimum_under_head_length_mm", "maximum_under_head_length_mm"):
                        tip_z = seat_z + pair.evidence["screw"][endpoint]
                        self.assertGreaterEqual(tip_z + 1e-9, engagement)
                        # The requested tip gap must be void right up to the
                        # physical blind end, with real closed material after it.
                        self.assertFalse(receiver.is_inside(point(0, 0, tip_z + gap - 0.001)), endpoint)
                        self.assertTrue(receiver.is_inside(point(0, 0, tip_z + gap + 0.001)), endpoint)

    def test_self_tapping_pair_requires_an_explicit_cover_land_with_a_recess(self) -> None:
        with self.assertRaisesRegex(
            recipes.InterfaceRecipeError,
            "minimum_cover_land_mm is required",
        ):
            recipes.self_tapping_screw_pair(
                interface_id="housing-base-fastener",
                axis_id="side-left",
                cover_thickness_mm=2.4,
                head_recess_diameter_mm=6.2,
                head_recess_depth_mm=1.0,
            )

    def test_self_tapping_pair_requires_positive_tip_clearance_and_overshoot(self) -> None:
        for field in ("pilot_tip_clearance_mm", "cutter_overshoot_mm"):
            with self.subTest(field=field), self.assertRaisesRegex(
                recipes.InterfaceRecipeError,
                f"{field} must be finite and positive",
            ):
                recipes.self_tapping_screw_pair(
                    interface_id="housing-base-fastener",
                    axis_id="side-left",
                    cover_thickness_mm=2.4,
                    **{field: 0.0},
                )

    def test_self_tapping_pair_rejects_inverted_hole_sizes(self) -> None:
        with self.assertRaisesRegex(
            recipes.InterfaceRecipeError,
            "pilot < nominal < clearance",
        ):
            recipes.self_tapping_screw_pair(
                interface_id="housing-base-fastener",
                axis_id="side-left",
                cover_thickness_mm=2.4,
                pilot_diameter_mm=3.4,
                clearance_diameter_mm=3.2,
            )

    def test_self_tapping_pair_rejects_an_underbuilt_boss(self) -> None:
        with self.assertRaisesRegex(
            recipes.InterfaceRecipeError,
            "minimum_boss_wall_mm",
        ):
            recipes.self_tapping_screw_pair(
                interface_id="housing-base-fastener",
                axis_id="side-left",
                cover_thickness_mm=2.4,
                boss_outer_diameter_mm=5.0,
            )


if __name__ == "__main__":
    unittest.main()
