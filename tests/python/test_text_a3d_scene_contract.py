from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import scene_contract  # noqa: E402
import shape_consistency  # noqa: E402
from tests.python.intent_fixture import bind_scene_intent  # noqa: E402


def _scene(root: Path) -> dict:
    scene = {
        "schema": "evidence-semantic-scene/v1",
        "revision": "rev-001",
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "parts": [
            {"id": "base", "representationMaster": "brep"},
            {"id": "button", "representationMaster": "mesh"},
        ],
        "nodes": [
            {
                "id": "base-shell",
                "partId": "base",
                "featureId": "base-shell",
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "roundedBox",
                    "parameters": {"sizeMm": [10, 8, 6], "radiusMm": 1.0},
                },
            },
            {
                "id": "button-stem",
                "partId": "button",
                "featureId": "button-stem",
                "role": "separate",
                "operation": "none",
                "recipe": {
                    "kind": "revolvedProfile",
                    "parameters": {"diameterMm": 3.0},
                },
            },
            {
                "id": "button-guide",
                "partId": "base",
                "featureId": "button-guide",
                "role": "cutter",
                "operation": "subtract",
                "recipe": {
                    "kind": "cylinder",
                    "parameters": {"diameterMm": 3.35, "lengthMm": 8.0},
                },
            },
            {
                "id": "guide-appearance",
                "partId": "base",
                "featureId": "guide-appearance",
                "role": "display-only",
                "operation": "none",
                "physicalFeatureRef": "button-guide",
                "recipe": {
                    "kind": "displayComponent",
                    "parameters": {
                        "sourceMesh": "guide-appearance.ply",
                        "appearance": {
                            "baseColor": "#111417",
                            "metallic": 0.0,
                            "roughness": 0.28,
                        },
                    },
                },
            },
        ],
        "interfaces": [
            {
                "id": "button-fit",
                "kind": "pin-socket",
                "male": {
                    "partId": "button",
                    "featureId": "button-stem",
                    "dimensionsMm": {"diameter": 3.0},
                },
                "female": {
                    "partId": "base",
                    "featureId": "button-guide",
                    "dimensionsMm": {"diameter": 3.35},
                    "derivedDimensionsMm": {
                        "diameter": {
                            "from": "male.diameter",
                            "offsetMm": 0.35,
                        }
                    },
                },
            }
        ],
    }
    manufacturing = {
        "mode": "multipart",
        "parts": [
            {"name": "base", "role": "housing", "acceptance": "base remains physical"},
            {"name": "button", "role": "control", "acceptance": "button remains physical"},
        ],
        "interfaces": [
            {
                "id": "button-fit",
                "between": ["button", "base"],
                "connection": "pin-socket",
                "assembly_axis": "+Z",
                "clearance_mm": 0.35,
                "engagement_mm": 3.0,
                "features": ["button-stem", "button-guide"],
                "acceptance": "button stem enters its guide",
            }
        ],
    }
    return bind_scene_intent(root, scene, manufacturing=manufacturing)


