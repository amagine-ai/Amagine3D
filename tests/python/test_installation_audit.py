from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

from build123d import Align, Box, Pos, Rot, export_step

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "text-a3d"))
from authoring import write_scene
from geometry_binding import bind_brep_feature
from installation_check import bind_installation_check
from installation_contract import validate_installations
from installation_audit import audit
from intent_contract import validate as validate_intent
from scene_contract import validate as validate_scene
from tests.python.intent_fixture import write_intent


def box(w, d, h, z=0):
    return Pos(0, 0, z) * Box(w, d, h, align=(Align.CENTER, Align.CENTER, Align.MIN))


def bound(path):
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}


class InstallationAuditTests(unittest.TestCase):
    def fixture(self, root, body, requirements, **geometry):
        intent_path, intent = write_intent(root, part="holder", feature_owners={"mount": "holder"})
        intent["features"][0]["installation_checks"] = requirements
        intent_path.write_text(json.dumps(intent))
        record = bind_installation_check(feature_id="mount", out_dir=root / ".witnesses", **geometry)
        scene_path = root / "holder_scene.json"
        scene = write_scene(scene_path, intent_path=intent_path,
            parts={"holder": {"representationMaster": "brep", "nodes": [bind_brep_feature(
                node_id="holder-node", feature_id="mount", role="solid", shape=body, path=root / "feature.stl")]}},
            installation_checks=[record])
        step = root / "holder.step"
        export_step(body, step)
        report_path = root / "holder_report.json"
        report = {"parts": {"holder": {}}, "inputs": {"intent": bound(intent_path), "scene": bound(scene_path)},
                  "artifacts": {"step:holder": bound(step)}}
        report_path.write_text(json.dumps(report))
        return report_path, scene, intent

    def test_passage_reads_final_geometry_and_rejects_blind_or_unrelated_cuts(self):
        plate = box(24, 20, 6)
        aperture = box(10, 6, 8, -1)
        module = box(16, 12, 2, -2)
        for kind, body, passed in (
            ("through", plate - aperture, True),
            ("blind", plate - box(10, 6, 2, 5), False),
            ("unrelated", plate - Pos(9, 0, 0) * box(4, 6, 8, -1), False),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=ROOT / "workspace") as directory:
                path, _, _ = self.fixture(Path(directory), body, ["clearance", "passage"],
                    envelope=module, obstacle_parts=["holder"], passage_parts=["holder"],
                    passage_envelope=box(8, 4, 8, -1))
                result = audit(path)
                self.assertEqual(result["pass"], passed, result)
                if not passed:
                    self.assertTrue(any("passage" in str(error) for error in result["errors"]))

    def test_support_and_retention_work_in_different_assembly_frames(self):
        module = box(20, 30, 1.6, 2)
        floor = box(24, 34, 2)
        roof = box(24, 34, 2, 3.9)
        side = Pos(11, 0, 0) * box(2, 34, 2, 2)
        for rotation, axis in ((Rot(), (0, 0, 1)), (Rot(Y=90), (1, 0, 0))):
            for missing in (False, True):
                body = floor + side + (Pos(0, 0, 2) * roof if missing else roof)
                # Extend the side when moving the roof: this is still one manufactured solid.
                if missing:
                    body = body + Pos(11, 0, 0) * box(2, 34, 4, 2)
                with self.subTest(axis=axis, missing=missing), tempfile.TemporaryDirectory(dir=ROOT / "workspace") as directory:
                    path, _, _ = self.fixture(Path(directory), rotation * body,
                        ["clearance", "support", "retention"], envelope=rotation * module,
                        support_parts=["holder"], retainer_parts=["holder"], withdrawal_axis=axis,
                        free_travel_mm=0.29, stop_travel_mm=0.35)
                    result = audit(path)
                    self.assertEqual(result["pass"], not missing, result)

    def test_required_checks_cannot_be_omitted_and_hash_changes_fail(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as directory:
            root = Path(directory)
            path, scene, intent = self.fixture(root, box(24, 34, 2), ["clearance", "support"],
                envelope=box(20, 30, 1.6, 2), support_parts=["holder"])
            self.assertTrue(audit(path)["pass"])
            dropped = deepcopy(scene)
            dropped.pop("installationChecks")
            self.assertTrue(any("missing intent feature" in e for e in validate_scene(dropped, root)))
            dropped = deepcopy(scene)
            dropped["installationChecks"][0]["supportParts"] = []
            dropped["installationChecks"][0]["obstacleParts"] = ["holder"]
            self.assertTrue(any("missing required checks" in e for e in validate_scene(dropped, root)))
            witness = Path(scene["installationChecks"][0]["envelope"]["path"])
            witness.write_bytes(b"replaced")
            self.assertFalse(audit(path)["pass"])
            intent["features"][0]["installation_checks"] = ["seal-all-bottoms"]
            self.assertTrue(any("installation_checks" in e for e in validate_intent(intent, root)))

    def test_non_installation_tasks_do_not_gain_requirements(self):
        self.assertEqual(validate_installations([], {"features": []}, {"part"}), [])

    def test_clear_but_unrelated_passage_cannot_satisfy_component_access(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as directory:
            path, _, _ = self.fixture(Path(directory), box(24, 20, 6), ["clearance", "passage"],
                envelope=box(16, 12, 2, -2), obstacle_parts=["holder"], passage_parts=["holder"],
                passage_envelope=Pos(50, 0, 0) * box(8, 4, 8, -1))
            result = audit(path)
            self.assertFalse(result["pass"])
            self.assertIn("extend into its component", str(result["errors"]))

    def test_unknown_parts_and_malformed_axis_are_rejected_without_crashing(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as directory:
            root = Path(directory)
            _, scene, intent = self.fixture(root, box(24, 34, 2), ["clearance"],
                envelope=box(20, 30, 1.6, 2), obstacle_parts=["holder"])
            record = scene["installationChecks"][0]
            record["obstacleParts"] = ["fake-support"]
            record["withdrawalAxis"] = [False, 0, 1]
            errors = validate_installations([record], intent, {"holder"}, root)
            self.assertTrue(any("part IDs" in e for e in errors))
            self.assertTrue(any("withdrawalAxis" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
