from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

from build123d import Box, Pos


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import cad_helpers  # noqa: E402


class SourceDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        cad_helpers._DEFERRED_ISSUES.clear()
        cad_helpers._EVENTS.clear()

    def tearDown(self) -> None:
        cad_helpers._DEFERRED_ISSUES.clear()
        cad_helpers._EVENTS.clear()

    def test_checked_operations_collect_independent_failures_during_compile(self):
        body = Box(10, 10, 10)
        first = Pos(100, 0, 0) * Box(1, 1, 1)
        second = Pos(200, 0, 0) * Box(1, 1, 1)

        with mock.patch.dict(
            os.environ,
            {"AMAGINE3D_SOURCE_PHASE": "compile"},
            clear=False,
        ):
            after_first = cad_helpers.checked_cut(
                body,
                first,
                "first-miss",
                part_name="body",
            )
            after_second = cad_helpers.checked_cut(
                after_first,
                second,
                "second-miss",
                part_name="body",
            )

        self.assertAlmostEqual(float(after_second.volume), float(body.volume))
        self.assertEqual(
            [issue["featureId"] for issue in cad_helpers._DEFERRED_ISSUES],
            ["first-miss", "second-miss"],
        )
        self.assertTrue(
            all(
                issue["code"] == "SOURCE.CUT_MISSED_OWNER"
                for issue in cad_helpers._DEFERRED_ISSUES
            )
        )

    def test_checked_operation_remains_fail_fast_outside_compile(self):
        body = Box(10, 10, 10)
        miss = Pos(100, 0, 0) * Box(1, 1, 1)

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(cad_helpers.BuildInvariantError):
                cad_helpers.checked_cut(body, miss, "miss")

        self.assertEqual(cad_helpers._DEFERRED_ISSUES, [])

    def test_deferred_diagnostics_emit_one_structured_issue_bundle(self):
        cad_helpers._DEFERRED_ISSUES.extend(
            [
                {
                    "code": "SOURCE.CUT_MISSED_OWNER",
                    "featureId": "first-miss",
                    "message": "first",
                    "severity": "error",
                },
                {
                    "code": "SOURCE.CUT_MISSED_OWNER",
                    "featureId": "second-miss",
                    "message": "second",
                    "severity": "error",
                },
            ]
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            with self.assertRaises(cad_helpers.BuildInvariantError):
                cad_helpers._raise_deferred_source_issues()

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schema"], "evidence-cad-source-diagnostics/v1")
        self.assertEqual(len(payload["issues"]), 2)
        self.assertEqual(cad_helpers._DEFERRED_ISSUES, [])


if __name__ == "__main__":
    unittest.main()