def _self_tapping_scene(root: Path) -> dict:
    nodes = [
        {
            "id": "housing-shell",
            "partId": "housing",
            "featureId": "housing-shell",
            "role": "solid",
            "operation": "union",
            "recipe": {"kind": "sourceMesh", "parameters": {"sourceMesh": "housing.stl"}},
        },
        {
            "id": "base-shell",
            "partId": "base",
            "featureId": "base-shell",
            "role": "solid",
            "operation": "union",
            "recipe": {"kind": "sourceMesh", "parameters": {"sourceMesh": "base.stl"}},
        },
        {
            "id": "base-collar",
            "partId": "base",
            "featureId": "base-collar",
            "role": "solid",
            "operation": "union",
            "recipe": {"kind": "sourceMesh", "parameters": {"sourceMesh": "collar.stl"}},
        },
        {
            "id": "housing-socket",
            "partId": "housing",
            "featureId": "housing-socket",
            "role": "cutter",
            "operation": "subtract",
            "recipe": {"kind": "sourceMesh", "parameters": {"sourceMesh": "socket.stl"}},
        },
    ]
    fasteners = []
    for side, x in (("left", -24.0), ("right", 24.0)):
        axis_id = f"side-{side}"
        clearance_feature = f"base-clearance-{side}"
        pilot_feature = f"housing-pilot-{side}"
        boss_feature = f"housing-boss-{side}"
        nodes.extend([
            {
                "id": clearance_feature,
                "partId": "base",
                "featureId": clearance_feature,
                "role": "cutter",
                "operation": "subtract",
                "recipe": {
                    "kind": "selfTappingScrewPair",
                    "parameters": {
                        "interfaceId": "housing-base-service-joint",
                        "fastenerId": axis_id,
                        "output": "clearance-cutter",
                    },
                },
            },
            {
                "id": pilot_feature,
                "partId": "housing",
                "featureId": pilot_feature,
                "role": "cutter",
                "operation": "subtract",
                "recipe": {
                    "kind": "selfTappingScrewPair",
                    "parameters": {
                        "interfaceId": "housing-base-service-joint",
                        "fastenerId": axis_id,
                        "output": "pilot-cutter",
                    },
                },
            },
            {
                "id": boss_feature,
                "partId": "housing",
                "featureId": boss_feature,
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "selfTappingScrewPair",
                    "parameters": {
                        "interfaceId": "housing-base-service-joint",
                        "fastenerId": axis_id,
                        "output": "receiver-boss",
                    },
                },
            },
        ])
        fasteners.append({
            "id": axis_id,
            "axis": {"originMm": [x, 0.0, 4.0], "direction": [0.0, 1.0, 0.0]},
            "screwFamily": "M3 plastic thread-forming/self-tapping",
            "nominalDiameterMm": 3.0,
            "cutterOvershootMm": 1.0,
            "cover": {
                "partId": "base",
                "featureId": clearance_feature,
                "diameterMm": 3.4,
                "thicknessMm": 2.4,
            },
            "receiver": {
                "partId": "housing",
                "featureId": pilot_feature,
                "bossFeatureId": boss_feature,
                "diameterMm": 2.6,
                "bossOuterDiameterMm": 7.5,
                "engagementMm": 6.0,
                "closedEndMm": 1.2,
                "minimumBossWallMm": 1.8,
                "minimumRootEmbedMm": 0.4,
                "tipClearanceMm": 0.8,
            },
        })
    scene = {
        "schema": "evidence-semantic-scene/v1",
        "revision": "rev-fastener-001",
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "parts": [
            {"id": "housing", "representationMaster": "mesh"},
            {"id": "base", "representationMaster": "brep"},
        ],
        "nodes": nodes,
        "interfaces": [
            {
                "id": "housing-base-service-joint",
                "kind": "self-tapping-screw",
                "locatorInterfaceIds": ["housing-base-locator"],
                "fasteners": fasteners,
            },
            {
                "id": "housing-base-locator",
                "kind": "collar-socket",
                "male": {
                    "partId": "base",
                    "featureId": "base-collar",
                    "dimensionsMm": {"diameter": 70.0},
                },
                "female": {
                    "partId": "housing",
                    "featureId": "housing-socket",
                    "dimensionsMm": {"diameter": 70.8},
                    "derivedDimensionsMm": {
                        "diameter": {"from": "male.diameter", "offsetMm": 0.8}
                    },
                },
            },
        ],
    }
    intent_fasteners = [
        {
            "id": item["id"],
            "clearance_feature": item["cover"]["featureId"],
            "pilot_feature": item["receiver"]["featureId"],
            "boss_feature": item["receiver"]["bossFeatureId"],
        }
        for item in fasteners
    ]
    interface_features = ["base-collar", "housing-socket", "base-shell"] + [
        feature
        for item in intent_fasteners
        for feature in (
            item["clearance_feature"],
            item["pilot_feature"],
            item["boss_feature"],
        )
    ]
    manufacturing = {
        "mode": "multipart",
        "parts": [
            {"name": "housing", "role": "housing", "acceptance": "one housing"},
            {"name": "base", "role": "base", "acceptance": "one service base"},
        ],
        "interfaces": [
            {
                "id": "housing-base-service-joint",
                "between": ["base", "housing"],
                "connection": "self-tapping-screw",
                "assembly_axis": "+Y",
                "clearance_mm": 0.4,
                "engagement_mm": 6.0,
                "features": interface_features,
                "acceptance": "two screw axes and a locator retain the base",
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
                    "fasteners": intent_fasteners,
                },
            }
        ],
    }
    return bind_scene_intent(root, scene, manufacturing=manufacturing)


