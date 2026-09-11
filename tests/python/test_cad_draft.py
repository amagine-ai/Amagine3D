"""Exercise real draft rendering and its separation from final compile."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from build123d import import_step
from PIL import Image
import trimesh

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
sys.path.insert(0, str(SKILL))
from capability_manifest import build_manifest
from cad_draft import export_draft
from tests.python.intent_fixture import write_intent


class CadDraftTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "workspace" / "skill-validation"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("AMAGINE3D_")}
        self.environment["AMAGINE3D_PYTHON"] = sys.executable
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"

    def cli(self, *args):
        return subprocess.run(["node", str(ROOT / "bin" / "a3d.mjs"), *map(str, args)],
                              cwd=self.workspace, env=self.environment, text=True, encoding="utf-8",
                              capture_output=True, timeout=35)

    def source(self, content=None):
        source = self.workspace / "draft.py"
        source.write_text(content or "from build123d import Box\nfrom cad_draft import export_draft\nexport_draft({'body': Box(10, 10, 10)})\n")
        return source

    def test_real_installed_draft_without_contract_preserves_final_files(self):
        source = self.workspace / "installed_module_draft.py"
        shutil.copyfile(SKILL / "examples" / source.name, source)
        protected = [self.workspace / name for name in ("unit.publish.json", "unit_report.json", "unit_compile-result.json")]
        for path in protected:
            path.write_text("previous final artifact\n")
        before = {path: path.read_bytes() for path in protected}
        command = self.cli("draft", source.name)
        self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
        result = json.loads(command.stdout)
        self.assertEqual(result["schema"], "a3d-draft-result/v1")
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["issues"], [])
        self.assertFalse(result["deliveryReady"])
        self.assertEqual(result["constructionFeatures"], {
            "frame-body": {"owner": "frame", "role": "solid"},
            "module-space": {"owner": "frame", "role": "cutter"},
            "viewing-opening": {"owner": "frame", "role": "cutter"},
            "service-cover": {"owner": "cover", "role": "separate"},
        })
        self.assertIn("not intent requirements", result["constructionFeatureScope"])
        self.assertNotIn("pass", result)
        self.assertNotIn("deliverables", result)
        self.assertEqual(before, {path: path.read_bytes() for path in protected})
        self.assertFalse(list(self.workspace.glob("*intent*")))
        self.assertFalse(list(self.workspace.glob("*scene*")))
        for artifact in result["artifacts"].values():
            path = Path(artifact["path"])
            self.assertTrue(path.is_relative_to(self.workspace / ".amagine3d-drafts"))
            self.assertEqual(sha256(path.read_bytes()).hexdigest(), artifact["sha256"])
        geometry = import_step(result["artifacts"]["step"]["path"])
        self.assertEqual(len(geometry.solids()), 3)
        records = {item["name"]: item for item in result["objects"]}
        self.assertEqual(set(records), {"frame", "cover", "module-envelope"})
        self.assertNotIn("cover-exploded", records)
        self.assertEqual(records["frame"]["role"], "proposed-part")
        self.assertEqual(records["cover"]["role"], "proposed-part")
        self.assertEqual(records["module-envelope"]["role"], "component-reference")
        self.assertEqual(records["module-envelope"]["geometry"]["boundsMm"]["size"], [40, 28, 8])
        # The module cavity and smaller through viewing aperture are real BRep cuts.
        frame_volume = records["frame"]["geometry"]["volumeMm3"]
        self.assertAlmostEqual(frame_volume, 47 * 35 * 15 - 41 * 29 * 13 - 34 * 22 * 2, places=5)
        display = trimesh.load(result["artifacts"]["glb"]["path"], force="scene", process=False)
        self.assertEqual(display.metadata["status"], "draft")
        self.assertFalse(display.metadata["deliveryReady"])
        self.assertTrue(all(mesh.metadata["amagine3d"]["role"] == "display-only" for mesh in display.geometry.values()))
        with Image.open(result["artifacts"]["preview"]["path"]) as picture:
            self.assertGreaterEqual(picture.width, 640)
            self.assertGreater(len(picture.getcolors(picture.width * picture.height)), 50)

    def test_timeout_kills_source_and_descendant(self):
        self.source("import subprocess, sys, time\nfrom pathlib import Path\np = subprocess.Popen([sys.executable, '-c', \"import time; from pathlib import Path; time.sleep(1); Path('escaped-timeout').write_text('bad')\"])\nPath('child.pid').write_text(str(p.pid))\ntime.sleep(20)\n")
        command = self.cli("draft", "draft.py", "--timeout-seconds", "0.4")
        self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
        result = json.loads(command.stdout)
        self.assertEqual(result["issues"][0]["code"], "DRAFT.TIMEOUT")
        self.assertNotIn("repairHint", result["issues"][0])
        self.assertNotIn("detail", result["issues"][0])
        self.assertFalse(result["deliveryReady"])
        self.assertEqual(result["artifacts"], {})
        pid = int((self.workspace / "child.pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse((self.workspace / "escaped-timeout").exists())

    def test_workspace_paths_and_cli_override_are_rejected_before_execution(self):
        outside = self.workspace.parent / f"{self.workspace.name}-outside.py"
        outside.write_text("raise AssertionError('must not run')\n")
        self.addCleanup(outside.unlink)
        (self.workspace / "linked.py").symlink_to(outside)
        for source in (outside, "linked.py"):
            with self.subTest(source=source):
                result = self.cli("draft", source)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((self.workspace / ".amagine3d-drafts").exists())
        self.source()
        for argument in ("--workspace=..", "--workspace"):
            with self.subTest(argument=argument):
                result = self.cli("draft", "draft.py", argument, *([".."] if argument == "--workspace" else []))
                self.assertEqual(result.returncode, 2)
        (self.workspace / ".amagine3d-drafts").symlink_to(self.workspace.parent, target_is_directory=True)
        result = self.cli("draft", "draft.py")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_empty_source_and_non_solid_geometry_cannot_claim_preview(self):
        for source in ("x = 1\n", "from build123d import Rectangle\nfrom cad_draft import export_draft\nexport_draft({'face': Rectangle(5, 5)})\n",
                       "from build_session import BuildSession\nbuild = BuildSession(__file__)\n"):
            with self.subTest(source=source):
                self.source(source)
                command = self.cli("draft", "draft.py")
                self.assertEqual(command.returncode, 1)
                result = json.loads(command.stdout)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["deliveryReady"])
                self.assertEqual(result["artifacts"], {})
                hint = result["issues"][0]["repairHint"]
                self.assertIn("export_draft(parts)", hint)
                self.assertIn("BuildSession", hint)
                self.assertIn("a3d compile", hint)
                log = Path(result["log"]["path"])
                self.assertEqual(sha256(log.read_bytes()).hexdigest(), result["log"]["sha256"])
                if "BuildSession" in source:
                    self.assertEqual(result["issues"][0]["code"], "DRAFT.SOURCE_FAILED")
                    self.assertIn("AuthoringError", result["issues"][0]["detail"])
                    self.assertIn("build session authoring failed", log.read_text())
                elif source == "x = 1\n":
                    self.assertEqual(result["issues"][0]["code"], "DRAFT.INCOMPLETE")
                    self.assertIn("draft-geometry.json", result["issues"][0]["message"])

    def test_error_after_export_does_not_promote_partial_output(self):
        source = self.source()
        source.write_text(source.read_text() + "raise RuntimeError('unfinished source')\n")
        command = self.cli("draft", source.name)
        result = json.loads(command.stdout)
        self.assertEqual(command.returncode, 1)
        self.assertEqual(result["issues"][0]["code"], "DRAFT.SOURCE_FAILED")
        self.assertIn("RuntimeError: unfinished source", result["issues"][0]["detail"])
        self.assertIn("RuntimeError: unfinished source", Path(result["log"]["path"]).read_text())
        self.assertEqual(result["artifacts"], {})
        self.assertTrue((Path(result["result"]).parent / "draft-preview.png").is_file())

    def test_mutated_geometry_bytes_are_not_bound_as_ready_draft(self):
        mutations = ("(root / 'draft-preview.png').write_bytes(b'changed')", '''
p = root / "draft-geometry.json"
data = json.loads(p.read_text())
data["constructionFeatures"] = {"feature": {"owner": "absent", "role": "solid"}}
p.write_text(json.dumps(data))
''')
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                source = self.source()
                source.write_text(source.read_text() + "import json, os\nfrom pathlib import Path\nroot = Path(os.environ['AMAGINE3D_DRAFT_DIR'])\n" + mutation + "\n")
                command = self.cli("draft", source.name)
                result = json.loads(command.stdout)
                self.assertEqual(command.returncode, 1)
                self.assertEqual(result["issues"][0]["code"], "DRAFT.INCOMPLETE")
                self.assertNotIn("repairHint", result["issues"][0])
                self.assertEqual(result["artifacts"], {})
                self.assertNotIn("constructionFeatures", result)

    def test_construction_metadata_rejects_invalid_fields_before_geometry_export(self):
        invalid = ([], {" ": {"owner": "body", "role": "solid"}}, {"f": None},
                   {"f": {"owner": "body"}}, {"f": {"owner": "body", "role": "solid", "acceptance": "passed"}},
                   {"f": {"owner": "unknown", "role": "solid"}}, {"f": {"owner": "module", "role": "solid"}},
                   {"f": {"owner": [], "role": "solid"}}, {"f": {"owner": "body", "role": "requirement"}},
                   {"f": {"owner": "body", "role": []}})
        output = self.workspace / "metadata-draft"
        with patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": "draft",
                                    "AMAGINE3D_DRAFT_DIR": str(output), "AMAGINE3D_DRAFT_RUN_ID": "metadata-test"}):
            for features in invalid:
                with self.subTest(features=features), self.assertRaisesRegex(ValueError, "construction"):
                    export_draft({"body": object()}, references={"module": object()}, construction_features=features)
        self.assertFalse(output.exists())

    def test_intent_option_is_bounded_and_input_mutation_rejects_preview(self):
        intent, _ = write_intent(self.workspace, part="body", feature_owners={"body": "body"})
        source = self.source('''from build123d import Box
from build_session import BuildSession
from pathlib import Path
import os
build = BuildSession(__file__)
build.add("body", Box(10, 10, 10))
build.export()
p = Path(os.environ["AMAGINE3D_INTENT_PATH"])
p.write_text(p.read_text() + " ")
''')
        before = sha256(intent.read_bytes()).hexdigest()
        command = self.cli("draft", source.name, "--intent", intent.name)
        self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
        result = json.loads(command.stdout)
        self.assertEqual(result["intent"]["sha256"], before)
        self.assertEqual(result["issues"][0]["code"], "DRAFT.INCOMPLETE")
        self.assertIn("intent changed", result["issues"][0]["message"])
        self.assertEqual(result["artifacts"], {})
        outside = self.workspace.parent / f"{self.workspace.name}-intent.json"
        outside.write_text(intent.read_text())
        self.addCleanup(outside.unlink)
        (self.workspace / "outside-intent.json").symlink_to(outside)
        count = len(list((self.workspace / ".amagine3d-drafts").iterdir()))
        for path in (outside, "outside-intent.json"):
            command = self.cli("draft", source.name, "--intent", path)
            self.assertEqual(command.returncode, 2, command.stdout + command.stderr)
        self.assertEqual(len(list((self.workspace / ".amagine3d-drafts").iterdir())), count)

    def test_explicit_session_intent_must_match_the_managed_binding(self):
        intent, _ = write_intent(self.workspace, part="body", feature_owners={"body": "body"})
        (self.workspace / "different.json").write_bytes(intent.read_bytes())
        self.source('''from build123d import Box
from build_session import BuildSession
build = BuildSession(__file__, intent_path="different.json")
build.add("body", Box(10, 10, 10))
build.export()
''')
        for arguments in ((), ("--intent", intent.name)):
            command = self.cli("draft", "draft.py", *arguments)
            self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
            result = json.loads(command.stdout)
            self.assertEqual(result["artifacts"], {})
            self.assertIn("explicit intent_path must match", result["issues"][0]["detail"])
        command = self.cli("draft", "draft.py", "--intent", "different.json")
        self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
        result = json.loads(command.stdout)
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["intent"]["path"], str((self.workspace / "different.json").resolve()))

    def test_draft_boolean_failures_retain_spatial_witnesses(self):
        for operation, gap, code in (("add", .05, "SOURCE.UNION_DISCONNECTED"),
                                     ("cut", 2., "SOURCE.CUT_MISSED_OWNER")):
            with self.subTest(operation=operation):
                self.source(f'''from build123d import Box, Pos
from build_session import BuildSession
build = BuildSession(__file__, part_names=("body",))
build.add("body-main", Box(10, 10, 10))
build.{operation}("feature", Pos({6 + gap}, 0, 0) * Box(2, 2, 2))
build.export()
''')
                command = self.cli("draft", "draft.py")
                self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
                result = json.loads(command.stdout)
                self.assertEqual(result["artifacts"], {})
                cause = result["issues"][0]["sourceIssue"]
                self.assertEqual(cause["code"], code)
                witness = cause["booleanWitness"]
                self.assertEqual(witness["ownerPartId"], "body")
                self.assertAlmostEqual(witness["component"]["gapMm"], gap)
                self.assertEqual(len(witness["component"]["ownerPointMm"]), 3)
                binding = result["sourceDiagnostics"]
                payload = Path(binding["path"]).read_bytes()
                self.assertEqual(sha256(payload).hexdigest(), binding["sha256"])
                self.assertEqual(json.loads(payload)["runId"], result["runId"])

    def test_caught_draft_operation_failure_cannot_claim_ready_preview(self):
        source = '''from build123d import Box, Pos
from cad_helpers import checked_union
from cad_draft import export_draft
body = Box(10, 10, 10)
try:
    checked_union(body, Pos(8, 0, 0) * Box(2, 2, 2), "floating")
except RuntimeError:
    pass
export_draft({"body": body})
'''
        tails = ["", '''
from pathlib import Path
import os
Path(os.environ["AMAGINE3D_SOURCE_DIAGNOSTICS_PATH"]).write_text('{"schema":"old","issues":[]}')
''']
        for edit in ('data["issues"] = []', 'data["issues"] = [None]', 'data["pass"] = True'):
            tails.append('''
from pathlib import Path
import json, os
p = Path(os.environ["AMAGINE3D_SOURCE_DIAGNOSTICS_PATH"])
data = json.loads(p.read_text())
''' + edit + '\np.write_text(json.dumps(data))\n')
        for tail in tails:
            self.source(source + tail)
            command = self.cli("draft", "draft.py")
            self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
            result = json.loads(command.stdout)
            self.assertFalse(result["deliveryReady"])
            self.assertEqual(result["artifacts"], {})
            if tail:
                self.assertTrue(result["diagnosticWarning"])
            else:
                self.assertEqual(result["issues"][0]["sourceIssue"]["code"], "SOURCE.UNION_DISCONNECTED")

    def test_draft_api_and_incomplete_contract_cannot_enter_final_compile(self):
        marker = self.workspace / ".start"
        marker.write_text("start")
        source = self.source()
        intent, data = write_intent(self.workspace, part="body", feature_owners={"body": "body"})
        profile = self.workspace / "profile.json"
        shutil.copyfile(data["printability"]["profile"]["path"], profile)
        data["printability"]["profile"]["path"] = str(profile)
        intent.write_text(json.dumps(data))
        command = self.cli("compile", "body_scene.json", "--marker", marker.name,
                           "--intent", intent.name, "--source", source.name)
        self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
        result = json.loads((self.workspace / "body_compile-result.json").read_text())
        self.assertFalse(result["pass"])
        self.assertIn("export_draft requires a3d draft", (self.workspace / "body_compile.log").read_text())
        self.assertFalse((self.workspace / "body.publish.json").exists())
        self.assertFalse((self.workspace / ".amagine3d-drafts").exists())
        data["features"] = []
        intent.write_text(json.dumps(data))
        command = self.cli("compile", "body_scene.json", "--marker", marker.name,
                           "--intent", intent.name, "--source", source.name)
        self.assertEqual(command.returncode, 1)
        result = json.loads((self.workspace / "body_compile-result.json").read_text())
        self.assertFalse(result["pass"])
        self.assertTrue(any(issue["code"].startswith("CONTRACT.") for issue in result["issues"]))

    def test_draft_symbol_is_discoverable_and_direct_call_is_rejected(self):
        help_result = self.cli("draft", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stdout + help_result.stderr)
        self.assertIn("export_draft", help_result.stdout)
        self.assertIn("BuildSession", help_result.stdout)
        self.assertIn("a3d compile", help_result.stdout)
        record = build_manifest(["export_draft"])["query"]["export_draft"]
        self.assertTrue(record["available"])
        self.assertEqual(record["provider"], "cad_draft")
        self.assertIn("construction_features", record["parameters"])
        self.assertIn("export_draft(parts)", record["description"])
        self.assertIn("BuildSession", record["description"])
        self.assertIn("a3d compile", record["description"])
        with self.assertRaisesRegex(RuntimeError, "a3d draft"):
            export_draft({})


if __name__ == "__main__":
    unittest.main()
