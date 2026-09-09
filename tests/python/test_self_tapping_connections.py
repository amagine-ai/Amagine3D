from __future__ import annotations

from pathlib import Path
import math
import sys
import unittest

import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import intent_contract  # noqa: E402
import self_tapping_geometry  # noqa: E402


def _manufacturing() -> tuple[dict, set[str]]:
    feature_ids = {
        "base-collar",
        "housing-socket",
        "base-clearance-left",
        "housing-pilot-left",
        "housing-boss-left",
        "base-clearance-right",
        "housing-pilot-right",
        "housing-boss-right",
    }
    manufacturing = {
        "mode": "multipart",
        "parts": [
            {
                "name": "housing",
                "role": "main enclosure",
                "acceptance": "one housing with a locating socket and screw bosses",
            },
            {
                "name": "base",
                "role": "removable speaker base",
                "acceptance": "one base with a collar and two clearance holes",
            },
        ],
        "interfaces": [
            {
                "id": "housing-base-service-joint",
                "between": ["housing", "base"],
                "connection": "self-tapping-screw",
                "assembly_axis": "+Z",
                "engagement_mm": 6.0,
                "features": sorted(feature_ids),
                "fastening": {
                    "screw_family": "M3 plastic thread-forming/self-tapping",
                    "nominal_diameter_mm": 3.0,
                    "pilot_diameter_mm": 2.6,
                    "clearance_diameter_mm": 3.4,
                    "boss_outer_diameter_mm": 7.5,
                    "closed_end_mm": 1.2,
                    "cutter_overshoot_mm": 1.0,
                    "cover_thickness_mm": 2.4,
                    "pilot_tip_clearance_mm": 0.8,
                    "minimum_boss_wall_mm": 1.8,
                    "minimum_root_embed_mm": 0.4,
                    "locator_pairs": [
                        {
                            "id": "housing-base-locator",
                            "male_feature": "base-collar",
                            "female_feature": "housing-socket",
                        }
                    ],
                    "fasteners": [
                        {
                            "id": "side-left",
                            "clearance_feature": "base-clearance-left",
                            "pilot_feature": "housing-pilot-left",
                            "boss_feature": "housing-boss-left",
                        },
                        {
                            "id": "side-right",
                            "clearance_feature": "base-clearance-right",
                            "pilot_feature": "housing-pilot-right",
                            "boss_feature": "housing-boss-right",
                        },
                    ],
                },
                "acceptance": (
                    "the collar locates the parts and two M3 plastic screws clamp "
                    "coaxial clearance/pilot pairs"
                ),
            }
        ],
    }
    return manufacturing, feature_ids


class SelfTappingIntentTests(unittest.TestCase):
    def test_accepts_located_two_screw_connection(self) -> None:
        manufacturing, feature_ids = _manufacturing()
        self.assertEqual(
            intent_contract.validate_manufacturing(manufacturing, feature_ids),
            [],
        )

    def test_requires_locator_and_ordered_diameters(self) -> None:
        manufacturing, feature_ids = _manufacturing()
        fastening = manufacturing["interfaces"][0]["fastening"]
        fastening["locator_pairs"] = []
        fastening["pilot_diameter_mm"] = 3.5
        errors = intent_contract.validate_manufacturing(manufacturing, feature_ids)
        text = "\n".join(errors)
        self.assertIn("locator_pairs", text)
        self.assertIn("pilot < nominal < clearance", text)

    def test_requires_every_geometry_controlling_fastener_dimension(self) -> None:
        required = (
            "cutter_overshoot_mm",
            "cover_thickness_mm",
            "pilot_tip_clearance_mm",
            "minimum_boss_wall_mm",
            "minimum_root_embed_mm",
        )
        for field in required:
            with self.subTest(field=field):
                manufacturing, feature_ids = _manufacturing()
                manufacturing["interfaces"][0]["fastening"].pop(field)
                errors = intent_contract.validate_manufacturing(
                    manufacturing,
                    feature_ids,
                )
                self.assertTrue(any(field in error for error in errors), errors)

    def test_rejects_reusing_one_hole_for_two_axes(self) -> None:
        manufacturing, feature_ids = _manufacturing()
        fasteners = manufacturing["interfaces"][0]["fastening"]["fasteners"]
        fasteners[1]["pilot_feature"] = fasteners[0]["pilot_feature"]
        errors = intent_contract.validate_manufacturing(manufacturing, feature_ids)
        self.assertTrue(any("cannot reuse" in error for error in errors), errors)

    def test_locator_features_must_be_independent_from_screw_features(self) -> None:
        manufacturing, feature_ids = _manufacturing()
        locator = manufacturing["interfaces"][0]["fastening"]["locator_pairs"][0]
        locator["female_feature"] = "housing-pilot-left"
        errors = intent_contract.validate_manufacturing(manufacturing, feature_ids)
        self.assertTrue(
            any("locator and screw features must be independent" in error for error in errors),
            errors,
        )

    def test_other_connection_recipes_remain_available(self) -> None:
        manufacturing, feature_ids = _manufacturing()
        interface = manufacturing["interfaces"][0]
        interface["connection"] = "collar-socket"
        interface.pop("fastening")
        interface["clearances_mm"] = {"diameter": 0.4}
        self.assertEqual(
            intent_contract.validate_manufacturing(manufacturing, feature_ids),
            [],
        )


