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


def _write_scene(root: Path, intent_path: Path, *, master: str = "brep") -> Path:
    geometry = root / "part-source.stl"
    if master == "mesh" and not geometry.exists():
        geometry.write_bytes(b"solid part\nendsolid part\n")
    recipe = (
        {
            "kind": "meshGeometry",
            "parameters": {
                "geometry": {
                    "path": geometry.name,
                    "scale": 1.0,
                    "sha256": sha256(geometry.read_bytes()).hexdigest(),
                }
            },
        }
        if master == "mesh"
        else {
            "kind": "roundedBox",
            "parameters": {"sizeMm": [40, 30, 20], "radiusMm": 1},
        }
    )
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
                "parts": [{"id": "part", "representationMaster": master}],
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
        *,
        report_stage: str = "source",
    ):
        self.log_path = log_path
        self.log_path.write_text("fake runner\n", encoding="utf-8")
        self.report_path = report_path
        self.report = report
        self.report_stage = report_stage
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
        if stage == self.report_stage:
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
        report_stage: str = "source",
    ):
        super().__init__(
            log_path,
            report_path,
            report,
            report_stage=report_stage,
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
    master: str = "brep",
) -> tuple[dict, _ZeroReturncodeTimeoutRunner]:
    marker = _mark(root)
    intent, _ = write_intent(
        root,
        part="part",
        feature_owners={"part-body": "part"},
        dimensions_mm=(40, 30, 20),
    )
    _localize_profile(intent, root)
    scene = _write_scene(root, intent, master=master)
    source_mesh = root / "part-source.stl"
    if master == "mesh":
        source_mesh.write_bytes(b"solid part\nendsolid part\n")
    source = root / "build.py"
    source.write_text("# fake source is handled by the injected runner\n")
    report_path, report = _minimal_report(
        root,
        intent_path=intent,
        scene_path=scene,
        source_path=source,
    )
    report_stage = "source"
    if master == "mesh":
        report_stage = "backend-hybrid"
        report["backend"] = "hybrid-mesh"
        report["parts"]["part"]["representationMaster"] = "mesh"
        report["inputs"].pop("source")
        report["inputs"]["geometry"] = {
            "node:part-body-node": _bound(source_mesh, schema="mesh-source/v1")
        }
        report["artifacts"].pop("step:part")
    holder: dict[str, _ZeroReturncodeTimeoutRunner] = {}

    def factory(log_path: Path) -> _ZeroReturncodeTimeoutRunner:
        runner = _ZeroReturncodeTimeoutRunner(
            log_path,
            report_path,
            report,
            timed_out_stage=timed_out_stage,
            report_stage=report_stage,
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


class CadCompileTests(unittest.TestCase):
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
                ["source", "build-check", "mesh-qa:part", "step-qa:part"],
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
        self.assertEqual(
            select_backend({"parts": [{"representationMaster": "mesh"}]}),
            "hybrid",
        )
        self.assertEqual(
            select_backend(
                {
                    "parts": [
                        {"representationMaster": "brep"},
                        {"representationMaster": "mesh"},
                    ]
                }
            ),
            "hybrid",
        )

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

    def test_hybrid_timeout_is_fail_closed_when_process_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, runner = _compile_with_zero_returncode_timeout(
                Path(directory),
                timed_out_stage="backend-hybrid",
                master="mesh",
            )

            self.assertFalse(result["pass"], result)
            self.assertEqual(
                [issue["code"] for issue in result["issues"]],
                ["BACKEND.TIMEOUT"],
            )
            self.assertEqual(runner.calls, ["source", "backend-hybrid"])
            self.assertNotIn("buildReport", result["artifacts"])
            full = json.loads(
                Path(result["result"]["path"]).read_text(encoding="utf-8")
            )
            hybrid_stage = next(
                stage
                for stage in full["stages"]
                if stage["name"] == "backend-hybrid"
            )
            self.assertEqual(hybrid_stage["returnCode"], 0)
            self.assertEqual(hybrid_stage["status"], "timeout")

    def test_render_timeout_is_fail_closed_when_process_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, runner = _compile_with_zero_returncode_timeout(
                Path(directory),
                timed_out_stage="render",
            )

            self.assertFalse(result["pass"], result)
            self.assertIn("freshness", runner.calls)
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
                    "mesh-qa:part",
                    "step-qa:part",
                    "render",
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

    def test_mesh_scene_delegates_to_hybrid_without_claiming_step(self) -> None:
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
            scene = _write_scene(root, intent, master="mesh")
            (root / "part-source.stl").write_bytes(
                b"solid part\nendsolid part\n"
            )
            source = root / "build.py"
            source.write_text("# fake source is handled by the injected runner\n")
            report_path, report = _minimal_report(
                root,
                intent_path=intent,
                scene_path=scene,
                source_path=source,
            )
            report["backend"] = "hybrid-mesh"
            report["parts"]["part"]["representationMaster"] = "mesh"
            report["inputs"].pop("source")
            report["inputs"]["geometry"] = {
                "node:part-body-node": _bound(
                    root / "part-source.stl", schema="mesh-source/v1"
                )
            }
            report["artifacts"].pop("step:part")
            holder = {}

            def factory(log_path):
                runner = _PassingRunner(
                    log_path,
                    report_path,
                    report,
                    report_stage="backend-hybrid",
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
            self.assertTrue(result["pass"], result)
            self.assertEqual(result["backend"], "hybrid")
            self.assertEqual(
                holder["runner"].calls,
                [
                    "source",
                    "backend-hybrid",
                    "build-check",
                    "mesh-qa:part",
                    "render",
                    "freshness",
                ],
            )

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
                    marker=marker,
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


if __name__ == "__main__":
    unittest.main()
