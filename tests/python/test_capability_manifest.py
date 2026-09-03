from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import capability_manifest  # noqa: E402
import capability_registry  # noqa: E402


class CapabilityManifestTests(unittest.TestCase):
    def test_manifest_reports_the_pinned_api_without_importing_model_source(self):
        manifest = capability_manifest.build_manifest(["Sphere", "Ellipsoid"])

        self.assertEqual(manifest["schema"], "evidence-cad-capabilities/v1")
        self.assertEqual(manifest["authoring"]["agentLoop"], "agent-directed")
        self.assertRegex(manifest["fingerprint"], re.compile(r"[0-9a-f]{64}"))
        self.assertTrue(manifest["query"]["Sphere"]["available"])
        self.assertFalse(manifest["query"]["Ellipsoid"]["available"])
        self.assertIn("Sphere", manifest["build123d"]["publicSymbols"])
        self.assertNotIn("Ellipsoid", manifest["build123d"]["publicSymbols"])
        self.assertTrue(
            next(
                item
                for item in manifest["authoring"]["modelingRecipes"]
                if item["id"] == "sdf-organic-shell"
            )["available"]
        )
        self.assertTrue(
            next(
                item
                for item in manifest["authoring"]["modelingRecipes"]
                if item["id"] == "checked-brep-features"
            )["available"]
        )
        self.assertNotIn(
            "scaled-round-volume",
            {
                item["id"]
                for item in manifest["authoring"]["modelingRecipes"]
            },
        )
        self.assertEqual(manifest["runtime"]["manifold3d"], "3.5.2")

    def test_interface_proofs_are_one_registry_backed_manifest(self):
        manifest = capability_manifest.build_manifest()
        proofs = manifest["authoring"]["interfaceProofs"]
        manifested_connections = {
            connection
            for proof in proofs
            for connection in proof["connectionKinds"]
        }

        self.assertEqual(
            manifested_connections,
            capability_registry.connection_kinds(),
        )
        self.assertEqual(
            manifest["policies"]["geometryToleranceMm"],
            capability_registry.GEOMETRY_TOLERANCE_MM,
        )
        proofs[0]["connectionKinds"] = ()
        self.assertTrue(capability_registry.proof_capabilities()[0]["connectionKinds"])

    def test_symbol_queries_do_not_change_the_runtime_fingerprint(self):
        baseline = capability_manifest.build_manifest()
        queried = capability_manifest.build_manifest(["Sphere", "Ellipsoid"])

        self.assertEqual(baseline["fingerprint"], queried["fingerprint"])

    def test_manifest_exposes_current_interface_helper_signatures(self):
        manifest = capability_manifest.build_manifest()
        authoring = {
            item["name"]: item["parameters"]
            for item in manifest["authoring"]["authoringHelpers"]
        }
        self.assertEqual(authoring["paired_dimensions"], ["interface"])
        self.assertNotIn(
            "female_dimensions_mm",
            authoring["paired_interface"],
        )
        helpers = {
            item["name"]: item["parameters"]
            for item in manifest["authoring"]["interfaceRecipes"]
        }

        self.assertIn("collar_socket", helpers)
        self.assertIn("radial_clearance_mm", helpers["collar_socket"])
        self.assertIn("self_tapping_screw_pair", helpers)

        shell_helpers = {
            item["name"]: item["parameters"]
            for item in manifest["authoring"]["organicShellHelpers"]
        }
        self.assertIn("build_organic_shell", shell_helpers)
        self.assertIn("cavity_strategy", shell_helpers["build_organic_shell"])
        self.assertIn("self_supporting_cavity", shell_helpers)

        geometry_helpers = {
            item["name"]: item["parameters"]
            for item in manifest["authoring"]["geometryHelpers"]
        }
        self.assertIn("checked_cut", geometry_helpers)
        self.assertIn("checked_union", geometry_helpers)
        self.assertIn("min_added_mm3", geometry_helpers["checked_union"])


if __name__ == "__main__":
    unittest.main()
