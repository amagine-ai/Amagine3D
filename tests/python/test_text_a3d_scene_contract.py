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


def _intent_ref(root: Path) -> dict:
    path = root / "device_intent.json"
    path.write_text(
        json.dumps({"schema": "evidence-cad-intent/v4", "part": "device"}),
        encoding="utf-8",
    )
    return {
        "path": path.name,
        "schema": "evidence-cad-intent/v4",
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _scene(root: Path) -> dict:
    return {
        "schema": "evidence-semantic-scene/v1",
        "revision": "rev-001",
        "intentRef": _intent_ref(root),
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Y"},
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
                    "kind": "darkAperture",
                    "parameters": {"diameterMm": 3.35},
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


class SemanticSceneContractTests(unittest.TestCase):
    def test_validates_mutable_graph_separate_from_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _scene(root)
            self.assertEqual(scene_contract.validate(data, root), [])

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

            display["physicalFeatureRef"] = "base-shell"
            display["recipe"]["parameters"]["sourceMesh"] = "screen-plane.ply"
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
            self._write_physical_glb(glb, body)
            body.export(stl)

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
