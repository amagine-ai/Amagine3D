"""Calibrate exact design-envelope checks against analytic curved geometry."""

import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "text-a3d"))

from build123d import Cylinder, Pos, export_step, import_step
from build_manifest import (
    BREP_ENVELOPE_TOLERANCE_MM,
    semantic_assembly_record,
    semantic_envelope_errors,
)
from cad_helpers import _manifest_geometry_record, _stats
from export_audit import geometry_record


class BrepEnvelopePrecisionTests(unittest.TestCase):
    def test_curved_brep_and_rounded_records_obey_the_same_analytic_target(self):
        angle = math.radians(30)
        expected = [20 * math.cos(angle) + 30 * math.sin(angle),
                    20, 20 * math.sin(angle) + 30 * math.cos(angle)]
        intent = {"dimensions_mm": {
            axis: {"value": size} for axis, size in zip("xyz", expected)
        }}
        # Oblique cylindrical extrema require actual curved geometry; the
        # non-round translation also exercises four-decimal report rounding.
        shape = Pos(0.123456, -7.654321, 4.987654) * Cylinder(10, 30, rotation=(0, 30, 0))
        temporary_root = ROOT / "workspace" / "skill-validation"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            path = Path(directory) / "curved-control.step"
            export_step(shape, path)
            readback = import_step(path)
            for actual in (shape, readback):
                precise = geometry_record(actual)["boundsMm"]
                self.assertEqual(semantic_envelope_errors(
                    precise, intent, tolerance_mm=BREP_ENVELOPE_TOLERANCE_MM,
                ), [])
                self.assertLess(max(abs(a - b) for a, b in zip(precise["size"], expected)), 1e-5)
                semantic_assembly_record({"part": {
                    "semantic": _manifest_geometry_record(_stats(actual)),
                }}, "a" * 64, intent)
        smaller_intent = {"dimensions_mm": {
            axis: {"value": size - (0.01 if axis == "x" else 0)}
            for axis, size in zip("xyz", expected)
        }}
        errors = semantic_envelope_errors(
            geometry_record(shape)["boundsMm"], smaller_intent,
            tolerance_mm=BREP_ENVELOPE_TOLERANCE_MM,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("dimension x differs from intent", errors[0])


if __name__ == "__main__":
    unittest.main()
