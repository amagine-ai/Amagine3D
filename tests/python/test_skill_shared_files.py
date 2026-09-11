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
SINGLE = ROOT / "skills" / "text-a3d"
COLOR = SINGLE / "color"


class SharedSkillFileTests(unittest.TestCase):
    def test_shared_entrypoints_exist_only_at_the_skill_root(self):
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

        for relative in (
            "references/multipart-connections.md",
            "references/installed-displays.md",
        ):
            with self.subTest(path=relative):
                self.assertTrue((SINGLE / relative).is_file())
                self.assertIn(relative, skill)

        self.assertFalse((SINGLE / "references" / "multipart-basics.md").exists())

    def test_skill_has_one_classified_evidence_gated_workflow(self):
        skill = (SINGLE / "SKILL.md").read_text(encoding="utf-8")
        marker = (
            "a3d-workflow:v2 classify > functional-draft > feedback > contract > "
            "compile > evidence-repair"
        )
        self.assertEqual(skill.count(marker), 1)
        self.assertEqual(skill.count("$AMAGINE3D_SKILL_DIR/SKILL.md"), 1)
        normalized = re.sub(r"\s+", " ", skill)
        self.assertIn("never substitute a cwd or global namesake", normalized)

        headings = (
            "## 1. Classify and draft visible construction",
            "## 2. Use visible and functional feedback",
            "## 3. Finalize the contract and construction",
            "## 4. Run the initial full compile",
            "## 5. Diagnose and make evidence-gated repairs",
        )
        positions = [skill.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(skill.index("\na3d compile "), positions[2])

    def test_early_routes_use_only_minimal_examples(self):
        skill = (SINGLE / "SKILL.md").read_text(encoding="utf-8")
        draft_and_feedback = skill[
            skill.index("## 1. Classify"):skill.index("## 3. Finalize")
        ]
        contract = skill[
            skill.index("## 3. Finalize"):skill.index("## 4. Run")
        ]

        self.assertIn("simple_brep_build.py", draft_and_feedback)
        self.assertIn("installed_module_draft.py", draft_and_feedback)
        self.assertNotIn("installed_module_build.py", draft_and_feedback)
        self.assertIn("installed_module_build.py", contract)
        normalized = re.sub(r"\s+", " ", skill)
        self.assertIn("assembly-critical", normalized)
        self.assertIn("manufactured parts", normalized)
        self.assertIn("Mere seams, color regions and grooves are not", normalized)

    def test_assembly_critical_skeleton_precedes_contract(self):
        skill = (SINGLE / "SKILL.md").read_text(encoding="utf-8")
        early = skill[
            skill.index("## 1. Classify"):skill.index("## 3. Finalize")
        ]
        for fragment in (
            "**functional skeleton**",
            "part owners",
            "component envelope/cavity",
            "insertion/service/driver",
            "symmetric shared fastener datums",
            "named design margin",
            "constructionFeatures",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, early)
        self.assertNotIn("references/multipart-connections.md", early)
        self.assertNotIn("installed_module_build.py", early)

    def test_repeated_work_requires_evidence_not_a_retry_count(self):
        skill = (SINGLE / "SKILL.md").read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", skill)
        for fragment in (
            "`source.sha256`",
            "(runId, issue ID, field)",
            "persisted result/runId",
            "repairDelta.resolved",
            "newlyUnblocked",
            "`remaining`",
            "`regressed`",
            "`awaiting-visual-review`",
            "`deliveryReady=false`",
            "`visualReviewRequired`",
            "direct exit status",
            "selected print orientation",
            "mesh-audit warnings",
            "never claim done, ready or review complete",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, normalized)
        self.assertNotRegex(
            normalized.lower(),
            r"(?:at most|maximum|no more than) \d+ (?:drafts?|compiles?|repairs?|iterations?)",
        )

    def test_contract_references_allow_only_unbound_drafts_before_contract(self):
        evidence = (SINGLE / "references" / "evidence-contract.md").read_text(
            encoding="utf-8"
        )
        printability = (SINGLE / "references" / "bambu-printability.md").read_text(
            encoding="utf-8"
        )
        construction = (
            SINGLE / "references" / "construction-strategies.md"
        ).read_text(encoding="utf-8")

        normalized_evidence = re.sub(r"\s+", " ", evidence)
        normalized_printability = re.sub(r"\s+", " ", printability)
        normalized_construction = re.sub(r"\s+", " ", construction)
        self.assertIn("An unbound draft may", normalized_evidence)
        self.assertIn("before contract-bound", normalized_evidence)
        self.assertIn("before the full compile", normalized_printability)
        self.assertIn("recorded selected print transform", normalized_printability)
        self.assertIn("Machine evidence wins", normalized_printability)
        self.assertIn("correct the report", normalized_printability)
        self.assertIn(
            "before contract-bound final geometry",
            normalized_construction.lower(),
        )
        self.assertIn("Never claim manufacturing acceptance", normalized_evidence)

    def test_authoring_commands_are_self_contained_and_multi_plate_is_current(self):
        authoring = (SINGLE / "references" / "authoring-example.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("example_name=", authoring)
        self.assertNotIn("${example_name}", authoring)
        self.assertNotIn("still exports a single plate", authoring)
        self.assertIn("allows multiple plates by default", authoring)
        self.assertIn("name-plate-NN.3mf", authoring)

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            shutil.copyfile(
                SINGLE / "examples" / "simple_brep_intent.py",
                workspace / "model_intent.py",
            )
            shutil.copyfile(
                SINGLE / "examples" / "bambu-a1-mini-0.4-standard.example.json",
                workspace / "model_printer-profile.json",
            )
            completed = subprocess.run(
                [sys.executable, str(workspace / "model_intent.py")],
                capture_output=True,
                check=False,
                env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SINGLE)},
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((workspace / "model_intent.json").is_file())

    def test_skill_local_links_exist(self):
        skill = (SINGLE / "SKILL.md").read_text(encoding="utf-8")
        references = set(
            re.findall(
                r"(?:references|examples|color)/[A-Za-z0-9_.\-/]+",
                skill,
            )
        )
        self.assertTrue(references)
        for relative in references:
            with self.subTest(path=relative):
                self.assertTrue((SINGLE / relative).is_file())


if __name__ == "__main__":
    unittest.main()