class SemanticSceneContractTests(unittest.TestCase):
    def test_validates_mutable_graph_separate_from_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            self.assertEqual(scene_contract.validate(data, root), [])

    def test_referenced_intent_must_pass_the_complete_v4_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            intent_path = root / data["intentRef"]["path"]
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            intent["visual"]["required"] = False
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            data["intentRef"]["sha256"] = sha256(intent_path.read_bytes()).hexdigest()
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("intentRef: visual.required must be true" in item for item in errors),
                errors,
            )

    def test_scene_parts_and_physical_features_are_hard_bound_to_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            data["parts"].append({"id": "unrelated", "representationMaster": "mesh"})
            data["nodes"].append({
                "id": "unrelated-detail",
                "partId": "unrelated",
                "featureId": "unrelated-detail",
                "role": "solid",
                "operation": "union",
                "recipe": {"kind": "roundedBox", "parameters": {"sizeMm": [1, 1, 1]}},
            })
            errors = scene_contract.validate(data, root)
            self.assertTrue(any("scene parts must exactly match" in item for item in errors), errors)
            self.assertTrue(any("featureId is not declared" in item for item in errors), errors)

            data = _scene(root)
            data["nodes"][0]["partId"] = "button"
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("must match immutable intent owner 'base'" in item for item in errors),
                errors,
            )

    def test_display_reference_must_share_the_intent_feature_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            data["nodes"][3]["partId"] = "button"
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any(
                    "physicalFeatureRef 'button-guide'" in item
                    and "intent owner 'base'" in item
                    for item in errors
                ),
                errors,
            )

    def test_single_part_intent_maps_to_exactly_its_top_level_part(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene = {
                "schema": "evidence-semantic-scene/v1",
                "revision": "single-part-001",
                "units": "mm",
                "coordinateSystem": {"handedness": "right", "up": "Z"},
                "parts": [{"id": "device", "representationMaster": "mesh"}],
                "nodes": [{
                    "id": "device-shell",
                    "partId": "device",
                    "featureId": "device-shell",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {"kind": "roundedBox", "parameters": {"sizeMm": [1, 1, 1]}},
                }],
                "interfaces": [],
            }
            bind_scene_intent(root, scene, part="device")
            self.assertEqual(scene_contract.validate(scene, root), [])
            scene["parts"][0]["id"] = "unrelated"
            scene["nodes"][0]["partId"] = "unrelated"
            errors = scene_contract.validate(scene, root)
            self.assertTrue(any("expected ['device']" in item for item in errors), errors)

    def test_rejects_role_drift_proxy_drift_and_interface_arithmetic_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            data["task_mode"] = "reference-inspired"
            data["nodes"][2]["operation"] = "union"
            data["nodes"][3]["physicalFeatureRef"] = "missing-feature"
            data["interfaces"][0]["female"]["dimensionsMm"]["diameter"] = 3.2
            errors = scene_contract.validate(data, root)
            text = "\n".join(errors)
            self.assertIn("belongs in the immutable intent contract", text)
            self.assertIn("operation must be subtract for role cutter", text)
            self.assertIn("must reference a non-display feature", text)
            self.assertIn("expects 3.35 mm", text)

    def test_display_component_requires_real_cutter_and_source_mesh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            display = data["nodes"][3]
            display["recipe"] = {
                "kind": "displayComponent",
                "parameters": {},
            }
            display.pop("physicalFeatureRef")
            errors = scene_contract.validate(data, root)
            text = "\n".join(errors)
            self.assertIn("physicalFeatureRef is required", text)
            self.assertIn("sourceMesh is required", text)
            self.assertIn("appearance is required", text)

            display["physicalFeatureRef"] = "base-shell"
            display["recipe"]["parameters"]["sourceMesh"] = "screen-plane.ply"
            display["recipe"]["parameters"]["appearance"] = {
                "baseColor": "#111417"
            }
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("displayComponent must reference a cutter" in item for item in errors),
                errors,
            )

            display["physicalFeatureRef"] = "button-guide"
            self.assertEqual(scene_contract.validate(data, root), [])

    def test_rejects_scaled_canonical_transform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            data["parts"][0]["artifacts"] = {
                "physicalGlb": {
                    "path": "device.glb",
                    "revision": "rev-001",
                    "scale": 1.0,
                    "toCanonicalTransform": [
                        [2, 0, 0, 0],
                        [0, 1, 0, 0],
                        [0, 0, 1, 0],
                        [0, 0, 0, 1],
                    ],
                }
            }
            errors = scene_contract.validate(data, root)
            self.assertTrue(any("axis 0 must have unit scale" in item for item in errors))

    def test_brep_step_is_optional_in_source_scene_but_required_when_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            self.assertEqual(scene_contract.validate(data, root), [])
            data["parts"][0]["artifacts"] = {
                "manufacturingStl": {
                    "path": "base.stl",
                    "revision": "rev-001",
                    "scale": 1.0,
                }
            }
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("masterStep is required once a brep part is bound" in item for item in errors),
                errors,
            )

    def test_self_tapping_scene_uses_one_axis_for_each_hole_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _self_tapping_scene(root)
            self.assertEqual(scene_contract.validate(data, root), [])

    def test_self_tapping_scene_can_reference_multiple_locator_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _self_tapping_scene(root)
            data["nodes"].extend([
                {
                    "id": "base-pin-right",
                    "partId": "base",
                    "featureId": "base-pin-right",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "pin-right.stl"},
                    },
                },
                {
                    "id": "housing-pin-socket-right",
                    "partId": "housing",
                    "featureId": "housing-pin-socket-right",
                    "role": "cutter",
                    "operation": "subtract",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "pin-socket-right.stl"},
                    },
                },
            ])
            data["interfaces"].append({
                "id": "housing-base-locator-right",
                "kind": "pin-socket",
                "male": {
                    "partId": "base",
                    "featureId": "base-pin-right",
                    "dimensionsMm": {"diameter": 3.0},
                },
                "female": {
                    "partId": "housing",
                    "featureId": "housing-pin-socket-right",
                    "dimensionsMm": {"diameter": 3.35},
                    "derivedDimensionsMm": {
                        "diameter": {"from": "male.diameter", "offsetMm": 0.35}
                    },
                },
            })
            data["interfaces"][0]["locatorInterfaceIds"].append(
                "housing-base-locator-right"
            )
            intent_path = root / data["intentRef"]["path"]
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            intent["features"].extend([
                {
                    "id": "base-pin-right",
                    "part": "base",
                    "kind": "detail",
                    "evidence": "fixture adds a second locator pin",
                    "acceptance": "scene binds the pin to the base",
                },
                {
                    "id": "housing-pin-socket-right",
                    "part": "housing",
                    "kind": "detail",
                    "evidence": "fixture adds a second locator socket",
                    "acceptance": "scene binds the socket to the housing",
                },
            ])
            fastening = intent["manufacturing"]["interfaces"][0]["fastening"]
            fastening["locator_pairs"].append({
                "id": "housing-base-locator-right",
                "male_feature": "base-pin-right",
                "female_feature": "housing-pin-socket-right",
            })
            intent["manufacturing"]["interfaces"][0]["features"].extend(
                ["base-pin-right", "housing-pin-socket-right"]
            )
            intent["printability"]["critical_features"].extend(
                ["base-pin-right", "housing-pin-socket-right"]
            )
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            data["intentRef"]["sha256"] = sha256(intent_path.read_bytes()).hexdigest()
            self.assertEqual(scene_contract.validate(data, root), [])

            data["interfaces"][0]["locatorInterfaceIds"].append("missing-locator")
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("references an unknown interface" in item for item in errors),
                errors,
            )

    def test_self_tapping_scene_rejects_axis_and_feature_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _self_tapping_scene(root)
            fastener = data["interfaces"][0]["fasteners"][0]
            fastener["axis"]["direction"] = [0.0, 2.0, 0.0]
            fastener["cover"]["originMm"] = [-23.9, 0.0, 4.0]
            pilot_node = next(
                node
                for node in data["nodes"]
                if node["featureId"] == fastener["receiver"]["featureId"]
            )
            pilot_node["recipe"]["parameters"]["fastenerId"] = "side-right"
            errors = scene_contract.validate(data, root)
            text = "\n".join(errors)
            self.assertIn("must be a unit vector", text)
            self.assertIn("instead of declaring an independent axis", text)
            self.assertIn("must bind the shared recipe output", text)

    def test_self_tapping_locator_cannot_reuse_a_screw_feature(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _self_tapping_scene(root)
            locator = data["interfaces"][1]
            locator["female"]["featureId"] = "housing-pilot-right"
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("must be independent from clearance" in item for item in errors),
                errors,
            )

    def test_self_tapping_scene_rejects_independent_source_meshes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _self_tapping_scene(root)
            clearance = next(
                node
                for node in data["nodes"]
                if node["featureId"] == "base-clearance-left"
            )
            clearance["recipe"] = {
                "kind": "sourceMesh",
                "parameters": {
                    "axisId": "side-left",
                    "sourceMesh": "misaligned-clearance.stl",
                },
            }
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("must use recipe.kind selfTappingScrewPair" in item for item in errors),
                errors,
            )

    def test_self_tapping_scene_rejects_unbound_or_duplicate_recipe_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _self_tapping_scene(root)
            existing = next(
                node
                for node in data["nodes"]
                if node["featureId"] == "housing-boss-left"
            )
            duplicate = json.loads(json.dumps(existing))
            duplicate["id"] = "unbound-extra-boss"
            duplicate["featureId"] = "unbound-extra-boss"
            data["nodes"].append(duplicate)
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("must have exactly one node" in item for item in errors),
                errors,
            )

            duplicate["recipe"]["parameters"]["fastenerId"] = "undeclared-axis"
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("declares an unbound selfTappingScrewPair output" in item for item in errors),
                errors,
            )

    def test_self_tapping_scene_dimensions_and_features_match_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _self_tapping_scene(root)
            intent_path = root / data["intentRef"]["path"]
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            self.assertEqual(scene_contract.validate(data, root), [])

            locator_pairs = intent["manufacturing"]["interfaces"][0]["fastening"][
                "locator_pairs"
            ]
            locator_pairs.append(json.loads(json.dumps(locator_pairs[0])))
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            data["intentRef"]["sha256"] = sha256(intent_path.read_bytes()).hexdigest()
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("locator_pairs ids must be unique" in item for item in errors),
                errors,
            )
            locator_pairs.pop()

            intent["manufacturing"]["interfaces"][0]["fastening"][
                "locator_pairs"
            ][0]["male_feature"] = "base-shell"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            data["intentRef"]["sha256"] = sha256(intent_path.read_bytes()).hexdigest()
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("male_feature must match immutable intent" in item for item in errors),
                errors,
            )

            intent["manufacturing"]["interfaces"][0]["fastening"][
                "locator_pairs"
            ][0]["male_feature"] = "base-collar"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            data["intentRef"]["sha256"] = sha256(intent_path.read_bytes()).hexdigest()

            data["interfaces"][0]["fasteners"][0]["receiver"]["diameterMm"] = 2.7
            errors = scene_contract.validate(data, root)
            self.assertTrue(
                any("pilot_diameter_mm must match immutable intent" in item for item in errors),
                errors,
            )


