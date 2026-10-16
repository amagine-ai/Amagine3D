from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import signal
import shutil
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "a3d-public"))
import agent_api
import agent_protocol as protocol


class AgentProtocolTests(unittest.TestCase):
    def test_next_request_keeps_the_resolved_issue_field_and_returns_every_item(self):
        document = {"issues": [{"id": "problem", "observed": [{"index": i, "evidence": "x" * 400} for i in range(45)]}]}
        ref = self.report(document)
        page = agent_api.dispatch(self.root, "inspect", {"report": ref["path"], "sha256": ref["sha256"], "issueId": "problem", "field": "/observed"})
        values = []
        while True:
            self.assertLessEqual(protocol.wire_size(page), protocol.MAX_WIRE_BYTES)
            values.extend(item["value"]["index"] for item in page["items"])
            request = page["page"]["nextRequest"]
            if not request:
                break
            self.assertEqual(request["field"], "/issues/0/observed")
            page = agent_api.dispatch(self.root, "inspect", request)
        self.assertEqual(values, list(range(45)))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def report(self, value):
        path = self.root / "report.json"
        path.write_text(json.dumps(value, ensure_ascii=False))
        return protocol.binding(path)

    def test_issue_pages_preserve_every_issue_and_report_bytes(self):
        document = {"runId": "run", "status": "failed", "issues": [
            {"id": str(i), "severity": "error" if i % 2 else "warning", "message": '"\\\n屏幕' * 100,
             "observed": {"value": i}, "expected": {"value": i + 1}} for i in range(50)]}
        ref = self.report(document)
        before = Path(ref["path"]).read_bytes()
        ids, cursor = [], None
        while True:
            page = protocol.summary(document, ref, cursor)
            self.assertLessEqual(protocol.wire_size(page), protocol.MAX_WIRE_BYTES)
            ids.extend(item["id"] for item in page["items"])
            self.assertEqual(page["page"]["total"], 50)
            cursor = page["page"]["next"]
            if not cursor:
                break
        self.assertEqual(set(ids), {str(i) for i in range(50)})
        self.assertEqual(len(ids), 50)
        self.assertEqual(Path(ref["path"]).read_bytes(), before)

    def test_known_placement_keeps_offenders_and_counts_normal_observations(self):
        observed = {"offenders": [{"feature_id": "socket", "actual": 2, "expected": 0}],
                    "observations": [{"id": i, "measurements": [1] * 100} for i in range(29)]}
        issue = {"id": "locator", "check": "semantic_feature_placement", "observed": observed}
        original = deepcopy(issue)
        result = protocol.issue_view(issue, 0)
        self.assertEqual(result["observed"]["offenders"], observed["offenders"])
        self.assertEqual(result["observed"]["observationCounts"], {"total": 29, "offenders": 1})
        self.assertEqual(result["observed"]["allObservations"]["field"], "/issues/0/observed/observations")
        self.assertEqual(issue, original)

    def test_large_string_is_reassemblable_and_cursor_is_bound(self):
        value = '"\n\\屏幕' * 5_000
        document = {"detail": value}
        ref = self.report(document)
        cursor, chunks = None, []
        while True:
            page = protocol.inspect(document, ref, field="/detail", cursor=cursor)
            self.assertLessEqual(protocol.wire_size(page), protocol.MAX_WIRE_BYTES)
            chunks.extend(page["items"])
            cursor = page["page"]["next"]
            if not cursor:
                break
        self.assertEqual("".join(item["textChunk"] for item in chunks), value)
        self.assertEqual([item["textOffset"] for item in chunks], list(range(0, len(value), 500)))
        first = protocol.inspect(document, ref, field="/detail")
        with self.assertRaisesRegex(ValueError, "cursor"):
            protocol.inspect(document, {**ref, "sha256": "b" * 64}, field="/detail", cursor=first["page"]["next"])
        Path(ref["path"]).write_text('{}')
        with self.assertRaisesRegex(ValueError, "changed"):
            protocol.read_report(self.root, ref["path"], ref["sha256"])

    def test_large_metadata_and_issue_fields_are_exact_references(self):
        document = {"runId": "run", "status": "failed", "inputBindings": {"source": "x" * 40_000},
                    "artifacts": {f"file{i}": {"path": "long" * 2_000, "sha256": "a" * 64} for i in range(50)},
                    "issues": [{"id": "large", "message": "large" * 20_000, "expected": [5] * 30_000}]}
        page = protocol.summary(document, self.report(document))
        self.assertLessEqual(protocol.wire_size(page), protocol.MAX_WIRE_BYTES)
        self.assertEqual(page["items"][0]["message"]["field"], "/issues/0/message")
        self.assertEqual(page["items"][0]["expected"]["field"], "/issues/0/expected")

    def test_pointer_and_scope_errors_do_not_guess_fields(self):
        self.assertEqual(protocol.at_pointer({"a/b": {"~": [2]}}, "/a~1b/~0/0"), 2)
        for pointer in ("inputs.source.sha256", "/parts", "/a~1b/~0/-1"):
            with self.assertRaises(ValueError):
                protocol.at_pointer({"a/b": {"~": [2]}}, pointer)
        with self.assertRaisesRegex(ValueError, "workspace"):
            protocol.scoped(self.root, "../outside.json", exists=False)
        link = self.root / "link"
        link.symlink_to(self.root.parent)
        with self.assertRaisesRegex(ValueError, "workspace"):
            protocol.scoped(self.root, "link/file.json", exists=False)

    def test_contract_field_and_shared_cause_measurements_keep_their_identity(self):
        import cad_compile
        issues = [{"causeId": "cause", "code": "QA.FAILED", "stage": stage, "part": part,
                   "observed": {"value": value, "coordinateFrame": frame}, "expected": {"value": 0}}
                  for stage, part, value, frame in (("step-qa", "body", 4, "semantic"), ("mesh-qa", "plate", 7, "print-pose"))]
        grouped = cad_compile._aggregate_issues(issues)
        self.assertEqual(len(grouped), 1)
        self.assertEqual([item["observed"]["value"] for item in grouped[0]["impacts"]], [4, 7])
        item = protocol.issue_view({"field": "interfaces[0].kind", "expected": ["pin-socket"]}, 0)
        self.assertEqual(item["field"], "interfaces[0].kind")
        self.assertEqual(item["evidenceField"], "/issues/0")

    def test_derived_view_references_always_resolve_into_saved_evidence(self):
        document = {"issues": [{"check": "printability_local_thin_region", "observed": {
            "sampling": {"minimum_sample": {"point_mm": [1, 2, 3], "large_detail": "a" * 3_000}},
            "affected_feature_attribution": {"basis": "b" * 3_000},
            "measurement_context": {"coordinate_frame": {"name": "part-print"}, "other": "c" * 4_000}}}]}
        view = protocol.issue_view(document["issues"][0], 0)
        def verify(value):
            if isinstance(value, dict):
                if "field" in value and value.get("type") in {"str", "dict", "list"}:
                    protocol.at_pointer(document, value["field"])
                for item in value.values(): verify(item)
            elif isinstance(value, list):
                for item in value: verify(item)
        verify(view)
        empty = protocol.summary({"issues": []}, self.report({"issues": []}))
        self.assertNotIn("allArtifacts", empty)


