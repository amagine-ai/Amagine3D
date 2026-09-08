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
                              cwd=self.workspace, env=self.environment, text=True,
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
        self.assertFalse(result["deliveryReady"])
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
        for source in ("x = 1\n", "from build123d import Rectangle\nfrom cad_draft import export_draft\nexport_draft({'face': Rectangle(5, 5)})\n"):
            with self.subTest(source=source):
                self.source(source)
                command = self.cli("draft", "draft.py")
                self.assertEqual(command.returncode, 1)
                result = json.loads(command.stdout)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["deliveryReady"])
                self.assertEqual(result["artifacts"], {})

    def test_error_after_export_does_not_promote_partial_output(self):
        source = self.source()
        source.write_text(source.read_text() + "raise RuntimeError('unfinished source')\n")
        command = self.cli("draft", source.name)
        result = json.loads(command.stdout)
        self.assertEqual(command.returncode, 1)
        self.assertEqual(result["issues"][0]["code"], "DRAFT.SOURCE_FAILED")
        self.assertEqual(result["artifacts"], {})
        self.assertTrue((Path(result["result"]).parent / "draft-preview.png").is_file())

    def test_mutated_geometry_bytes_are_not_bound_as_ready_draft(self):
        source = self.source()
        source.write_text(source.read_text() + "import os\nfrom pathlib import Path\n(Path(os.environ['AMAGINE3D_DRAFT_DIR']) / 'draft-preview.png').write_bytes(b'changed')\n")
        command = self.cli("draft", source.name)
        result = json.loads(command.stdout)
        self.assertEqual(command.returncode, 1)
        self.assertEqual(result["issues"][0]["code"], "DRAFT.INCOMPLETE")
        self.assertEqual(result["artifacts"], {})

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
        record = build_manifest(["export_draft"])["query"]["export_draft"]
        self.assertTrue(record["available"])
        self.assertEqual(record["provider"], "cad_draft")
        with self.assertRaisesRegex(RuntimeError, "a3d draft"):
            export_draft({})


if __name__ == "__main__":
    unittest.main()
