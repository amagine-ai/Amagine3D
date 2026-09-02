from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import interface_geometry  # noqa: E402
import assembly_check  # noqa: E402


def _pin_contract() -> tuple[dict, dict]:
    intent = {
        "manufacturing": {
            "interfaces": [
                {
                    "id": "pin-guide",
                    "between": ["pin", "guide"],
                    "connection": "pin-socket",
                    "assembly_axis": "+Z",
                    "clearances_mm": {"diameter": 0.4},
                    "engagement_mm": 3.0,
                    "features": ["pin/stem", "guide/bore"],
                }
            ]
        }
    }
    scene = {
        "interfaces": [
            {
                "id": "pin-guide",
                "kind": "pin-socket",
                "male": {
                    "partId": "pin",
                    "featureId": "pin/stem",
                    "dimensionsMm": {"diameter": 4.0},
                },
                "female": {
                    "partId": "guide",
                    "featureId": "guide/bore",
                    "dimensionsMm": {"diameter": 4.4},
                    "derivedDimensionsMm": {
                        "diameter": {
                            "from": "male.diameter",
                            "offsetMm": 0.4,
                        }
                    },
                },
            }
        ]
    }
    return intent, scene


def _record(low: list[float], high: list[float]) -> dict:
    return {"bbox_mm": {"min": low, "max": high}}


def _cylinder(radius: float, height: float, z: float) -> trimesh.Trimesh:
    mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=64)
    mesh.apply_translation([0.0, 0.0, z])
    return mesh


def _annulus(
    inner: float,
    outer: float,
    height: float,
    z: float,
) -> trimesh.Trimesh:
    mesh = trimesh.creation.annulus(
        r_min=inner,
        r_max=outer,
        height=height,
        sections=64,
    )
    mesh.apply_translation([0.0, 0.0, z])
    return mesh


def _self_tapping_contract() -> tuple[dict, dict]:
    intent = {
        "manufacturing": {
            "interfaces": [
                {
                    "id": "service-joint",
                    "connection": "self-tapping-screw",
                    "fastening": {
                        "cutter_overshoot_mm": 1.0,
                        "cover_thickness_mm": 2.4,
                        "pilot_tip_clearance_mm": 0.8,
                        "minimum_boss_wall_mm": 1.8,
                        "minimum_root_embed_mm": 0.4,
                    },
                    "features": [
                        "cover/clearance",
                        "receiver/pilot",
                        "receiver/boss",
                    ],
                }
            ]
        }
    }
    scene = {
        "interfaces": [
            {
                "id": "service-joint",
                "kind": "self-tapping-screw",
                "fasteners": [
                    {
                        "id": "axis-a",
                        "axis": {
                            "originMm": [0.0, 0.0, 0.0],
                            "direction": [0.0, 0.0, 1.0],
                        },
                        "cutterOvershootMm": 1.0,
                        "cover": {
                            "partId": "cover",
                            "featureId": "cover/clearance",
                            "diameterMm": 3.4,
                            "thicknessMm": 2.4,
                        },
                        "receiver": {
                            "partId": "receiver",
                            "featureId": "receiver/pilot",
                            "bossFeatureId": "receiver/boss",
                            "diameterMm": 2.6,
                            "bossOuterDiameterMm": 7.5,
                            "engagementMm": 6.0,
                            "tipClearanceMm": 0.8,
                            "closedEndMm": 1.2,
                            "minimumBossWallMm": 1.8,
                            "minimumRootEmbedMm": 0.4,
                        },
                    }
                ],
            }
        ]
    }
    return intent, scene


