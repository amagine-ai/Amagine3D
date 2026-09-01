from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import intent_contract  # noqa: E402


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
                "clearance_mm": 0.4,
                "engagement_mm": 6.0,
                "features": sorted(feature_ids),
                "fastening": {
                    "screw_family": "M3 plastic thread-forming/self-tapping",
                    "nominal_diameter_mm": 3.0,
                    "pilot_diameter_mm": 2.6,
                    "clearance_diameter_mm": 3.4,
                    "boss_outer_diameter_mm": 7.5,
                    "closed_end_mm": 1.2,
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
        self.assertEqual(
            intent_contract.validate_manufacturing(manufacturing, feature_ids),
            [],
        )


if __name__ == "__main__":
    unittest.main()