class AgentOperationsTests(unittest.TestCase):
    def test_manufacturing_pass_survives_render_failure_and_preview_retry_reuses_checks(self):
        import cad_compile
        runtime = ROOT / "skills" / "a3d-public"
        examples = ROOT / "skills" / "a3d-text" / "examples"
        for name in ("simple_brep_build.py", "simple_brep_intent.py"):
            shutil.copyfile(examples / name, self.root / name)
        source = self.root / "simple_brep_build.py"
        source.write_text("from pathlib import Path\nPath('source-count').write_text('once')\n" + source.read_text())
        env = {**os.environ, "AMAGINE3D_RUNTIME_DIR": str(runtime), "PYTHONPATH": str(runtime)}
        setup = subprocess.run(["node", str(ROOT / "bin/a3d.mjs"), "profile", "--machine", "a1-mini", "--nozzle", "0.4", "--tool", "0", "--out", "simple_brep_printer-profile.json"], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
        setup = subprocess.run([sys.executable, "simple_brep_intent.py"], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
        original_run = cad_compile.CommandRunner.run
        def run(runner, stage, argv, **kwargs):
            if stage == "render": return cad_compile.CommandResult(1, 0, "injected PNG failure")
            return original_run(runner, stage, argv, **kwargs)
        with mock.patch.object(cad_compile.CommandRunner, "run", run):
            first = agent_api.dispatch(self.root, "build", {"phase": "compile", "source": source.name,
                "intent": "simple_brep_intent.json", "scene": "simple_brep_scene.json"})
        ref, parent = protocol.read_report(self.root, first["report"]["path"], first["report"]["sha256"])
        self.assertEqual(parent["status"], "awaiting-preview", parent["issues"])
        self.assertFalse(parent["pass"])
        self.assertEqual(parent["artifactStages"]["manufacturing"]["status"], "passed")
        recovery = agent_api.context(self.root, {})
        self.assertEqual(recovery["suggestedPhase"], "display-retry")
        display_request = {"report": ref["path"], "sha256": ref["sha256"]}
        self.assertEqual(recovery["nextDisplay"], display_request)
        protected = {path: path.read_bytes() for path in self.root.rglob("*.step")}
        protected[source] = source.read_bytes()
        intent = self.root / "simple_brep_intent.json"
        protected[intent] = intent.read_bytes()
        with mock.patch.object(cad_compile.CommandRunner, "run", side_effect=AssertionError("QA and source must not rerun")):
            result = agent_api.dispatch(self.root, "display", display_request)
        _, retried = protocol.read_report(self.root, result["report"]["path"], result["report"]["sha256"])
        self.assertEqual(retried["status"], "awaiting-visual-review", retried["issues"])
        self.assertTrue(retried["pass"])
        self.assertEqual(retried["artifactStages"]["manufacturing"]["status"], "passed")
        self.assertTrue(retried["manufacturingEvidence"]["reused"])
        self.assertEqual(retried["manufacturingEvidence"]["parentReport"], ref)
        self.assertEqual([i for i in retried["issues"] if i.get("severity") == "warning"], [i for i in parent["issues"] if i.get("severity") == "warning"])
        self.assertEqual(protected, {path: path.read_bytes() for path in protected})
        self.assertEqual((self.root / "source-count").read_text(), "once")
        cached = agent_api.dispatch(self.root, "display", display_request)
        self.assertTrue(cached["reused"])
        self.assertFalse(list(self.root.rglob("*.glb")))
        # A changed print export invalidates the reuse proof, including cache hits.
        _, build = protocol.read_report(self.root, parent["artifacts"]["buildReport"]["path"], parent["artifacts"]["buildReport"]["sha256"])
        stl = Path(next(value["path"] for key, value in build["artifacts"].items() if key.startswith("stl:")))
        stl.write_bytes(stl.read_bytes() + b"changed")
        rejected = agent_api.dispatch(self.root, "display", display_request)
        self.assertFalse(rejected.get("pass", False))
        self.assertFalse(rejected.get("reused", False))

    def test_another_designs_draft_and_failure_cannot_route_current_recovery(self):
        from tests.python.test_design_language import box_design
        old, new = self.root / "old.a3d.json", self.root / "new.a3d.json"
        old.write_text(json.dumps(box_design()))
        new.write_text(json.dumps(box_design()))
        report = agent_api.save(self.root, "old-result.json", {"schema": "a3d-draft-result/v2", "runId": "old-run",
            "status": "draft", "issues": [], "taskInputBindings": {"design": protocol.binding(old)}})
        latest = {"operation": "draft", "runId": "old-run", "report": protocol.binding(report), "inputs": {"design": protocol.binding(old)}}
        agent_api.save(self.root, ".a3d-agent/state.json", {"schema": protocol.SCHEMA, "latest": latest})
        agent_api.remember_failure(self.root, "validate", {"design": old.name}, {"report": protocol.binding(report)})
        agent_api.dispatch(self.root, "validate", {"design": new.name})
        result = agent_api.context(self.root, {})
        self.assertEqual(result["design"]["design"], str(new))
        self.assertFalse(result["latest"]["inputsCurrent"])
        self.assertFalse(result["historicalFailures"][0]["inputsCurrent"])
        self.assertNotIn("nextInspect", result)
        self.assertEqual(result["suggestedPhase"], "functional-draft")

    def test_checkpoint_is_bound_to_current_inputs_and_never_changes_acceptance(self):
        source = self.root / "model.py"
        source.write_text("# current body")
        inputs = {"source": protocol.binding(source)}
        report = agent_api.save(self.root, "draft-result.json", {"schema": "a3d-draft-result/v2", "runId": "draft-run",
            "status": "draft", "issues": [], "taskInputBindings": inputs})
        agent_api.save(self.root, ".a3d-agent/state.json", {"schema": protocol.SCHEMA,
            "latest": {"operation": "draft", "report": protocol.binding(report), "inputs": inputs},
            "activeInput": {"kind": "source", "path": str(source)}})
        result = agent_api.dispatch(self.root, "checkpoint", {"report": str(report), "sha256": protocol.binding(report)["sha256"],
            "observations": ["module opening visible", "中" * 250], "todo": ["check insertion path"], "functionalReady": True})
        self.assertEqual(result["status"], "saved")
        restored = agent_api.context(self.root, {})
        self.assertEqual(restored["suggestedPhase"], "contract")
        self.assertFalse(restored["latest"]["pass"])
        self.assertTrue(restored["latest"]["visualReviewRequired"])
        source.write_text("# changed body")
        self.assertFalse(agent_api.context(self.root, {})["checkpoint"]["inputsCurrent"])

    def test_display_retry_never_executes_source_or_changes_saved_step(self):
        source = self.root / "model.py"
        source.write_text("""from pathlib import Path
Path('source-count').write_text('ran once')
from build123d import Box
import display_step
def fail(*args, **kwargs):
    raise display_step.DisplayStepError('injected display failure')
display_step.export_display_step = fail
from cad_draft import export_draft
export_draft({'body': Box(10, 10, 10)})
""")
        command = self.cli("build", {"phase": "draft", "source": source.name})
        first = json.loads(command.stdout)
        self.assertEqual(command.returncode, 1, command.stdout + command.stderr)
        ref, full = protocol.read_report(self.root, first["report"]["path"], first["report"]["sha256"])
        step = Path(full["artifacts"]["step"]["path"])
        step_bytes, source_bytes = step.read_bytes(), source.read_bytes()
        command = self.cli("display", {"report": ref["path"], "sha256": ref["sha256"]})
        result = json.loads(command.stdout)
        self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
        self.assertEqual(result["status"], "draft")
        self.assertNotIn("pass", result)
        self.assertEqual(step.read_bytes(), step_bytes)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual((self.root / "source-count").read_text(), "ran once")
        _, full = protocol.read_report(self.root, result["report"]["path"], result["report"]["sha256"])
        self.assertEqual(full["artifactStages"]["display"]["status"], "passed")
        self.assertEqual(full["artifactStages"]["manufacturing"]["status"], "pending")
        self.assertNotIn("lastFailure", agent_api.state(self.root))
        source.write_text(source_bytes.decode() + "\n# new controls")
        rejected = json.loads(self.cli("display", {"report": ref["path"], "sha256": ref["sha256"]}).stdout)
        self.assertEqual(rejected["status"], "api-error")

    def test_validate_and_display_success_preserve_current_manufacturing_failure(self):
        source = self.root / "model.py"
        source.write_text("# unchanged current geometry")
        inputs = {"source": protocol.binding(source)}
        failed = agent_api.save(self.root, "failed.json", {"schema": "evidence-cad-compile-result/v2", "runId": "failed-run",
            "status": "failed", "pass": False, "taskInputBindings": inputs,
            "artifactStages": {"geometry": {"status": "passed"}, "cadExport": {"status": "passed"},
                "display": {"status": "passed"}, "manufacturing": {"status": "failed"}},
            "issues": [{"code": "INSTALLATION.BLOCKED", "severity": "error", "stage": "installation"}]})
        failure_ref = protocol.binding(failed)
        agent_api.remember_failure(self.root, "build", {"phase": "compile", "source": source.name}, {"report": failure_ref})
        validation = agent_api.save(self.root, "valid.json", {"schema": "a3d-validation/v1", "runId": "validation-run",
            "status": "valid", "pass": True, "inputBindings": inputs, "issues": []})
        with mock.patch.dict(agent_api.OPERATIONS, {"validate": lambda root, args: {"status": "valid", "report": protocol.binding(validation)}}):
            agent_api.dispatch(self.root, "validate", {"source": source.name})
        self.assertEqual(agent_api.state(self.root)["lastFailure"]["report"], failure_ref)
        display = agent_api.save(self.root, "display.json", {"schema": "a3d-draft-result/v2", "runId": "display-run",
            "status": "draft", "taskInputBindings": inputs, "parentReport": failure_ref,
            "artifactStages": {"display": {"status": "passed"}, "manufacturing": {"status": "pending"}}, "issues": []})
        with mock.patch.dict(agent_api.OPERATIONS, {"display": lambda root, args: {"status": "draft", "report": protocol.binding(display)}}):
            agent_api.dispatch(self.root, "display", {"report": failure_ref["path"], "sha256": failure_ref["sha256"]})
        self.assertEqual(agent_api.context(self.root, {})["nextInspect"], {"report": failure_ref["path"], "sha256": failure_ref["sha256"]})
        passed = agent_api.save(self.root, "passed.json", {"schema": "evidence-cad-compile-result/v2", "runId": "passed-run",
            "status": "awaiting-visual-review", "pass": True, "taskInputBindings": inputs, "issues": []})
        with mock.patch.dict(agent_api.OPERATIONS, {"build": lambda root, args: {"status": "awaiting-visual-review", "report": protocol.binding(passed)}}):
            agent_api.dispatch(self.root, "build", {"phase": "compile", "source": source.name, "intent": "intent.json", "scene": "scene.json"})
        self.assertNotIn("lastFailure", agent_api.state(self.root))
        self.assertEqual(agent_api.state(self.root)["failureHistory"][0]["report"], failure_ref)

    def test_new_intent_for_same_design_makes_old_acceptance_historical_and_supplies_compile(self):
        from tests.python.test_design_language import box_design
        design = self.root / "model.a3d.json"
        design.write_text(json.dumps(box_design()))
        old_intent, new_intent = self.root / "intent-v1.json", self.root / "intent-v2.json"
        old_intent.write_text(json.dumps({"part": "body", "assumptions": ["old"]}))
        new_intent.write_text(json.dumps({"part": "body", "assumptions": ["new"]}))
        inputs = {"design": protocol.binding(design), "intent": protocol.binding(old_intent)}
        old_report = agent_api.save(self.root, "accepted.json", {"schema": "evidence-cad-compile-result/v2", "runId": "old-run",
            "status": "awaiting-visual-review", "pass": True, "taskInputBindings": inputs, "issues": []})
        agent_api.save(self.root, ".a3d-agent/state.json", {"schema": protocol.SCHEMA, "latest": {
            "operation": "compile", "report": protocol.binding(old_report), "inputs": inputs},
            "activeInput": {"kind": "design", "path": str(design)}, "activeIntent": protocol.binding(old_intent)})
        self.assertTrue(agent_api.context(self.root, {})["latest"]["inputsCurrent"])
        current = agent_api.state(self.root)
        new_inputs = {"design": protocol.binding(design), "intent": protocol.binding(new_intent)}
        agent_api._activate_inputs(self.root, current, new_inputs)
        validation = agent_api.save(self.root, "validation.json", {"schema": "a3d-validation/v1", "runId": "validation-run",
            "status": "valid", "pass": True, "inputBindings": new_inputs, "issues": []})
        current["validation"] = {"report": protocol.binding(validation), "capabilityVersion": agent_api.capability_version()}
        agent_api.save(self.root, ".a3d-agent/state.json", current)
        context = agent_api.context(self.root, {})
        self.assertFalse(context["latest"]["inputsCurrent"])
        self.assertEqual(context["nextBuild"], {"phase": "compile", "design": str(design),
            "revision": new_inputs["design"]["sha256"], "intent": str(new_intent)})
        self.assertEqual(context["suggestedPhase"], "compile-current-inputs")
        self.assertNotIn("nextInspect", context)
        self.assertTrue(agent_api._active_matches(self.root, current, {"design": protocol.binding(design)}))
        agent_api._activate_inputs(self.root, current, {"source": {"path": str(self.root / "other.py"), "sha256": "a" * 64}})
        self.assertNotIn("activeIntent", current)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def cli(self, operation, arguments=None, *extra):
        return subprocess.run(["node", str(ROOT / "bin" / "a3d.mjs"), "agent", operation,
                               *([json.dumps(arguments)] if arguments is not None else []), *extra],
                              cwd=self.root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                              capture_output=True, text=True, timeout=60)

    def test_cli_context_and_argument_file(self):
        result = self.cli("context")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        document = json.loads(result.stdout)
        self.assertEqual(document["workspace"], str(self.root))
        self.assertIsNone(document["latest"])
        self.assertIn("unknown", document["visualReview"])
        self.assertEqual(result.stderr, "")
        (self.root / "args.json").write_text('{"source":"bad.py"}')
        (self.root / "bad.py").write_text('def broken(:\n')
        failed = self.cli("validate", None, "--args-file", "args.json")
        self.assertEqual(failed.returncode, 1, failed.stderr + failed.stdout)
        self.assertFalse(json.loads(failed.stdout)["pass"])

    def test_capabilities_preserve_bind_semantics_and_session_knowledge(self):
        page = agent_api.dispatch(self.root, "capabilities", {"symbols": ["SelfTappingScrewPair.bind"]})
        helper = page["items"][0]["value"]
        self.assertIn("boss_mode", helper["signature"])
        self.assertIn("observe", helper["description"])
        self.assertIn("add", helper["description"])
        self.assertEqual(helper["parameterChoices"]["boss_mode"], ["add", "observe"])
        restored = agent_api.context(self.root, {})
        self.assertEqual(restored["previouslyProvidedSymbols"], ["SelfTappingScrewPair.bind"])
        self.assertEqual(restored["capabilityVersion"], page["capabilityVersion"])

    def test_cli_queries_all_design_operations_and_pages_without_losing_contracts(self):
        from design_language import OPS
        names = list(OPS)
        self.assertGreater(len(names), 20)
        result = self.cli("capabilities", {"designOps": names})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        page = json.loads(result.stdout)
        self.assertEqual(page["operationNames"], names)
        self.assertIn("selector", page["fieldTypes"])
        self.assertIn("axis", page["fieldTypes"])
        self.assertIsNotNone(page["page"]["nextRequest"])
        path = Path(page["report"]["path"])
        original = path.read_bytes()
        contracts = {}
        while True:
            self.assertLessEqual(protocol.wire_size(page), protocol.MAX_WIRE_BYTES)
            for item in page["items"]:
                name = item["field"].rsplit("/", 1)[1]
                self.assertNotIn(name, contracts)
                contracts[name] = item["value"]
            request = page["page"]["nextRequest"]
            if not request:
                break
            page = agent_api.dispatch(self.root, "inspect", request)
        self.assertEqual(contracts, json.loads(original)["operations"])
        self.assertEqual(list(contracts), names)
        self.assertEqual(agent_api.context(self.root, {})["previouslyProvidedDesignOps"], sorted(names))
        self.assertEqual(path.read_bytes(), original)

    def test_symbol_queries_accept_more_than_twenty_names(self):
        names = [f"unknown_symbol_{i}" for i in range(25)]
        page = agent_api.dispatch(self.root, "capabilities", {"symbols": names})
        returned = []
        while True:
            self.assertLessEqual(protocol.wire_size(page), protocol.MAX_WIRE_BYTES)
            returned.extend(item["field"].rsplit("/", 1)[1] for item in page["items"])
            request = page["page"]["nextRequest"]
            if not request:
                break
            page = agent_api.dispatch(self.root, "inspect", request)
        self.assertEqual(returned, sorted(names))
        self.assertEqual(agent_api.context(self.root, {})["previouslyProvidedSymbols"], [])

    def test_capability_requests_keep_name_and_byte_limits_without_affecting_other_lists(self):
        for field in ("symbols", "designOps"):
            for invalid in ("box", [None], [""], ["  "], ["x" * 201]):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(agent_api.RequestError) as failure:
                        agent_api.check_arguments("capabilities", {field: invalid})
                    self.assertEqual(failure.exception.details["field"], field)
                    self.assertNotIn("maximumItems", failure.exception.details)
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                agent_api.check_arguments("capabilities", {field: ["box"] * 11_000})
        for operation, args in (
            ("build", {"phase": "draft", "source": "model.py", "dependencies": ["dep.py"] * 21}),
            ("design", {"action": "read", "design": "model.a3d.json", "ids": ["body"] * 21}),
        ):
            with self.assertRaises(agent_api.RequestError) as failure:
                agent_api.check_arguments(operation, args)
            self.assertEqual(failure.exception.details["maximumItems"], 20)

    def test_large_capability_metadata_uses_exact_inspectable_references(self):
        from design_language import capabilities
        payload = capabilities(["box"])
        large = '\"\\\n轴' * 2_000
        payload["fieldTypes"] = {"axis": large}
        payload["operationNames"] = [f"operation_{i}" for i in range(500)]
        with mock.patch("design_language.capabilities", return_value=payload):
            page = agent_api.dispatch(self.root, "capabilities", {"designOps": ["box"]})
        self.assertLessEqual(protocol.wire_size(page), protocol.MAX_WIRE_BYTES)
        self.assertEqual(page["fieldTypes"]["axis"]["field"], "/fieldTypes/axis")
        self.assertEqual(page["operationNames"]["field"], "/operationNames")
        report = page["report"]
        _, document = protocol.read_report(self.root, report["path"], report["sha256"])
        self.assertEqual(protocol.at_pointer(document, page["operationNames"]["field"]), payload["operationNames"])
        request = {"report": report["path"], "sha256": report["sha256"], "field": "/fieldTypes/axis"}
        chunks = []
        while request:
            detail = agent_api.dispatch(self.root, "inspect", request)
            self.assertLessEqual(protocol.wire_size(detail), protocol.MAX_WIRE_BYTES)
            chunks.extend(item["textChunk"] for item in detail["items"])
            request = detail["page"]["nextRequest"]
        self.assertEqual("".join(chunks), large)

    def test_malformed_request_file_returns_json_location(self):
        (self.root / "request.json").write_text('{"changes": [\n }')
        result = self.cli("design", None, "--args-file", "request.json")
        self.assertEqual(result.returncode, 2)
        error = json.loads(result.stdout)["error"]
        self.assertEqual(error["type"], "JSONDecodeError")
        self.assertEqual(error["requestFile"], "request.json")
        self.assertEqual(error["line"], 2)
        self.assertGreater(error["offset"], 0)

    def test_legacy_result_and_stale_result_are_explicit(self):
        (self.root / "model_compile-result.json").write_text(json.dumps({
            "schema": "evidence-cad-compile-result/v2", "runId": "old", "status": "failed", "issues": []}))
        context = agent_api.context(self.root, {})
        self.assertTrue(context["latest"]["legacy"])
        self.assertFalse(context["latest"]["inputsCurrent"])
        agent_api.save(self.root, ".a3d-agent/state.json", {"schema": protocol.SCHEMA, "latest": context["latest"]})
        (self.root / "model_compile-result.json").write_text('{}')
        self.assertTrue(agent_api.context(self.root, {})["latest"]["stale"])

    def test_draft_reuse_requires_every_binding_and_does_not_block_failures(self):
        import cad_draft
        source = self.root / "model.py"
        source.write_text("# source")
        artifact = self.root / "preview.png"
        artifact.write_bytes(b"preview")
        calls = []
        def run(*args, **kwargs):
            calls.append(1)
            return {"schema": "a3d-draft-result/v2", "runId": str(len(calls)), "status": "draft", "source": protocol.binding(source),
                    "artifacts": {"preview": protocol.binding(artifact)}, "issues": []}
        args = {"phase": "draft", "source": source.name}
        with mock.patch.object(cad_draft, "run_draft", side_effect=run):
            first = agent_api.dispatch(self.root, "build", args)
            self.assertFalse(first["reused"])
            self.assertTrue(agent_api.dispatch(self.root, "build", args)["reused"])
            source.write_text("# changed")
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
            artifact.write_bytes(b"changed preview")
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
            with mock.patch.object(cad_draft, "run_draft", return_value={"runId": "failed", "status": "failed", "issues": []}):
                source.write_text("# failed")
                self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
                self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
        self.assertEqual(len(calls), 3)

    def test_source_dependencies_track_imported_package_children_and_relative_imports(self):
        source = self.root / "model.py"
        package = self.root / "dimensions"
        package.mkdir()
        (package / "__init__.py").write_text("")
        child = package / "controls.py"
        child.write_text("WIDTH = 10\n")
        source.write_text("from dimensions import controls\n")
        found = agent_api.source_dependencies(self.root, source, [])
        self.assertIn("dependency:dimensions/__init__.py", found)
        self.assertIn("dependency:dimensions/controls.py", found)
        source.write_text("from .dimensions import controls\n")
        (self.root / "__init__.py").write_text("")
        found = agent_api.source_dependencies(self.root, source, [])
        self.assertIn("dependency:dimensions/controls.py", found)

        namespace = self.root / "pkg" / "sub"
        namespace.mkdir(parents=True)
        (namespace.parent / "__init__.py").write_text("")
        (namespace / "controls.py").write_text("WIDTH = 12\n")
        nested_source = namespace / "model.py"
        nested_source.write_text("from . import controls\n")
        found = agent_api.source_dependencies(self.root, nested_source, [])
        self.assertIn("dependency:pkg/__init__.py", found)
        self.assertIn("dependency:pkg/sub/controls.py", found)

        # Namespace packages are valid local imports even without __init__.py.
        (package / "__init__.py").unlink()
        source.write_text("from dimensions import controls\n")
        found = agent_api.source_dependencies(self.root, source, [])
        self.assertIn("dependency:dimensions/controls.py", found)

    def test_declared_import_dependency_is_recursively_scanned(self):
        source = self.root / "model.py"
        source.write_text("import controls\n")
        controls = self.root / "controls.py"
        controls.write_text("import dimensions.controls\n")
        package = self.root / "dimensions"
        package.mkdir()
        (package / "controls.py").write_text("WIDTH = 10\n")
        found = agent_api.source_dependencies(self.root, source, ["controls.py"])
        self.assertIn("dependency:controls.py", found)
        self.assertIn("dependency:dimensions/controls.py", found)

    def test_compile_cache_binds_rich_display_scene_and_object_files(self):
        import cad_compile
        source, intent, semantic_scene = [self.root / name for name in ("build.py", "intent.json", "scene.json")]
        output, display_scene, object_step = [self.root / name for name in ("final.step", "display.scene.json", "object.step")]
        build_report_path = self.root / "build-evidence.json"
        source.write_text("# source")
        intent.write_text(json.dumps({"part": "model"}))
        calls = []

        def compile(_options):
            from intent_revision import audit_lineage, history_path
            calls.append(1)
            history, _ = audit_lineage(self.root, intent, json.loads(intent.read_text()))
            agent_api.save(self.root, history_path(self.root, "model").name, history)
            semantic_scene.write_text("{}")
            output.write_text("STEP")
            object_step.write_text("OBJECT STEP")
            descriptor = {"schema": "a3d-step-display/v1", "step": protocol.binding(output),
                          "objects": [{"step": protocol.binding(object_step)}]}
            display_scene.write_text(json.dumps(descriptor))
            rich_step = {**protocol.binding(output), "scene": protocol.binding(display_scene)}
            agent_api.save(self.root, "build-evidence.json",
                {"schema": "evidence-a3d-build/v2", "artifacts": {"step": rich_step}})
            value = {"schema": cad_compile.RESULT_SCHEMA, "runId": str(len(calls)),
                     "status": "awaiting-visual-review", "pass": True, "visualReviewRequired": True,
                     "inputBindings": {"source": protocol.binding(source)}, "issues": [],
                     "artifacts": {"buildReport": protocol.binding(build_report_path)}, "deliverables": {"step": str(output)}}
            path = agent_api.save(self.root, "model_compile-result.json", value)
            return {"runId": value["runId"], "result": {"path": str(path)}}

        args = {"phase": "compile", "source": source.name, "intent": intent.name, "scene": semantic_scene.name}
        with mock.patch.object(cad_compile, "compile_cad", side_effect=compile):
            first = agent_api.dispatch(self.root, "build", args)
            self.assertFalse(first["reused"])
            outputs = agent_api.state(self.root)["latest"]["outputs"]
            self.assertEqual(outputs["step"]["scene"], protocol.binding(display_scene))
            self.assertEqual(outputs["buildReport"], protocol.binding(build_report_path))
            self.assertTrue(agent_api.dispatch(self.root, "build", args)["reused"])
            self.assertEqual(len(calls), 1)
            build_report_path.write_text(build_report_path.read_text() + " ")
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
            object_step.write_text("changed object")
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
            object_step.unlink()
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
            display_scene.write_text(display_scene.read_text() + " ")
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
            display_scene.unlink()
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
        self.assertEqual(len(calls), 6)

    def test_compile_uses_full_saved_result_and_checks_profile_scene_and_outputs(self):
        import cad_compile
        source, intent, profile, scene, output = [self.root / name for name in
                                                 ("build.py", "intent.json", "profile.json", "scene.json", "final.step")]
        source.write_text("# source")
        profile.write_text('{}')
        intent.write_text(json.dumps({"part": "model", "printability": {"profile": {"path": str(profile)}}}))
        calls = []
        preview = self.root / "preview.png"
        def compile(options):
            from intent_revision import audit_lineage, history_path
            calls.append(1)
            history, _ = audit_lineage(self.root, intent, json.loads(intent.read_text()))
            agent_api.save(self.root, history_path(self.root, "model").name, history)
            scene.write_text(json.dumps({"run": len(calls)}))
            output.write_text("STEP")
            preview.write_bytes(b"preview")
            value = {"schema": cad_compile.RESULT_SCHEMA, "runId": str(len(calls)),
                     "status": "awaiting-visual-review", "pass": True, "visualReviewRequired": True,
                     "inputBindings": {"source": protocol.binding(source)}, "issues": [],
                     "artifacts": {"preview": protocol.binding(preview)}, "deliverables": {"step": str(output)}}
            path = agent_api.save(self.root, "model_compile-result.json", value)
            return {"runId": value["runId"], "result": {"path": str(path)}}
        args = {"phase": "compile", "source": source.name, "intent": intent.name, "scene": scene.name}
        with mock.patch.object(cad_compile, "compile_cad", side_effect=compile):
            first = agent_api.dispatch(self.root, "build", args)
            self.assertIn("inputBindings", first)
            self.assertTrue(agent_api.dispatch(self.root, "build", args)["reused"])
            for path in (profile, scene, source):
                path.write_text(path.read_text() + "\n")
                self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
            output.unlink()
            self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
        self.assertEqual(len(calls), 5)

    def test_invalid_arguments_and_concurrent_writes_fail_clearly(self):
        for operation, args in (("build", {"phase": "final", "source": "x"}),
                                ("build", {"phase": "draft", "source": "x", "timeoutSeconds": True}),
                                ("context", {"workspace": ".."}), ("capabilities", {"symbols": [None]})):
            with self.assertRaises(ValueError):
                agent_api.dispatch(self.root, operation, args)
        with agent_api.mutation_lock(self.root):
            with self.assertRaisesRegex(ValueError, "running"):
                agent_api.dispatch(self.root, "capabilities", {})
            self.assertIsNone(agent_api.context(self.root, {})["latest"])
        metrics = [json.loads(line) for line in (self.root / ".a3d-agent/metrics.jsonl").read_text().splitlines()]
        self.assertEqual(len(metrics), 5)
        self.assertTrue(all(item["status"] == "api-error" for item in metrics))
        self.assertTrue(all(item["wireBytes"] is None for item in metrics))
        self.assertTrue(all("arguments" not in item for item in metrics))

    def test_compile_missing_scene_returns_actionable_fields_without_running_source(self):
        result = self.cli("build", {"phase": "compile", "source": "unwritten.py", "intent": "unwritten.json"})
        self.assertEqual(result.returncode, 2)
        error = json.loads(result.stdout)["error"]
        self.assertEqual(error["requiredFields"], ["intent", "phase", "scene", "source"])
        self.assertFalse((self.root / ".a3d-agent/runs").exists())

    def test_local_parameter_import_and_declared_data_invalidate_reuse(self):
        import cad_draft
        source = self.root / "model.py"
        source.write_text("from controls import SIZE\n")
        controls = self.root / "controls.py"
        controls.write_text("SIZE = 10\n")
        data = self.root / "dimensions.json"
        data.write_text('{"width":10}')
        artifact = self.root / "preview.png"
        artifact.write_bytes(b"preview")
        args = {"phase": "draft", "source": source.name, "dependencies": [data.name]}
        def run(*unused, **kwargs):
            return {"schema": "a3d-draft-result/v2", "runId": str(time.monotonic_ns()), "status": "draft", "issues": [],
                    "artifacts": {"preview": protocol.binding(artifact)}}
        with mock.patch.object(cad_draft, "run_draft", side_effect=run):
            agent_api.dispatch(self.root, "build", args)
            self.assertTrue(agent_api.dispatch(self.root, "build", args)["reused"])
            for path in (controls, data):
                path.write_text(path.read_text() + "\n")
                self.assertFalse(agent_api.dispatch(self.root, "build", args)["reused"])
                self.assertTrue(agent_api.dispatch(self.root, "build", args)["reused"])

    def test_real_draft_returns_one_bounded_result_and_reuses_bound_preview(self):
        (self.root / "model.py").write_text('from build123d import Box\nfrom build_session import BuildSession\n'
                                             'build = BuildSession(__file__, part_names=("body",))\n'
                                             'build.add("body", Box(10, 10, 10))\nbuild.export()\n')
        first = self.cli("build", {"phase": "draft", "source": "model.py"})
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        result = json.loads(first.stdout)
        self.assertEqual(result["status"], "draft")
        self.assertFalse(result["reused"])
        self.assertEqual(first.stderr, "")
        self.assertLessEqual(protocol.wire_size(result), protocol.MAX_WIRE_BYTES)
        self.assertTrue(Path(result["artifacts"]["preview"]["path"]).is_file())
        second = json.loads(self.cli("build", {"phase": "draft", "source": "model.py"}).stdout)
        self.assertTrue(second["reused"])
        self.assertEqual(second["runId"], result["runId"])
        step_ref = agent_api.state(self.root)["latest"]["outputs"]["step"]
        self.assertIn("scene", step_ref)
        scene_path = Path(step_ref["scene"]["path"])
        scene_document = json.loads(scene_path.read_text())
        object_path = Path(scene_document["objects"][0]["step"]["path"])
        self.assertTrue(object_path.is_file())
        object_path.unlink()
        rebuilt = json.loads(self.cli("build", {"phase": "draft", "source": "model.py"}).stdout)
        self.assertFalse(rebuilt["reused"])
        self.assertNotEqual(rebuilt["runId"], result["runId"])

    def test_real_finishing_failure_inspect_uses_report_sha_and_preserves_report_bytes(self):
        (self.root / "model.py").write_text(
            "from build123d import Box\n"
            "from build_session import BuildSession\n"
            "from cad_helpers import checked_fillet\n"
            "build = BuildSession(__file__, part_names=(\"body\",))\n"
            "build.add(\"body\", Box(10, 10, 10))\n"
            "build.finish(\"body\", lambda p: checked_fillet(p, p.edges(), 100, \"round\", allow_reduce=False))\n"
            "build.export()\n"
        )
        failed = self.cli("build", {"phase": "draft", "source": "model.py"})
        self.assertEqual(failed.returncode, 1, failed.stderr + failed.stdout)
        result = json.loads(failed.stdout)
        issue = next(item for item in result["items"] if item.get("code") == "SOURCE.CHECKED_FILLET_FAILED")
        self.assertIn("id", issue)
        report = result["report"]
        path = Path(report["path"])
        before = path.read_bytes()

        inspected = self.cli("inspect", {
            "report": report["path"], "sha256": report["sha256"],
            "issueId": issue["id"], "field": "/observed",
        })
        self.assertEqual(inspected.returncode, 0, inspected.stderr + inspected.stdout)
        detail = json.loads(inspected.stdout)
        attempts = next(item["value"] for item in detail["items"] if item["field"].endswith("/attempts"))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(path.read_bytes(), before)

    def test_real_compile_keeps_full_evidence_and_binds_string_deliverables(self):
        examples = ROOT / "skills" / "a3d-text" / "examples"
        for name in ("simple_brep_build.py", "simple_brep_intent.py"):
            shutil.copyfile(examples / name, self.root / name)
        shutil.copyfile(examples / "bambu-a1-mini-0.4-standard.example.json", self.root / "simple_brep_printer-profile.json")
        generated = subprocess.run([sys.executable, "-B", "simple_brep_intent.py"], cwd=self.root,
                                   env={**os.environ, "AMAGINE3D_RUNTIME_DIR": str(ROOT / "skills" / "a3d-public")},
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        args = {"phase": "compile", "source": "simple_brep_build.py", "intent": "simple_brep_intent.json",
                "scene": "simple_brep_scene.json"}
        result = self.cli("build", args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        page = json.loads(result.stdout)
        self.assertTrue(page["pass"])
        self.assertTrue(page["visualReviewRequired"])
        saved = json.loads(Path(page["report"]["path"]).read_text())
        self.assertTrue(saved["inputBindings"]["source"]["sha256"])
        current = agent_api.state(self.root)["latest"]
        for key, path in saved["deliverables"].items():
            self.assertEqual({field: current["outputs"][key][field] for field in ("path", "sha256")},
                             protocol.binding(Path(path)))
        repeated = json.loads(self.cli("build", args).stdout)
        self.assertTrue(repeated["reused"])
        self.assertEqual(repeated["runId"], page["runId"])

    def test_real_design_compile_reads_unicode_intent_under_ascii_locale(self):
        examples = ROOT / "skills" / "a3d-text" / "examples"
        shutil.copyfile(examples / "simple_brep_intent.py", self.root / "simple_brep_intent.py")
        shutil.copyfile(examples / "bambu-a1-mini-0.4-standard.example.json", self.root / "simple_brep_printer-profile.json")
        generated = subprocess.run([sys.executable, "-B", "simple_brep_intent.py"], cwd=self.root,
            env={**os.environ, "AMAGINE3D_RUNTIME_DIR": str(ROOT / "skills/a3d-public")}, capture_output=True, text=True, timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        intent_path = self.root / "simple_brep_intent.json"
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        intent["assumptions"].append("中文说明：独立尺寸目标保持不变。")
        intent_path.write_text(json.dumps(intent, ensure_ascii=False), encoding="utf-8")
        design = {"schema": "a3d-design/v1", "units": {"length": "mm", "angle": "deg"}, "params": {},
            "frames": {"top": {"origin": [0, 0, 10], "u": [1, 0, 0], "normal": [0, 0, 1]}},
            "parts": {"simple-brep": {"result": "@simple-brep.envelope", "features": [
                {"id": "blank", "op": "box", "size": [40, 28, 10], "align": ["center", "center", "min"]},
                {"id": "round", "op": "fillet", "body": "@simple-brep.blank", "radius": 2,
                 "edges": {"of": "@simple-brep.blank", "where": {"curve": "line", "parallel": [0, 0, 1]}, "expect": {"count": 4}}},
                {"id": "pocket", "op": "hole", "body": "@simple-brep.round", "radius": 6, "requirement": "pocket",
                 "frame": "@frames.top", "direction": "-normal", "depth": {"mode": "blind", "length": 6}},
                {"id": "envelope", "op": "translate", "input": "@simple-brep.pocket", "offset": [0, 0, 0], "requirement": "body"}]}}}
        path = self.root / "model.a3d.json"
        path.write_text(json.dumps(design))
        args = {"phase": "compile", "design": path.name, "revision": sha256(path.read_bytes()).hexdigest(), "intent": intent_path.name}
        result = subprocess.run(["node", str(ROOT / "bin/a3d.mjs"), "agent", "build", json.dumps(args)], cwd=self.root,
            env={**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8") + result.stderr.decode("utf-8"))
        self.assertTrue(json.loads(result.stdout)["pass"])

    @unittest.skipUnless(os.name == "posix", "process group cancellation test")
    def test_cancelled_cli_reaps_source_and_context_recovers_interruption(self):
        source = self.root / "slow.py"
        source.write_text('import os, time\nfrom pathlib import Path\nPath("source-pid").write_text(str(os.getpid()))\ntime.sleep(60)\n')
        process = subprocess.Popen(["node", str(ROOT / "bin" / "a3d.mjs"), "agent", "build",
                                    json.dumps({"phase": "draft", "source": source.name})], cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not (self.root / "source-pid").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.root / "source-pid").exists())
            pid = int((self.root / "source-pid").read_text())
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=15)
            self.assertNotEqual(process.returncode, 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            context = agent_api.context(self.root, {})
            self.assertEqual(context["activeAttempt"]["status"], "interrupted")
            self.assertIsNone(context["latest"])
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=15)


if __name__ == "__main__":
    unittest.main()
