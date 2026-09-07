"""BRep manufacturing exports must contain valid triangles in the STL itself."""

from __future__ import annotations

import contextlib
from hashlib import sha256
import io
from pathlib import Path
import sys
import tempfile
import unittest

from build123d import Box, Shell, fillet
import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import cad_helpers  # noqa: E402
from authoring import write_scene  # noqa: E402
from geometry_binding import (  # noqa: E402
    GeometryBindingError,
    bind_brep_feature,
    export_shape_stl,
)
from tests.python.intent_fixture import write_intent  # noqa: E402


class BrepStlNormalizationTests(unittest.TestCase):
    def assertValidArtifact(self, path: Path) -> trimesh.Trimesh:
        payload = path.read_bytes()
        mesh = trimesh.load(path, file_type="stl", process=False)
        self.assertIsInstance(mesh, trimesh.Trimesh)
        self.assertEqual(len(payload), 84 + 50 * len(mesh.faces))
        self.assertTrue(np.isfinite(mesh.vertices).all())
        # Check the serialized faces before any loader repair or face removal.
        self.assertTrue(np.any(trimesh.triangles.cross(mesh.triangles) != 0, axis=1).all())
        mesh.merge_vertices(merge_tex=True, merge_norm=True)
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertTrue(mesh.is_volume)
        self.assertGreater(mesh.volume, 0)
        return mesh

    def test_rounded_enclosure_and_lcd_export_closed_stls_without_moving_geometry(self):
        for dimensions, radius in (((150, 112, 118), 18), ((72, 3, 44), 1.2)):
            with self.subTest(dimensions=dimensions), tempfile.TemporaryDirectory() as directory:
                box = Box(*dimensions)
                shape = fillet(box.edges(), radius)
                shape = shape.translate((20.125, -17.625, dimensions[2] / 2))
                expected = shape.bounding_box()
                path = Path(directory) / "rounded.stl"

                digest = export_shape_stl(
                    shape, path,
                    linear_tolerance_mm=0.01,
                    angular_tolerance_rad=0.1,
                )

                mesh = self.assertValidArtifact(path)
                self.assertEqual(digest, sha256(path.read_bytes()).hexdigest())
                np.testing.assert_allclose(
                    mesh.bounds,
                    [list(expected.min), list(expected.max)],
                    atol=1e-5, rtol=0,
                )
                self.assertAlmostEqual(mesh.bounds[0, 2], 0.0, places=5)
                self.assertLess(abs(mesh.volume / shape.volume - 1), 0.0003)

    def test_public_single_part_export_publishes_normalized_manufacturing_stl(self):
        cad_helpers._FEATURES.clear()
        cad_helpers._EVENTS.clear()
        cad_helpers._PARAMETERS.clear()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                box = Box(40, 30, 20)
                shape = fillet(box.edges(), 5)
                intent, _ = write_intent(
                    root, part="enclosure",
                    feature_owners={"enclosure-body": "enclosure"},
                    dimensions_mm=(40, 30, 20),
                )
                scene = root / "enclosure_scene.json"
                write_scene(
                    scene, intent_path=intent,
                    parts={"enclosure": {
                        "representationMaster": "brep",
                        "nodes": [bind_brep_feature(
                            node_id="enclosure-node", feature_id="enclosure-body",
                            role="solid", shape=shape, path=root / "enclosure-bound.stl",
                        )],
                    }},
                )
                cad_helpers.observe(shape, "enclosure-body", "additive")
                with contextlib.redirect_stdout(io.StringIO()):
                    report = cad_helpers.export_part(
                        shape, "enclosure", str(root),
                        intent_path=str(intent), scene_path=str(scene), source_path=__file__,
                    )

                reference = report["artifacts"]["stl:enclosure"]
                path = Path(reference["path"])
                mesh = self.assertValidArtifact(path)
                self.assertEqual(reference["sha256"], sha256(path.read_bytes()).hexdigest())
                self.assertAlmostEqual(mesh.bounds[0, 2], 0.0, places=5)
                np.testing.assert_allclose(sorted(mesh.extents), [20, 30, 40], atol=1e-5)
                self.assertTrue(report["backendData"]["exportAudit"]["pass"])
        finally:
            cad_helpers._FEATURES.clear()
            cad_helpers._EVENTS.clear()
            cad_helpers._PARAMETERS.clear()

    def test_open_brep_is_rejected_without_replacing_previous_stl(self):
        box = Box(40, 30, 20)
        open_shell = Shell(box.faces()[:-1])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previous.stl"
            path.write_bytes(b"previous successful artifact")
            with self.assertRaises(GeometryBindingError):
                export_shape_stl(open_shell, path)
            self.assertEqual(path.read_bytes(), b"previous successful artifact")


if __name__ == "__main__":
    unittest.main()
