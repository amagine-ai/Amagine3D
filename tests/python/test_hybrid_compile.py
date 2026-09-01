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
