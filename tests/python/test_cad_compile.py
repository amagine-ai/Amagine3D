from __future__ import annotations

import contextlib
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import cad_compile  # noqa: E402
from cad_compile import (  # noqa: E402
    CommandResult,
    CommandRunner,
    CompileOptions,
    ConfigurationError,
    compile_cad,
    main,
    select_backend,
)
from tests.python.intent_fixture import intent_ref, write_intent  # noqa: E402


def _mark(root: Path) -> Path:
    marker = root / ".generation-start"
    marker.write_text("generation started\n", encoding="utf-8")
    return marker


def _localize_profile(intent_path: Path, root: Path) -> None:
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    source = Path(intent["printability"]["profile"]["path"])
    profile = root / "printer-profile.json"
    profile.write_bytes(source.read_bytes())
    intent["printability"]["profile"] = _bound(profile)
    intent_path.write_text(json.dumps(intent) + "\n", encoding="utf-8")


def _write_scene(root: Path, intent_path: Path) -> Path:
    recipe = {
        "kind": "roundedBox",
        "parameters": {"sizeMm": [40, 30, 20], "radiusMm": 1},
    }
    scene_path = root / "part_scene.json"
    scene_path.write_text(
        json.dumps(
            {
                "schema": "evidence-semantic-scene/v1",
                "revision": "cad-compile-test-001",
                "intentRef": intent_ref(intent_path),
                "units": "mm",
                "coordinateSystem": {"handedness": "right", "up": "Z"},
                "materials": [],
                "parts": [{"id": "part", "representationMaster": "brep"}],
                "nodes": [
                    {
                        "id": "part-body-node",
                        "partId": "part",
                        "featureId": "part-body",
                        "role": "solid",
                        "operation": "union",
                        "recipe": recipe,
                    }
                ],
                "interfaces": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return scene_path


def _bound(path: Path, **fields) -> dict:
    return {
        "path": str(path.resolve()),
        "sha256": sha256(path.read_bytes()).hexdigest(),
        **fields,
    }


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            return proc_stat.read_text(encoding="utf-8").split()[2] != "Z"
        except (IndexError, OSError):
            pass
    return True


def _wait_until_not_running(pid: int, timeout_seconds: float = 3) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_is_running(pid):
            return True
        time.sleep(0.02)
    return not _process_is_running(pid)


def _minimal_report(
    root: Path,
    *,
    intent_path: Path,
    scene_path: Path,
    source_path: Path,
) -> tuple[Path, dict]:
    stl = root / "part.stl"
    step = root / "part.step"
    display = root / "part-display.glb"
    stl.write_bytes(b"solid part\nendsolid part\n")
    step.write_bytes(b"STEP fixture")
    display.write_bytes(b"GLB fixture")
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    profile = Path(intent["printability"]["profile"]["path"])
    geometry = {
        "bodyCount": 1,
        "boundsMm": {"min": [0, 0, 0], "max": [40, 30, 20], "size": [40, 30, 20]},
        "isVolume": True,
        "valid": True,
        "volumeMm3": 1,
    }
    report = {
        "schema": "evidence-a3d-build/v1",
        "backend": "brep-part",
        "part": "part",
        "pass": True,
        "revision": "cad-compile-test-001",
        "inputs": {
            "intent": _bound(intent_path, schema="evidence-cad-intent/v5"),
            "scene": _bound(
                scene_path,
                schema="evidence-semantic-scene/v1",
                revision="cad-compile-test-001",
            ),
            "source": _bound(source_path, schema="python-source/v1"),
            "profile": _bound(
                profile,
                schema="evidence-bambu-printer-profile/v1",
            ),
        },
        "artifacts": {
            "stl:part": _bound(stl, coordinateFrame="part-print"),
            "step:part": _bound(step, coordinateFrame="semantic"),
            "glb:display": _bound(display, coordinateFrame="semantic"),
        },
        "parts": {
            "part": {
                "representationMaster": "brep",
                "semantic": geometry,
                "print": geometry,
            }
        },
        "warnings": [],
    }
    return root / "part_report.json", report


def _audit_schema(stage: str) -> str:
    if stage == "build-check":
        return "evidence-a3d-build-audit/v1"
    if stage in {"assembly-qa", "color-assembly-qa"}:
        return "evidence-assembly-audit/v1"
    if stage.startswith("mesh-qa:"):
        return "evidence-mesh-audit/v3"
    if stage.startswith("step-qa:"):
        return "evidence-step-audit/v1"
    if stage == "color-qa":
        return "evidence-color-print-package-audit/v1"
    raise AssertionError(f"no test audit schema declared for stage {stage}")


class _PassingRunner:
    def __init__(
        self,
        log_path: Path,
        report_path: Path,
        report: dict,
    ):
        self.log_path = log_path
        self.log_path.write_text("fake runner\n", encoding="utf-8")
        self.report_path = report_path
        self.report = report
        self.calls: list[str] = []
        scene_reference = report.get("inputs", {}).get("scene", {})
        self.scene_path = Path(scene_reference["path"])
        self.scene_bytes = self.scene_path.read_bytes()

    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        self.calls.append(stage)
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(f"{stage}\n")
        if stage == "source":
            self.scene_path.write_bytes(self.scene_bytes)
        if stage == "source":
            for reference in self.report.get("artifacts", {}).values():
                if not isinstance(reference, dict) or not isinstance(
                    reference.get("path"), str
                ):
                    continue
                artifact_path = Path(reference["path"])
                if not artifact_path.is_absolute():
                    artifact_path = self.report_path.parent / artifact_path
                if artifact_path.is_file():
                    artifact_path.write_bytes(artifact_path.read_bytes())
            payload = {
                **self.report,
                "runId": (env_extra or {}).get("AMAGINE3D_COMPILE_RUN_ID"),
            }
            self.report_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        elif stage == "source":
            pass
        elif stage == "render":
            display = Path(argv[2]).resolve()
            preview = Path(argv[argv.index("--out") + 1]).resolve()
            evidence = Path(argv[argv.index("--report") + 1]).resolve()
            reference_view = argv[argv.index("--reference-view") + 1]
            reference = Path(argv[argv.index("--reference-out") + 1]).resolve()
            preview.write_bytes(b"preview")
            reference.write_bytes(b"matched view")
            evidence.write_text(
                json.dumps(
                    {
                        "schema": "evidence-render/v2",
                        "meshes": [_bound(display)],
                        "preview": _bound(preview),
                        "matched_view": {
                            **_bound(reference),
                            "name": reference_view,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        elif stage == "freshness":
            marker_index = argv.index("--after")
            marker = argv[marker_index + 1]
            checked = argv[marker_index + 2 :]
            marker_path = Path(marker)
            marker_stat = marker_path.stat()
            payload = {
                "pass": True,
                "marker": marker,
                "marker_mtime_ns": marker_stat.st_mtime_ns,
                "marker_sha256": sha256(marker_path.read_bytes()).hexdigest(),
                "marker_size": marker_stat.st_size,
                "artifacts": [
                    {
                        "path": path,
                        "exists": True,
                        "fresh": True,
                        "mtime_ns": Path(path).stat().st_mtime_ns,
                        "sha256": sha256(Path(path).read_bytes()).hexdigest(),
                        "size": Path(path).stat().st_size,
                        "stable": True,
                    }
                    for path in checked
                ],
            }
            return CommandResult(
                returncode=0,
                elapsed_ms=1,
                output_tail=json.dumps(payload),
            )
        else:
            output = Path(argv[argv.index("--out") + 1])
            output.write_text(
                json.dumps(
                    {
                        "errors": [],
                        "pass": True,
                        "schema": _audit_schema(stage),
                        "warnings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return CommandResult(returncode=0, elapsed_ms=1, output_tail="")


class _DeferredSourceDiagnosticRunner(_PassingRunner):
    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        command = super().run(
            stage,
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env_extra=env_extra,
        )
        if stage != "source":
            return command
        return CommandResult(
            returncode=1,
            elapsed_ms=command.elapsed_ms,
            output_tail=json.dumps(
                {
                    "schema": "evidence-cad-source-diagnostics/v1",
                    "pass": False,
                    "issues": [
                        {
                            "check": "checked-cut",
                            "code": "SOURCE.CUT_MISSED_OWNER",
                            "expected": {"minimumRemovedMm3": ">0"},
                            "featureId": "part/opening",
                            "message": "opening cutter does not intersect its owner",
                            "observed": {"removedMm3": 0.0},
                            "partId": "part",
                            "severity": "error",
                        }
                    ],
                }
            ),
        )


class _ZeroReturncodeTimeoutRunner(_PassingRunner):
    def __init__(
        self,
        log_path: Path,
        report_path: Path,
        report: dict,
        *,
        timed_out_stage: str,
    ):
        super().__init__(
            log_path,
            report_path,
            report,
        )
        self.timed_out_stage = timed_out_stage

    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        command = super().run(
            stage,
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env_extra=env_extra,
        )
        if stage != self.timed_out_stage:
            return command
        return CommandResult(
            returncode=0,
            elapsed_ms=command.elapsed_ms,
            output_tail=command.output_tail,
            timed_out=True,
        )


class _InvalidFreshnessRunner(_PassingRunner):
    def __init__(self, *args, invalidity: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.invalidity = invalidity

    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        command = super().run(
            stage,
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env_extra=env_extra,
        )
        if stage != "freshness":
            return command
        payload = json.loads(command.output_tail)
        if self.invalidity == "wrong-marker":
            payload["marker"] = str(self.report_path.parent / ".unrelated-marker")
        elif self.invalidity == "missing-artifact":
            payload["artifacts"].pop()
        elif self.invalidity == "false-freshness":
            payload["artifacts"][0]["fresh"] = False
        elif self.invalidity == "deleted-artifact":
            Path(payload["artifacts"][0]["path"]).unlink()
        elif self.invalidity == "deleted-marker":
            Path(payload["marker"]).unlink()
        elif self.invalidity == "wrong-mtime":
            payload["artifacts"][0]["mtime_ns"] += 1
        elif self.invalidity == "wrong-size":
            payload["artifacts"][0]["size"] += 1
        elif self.invalidity == "wrong-sha256":
            payload["artifacts"][0]["sha256"] = "0" * 64
        elif self.invalidity == "changed-after-check":
            artifact = payload["artifacts"][0]
            path = Path(artifact["path"])
            original = path.read_bytes()
            if not original:
                raise AssertionError("freshness mutation fixture must be non-empty")
            changed = bytes([original[0] ^ 1]) + original[1:]
            before = path.stat()
            path.write_bytes(changed)
            os.utime(
                path,
                ns=(before.st_atime_ns, artifact["mtime_ns"]),
            )
        else:
            raise AssertionError(f"unknown invalidity {self.invalidity}")
        return CommandResult(
            returncode=0,
            elapsed_ms=command.elapsed_ms,
            output_tail=json.dumps(payload),
        )


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _BudgetRunner(_PassingRunner):
    def __init__(self, *args, clock: _FakeClock, **kwargs):
        super().__init__(*args, **kwargs)
        self.clock = clock
        self.timeouts: list[tuple[str, float]] = []

    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        self.timeouts.append((stage, timeout_seconds))
        command = super().run(
            stage,
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env_extra=env_extra,
        )
        elapsed = 3.0 if stage == "source" else timeout_seconds
        self.clock.advance(elapsed)
        if stage != "build-check":
            return command
        return CommandResult(
            returncode=0,
            elapsed_ms=round(elapsed * 1_000),
            output_tail=command.output_tail,
            timed_out=True,
        )


def _compile_with_zero_returncode_timeout(
    root: Path,
    *,
    timed_out_stage: str,
) -> tuple[dict, _ZeroReturncodeTimeoutRunner]:
    marker = _mark(root)
    intent, _ = write_intent(
        root,
        part="part",
        feature_owners={"part-body": "part"},
        dimensions_mm=(40, 30, 20),
    )
    _localize_profile(intent, root)
    scene = _write_scene(root, intent)
    source = root / "build.py"
    source.write_text("# fake source is handled by the injected runner\n")
    report_path, report = _minimal_report(
        root,
        intent_path=intent,
        scene_path=scene,
        source_path=source,
    )
    holder: dict[str, _ZeroReturncodeTimeoutRunner] = {}

    def factory(log_path: Path) -> _ZeroReturncodeTimeoutRunner:
        runner = _ZeroReturncodeTimeoutRunner(
            log_path,
            report_path,
            report,
            timed_out_stage=timed_out_stage,
        )
        holder["runner"] = runner
        return runner

    result = compile_cad(
        CompileOptions(
            workspace=root,
            marker=marker,
            intent=intent,
            scene=scene,
            source=source,
            output_dir=Path("."),
        ),
        runner_factory=factory,
    )
    return result, holder["runner"]


class _FailingMeshRunner(_PassingRunner):
    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        if stage != "mesh-qa:part":
            return super().run(
                stage,
                argv,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                env_extra=env_extra,
            )
        self.calls.append(stage)
        output = Path(argv[argv.index("--out") + 1])
        output.write_text(
            json.dumps(
                {
                    "errors": ["minimum_wall_thickness"],
                    "pass": False,
                    "schema": "evidence-mesh-audit/v3",
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return CommandResult(returncode=1, elapsed_ms=1, output_tail="")


class _StructuredFailingMeshRunner(_PassingRunner):
    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        if stage != "mesh-qa:part":
            return super().run(
                stage,
                argv,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                env_extra=env_extra,
            )
        self.calls.append(stage)
        output = Path(argv[argv.index("--out") + 1])
        output.write_text(
            json.dumps(
                {
                    "errors": ["legacy-summary"],
                    "issues": [
                        {
                            "check": "component-count",
                            "code": "QA.MULTIPLE_COMPONENTS",
                            "componentCount": 3,
                            "expected": {"componentCount": 1},
                            "observed": {"componentCount": 3},
                            "partId": "part",
                            "repairHint": "Join only the physical regions that belong to this part.",
                            "severity": "error",
                        },
                        {
                            "check": "thin-wall",
                            "code": "QA.THIN_WALL",
                            "expected": {"minimumMm": 0.8},
                            "observed": {"minimumMm": 0.5},
                            "partId": "part",
                            "severity": "error",
                        },
                    ],
                    "pass": False,
                    "schema": "evidence-mesh-audit/v3",
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return CommandResult(returncode=1, elapsed_ms=1, output_tail="")


class _InvalidBuildAuditRunner(_PassingRunner):
    def __init__(self, *args, invalidity: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.invalidity = invalidity

    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        command = super().run(
            stage,
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env_extra=env_extra,
        )
        if stage != "build-check":
            return command
        payload = {
            "errors": [],
            "pass": True,
            "schema": "evidence-a3d-build-audit/v1",
            "warnings": [],
        }
        if self.invalidity == "wrong-schema":
            payload["schema"] = "evidence-unrelated-audit/v1"
        elif self.invalidity == "errors-with-pass":
            payload["errors"] = ["contradictory failure"]
        elif self.invalidity == "error-issue-with-pass":
            payload["issues"] = [
                {
                    "code": "QA.CONTRADICTORY_FAILURE",
                    "message": "contradictory failure",
                    "severity": "error",
                }
            ]
        else:
            raise AssertionError(f"unknown invalidity {self.invalidity}")
        output = Path(argv[argv.index("--out") + 1])
        output.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return command


def _compile_with_render_fixture(root: Path, runner_type) -> tuple[dict, _PassingRunner]:
    marker = _mark(root)
    intent, _ = write_intent(
        root,
        part="part",
        feature_owners={"part-body": "part"},
        dimensions_mm=(40, 30, 20),
    )
    _localize_profile(intent, root)
    scene = _write_scene(root, intent)
    source = root / "build.py"
    source.write_text("# fake source is handled by the injected runner\n")
    report_path, report = _minimal_report(
        root, intent_path=intent, scene_path=scene, source_path=source
    )
    runner = None

    def factory(log_path):
        nonlocal runner
        runner = runner_type(log_path, report_path, report)
        return runner

    result = compile_cad(
        CompileOptions(
            workspace=root,
            marker=marker,
            intent=intent,
            scene=scene,
            source=source,
            output_dir=Path("."),
        ),
        runner_factory=factory,
    )
    return result, runner


class CadCompileTests(unittest.TestCase):
    def test_declared_installation_failure_blocks_successful_preview_publication(self) -> None:
        class InstallationFailure(_PassingRunner):
            def __init__(self, log_path, report_path, report):
                super().__init__(log_path, report_path, report)
                # Contract/geometry behavior is covered by the real STEP audit tests;
                # this injected runner isolates stage dispatch and publication gating.
                scene = json.loads(self.scene_bytes)
                scene["installationChecks"] = [{"featureId": "part-body"}]
                self.scene_bytes = json.dumps(scene).encode()
                self.scene_path.write_bytes(self.scene_bytes)
                self.report["inputs"]["scene"]["sha256"] = sha256(self.scene_bytes).hexdigest()

            def run(self, stage, argv, **kwargs):
                if stage == "installation-qa":
                    self.calls.append(stage)
                    output = Path(argv[argv.index("--out") + 1])
                    output.write_text(json.dumps({
                        "schema": "evidence-installation-audit/v1", "pass": False,
                        "errors": [{"code": "QA.INSTALLATION_FAILED", "check": "mount/passage",
                                    "message": "required passage is blocked"}],
                    }))
                    return CommandResult(returncode=1, elapsed_ms=1, output_tail="")
                return super().run(stage, argv, **kwargs)

        with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as directory:
            root = Path(directory)
            with mock.patch.object(cad_compile, "validate_scene", return_value=[]):
                result, runner = _compile_with_render_fixture(root, InstallationFailure)
            self.assertIn("installation-qa", runner.calls)
            self.assertFalse(result["pass"])
            self.assertFalse(result["deliveryReady"])
            persisted = json.loads((root / "part_compile-result.json").read_text())
            self.assertIn("installationAudit", persisted["artifacts"])
            self.assertIn("diagnosticPreview", result["artifacts"])
            self.assertNotIn("preview", result["artifacts"])
            self.assertTrue(any(i["code"] == "QA.INSTALLATION_FAILED" for i in result["issues"]))

    def test_source_failure_preserves_full_traceback_but_prints_compact_root_cause(self) -> None:
        traceback = (
            "Traceback (most recent call last):\n"
            + '  File "/managed/library.py", line 42, in construct\n    invoke()\n' * 35
            + '  File "build.py", line 69, in <module>\n    Cylinder(axis=(0, 1, 0))\n'
            + "TypeError: Cylinder.__init__() got an unexpected keyword argument 'axis'"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(root, part="part", feature_owners={"part-body": "part"})
            _localize_profile(intent, root)
            source = root / "build.py"
            source.write_text("# injected source failure\n", encoding="utf-8")
            runner = mock.Mock()
            runner.run.return_value = CommandResult(returncode=1, elapsed_ms=1, output_tail=traceback)
            result = compile_cad(
                CompileOptions(
                    workspace=root, marker=marker, intent=intent,
                    scene=root / "scene.json", source=source, output_dir=Path("."),
                ),
                runner_factory=lambda _: runner,
            )
            persisted = json.loads(Path(result["result"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted["issues"][0]["message"], traceback)
            self.assertEqual(result["issues"][0]["message"], traceback)
            summary = cad_compile._agent_summary(result)
            message = summary["issues"][0]["message"]
            self.assertLessEqual(len(message), cad_compile.MAX_MESSAGE_CHARS)
            self.assertIn('File "build.py", line 69', message)
            self.assertTrue(message.endswith("unexpected keyword argument 'axis'"))
            self.assertEqual(summary["issues"][0]["id"], persisted["issues"][0]["id"])
            self.assertEqual(result["issues"][0]["message"], traceback)

    def test_agent_summary_preserves_real_boolean_gap_points_and_component_selection(self):
        from build123d import Box, Compound, Pos
        import cad_helpers

        body = Box(10, 10, 10)
        cases = (
            (cad_helpers.checked_union, body,
             Compound(children=[Pos(0, 4.5, 0) * Box(2, 2, 2), Pos(6.05, 0, 0) * Box(2, 2, 2)]),
             "SOURCE.UNION_DISCONNECTED", 0.05, 1),
            (cad_helpers.checked_cut, body - Box(6, 6, 6), Box(2, 2, 2),
             "SOURCE.CUT_MISSED_OWNER", 2.0, 0),
        )
        for operation, owner, operand, code, gap, index in cases:
            with self.subTest(code=code):
                evidence = cad_helpers._EvidenceState()
                with cad_helpers._evidence_scope(evidence), mock.patch.dict(
                        os.environ, {"AMAGINE3D_SOURCE_PHASE": "compile"}):
                    operation(owner, operand, "feature", part_name="owner")
                issue = next(item for item in evidence.issues if item["code"] == code)
                original = json.dumps(issue, sort_keys=True)
                summary = cad_compile._agent_summary({"issues": [issue], "runId": "boolean-test"})
                compact = summary["issues"][0]["booleanWitness"]
                self.assertNotIn("witness", summary["issues"][0])
                self.assertEqual(compact["coordinateFrame"], "operation-input")
                self.assertEqual(compact["units"], "mm")
                self.assertEqual(compact["unconnectedComponentCount"], 1)
                self.assertEqual(compact["unresolvedComponentCount"], 0)
                component = compact["component"]
                self.assertEqual(component["solidIndex"], index)
                self.assertAlmostEqual(component["gapMm"], gap)
                self.assertEqual(component["relation"], "disjoint")
                self.assertFalse(component["connectedToOwner"])
                points = (component["ownerPointMm"], component["operandPointMm"])
                self.assertTrue(all(len(point) == 3 for point in points))
                self.assertAlmostEqual(sum((a-b)**2 for a, b in zip(*points))**0.5, gap)
                if operation is cad_helpers.checked_cut:
                    self.assertEqual(compact["removedMm3"], 0)
                    self.assertEqual(compact["intersectionVolumeMm3"], 0)
                    self.assertTrue(compact["operandInsideOwnerBounds"])
                self.assertLessEqual(len(cad_compile._summary_json(summary)), cad_compile.MAX_SUMMARY_CHARS)
                self.assertEqual(json.dumps(issue, sort_keys=True), original)

    def test_agent_summary_preserves_errors_and_groups_repeated_warnings(self) -> None:
        result = {
            "artifacts": {
                "preview": {"path": "/tmp/preview.png", "sha256": "preview"},
                "sourcePreflight": {
                    "path": "/tmp/source.json",
                    "sha256": "source",
                },
            },
            "backend": "brep-source",
            "deliveryReady": False,
            "issues": [
                {
                    "code": "QA.THIN_WALL",
                    "message": "wall is too thin",
                    "observed": {"minimumMm": 0.5},
                    "part": "housing",
                    "repairHint": "Increase the source wall thickness.",
                    "severity": "error",
                    "stage": "mesh-qa:housing",
                },
                *[
                    {
                        "check": "printability_overhang",
                        "code": "QA.WARNING",
                        "message": "overhang warning",
                        "part": part,
                        "repairHint": "Review this region.",
                        "severity": "warning",
                        "stage": f"mesh-qa:{part}",
                    }
                    for part in ("key-1", "key-2")
                ],
            ],
            "model": "keypad",
            "omittedErrorCount": 0,
            "omittedIssueCount": 0,
            "pass": False,
            "result": {"path": "/tmp/keypad_compile-result.json"},
            "runId": "run-1",
            "schema": cad_compile.RESULT_SCHEMA,
            "status": "failed",
            "visualReviewRequired": True,
        }

        summary = cad_compile._agent_summary(result)

        self.assertEqual(summary["schema"], "a3d-compile-summary/v1")
        self.assertEqual(summary["artifacts"], {"preview": "/tmp/preview.png"})
        self.assertEqual(
            summary["issueCounts"],
            {"errors": 1, "omitted": 0, "warnings": 2},
        )
        self.assertEqual(summary["issues"][0]["observed"], {"minimumMm": 0.5})
        grouped = summary["issues"][1]
        self.assertEqual(grouped["count"], 2)
        self.assertEqual(grouped["parts"], ["key-1", "key-2"])
        self.assertEqual(
            grouped["stages"],
            ["mesh-qa:key-1", "mesh-qa:key-2"],
        )

    def test_agent_summary_bounds_nested_evidence_and_retains_cause_without_mutation(self) -> None:
        message = (
            "Traceback (most recent call last):\n" + "library frame\n" * 5_000
            + 'File "build.py", line 69\nTypeError: unexpected keyword argument axis'
        )
        result = {
            "pass": False, "status": "failed", "deliveryReady": False,
            "result": {"path": "/tmp/part_compile-result.json"},
            "issues": [{
                "id": "source-failure", "code": "BACKEND.COMPILE_FAILED",
                "severity": "error", "stage": "source", "message": message,
                "actual": {
                    "details": "x" * 50_000, "minimumMm": 0.5,
                    "nested": [{"report": {"frames": ["y" * 10_000] * 20}}] * 20,
                },
                "expected": {"minimumMm": 1.2},
            }],
        }
        original = json.dumps(result)

        summary = cad_compile._agent_summary(result)
        encoded = cad_compile._summary_json(summary)

        self.assertLessEqual(len(encoded), cad_compile.MAX_SUMMARY_CHARS)
        self.assertFalse(summary["pass"])
        self.assertFalse(summary["deliveryReady"])
        self.assertEqual(summary["result"], result["result"])
        issue = summary["issues"][0]
        self.assertEqual(issue["id"], "source-failure")
        self.assertIn('File "build.py", line 69', issue["message"])
        self.assertTrue(issue["message"].endswith("TypeError: unexpected keyword argument axis"))
        self.assertEqual(issue["actual"]["minimumMm"], 0.5)
        self.assertEqual(issue["expected"]["minimumMm"], 1.2)
        self.assertTrue(issue["summaryTruncated"])
        self.assertTrue(summary["diagnostics"]["truncated"])
        self.assertEqual(json.dumps(result), original)

    def test_agent_summary_keeps_warning_measurements_and_labels_group_samples(self) -> None:
        warning = {
            "id": "warning-a", "code": "QA.THIN_WALL", "severity": "warning",
            "message": "review minimum wall", "featureId": "button/web",
            "observed": {"minimumMm": 0.5}, "expected": {"minimumMm": 2.4},
        }
        single = cad_compile._agent_summary({"issues": [warning]})
        self.assertEqual(single["issues"][0]["observed"], warning["observed"])
        self.assertEqual(single["issues"][0]["expected"], warning["expected"])
        self.assertEqual(single["issues"][0]["featureId"], "button/web")
        self.assertFalse(single["diagnostics"]["truncated"])

        grouped = cad_compile._agent_summary({"issues": [
            warning, {**warning, "id": "warning-b", "observed": {"minimumMm": 0.8}},
        ]})
        sample = grouped["issues"][0]
        self.assertEqual(sample["id"], "warning-a")
        self.assertEqual(sample["observed"], warning["observed"])
        self.assertEqual(sample["ids"], ["warning-a", "warning-b"])
        self.assertEqual(sample["detailScope"], "representative-issue")
        self.assertTrue(sample["summaryTruncated"])
        self.assertTrue(grouped["diagnostics"]["truncated"])

    def test_agent_summary_reserves_first_cause_before_extreme_path_metadata(self) -> None:
        result = {
            "result": {"path": "/" + "\n" * 4_050 + "/compile-result.json"},
            "issues": [{"id": "failure", "code": "SOURCE.FAILED", "severity": "error",
                        "message": "\x00" * 2_000 + "\nTypeError: actual root cause"}],
        }
        summary = cad_compile._agent_summary(result)
        self.assertLessEqual(len(cad_compile._summary_json(summary)), cad_compile.MAX_SUMMARY_CHARS)
        self.assertEqual(summary["diagnostics"]["shownIssueGroups"], 1)
        self.assertTrue(summary["issues"][0]["message"].endswith("TypeError: actual root cause"))
        self.assertTrue(summary["diagnostics"]["truncated"])
        if "result" in summary:
            self.assertEqual(summary["result"], result["result"])

    def test_agent_summary_emits_strict_json_for_nonfinite_measurements(self) -> None:
        result = {"issues": [{"code": "QA.FAILED", "message": "invalid measured bounds",
                              "actual": {"volumeMm3": float("nan"), "distanceMm": float("inf")}}]}
        summary = cad_compile._agent_summary(result)
        # allow_nan=False would reject an unprojected invalid numeric value.
        json.dumps(summary, allow_nan=False)
        self.assertEqual(summary["issues"][0]["actual"], {"volumeMm3": "NaN", "distanceMm": "Infinity"})
        self.assertTrue(summary["diagnostics"]["truncated"])

    def test_agent_summary_budgets_all_fields_including_escaped_json_and_warning_groups(self) -> None:
        # Controls expand sixfold in JSON; per-string limits alone cannot bound stdout.
        detail = "\x00\n\"\\屏幕" * 1_000
        result = {
            "pass": False, "status": "failed", "deliveryReady": False,
            "result": {"path": "/tmp/part_compile-result.json"},
            "issues": [
                {"id": f"error-{i}", "code": "QA.FAILED", "severity": "error",
                 "message": detail + f"\nFailure {i}",
                 "actual": {f"measurement-{j}": detail for j in range(40)},
                 "repairHint": detail}
                for i in range(40)
            ] + [
                {"id": f"warning-{i}", "code": "QA.WARNING", "severity": "warning",
                 "message": "review overhang", "part": f"part-{i}", "repairHint": detail}
                for i in range(40)
            ],
            "artifacts": {key: {"path": "/tmp/" + detail} for key in cad_compile.AGENT_ARTIFACT_KEYS},
            "deliverables": {f"stl:part-{i}": "/tmp/" + detail for i in range(40)},
            "physicalParts": [detail] * 40,
            "colors": {f"part-{i}": detail for i in range(40)},
            "repairDelta": {"remaining": [detail] * 40},
            "omittedIssueCount": 3, "omittedErrorCount": 2,
        }

        for issues in (result["issues"], result["issues"][40:]):
            with self.subTest(warnings_only=issues[0]["severity"] == "warning"):
                payload = {**result, "issues": issues}
                summary = cad_compile._agent_summary(payload)
                self.assertLessEqual(len(cad_compile._summary_json(summary)), cad_compile.MAX_SUMMARY_CHARS)
                shown = len(summary["issues"])
                self.assertGreater(shown, 0)
                self.assertLessEqual(shown, cad_compile.MAX_SUMMARY_ISSUES)
                self.assertEqual(summary["diagnostics"]["shownIssueGroups"], shown)
                groups = 1 + (40 if issues[0]["severity"] == "error" else 0)
                self.assertEqual(summary["diagnostics"]["omittedIssueGroups"], groups - shown)
                self.assertEqual(summary["issueCounts"]["warnings"], 40)
                self.assertEqual(summary["issueCounts"]["omitted"], 3)
                self.assertTrue(summary["diagnostics"]["truncated"])
                self.assertEqual(summary["result"], result["result"])
                self.assertFalse(summary["pass"])
                if issues[0]["severity"] == "error":
                    self.assertEqual(summary["issueCounts"]["errors"], 42)
                    self.assertTrue(summary["issues"][0]["message"].endswith("Failure 0"))
                else:
                    self.assertEqual(summary["issues"][0]["count"], 40)
                    self.assertTrue(summary["issues"][0]["summaryTruncated"])

    def test_report_agent_facts_exposes_only_public_delivery_paths(self) -> None:
        facts = cad_compile._report_agent_facts(
            {
                "artifacts": {
                    "3mf": {"path": "/tmp/model.3mf"},
                    "glb:display": {
                        "path": "/tmp/model.glb",
                        "readbackBaseColors": {"body": "#102030"},
                    },
                    "plate-stl:body": {
                        "path": "/tmp/.amagine3d-internal/plate/body.stl"
                    },
                    "step:body": {"path": "/tmp/body.step"},
                },
                "parts": {"body": {}},
            }
        )

        self.assertEqual(facts["colors"], {"body": "#102030"})
        self.assertEqual(facts["physicalParts"], ["body"])
        self.assertEqual(
            facts["deliverables"],
            {
                "3mf": "/tmp/model.3mf",
                "glb:display": "/tmp/model.glb",
                "step:body": "/tmp/body.step",
            },
        )

    def test_repair_state_tracks_unblocked_resolved_and_regressed_issues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / "part_intent.json"
            source = root / "build.py"
            intent.write_text('{"part":"part"}\n', encoding="utf-8")
            source.write_text("# repair ledger fixture\n", encoding="utf-8")
            result_path = root / "part_compile-result.json"

            def compile_result(run_id: str, issues: list[dict]) -> dict:
                return {
                    "finishedAt": f"2026-09-02T00:00:0{run_id[-1]}+00:00",
                    "inputs": {"intent": str(intent), "source": str(source)},
                    "issues": issues,
                    "model": "part",
                    "runId": run_id,
                    "stages": [{"name": "source", "status": "pass"}],
                }

            issue = {
                "check": "checked-cut",
                "code": "SOURCE.CUT_MISSED_OWNER",
                "featureId": "part/opening",
                "part": "part",
                "severity": "error",
                "stage": "source",
            }
            blocked = {**issue, "blockedBy": "OWNER_UNAVAILABLE", "status": "blocked"}

            state_path = cad_compile._write_repair_state(
                compile_result("run-1", [blocked]),
                result_path=result_path,
            )
            first = json.loads(state_path.read_text(encoding="utf-8"))
            issue_id = first["blocked"][0]["id"]
            self.assertEqual(first["delta"]["new"], [])

            second_result = compile_result("run-2", [issue])
            cad_compile._write_repair_state(second_result, result_path=result_path)
            self.assertEqual(second_result["repairDelta"]["newlyUnblocked"], [issue_id])

            third_result = compile_result("run-3", [])
            cad_compile._write_repair_state(third_result, result_path=result_path)
            self.assertEqual(third_result["repairDelta"]["resolved"], [issue_id])

            fourth_result = compile_result("run-4", [issue])
            cad_compile._write_repair_state(fourth_result, result_path=result_path)
            self.assertEqual(fourth_result["repairDelta"]["regressed"], [issue_id])

    def test_structured_source_failure_continues_independent_audits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )
            holder = {}

            def factory(log_path):
                runner = _DeferredSourceDiagnosticRunner(
                    log_path,
                    report_path,
                    report,
                )
                holder["runner"] = runner
                return runner

            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=scene,
                    source=source,
                    output_dir=Path("."),
                ),
                runner_factory=factory,
            )

            self.assertFalse(result["pass"], result)
            self.assertEqual(
                holder["runner"].calls,
                ["source", "build-check", "render", "mesh-qa:part", "step-qa:part"],
            )
            issue = next(
                issue
                for issue in result["issues"]
                if issue["code"] == "SOURCE.CUT_MISSED_OWNER"
            )
            self.assertEqual(issue["part"], "part")
            self.assertEqual(issue["featureId"], "part/opening")
            self.assertNotIn("renderEvidence", result["artifacts"])

    def test_backend_selection_uses_part_representation_masters(self) -> None:
        self.assertEqual(
            select_backend({"parts": [{"representationMaster": "brep"}]}),
            "brep-source",
        )
        for parts in (
            [{"representationMaster": "mesh"}],
            [{"representationMaster": "brep"}, {"representationMaster": "mesh"}],
            [{"representationMaster": "brep"}, None],
        ):
            with self.subTest(parts=parts), self.assertRaisesRegex(ValueError, "must be brep"):
                select_backend({"parts": parts})

    def test_workspace_escape_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_directory, tempfile.TemporaryDirectory() as outside_directory:
            workspace = Path(workspace_directory)
            outside = Path(outside_directory)
            marker = _mark(workspace)
            intent = outside / "intent.json"
            intent.write_text("{}", encoding="utf-8")
            source = workspace / "build.py"
            source.write_text("raise SystemExit(99)\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                compile_cad(
                    CompileOptions(
                        workspace=workspace,
                        marker=marker,
                        intent=intent,
                        scene=Path("scene.json"),
                        source=source,
                        output_dir=Path("."),
                    )
                )

    def test_invalid_intent_stops_before_agent_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent = root / "intent.json"
            intent.write_text("{}\n", encoding="utf-8")
            source = root / "build.py"
            source.write_text(
                "from pathlib import Path\nPath('source-ran').write_text('yes')\n",
                encoding="utf-8",
            )
            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=Path("scene.json"),
                    source=source,
                    output_dir=Path("."),
                )
            )
            self.assertFalse(result["pass"])
            self.assertFalse((root / "source-ran").exists())
            self.assertTrue(
                all(
                    issue["code"] == "CONTRACT.INTENT_INVALID"
                    for issue in result["issues"]
                )
            )
            self.assertTrue(
                all(
                    {"code", "stage", "message", "repairHint"}.issubset(issue)
                    for issue in result["issues"]
                )
            )
            audit = json.loads(
                (root / "cad_intent-validation.json").read_text(encoding="utf-8")
            )
            self.assertFalse(audit["pass"])

    def test_source_preflight_stops_invalid_api_and_contract_authoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
            )
            _localize_profile(intent, root)
            source = root / "build.py"
            source.write_text(
                "from authoring import write_intent\n"
                "from build123d import Ellipsoid\n"
                "from pathlib import Path\n"
                "Path('source-ran').write_text('yes')\n",
                encoding="utf-8",
            )

            class NeverRunSource:
                def __init__(self, log_path: Path):
                    self.log_path = log_path

                def run(self, *args, **kwargs):
                    raise AssertionError("invalid source reached the subprocess runner")

            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=Path("part_scene.json"),
                    source=source,
                    output_dir=Path("."),
                ),
                runner_factory=NeverRunSource,
            )
            self.assertFalse(result["pass"])
            self.assertFalse((root / "source-ran").exists())
            self.assertTrue(
                all(
                    issue["code"] == "SOURCE.PREFLIGHT_FAILED"
                    and issue["stage"] == "source-preflight"
                    for issue in result["issues"]
                )
            )
            self.assertEqual(
                {issue["check"] for issue in result["issues"]},
                {"api-symbol", "contract-authoring"},
            )
            preflight_path = Path(result["artifacts"]["sourcePreflight"]["path"])
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            self.assertEqual(
                preflight["schema"], "evidence-python-source-preflight/v1"
            )
            self.assertFalse(preflight["pass"])
            self.assertEqual(
                {error["name"] for error in preflight["errors"]},
                {"Ellipsoid", "write_intent"},
            )

    def test_rejected_legacy_revision_keeps_baseline_for_next_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, original = write_intent(root, part="part", feature_owners={"part-body": "part"})
            parent_hash = sha256(intent.read_bytes()).hexdigest()
            (root / "part_compile-result.json").write_text(json.dumps({"inputs": {"intent": str(intent)}}))
            (root / "part_repair-state.json").write_text(json.dumps({"schema": cad_compile.REPAIR_STATE_SCHEMA, "intentHash": parent_hash}))
            source = root / "build.py"
            source.write_text("# source is never run for unlinked targets\n")
            revised = json.loads(json.dumps(original))
            revised["features"][0]["acceptance"] = "Changed target"
            revised_path = root / "part_next.json"
            revised_path.write_text(json.dumps(revised))

            class NoGeometryRunner:
                def __init__(self, log_path):
                    self.log_path = log_path
                def run(self, stage, argv, **kwargs):
                    return CommandResult(returncode=0, elapsed_ms=1, output_tail="")

            options = CompileOptions(workspace=root, marker=marker, intent=revised_path, scene=Path("part_scene.json"), source=source, output_dir=Path("."))
            invalid = {**revised, "features": []}
            revised_path.write_text(json.dumps(invalid))
            invalid_result = compile_cad(options, runner_factory=NoGeometryRunner)
            self.assertTrue(any(issue["code"] == "CONTRACT.INTENT_INVALID" for issue in invalid_result["issues"]))
            self.assertEqual(json.loads((root / ".part_intent-history.json").read_text())["headHash"], parent_hash)
            revised_path.write_text(json.dumps(revised))
            first = compile_cad(options, runner_factory=NoGeometryRunner)
            self.assertTrue(any(issue["code"] == "CONTRACT.INTENT_REVISION_UNVERIFIED" for issue in first["issues"]))
            history = json.loads((root / ".part_intent-history.json").read_text())
            self.assertEqual(history["headHash"], parent_hash)
            revised["revision"] = {"parent": {"path": intent.name, "sha256": parent_hash}, "kind": "target-change", "reason": "Updated requirement", "evidence": {"kind": "user-request", "text": "The target should change."}}
            revised_path.write_text(json.dumps(revised))
            second = compile_cad(options, runner_factory=NoGeometryRunner)
            self.assertTrue(any(issue["code"] == "CONTRACT.SCENE_MISSING" for issue in second["issues"]))
            self.assertFalse(any("REVISION_UNVERIFIED" in issue["code"] or "HISTORY_UNVERIFIED" in issue["code"] for issue in second["issues"]))
            history = json.loads((root / ".part_intent-history.json").read_text())
            self.assertEqual(history["headHash"], sha256(revised_path.read_bytes()).hexdigest())

    def test_source_diagnostics_sidecar_preserves_fields_beyond_log_tail(self) -> None:
        for stale in (False, True):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = _mark(root)
                intent, _ = write_intent(root, part="part", feature_owners={"part-body": "part"})
                source = root / "build.py"
                source.write_text("# structural authoring failure\n")

                class SidecarRunner:
                    def __init__(self, log_path):
                        self.log_path = log_path

                    def run(self, stage, argv, **kwargs):
                        self.log_path.write_text("full source traceback\n")
                        environment = kwargs["env_extra"]
                        payload = {
                            "schema": cad_compile.SOURCE_DIAGNOSTICS_SCHEMA,
                            "runId": "older-run" if stale else environment["AMAGINE3D_COMPILE_RUN_ID"],
                            "pass": False,
                            "issues": [{"code": "SOURCE.AUTHORING_INVALID", "check": "authoring-interface", "path": f"interfaces[{i}].male.featureId", "interfaceId": f"join-{i}", "actual": None, "expected": ["male", "female"], "message": "Missing endpoint " + "details " * 100, "severity": "error"} for i in range(60)],
                        }
                        Path(environment["AMAGINE3D_SOURCE_DIAGNOSTICS_PATH"]).write_text(json.dumps(payload))
                        return CommandResult(returncode=1, elapsed_ms=1, output_tail="truncated traceback")

                result = compile_cad(CompileOptions(workspace=root, marker=marker, intent=intent, scene=Path("part_scene.json"), source=source, output_dir=Path(".")), runner_factory=SidecarRunner)
                self.assertFalse(result["pass"])
                if stale:
                    self.assertTrue(any(issue["code"] == "SOURCE.EXECUTION_FAILED" for issue in result["issues"]))
                    self.assertFalse(any(issue["code"] == "SOURCE.AUTHORING_INVALID" for issue in result["issues"]))
                else:
                    self.assertEqual(len(result["issues"]), 60)
                    self.assertEqual(result["issues"][-1]["path"], "interfaces[59].male.featureId")
                    self.assertTrue(all(issue["code"] == "SOURCE.AUTHORING_INVALID" for issue in result["issues"]))
                    saved = json.loads(Path(result["result"]["path"]).read_text())
                    self.assertEqual(len(saved["issueOccurrences"]), 60)
                    self.assertIn("sourceDiagnostics", saved["artifacts"])

    def test_layout_failure_publishes_only_current_bound_diagnostics(self) -> None:
        for tamper in (None, "old-report", "old-run", "artifact-hash", "input-hash", "escape", "symlink", "hardlink", "stale"):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = _mark(root)
                intent, _ = write_intent(root, part="part", feature_owners={"part-body": "part"})
                _localize_profile(intent, root)
                source = root / "build.py"
                source.write_text("# failed packing fixture\n")
                old_pointer = root / "part_render.json"
                old_pointer.write_text("previous successful render")

                class CandidateRunner:
                    def __init__(self, log_path):
                        self.log_path = log_path

                    def run(self, stage, argv, **kwargs):
                        self.log_path.write_text("layout failure log\n")
                        environment = kwargs["env_extra"]
                        run_id = environment["AMAGINE3D_COMPILE_RUN_ID"]
                        scene = _write_scene(root, intent)
                        candidate = {
                            "runId": run_id, "manufacturingValidated": False,
                            "inputBindings": {name: _bound(path) for name, path in (("intent", intent), ("source", source), ("scene", scene), ("profile", root / "printer-profile.json"))},
                            "artifacts": {},
                        }
                        for kind, suffix in (("step", ".step"), ("glb", ".glb"), ("preview", ".png")):
                            path = root / f"part-{run_id}-diagnostic{suffix}"
                            path.write_bytes(b"diagnostic geometry fixture")
                            candidate["artifacts"][kind] = _bound(path)
                        preview = Path(candidate["artifacts"]["preview"]["path"])
                        if tamper == "old-run":
                            candidate["runId"] = "older-run"
                        elif tamper == "artifact-hash":
                            candidate["artifacts"]["preview"]["sha256"] = "0" * 64
                        elif tamper == "input-hash":
                            candidate["inputBindings"]["source"]["sha256"] = "0" * 64
                        elif tamper == "escape":
                            candidate["artifacts"]["preview"] = _bound(Path(__file__))
                        elif tamper == "symlink":
                            preview.unlink()
                            preview.symlink_to(source)
                            candidate["artifacts"]["preview"]["sha256"] = sha256(source.read_bytes()).hexdigest()
                        elif tamper == "hardlink":
                            os.link(preview, root / "duplicate.png")
                        elif tamper == "stale":
                            os.utime(preview, (1, 1))
                        elif tamper == "old-report":
                            # A failed source can leave an old report alongside
                            # its new diagnostic bundle. It must not suppress
                            # independently verified evidence from this run.
                            (root / "part_report.json").write_text(json.dumps({
                                "schema": cad_compile.BUILD_SCHEMA, "runId": "older-run",
                                "part": "part", "pass": True, "artifacts": {},
                            }))
                        payload = {"schema": cad_compile.SOURCE_DIAGNOSTICS_SCHEMA, "runId": run_id, "pass": False,
                                   "issues": [{"code": "SOURCE.PLATE_LAYOUT_FAILED", "message": "single-plate heuristic found no placement", "severity": "error"}],
                                   "diagnosticCandidate": candidate}
                        Path(environment["AMAGINE3D_SOURCE_DIAGNOSTICS_PATH"]).write_text(json.dumps(payload))
                        return CommandResult(returncode=1, elapsed_ms=1, output_tail="layout failed")

                result = compile_cad(CompileOptions(workspace=root, marker=marker, intent=intent, scene=Path("part_scene.json"), source=source, output_dir=Path(".")), runner_factory=CandidateRunner)
                self.assertFalse(result["pass"])
                self.assertFalse(result["deliveryReady"])
                self.assertNotIn("buildReport", result["artifacts"])
                self.assertNotIn("renderEvidence", result["artifacts"])
                self.assertEqual(old_pointer.read_text(), "previous successful render")
                if tamper and tamper != "old-report":
                    self.assertNotIn("diagnosticPreview", result["artifacts"])
                    self.assertTrue(any(issue["code"] == "SOURCE.DIAGNOSTIC_PROVENANCE_INVALID" for issue in result["issues"]))
                else:
                    self.assertTrue({"diagnosticPreview", "diagnosticStep", "diagnosticGlb", "diagnosticSourceEvidence"} <= result["artifacts"].keys())
                    evidence = json.loads(Path(result["artifacts"]["diagnosticSourceEvidence"]["path"]).read_text())
                    self.assertFalse(evidence["manufacturingValidated"])
                    self.assertEqual(evidence["runId"], result["runId"])

    def test_source_cannot_mutate_immutable_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
            )
            _localize_profile(intent, root)
            source = root / "build.py"
            source.write_text(
                "from pathlib import Path\nPath('part_intent.json').write_text('{}')\n",
                encoding="utf-8",
            )
            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=Path("part_scene.json"),
                    source=source,
                    output_dir=Path("."),
                )
            )
            self.assertFalse(result["pass"])
            self.assertEqual(result["issues"][0]["code"], "CONTRACT.INTENT_MUTATED")
            self.assertFalse((root / "part_scene-validation.json").exists())

    def test_source_timeout_is_reported_without_attempting_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
            )
            _localize_profile(intent, root)
            source = root / "build.py"
            source.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
            progress_stream = io.StringIO()
            with contextlib.redirect_stderr(progress_stream):
                result = compile_cad(
                    CompileOptions(
                        workspace=root,
                        marker=marker,
                        intent=intent,
                        scene=Path("part_scene.json"),
                        source=source,
                        output_dir=Path("."),
                        source_timeout_seconds=0.02,
                    )
                )
            self.assertFalse(result["pass"])
            self.assertEqual(result["issues"][0]["code"], "SOURCE.TIMEOUT")
            progress = [
                json.loads(line)
                for line in progress_stream.getvalue().splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [event["status"] for event in progress],
                ["running", "timeout"],
            )
            full = json.loads(
                (root / "part_compile-result.json").read_text(encoding="utf-8")
            )
            self.assertEqual([stage["name"] for stage in full["stages"]], ["source"])

    def test_source_timeout_is_fail_closed_when_process_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, runner = _compile_with_zero_returncode_timeout(
                Path(directory),
                timed_out_stage="source",
            )

            self.assertFalse(result["pass"], result)
            self.assertEqual(
                [issue["code"] for issue in result["issues"]],
                ["SOURCE.TIMEOUT"],
            )
            self.assertEqual(runner.calls, ["source"])
            full = json.loads(
                Path(result["result"]["path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                full["stages"],
                [
                    {
                        "elapsedMs": 1,
                        "name": "source",
                        "returnCode": 0,
                        "status": "timeout",
                    }
                ],
            )

    def test_render_timeout_is_fail_closed_when_process_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, runner = _compile_with_zero_returncode_timeout(
                Path(directory),
                timed_out_stage="render",
            )

            self.assertFalse(result["pass"], result)
            self.assertNotIn("freshness", runner.calls)
            self.assertEqual(
                [issue["code"] for issue in result["issues"]],
                ["VISUAL.RENDER_TIMEOUT"],
            )
            self.assertNotIn("preview", result["artifacts"])
            self.assertNotIn("referencePreview", result["artifacts"])
            self.assertNotIn("renderEvidence", result["artifacts"])
            full = json.loads(
                Path(result["result"]["path"]).read_text(encoding="utf-8")
            )
            render_stage = next(
                stage for stage in full["stages"] if stage["name"] == "render"
            )
            self.assertEqual(render_stage["returnCode"], 0)
            self.assertEqual(render_stage["status"], "timeout")

    def test_freshness_timeout_is_fail_closed_when_process_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_render = Path(directory) / "part_render.json"
            previous_render.write_bytes(b"previous successful render pointer")
            result, _ = _compile_with_zero_returncode_timeout(
                Path(directory),
                timed_out_stage="freshness",
            )

            self.assertFalse(result["pass"], result)
            self.assertEqual(
                [issue["code"] for issue in result["issues"]],
                ["FRESHNESS.TIMEOUT"],
            )
            self.assertNotIn("freshnessAudit", result["artifacts"])
            self.assertNotIn("renderEvidence", result["artifacts"])
            self.assertIn("diagnosticPreview", result["artifacts"])
            self.assertEqual(previous_render.read_bytes(), b"previous successful render pointer")
            full = json.loads(
                Path(result["result"]["path"]).read_text(encoding="utf-8")
            )
            freshness_stage = next(
                stage for stage in full["stages"] if stage["name"] == "freshness"
            )
            self.assertEqual(freshness_stage["returnCode"], 0)
            self.assertEqual(freshness_stage["status"], "timeout")

    def test_aggregate_deadline_clamps_later_stage_and_stops_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )
            clock = _FakeClock()
            holder = {}

            def factory(log_path):
                runner = _BudgetRunner(
                    log_path,
                    report_path,
                    report,
                    clock=clock,
                )
                holder["runner"] = runner
                return runner

            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=scene,
                    source=source,
                    output_dir=Path("."),
                    compile_timeout_seconds=5,
                    source_timeout_seconds=4,
                    check_timeout_seconds=10,
                ),
                runner_factory=factory,
                monotonic=clock,
            )

            self.assertFalse(result["pass"], result)
            self.assertEqual(
                [issue["code"] for issue in result["issues"]],
                ["COMPILE.DEADLINE_EXCEEDED"],
            )
            self.assertEqual(holder["runner"].calls, ["source", "build-check"])
            self.assertEqual(holder["runner"].timeouts[0], ("source", 4))
            self.assertEqual(holder["runner"].timeouts[1][0], "build-check")
            self.assertAlmostEqual(holder["runner"].timeouts[1][1], 2)
            full = json.loads(Path(result["result"]["path"]).read_text())
            self.assertEqual(full["stages"][-1]["status"], "timeout")
            self.assertEqual(full["stages"][-1]["timeoutScope"], "compile")

    def test_freshness_evidence_must_bind_exact_marker_and_artifact_set(self) -> None:
        for invalidity in (
            "wrong-marker",
            "missing-artifact",
            "false-freshness",
            "deleted-artifact",
            "deleted-marker",
            "wrong-mtime",
            "wrong-size",
            "wrong-sha256",
            "changed-after-check",
        ):
            with self.subTest(invalidity=invalidity), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = _mark(root)
                intent, _ = write_intent(
                    root,
                    part="part",
                    feature_owners={"part-body": "part"},
                    dimensions_mm=(40, 30, 20),
                )
                _localize_profile(intent, root)
                scene = _write_scene(root, intent)
                source = root / "build.py"
                source.write_text("# fake source is handled by the injected runner\n")
                report_path, report = _minimal_report(
                    root,
                    intent_path=intent,
                    scene_path=scene,
                    source_path=source,
                )
                old_audit = root / "part_freshness-audit.json"
                old_audit.write_bytes(b"previous valid audit\n")

                result = compile_cad(
                    CompileOptions(
                        workspace=root,
                        marker=marker,
                        intent=intent,
                        scene=scene,
                        source=source,
                        output_dir=Path("."),
                    ),
                    runner_factory=lambda log_path: _InvalidFreshnessRunner(
                        log_path,
                        report_path,
                        report,
                        invalidity=invalidity,
                    ),
                )

                self.assertFalse(result["pass"], result)
                self.assertEqual(result["issues"][-1]["code"], "FRESHNESS.CHECK_FAILED")
                self.assertNotIn("freshnessAudit", result["artifacts"])
                self.assertEqual(old_audit.read_bytes(), b"previous valid audit\n")

    def test_interrupted_render_publication_preserves_old_evidence_and_cleans_run_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )
            render_audit = root / "part_render.json"
            render_audit.write_bytes(b"previous render evidence\n")
            moves = 0

            def interrupt_second_move(source_path: Path, destination: Path) -> None:
                nonlocal moves
                if moves == 1:
                    raise OSError("injected render publication interruption")
                source_path.replace(destination)
                moves += 1

            with mock.patch.object(
                cad_compile,
                "_replace_file",
                side_effect=interrupt_second_move,
            ):
                result = compile_cad(
                    CompileOptions(
                        workspace=root,
                        marker=marker,
                        intent=intent,
                        scene=scene,
                        source=source,
                        output_dir=Path("."),
                    ),
                    runner_factory=lambda log_path: _PassingRunner(
                        log_path,
                        report_path,
                        report,
                    ),
                )

            self.assertFalse(result["pass"], result)
            self.assertEqual(render_audit.read_bytes(), b"previous render evidence\n")
            self.assertNotIn("renderEvidence", result["artifacts"])
            self.assertEqual(list(root.glob(f"part_{result['runId']}_*.png")), [])

    def test_configuration_failure_payload_has_canonical_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                returncode = main(
                    [
                        "scene.json",
                        "--marker",
                        "marker",
                        "--intent",
                        "intent.json",
                        "--source",
                        "source.py",
                        "--workspace",
                        directory,
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(returncode, 1)
            self.assertEqual(str(UUID(payload["runId"])), payload["runId"])
            self.assertEqual(payload["issues"][0]["code"], "CONFIG.INVALID")

    @unittest.skipUnless(os.name == "posix", "POSIX process-group regression")
    def test_stage_timeout_terminates_nested_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descendant_pid_path = root / "descendant.pid"
            descendant_ready = root / "descendant.ready"
            descendant = root / "descendant.py"
            descendant.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import os, signal, sys, time",
                        "Path(sys.argv[1]).write_text(str(os.getpid()))",
                        "Path(sys.argv[2]).write_text('ready')",
                        "signal.signal(signal.SIGTERM, lambda *_: None)",
                        "while True: time.sleep(0.05)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            child_pid_path = root / "child.pid"
            child = root / "child.py"
            child.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import os, subprocess, sys, time",
                        "Path(sys.argv[1]).write_text(str(os.getpid()))",
                        "subprocess.Popen([sys.executable, *sys.argv[2:]])",
                        "while True: time.sleep(0.05)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            runner = CommandRunner(root / "compile.log")
            result = runner.run(
                "nested-timeout",
                [
                    sys.executable,
                    str(child),
                    str(child_pid_path),
                    str(descendant),
                    str(descendant_pid_path),
                    str(descendant_ready),
                ],
                cwd=root,
                timeout_seconds=0.5,
            )
            self.assertTrue(result.timed_out, result)
            self.assertTrue(descendant_ready.is_file())
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
            self.assertTrue(_wait_until_not_running(child_pid))
            self.assertTrue(_wait_until_not_running(descendant_pid))

    @unittest.skipUnless(os.name == "posix", "POSIX signal-race regression")
    def test_outer_termination_during_spawn_reaps_nested_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_pid_path = root / "child.pid"
            descendant_pid_path = root / "descendant.pid"
            descendant_ready = root / "descendant.ready"
            descendant_survived = root / "descendant.survived"
            descendant = root / "descendant.py"
            descendant.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import os, sys, time",
                        "Path(sys.argv[1]).write_text(str(os.getpid()))",
                        "Path(sys.argv[2]).write_text('ready')",
                        "time.sleep(0.8)",
                        "Path(sys.argv[3]).write_text('survived')",
                        "time.sleep(30)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            child = root / "child.py"
            child.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import os, subprocess, sys, time",
                        "Path(sys.argv[1]).write_text(str(os.getpid()))",
                        "subprocess.Popen([sys.executable, *sys.argv[2:]])",
                        "time.sleep(30)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            harness = root / "harness.py"
            harness.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import os, signal, sys, time",
                        f"sys.path.insert(0, {str(SKILL)!r})",
                        "import cad_compile",
                        "original_popen = cad_compile.subprocess.Popen",
                        "ready = Path(sys.argv[2])",
                        "def racing_popen(*args, **kwargs):",
                        "    process = original_popen(*args, **kwargs)",
                        "    deadline = time.monotonic() + 5",
                        "    while not ready.is_file() and time.monotonic() < deadline:",
                        "        time.sleep(0.01)",
                        "    if not ready.is_file():",
                        "        process.kill()",
                        "        raise RuntimeError('nested descendant did not start')",
                        "    os.kill(os.getpid(), signal.SIGTERM)",
                        "    time.sleep(0.05)",
                        "    return process",
                        "cad_compile.subprocess.Popen = racing_popen",
                        "cad_compile.CommandRunner(Path(sys.argv[1])).run(",
                        "    'signal-race', sys.argv[3:],",
                        "    cwd=Path.cwd(), timeout_seconds=30,",
                        ")",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(harness),
                str(root / "compile.log"),
                str(descendant_ready),
                sys.executable,
                str(child),
                str(child_pid_path),
                str(descendant),
                str(descendant_pid_path),
                str(descendant_ready),
                str(descendant_survived),
            ]
            outer = subprocess.Popen(
                command,
                cwd=root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                self.assertEqual(outer.wait(timeout=10), 128 + signal.SIGTERM)
                self.assertTrue(descendant_ready.is_file())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
                time.sleep(1)
                self.assertFalse(descendant_survived.exists())
                self.assertTrue(_wait_until_not_running(child_pid))
                self.assertTrue(_wait_until_not_running(descendant_pid))
            finally:
                if outer.poll() is None:
                    outer.kill()
                    outer.wait(timeout=3)
                if child_pid_path.is_file():
                    try:
                        os.killpg(
                            int(child_pid_path.read_text(encoding="utf-8")),
                            signal.SIGKILL,
                        )
                    except ProcessLookupError:
                        pass

    def test_brep_source_runs_all_applicable_checks_and_stops_for_visual_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )
            holder = {}

            def factory(log_path):
                runner = _PassingRunner(log_path, report_path, report)
                holder["runner"] = runner
                return runner

            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=scene,
                    source=source,
                    output_dir=Path("."),
                ),
                runner_factory=factory,
            )
            self.assertTrue(result["pass"], result)
            self.assertEqual(result["status"], "awaiting-visual-review")
            self.assertFalse(result["deliveryReady"])
            self.assertTrue(result["visualReviewRequired"])
            self.assertEqual(
                holder["runner"].calls,
                [
                    "source",
                    "build-check",
                    "render",
                    "mesh-qa:part",
                    "step-qa:part",
                    "freshness",
                ],
            )
            full = json.loads(
                (root / "part_compile-result.json").read_text(encoding="utf-8")
            )
            self.assertIn("renderEvidence", full["artifacts"])
            self.assertIn("referencePreview", full["artifacts"])
            self.assertIn("freshnessAudit", full["artifacts"])
            self.assertNotIn("assemblyAudit", full["artifacts"])
            self.assertNotIn("colorAudit", full["artifacts"])

    def test_per_part_qa_issue_has_positive_non_simplifying_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )

            def factory(log_path):
                return _FailingMeshRunner(log_path, report_path, report)

            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=scene,
                    source=source,
                    output_dir=Path("."),
                ),
                runner_factory=factory,
            )
            issue = next(
                item for item in result["issues"] if item["code"] == "QA.MESH_FAILED"
            )
            self.assertEqual(issue["part"], "part")
            self.assertEqual(issue["check"], "minimum_wall_thickness")
            self.assertIn("preserve identity and function", issue["repairHint"])
            self.assertIn("never simplify or scale", issue["repairHint"])

    def test_failed_qa_keeps_current_diagnostic_and_previous_successful_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(root, part="part", feature_owners={"part-body": "part"}, dimensions_mm=(40, 30, 20))
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# generated by injected runner\n")
            report_path, report = _minimal_report(root, intent_path=intent, scene_path=scene, source_path=source)
            old_pointer = root / "part_render.json"
            old_pointer.write_bytes(b"last successful render")
            runner = None

            def factory(log_path):
                nonlocal runner
                runner = _FailingMeshRunner(log_path, report_path, report)
                return runner

            result = compile_cad(CompileOptions(workspace=root, marker=marker, intent=intent, scene=scene, source=source, output_dir=Path(".")), runner_factory=factory)
            self.assertFalse(result["pass"])
            self.assertFalse(result["deliveryReady"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(old_pointer.read_bytes(), b"last successful render")
            self.assertNotIn("preview", result["artifacts"])
            self.assertNotIn("renderEvidence", result["artifacts"])
            self.assertLess(runner.calls.index("render"), runner.calls.index("mesh-qa:part"))
            preview = result["artifacts"]["diagnosticPreview"]
            evidence = json.loads(Path(result["artifacts"]["diagnosticRenderEvidence"]["path"]).read_text())
            self.assertEqual(evidence["runId"], result["runId"])
            self.assertEqual(evidence["purpose"], "diagnostic")
            self.assertEqual(evidence["inputs"]["source"]["sha256"], sha256(source.read_bytes()).hexdigest())
            self.assertEqual(evidence["preview"]["sha256"], sha256(Path(preview["path"]).read_bytes()).hexdigest())
            self.assertTrue(any(issue["code"] == "QA.MESH_FAILED" for issue in result["issues"]))
            self.assertIn("diagnosticPreview", cad_compile._agent_summary(result)["artifacts"])

    def test_warning_overflow_keeps_fatal_evidence_and_previous_successful_render(self) -> None:
        class OverflowWarningsRunner(_FailingMeshRunner):
            def run(self, stage, argv, **kwargs):
                command = super().run(stage, argv, **kwargs)
                if stage == "build-check":
                    output = Path(argv[argv.index("--out") + 1])
                    payload = json.loads(output.read_text())
                    payload["warnings"] = [
                        f"advisory-{index}" for index in range(cad_compile.MAX_ISSUES)
                    ]
                    output.write_text(json.dumps(payload) + "\n")
                return command

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_pointer = root / "part_render.json"
            old_pointer.write_bytes(b"previous successful render")
            result, runner = _compile_with_render_fixture(root, OverflowWarningsRunner)

            self.assertIn("mesh-qa:part", runner.calls)
            self.assertEqual(result["omittedErrorCount"], 0)
            self.assertTrue(any(issue["severity"] == "error" for issue in result["issues"]))
            saved = json.loads(Path(result["result"]["path"]).read_text())
            self.assertGreater(len(saved["issueOccurrences"]), cad_compile.MAX_ISSUES)
            self.assertFalse(result["pass"], result)
            self.assertEqual(old_pointer.read_bytes(), b"previous successful render")
            self.assertIn("diagnosticPreview", result["artifacts"])
            self.assertNotIn("renderEvidence", result["artifacts"])
            self.assertNotIn("preview", result["artifacts"])

    def test_changes_during_qa_cannot_promote_a_stale_render_binding(self) -> None:
        for target in ("glb", "preview", "reference-preview", "source", "diagnostic"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                old_pointer = root / "part_render.json"
                old_pointer.write_bytes(b"previous successful render")

                class MutatingRunner(_PassingRunner):
                    def run(self, stage, argv, **kwargs):
                        command = super().run(stage, argv, **kwargs)
                        if stage == "render":
                            self.render_outputs = {
                                "preview": Path(argv[argv.index("--out") + 1]).name,
                                "reference-preview": Path(
                                    argv[argv.index("--reference-out") + 1]
                                ).name,
                                "diagnostic": Path(argv[argv.index("--report") + 1]).name,
                            }
                        elif stage == "mesh-qa:part":
                            if target == "glb":
                                path = Path(self.report["artifacts"]["glb:display"]["path"])
                            elif target == "source":
                                path = Path(self.report["inputs"]["source"]["path"])
                            else:
                                path = self.report_path.parent / self.render_outputs[target]
                            if target == "diagnostic":
                                evidence = json.loads(path.read_text())
                                evidence["purpose"] = "changed-during-qa"
                                path.write_text(json.dumps(evidence) + "\n")
                            else:
                                path.write_bytes(path.read_bytes() + b"\nchanged during QA\n")
                        return command

                result, runner = _compile_with_render_fixture(root, MutatingRunner)

                self.assertLess(runner.calls.index("render"), runner.calls.index("mesh-qa:part"))
                self.assertFalse(result["pass"], result)
                self.assertEqual(old_pointer.read_bytes(), b"previous successful render")
                self.assertNotIn("renderEvidence", result["artifacts"])
                self.assertNotIn("preview", result["artifacts"])
                expected = ("BUILD.INPUT_CHANGED", "input-binding") if target == "source" else (
                    "VISUAL.RENDER_EVIDENCE_INVALID", "render")
                self.assertTrue(
                    any(
                        (issue["code"], issue["stage"]) == expected
                        for issue in result["issues"]
                    ),
                    result,
                )

    def test_render_metadata_failure_preserves_previous_pointer_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_pointer = root / "part_render.json"
            old_pointer.write_bytes(b"previous successful render")
            original_artifact = cad_compile._artifact
            metadata_failure_injected = False

            def fail_promoted_evidence_metadata(path):
                nonlocal metadata_failure_injected
                try:
                    payload = json.loads(path.read_text())
                except (OSError, UnicodeError, ValueError):
                    payload = None
                if isinstance(payload, dict) and payload.get("purpose") == "automated-checks-passed":
                    metadata_failure_injected = True
                    raise OSError("injected promoted render metadata read failure")
                return original_artifact(path)

            with mock.patch.object(
                cad_compile, "_artifact", side_effect=fail_promoted_evidence_metadata
            ):
                result, _ = _compile_with_render_fixture(root, _PassingRunner)

            self.assertTrue(metadata_failure_injected)
            self.assertFalse(result["pass"], result)
            self.assertEqual(old_pointer.read_bytes(), b"previous successful render")
            self.assertIn("diagnosticPreview", result["artifacts"])
            self.assertIn("diagnosticRenderEvidence", result["artifacts"])
            self.assertNotIn("renderEvidence", result["artifacts"])
            self.assertNotIn("preview", result["artifacts"])
            self.assertTrue(
                any(
                    issue["code"] == "VISUAL.RENDER_EVIDENCE_INVALID"
                    and issue["stage"] == "render"
                    for issue in result["issues"]
                ),
                result,
            )

    def test_json_checks_fail_closed_on_schema_and_success_contradictions(self) -> None:
        for invalidity in (
            "wrong-schema",
            "errors-with-pass",
            "error-issue-with-pass",
        ):
            with self.subTest(invalidity=invalidity), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = _mark(root)
                intent, _ = write_intent(
                    root,
                    part="part",
                    feature_owners={"part-body": "part"},
                    dimensions_mm=(40, 30, 20),
                )
                _localize_profile(intent, root)
                scene = _write_scene(root, intent)
                source = root / "build.py"
                source.write_text(
                    "# fake source is handled by the injected runner\n",
                    encoding="utf-8",
                )
                report_path, report = _minimal_report(
                    root,
                    intent_path=intent,
                    scene_path=scene,
                    source_path=source,
                )

                result = compile_cad(
                    CompileOptions(
                        workspace=root,
                        marker=marker,
                        intent=intent,
                        scene=scene,
                        source=source,
                        output_dir=Path("."),
                    ),
                    runner_factory=lambda log_path: _InvalidBuildAuditRunner(
                        log_path,
                        report_path,
                        report,
                        invalidity=invalidity,
                    ),
                )

                self.assertFalse(result["pass"], result)
                internal = [
                    issue
                    for issue in result["issues"]
                    if issue["stage"] == "build-check"
                    and issue["code"] == "INTERNAL.QA_ERROR"
                ]
                self.assertEqual(len(internal), 1, result)
                if invalidity == "wrong-schema":
                    self.assertNotIn("buildAudit", result["artifacts"])
                    self.assertEqual(
                        internal[0]["expected"],
                        "evidence-a3d-build-audit/v1",
                    )
                else:
                    self.assertIn("buildAudit", result["artifacts"])

    def test_identical_deterministic_qa_reports_are_fresh_per_compile_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )

            def factory(log_path):
                return _PassingRunner(log_path, report_path, report)

            options = CompileOptions(
                workspace=root,
                marker=marker,
                intent=intent,
                scene=scene,
                source=source,
                output_dir=Path("."),
            )
            first = compile_cad(options, runner_factory=factory)
            first_build_audit = Path(
                first["artifacts"]["buildAudit"]["path"]
            ).read_bytes()
            second = compile_cad(options, runner_factory=factory)
            second_build_audit = Path(second["artifacts"]["buildAudit"]["path"]).read_bytes()

            self.assertTrue(first["pass"], first)
            self.assertTrue(second["pass"], second)
            self.assertNotEqual(first["runId"], second["runId"])
            self.assertEqual(first_build_audit, second_build_audit)
            self.assertFalse(
                any(issue["code"] == "INTERNAL.QA_ERROR" for issue in second["issues"])
            )

    def test_structured_qa_issues_are_aggregated_without_legacy_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )

            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=marker,
                    intent=intent,
                    scene=scene,
                    source=source,
                    output_dir=Path("."),
                ),
                runner_factory=lambda log_path: _StructuredFailingMeshRunner(
                    log_path, report_path, report
                ),
            )

            structured = [
                issue for issue in result["issues"] if issue["stage"] == "mesh-qa:part"
            ]
            self.assertEqual(
                [issue["code"] for issue in structured],
                ["QA.MULTIPLE_COMPONENTS", "QA.THIN_WALL"],
            )
            self.assertEqual(structured[0]["componentCount"], 3)
            self.assertEqual(structured[1]["observed"], {"minimumMm": 0.5})
            self.assertNotIn("renderEvidence", result["artifacts"])

    def test_real_brep_pipeline_emits_fresh_automated_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = _mark(root)
            intent, _ = write_intent(
                root,
                part="part",
                feature_owners={"part-body": "part"},
                dimensions_mm=(40, 30, 20),
            )
            _localize_profile(intent, root)
            scene = _write_scene(root, intent)
            source = root / "build.py"
            source.write_text(
                "\n".join(
                    [
                        "from build123d import Align, Box",
                        "import os",
                        "from pathlib import Path",
                        "from cad_helpers import export_part, observe",
                        "shape = Box(40, 30, 20, align=(Align.MIN, Align.MIN, Align.MIN))",
                        "observe(shape, 'part-body', 'additive')",
                        "export_part(",
                        "    shape,",
                        "    'part',",
                        "    os.environ['AMAGINE3D_OUTPUT_DIR'],",
                        "    intent_path=os.environ['AMAGINE3D_INTENT_PATH'],",
                        "    scene_path=os.environ['AMAGINE3D_SCENE_PATH'],",
                        "    source_path=__file__,",
                        ")",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = compile_cad(
                CompileOptions(
                    workspace=root,
                    marker=None,
                    intent=intent,
                    scene=scene,
                    source=source,
                    output_dir=Path("."),
                    check_timeout_seconds=60,
                )
            )
            self.assertTrue(result["pass"], result)
            self.assertEqual(result["status"], "awaiting-visual-review")
            self.assertIn("freshnessAudit", result["artifacts"])
            freshness = json.loads(
                Path(result["artifacts"]["freshnessAudit"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(freshness["pass"], freshness)
            self.assertTrue(
                all(item["fresh"] for item in freshness["artifacts"]),
                freshness,
            )
            self.assertTrue(
                all(
                    {"mtime_ns", "sha256", "size", "stable"}.issubset(item)
                    for item in freshness["artifacts"]
                ),
                freshness,
            )
            self.assertNotIn(
                str((root / "part_compile.log").resolve()),
                {str(Path(item["path"]).resolve()) for item in freshness["artifacts"]},
            )


class WarningFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.intent = self.root / "intent.json"
        self.source = self.root / "build.py"
        self.intent.write_text('{"part":"cup"}')
        self.source.write_text("# test source")
        self.run_number = 0

    def payload(self, minimum=0.5, *, status="warning"):
        return {
            "schema": "evidence-mesh-audit/v3", "printer_profile": {"sha256": "profile"},
            "checks": [{
                "name": "printability_local_thin_region", "status": status,
                "expected": {"minimum_local_wall_mm": 2.0},
                "observed": {
                    "minimum_mm": minimum, "p05_mm": 2.5,
                    "violating_area_ratio": 0.02, "sample_count": 2048,
                    "affected_feature_ids": ["body", "cavity", "rim"],
                    "risk_bounds_mm": [[0, 0, 0], [82, 62, 95]],
                    "measurement_context": {
                        "method": "max-sphere-at-triangle-surface-points/v1",
                        "sample_limit": 2048,
                        "coordinate_frame": {
                            "name": "part-print", "status": "bound", "part": "cup",
                            "artifact_key": "stl:cup", "semantic_to_mesh": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                        },
                    },
                    "sampling": {
                        **{f"large-field-{i}": i for i in range(15)},
                        "method": "max-sphere-at-triangle-surface-points",
                        "minimum_sample": {"face_index": 21, "point_mm": [17.2, 33.5, 94.6],
                                           "semantic_point_mm": [17.2, 33.5, 94.6]},
                    },
                },
            }],
        }

    def run_measurement(self, payload, *, model="cup", part="cup", workspace=None):
        self.run_number += 1
        result = {
            "model": model, "runId": f"run-{self.run_number}", "issues": [],
            "inputs": {"intent": str(self.intent), "source": str(self.source),
                       "workspace": str(workspace or self.root)},
            "stages": [{"name": f"mesh-qa:{part}", "status": "pass"}],
        }
        if payload is not None:
            cad_compile._capture_thickness_measurements(result, payload, stage=f"mesh-qa:{part}", part=part)
        state_path = cad_compile._write_repair_state(result, result_path=self.root / "result.json")
        return result, json.loads(state_path.read_text())

    def test_compact_keeps_minimum_point_instead_of_losing_it_among_sampling_counts(self):
        observed = self.payload()["checks"][0]["observed"]
        issue = {"check": "printability_local_thin_region", "severity": "warning",
                 "code": "QA.WARNING", "part": "cup", "observed": observed,
                 "message": "thin region"}
        original = json.dumps(issue)
        summary = cad_compile._agent_summary({"issues": [issue]})
        witness = summary["issues"][0]["witness"]
        self.assertEqual(witness["pointMm"], [17.2, 33.5, 94.6])
        self.assertEqual(witness["coordinateFrame"], "part-print")
        self.assertEqual(witness["frameStatus"], "bound")
        self.assertEqual(witness["semanticPointMm"], [17.2, 33.5, 94.6])
        self.assertEqual(witness["minimumMm"], 0.5)
        self.assertEqual(witness["featureCandidates"]["ids"], ["body", "cavity", "rim"])
        self.assertIn("not root causes", witness["featureCandidates"]["basis"])
        self.assertLessEqual(len(cad_compile._summary_json(summary)), 12000)
        self.assertEqual(json.dumps(issue), original)
        observed["affected_feature_ids"] = [f"candidate-{i}" for i in range(20)]
        limited = cad_compile._agent_summary({"issues": [issue]})
        self.assertEqual(limited["issues"][0]["witness"]["pointMm"], [17.2, 33.5, 94.6])
        self.assertTrue(limited["diagnostics"]["truncated"])

    def test_measured_delta_labels_changed_samples_without_claiming_cause(self):
        self.run_measurement(self.payload(0.02))
        changed = self.payload(0.004)
        changed["checks"][0]["observed"]["sampling"]["large-field-0"] = 99
        changed["checks"][0]["observed"]["sampling"]["minimum_sample"]["point_mm"] = [17.2, 33.5, 94.7]
        result, state = self.run_measurement(changed)
        comparison = result["warningComparisons"][0]
        self.assertEqual(comparison["status"], "measured")
        self.assertEqual(comparison["measurements"]["minimum_mm"], [0.02, 0.004, -0.016])
        self.assertTrue(comparison["changes"]["samplingChanged"])
        self.assertTrue(comparison["changes"]["riskLocationChanged"])
        self.assertIn("not causal", comparison["basis"])
        self.assertEqual(state["failed"], [])
        self.assertEqual(result["repairDelta"]["new"], [])
        result["issues"] = [{
            "code": "QA.WARNING", "severity": "warning", "part": "cup",
            "check": "printability_local_thin_region", "message": "thin region",
            "observed": {**changed["checks"][0]["observed"],
                         "bulkyEvidence": {str(i): "x" * 5000 for i in range(30)}},
        }]
        result["artifacts"] = {"preview": {"path": "/tmp/" + "p" * 20000}}
        compact = cad_compile._agent_summary(result)
        self.assertEqual(compact["warningComparisons"][0]["measurements"]["minimum_mm"], [0.02, 0.004, -0.016])
        self.assertEqual(compact["issues"][0]["witness"]["pointMm"], [17.2, 33.5, 94.7])
        self.assertLessEqual(len(cad_compile._summary_json(compact)), 12000)

    def test_unmeasured_warning_survives_until_an_actual_check_pass(self):
        self.run_measurement(self.payload())
        skipped = self.payload(status="not_evaluated")
        result, state = self.run_measurement(skipped)
        self.assertEqual(result["warningComparisons"][0]["status"], "not_remeasured")
        self.assertEqual(state["warningMeasurements"][0]["runId"], "run-1")
        self.assertEqual(result["repairDelta"]["resolved"], [])
        result, _ = self.run_measurement(self.payload(2.5, status="pass"))
        comparison = result["warningComparisons"][0]
        self.assertEqual(comparison["status"], "measured")
        self.assertEqual(comparison["previousRunId"], "run-1")
        self.assertEqual(comparison["changes"]["currentCheckStatus"], "pass")
        self.assertEqual(comparison["measurements"]["minimum_mm"], [0.5, 2.5, 2.0])

    def test_changed_frame_method_target_profile_or_sampling_policy_is_not_comparable(self):
        for field in ("rotation", "matrix_scale", "small_anisotropic_scale", "scale_metadata", "units", "missing_matrix",
                      "invalid_matrix", "missing_artifact", "missing_frame", "old_missing_matrix",
                      "malformed_context", "malformed_frame", "method", "sample_limit", "target", "profile", "unbound"):
            with self.subTest(field=field):
                previous = self.payload()
                if field == "old_missing_matrix":
                    del previous["checks"][0]["observed"]["measurement_context"]["coordinate_frame"]["semantic_to_mesh"]
                self.run_measurement(previous)
                changed = self.payload(0.1)
                context = changed["checks"][0]["observed"]["measurement_context"]
                frame = context["coordinate_frame"]
                if field == "rotation":
                    frame["semantic_to_mesh"] = [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
                elif field == "matrix_scale":
                    frame["semantic_to_mesh"][0][0] = 2
                elif field == "small_anisotropic_scale":
                    frame["semantic_to_mesh"][0][0] = 1.000001
                    frame["semantic_to_mesh"][1][1] = 1 / 1.000001
                elif field == "scale_metadata":
                    frame["scale"] = 2
                elif field == "units":
                    frame["units"] = "m"
                elif field == "missing_matrix":
                    del frame["semantic_to_mesh"]
                elif field == "invalid_matrix":
                    frame["semantic_to_mesh"][0][3] = float("nan")
                elif field == "missing_artifact":
                    del frame["artifact_key"]
                elif field == "missing_frame":
                    del context["coordinate_frame"]
                elif field == "malformed_context":
                    changed["checks"][0]["observed"]["measurement_context"] = ["invalid"]
                elif field == "malformed_frame":
                    context["coordinate_frame"] = ["invalid"]
                elif field == "unbound":
                    frame["status"] = "unbound"
                elif field in {"method", "sample_limit"}:
                    context[field] = "different"
                elif field == "target":
                    changed["checks"][0]["expected"]["minimum_local_wall_mm"] = 1.0
                elif field == "profile":
                    changed["printer_profile"]["sha256"] = "other-profile"
                result, _ = self.run_measurement(changed)
                comparison = result["warningComparisons"][0]
                self.assertEqual(comparison["status"], "not_comparable")
                self.assertIsNone(comparison["measurements"]["minimum_mm"][2])
                self.assertTrue(comparison["changes"]["notComparableReasons"])
                self.assertFalse(comparison["changes"]["translationNormalized"])
                self.assertIsNone(comparison["changes"]["riskLocationChanged"])

    def translate_payload(self, payload, offset):
        observed = payload["checks"][0]["observed"]
        matrix = observed["measurement_context"]["coordinate_frame"]["semantic_to_mesh"]
        point = observed["sampling"]["minimum_sample"]["point_mm"]
        for axis, distance in enumerate(offset):
            matrix[axis][3] += distance
            point[axis] += distance
            for bound in observed["risk_bounds_mm"]:
                bound[axis] += distance

    def test_print_translation_preserves_scalar_comparison_and_semantic_risk_location(self):
        self.run_measurement(self.payload(0.02))
        changed = self.payload(0.004)
        self.translate_payload(changed, [-0.00745, -0.00440, 3.0])
        result, _ = self.run_measurement(changed)
        comparison = result["warningComparisons"][0]
        self.assertEqual(comparison["status"], "measured")
        self.assertEqual(comparison["measurements"]["minimum_mm"], [0.02, 0.004, -0.016])
        self.assertTrue(comparison["changes"]["coordinateFrameChanged"])
        self.assertTrue(comparison["changes"]["translationNormalized"])
        self.assertFalse(comparison["changes"]["measurementBasisChanged"])
        self.assertFalse(comparison["changes"]["riskLocationChanged"])
        self.assertEqual(comparison["changes"]["notComparableReasons"], [])
        compact = cad_compile._agent_summary(result)
        self.assertEqual(compact["warningComparisons"], result["warningComparisons"])
        self.assertLessEqual(len(cad_compile._summary_json(compact)), 12000)

    def test_translation_with_same_nonidentity_rotation_uses_semantic_point_and_bounds(self):
        previous = self.payload()
        observed = previous["checks"][0]["observed"]
        observed["measurement_context"]["coordinate_frame"]["semantic_to_mesh"] = [
            [0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 0], [0, 0, 0, 1]]
        observed["sampling"]["minimum_sample"]["point_mm"] = [-23.5, 37.2, 94.6]
        observed["risk_bounds_mm"] = [[-52, 20, 0], [10, 102, 95]]
        first, _ = self.run_measurement(previous)
        record = first["warningMeasurements"][0]
        semantic = cad_compile._warning_semantic_risk(record, cad_compile._warning_coordinate_matrix(record))
        for actual, expected in zip(semantic, [17.2, 33.5, 94.6, 0, 0, 0, 82, 62, 95]):
            self.assertAlmostEqual(actual, expected)
        changed = json.loads(json.dumps(previous))
        self.translate_payload(changed, [13, -7, 2])
        result, _ = self.run_measurement(changed)
        self.assertEqual(result["warningComparisons"][0]["status"], "measured")
        self.assertFalse(result["warningComparisons"][0]["changes"]["riskLocationChanged"])
        self.assertTrue(result["warningComparisons"][0]["changes"]["translationNormalized"])

    def test_missing_or_invalid_risk_location_is_unknown_without_hiding_scalar_measurements(self):
        for point in (None, [1, 2], [float("nan"), 2, 3]):
            with self.subTest(point=point):
                self.run_measurement(self.payload())
                changed = self.payload(0.1)
                changed["checks"][0]["observed"]["sampling"]["minimum_sample"]["point_mm"] = point
                result, _ = self.run_measurement(changed)
                comparison = result["warningComparisons"][0]
                self.assertEqual(comparison["status"], "measured")
                self.assertEqual(comparison["measurements"]["minimum_mm"], [0.5, 0.1, -0.4])
                self.assertIsNone(comparison["changes"]["riskLocationChanged"])

    def test_no_cross_model_intent_part_or_workspace_comparison(self):
        self.run_measurement(self.payload())
        result, _ = self.run_measurement(self.payload(), model="other")
        self.assertEqual(result["warningComparisons"][0]["status"], "first_measurement")
        self.intent.write_text('{"part":"cup","target":"changed"}')
        result, _ = self.run_measurement(self.payload())
        self.assertEqual(result["warningComparisons"][0]["status"], "first_measurement")
        result, _ = self.run_measurement(self.payload(), part="other")
        self.assertTrue(all(item["status"] != "measured" for item in result["warningComparisons"]))
        other = self.root / "other-workspace"
        other.mkdir()
        result, _ = self.run_measurement(self.payload(), workspace=other)
        self.assertEqual(result["warningComparisons"][0]["status"], "first_measurement")


class InputProvenanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.intent, _ = write_intent(self.root, part="part", feature_owners={"part-body": "part"},
                                      dimensions_mm=(40, 30, 20))
        _localize_profile(self.intent, self.root)
        self.scene = _write_scene(self.root, self.intent)
        self.source = self.root / "build.py"
        self.source.write_text("# test runner supplies real protocol outputs\n")
        self.profile = self.root / "printer-profile.json"
        self.report_path, self.report = _minimal_report(
            self.root, intent_path=self.intent, scene_path=self.scene, source_path=self.source)

    def compile(self, *, marker=None, runner_class=_PassingRunner):
        self.runner = runner_class(self.root / "part_compile.log", self.report_path, self.report)
        return compile_cad(CompileOptions(workspace=self.root, marker=marker, intent=self.intent,
                                         scene=self.scene, source=self.source, output_dir=Path(".")),
                           runner_factory=lambda _: self.runner)

    def test_old_unchanged_inputs_compile_without_marker_or_with_late_legacy_marker(self):
        before = {p: cad_compile._current_file_binding(p) for p in (self.intent, self.source, self.profile)}
        for marker in (None, self.root / ".legacy-marker"):
            with self.subTest(marker=marker):
                if marker is not None:
                    marker.write_text("legacy provenance only")
                    future = max(p.stat().st_mtime_ns for p in before) + 1_000_000_000
                    os.utime(marker, ns=(future, future))
                result = self.compile(marker=marker)
                self.assertTrue(result["pass"], result)
                full = json.loads((self.root / "part_compile-result.json").read_text())
                self.assertEqual(full["inputs"]["marker"], str(marker.resolve()) if marker else None)
                self.assertNotEqual(full["inputs"]["attemptMarker"], full["inputs"]["marker"])
                for name, path in (("source", self.source), ("intent", self.intent), ("profile", self.profile)):
                    self.assertEqual(full["inputBindings"][name], {"path": str(path.resolve()), **before[path]})
                    self.assertEqual(cad_compile._current_file_binding(path), before[path])

    def test_source_rewrite_cannot_rebind_report_to_different_executed_bytes(self):
        source = self.source
        before = _bound(source)

        class MutatingRunner(_PassingRunner):
            def run(self, stage, argv, **kwargs):
                if stage == "source":
                    source.write_text("# replaced after the source began\n")
                    self.report["inputs"]["source"] = _bound(source, schema="python-source/v1")
                return super().run(stage, argv, **kwargs)

        result = self.compile(runner_class=MutatingRunner)
        self.assertFalse(result["pass"])
        self.assertIn("BUILD.INPUT_CHANGED", [x["code"] for x in result["issues"]])
        self.assertNotIn("build-check", self.runner.calls)
        full = json.loads((self.root / "part_compile-result.json").read_text())
        self.assertEqual(full["inputBindings"]["source"]["sha256"], before["sha256"])

    def test_intent_changed_after_validation_is_rejected_before_lineage_and_source(self):
        validate = cad_compile.validate_intent

        def validate_then_change(*args, **kwargs):
            errors = validate(*args, **kwargs)
            self.assertEqual(errors, [])
            self.intent.write_bytes(self.intent.read_bytes() + b"\n")
            return errors

        with mock.patch.object(cad_compile, "validate_intent", side_effect=validate_then_change), \
                mock.patch.object(cad_compile, "audit_lineage") as audit:
            result = self.compile()
        self.assertFalse(result["pass"])
        issue = next(item for item in result["issues"] if item["code"] == "CONTRACT.INTENT_MUTATED")
        self.assertEqual(issue["stage"], "input-binding")
        full = json.loads((self.root / "part_compile-result.json").read_text())
        self.assertFalse(full["intentRevision"]["verified"])
        self.assertEqual(self.runner.calls, [])
        audit.assert_not_called()

    def test_input_changes_during_final_freshness_cannot_publish(self):
        for name in ("source", "profile"):
            with self.subTest(input=name):
                path = getattr(self, name)
                original = path.read_bytes()
                pointer = self.root / "part_render.json"
                pointer.write_text("previous successful preview")

                class MutatingRunner(_PassingRunner):
                    def run(self, stage, argv, **kwargs):
                        result = super().run(stage, argv, **kwargs)
                        if stage == "freshness":
                            path.write_bytes(original + b"\n")
                        return result

                result = self.compile(runner_class=MutatingRunner)
                self.assertFalse(result["pass"])
                self.assertIn("BUILD.INPUT_CHANGED", [x["code"] for x in result["issues"]])
                self.assertEqual(pointer.read_text(), "previous successful preview")
                path.write_bytes(original)

    def test_public_cli_without_marker_reaches_selected_source(self):
        self.source.write_text("raise RuntimeError('selected source reached')\n")
        command = subprocess.run(["node", str(ROOT / "bin/a3d.mjs"), "compile", self.scene.name,
                                  "--intent", self.intent.name, "--source", self.source.name],
                                 cwd=self.root, text=True, capture_output=True, timeout=30)
        self.assertEqual(command.returncode, 1)
        full = json.loads((self.root / "part_compile-result.json").read_text())
        self.assertIsNone(full["inputs"]["marker"])
        self.assertIn("SOURCE.EXECUTION_FAILED", [x["code"] for x in full["issues"]])
        self.assertIn("selected source reached", (self.root / "part_compile.log").read_text())


if __name__ == "__main__":
    unittest.main()