class SelfTappingHeadRecessTests(unittest.TestCase):
    # Independent geometry fixtures: rectangular/circular covers, two recess
    # dimensions and two placements. No product recipe or canned model inputs.
    CASES = (
        dict(cover="box", thickness=4.8, clearance=4.6, pilot=3.3, boss=10.0,
             engagement=7.5, tip=1.1, closed=1.7, head=8.2, depth=1.2),
        dict(cover="round", thickness=5.4, clearance=3.8, pilot=2.9, boss=9.6,
             engagement=8.4, tip=0.9, closed=1.9, head=7.6, depth=2.1),
    )

    @staticmethod
    def fixture(parameters, *, oblique, state):
        p = parameters
        t, depth, clearance = p["thickness"], p["depth"], p["clearance"] / 2
        pilot_depth, overshoot = p["engagement"] + p["tip"], 0.6

        def cylinder(radius, low, high):
            shape = trimesh.creation.cylinder(radius, high - low, sections=192)
            shape.apply_translation((0, 0, (low + high) / 2))
            return shape

        def boolean(operation, *shapes):
            result = getattr(trimesh.boolean, operation)(list(shapes), engine="manifold")
            assert result.is_volume
            return result

        if p["cover"] == "box":
            cover = trimesh.creation.box((30, 26, t))
            cover.apply_translation((0, 0, -t / 2))
        else:
            cover = cylinder(12, -t, 0)
        clearance_tool = cylinder(clearance, -t - overshoot, overshoot)
        if state != "no-recess":
            clearance_tool = boolean("union", clearance_tool,
                                     cylinder(p["head"] / 2, -t - overshoot, -t + depth))
        cover = boolean("difference", cover, clearance_tool)
        boss = cylinder(p["boss"] / 2, 0, pilot_depth + p["closed"])
        pilot_tool = cylinder(p["pilot"] / 2, -overshoot, pilot_depth)
        receiver = boolean("difference", boss, pilot_tool)
        if state in ("filled", "partial", "shallow"):
            low = -t + (depth / 2 if state == "shallow" else 0)
            filling = boolean("difference", cylinder(p["head"] / 2, low, -t + depth),
                              cylinder(clearance, low - 0.1, -t + depth + 0.1))
            if state == "partial":
                half = trimesh.creation.box((20, 30, 20))
                half.apply_translation((10, 0, 0))
                filling = boolean("intersection", filling, half)
            cover = boolean("union", cover, filling)

        transform = np.eye(4)
        if oblique:
            transform = trimesh.transformations.euler_matrix(
                *map(math.radians, (23, -31, 17)), axes="sxyz")
            transform[:3, 3] = (13, -7, 19)
        for shape in (cover, receiver, clearance_tool, pilot_tool, boss):
            shape.apply_transform(transform)
        records = {
            name: {"volume_mm3": float(shape.volume),
                   "bbox_mm": {"min": shape.bounds[0].tolist(), "max": shape.bounds[1].tolist()}}
            for name, shape in (("clearance", clearance_tool), ("pilot", pilot_tool), ("boss", boss))
        }
        cover_record = {"partId": "cover", "featureId": "clearance",
                        "diameterMm": p["clearance"], "thicknessMm": t}
        if state != "no-recess":
            cover_record.update(headRecessDiameterMm=p["head"], headRecessDepthMm=depth,
                                minimumResidualWallMm=1.1)
        fastener = {
            "id": "axis", "axis": {"originMm": transform[:3, 3].tolist(),
                                       "direction": transform[:3, 2].tolist()},
            "cutterOvershootMm": overshoot, "cover": cover_record,
            "receiver": {"partId": "receiver", "featureId": "pilot", "bossFeatureId": "boss",
                         "diameterMm": p["pilot"], "bossOuterDiameterMm": p["boss"],
                         "engagementMm": p["engagement"], "tipClearanceMm": p["tip"],
                         "closedEndMm": p["closed"], "minimumBossWallMm": 2.0,
                         "minimumRootEmbedMm": 0.7},
        }
        scene = {"interfaces": [{"id": "joint", "kind": "self-tapping-screw", "fasteners": [fastener]}]}
        return scene, {"cover": cover, "receiver": receiver}, records

    def test_final_head_recess_remains_open_after_later_material_edits(self):
        for case, parameters in enumerate(self.CASES):
            for oblique in (False, True):
                for state in ("valid", "filled", "partial", "shallow", "no-recess"):
                    with self.subTest(case=case, oblique=oblique, state=state):
                        scene, parts, records = self.fixture(parameters, oblique=oblique, state=state)
                        result = self_tapping_geometry.audit_self_tapping_geometry(
                            scene, parts, feature_records=records)["joint/axis"]
                        self.assertNotIn("error", result, result)
                        checks = result["checks"]
                        self.assertTrue(checks["clearance_volume_is_open"], result)
                        blocked = state in ("filled", "partial", "shallow")
                        self.assertEqual(result["pass"], not blocked, result)
                        if state == "no-recess":
                            self.assertNotIn("head_recess_volume_is_open", checks)
                        else:
                            self.assertEqual(checks["head_recess_volume_is_open"], not blocked)
                        self.assertTrue(all(value for key, value in checks.items()
                                            if key != "head_recess_volume_is_open"), result)


if __name__ == "__main__":
    unittest.main()
