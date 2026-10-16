from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]
TEXT = ROOT / "skills" / "a3d-text"
PUBLIC = ROOT / "skills" / "a3d-public"
COLOR = PUBLIC / "color"


class SharedSkillFileTests(unittest.TestCase):
    def test_shared_runtime_is_not_a_third_skill(self):
        self.assertTrue((PUBLIC / "cad_compile.py").is_file())
        self.assertTrue((PUBLIC / "build_session.py").is_file())
        self.assertTrue((PUBLIC / "geometry_binding.py").is_file())
        self.assertFalse((PUBLIC / "SKILL.md").exists())
        body = (TEXT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(body, r"(?m)^name:\s*a3d-text\s*$")
        self.assertIn("$AMAGINE3D_SKILL_DIR/SKILL.md", body)
        self.assertFalse((TEXT / "cad_compile.py").exists())
        self.assertFalse((TEXT / "color").exists())
        self.assertFalse((TEXT / "references").exists())

    def test_shared_entrypoints_exist_only_in_runtime(self):
        shared_entrypoints = (
            "bambu_profile.py",
            "cad_diagnostics.py",
            "compare_silhouette.py",
            "cpu_z_buffer.py",
            "freshness_check.py",
            "mesh_topology.py",
            "reference_analyze.py",
            "render_preview.py",
        )
        for relative in shared_entrypoints:
            with self.subTest(path=relative):
                self.assertTrue((PUBLIC / relative).is_file())
                self.assertFalse((TEXT / relative).exists())
                self.assertFalse((COLOR / relative).exists())

    def test_color_runtime_is_one_shared_namespace(self):
        command = (
            "import sys; "
            f"sys.path.insert(0, {str(PUBLIC)!r}); "
            "import cad_helpers; "
            "from color import cad_helpers as color_helpers; "
            "assert hasattr(cad_helpers, 'export_assembly'); "
            "assert hasattr(color_helpers, 'export_regions'); "
            "assert cad_helpers.__file__ != color_helpers.__file__"
        )
        completed = subprocess.run(
            [sys.executable, "-c", command], capture_output=True, check=False, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_text_skill_is_pure_brep(self):
        skill = (TEXT / "SKILL.md").read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", skill)
        for fragment in (
            "BuildSession",
            "constructionFeatures",
            "a3d-workflow:v2",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, normalized)
        for forbidden in (
            "Blender",
            "blender-mesh",
            "HybridBuildSession",
            "build_blender_shell",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, skill)
        self.assertTrue((TEXT / "examples" / "simple_brep_build.py").is_file())
        self.assertTrue((TEXT / "examples" / "installed_module_draft.py").is_file())

    def test_common_references_are_loaded_from_runtime(self):
        for relative in (
            "references/authoring-example.md",
            "references/bambu-printability.md",
            "references/bambu-profiles.json",
            "references/evidence-contract.md",
            "references/multipart-connections.md",
        ):
            with self.subTest(path=relative):
                self.assertTrue((PUBLIC / relative).is_file())
        self.assertTrue((TEXT / "examples" / "bambu-a1-mini-0.4-standard.example.json").is_file())
        body = (TEXT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("$AMAGINE3D_RUNTIME_DIR/references/", body)
        self.assertIn("$AMAGINE3D_RUNTIME_DIR/color/BACKEND.md", body)

    def test_skill_entry_fits_the_read_budget_and_its_dsl_starter_executes(self):
        import json
        sys.path.insert(0, str(PUBLIC))
        from design_api import apply, load
        from design_language import normalize
        skill = (TEXT / "SKILL.md").read_text(encoding="utf-8")
        # The native shell projection clipped the old entry at about 10 KB.
        self.assertLess(len(skill.encode("utf-8")), 8_000)
        match = re.search(r"```json\n(.*?)\n```", skill, re.S)
        self.assertIsNotNone(match)
        request = json.loads(match[1])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = apply(root, request)
            _, document, _ = load(root, request["design"], saved["revision"])
            normalize(document)
            updated = apply(root, {"action": "apply", "design": request["design"],
                "baseRevision": saved["revision"], "changes": [
                    {"kind": "update", "target": "params.wall", "set": {"value": 2.4}}]})
            self.assertTrue(updated["changed"])
            self.assertNotEqual(updated["revision"], saved["revision"])

    def test_skill_local_links_exist(self):
        skill = (TEXT / "SKILL.md").read_text(encoding="utf-8")
        references = set(
            re.findall(
                r"(?:references|examples|color)/[A-Za-z0-9_.\-/]+",
                skill,
            )
        )
        self.assertTrue(references)
        for relative in references:
            with self.subTest(path=relative):
                base = TEXT if relative.startswith("examples/") else PUBLIC
                self.assertTrue((base / relative).is_file())

    def test_error_guides_are_discoverable_from_runtime_and_links_resolve(self):
        entry = TEXT / "SKILL.md"
        guide = PUBLIC / "references" / "error-diagnostics.md"
        operations = PUBLIC / "references" / "operations"
        self.assertTrue(guide.is_file())
        self.assertTrue((operations / "booleans.md").is_file())
        self.assertTrue((operations / "finishing.md").is_file())
        self.assertIn("references/error-diagnostics.md", entry.read_text(encoding="utf-8"))
        self.assertFalse((TEXT / "references" / "error-diagnostics.md").exists())

        for source in (guide, operations / "booleans.md", operations / "finishing.md"):
            body = source.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)#]+\.md)(?:#[^)]*)?\)", body):
                with self.subTest(source=source.name, target=target):
                    self.assertTrue((source.parent / target).resolve().is_file())

        boolean_text = (operations / "booleans.md").read_text(encoding="utf-8")
        finish_text = (operations / "finishing.md").read_text(encoding="utf-8")
        for fragment in ("observed.booleanWitness", "observed.result.bodyCount", "blockedBy"):
            self.assertIn(fragment, boolean_text)
        for fragment in ("observed.selectedEdgeCount", "observed.attempts", "actual.count", "designEvidence"):
            self.assertIn(fragment, finish_text)

    def test_authoring_example_runs_with_separate_skill_and_runtime_roots(self):
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            shutil.copyfile(
                TEXT / "examples" / "simple_brep_intent.py",
                workspace / "model_intent.py",
            )
            shutil.copyfile(
                TEXT / "examples" / "bambu-a1-mini-0.4-standard.example.json",
                workspace / "model_printer-profile.json",
            )
            completed = subprocess.run(
                [sys.executable, str(workspace / "model_intent.py")],
                capture_output=True,
                check=False,
                env={
                    **os.environ,
                    "AMAGINE3D_RUNTIME_DIR": str(PUBLIC),
                    "AMAGINE3D_SKILL_DIR": str(TEXT),
                    "PYTHONPATH": str(PUBLIC),
                },
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((workspace / "model_intent.json").is_file())


if __name__ == "__main__":
    unittest.main()
