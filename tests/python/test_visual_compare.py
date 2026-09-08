import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import trimesh

SKILL = Path(__file__).resolve().parents[2] / "skills" / "text-a3d"
sys.path.insert(0, str(SKILL))
from cpu_z_buffer import MeshInput, RenderLimits, render_view
from visual_compare import compare_revisions


def box(size=(10, 10, 10), translation=(0, 0, 0)):
    mesh = trimesh.creation.box(extents=size)
    mesh.apply_translation(translation)
    return [MeshInput("box", mesh)]


class RevisionComparisonTests(unittest.TestCase):
    def test_identical_geometry_has_no_projected_change(self):
        image, result = compare_revisions(box(), box(), size=320)
        self.assertEqual(image.size, (1008, 394))
        self.assertEqual(result["projectedPixels"]["beforeOnly"], 0)
        self.assertEqual(result["projectedPixels"]["afterOnly"], 0)
        self.assertEqual(result["projectedPixels"]["intersectionOverUnion"], 1)
        self.assertNotIn("pass", result)

    def test_uniform_scale_is_visible_in_shared_frame(self):
        _, result = compare_revisions(box(), box((20, 20, 20)), size=320)
        pixels = result["projectedPixels"]
        self.assertGreater(pixels["after"], 3.8 * pixels["before"])
        self.assertEqual(pixels["beforeOnly"], 0)
        self.assertFalse(result["alignmentApplied"])

    def test_translation_is_not_automatically_recentered(self):
        _, result = compare_revisions(box(), box(translation=(5, 0, 0)), size=320)
        pixels = result["projectedPixels"]
        self.assertGreater(pixels["beforeOnly"], 1000)
        self.assertGreater(pixels["afterOnly"], 1000)
        self.assertAlmostEqual(pixels["beforeOnly"], pixels["afterOnly"], delta=300)

    def test_view_can_hide_depth_change_without_claiming_geometry_equal(self):
        _, result = compare_revisions(box(), box((10, 20, 10)), view="front", size=320)
        self.assertEqual(result["projectedPixels"]["intersectionOverUnion"], 1)
        self.assertNotEqual(result["beforeBoundsMm"], result["afterBoundsMm"])
        self.assertIn("occluded", result["scope"])

    def test_limits_and_invalid_frames_are_rejected(self):
        with self.assertRaises(ValueError):
            compare_revisions(box(), box(), size=320, limits=RenderLimits(max_triangles=20))
        for frame in (([0, 0, 0], [1, 1, 1]), ([np.nan, 0, 0], [10, 10, 10])):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                render_view(box(), "front", 320, frame_bounds=frame)

    def test_cli_binds_inputs_and_rejects_overwriting_or_escaping_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("before", "after"):
                box()[0].mesh.export(root / f"{name}.stl")
            command = [sys.executable, str(SKILL / "visual_compare.py"), "before.stl", "after.stl", "--size", "320"]
            result = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout)
            self.assertEqual(record["before"]["sha256"], hashlib.sha256((root / "before.stl").read_bytes()).hexdigest())
            self.assertEqual(record["preview"]["sha256"], hashlib.sha256(Path(record["preview"]["path"]).read_bytes()).hexdigest())
            (root / "alias.png").hardlink_to(root / "before.stl")
            for extra in (["--out", "before.stl"], ["--out", "alias.png"], ["--out", "../escaped.png"], ["--out", "same", "--report", "same"]):
                bad = subprocess.run([*command, *extra], cwd=root, capture_output=True, text=True)
                self.assertNotEqual(bad.returncode, 0)

    def test_unsupported_3mf_returns_actionable_error_before_optional_importer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Format rejection happens before invoking an optional 3MF loader.
            (root / "part.3mf").write_bytes(b"not-needed-for-format-dispatch")
            result = subprocess.run(
                [sys.executable, str(SKILL / "visual_compare.py"), "part.3mf", "part.3mf"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("semantic display GLB", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
