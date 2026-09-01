from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import hybrid_compile  # noqa: E402
import scene_contract  # noqa: E402
import shape_consistency  # noqa: E402


def _write_intent(root: Path) -> dict:
    path = root / "companion_intent.json"
    path.write_text(
        json.dumps({"schema": "evidence-cad-intent/v4", "part": "companion"}),
        encoding="utf-8",
    )
    return {
        "path": path.name,
        "schema": "evidence-cad-intent/v4",
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _fixture(root: Path) -> tuple[dict, float]:
    source = root / "source"
    source.mkdir()
    housing = trimesh.creation.box(extents=[20, 20, 10])
    cutter = trimesh.creation.cylinder(radius=3, height=14, sections=48)
    button = trimesh.creation.cylinder(radius=2, height=3, sections=32)
    button.apply_translation([15, 0, 0])
    # A screen surface is intentionally non-volumetric: it belongs in the
    # visual GLB but must never become a printable solid.
    display_component = trimesh.Trimesh(
        vertices=np.asarray(
            [
                [-4.0, -3.0, 5.05],
                [4.0, -3.0, 5.05],
                [4.0, 3.0, 5.05],
                [-4.0, 3.0, 5.05],
            ]
        ),
        faces=np.asarray([[0, 1, 2], [0, 2, 3]]),
        process=False,
    )
    housing.export(source / "housing.stl")
    cutter.export(source / "cutter.stl")
    button.export(source / "button.stl")
    display_component.export(source / "display-component.ply")

    scene = {
        "schema": "evidence-semantic-scene/v1",
        "revision": "hybrid-rev-001",
        "intentRef": _write_intent(root),
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "parts": [
            {
                "id": "housing",
                "representationMaster": "mesh",
                "color": "#EEE8DC",
            },
            {
                "id": "button",
                "representationMaster": "brep",
                "appearance": {
                    "baseColor": "#F06450",
                    "metallic": 0.0,
                    "roughness": 0.42,
                },
            },
        ],
        "nodes": [
            {
                "id": "housing-outer",
                "partId": "housing",
                "featureId": "housing/outer",
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {"sourceMesh": "source/housing.stl"},
                },
            },
            {
                "id": "housing-hole",
                "partId": "housing",
                "featureId": "housing/through-hole",
                "role": "cutter",
                "operation": "subtract",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {
                        "sourceMesh": {
                            "path": "source/cutter.stl",
                            "scale": 1.0,
                        }
                    },
                },
            },
            {
                "id": "button-body",
                "partId": "button",
                "featureId": "controls/button",
                "role": "separate",
                "operation": "none",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {"sourceMesh": "source/button.stl"},
                },
            },
            {
                "id": "hole-appearance",
                "partId": "housing",
                "featureId": "display/hole-appearance",
                "role": "display-only",
                "operation": "none",
                "physicalFeatureRef": "housing/through-hole",
                "recipe": {
                    "kind": "displayComponent",
                    "parameters": {
                        "sourceMesh": "source/display-component.ply",
                        "appearance": {
                            "baseColor": "#101418",
                            "metallic": 0.0,
                            "roughness": 0.2,
                        },
                    },
                },
            },
        ],
        "interfaces": [],
    }
    return scene, float(housing.volume)


