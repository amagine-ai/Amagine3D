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


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from cad_compile import (  # noqa: E402
    CommandResult,
    CommandRunner,
    CompileOptions,
    ConfigurationError,
    compile_cad,
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
                        "recipe": {
                            "kind": "sourceMesh" if master == "mesh" else "roundedBox",
                            "parameters": (
                                {"sourceMesh": "part-source.stl"}
                                if master == "mesh"
                                else {"sizeMm": [40, 30, 20], "radiusMm": 1}
                            ),
                        },
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
            "intent": _bound(intent_path, schema="evidence-cad-intent/v4"),
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

    def run(self, stage, argv, *, cwd, timeout_seconds, env_extra=None):
        self.calls.append(stage)
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(f"{stage}\n")
        if stage == self.report_stage:
            payload = {**self.report, "runId": "current-source-run"}
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
            payload = {
                "pass": True,
                "marker": marker,
                "marker_mtime_ns": 1,
                "artifacts": [
                    {
                        "path": path,
                        "exists": True,
                        "fresh": True,
                        "mtime_ns": 2,
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
                json.dumps({"errors": [], "pass": True, "warnings": []}) + "\n",
                encoding="utf-8",
            )
        return CommandResult(returncode=0, elapsed_ms=1, output_tail="")


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
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return CommandResult(returncode=1, elapsed_ms=1, output_tail="")


class CadCompileTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