class ShapeConsistencyTests(unittest.TestCase):
    def test_emit_report_can_persist_the_cli_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "report.json"
            report = {"pass": True, "schema": "evidence-shape-consistency/v1"}
            with redirect_stdout(io.StringIO()):
                shape_consistency._emit_report(report, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)

    def _write_physical_glb(self, path: Path, body: trimesh.Trimesh) -> None:
        scene = trimesh.Scene()
        scene.add_geometry(body.copy(), node_name="base-shell", geom_name="body")
        display = trimesh.creation.box(extents=[2, 2, 2])
        display.apply_translation([30, 0, 0])
        scene.add_geometry(
            display,
            node_name="guide-appearance",
            geom_name="display-decoration",
        )
        payload = scene.export(file_type="glb")
        self.assertIsInstance(payload, bytes)
        path.write_bytes(payload)

    def test_manifest_selects_physical_glb_nodes_and_matches_stl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = trimesh.creation.box(extents=[10, 8, 6])
            glb = root / "device.glb"
            stl = root / "base.stl"
            step = root / "base.step"
            self._write_physical_glb(glb, body)
            body.export(stl)
            step.write_text(
                "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n",
                encoding="ascii",
            )

            data = _scene(root)
            data["parts"][0]["artifacts"] = {
                "physicalGlb": {
                    "path": glb.name,
                    "nodeNames": ["base-shell"],
                    "revision": "rev-001",
                    "scale": 1.0,
                },
                "manufacturingStl": {
                    "path": stl.name,
                    "revision": "rev-001",
                    "scale": 1.0,
                },
                "masterStep": {
                    "path": step.name,
                    "revision": "rev-001",
                    "scale": 1.0,
                    "sha256": sha256(step.read_bytes()).hexdigest(),
                },
            }
            report = shape_consistency.compare_manifest(
                data,
                base_dir=root,
                sample_count=512,
                surface_p99_tolerance_mm=0.001,
                surface_max_tolerance_mm=0.001,
            )
            self.assertTrue(report["pass"], report)
            self.assertEqual(report["skippedParts"], ["button"])
            observed = report["parts"]["base"]
            self.assertLess(observed["surfaceDistance"]["bidirectional"]["maxMm"], 1e-5)
            self.assertEqual(observed["dimensionDeltaMm"], [0.0, 0.0, 0.0])
            self.assertTrue(observed["meshes"]["b"]["watertight"])
            self.assertEqual(observed["meshes"]["b"]["bodyCount"], 1)
            self.assertIsNotNone(observed["meshes"]["b"]["volumeMm3"])

    def test_surface_distance_catches_translation_with_equal_dimensions(self):
        body = trimesh.creation.box(extents=[10, 8, 6])
        shifted = body.copy()
        shifted.apply_translation([0.5, 0, 0])
        report = shape_consistency.compare_meshes(
            body,
            shifted,
            revision_a="rev-001",
            revision_b="rev-001",
            sample_count=512,
            dimension_tolerance_mm=0.001,
            surface_p99_tolerance_mm=0.1,
            surface_max_tolerance_mm=0.1,
        )
        self.assertFalse(report["pass"])
        self.assertEqual(report["dimensionDeltaMm"], [0.0, 0.0, 0.0])
        maximum = next(
            item for item in report["checks"] if item["name"] == "surface_max"
        )
        self.assertFalse(maximum["pass"])

    def test_revision_and_scale_are_hard_checks(self):
        body = trimesh.creation.box(extents=[4, 4, 4])
        report = shape_consistency.compare_meshes(
            body,
            body.copy(),
            revision_a="rev-a",
            revision_b="rev-b",
            scale_a=1.0,
            scale_b=0.5,
            sample_count=128,
        )
        self.assertFalse(report["pass"])
        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(checks["revision_match"]["pass"])
        self.assertFalse(checks["unit_scale"]["pass"])

    def test_y_up_artifact_can_use_rigid_canonical_transform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            y_up = trimesh.creation.box(extents=[10, 6, 8])
            glb = root / "y-up.glb"
            scene = trimesh.Scene()
            scene.add_geometry(y_up, node_name="base-shell", geom_name="body")
            glb.write_bytes(scene.export(file_type="glb"))

            z_up = y_up.copy()
            transform = np.asarray(
                [
                    [1, 0, 0, 0],
                    [0, 0, -1, 0],
                    [0, 1, 0, 0],
                    [0, 0, 0, 1],
                ],
                dtype=float,
            )
            z_up.apply_transform(transform)
            stl = root / "z-up.stl"
            z_up.export(stl)

            mesh_a = shape_consistency.load_artifact(
                {
                    "path": glb.name,
                    "nodeNames": ["base-shell"],
                    "toCanonicalTransform": transform.tolist(),
                },
                root,
            )
            mesh_b = shape_consistency.load_artifact({"path": stl.name}, root)
            report = shape_consistency.compare_meshes(
                mesh_a,
                mesh_b,
                sample_count=256,
                surface_p99_tolerance_mm=0.001,
                surface_max_tolerance_mm=0.001,
            )
            self.assertTrue(report["pass"], report)


if __name__ == "__main__":
    unittest.main()