class HybridCompileTests(unittest.TestCase):
    def test_clearance_volume_probe_rejects_a_skin_with_only_a_center_pinhole(self):
        cover = trimesh.creation.box(extents=[10.0, 10.0, 4.0])
        cover.apply_translation([0.0, 0.0, -2.0])
        short_cutter = trimesh.creation.cylinder(
            radius=1.7,
            height=3.4,
            sections=64,
        )
        short_cutter.apply_translation([0.0, 0.0, -1.7])
        pinhole = trimesh.creation.cylinder(
            radius=0.25,
            height=6.0,
            sections=64,
        )
        pinhole.apply_translation([0.0, 0.0, -2.0])
        pinholed_skin, _ = hybrid_compile._difference(
            cover,
            [short_cutter, pinhole],
            "pinholed-skin-cover",
        )
        self.assertEqual(
            hybrid_compile._axis_triangle_intersections(
                pinholed_skin,
                np.zeros(3),
                np.asarray([0.0, 0.0, 1.0]),
            ),
            [],
        )
        passage_probe = hybrid_compile._segment_cylinder(
            1.68,
            np.asarray([0.0, 0.0, -4.05]),
            np.asarray([0.0, 0.0, 0.05]),
        )
        self.assertGreater(
            hybrid_compile._intersection_volume(
                pinholed_skin,
                passage_probe,
                "pinholed clearance",
            ),
            0.1,
        )

        through_cutter = trimesh.creation.cylinder(
            radius=1.7,
            height=6.0,
            sections=64,
        )
        through_cutter.apply_translation([0.0, 0.0, -2.0])
        open_cover, _ = hybrid_compile._difference(
            cover,
            [through_cutter],
            "open-cover",
        )
        self.assertLessEqual(
            hybrid_compile._intersection_volume(
                open_cover,
                passage_probe,
                "open clearance",
            ),
            hybrid_compile._probe_tolerance(passage_probe),
        )

    def test_head_recess_witness_requires_the_full_minimum_floor(self):
        cover = trimesh.creation.box(extents=[10.0, 10.0, 2.4])
        cover.apply_translation([0.0, 0.0, -1.2])
        through = hybrid_compile._segment_cylinder(
            1.7,
            np.asarray([0.0, 0.0, -3.0]),
            np.asarray([0.0, 0.0, 0.5]),
        )
        shallow_floor_recess = hybrid_compile._segment_cylinder(
            3.0,
            np.asarray([0.0, 0.0, -3.0]),
            np.asarray([0.0, 0.0, -0.6]),
        )
        insufficient_floor, _ = hybrid_compile._difference(
            cover,
            [through, shallow_floor_recess],
            "insufficient-floor-cover",
        )
        floor_witness = hybrid_compile._segment_annulus(
            1.72,
            2.98,
            np.asarray([0.0, 0.0, -0.78]),
            np.asarray([0.0, 0.0, -0.02]),
        )
        self.assertGreater(
            hybrid_compile._missing_volume(
                floor_witness,
                insufficient_floor,
                "insufficient head recess floor",
            ),
            0.1,
        )

        exact_floor_recess = hybrid_compile._segment_cylinder(
            3.0,
            np.asarray([0.0, 0.0, -3.0]),
            np.asarray([0.0, 0.0, -0.8]),
        )
        exact_floor, _ = hybrid_compile._difference(
            cover,
            [through, exact_floor_recess],
            "exact-floor-cover",
        )
        self.assertLessEqual(
            hybrid_compile._missing_volume(
                floor_witness,
                exact_floor,
                "exact head recess floor",
            ),
            hybrid_compile._probe_tolerance(floor_witness),
        )

    def test_shared_self_tapping_recipe_places_actual_geometry_on_one_axis(self):
        scene = {
            "interfaces": [
                {
                    "id": "housing-base-service-joint",
                    "kind": "self-tapping-screw",
                    "fasteners": [
                        {
                            "id": "side-left",
                            "axis": {
                                "originMm": [5.0, 6.0, 7.0],
                                "direction": [0.0, 1.0, 0.0],
                            },
                            "screwFamily": "M3 plastic thread-forming/self-tapping",
                            "nominalDiameterMm": 3.0,
                            "cutterOvershootMm": 1.0,
                            "cover": {
                                "diameterMm": 3.4,
                                "thicknessMm": 2.4,
                            },
                            "receiver": {
                                "diameterMm": 2.6,
                                "bossOuterDiameterMm": 7.5,
                                "engagementMm": 6.0,
                                "tipClearanceMm": 0.8,
                                "closedEndMm": 1.2,
                                "minimumBossWallMm": 1.8,
                                "rootOverlapMm": 0.4,
                            },
                        }
                    ],
                }
            ]
        }
        outputs, evidence = hybrid_compile._self_tapping_group(
            scene,
            "housing-base-service-joint",
            "side-left",
        )
        origin = np.asarray([5.0, 6.0, 7.0])
        expected_diameters = {
            "clearance-cutter": 3.4,
            "pilot-cutter": 2.6,
            "receiver-boss": 7.5,
        }
        for name, mesh in outputs.items():
            relative = np.asarray(mesh.vertices) - origin
            # The declared +Y axis leaves X/Z as its radial plane.
            radial = relative[:, [0, 2]]
            radial_min = radial.min(axis=0)
            radial_max = radial.max(axis=0)
            np.testing.assert_allclose(
                (radial_min + radial_max) / 2,
                [0.0, 0.0],
                atol=1e-6,
            )
            np.testing.assert_allclose(
                radial_max - radial_min,
                [expected_diameters[name], expected_diameters[name]],
                atol=0.03,
            )
        self.assertEqual(evidence["axis"]["origin_mm"], [5.0, 6.0, 7.0])
        self.assertEqual(evidence["axis"]["direction"], [0.0, 1.0, 0.0])
        self.assertEqual(
            set(evidence["outputs"]),
            {"clearance-cutter", "pilot-cutter", "receiver-boss"},
        )

    def test_compiler_builds_fused_blind_aligned_two_screw_joint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            base = trimesh.creation.box(extents=[20.0, 20.0, 2.4])
            base.apply_translation([0.0, 0.0, -1.2])
            housing = trimesh.creation.box(extents=[20.0, 20.0, 1.0])
            housing.apply_translation([0.0, 0.0, 0.5])
            locator = trimesh.creation.cylinder(radius=2.0, height=1.0, sections=48)
            locator.apply_translation([0.0, 0.0, -0.5])
            socket = trimesh.creation.cylinder(radius=2.2, height=3.0, sections=48)
            socket.apply_translation([0.0, 0.0, 0.5])
            for name, mesh in (
                ("base", base),
                ("housing", housing),
                ("locator", locator),
                ("socket", socket),
            ):
                mesh.export(source / f"{name}.stl")

            nodes = [
                {
                    "id": "base-shell",
                    "partId": "base",
                    "featureId": "base-shell",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/base.stl"},
                    },
                },
                {
                    "id": "housing-shell",
                    "partId": "housing",
                    "featureId": "housing-shell",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/housing.stl"},
                    },
                },
                {
                    "id": "base-locator",
                    "partId": "base",
                    "featureId": "base-locator",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/locator.stl"},
                    },
                },
                {
                    "id": "housing-socket",
                    "partId": "housing",
                    "featureId": "housing-socket",
                    "role": "cutter",
                    "operation": "subtract",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/socket.stl"},
                    },
                },
            ]
            fasteners = []
            for side, x in (("left", -6.0), ("right", 6.0)):
                fastener_id = f"side-{side}"
                outputs = {
                    "clearance-cutter": ("base", "cutter"),
                    "pilot-cutter": ("housing", "cutter"),
                    "receiver-boss": ("housing", "solid"),
                }
                feature_ids = {}
                for output_name, (part_id, role) in outputs.items():
                    feature_id = f"{fastener_id}-{output_name}"
                    feature_ids[output_name] = feature_id
                    nodes.append({
                        "id": feature_id,
                        "partId": part_id,
                        "featureId": feature_id,
                        "role": role,
                        "operation": "subtract" if role == "cutter" else "union",
                        "recipe": {
                            "kind": "selfTappingScrewPair",
                            "parameters": {
                                "interfaceId": "housing-base-service-joint",
                                "fastenerId": fastener_id,
                                "output": output_name,
                            },
                        },
                    })
                fasteners.append({
                    "id": fastener_id,
                    "axis": {"originMm": [x, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]},
                    "screwFamily": "M3 plastic thread-forming/self-tapping",
                    "nominalDiameterMm": 3.0,
                    "cutterOvershootMm": 1.0,
                    "cover": {
                        "partId": "base",
                        "featureId": feature_ids["clearance-cutter"],
                        "diameterMm": 3.4,
                        "thicknessMm": 2.4,
                    },
                    "receiver": {
                        "partId": "housing",
                        "featureId": feature_ids["pilot-cutter"],
                        "bossFeatureId": feature_ids["receiver-boss"],
                        "diameterMm": 2.6,
                        "bossOuterDiameterMm": 7.5,
                        "engagementMm": 6.0,
                        "tipClearanceMm": 0.8,
                        "closedEndMm": 1.2,
                        "minimumBossWallMm": 1.8,
                        "rootOverlapMm": 0.4,
                    },
                })
            scene = {
                "schema": "evidence-semantic-scene/v1",
                "revision": "fastener-compile-001",
                "intentRef": _write_intent(root),
                "units": "mm",
                "coordinateSystem": {"handedness": "right", "up": "Z"},
                "parts": [
                    {"id": "housing", "representationMaster": "mesh"},
                    {"id": "base", "representationMaster": "mesh"},
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
                            "featureId": "base-locator",
                            "dimensionsMm": {"diameter": 4.0},
                        },
                        "female": {
                            "partId": "housing",
                            "featureId": "housing-socket",
                            "dimensionsMm": {"diameter": 4.4},
                            "derivedDimensionsMm": {
                                "diameter": {"from": "male.diameter", "offsetMm": 0.4}
                            },
                        },
                    },
                ],
            }
            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=root / "artifacts",
                consistency_samples=64,
            )
            self.assertTrue(report["pass"], report)
            self.assertEqual(
                set(report["fastenerGroups"]),
                {
                    "housing-base-service-joint/side-left",
                    "housing-base-service-joint/side-right",
                },
            )
            self.assertTrue(
                all(
                    item["pass"]
                    for item in report["fastenerGeometryChecks"].values()
                )
            )
            output = root / "artifacts"
            compiled_base = trimesh.load(output / "base.stl", force="mesh")
            compiled_housing = trimesh.load(output / "housing.stl", force="mesh")
            self.assertEqual(len(compiled_base.split()), 1)
            self.assertEqual(len(compiled_housing.split()), 1)
            for x in (-6.0, 6.0):
                self.assertFalse(bool(compiled_base.contains([[x, 0.0, -1.2]])[0]))
                self.assertFalse(bool(compiled_housing.contains([[x, 0.0, 3.0]])[0]))
                self.assertTrue(bool(compiled_housing.contains([[x, 0.0, 7.4]])[0]))
                self.assertTrue(bool(compiled_housing.contains([[x + 2.0, 0.0, 3.0]])[0]))

    def test_compiles_true_hole_excludes_display_and_binds_consistent_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, housing_volume = _fixture(root)
            scene_path = root / "scene.json"
            scene_path.write_text(json.dumps(scene), encoding="utf-8")
            output = root / "artifacts"

            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=output,
                source_scene=scene_path,
                consistency_samples=256,
            )
            self.assertTrue(report["pass"], report)
            self.assertEqual(report["scale"], 1.0)
            self.assertFalse(report["autoScale"])
            self.assertEqual(report["part"], "companion")
            self.assertEqual(
                set(report["artifacts"]),
                {"stl", "3mf", "glb:display", "shapeConsistency"},
            )
            for key in ("stl", "3mf", "glb:display"):
                artifact = report["artifacts"][key]
                self.assertTrue(Path(artifact["path"]).is_file())
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(report["source"]["path"], str(scene_path.resolve()))
            self.assertEqual(
                report["source"]["sha256"],
                sha256(scene_path.read_bytes()).hexdigest(),
            )
            self.assertTrue((output / "companion_report.json").is_file())
            self.assertEqual(
                report["excludedDisplayNodes"],
                [
                    {
                        "nodeId": "hole-appearance",
                        "physicalFeatureRef": "housing/through-hole",
                        "reason": "display-only nodes never alter physical contours",
                    }
                ],
            )
            self.assertEqual(
                report["includedDisplayNodes"],
                [
                    {
                        "nodeId": "hole-appearance",
                        "physicalFeatureRef": "housing/through-hole",
                        "includedInDisplay": True,
                        "excludedFromManufacturing": True,
                        "reason": "display-only nodes never alter physical contours",
                    }
                ],
            )
            self.assertEqual(
                report["excludedFromManufacturingNodes"],
                report["includedDisplayNodes"],
            )

            housing = trimesh.load(output / "housing.stl", force="mesh")
            self.assertTrue(housing.is_watertight)
            self.assertTrue(housing.is_volume)
            self.assertLess(float(housing.volume), housing_volume)
            self.assertAlmostEqual(
                housing_volume - float(housing.volume),
                np.pi * 3 * 3 * 10,
                delta=1.0,
            )
            self.assertFalse(bool(housing.contains([[0, 0, 0]])[0]))
            self.assertTrue(bool(housing.contains([[6, 0, 0]])[0]))
            locations, _, _ = housing.ray.intersects_location(
                [[0, 0, -20]], [[0, 0, 1]], multiple_hits=True
            )
            self.assertEqual(len(locations), 0, "centerline must pass through the hole")
            self.assertTrue(np.allclose(housing.extents, [20, 20, 10], atol=1e-5))

            button = trimesh.load(output / "button.stl", force="mesh")
            self.assertTrue(button.is_volume)
            combined = trimesh.load(output / "companion.stl", force="mesh")
            self.assertEqual(len(combined.split()), 2)
            self.assertTrue((output / "companion-display.glb").is_file())
            display_scene = trimesh.load(
                output / "companion-display.glb", force="scene", process=False
            )
            self.assertIsInstance(display_scene, trimesh.Scene)
            self.assertEqual(
                sorted(display_scene.graph.nodes_geometry),
                ["button", "hole-appearance", "housing"],
            )
            self.assertEqual(
                report["artifacts"]["glb:display"]["physicalNodeNames"],
                ["button", "housing"],
            )
            self.assertEqual(
                report["artifacts"]["glb:display"]["displayOnlyNodeNames"],
                ["hole-appearance"],
            )
            self.assertFalse(any(output.glob("*.step")))

            three_mf = report["artifacts"]["3mf"]
            self.assertTrue(three_mf["verified"])
            self.assertEqual(three_mf["colorScope"], "part-level")
            self.assertEqual(three_mf["regionColoring"], "not-supported-in-v1")
            self.assertEqual(three_mf["objectCount"], 2)
            self.assertEqual(
                [(item["name"], item["color"]) for item in three_mf["objects"]],
                [("housing", "#EEE8DC"), ("button", "#F06450")],
            )
            with ZipFile(output / "companion.3mf") as archive:
                self.assertIn("3D/3dmodel.model", archive.namelist())

            bound = json.loads(
                (output / "companion_scene_artifacts.json").read_text()
            )
            self.assertEqual(scene_contract.validate(bound, output), [])
            self.assertEqual(bound["revision"], "hybrid-rev-001")
            for part in bound["parts"]:
                artifacts = part["artifacts"]
                self.assertEqual(artifacts["physicalGlb"]["scale"], 1.0)
                self.assertEqual(artifacts["manufacturingStl"]["scale"], 1.0)
                self.assertEqual(
                    artifacts["physicalGlb"]["revision"], "hybrid-rev-001"
                )
            consistency = shape_consistency.compare_manifest(
                bound,
                base_dir=output,
                sample_count=256,
                surface_p99_tolerance_mm=0.001,
                surface_max_tolerance_mm=0.001,
            )
            self.assertTrue(consistency["pass"], consistency)
            persisted = json.loads(
                (output / "companion_shape-consistency.json").read_text()
            )
            self.assertTrue(persisted["pass"])
            self.assertTrue(
                any("STEP remains owned by build123d" in item for item in report["warnings"])
            )
            self.assertFalse(report["step"]["generated"])

    def test_legacy_display_proxy_stays_excluded_from_final_visuals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            proxy = trimesh.creation.box(extents=[8, 6, 0.2])
            proxy.export(root / "source" / "display-proxy.stl")
            display = scene["nodes"][3]
            display["recipe"] = {
                "kind": "displayProxy",
                "parameters": {
                    "sourceMesh": "source/display-proxy.stl",
                    "material": "dark-aperture",
                },
            }
            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=root / "artifacts",
                consistency_samples=64,
            )
            self.assertTrue(report["pass"], report)
            self.assertEqual(
                report["artifacts"]["3mf"]["objectCount"], 2
            )
            self.assertEqual(report["includedDisplayNodes"], [])
            self.assertEqual(
                report["artifacts"]["glb:display"]["displayOnlyNodeNames"], []
            )

    def test_legacy_display_proxy_without_a_mesh_remains_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            scene["nodes"][3]["recipe"] = {
                "kind": "displayProxy",
                "parameters": {"material": "dark-aperture"},
            }
            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=root / "artifacts",
                consistency_samples=64,
            )
            self.assertTrue(report["pass"], report)
            self.assertEqual(report["includedDisplayNodes"], [])
            self.assertEqual(
                report["artifacts"]["glb:display"]["displayOnlyNodeNames"], []
            )

    def test_rejects_source_mesh_scale_instead_of_fitting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            scene["nodes"][0]["recipe"]["parameters"]["sourceMesh"] = {
                "path": "source/housing.stl",
                "scale": 0.5,
            }
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "sourceMesh.scale must be 1",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    consistency_samples=64,
                )


if __name__ == "__main__":
    unittest.main()
