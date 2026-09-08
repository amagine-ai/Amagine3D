from __future__ import annotations

from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import capability_manifest  # noqa: E402
import capability_registry  # noqa: E402
import intent_contract  # noqa: E402


class CapabilityManifestTests(unittest.TestCase):
    def test_managed_queries_expose_installed_call_parameters_without_importing_kernel(self):
        completed = subprocess.run(
            [sys.executable, "-c", (
                "import json, sys; import capability_manifest; "
                "result = capability_manifest.build_manifest(['Cylinder', 'RectangleRounded', 'extrude', 'Pos', 'Rot', 'MM', 'write_intent', 'BuildSession', 'BuildSession.finish']); "
                "assert 'build123d' not in sys.modules; "
                "assert 'build_session' not in sys.modules; "
                "assert not any(name.startswith('OCP') for name in sys.modules); "
                "print(json.dumps(result['query']))"
            )],
            cwd=SKILL, check=True, capture_output=True, text=True,
        )
        query = json.loads(completed.stdout)
        self.assertIn("radius", query["Cylinder"]["parameters"])
        self.assertIn("rotation", query["Cylinder"]["parameters"])
        self.assertNotIn("axis", query["Cylinder"]["parameters"])
        self.assertNotIn("self", query["Cylinder"]["parameters"])
        self.assertIn("width", query["RectangleRounded"]["parameters"])
        self.assertIn("both: bool=False", query["extrude"]["signature"])
        self.assertTrue(any("X: float=0" in item for item in query["Pos"]["overloadSignatures"]))
        self.assertTrue(all(item.startswith("Rot(") for item in query["Rot"]["overloadSignatures"]))
        self.assertTrue(query["MM"]["available"])
        self.assertNotIn("signature", query["MM"])
        self.assertIn("inputConstraints", query["write_intent"])
        self.assertEqual(query["BuildSession"]["provider"], "build_session")
        self.assertIn("operation", query["BuildSession.finish"]["parameters"])

    def test_session_operations_are_discoverable_without_extra_contract_inputs(self):
        names = ["BuildSession", *[f"BuildSession.{method}" for method in
                                  ("add", "cut", "part", "observe", "finish", "capture", "export")]]
        manifest = capability_manifest.build_manifest(names)
        for name in names:
            item = manifest["query"][name]
            self.assertTrue(item["available"])
            self.assertEqual(item["provider"], "build_session")
            self.assertTrue(item["description"])
        self.assertEqual(manifest["query"]["BuildSession.add"]["parameters"],
                         ["self", "feature_id", "shape", "min_added_mm3"])
        self.assertEqual(manifest["query"]["BuildSession.cut"]["parameters"],
                         ["self", "feature_id", "tool", "min_removed_mm3"])
        self.assertIn("interfaces", manifest["query"]["BuildSession.export"]["parameters"])
        self.assertEqual({item["name"] for item in manifest["authoring"]["buildSessionHelpers"]}, set(names))

    def test_intent_query_exposes_nested_constraints_without_growing_other_queries(self):
        manifest = capability_manifest.build_manifest(["write_intent", "write_scene"])
        helper = manifest["query"]["write_intent"]
        constraints = helper["inputConstraints"]
        feature = constraints["parts.*.features[]"]
        self.assertEqual(feature["required"], ["id", "evidence", "acceptance"])
        self.assertEqual(feature["optionalEnums"]["kind"], sorted(intent_contract.FEATURE_KINDS))
        self.assertEqual(feature["optionalEnums"]["face"], sorted(intent_contract.FACES))
        self.assertIn("port", feature["optionalEnums"]["kind"])
        self.assertNotIn("passage", feature["optionalEnums"]["kind"])
        self.assertIn("back", feature["optionalEnums"]["face"])
        self.assertNotIn("rear", feature["optionalEnums"]["face"])
        self.assertEqual(feature["openingFields"]["whenKind"], sorted(intent_contract.PLACED_OPENING_KINDS))
        self.assertEqual(feature["faceDirections"]["back"], sorted(intent_contract.FACE_DIRECTIONS["back"]))
        self.assertIn("parts.*.features[].id", constraints["critical_features"])
        self.assertEqual(constraints["parts"]["multipart"]["minimumInterfaces"], 1)
        self.assertNotIn("inputConstraints", manifest["query"]["write_scene"])
        self.assertLess(len(json.dumps(helper, indent=2)), 8_000)

    def test_intent_constraints_follow_validator_changes_and_participate_in_fingerprint(self):
        baseline = capability_manifest.build_manifest(["write_intent"])
        self.assertEqual(baseline["fingerprint"], capability_manifest.build_manifest()["fingerprint"])
        with mock.patch.object(intent_contract, "FACES", intent_contract.FACES | {"test-face"}):
            changed = capability_manifest.build_manifest(["write_intent"])
        self.assertIn("test-face", changed["query"]["write_intent"]["inputConstraints"]["parts.*.features[]"]["optionalEnums"]["face"])
        self.assertNotEqual(changed["fingerprint"], baseline["fingerprint"])

    def test_source_signatures_follow_export_aliases_and_inherited_constructor(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / "__init__.py").write_text(
                "raise RuntimeError('source must never execute')\n"
                "from .objects import *\n"
                "from .unrelated import *\n"
                "__all__ = ['Alias']\n", encoding="utf-8",
            )
            (package / "objects.py").write_text(
                "class Base:\n"
                "    def __init__(self, width: float, /, *, gap: float=0.2): pass\n"
                "class Child(Base): pass\n"
                "Alias = Child\n"
                "__all__ = ['Alias']\n", encoding="utf-8",
            )
            (package / "unrelated.py").write_text(
                "def Alias(wrong): pass\n__all__ = []\n", encoding="utf-8",
            )
            with mock.patch.object(
                capability_manifest.util, "find_spec",
                return_value=SimpleNamespace(origin=str(package / "__init__.py")),
            ):
                query = capability_manifest._ManagedSignatures().query("Alias")
            self.assertEqual(query["signature"], "Alias(width: float, /, *, gap: float=0.2)")
            self.assertEqual(query["parameters"], ["width", "gap"])

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
                if item["id"] == "section-loft-shell"
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
        self.assertIn(
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

        geometry_helpers = {
            item["name"]: item["parameters"]
            for item in manifest["authoring"]["geometryHelpers"]
        }
        self.assertIn("checked_cut", geometry_helpers)
        self.assertIn("checked_union", geometry_helpers)
        self.assertIn("min_added_mm3", geometry_helpers["checked_union"])

        binding_helpers = {
            item["name"]: item["parameters"]
            for item in manifest["authoring"]["geometryBindingHelpers"]
        }
        self.assertIn("bind_brep_feature", binding_helpers)
        self.assertIn("shape", binding_helpers["bind_brep_feature"])

    def test_owned_feature_and_plate_planning_signatures_are_discoverable(self):
        names = ["BrepFeature", "BrepFeature.bind", "BrepFeature.cut_from", "plan_plates"]
        query = capability_manifest.build_manifest(names)["query"]
        self.assertEqual(query["BrepFeature"]["parameters"], ["id", "part", "role", "shape"])
        self.assertIn("path", query["BrepFeature.bind"]["parameters"])
        self.assertIn("min_removed_mm3", query["BrepFeature.cut_from"]["parameters"])
        self.assertEqual(query["BrepFeature"]["provider"], "geometry_binding")
        self.assertEqual(query["plan_plates"]["provider"], "plate_layout")
        self.assertEqual(query["plan_plates"]["parameters"], ["bboxes", "profile", "spacing_mm", "edge_margin_mm", "max_plates"])
        self.assertIn("max_plates: int=1", query["plan_plates"]["signature"])
        self.assertIn("revision", capability_manifest.build_manifest(["write_intent"])["query"]["write_intent"]["parameters"])

    def test_public_authoring_surface_supports_brep_without_mesh_master_helpers(self):
        retired = ["bind_mesh_feature", "build_organic_shell", "self_supporting_cavity"]
        manifest = capability_manifest.build_manifest(["loft", "bind_brep_feature", *retired])
        self.assertTrue(manifest["query"]["loft"]["available"])
        self.assertTrue(manifest["query"]["bind_brep_feature"]["available"])
        for name in retired:
            with self.subTest(name=name):
                self.assertFalse(manifest["query"][name]["available"])
        self.assertEqual(manifest["policies"]["representationMasters"], ["brep"])
        self.assertNotIn("hybrid", {
            mode["id"] for mode in manifest["authoring"]["artifactModes"]
        })
        self.assertTrue({"STL", "display GLB"}.issubset({
            output
            for mode in manifest["authoring"]["artifactModes"]
            for output in mode["outputs"]
        }))

    def test_queries_resolve_public_authoring_and_export_helpers(self):
        names = ["write_intent", "write_scene", "export_part", "export_assembly", "retained_slider"]
        manifest = capability_manifest.build_manifest(names)
        for name in names:
            with self.subTest(name=name):
                item = manifest["query"][name]
                self.assertTrue(item["available"])
                self.assertIn("signature", item)
                self.assertTrue(item["description"])
                self.assertIn("*", item["signature"])
        self.assertEqual(manifest["query"]["write_intent"]["provider"], "authoring")
        self.assertIn("out_dir: str='.'", manifest["query"]["export_assembly"]["signature"])


if __name__ == "__main__":
    unittest.main()