def _self_tapping_feature_records(
    *,
    cover_thickness: float = 2.4,
    cutter_overshoot: float = 1.0,
) -> dict[str, dict]:
    clearance_radius = 3.4 / 2
    pilot_radius = 2.6 / 2
    pilot_depth = 6.0 + 0.8
    boss_radius = 7.5 / 2

    def record(
        *,
        radius: float,
        z_min: float,
        z_max: float,
        volume_mm3: float,
    ) -> dict:
        return {
            "bbox_mm": {
                "min": [-radius, -radius, z_min],
                "max": [radius, radius, z_max],
                "size": [2 * radius, 2 * radius, z_max - z_min],
            },
            "volume_mm3": volume_mm3,
        }

    return {
        "cover/clearance": record(
            radius=clearance_radius,
            z_min=-cover_thickness - cutter_overshoot,
            z_max=cutter_overshoot,
            volume_mm3=math.pi
            * clearance_radius**2
            * (cover_thickness + 2 * cutter_overshoot),
        ),
        "receiver/pilot": record(
            radius=pilot_radius,
            z_min=-cutter_overshoot,
            z_max=pilot_depth,
            volume_mm3=math.pi
            * pilot_radius**2
            * (pilot_depth + cutter_overshoot),
        ),
        "receiver/boss": record(
            radius=boss_radius,
            z_min=0.0,
            z_max=pilot_depth + 1.2,
            volume_mm3=math.pi * boss_radius**2 * (pilot_depth + 1.2),
        ),
    }


def _self_tapping_meshes(
    *,
    open_pilot: bool,
    cover_thickness: float = 2.4,
) -> dict[str, trimesh.Trimesh]:
    cover = trimesh.creation.box(extents=[20.0, 20.0, cover_thickness])
    cover.apply_translation([0.0, 0.0, -cover_thickness / 2])
    clearance = trimesh.creation.cylinder(
        radius=1.7,
        segment=[
            [0.0, 0.0, -cover_thickness - 0.1],
            [0.0, 0.0, 0.1],
        ],
        sections=96,
    )
    cover = trimesh.boolean.difference(
        [cover, clearance],
        engine="manifold",
        check_volume=True,
    )

    receiver = trimesh.creation.cylinder(
        radius=3.75,
        segment=[[0.0, 0.0, 0.0], [0.0, 0.0, 8.0]],
        sections=96,
    )
    if open_pilot:
        pilot = trimesh.creation.cylinder(
            radius=1.3,
            segment=[[0.0, 0.0, -0.1], [0.0, 0.0, 6.8]],
            sections=96,
        )
        receiver = trimesh.boolean.difference(
            [receiver, pilot],
            engine="manifold",
            check_volume=True,
        )
    return {"cover": cover, "receiver": receiver}


