from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
PROFILE = SKILL / "examples" / "bambu-a1-mini-0.4-standard.example.json"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import authoring  # noqa: E402
import intent_contract  # noqa: E402
import scene_contract  # noqa: E402


def _dimensions() -> dict:
    return {
        axis: {"value": value, "source": "user", "confidence": "high"}
        for axis, value in zip("xyz", (40.0, 30.0, 20.0), strict=True)
    }


def _intent_kwargs() -> dict:
    return {
        "profile_path": PROFILE,
        "task_mode": "specification",
        "representation": "full-3d",
        "dimensions_mm": _dimensions(),
        "support_policy": "support-free",
        "minimum_wall_target_mm": 0.9,
        "reference_view": "isometric",
        "landmarks": ["the declared form remains identifiable"],
        "assumptions": [],
    }


class AuthoringTests(unittest.TestCase):
    def test_intent_writer_is_idempotent_but_never_retargets_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            intent_path = Path(directory) / "device_intent.json"
            parts = {
                "device": {
                    "features": [
                        {
                            "id": "body",
                            "kind": "envelope",
                            "evidence": "one physical body",
                            "acceptance": "one physical body",
                        }
                    ]
                }
            }
            authoring.write_intent(
                intent_path,
                part="device",
                manufacturing_mode="single-part",
                parts=parts,
                critical_features=["body"],
                **_intent_kwargs(),
            )
            original = intent_path.read_bytes()

            authoring.write_intent(
                intent_path,
                part="device",
                manufacturing_mode="single-part",
                parts=parts,
                critical_features=["body"],
                **_intent_kwargs(),
            )
            self.assertEqual(intent_path.read_bytes(), original)

            changed = _intent_kwargs()
            changed["assumptions"] = ["a changed target"]
            with self.assertRaisesRegex(authoring.AuthoringError, "already exists"):
                authoring.write_intent(
                    intent_path,
                    part="device",
                    manufacturing_mode="single-part",
                    parts=parts,
                    critical_features=["body"],
                    **changed,
                )
            self.assertEqual(intent_path.read_bytes(), original)

    def test_uses_the_profile_wall_target_when_no_higher_target_is_declared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kwargs = _intent_kwargs()
            kwargs.pop("minimum_wall_target_mm")
            intent = authoring.write_intent(
                root / "device_intent.json",
                part="device",
                manufacturing_mode="single-part",
                parts={
                    "device": {
                        "features": [
                            {
                                "id": "body",
                                "kind": "envelope",
                                "evidence": "one physical body",
                                "acceptance": "one physical body",
                            }
                        ]
                    }
                },
                critical_features=["body"],
                **kwargs,
            )

            profile = json.loads(PROFILE.read_text(encoding="utf-8"))
            self.assertEqual(
                intent["printability"]["minimum_wall_target_mm"],
                profile["derived"]["process_wall_target_mm"],
            )

    def test_writes_canonical_single_part_intent_and_scene(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "device_intent.json"
            intent = authoring.write_intent(
                intent_path,
                part="device",
                manufacturing_mode="single-part",
                parts={
                    "device": {
                        "features": [
                            {
                                "id": "body",
                                "kind": "envelope",
                                "evidence": "the user requires one physical body",
                                "acceptance": "one body occupies the contracted envelope",
                            }
                        ]
                    }
                },
                critical_features=["body"],
                **_intent_kwargs(),
            )

            self.assertEqual(intent["schema"], "evidence-cad-intent/v4")
            self.assertEqual(intent["coordinate_system"], intent_contract.COORDINATE_SYSTEM)
            self.assertEqual(intent["features"][0]["part"], "device")
            self.assertEqual(intent["printability"]["build_axis"], "+Z")
            self.assertEqual(intent["printability"]["bed_contact"], "z-min")
            self.assertEqual(
                intent["printability"]["profile"]["sha256"],
                sha256(PROFILE.read_bytes()).hexdigest(),
            )
            self.assertEqual(intent_contract.validate(intent, root), [])

            scene_path = root / "device_scene.json"
            scene = authoring.write_scene(
                scene_path,
                intent_path=intent_path,
                parts={
                    "device": {
                        "representationMaster": "mesh",
                        "nodes": [
                            {
                                "id": "body-node",
                                "featureId": "body",
                                "role": "solid",
                                "recipe": {
                                    "kind": "sourceMesh",
                                    "parameters": {"sourceMesh": "body.stl"},
                                },
                            }
                        ],
                    }
                },
            )
            second_scene = authoring.write_scene(
                root / "device_scene_copy.json",
                intent_path=intent_path,
                parts={
                    "device": {
                        "representationMaster": "mesh",
                        "nodes": [
                            {
                                "id": "body-node",
                                "featureId": "body",
                                "role": "solid",
                                "recipe": {
                                    "kind": "sourceMesh",
                                    "parameters": {"sourceMesh": "body.stl"},
                                },
                            }
                        ],
                    }
                },
            )

            self.assertEqual(scene["schema"], "evidence-semantic-scene/v1")
            self.assertEqual(scene["units"], "mm")
            self.assertEqual(scene["coordinateSystem"], {"handedness": "right", "up": "Z"})
            self.assertEqual(scene["nodes"][0]["partId"], "device")
            self.assertEqual(scene["nodes"][0]["operation"], "union")
            self.assertEqual(scene["revision"], second_scene["revision"])
            self.assertEqual(
                scene["intentRef"]["sha256"], sha256(intent_path.read_bytes()).hexdigest()
            )
            self.assertEqual(scene_contract.validate(scene, root), [])

    def test_derives_multipart_ownership_interface_dimensions_and_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "control_intent.json"
            intent = authoring.write_intent(
                intent_path,
                part="control",
                manufacturing_mode="multipart",
                parts={
                    "base": {
                        "role": "housing",
                        "acceptance": "base remains a separate housing",
                        "features": [
                            {
                                "id": "base-body",
                                "kind": "envelope",
                                "evidence": "the base is a physical housing",
                                "acceptance": "the base remains one physical body",
                            },
                            {
                                "id": "button-guide",
                                "kind": "interface",
                                "evidence": "the base guides the button",
                                "acceptance": "the guide receives the button stem",
                            }
                        ],
                    },
                    "button": {
                        "role": "control",
                        "acceptance": "button remains a separate control",
                        "features": [
                            {
                                "id": "button-stem",
                                "kind": "interface",
                                "evidence": "the button has a guided stem",
                                "acceptance": "the stem enters the base guide",
                            }
                        ],
                    },
                },
                interfaces=[
                    {
                        "id": "button-fit",
                        "connection": "pin-socket",
                        "assembly_axis": "+Z",
                        "clearance_mm": 0.35,
                        "engagement_mm": 3.0,
                        "features": ["button-stem", "button-guide"],
                        "acceptance": "the stem enters the guide with 0.35 mm clearance",
                    }
                ],
                color_regions=[
                    {
                        "name": "base",
                        "part": "base",
                        "hex": "#D8D2C8",
                        "purpose": "whole base material",
                        "boundary": "the complete base physical body",
                        "evidence": "the base color is explicitly chosen",
                        "continuity": "separate-part",
                        "material": {"transmission": "opaque"},
                    },
                    {
                        "name": "button",
                        "part": "button",
                        "hex": "#20242A",
                        "purpose": "whole button material",
                        "boundary": "the complete button physical body",
                        "evidence": "the button color is explicitly chosen",
                        "continuity": "separate-part",
                        "material": {"transmission": "opaque"},
                    },
                ],
                palette_reduction={
                    "applied": False,
                    "reason": "both explicitly chosen whole-part colors are retained",
                },
                critical_features=["button-guide", "button-stem"],
                **_intent_kwargs(),
            )
            self.assertEqual(
                intent["manufacturing"]["interfaces"][0]["between"],
                ["button", "base"],
            )
            self.assertNotIn("features", intent["manufacturing"]["parts"][0])
            self.assertEqual(
                intent["printability"]["print_package_mode"], "separate_parts"
            )

            scene_parts = {
                    "base": {
                        "representationMaster": "brep",
                        "nodes": [
                            {
                                "id": "button-guide-node",
                                "featureId": "button-guide",
                                "role": "cutter",
                                "recipe": {
                                    "kind": "cylinder",
                                    "parameters": {"diameterMm": 3.35, "lengthMm": 8.0},
                                },
                            },
                            {
                                "id": "base-body-node",
                                "featureId": "base-body",
                                "role": "solid",
                                "recipe": {
                                    "kind": "sourceMesh",
                                    "parameters": {"sourceMesh": "base.step"},
                                },
                            },
                        ],
                    },
                    "button": {
                        "representationMaster": "brep",
                        "nodes": [
                            {
                                "id": "button-stem-node",
                                "featureId": "button-stem",
                                "role": "solid",
                                "recipe": {
                                    "kind": "cylinder",
                                    "parameters": {"diameterMm": 3.0, "lengthMm": 6.0},
                                },
                            }
                        ],
                    },
                }
            scene = authoring.write_scene(
                root / "control_scene.json",
                intent_path=intent_path,
                parts=scene_parts,
                paired_interfaces=[
                    authoring.paired_interface(
                        id="button-fit",
                        kind="pin-socket",
                        male_feature="button-stem",
                        male_dimensions_mm={"diameter": 3.0},
                        female_feature="button-guide",
                        female_offsets_mm={"diameter": 0.35},
                    )
                ],
            )

            self.assertEqual({item["id"] for item in scene["materials"]}, {"base", "button"})
            self.assertEqual(
                {item["id"]: item["materialId"] for item in scene["parts"]},
                {"base": "base", "button": "button"},
            )
            interface = scene["interfaces"][0]
            self.assertNotIn("nodes", scene["parts"][0])
            self.assertNotIn("offsetsMm", interface["female"])
            self.assertEqual(interface["male"]["partId"], "button")
            self.assertEqual(interface["female"]["partId"], "base")
            self.assertEqual(interface["female"]["dimensionsMm"]["diameter"], 3.35)
            self.assertEqual(
                interface["female"]["derivedDimensionsMm"]["diameter"],
                {"from": "male.diameter", "offsetMm": 0.35},
            )
            self.assertEqual(scene_contract.validate(scene, root), [])

            invalid_interfaces = [
                authoring.paired_interface(
                    id="renamed-fit",
                    kind="pin-socket",
                    male_feature="button-stem",
                    male_dimensions_mm={"diameter": 3.0},
                    female_feature="button-guide",
                    female_offsets_mm={"diameter": 0.35},
                ),
                authoring.paired_interface(
                    id="button-fit",
                    kind="peg-socket",
                    male_feature="button-stem",
                    male_dimensions_mm={"diameter": 3.0},
                    female_feature="button-guide",
                    female_offsets_mm={"diameter": 0.35},
                ),
                authoring.paired_interface(
                    id="button-fit",
                    kind="pin-socket",
                    male_feature="button-stem",
                    male_dimensions_mm={"diameter": 3.0},
                    female_feature="base-body",
                    female_offsets_mm={"diameter": 0.35},
                ),
            ]
            for index, invalid in enumerate(invalid_interfaces):
                with self.subTest(invalid_interface=index):
                    with self.assertRaises(authoring.AuthoringError):
                        authoring.write_scene(
                            root / f"invalid_control_scene_{index}.json",
                            intent_path=intent_path,
                            parts=scene_parts,
                            paired_interfaces=[invalid],
                        )

    def test_fails_closed_instead_of_guessing_semantic_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "invalid_intent.json"
            with self.assertRaisesRegex(authoring.AuthoringError, "declares owner"):
                authoring.write_intent(
                    intent_path,
                    part="device",
                    manufacturing_mode="single-part",
                    parts={
                        "device": {
                            "features": [
                                {
                                    "id": "body",
                                    "part": "guessed-other-part",
                                    "kind": "envelope",
                                    "evidence": "one body",
                                    "acceptance": "one body",
                                }
                            ]
                        }
                    },
                    critical_features=["body"],
                    **_intent_kwargs(),
                )
            self.assertFalse(intent_path.exists())

            valid_intent = authoring.write_intent(
                root / "device_intent.json",
                part="device",
                manufacturing_mode="single-part",
                parts={
                    "device": {
                        "features": [
                            {
                                "id": "body",
                                "kind": "envelope",
                                "evidence": "one body",
                                "acceptance": "one body",
                            }
                        ]
                    }
                },
                critical_features=["body"],
                **_intent_kwargs(),
            )
            self.assertEqual(valid_intent["manufacturing"]["mode"], "single-part")

            scene_path = root / "invalid_scene.json"
            with self.assertRaisesRegex(authoring.AuthoringError, "representationMaster"):
                authoring.write_scene(
                    scene_path,
                    intent_path=root / "device_intent.json",
                    parts={
                        "device": {
                            "nodes": [
                                {
                                    "id": "body-node",
                                    "featureId": "body",
                                    "role": "solid",
                                    "recipe": {
                                        "kind": "sourceMesh",
                                        "parameters": {"sourceMesh": "body.stl"},
                                    },
                                }
                            ]
                        }
                    },
                )
            self.assertFalse(scene_path.exists())


if __name__ == "__main__":
    unittest.main()
