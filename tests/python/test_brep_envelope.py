"""Calibrate ordinary and precise dimension checks against analytic curved geometry."""

import math
import json
from pathlib import Path
import subprocess
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
            # The public intent-only STEP audit must use the same raw dimension
            # allowance and ranges, even when --tol would permit a larger error.
            cases = [
                ({"value": expected[0] - 0.009}, True),
                ({"value": expected[0] - 0.011}, False),
                ({"value": expected[0] - 0.00005, "measurement_precision_mm": 0.0001}, True),
                ({"value": expected[0] - 0.00015, "measurement_precision_mm": 0.0001}, False),
                ({"value": expected[0] - 2, "constraint": {"kind": "range",
                    "min_mm": expected[0] - 2.1, "max_mm": expected[0] - 0.009}}, True),
                ({"value": expected[0] - 2, "constraint": {"kind": "range",
                    "min_mm": expected[0] - 2.1, "max_mm": expected[0] - 0.011}}, False),
            ]
            for item, passes in cases:
                with self.subTest(intent_only=item):
                    standalone = {"dimensions_mm": {
                        axis: {"value": value} for axis, value in zip("xyz", expected)}}
                    standalone["dimensions_mm"]["x"] = item
                    intent_path = Path(directory) / "intent-only.json"
                    intent_path.write_text(json.dumps(standalone), encoding="utf-8")
                    command = subprocess.run([sys.executable, str(ROOT / "skills/text-a3d/step_check.py"),
                        str(path), "--intent", str(intent_path), "--tol", "10"],
                        capture_output=True, text=True, timeout=60)
                    result = json.loads(command.stdout)
                    self.assertEqual(command.returncode, 0 if passes else 1, command.stderr + command.stdout)
                    checks = {check["name"]: check for check in result["checks"]}
                    self.assertEqual(checks["intent_envelope_dimensions"]["pass"], passes)
                    self.assertTrue(checks["intent_step_unchanged"]["pass"])
                    raw = checks["intent_envelope_dimensions"]["observed"]["bounds_mm"]["size"][0]
                    self.assertLess(abs(raw - expected[0]), 1e-5)
                    self.assertNotIn("dimension_x", checks)
            for delta, flags, passes in ((0.009, [], True), (0.011, [], False),
                                         (0.011, ["--tol", "0.02"], True)):
                with self.subTest(explicit_expect_delta=delta, flags=flags):
                    command = subprocess.run([sys.executable, str(ROOT / "skills/text-a3d/step_check.py"),
                        str(path), "--expect-x", str(expected[0] - delta), *flags],
                        capture_output=True, text=True, timeout=60)
                    result = json.loads(command.stdout)
                    self.assertEqual(command.returncode, 0 if passes else 1, command.stderr + command.stdout)
                    check = next(check for check in result["checks"] if check["name"] == "dimension_x")
                    self.assertEqual(check["expected"]["tolerance"], 0.02 if flags else 0.01)
        smaller_intent = {"dimensions_mm": {
            axis: {"value": size - (0.011 if axis == "x" else 0)}
            for axis, size in zip("xyz", expected)
        }}
        errors = semantic_envelope_errors(
            geometry_record(shape)["boundsMm"], smaller_intent,
            tolerance_mm=BREP_ENVELOPE_TOLERANCE_MM,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("dimension x differs from intent", errors[0])
        smaller_intent["dimensions_mm"]["x"]["value"] = expected[0] - 0.009
        self.assertEqual(semantic_envelope_errors(geometry_record(shape)["boundsMm"], smaller_intent), [])
        smaller_intent["dimensions_mm"]["x"]["measurement_precision_mm"] = 0.0001
        self.assertTrue(semantic_envelope_errors(geometry_record(shape)["boundsMm"], smaller_intent))


if __name__ == "__main__":
    unittest.main()