class InterfaceGeometryTests(unittest.TestCase):
    def test_assembly_failures_are_structured_without_interface_wrapper_duplication(self):
        audit = assembly_check.Audit()
        audit.add("part_count", False, 1, ">= 2")
        audit.add(
            "part:base:valid_single_solid",
            False,
            {"solid_count": 2, "valid": True},
            {"solid_count": 1, "valid": True},
        )
        audit.add("interface_geometry", False, {"issue_count": 1}, "passing proof")
        detailed = [
            {
                "check": "engagement",
                "code": "INTERFACE.ENGAGEMENT_SHORT",
                "expected": {"minimumEngagementMm": 3.0},
                "interfaceId": "pin-guide",
                "observed": {"engagementMm": 1.0},
                "repairHint": "Extend the paired features.",
                "severity": "error",
            }
        ]

        issues = assembly_check._structured_issues(audit, detailed)

        self.assertEqual(
            {item["check"] for item in issues},
            {"part_count", "part:base:valid_single_solid", "engagement"},
        )
        self.assertNotIn(
            "ASSEMBLY.INTERFACE_GEOMETRY",
            {item["code"] for item in issues},
        )
        part_issue = next(item for item in issues if item["check"].startswith("part:"))
        self.assertEqual(part_issue["part"], "base")
        self.assertEqual(part_issue["observed"]["solid_count"], 2)
        self.assertEqual(part_issue["expected"]["solid_count"], 1)
        self.assertTrue(part_issue["repairHint"])

    def test_contract_dimensions_engagement_and_local_proximity_pass_together(self):
        intent, scene = _pin_contract()
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records={
                "pin/stem": _record([-2.0, -2.0, 0.0], [2.0, 2.0, 4.0]),
                "guide/bore": _record([-2.2, -2.2, 1.0], [2.2, 2.2, 5.0]),
            },
            part_meshes={
                "pin": _cylinder(2.0, 4.0, 2.0),
                "guide": _annulus(2.2, 3.0, 4.0, 3.0),
            },
        )

        self.assertTrue(result["pass"], result)
        self.assertEqual(result["errors"], [])
        self.assertIn(
            "INTERFACE.SUPPORT_CONTACT_ABSENT",
            {item["code"] for item in result["warnings"]},
        )
        self.assertTrue(
            all(item["proofCapability"] == "coaxial-insertion/v1" for item in result["checks"])
        )

    def test_radial_and_axial_clearances_are_measured_independently(self):
        intent, scene = _pin_contract()
        target = intent["manufacturing"]["interfaces"][0]
        target["clearances_mm"] = {"diameter": 0.4, "length": 0.2}
        implementation = scene["interfaces"][0]
        implementation["male"]["dimensionsMm"]["length"] = 4.0
        implementation["female"]["dimensionsMm"]["length"] = 4.2
        implementation["female"]["derivedDimensionsMm"]["length"] = {
            "from": "male.length",
            "offsetMm": 0.2,
        }

        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records={
                "pin/stem": _record([-2.0, -2.0, 0.0], [2.0, 2.0, 4.0]),
                "guide/bore": _record([-2.2, -2.2, 0.0], [2.2, 2.2, 4.2]),
            },
            part_meshes={},
        )

        self.assertTrue(result["pass"], result)
        clearances = {
            item["field"]: item["expected"]
            for item in result["checks"]
            if item["check"] == "clearance"
        }
        self.assertEqual(clearances, {"diameter": 0.4, "length": 0.2})

    def test_independent_interface_failures_are_aggregated(self):
        intent, scene = _pin_contract()
        guide = _annulus(2.5, 3.5, 4.0, 7.0)
        guide.apply_translation([1.0, 0.0, 0.0])
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records={
                "pin/stem": _record([-2.0, -2.0, 0.0], [2.0, 2.0, 4.0]),
                "guide/bore": _record([-1.5, -2.5, 5.0], [3.5, 2.5, 9.0]),
            },
            part_meshes={
                "pin": _cylinder(2.0, 4.0, 2.0),
                "guide": guide,
            },
        )

        codes = {item["code"] for item in result["errors"]}
        self.assertFalse(result["pass"])
        self.assertTrue(
            {
                "INTERFACE.ASSEMBLY_GAP",
                "INTERFACE.AXIS_MISALIGNED",
                "INTERFACE.CLEARANCE_MISMATCH",
                "INTERFACE.DIMENSION_DRIFT",
                "INTERFACE.ENGAGEMENT_SHORT",
            }.issubset(codes),
            result,
        )

    def test_surface_contact_does_not_require_volumetric_interpenetration(self):
        intent = {
            "manufacturing": {
                "interfaces": [
                    {
                        "id": "bond",
                        "between": ["base", "cover"],
                        "connection": "glue-face",
                        "assembly_axis": "+Z",
                        "engagement_mm": 1.0,
                        "features": ["base/face", "cover/face"],
                    }
                ]
            }
        }
        scene = {
            "interfaces": [
                {
                    "id": "bond",
                    "kind": "glue-face",
                    "male": {
                        "partId": "base",
                        "featureId": "base/face",
                        "dimensionsMm": {"width": 4.0},
                    },
                    "female": {
                        "partId": "cover",
                        "featureId": "cover/face",
                        "dimensionsMm": {"width": 4.0},
                    },
                }
            ]
        }
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records={
                "base/face": _record([-2.0, -2.0, 0.99], [2.0, 2.0, 1.0]),
                "cover/face": _record([-2.0, -2.0, 1.0], [2.0, 2.0, 1.01]),
            },
            part_meshes={
                "base": trimesh.creation.box(extents=[4.0, 4.0, 2.0]),
                "cover": trimesh.creation.box(
                    extents=[4.0, 4.0, 2.0],
                    transform=trimesh.transformations.translation_matrix([0, 0, 2]),
                ),
            },
        )

        self.assertTrue(result["pass"], result)
        self.assertNotIn("engagement", {item["check"] for item in result["checks"]})

    def test_brep_self_tapping_uses_the_same_physical_witness_proof(self):
        intent, scene = _self_tapping_contract()
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records=_self_tapping_feature_records(),
            part_meshes=_self_tapping_meshes(open_pilot=True),
        )

        self.assertTrue(result["pass"], result)
        proof = next(
            item
            for item in result["checks"]
            if item["check"] == "fastener-witness-volumes"
        )
        self.assertEqual(proof["offenderId"], "axis-a")
        self.assertTrue(all(proof["observed"]["checks"].values()))
        self.assertNotIn(
            "cover_radial_land_is_complete",
            proof["observed"]["checks"],
        )
        self.assertNotIn("coverLand", proof["observed"]["witnessVolumesMm3"])

    def test_brep_self_tapping_rejects_a_filled_pilot(self):
        intent, scene = _self_tapping_contract()
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records=_self_tapping_feature_records(),
            part_meshes=_self_tapping_meshes(open_pilot=False),
        )

        self.assertFalse(result["pass"])
        issue = next(
            item
            for item in result["errors"]
            if item["code"] == "INTERFACE.FASTENER_GEOMETRY_FAILED"
        )
        self.assertEqual(issue["interfaceId"], "service-joint")
        self.assertEqual(issue["offenderId"], "axis-a")
        self.assertFalse(issue["observed"]["checks"]["pilot_volume_is_open"])

    def test_brep_self_tapping_rejects_cover_thickness_recipe_drift(self):
        intent, scene = _self_tapping_contract()
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records=_self_tapping_feature_records(cover_thickness=1.2),
            part_meshes=_self_tapping_meshes(
                open_pilot=True,
                cover_thickness=1.2,
            ),
        )

        self.assertFalse(result["pass"], result)
        proof = result["checks"][0]["observed"]
        self.assertEqual(proof["recipeControlsMm"]["coverThickness"], 2.4)
        self.assertFalse(
            proof["checks"]["clearance_cutter_volume_matches_recipe_controls"]
        )
        self.assertFalse(
            proof["checks"]["clearance_cutter_bounds_match_recipe_controls"]
        )
        self.assertFalse(
            proof["checks"]["cover_thickness_material_is_complete"]
        )

    def test_brep_self_tapping_rejects_cutter_overshoot_recipe_drift(self):
        intent, scene = _self_tapping_contract()
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records=_self_tapping_feature_records(cutter_overshoot=0.5),
            part_meshes=_self_tapping_meshes(open_pilot=True),
        )

        self.assertFalse(result["pass"], result)
        proof = result["checks"][0]["observed"]
        self.assertEqual(proof["recipeControlsMm"]["cutterOvershoot"], 1.0)
        self.assertFalse(
            proof["checks"]["clearance_cutter_volume_matches_recipe_controls"]
        )
        self.assertFalse(
            proof["checks"]["clearance_cutter_bounds_match_recipe_controls"]
        )
        self.assertFalse(
            proof["checks"]["pilot_cutter_volume_matches_recipe_controls"]
        )
        self.assertFalse(
            proof["checks"]["pilot_cutter_bounds_match_recipe_controls"]
        )

    def test_brep_self_tapping_rejects_equal_volume_wrong_feature_bounds(self):
        intent, scene = _self_tapping_contract()
        records = _self_tapping_feature_records()
        clearance_bounds = records["cover/clearance"]["bbox_mm"]
        clearance_bounds["min"][2] += 1.0
        clearance_bounds["max"][2] += 1.0
        result = interface_geometry.audit_interfaces(
            intent=intent,
            scene=scene,
            feature_records=records,
            part_meshes=_self_tapping_meshes(open_pilot=True),
        )

        self.assertFalse(result["pass"], result)
        proof = result["checks"][0]["observed"]
        self.assertTrue(
            proof["checks"]["clearance_cutter_volume_matches_recipe_controls"]
        )
        self.assertFalse(
            proof["checks"]["clearance_cutter_bounds_match_recipe_controls"]
        )


if __name__ == "__main__":
    unittest.main()
