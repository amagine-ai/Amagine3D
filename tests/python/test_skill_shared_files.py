from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SINGLE = ROOT / "skills" / "text-a3d"
COLOR = SINGLE / "color"


class SharedSkillFileTests(unittest.TestCase):
    def test_shared_entrypoints_exist_only_at_the_skill_root(self):
        shared_entrypoints = (
            "bambu_profile.py",
            "compare_silhouette.py",
            "cpu_z_buffer.py",
            "freshness_check.py",
            "reference_analyze.py",
            "render_preview.py",
        )
        for relative in shared_entrypoints:
            with self.subTest(path=relative):
                self.assertTrue((SINGLE / relative).is_file())
                self.assertFalse((COLOR / relative).exists())

    def test_shared_profiles_and_printability_reference_exist_only_at_root(self):
        shared_resources = (
            "examples/bambu-a1-mini-0.4-standard.example.json",
            "references/bambu-printability.md",
            "references/bambu-profiles.json",
        )
        for relative in shared_resources:
            with self.subTest(path=relative):
                self.assertTrue((SINGLE / relative).is_file())
                self.assertFalse((COLOR / relative).exists())

    def test_color_runtime_is_a_namespace_not_a_competing_import_root(self):
        command = (
            "import sys; "
            f"sys.path.insert(0, {str(SINGLE)!r}); "
            "import cad_helpers; "
            "from color import cad_helpers as color_helpers; "
            "assert hasattr(cad_helpers, 'export_assembly'); "
            "assert hasattr(color_helpers, 'export_regions'); "
            "assert cad_helpers.__file__ != color_helpers.__file__"
        )
        completed = subprocess.run(
            [sys.executable, "-c", command],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_specialist_guidance_is_routed_without_loading_it_for_every_task(self):
        skill = (SINGLE / "SKILL.md").read_text(encoding="utf-8")
        construction = (
            SINGLE / "references" / "construction-strategies.md"
        ).read_text(encoding="utf-8")
        evidence = (SINGLE / "references" / "evidence-contract.md").read_text(
            encoding="utf-8"
        )

        for relative in (
            "references/multipart-basics.md",
            "references/multipart-connections.md",
            "references/installed-displays.md",
        ):
            with self.subTest(path=relative):
                self.assertTrue((SINGLE / relative).is_file())
                self.assertIn(relative, skill)

        self.assertNotIn("two symmetric M3", construction)
        self.assertNotIn('"id": "screen-active-surface"', evidence)


if __name__ == "__main__":
    unittest.main()
