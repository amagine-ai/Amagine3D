from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "text-a3d"))

import cad_compile
from intent_contract import dimension_limits, validate
from intent_revision import audit_lineage, history_path, semantic_diff, validate_revision
from tests.python.intent_fixture import write_intent


class IntentRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path, self.original = write_intent(self.root, part="part", feature_owners={"part-body": "part"})

    def save(self, name, document):
        path = self.root / name
        path.write_text(json.dumps(document) + "\n")
        return path

    def register(self, path, document):
        history, audit = audit_lineage(self.root, path, document)
        self.save(history_path(self.root, document["part"]).name, history)
        return audit

    def revision(self, parent, document, kind="parameter-adjustment"):
        updated = deepcopy(document)
        updated["revision"] = {
            "parent": {"path": parent.name, "sha256": sha256(parent.read_bytes()).hexdigest()},
            "kind": kind, "reason": "Explicit design revision",
        }
        if kind == "target-change":
            updated["revision"]["evidence"] = {"kind": "user-request", "text": "Change the recorded target."}
        return updated

    def test_inferred_dimension_is_fixed_unless_range_declared(self):
        original = deepcopy(self.original)
        item = original["dimensions_mm"]["x"]
        item["value"], item["source"] = 40, "inferred"
        self.assertEqual(dimension_limits(original, "x"), (40.0, 40.0))
        self.assertEqual(dimension_limits(original, "x", tolerance_mm=0.5), (39.5, 40.5))
        item["constraint"] = {"kind": "range", "min_mm": 35, "max_mm": 45}
        self.assertEqual(validate(original, self.root), [])
        self.assertEqual(dimension_limits(original, "x"), (35.0, 45.0))
        self.assertEqual(dimension_limits(original, "x", tolerance_mm=0.5), (34.5, 45.5))
        item["value"] = 46
        self.assertTrue(any("within its declared range" in error for error in validate(original, self.root)))

    def test_new_filename_cannot_replace_recorded_baseline(self):
        self.register(self.path, self.original)
        changed = deepcopy(self.original)
        changed["features"][0]["acceptance"] = "Appearance only"
        path = self.save("unlinked.json", changed)
        with self.assertRaisesRegex(ValueError, "revision must be an object"):
            self.register(path, changed)

    def test_dimension_limits_rejects_malformed_constraints_with_value_error(self):
        for constraint in (None, [], {"kind": "range"}, {"kind": "range", "min_mm": 1, "max_mm": float("inf")}, {"kind": "range", "min_mm": True, "max_mm": 99}, {"kind": "fixed", "min_mm": 1}):
            with self.subTest(constraint=constraint):
                document = {"dimensions_mm": {"x": {"value": 40, "constraint": constraint}}}
                with self.assertRaises(ValueError):
                    dimension_limits(document, "x")

    def test_section_dimensions_validate_fixed_ranges_and_reject_unsupported_targets(self):
        original = deepcopy(self.original)
        self.assertEqual(validate(original, self.root), [])
        section = {"plane": {"axis": "z", "coordinate_mm": 10},
                   "outer_envelope": {"width_u_mm": {"value": 54}}}
        original["features"][0]["section_dimensions"] = [section]
        self.assertEqual(validate(original, self.root), [])
        section["outer_envelope"]["width_u_mm"] = {
            "value": 54, "constraint": {"kind": "range", "min_mm": 53.9, "max_mm": 54.1}}
        self.assertEqual(validate(original, self.root), [])
        for axis in "xyz":
            section["plane"] = {"axis": axis, "coordinate_mm": -2.5}
            self.assertEqual(validate(original, self.root), [])

        mutations = {
            "empty": lambda feature: feature.update(section_dimensions=[]),
            "invalid plane": lambda feature: feature["section_dimensions"][0]["plane"].update(axis=["z"]),
            "nonfinite plane": lambda feature: feature["section_dimensions"][0]["plane"].update(coordinate_mm=float("nan")),
            "unsupported hole": lambda feature: feature["section_dimensions"][0].update(hole_envelope={"diameter_mm": {"value": 4}}),
            "unknown outer metric": lambda feature: feature["section_dimensions"][0]["outer_envelope"].update(diameter_mm={"value": 54}),
            "authored tolerance": lambda feature: feature["section_dimensions"][0]["outer_envelope"]["width_u_mm"].update(tolerance_mm=2),
            "outside range": lambda feature: feature["section_dimensions"][0]["outer_envelope"]["width_u_mm"].update(value=55),
            "fixed with range": lambda feature: feature["section_dimensions"][0]["outer_envelope"]["width_u_mm"].update(constraint={"kind": "fixed", "min_mm": 1}),
            "invalid dimension": lambda feature: feature["section_dimensions"][0]["outer_envelope"]["width_u_mm"].update(value=True),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                invalid = deepcopy(original)
                mutate(invalid["features"][0])
                self.assertTrue(any("section_dimensions" in error for error in validate(invalid, self.root)))

        # Feature kind does not turn an explicit outer-envelope measurement into a hole diameter.
        hole = deepcopy(original)
        hole["features"][0].update(kind="hole", face="top", direction="+Z", edge_crossing="forbidden")
        self.assertEqual(validate(hole, self.root), [])
        hole["features"][0]["section_dimensions"][0]["hole_envelope"] = {"width_u_mm": {"value": 4}}
        self.assertTrue(any("section_dimensions" in error for error in validate(hole, self.root)))

    def test_allowed_parameter_revision_keeps_baseline_and_records_diff(self):
        original = deepcopy(self.original)
        original["dimensions_mm"]["x"] = {"value": 40, "source": "inferred", "confidence": "medium", "constraint": {"kind": "range", "min_mm": 35, "max_mm": 45}}
        self.path = self.save(self.path.name, original)
        baseline = self.register(self.path, original)
        updated = self.revision(self.path, original)
        updated["dimensions_mm"]["x"]["value"] = 44
        path = self.save("next.json", updated)
        audit = self.register(path, updated)
        self.assertEqual(audit["baselineHash"], baseline["intentHash"])
        self.assertFalse(audit["targetChanged"])
        self.assertEqual(audit["changes"][0]["classification"], "parameter-adjustment")

    def test_parameter_label_cannot_hide_fixed_dimension_profile_or_requirement_changes(self):
        changes = (
            lambda data: data["dimensions_mm"]["x"].update(value=99),
            lambda data: data["dimensions_mm"]["x"].update(constraint={"kind": "range", "min_mm": 1, "max_mm": 999}),
            lambda data: data["printability"]["profile"].update(path="different-printer.json"),
            lambda data: data["features"][0].update(acceptance="Optional feature"),
            lambda data: data["features"][0].update(section_dimensions=[{
                "plane": {"axis": "z", "coordinate_mm": 10},
                "outer_envelope": {"width_u_mm": {"value": 54}},
            }]),
            lambda data: data["features"].clear(),
        )
        for change in changes:
            with self.subTest(change=change):
                updated = self.revision(self.path, self.original)
                change(updated)
                self.assertTrue(any("changes a fixed target" in error for error in validate_revision(updated, self.root)))

    def test_interface_order_is_not_a_target_revision_but_loose_is(self):
        original = deepcopy(self.original)
        original["manufacturing"] = {"parts": [{"name": "body", "installation": "interface"}], "interfaces": [{"id": "join", "features": ["a", "b"]}]}
        updated = deepcopy(original)
        updated["manufacturing"]["interfaces"][0]["features"].reverse()
        self.assertEqual(semantic_diff(original, updated), [])
        updated["manufacturing"]["parts"][0]["installation"] = "loose"
        self.assertEqual(semantic_diff(original, updated)[0]["classification"], "target-change")

    def test_target_change_records_declared_evidence_without_claiming_authorization(self):
        self.register(self.path, self.original)
        updated = self.revision(self.path, self.original, "target-change")
        updated["dimensions_mm"]["x"]["value"] += 10
        path = self.save("next.json", updated)
        audit = self.register(path, updated)
        self.assertTrue(audit["targetChanged"])
        self.assertEqual(audit["evidenceStatus"], "author-declared")

    def test_generated_build_cannot_be_evidence_for_replacement_target(self):
        self.save("build.json", {"schema": "evidence-a3d-build/v1"})
        updated = self.revision(self.path, self.original, "evidence-correction")
        updated["revision"]["evidence"] = {"kind": "external-evidence", "text": "Measured output", "reference": "build.json"}
        self.assertTrue(any("cannot establish replacement" in error for error in validate_revision(updated, self.root)))

    def test_parent_content_and_parent_path_must_remain_bound(self):
        self.register(self.path, self.original)
        updated = self.revision(self.path, self.original)
        self.path.write_text(self.path.read_text() + " ")
        path = self.save("next.json", updated)
        with self.assertRaisesRegex(ValueError, "recorded immutable intent"):
            self.register(path, updated)

    def test_legacy_migration_uses_saved_hash_and_rejects_unverifiable_history(self):
        self.save("part_compile-result.json", {"inputs": {"intent": str(self.path)}})
        self.save("part_repair-state.json", {"intentHash": "0" * 64})
        with self.assertRaisesRegex(ValueError, "unverified"):
            self.register(self.path, self.original)
        self.save("part_repair-state.json", {"intentHash": sha256(self.path.read_bytes()).hexdigest()})
        self.assertTrue(self.register(self.path, self.original)["verified"])

    def test_cross_revision_repair_memory_marks_changed_scope_instead_of_resolution(self):
        source = self.root / "build.py"
        source.write_text("# fixture\n")
        baseline = self.register(self.path, self.original)
        result_path = self.root / "part_compile-result.json"
        problem = {"code": "QA.MESH_FAILED", "check": "dimension", "stage": "mesh-qa:part", "part": "part", "severity": "error"}
        def result(path, audit, issues, run):
            return {"inputs": {"intent": str(path), "source": str(source), "workspace": str(self.root)}, "model": "part", "intentRevision": audit, "issues": issues, "runId": run, "stages": []}
        first = result(self.path, baseline, [problem], "one")
        state_path = cad_compile._write_repair_state(first, result_path=result_path)
        issue_id = json.loads(state_path.read_text())["failed"][0]["id"]
        updated = self.revision(self.path, self.original, "target-change")
        updated["dimensions_mm"]["x"]["value"] += 10
        path = self.save("next.json", updated)
        audit = self.register(path, updated)
        second = result(path, audit, [], "two")
        cad_compile._write_repair_state(second, result_path=result_path)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["previousRunId"], "one")
        self.assertEqual(second["repairDelta"]["scope_changed"], [issue_id])
        self.assertEqual(second["repairDelta"]["resolved"], [])
        self.assertEqual(state["knownResolvedIssueIds"], [])

    def test_shared_failure_aggregates_impacts_and_exception_ids_ignore_traceback_noise(self):
        observed = {"offenders": [{"feature_id": "port", "expected_part": "shell", "observed_part": "insert", "source": "event:cut"}]}
        issues = [{"code": "QA.MESH_FAILED", "check": "intent_report_feature_ownership", "observed": observed, "part": part, "stage": f"mesh-qa:{part}", "severity": "error"} for part in ("shell", "insert", "plate")]
        groups = cad_compile._aggregate_issues(issues)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["affectedParts"], ["insert", "plate", "shell"])
        self.assertEqual(groups[0]["occurrenceCount"], 3)
        first = {"code": "SOURCE.EXECUTION_FAILED", "stage": "source", "message": '2026-09-01T09:00:00Z\n File "build.py", line 100\nTypeError: missing male endpoint'}
        second = {**first, "message": '2026-09-08T11:22:00Z\n File "build.py", line 140\nTypeError: missing male endpoint'}
        self.assertEqual(cad_compile._issue_identity(first), cad_compile._issue_identity(second))

    def test_early_failure_does_not_resolve_downstream_defects(self):
        source = self.root / "build.py"
        source.write_text("# fixture\n")
        audit = self.register(self.path, self.original)
        problem = {"code": "QA.MESH_FAILED", "check": "dimension", "stage": "mesh-qa:part", "part": "part", "severity": "error"}
        result = {"inputs": {"intent": str(self.path), "source": str(source)}, "intentRevision": audit, "model": "part", "issues": [problem], "runId": "one", "stages": []}
        path = self.root / "part_compile-result.json"
        state_path = cad_compile._write_repair_state(result, result_path=path)
        issue_id = json.loads(state_path.read_text())["failed"][0]["id"]
        result.update(issues=[], runId="two", stages=[{"name": "source", "status": "fail"}])
        cad_compile._write_repair_state(result, result_path=path)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["delta"]["resolved"], [])
        self.assertEqual(state["blocked"][0]["id"], issue_id)
        self.assertEqual(state["blocked"][0]["blockedBy"], "NOT_REEVALUATED")
        result.update(runId="three", stages=[{"name": "mesh-qa:part", "status": "pass"}])
        cad_compile._write_repair_state(result, result_path=path)
        self.assertEqual(result["repairDelta"]["resolved"], [issue_id])

    def test_cross_revision_regression_survives_parameter_adjustment_and_output_directory(self):
        original = deepcopy(self.original)
        item = original["dimensions_mm"]["x"]
        value = item["value"]
        item["constraint"] = {"kind": "range", "min_mm": value, "max_mm": value + 10}
        self.path = self.save(self.path.name, original)
        audit = self.register(self.path, original)
        source = self.root / "build.py"
        source.write_text("# fixture\n")
        problem = {"code": "SOURCE.CUT_MISSED_OWNER", "check": "cut", "stage": "source", "part": "part", "severity": "error"}
        result = {"inputs": {"intent": str(self.path), "source": str(source), "workspace": str(self.root)}, "intentRevision": audit, "model": "part", "issues": [problem], "runId": "one", "stages": [{"name": "source", "status": "pass"}]}
        path = self.root / "part_compile-result.json"
        state_path = cad_compile._write_repair_state(result, result_path=path)
        issue_id = json.loads(state_path.read_text())["failed"][0]["id"]
        result.update(issues=[], runId="two")
        cad_compile._write_repair_state(result, result_path=path)
        updated = self.revision(self.path, original)
        updated["dimensions_mm"]["x"]["value"] += 5
        next_path = self.save("next.json", updated)
        result["inputs"]["intent"] = str(next_path)
        result.update(intentRevision=self.register(next_path, updated), issues=[problem], runId="three")
        output = self.root / "another-output"
        output.mkdir()
        observed_path = cad_compile._write_repair_state(result, result_path=output / path.name)
        self.assertEqual(observed_path, state_path)
        self.assertEqual(result["repairDelta"]["regressed"], [issue_id])


if __name__ == "__main__":
    unittest.main()
