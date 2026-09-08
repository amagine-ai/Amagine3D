from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np
import trimesh
from build123d import Box, Rectangle


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from display_glb import DisplayGlbError, export_display_glb, load_display_components
from geometry_binding import GeometryBindingError, bind_display_component


def surface() -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=[[1, 2, 3], [5, 2, 3], [5, 4, 3], [1, 4, 3]],
        faces=[[0, 1, 2], [0, 2, 3]],
        process=False,
    )


class DisplayComponentTests(unittest.TestCase):
    def bind(self, root: Path, shape, suffix: str = ".ply") -> dict:
        return bind_display_component(
            node_id="installed-component",
            feature_id="reference/installed-component",
            physical_feature_ref="component-support",
            shape=shape,
            path=root / f"component-source{suffix}",
            appearance={"baseColor": "#aabbcc", "roughness": 0.2},
        )

    def test_open_mesh_binds_without_inventing_a_printable_volume(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            mesh = surface()
            original_vertices = mesh.vertices.copy()
            node = self.bind(root, mesh)
            self.assertEqual(node["role"], "display-only")
            self.assertEqual(node["operation"], "none")
            self.assertEqual(node["physicalFeatureRef"], "component-support")
            source = node["recipe"]["parameters"]["sourceMesh"]
            self.assertEqual(source["scale"], 1.0)
            self.assertEqual(source["sha256"], sha256(Path(source["path"]).read_bytes()).hexdigest())
            items = load_display_components({"nodes": [node]}, root / "scene.json")
            self.assertEqual(items[0][0], "installed-component")
            self.assertFalse(items[0][1].is_watertight)
            np.testing.assert_allclose(items[0][1].bounds, mesh.bounds)
            np.testing.assert_array_equal(mesh.vertices, original_vertices)
            self.assertEqual(items[0][2]["baseColor"], "#AABBCC")

    def test_brep_solid_and_face_sources_are_supported(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for shape, suffix in [(Box(2, 3, 4), ".stl"), (Rectangle(2, 3).face(), ".ply")]:
                with self.subTest(suffix=suffix):
                    node = self.bind(root, shape, suffix)
                    items = load_display_components({"nodes": [node]}, root / "scene.json")
                    self.assertGreater(len(items[0][1].faces), 0)
                    self.assertEqual(node["recipe"]["kind"], "displayComponent")

    def test_bound_source_change_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            node = self.bind(root, surface())
            Path(node["recipe"]["parameters"]["sourceMesh"]["path"]).write_bytes(b"changed")
            with self.assertRaisesRegex(DisplayGlbError, "sha256 does not match"):
                load_display_components({"nodes": [node]}, root / "scene.json")

    def test_degenerate_surface_is_rejected_without_writing_a_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            mesh = surface()
            mesh.vertices[2] = mesh.vertices[1]
            with self.assertRaisesRegex(GeometryBindingError, "zero-area"):
                self.bind(root, mesh)
            self.assertFalse((root / "component-source.ply").exists())

    def test_glb_roundtrip_encodes_roles_and_support_without_name_heuristics(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            node = self.bind(root, surface())
            display = load_display_components({"nodes": [node]}, root / "scene.json")
            physical = trimesh.creation.box(extents=[2, 3, 4])
            physical.metadata["amagine3d"] = {"role": "display-only", "physicalFeatureRef": "stale"}
            path = root / "assembly-display.glb"
            report = export_display_glb(
                [("screen-lens", physical, {"baseColor": "#AABBCC"})],
                path,
                display_items=display,
            )
            self.assertEqual(report["physicalNodeNames"], ["screen-lens"])
            self.assertEqual(report["displayOnlyNodeNames"], ["installed-component"])
            payload = path.read_bytes()
            length = struct.unpack_from("<I", payload, 12)[0]
            tree = json.loads(payload[20:20 + length])
            extras = {
                mesh["extras"]["name"]: mesh["extras"]["amagine3d"]
                for mesh in tree["meshes"]
            }
            self.assertEqual(extras, {
                "screen-lens": {"role": "manufactured"},
                "installed-component": {
                    "role": "display-only", "physicalFeatureRef": "component-support"
                },
            })
            self.assertEqual(physical.metadata["amagine3d"]["role"], "display-only")

    def test_glb_allows_an_entirely_manufactured_preview(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            result = export_display_glb(
                [("housing", trimesh.creation.box(), {"baseColor": "#123456"})],
                root / "housing-display.glb",
            )
            self.assertEqual(result["displayOnlyNodeNames"], [])
            self.assertEqual(result["physicalNodeNames"], ["housing"])
            self.assertTrue(result["verified"])


if __name__ == "__main__":
    unittest.main()
