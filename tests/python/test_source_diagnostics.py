from __future__ import annotations

import contextlib
import io
import json
import os
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from build123d import Box, Pos


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import cad_helpers  # noqa: E402
from tests.python.intent_fixture import write_intent  # noqa: E402


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

    def test_public_compile_preserves_failed_boolean_measurements_and_never_publishes(self):
        directory = ROOT / "workspace/skill-validation"
        directory.mkdir(parents=True, exist_ok=True)
        cases = (
            ("build.add('body', Box(10,10,10))\nbuild.add('feature', Pos(6.05,0,0)*Box(2,2,2))\n", "SOURCE.UNION_DISCONNECTED", 0.05),
            ("build.add('body', Box(10,10,10)-Box(6,6,6))\nbuild.cut('feature', Box(2,2,2))\n", "SOURCE.CUT_MISSED_OWNER", 2.0),
        )
        for content, code, gap in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory(dir=directory) as temporary:
                root = Path(temporary)
                (root / ".start").write_text("start")
                intent, data = write_intent(root, part="body", feature_owners={"body": "body", "feature": "body"})
                profile = root / "profile.json"
                shutil.copyfile(data["printability"]["profile"]["path"], profile)
                data["printability"]["profile"]["path"] = str(profile)
                intent.write_text(json.dumps(data))
                source = root / "build.py"
                source.write_text("from build123d import Box, Pos\nfrom build_session import BuildSession\nbuild=BuildSession(__file__)\n" + content + "build.export()\n")
                inputs = {p: sha256(p.read_bytes()).hexdigest() for p in (source, intent)}
                environment = {key: value for key, value in os.environ.items() if not key.startswith("AMAGINE3D_")}
                environment.update(AMAGINE3D_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE="1")
                command = subprocess.run(
                    ["node", str(ROOT / "bin/a3d.mjs"), "compile", "body_scene.json", "--marker", ".start",
                     "--intent", intent.name, "--source", source.name], cwd=root, env=environment,
                    capture_output=True, text=True, timeout=35,
                )
                self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
                result = json.loads((root / "body_compile-result.json").read_text())
                issue = next(item for item in result["issues"] if item["code"] == code)
                witness = issue["observed"]["booleanWitness"]
                self.assertEqual(witness["components"][0]["gapMm"], gap)
                self.assertEqual(witness["coordinateFrame"], "operation-input")
                diagnostics = json.loads(Path(result["artifacts"]["sourceDiagnostics"]["path"]).read_text())
                self.assertEqual(diagnostics["runId"], result["runId"])
                self.assertEqual(diagnostics["issues"][0]["observed"]["booleanWitness"], witness)
                self.assertFalse(result["pass"])
                self.assertFalse(result["deliveryReady"])
                self.assertFalse((root / "body.publish.json").exists())
                self.assertFalse(list(root.glob("*.step")))
                self.assertEqual(inputs, {p: sha256(p.read_bytes()).hexdigest() for p in inputs})


if __name__ == "__main__":
    unittest.main()
