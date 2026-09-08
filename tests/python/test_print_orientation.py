"""Curved BRep candidates must preserve contact evidence in strict JSON."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from build123d import Box, BuildPart, BuildSketch, Circle, Plane, loft
import numpy as np
import trimesh

SKILL = Path(__file__).resolve().parents[2] / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import cad_helpers  # noqa: E402
from color import cad_helpers as color_helpers  # noqa: E402


class PrintOrientationTests(unittest.TestCase):
    def test_loft_candidates_serialize_without_inventing_bed_contact(self):
        with BuildPart() as envelope:
            with BuildSketch(Plane.XY):
                Circle(8)
            with BuildSketch(Plane.XY.offset(20)):
                Circle(5)
            loft()

        for helpers in (cad_helpers, color_helpers):
            with self.subTest(backend=helpers.__name__):
                candidates = helpers._orientation_candidates(envelope.part, {})
                json.dumps(candidates, allow_nan=False)
                contacting = [
                    candidate for candidate in candidates
                    if candidate["orientation_metrics"]["contact_area_mm2"] > 0
                ]
                noncontacting = [
                    candidate for candidate in candidates
                    if candidate["orientation_metrics"]["contact_area_mm2"] == 0
                ]
                self.assertTrue(contacting)
                self.assertTrue(noncontacting)
                for candidate in noncontacting:
                    metrics = candidate["orientation_metrics"]
                    self.assertIsNone(metrics["stability_offset_ratio"])
                    self.assertIsNone(metrics["contact_bounds_mm"])
                    self.assertFalse(metrics["center_inside_contact_bounds"])
                    self.assertTrue(np.isfinite(candidate["score"]).all())
                for candidate in contacting:
                    metrics = candidate["orientation_metrics"]
                    self.assertIsNotNone(metrics["stability_offset_ratio"])
                    self.assertTrue(metrics["center_inside_contact_bounds"])

                selected = min(candidates, key=lambda candidate: candidate["score"])
                self.assertGreater(selected["orientation_metrics"]["contact_area_mm2"], 0)

    def test_empty_or_invalid_orientation_mesh_fails_explicitly(self):
        invalid = trimesh.Trimesh(
            vertices=[[0.0, 0.0, 0.0]] * 3, faces=[[0, 1, 2]], process=False
        )
        for helpers, error_type in (
            (cad_helpers, cad_helpers.BuildInvariantError),
            (color_helpers, color_helpers.RegionInvariantError),
        ):
            for mesh in (trimesh.Trimesh(), invalid):
                with self.subTest(backend=helpers.__name__, empty=mesh.is_empty):
                    with patch.object(helpers, "export_shape_stl"), patch.object(
                        helpers.trimesh, "load", return_value=mesh
                    ):
                        with self.assertRaisesRegex(error_type, "empty or invalid"):
                            helpers._mesh_orientation_metrics(Box(2, 2, 2), threshold_deg=30)


if __name__ == "__main__":
    unittest.main()
