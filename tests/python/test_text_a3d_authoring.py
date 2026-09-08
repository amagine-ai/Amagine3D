from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from build123d import Box, Pos


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
PROFILE = SKILL / "examples" / "bambu-a1-mini-0.4-standard.example.json"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import authoring  # noqa: E402
import geometry_binding  # noqa: E402
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
    def test_loose_parts_still_need_a_multipart_interface(self):
        parts = {
            name: {"role": name, "acceptance": "intentionally loose", "installation": "loose",
                   "features": [{"id": name, "evidence": "requested part", "acceptance": "separate solid"}]}
            for name in ("body", "lid")
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "intent.json"
            kwargs = dict(_intent_kwargs(), part="device", manufacturing_mode="multipart",
                          parts=parts, critical_features=["body", "lid"])
            with self.assertRaises(authoring.AuthoringError) as failure:
                authoring.write_intent(path, **kwargs)
            self.assertTrue(any("interfaces" in error for error in failure.exception.errors))
            self.assertFalse(path.exists())

    def test_intent_enum_errors_give_valid_values_and_allow_a_corrected_write(self):
        feature = {
            "id": "usb-port", "kind": "port", "face": "back", "direction": "+Y",
            "edge_crossing": "forbidden", "evidence": "rear connector",
            "acceptance": "opening reaches the internal component space",
        }
        with tempfile.TemporaryDirectory() as directory:
            for field, invalid, allowed in (
                ("kind", "passage", intent_contract.FEATURE_KINDS),
                ("face", "rear", intent_contract.FACES),
                ("direction", "outward", intent_contract.DIRECTIONS),
                ("edge_crossing", "automatic", intent_contract.EDGE_CROSSING),
            ):
                with self.subTest(field=field):
                    path = Path(directory) / f"{field}.json"
                    kwargs = dict(_intent_kwargs(), part="device", manufacturing_mode="single-part",
                                  critical_features=["usb-port"])
                    with self.assertRaises(authoring.AuthoringError) as failure:
                        authoring.write_intent(path, parts={"device": {"features": [{**feature, field: invalid}]}}, **kwargs)
                    error = next(e for e in failure.exception.errors if e.startswith(f"features[0].{field} is invalid"))
                    self.assertIn(str(sorted(allowed)), error)
                    self.assertIn(repr(invalid), error)
                    self.assertFalse(path.exists())
                    document = authoring.write_intent(path, parts={"device": {"features": [feature]}}, **kwargs)
                    self.assertEqual(intent_contract.validate(document, path.parent), [])

    def test_critical_interface_id_reports_feature_choices_without_requiring_feature_kind(self):
        parts = {
            name: {"role": name, "acceptance": "separate physical part", "features": [{
                "id": f"{name}-seat", "evidence": "shared mating surface", "acceptance": "faces meet",
            }]}
            for name in ("body", "lid")
        }
        interface = {
            "id": "lid-fit", "connection": "glue-face", "assembly_axis": "+Z",
            "features": ["body-seat", "lid-seat"], "acceptance": "the two seats contact",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "intent.json"
            kwargs = dict(_intent_kwargs(), part="device", manufacturing_mode="multipart", parts=parts,
                          interfaces=[interface])
            with self.assertRaises(authoring.AuthoringError) as failure:
                authoring.write_intent(path, critical_features=["lid-fit"], **kwargs)
            error = next(e for e in failure.exception.errors if e.startswith("printability.critical_features"))
            self.assertIn("valid feature IDs: ['body-seat', 'lid-seat']", error)
            self.assertFalse(path.exists())
            document = authoring.write_intent(path, critical_features=["body-seat", "lid-seat"], **kwargs)
            self.assertEqual(intent_contract.validate(document, path.parent), [])
            self.assertEqual(document["manufacturing"]["interfaces"][0]["between"], ["body", "lid"])

    def test_paired_interface_derives_independent_named_clearances(self):
        compact = authoring.paired_interface(
            id="pin-fit",
            kind="pin-socket",
            male_feature="pin/stem",
            male_dimensions_mm={"diameter": 4.0, "length": 5.0},
            female_feature="guide/bore",
            clearances_mm={"diameter": 0.4, "length": 0.2},
        )
        geometry_dimensions = authoring.paired_dimensions(compact)
        self.assertEqual(
            geometry_dimensions,
            {
                "male": {"diameter": 4.0, "length": 5.0},
                "female": {"diameter": 4.4, "length": 5.2},
            },
        )

        expanded = authoring._expand_paired_interfaces(
            [compact],
            {"pin/stem": "pin", "guide/bore": "guide"},
        )[0]

        self.assertEqual(
            expanded["female"]["dimensionsMm"],
            {"diameter": 4.4, "length": 5.2},
        )
        self.assertEqual(
            expanded["female"]["derivedDimensionsMm"],
            {
                "diameter": {"from": "male.diameter", "offsetMm": 0.4},
                "length": {"from": "male.length", "offsetMm": 0.2},
            },
        )
        self.assertNotIn("clearancesMm", expanded["female"])

    def test_paired_interface_has_no_duplicate_female_dimension_path(self):
        with self.assertRaisesRegex(authoring.AuthoringError, "female dimensions are derived only"):
            authoring.paired_interface(
                id="pin-fit",
                kind="pin-socket",
                male_feature="pin/stem",
                male_dimensions_mm={"diameter": 4.0},
                female_feature="guide/bore",
                clearances_mm={"diameter": 0.4},
                female_dimensions_mm={"diameter": 4.4},
            )

        raw = authoring.paired_interface(
            id="pin-fit",
            kind="pin-socket",
            male_feature="pin/stem",
            male_dimensions_mm={"diameter": 4.0},
            female_feature="guide/bore",
            clearances_mm={"diameter": 0.4},
        )
        raw["female"]["dimensionsMm"] = {"diameter": 4.4}
        with self.assertRaisesRegex(
            authoring.AuthoringError,
            "female dimensions are derived only",
        ):
            authoring._expand_paired_interfaces(
                [raw],
                {"pin/stem": "pin", "guide/bore": "guide"},
            )

    def test_missing_endpoints_and_kind_are_reported_together_with_exact_fields(self):
        intent = {
            "manufacturing": {"mode": "multipart", "interfaces": [{
                "id": "mount", "connection": "pin-socket",
                "features": ["stem", "bore"], "between": ["cap", "base"],
                "clearances_mm": {"diameter": 0.2},
            }]},
            "features": [{"id": "stem", "part": "cap"}, {"id": "bore", "part": "base"}],
        }
        with self.assertRaises(authoring.AuthoringError) as failure:
            authoring._validate_interface_alignment(intent, [{
                "id": "mount", "kind": "glue-face", "features": ["bore", "stem"],
            }])
        issues = failure.exception.issues
        by_path = {issue["path"]: issue for issue in issues}
        for field in ("male", "female"):
            issue = by_path[f"interfaces[mount].{field}.featureId"]
            self.assertIsNone(issue["actual"])
            self.assertEqual(issue["expected"], ["bore", "stem"])
            self.assertIn("order is irrelevant", issue["message"])
        self.assertIn("interfaces[mount].kind", by_path)
        self.assertGreaterEqual(len(failure.exception.errors), 3)

    def test_dimension_errors_are_batched_and_invalid_kind_lists_allowed_values(self):
        with self.assertRaises(authoring.AuthoringError) as failure:
            authoring.paired_interface(
                id="mount", kind="pin-socket", male_feature="stem", female_feature="bore",
                male_dimensions_mm={"diameter": -1, "length": float("nan")},
                clearances_mm={"diameter": 0.2},
            )
        self.assertTrue(any("male.dimensionsMm.diameter" in i["path"] for i in failure.exception.issues))
        self.assertTrue(any("male.dimensionsMm.length" in i["path"] for i in failure.exception.issues))
        with self.assertRaises(authoring.AuthoringError) as failure:
            authoring.paired_interface(
                id="mount", kind="unknown", male_feature="stem", female_feature="bore",
                male_dimensions_mm={"diameter": 3}, clearances_mm={"diameter": 0.2},
            )
        self.assertIn("glue-face", failure.exception.issues[0]["expected"])

    def test_compile_diagnostics_preserve_all_fields_in_run_bound_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "source-errors.json"
            environment = {
                "AMAGINE3D_SOURCE_PHASE": "compile",
                "AMAGINE3D_SOURCE_DIAGNOSTICS_PATH": str(sidecar),
                "AMAGINE3D_COMPILE_RUN_ID": "test-run",
            }
            with patch.dict("os.environ", environment, clear=False):
                with self.assertRaises(authoring.AuthoringError):
                    authoring.paired_interface(
                        id="mount", kind="pin-socket", male_feature="stem", female_feature="bore",
                        male_dimensions_mm={"diameter": -1, "length": float("nan")},
                        clearances_mm={"diameter": 0.2},
                    )
            diagnostics = json.loads(sidecar.read_text())
            self.assertEqual(diagnostics["runId"], "test-run")
            self.assertFalse(diagnostics["pass"])
            self.assertGreaterEqual(len(diagnostics["issues"]), 2)
            self.assertTrue(all("observed" in issue and "expected" in issue for issue in diagnostics["issues"]))
            self.assertNotIn("NaN", sidecar.read_text())

    def test_surface_contact_writes_a_valid_scene_without_insertion_depth(self):
        import interface_geometry
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parts = {
                name: {"role": name, "acceptance": "one contact member", "features": [{
                    "id": f"{name}-face", "kind": "interface",
                    "evidence": "bonded mating surface", "acceptance": "the members touch",
                }]}
                for name in ("base", "cap")
            }
            path = root / "bond_intent.json"
            intent = authoring.write_intent(
                path, part="bond", manufacturing_mode="multipart", parts=parts,
                critical_features=["base-face", "cap-face"],
                interfaces=[{"id": "bond", "connection": "glue-face", "assembly_axis": "+Z",
                             "features": ["cap-face", "base-face"], "acceptance": "bonded surface contact"}],
                **_intent_kwargs(),
            )
            compact = authoring.paired_interface(
                id="bond", kind="glue-face", male_feature="cap-face",
                male_dimensions_mm={"width": 8, "depth": 6},
                female_feature="base-face", female_dimensions_mm={"width": 10, "depth": 8},
                clearances_mm={},
            )
            dims = authoring.paired_dimensions(compact)
            self.assertEqual(dims["female"], {"width": 10, "depth": 8})
            scene = authoring.write_scene(
                root / "bond_scene.json", intent_path=path,
                parts={name: {"representationMaster": "brep", "nodes": [{
                    "id": f"{name}-node", "featureId": f"{name}-face", "role": "solid",
                    "recipe": {"kind": "roundedBox", "parameters": {"sizeMm": [10, 8, 2] if name == "base" else [8, 6, 2]}},
                }]} for name in parts},
                paired_interfaces=[compact],
            )
            self.assertEqual(scene_contract.validate(scene, root), [])
            self.assertEqual(scene["interfaces"][0]["female"]["derivedDimensionsMm"], {})
            meshes = {
                "base": geometry_binding.shape_to_mesh(Box(10, 8, 2), "base"),
                "cap": geometry_binding.shape_to_mesh(Pos(0, 0, 2) * Box(8, 6, 2), "cap"),
            }
            audit = interface_geometry.audit_interfaces(
                intent=intent, scene=scene, part_meshes=meshes,
                feature_records={f"{name}-face": {"bbox_mm": {
                    "min": mesh.bounds[0].tolist(), "max": mesh.bounds[1].tolist(),
                }} for name, mesh in meshes.items()},
            )
            self.assertTrue(audit["pass"], audit)
            self.assertNotIn("engagement", {item["check"] for item in audit["checks"]})
            with self.assertRaises(authoring.AuthoringError) as failure:
                authoring.write_scene(root / "missing.json", intent_path=path, parts={},
                                      interfaces=[{"id": "bond", "kind": "glue-face"}])
            self.assertEqual(len([i for i in failure.exception.issues if i["code"] == "INTERFACE.ENDPOINT_FEATURE_REQUIRED"]), 2)

    def test_feature_handle_uses_one_identity_for_cut_observation_and_binding(self):
        import cad_helpers
        with tempfile.TemporaryDirectory() as directory, patch.dict(cad_helpers._FEATURES, {}, clear=True), patch.object(cad_helpers, "_EVENTS", []):
            cutter = geometry_binding.BrepFeature("slot", "housing", "cutter", Box(2, 3, 20))
            body = Box(10, 10, 10)
            result = cutter.cut_from(body)
            self.assertAlmostEqual(float(body.volume) - float(result.volume), 60)
            node = cutter.bind(Path(directory) / "slot.stl")
            self.assertEqual((node["featureId"], node["partId"], node["role"]), ("slot", "housing", "cutter"))
            event = [item for item in cad_helpers._EVENTS if item.get("id") == "slot"][-1]
            self.assertEqual(event["part"], "housing")
            self.assertEqual(cad_helpers._FEATURES["slot"]["part"], "housing")
            missed = geometry_binding.BrepFeature("remote-slot", "housing", "cutter", Pos(30, 0, 0) * Box(2, 3, 20))
            with self.assertRaisesRegex(cad_helpers.BuildInvariantError, "likely missed"):
                missed.cut_from(body)
            with self.assertRaisesRegex(geometry_binding.GeometryBindingError, "requires.*cutter"):
                geometry_binding.BrepFeature("body", "housing", "solid", body).cut_from(body)

    def test_compile_phase_forbids_intent_authoring_without_creating_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            intent_path = Path(directory) / "device_intent.json"
            with patch.dict(
                "os.environ", {"AMAGINE3D_SOURCE_PHASE": "compile"}, clear=False
            ):
                with self.assertRaisesRegex(
                    authoring.AuthoringError, "separate contract-only authoring step"
                ):
                    authoring.write_intent(
                        intent_path,
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
                        **_intent_kwargs(),
                    )
            self.assertFalse(intent_path.exists())

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

    def test_intent_writer_preserves_all_self_tapping_control_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature_specs = {
                "base": ("base-collar", "base-clearance"),
                "housing": ("housing-socket", "housing-pilot", "housing-boss"),
            }
            parts = {
                part: {
                    "role": part,
                    "acceptance": f"one {part}",
                    "features": [
                        {
                            "id": feature,
                            "kind": "interface",
                            "evidence": f"{feature} is required",
                            "acceptance": f"{feature} remains bound",
                        }
                        for feature in features
                    ],
                }
                for part, features in feature_specs.items()
            }
            controls = {
                "cutter_overshoot_mm": 1.0,
                "cover_thickness_mm": 2.4,
                "pilot_tip_clearance_mm": 0.8,
                "minimum_boss_wall_mm": 1.8,
                "minimum_root_embed_mm": 0.4,
            }
            interface = {
                "id": "service-joint",
                "connection": "self-tapping-screw",
                "assembly_axis": "+Z",
                "engagement_mm": 6.0,
                "features": [
                    feature
                    for features in feature_specs.values()
                    for feature in features
                ],
                "acceptance": "one located self-tapping service joint",
                "fastening": {
                    "screw_family": "M3 plastic thread-forming/self-tapping",
                    "nominal_diameter_mm": 3.0,
                    "pilot_diameter_mm": 2.6,
                    "clearance_diameter_mm": 3.4,
                    "boss_outer_diameter_mm": 7.5,
                    "closed_end_mm": 1.2,
                    **controls,
                    "locator_pairs": [
                        {
                            "id": "service-locator",
                            "male_feature": "base-collar",
                            "female_feature": "housing-socket",
                        }
                    ],
                    "fasteners": [
                        {
                            "id": "axis-a",
                            "clearance_feature": "base-clearance",
                            "pilot_feature": "housing-pilot",
                            "boss_feature": "housing-boss",
                        }
                    ],
                },
            }

            intent = authoring.write_intent(
                root / "device_intent.json",
                part="device",
                manufacturing_mode="multipart",
                parts=parts,
                interfaces=[interface],
                critical_features=interface["features"],
                **_intent_kwargs(),
            )

            fastening = intent["manufacturing"]["interfaces"][0]["fastening"]
            self.assertEqual(
                {field: fastening[field] for field in controls},
                controls,
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

            self.assertEqual(intent["schema"], "evidence-cad-intent/v5")
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
            body_node = geometry_binding.bind_brep_feature(
                node_id="body-node",
                feature_id="body",
                role="solid",
                shape=Box(4.0, 3.0, 2.0),
                path=root / "body.stl",
            )
            with patch.dict(
                "os.environ", {"AMAGINE3D_SOURCE_PHASE": "compile"}, clear=False
            ):
                scene = authoring.write_scene(
                    scene_path,
                    intent_path=intent_path,
                    parts={
                        "device": {
                            "representationMaster": "brep",
                            "nodes": [body_node],
                        }
                    },
                )
            second_scene = authoring.write_scene(
                root / "device_scene_copy.json",
                intent_path=intent_path,
                parts={
                    "device": {
                        "representationMaster": "brep",
                        "nodes": [body_node],
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
                        "clearances_mm": {"diameter": 0.35},
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
                    },
                    {
                        "name": "button",
                        "part": "button",
                        "hex": "#20242A",
                        "purpose": "whole button material",
                        "boundary": "the complete button physical body",
                        "evidence": "the button color is explicitly chosen",
                        "continuity": "separate-part",
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
                                    "kind": "roundedBox",
                                    "parameters": {"sizeMm": [20, 10, 4]},
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
                        clearances_mm={"diameter": 0.35},
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
            self.assertNotIn("clearancesMm", interface["female"])
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
                    clearances_mm={"diameter": 0.35},
                ),
                authoring.paired_interface(
                    id="button-fit",
                    kind="peg-socket",
                    male_feature="button-stem",
                    male_dimensions_mm={"diameter": 3.0},
                    female_feature="button-guide",
                    clearances_mm={"diameter": 0.35},
                ),
                authoring.paired_interface(
                    id="button-fit",
                    kind="pin-socket",
                    male_feature="button-stem",
                    male_dimensions_mm={"diameter": 3.0},
                    female_feature="base-body",
                    clearances_mm={"diameter": 0.35},
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
