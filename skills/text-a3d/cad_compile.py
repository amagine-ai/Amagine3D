"""Compile and audit one text-a3d semantic scene without changing geometry.

This command is deliberately an orchestrator, not a geometry author or workflow
engine.  It executes the Agent-authored Python source without a shell, validates
the immutable intent and generated semantic scene, selects the existing BRep or
Hybrid compiler path, then runs every applicable automated audit and a fresh
display render.  A successful result still requires the Agent to read the
render before delivery.

The subprocess boundary is path-scoped and time-bounded, but it is not an OS
sandbox: Agent-authored Python has the permissions of the Amagine3D process.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, Iterable

from intent_contract import validate as validate_intent
from scene_contract import validate as validate_scene
from source_preflight import audit as audit_source


RESULT_SCHEMA = "evidence-cad-compile-result/v1"
BUILD_SCHEMA = "evidence-a3d-build/v1"
MODEL_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
MAX_ISSUES = 40
MAX_MESSAGE_CHARS = 700
MAX_LOG_TAIL_BYTES = 32_000


class ConfigurationError(ValueError):
    """Raised when the requested compile escapes the declared workspace."""


@dataclass(frozen=True)
class CompileOptions:
    workspace: Path
    marker: Path
    intent: Path
    scene: Path
    source: Path
    output_dir: Path
    report: Path | None = None
    result: Path | None = None
    log: Path | None = None
    source_timeout_seconds: float = 1_800.0
    backend_timeout_seconds: float = 1_800.0
    check_timeout_seconds: float = 600.0
    consistency_samples: int = 1_024


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    elapsed_ms: int
    output_tail: str
    timed_out: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    return {"path": str(resolved), "sha256": _digest(resolved)}


def _write_json(path: Path, value: Any) -> None:
    payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"{label} cannot be read: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _workspace_path(
    workspace: Path,
    value: Path,
    label: str,
    *,
    must_exist: bool,
) -> Path:
    candidate = value if value.is_absolute() else workspace / value
    resolved = candidate.resolve()
    if not _inside(workspace, resolved):
        raise ConfigurationError(f"{label} must stay inside the session workspace")
    if must_exist and not resolved.is_file():
        raise ConfigurationError(f"{label} is not an existing file: {resolved}")
    return resolved


def _output_path(output_dir: Path, value: Path, label: str) -> Path:
    candidate = value if value.is_absolute() else output_dir / value
    resolved = candidate.resolve()
    if not _inside(output_dir, resolved):
        raise ConfigurationError(f"{label} must stay inside the output directory")
    if candidate.exists() and candidate.is_file() and candidate.stat().st_nlink > 1:
        raise ConfigurationError(f"{label} cannot be written through a hard link")
    return resolved


def _resolve_reference(reference: Any, base_dir: Path, label: str) -> Path:
    if not isinstance(reference, dict):
        raise ValueError(f"{label} must be an artifact reference")
    raw_path = reference.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label}.path is required")
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def select_backend(scene: dict[str, Any]) -> str:
    """Select an existing compiler path solely from canonical part masters."""

    parts = scene.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("scene parts must be a non-empty list")
    masters = {
        part.get("representationMaster")
        for part in parts
        if isinstance(part, dict)
    }
    if not masters.issubset({"brep", "mesh"}) or len(masters) == 0:
        raise ValueError("scene representation masters must be brep or mesh")
    return "hybrid" if "mesh" in masters else "brep-source"


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_posix_process_group(process: subprocess.Popen[Any]) -> None:
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + 3
    while _process_group_exists(process_group_id) and time.monotonic() < deadline:
        try:
            process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None:
            time.sleep(0.05)

    if _process_group_exists(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def _terminate_windows_process_tree(process: subprocess.Popen[Any]) -> None:
    taskkill = ["taskkill", "/PID", str(process.pid), "/T", "/F"]
    try:
        subprocess.run(
            taskkill,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if os.name == "posix":
        _terminate_posix_process_group(process)
    elif process.poll() is None:
        _terminate_windows_process_tree(process)


class CommandRunner:
    """Run argv-only subprocesses and retain complete output in one local log."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.write_text("", encoding="utf-8")

    def run(
        self,
        stage: str,
        argv: list[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        env_extra: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        print(
            json.dumps(
                {"schema": "cad-compile-progress/v1", "stage": stage, "status": "running"}
            ),
            file=sys.stderr,
            flush=True,
        )
        with self.log_path.open("ab") as output:
            offset = output.tell()
            header = (
                f"\n[{_utc_now()}] stage={stage}\n"
                f"argv={json.dumps(argv, ensure_ascii=False)}\n"
            )
            output.write(header.encode("utf-8"))
            output.flush()
            environment = os.environ.copy()
            environment.update(env_extra or {})
            environment["PYTHONNOUSERSITE"] = "1"
            previous_handlers: dict[int, Any] = {}
            process: subprocess.Popen[Any] | None = None
            abort_signum: int | None = None
            terminating = False

            def abort(signum: int, _frame: Any) -> None:
                nonlocal abort_signum, terminating
                abort_signum = signum
                if process is None or terminating:
                    return
                terminating = True
                try:
                    _terminate_process(process)
                finally:
                    terminating = False
                raise SystemExit(128 + signum)

            # Install outer termination handlers before spawning. If a signal
            # lands inside Popen, the handler records it without raising until
            # the new process group is known, then the group is reaped below.
            # This closes the start_new_session/handler-install orphan window.
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    previous_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, abort)
                except ValueError:
                    for installed, handler in previous_handlers.items():
                        signal.signal(installed, handler)
                    previous_handlers.clear()
                    break
            timed_out = False
            try:
                if abort_signum is not None:
                    raise SystemExit(128 + abort_signum)
                try:
                    process = subprocess.Popen(
                        argv,
                        cwd=str(cwd),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=os.name == "posix",
                        creationflags=(
                            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                            if os.name == "nt"
                            else 0
                        ),
                    )
                except OSError as error:
                    if abort_signum is not None:
                        raise SystemExit(128 + abort_signum) from error
                    output.write(f"spawn_error={error}\n".encode("utf-8"))
                    output.flush()
                    result = CommandResult(
                        returncode=None,
                        elapsed_ms=round((time.monotonic() - started) * 1_000),
                        output_tail=str(error),
                    )
                    print(
                        json.dumps(
                            {
                                "schema": "cad-compile-progress/v1",
                                "stage": stage,
                                "status": "fail",
                            }
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    return result
                if abort_signum is not None:
                    _terminate_process(process)
                    raise SystemExit(128 + abort_signum)
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                if process is not None:
                    _terminate_process(process)
                returncode = process.returncode
                output.write(
                    f"timeout_seconds={timeout_seconds:g}\n".encode("utf-8")
                )
            except BaseException:
                if process is not None:
                    _terminate_process(process)
                raise
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)
            output.flush()

        with self.log_path.open("rb") as source:
            source.seek(offset)
            chunk = source.read()
        if len(chunk) > MAX_LOG_TAIL_BYTES:
            chunk = chunk[-MAX_LOG_TAIL_BYTES:]
        result = CommandResult(
            returncode=returncode,
            elapsed_ms=round((time.monotonic() - started) * 1_000),
            output_tail=chunk.decode("utf-8", errors="replace"),
            timed_out=timed_out,
        )
        print(
            json.dumps(
                {
                    "elapsedMs": result.elapsed_ms,
                    "schema": "cad-compile-progress/v1",
                    "stage": stage,
                    "status": (
                        "timeout"
                        if result.timed_out
                        else "pass" if result.returncode == 0 else "fail"
                    ),
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return result


def _short_message(value: Any) -> str:
    message = str(value).strip() or "unspecified failure"
    if len(message) <= MAX_MESSAGE_CHARS:
        return message
    return message[: MAX_MESSAGE_CHARS - 1] + "…"


def _repair_hint(code: str) -> str:
    if code.startswith("CONTRACT.INTENT"):
        return (
            "Recreate the intent from the original user evidence and restart this "
            "build; preserve requested dimensions, identity, landmarks, and acceptance."
        )
    if code.startswith("CONTRACT.SCENE"):
        return (
            "Correct the mutable scene implementation while preserving every "
            "immutable intent target, owner, landmark, and representation master."
        )
    if code.startswith("SOURCE."):
        return (
            "Repair the Agent-authored source at the failing construct; preserve "
            "the immutable intent and each declared representation master."
        )
    if code.startswith("BACKEND."):
        return (
            "Repair the canonical source geometry identified by the compiler; "
            "preserve user-visible form, dimensions, interfaces, and part ownership."
        )
    if code.startswith("BUILD."):
        return (
            "Regenerate evidence through the owning exporter/compiler; do not "
            "hand-edit reports, hashes, geometry facts, or transforms."
        )
    if code.startswith("QA."):
        return (
            "Review the named physical part/check at its modeling source; preserve "
            "identity and function, and never simplify or scale geometry merely to pass QA."
        )
    if code.startswith("VISUAL."):
        return (
            "Regenerate display evidence from the same physical master and render "
            "again; do not substitute an independently modeled visual proxy."
        )
    if code.startswith("FRESHNESS."):
        return (
            "Regenerate stale artifacts from current canonical inputs and rerun "
            "their checks; never touch timestamps or hashes merely to pass freshness."
        )
    if code.startswith("CONFIG."):
        return "Correct the declared session-local path or option and rerun the same build."
    return (
        "Inspect the full compile log and repair the infrastructure failure; do "
        "not alter geometry merely to hide an internal error."
    )


def _issue(
    result: dict[str, Any],
    *,
    code: str,
    stage: str,
    message: Any,
    severity: str = "error",
    check: str | None = None,
    part: str | None = None,
    repair_hint: str | None = None,
) -> None:
    issue = {
        "code": code,
        "message": _short_message(message),
        "repairHint": repair_hint or _repair_hint(code),
        "severity": severity,
        "stage": stage,
    }
    if check:
        issue["check"] = check
    if part:
        issue["part"] = part
    if len(result["issues"]) < MAX_ISSUES:
        result["issues"].append(issue)
    else:
        result["omittedIssueCount"] += 1
        if severity == "error":
            result["omittedErrorCount"] += 1


def _stage_record(
    result: dict[str, Any],
    name: str,
    command: CommandResult,
) -> None:
    result["stages"].append(
        {
            "elapsedMs": command.elapsed_ms,
            "name": name,
            "returnCode": command.returncode,
            "status": (
                "timeout"
                if command.timed_out
                else "pass" if command.returncode == 0 else "fail"
            ),
        }
    )


def _validation_artifact(
    path: Path,
    *,
    schema: str,
    source: Path,
    errors: Iterable[str],
    **fields: Any,
) -> dict[str, Any]:
    error_list = list(errors)
    payload = {
        "errors": error_list,
        "pass": not error_list,
        "schema": schema,
        "source": str(source),
        **fields,
    }
    _write_json(path, payload)
    return payload


def _report_changed(path: Path, previous_digest: str | None) -> bool:
    if not path.is_file():
        return False
    return previous_digest is None or _digest(path) != previous_digest


def _json_from_tail(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _record_command_failure(
    result: dict[str, Any],
    command: CommandResult,
    *,
    stage: str,
    failure_code: str,
    timeout_code: str,
    internal_code: str,
    part: str | None = None,
) -> None:
    if command.timed_out:
        _issue(
            result,
            code=timeout_code,
            stage=stage,
            message="stage exceeded its configured subprocess timeout",
            part=part,
        )
        return
    parsed = _json_from_tail(command.output_tail)
    parsed_error = parsed.get("error") if isinstance(parsed, dict) else None
    if command.returncode is None or (
        command.returncode == 2 and not isinstance(parsed_error, str)
    ):
        code = internal_code
    else:
        code = failure_code
    message = parsed_error if isinstance(parsed_error, str) else command.output_tail
    typed_part = parsed.get("partId") if isinstance(parsed, dict) else None
    _issue(
        result,
        code=code,
        stage=stage,
        message=message,
        part=(
            typed_part
            if isinstance(typed_part, str) and typed_part
            else part
        ),
    )


def _run_json_check(
    result: dict[str, Any],
    runner: CommandRunner,
    *,
    name: str,
    argv: list[str],
    cwd: Path,
    timeout_seconds: float,
    output_path: Path,
    artifact_name: str,
    failure_code: str,
    part: str | None = None,
) -> None:
    previous_digest = _digest(output_path) if output_path.is_file() else None
    command = runner.run(
        name,
        argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    _stage_record(result, name, command)
    if command.timed_out:
        _record_command_failure(
            result,
            command,
            stage=name,
            failure_code=failure_code,
            timeout_code="QA.TIMEOUT",
            internal_code="INTERNAL.QA_ERROR",
            part=part,
        )
        return
    if output_path.is_file() and _report_changed(output_path, previous_digest):
        result["artifacts"][artifact_name] = _artifact(output_path)
        try:
            payload = _load_json(output_path, f"{name} output")
        except ValueError as error:
            payload = None
            _issue(
                result,
                code="INTERNAL.QA_ERROR",
                stage=name,
                message=error,
                part=part,
            )
        if isinstance(payload, dict):
            checks = payload.get("errors")
            if command.returncode != 0 or payload.get("pass") is not True:
                if isinstance(checks, list) and checks:
                    for check in checks:
                        _issue(
                            result,
                            code=failure_code,
                            stage=name,
                            message=f"automated check failed: {check}",
                            check=str(check),
                            part=part,
                        )
                else:
                    _issue(
                        result,
                        code=failure_code,
                        stage=name,
                        message=payload.get("error", "automated check did not pass"),
                        part=part,
                    )
            warnings = payload.get("warnings")
            if isinstance(warnings, list):
                for warning in warnings:
                    _issue(
                        result,
                        code="QA.WARNING",
                        stage=name,
                        severity="warning",
                        message=f"automated check warning: {warning}",
                        check=str(warning),
                        part=part,
                    )
        return
    if command.returncode != 0:
        _record_command_failure(
            result,
            command,
            stage=name,
            failure_code=failure_code,
            timeout_code="QA.TIMEOUT",
            internal_code="INTERNAL.QA_ERROR",
            part=part,
        )
    else:
        _issue(
            result,
            code="INTERNAL.QA_ERROR",
            stage=name,
            message=(
                "checker exited successfully without regenerating its evidence report"
            ),
            part=part,
        )


def _binding_matches(reference: Any, path: Path, base_dir: Path) -> bool:
    try:
        bound = _resolve_reference(reference, base_dir, "input")
    except ValueError:
        return False
    return (
        bound == path.resolve()
        and isinstance(reference, dict)
        and reference.get("sha256") == _digest(path)
    )


def _freshness_candidates(
    *,
    intent_path: Path,
    scene_path: Path,
    source_path: Path,
    report: dict[str, Any],
    report_dir: Path,
    result_artifacts: dict[str, Any],
    log_path: Path,
) -> list[Path]:
    candidates = {intent_path, scene_path, source_path, log_path}
    inputs = report.get("inputs")
    if isinstance(inputs, dict):
        for name, reference in inputs.items():
            if name == "geometry" and isinstance(reference, dict):
                for item in reference.values():
                    try:
                        candidates.add(_resolve_reference(item, report_dir, "geometry"))
                    except ValueError:
                        pass
            else:
                try:
                    candidates.add(_resolve_reference(reference, report_dir, name))
                except ValueError:
                    pass
    artifacts = report.get("artifacts")
    if isinstance(artifacts, dict):
        for name, reference in artifacts.items():
            try:
                candidates.add(_resolve_reference(reference, report_dir, name))
            except ValueError:
                pass
    for reference in result_artifacts.values():
        try:
            candidates.add(_resolve_reference(reference, report_dir, "compile artifact"))
        except ValueError:
            pass
    return sorted(candidates, key=lambda path: str(path))


def _finish(
    result: dict[str, Any],
    *,
    result_path: Path,
    log_path: Path,
) -> dict[str, Any]:
    result["finishedAt"] = _utc_now()
    has_errors = any(
        issue.get("severity") == "error" for issue in result["issues"]
    ) or result.get("omittedErrorCount", 0) > 0
    rendered = "renderEvidence" in result["artifacts"]
    result["pass"] = not has_errors and rendered
    result["status"] = (
        "awaiting-visual-review" if result["pass"] else "failed"
    )
    result["visualReviewRequired"] = True
    result["deliveryReady"] = False
    if log_path.is_file():
        result["artifacts"]["log"] = _artifact(log_path)
    _write_json(result_path, result)
    compact = {
        "artifacts": {
            key: value
            for key, value in result["artifacts"].items()
            if key
            in {
                "buildAudit",
                "buildReport",
                "freshnessAudit",
                "log",
                "preview",
                "renderEvidence",
                "sourcePreflight",
            }
        },
        "backend": result.get("backend"),
        "deliveryReady": False,
        "issues": result["issues"],
        "model": result.get("model"),
        "omittedErrorCount": result["omittedErrorCount"],
        "omittedIssueCount": result["omittedIssueCount"],
        "pass": result["pass"],
        "result": {"path": str(result_path)},
        "schema": RESULT_SCHEMA,
        "status": result["status"],
        "visualReviewRequired": True,
    }
    return compact


def compile_cad(
    options: CompileOptions,
    *,
    runner_factory: Any = CommandRunner,
) -> dict[str, Any]:
    """Execute one evidence build and return its compact result."""

    workspace = options.workspace.resolve()
    if not workspace.is_dir():
        raise ConfigurationError(f"workspace is not a directory: {workspace}")
    for label, timeout in (
        ("source timeout", options.source_timeout_seconds),
        ("backend timeout", options.backend_timeout_seconds),
        ("check timeout", options.check_timeout_seconds),
    ):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ConfigurationError(f"{label} must be finite and positive")
    if options.consistency_samples < 32:
        raise ConfigurationError("consistency samples must be at least 32")
    intent_path = _workspace_path(
        workspace, options.intent, "intent", must_exist=True
    )
    marker_path = _workspace_path(
        workspace, options.marker, "generation marker", must_exist=True
    )
    source_path = _workspace_path(
        workspace, options.source, "source", must_exist=True
    )
    scene_path = _workspace_path(
        workspace, options.scene, "scene", must_exist=False
    )
    if intent_path.suffix.lower() != ".json":
        raise ConfigurationError("intent must be a JSON file")
    if scene_path.suffix.lower() != ".json":
        raise ConfigurationError("scene must be a JSON file")
    if source_path.suffix.lower() != ".py":
        raise ConfigurationError("source must be a Python file")
    output_dir = _workspace_path(
        workspace, options.output_dir, "output directory", must_exist=False
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise ConfigurationError(f"output directory is not a directory: {output_dir}")

    try:
        intent = _load_json(intent_path, "intent contract")
    except ValueError:
        intent = {}
    raw_model = intent.get("part") if isinstance(intent, dict) else None
    model = raw_model if isinstance(raw_model, str) and MODEL_NAME.fullmatch(raw_model) else "cad"
    result_path = _output_path(
        output_dir,
        options.result or Path(f"{model}_compile-result.json"),
        "compile result",
    )
    log_path = _output_path(
        output_dir,
        options.log or Path(f"{model}_compile.log"),
        "compile log",
    )
    report_path = _output_path(
        output_dir,
        options.report or Path(f"{model}_report.json"),
        "build report",
    )
    runner = runner_factory(log_path)
    result: dict[str, Any] = {
        "artifacts": {},
        "backend": None,
        "deliveryReady": False,
        "inputs": {
            "intent": str(intent_path),
            "marker": str(marker_path),
            "scene": str(scene_path),
            "source": str(source_path),
        },
        "issues": [],
        "model": model,
        "omittedErrorCount": 0,
        "omittedIssueCount": 0,
        "schema": RESULT_SCHEMA,
        "stages": [],
        "startedAt": _utc_now(),
        "visualReviewRequired": True,
    }

    try:
        intent = _load_json(intent_path, "intent contract")
        intent_errors = validate_intent(intent, intent_path.parent)
    except Exception as error:
        intent_errors = [str(error)]
    intent_audit_path = output_dir / f"{model}_intent-validation.json"
    _validation_artifact(
        intent_audit_path,
        schema="intent-validation/v4",
        source=intent_path,
        errors=intent_errors,
        part=intent.get("part") if isinstance(intent, dict) else None,
    )
    result["artifacts"]["intentValidation"] = _artifact(intent_audit_path)
    if intent_errors:
        for error in intent_errors:
            _issue(
                result,
                code="CONTRACT.INTENT_INVALID",
                stage="intent-validation",
                message=error,
            )
        return _finish(result, result_path=result_path, log_path=log_path)

    source_preflight = audit_source(source_path)
    source_preflight_path = output_dir / f"{model}_source-preflight.json"
    _write_json(source_preflight_path, source_preflight)
    result["artifacts"]["sourcePreflight"] = _artifact(source_preflight_path)
    if source_preflight["errors"]:
        for error in source_preflight["errors"]:
            message = error.get("message", "source preflight failed")
            if isinstance(error.get("line"), int):
                message = f"line {error['line']}: {message}"
            _issue(
                result,
                code="SOURCE.PREFLIGHT_FAILED",
                stage="source-preflight",
                check=error.get("check"),
                message=message,
            )
        return _finish(result, result_path=result_path, log_path=log_path)

    intent_digest = _digest(intent_path)
    previous_report_digest = _digest(report_path) if report_path.is_file() else None
    source_command = runner.run(
        "source",
        [sys.executable, str(source_path)],
        cwd=workspace,
        timeout_seconds=options.source_timeout_seconds,
        env_extra={
            "AMAGINE3D_INTENT_PATH": str(intent_path),
            "AMAGINE3D_OUTPUT_DIR": str(output_dir),
            "AMAGINE3D_SCENE_PATH": str(scene_path),
            "AMAGINE3D_SOURCE_PHASE": "compile",
            "PYTHONPATH": str(Path(__file__).resolve().parent)
            + (
                os.pathsep + os.environ["PYTHONPATH"]
                if os.environ.get("PYTHONPATH")
                else ""
            ),
        },
    )
    _stage_record(result, "source", source_command)
    if source_command.returncode != 0:
        _record_command_failure(
            result,
            source_command,
            stage="source",
            failure_code="SOURCE.EXECUTION_FAILED",
            timeout_code="SOURCE.TIMEOUT",
            internal_code="INTERNAL.SOURCE_RUNNER_ERROR",
        )
        return _finish(result, result_path=result_path, log_path=log_path)
    if not intent_path.is_file() or _digest(intent_path) != intent_digest:
        _issue(
            result,
            code="CONTRACT.INTENT_MUTATED",
            stage="source",
            message="Agent-authored source changed the immutable intent contract",
        )
        return _finish(result, result_path=result_path, log_path=log_path)
    if not scene_path.is_file():
        _issue(
            result,
            code="CONTRACT.SCENE_MISSING",
            stage="scene-validation",
            message=f"source did not produce the declared semantic scene: {scene_path}",
        )
        return _finish(result, result_path=result_path, log_path=log_path)

    try:
        scene = _load_json(scene_path, "semantic scene")
        scene_errors = validate_scene(scene, scene_path.parent)
    except Exception as error:
        scene = {}
        scene_errors = [str(error)]
    scene_audit_path = output_dir / f"{model}_scene-validation.json"
    _validation_artifact(
        scene_audit_path,
        schema="semantic-scene-validation/v1",
        source=scene_path,
        errors=scene_errors,
        revision=scene.get("revision") if isinstance(scene, dict) else None,
    )
    result["artifacts"]["sceneValidation"] = _artifact(scene_audit_path)
    if scene_errors:
        for error in scene_errors:
            _issue(
                result,
                code="CONTRACT.SCENE_INVALID",
                stage="scene-validation",
                message=error,
            )
        return _finish(result, result_path=result_path, log_path=log_path)

    backend = select_backend(scene)
    result["backend"] = backend
    if backend == "hybrid":
        expected_report = (output_dir / f"{model}_report.json").resolve()
        if report_path != expected_report:
            _issue(
                result,
                code="CONFIG.REPORT_PATH_UNSUPPORTED",
                stage="backend",
                message=(
                    "Hybrid compiler owns its canonical report filename; "
                    f"expected {expected_report}"
                ),
            )
            return _finish(result, result_path=result_path, log_path=log_path)
        previous_report_digest = (
            _digest(report_path) if report_path.is_file() else None
        )
        backend_command = runner.run(
            "backend-hybrid",
            [
                sys.executable,
                str(Path(__file__).resolve().with_name("hybrid_compile.py")),
                str(scene_path),
                "--output-dir",
                str(output_dir),
                "--consistency-samples",
                str(options.consistency_samples),
            ],
            cwd=workspace,
            timeout_seconds=options.backend_timeout_seconds,
        )
        _stage_record(result, "backend-hybrid", backend_command)
        if backend_command.returncode != 0:
            _record_command_failure(
                result,
                backend_command,
                stage="backend-hybrid",
                failure_code="BACKEND.COMPILE_FAILED",
                timeout_code="BACKEND.TIMEOUT",
                internal_code="INTERNAL.COMPILER_ERROR",
            )
            return _finish(result, result_path=result_path, log_path=log_path)

    if not report_path.is_file():
        _issue(
            result,
            code="BUILD.REPORT_MISSING",
            stage="build-report",
            message=f"compiler did not produce the declared report: {report_path}",
        )
        return _finish(result, result_path=result_path, log_path=log_path)
    if not _report_changed(report_path, previous_report_digest):
        _issue(
            result,
            code="BUILD.REPORT_STALE",
            stage="build-report",
            message="build report was not regenerated by the current source/backend run",
        )
        return _finish(result, result_path=result_path, log_path=log_path)

    try:
        report = _load_json(report_path, "build report")
    except ValueError as error:
        _issue(
            result,
            code="BUILD.REPORT_INVALID",
            stage="build-report",
            message=error,
        )
        return _finish(result, result_path=result_path, log_path=log_path)
    result["artifacts"]["buildReport"] = _artifact(report_path)
    report_backend = report.get("backend")
    allowed_backends = (
        {"hybrid-mesh"}
        if backend == "hybrid"
        else {"brep-part", "brep-assembly", "brep-color-regions"}
    )
    if report.get("schema") != BUILD_SCHEMA or report_backend not in allowed_backends:
        _issue(
            result,
            code="BUILD.BACKEND_MISMATCH",
            stage="build-report",
            message=(
                f"scene selected {backend}, but report declares schema="
                f"{report.get('schema')!r}, backend={report_backend!r}"
            ),
        )
    report_inputs = report.get("inputs")
    report_dir = report_path.parent
    if not isinstance(report_inputs, dict) or not _binding_matches(
        report_inputs.get("intent"), intent_path, report_dir
    ) or not _binding_matches(report_inputs.get("scene"), scene_path, report_dir):
        _issue(
            result,
            code="BUILD.INPUT_BINDING_MISMATCH",
            stage="build-report",
            message="build report is not hash-bound to the supplied intent and scene",
        )
    if backend == "brep-source" and (
        not isinstance(report_inputs, dict)
        or not _binding_matches(report_inputs.get("source"), source_path, report_dir)
    ):
        _issue(
            result,
            code="BUILD.INPUT_BINDING_MISMATCH",
            stage="build-report",
            message="BRep build report is not hash-bound to the executed source",
        )

    build_audit_path = output_dir / f"{model}_build-audit.json"
    _run_json_check(
        result,
        runner,
        name="build-check",
        argv=[
            sys.executable,
            str(Path(__file__).resolve().with_name("build_check.py")),
            str(report_path),
            "--out",
            str(build_audit_path),
        ],
        cwd=workspace,
        timeout_seconds=options.check_timeout_seconds,
        output_path=build_audit_path,
        artifact_name="buildAudit",
        failure_code="BUILD.REPORT_INVALID",
    )
    if any(
        issue["severity"] == "error" and issue["stage"] in {"build-report", "build-check"}
        for issue in result["issues"]
    ):
        return _finish(result, result_path=result_path, log_path=log_path)

    artifacts = report.get("artifacts")
    parts = report.get("parts")
    if not isinstance(artifacts, dict) or not isinstance(parts, dict):
        _issue(
            result,
            code="BUILD.REPORT_INVALID",
            stage="build-report",
            message="validated build report did not expose artifacts and parts",
        )
        return _finish(result, result_path=result_path, log_path=log_path)
    profile_path = _resolve_reference(report_inputs["profile"], report_dir, "profile")

    mesh_script = Path(__file__).resolve().with_name("qa_check.py")
    mesh_targets: list[tuple[str, Path, int]] = []
    for part_id in sorted(parts):
        key = f"stl:{part_id}"
        if key in artifacts:
            mesh_targets.append(
                (part_id, _resolve_reference(artifacts[key], report_dir, key), 1)
            )
    if "stl" in artifacts:
        mesh_targets.append(
            (
                "plate",
                _resolve_reference(artifacts["stl"], report_dir, "stl"),
                len(parts),
            )
        )
    for target, mesh_path, components in mesh_targets:
        audit_path = output_dir / f"{model}_{target}-mesh-audit.json"
        _run_json_check(
            result,
            runner,
            name=f"mesh-qa:{target}",
            argv=[
                sys.executable,
                str(mesh_script),
                str(mesh_path),
                "--profile",
                str(profile_path),
                "--intent",
                str(intent_path),
                "--report",
                str(report_path),
                "--components",
                str(components),
                "--require-z0",
                "--out",
                str(audit_path),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=audit_path,
            artifact_name=f"meshAudit:{target}",
            failure_code="QA.MESH_FAILED",
            part=target if target != "plate" else None,
        )

    if len(parts) > 1 and "stl" in artifacts:
        assembly_audit_path = output_dir / f"{model}_assembly-audit.json"
        _run_json_check(
            result,
            runner,
            name="assembly-qa",
            argv=[
                sys.executable,
                str(Path(__file__).resolve().with_name("assembly_check.py")),
                str(report_path),
                str(_resolve_reference(artifacts["stl"], report_dir, "stl")),
                "--out",
                str(assembly_audit_path),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=assembly_audit_path,
            artifact_name="assemblyAudit",
            failure_code="QA.ASSEMBLY_FAILED",
        )

    for artifact_key in sorted(
        key for key in artifacts if key.startswith("step:")
    ):
        token = artifact_key.removeprefix("step:")
        step_path = _resolve_reference(artifacts[artifact_key], report_dir, artifact_key)
        audit_path = output_dir / f"{model}_{token}-step-audit.json"
        _run_json_check(
            result,
            runner,
            name=f"step-qa:{token}",
            argv=[
                sys.executable,
                str(Path(__file__).resolve().with_name("step_check.py")),
                str(step_path),
                "--intent",
                str(intent_path),
                "--report",
                str(report_path),
                "--out",
                str(audit_path),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=audit_path,
            artifact_name=f"stepAudit:{token}",
            failure_code="QA.STEP_FAILED",
            part=token if token != "assembly" else None,
        )

    if "3mf" in artifacts:
        three_mf_path = _resolve_reference(artifacts["3mf"], report_dir, "3mf")
        color_audit_path = output_dir / f"{model}_color-audit.json"
        _run_json_check(
            result,
            runner,
            name="color-qa",
            argv=[
                sys.executable,
                str(Path(__file__).resolve().parent / "color" / "qa_check.py"),
                str(three_mf_path),
                "--profile",
                str(profile_path),
                "--intent",
                str(intent_path),
                "--report",
                str(report_path),
                "--require-z0",
                "--out",
                str(color_audit_path),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=color_audit_path,
            artifact_name="colorAudit",
            failure_code="QA.COLOR_FAILED",
        )
        package_audit_path = output_dir / f"{model}_color-assembly-audit.json"
        _run_json_check(
            result,
            runner,
            name="color-assembly-qa",
            argv=[
                sys.executable,
                str(Path(__file__).resolve().parent / "color" / "assembly_check.py"),
                str(report_path),
                str(three_mf_path),
                "--out",
                str(package_audit_path),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=package_audit_path,
            artifact_name="colorAssemblyAudit",
            failure_code="QA.COLOR_ASSEMBLY_FAILED",
        )

    display_reference = artifacts.get("glb:display")
    try:
        display_path = _resolve_reference(
            display_reference, report_dir, "glb:display"
        )
    except ValueError as error:
        _issue(
            result,
            code="VISUAL.DISPLAY_ARTIFACT_MISSING",
            stage="render",
            message=error,
        )
        return _finish(result, result_path=result_path, log_path=log_path)
    preview_path = output_dir / f"{model}_views.png"
    reference_view = intent["visual"]["reference_view"]
    reference_preview_path = output_dir / f"{model}_{reference_view}-view.png"
    render_audit_path = output_dir / f"{model}_render.json"
    previous_preview_digest = _digest(preview_path) if preview_path.is_file() else None
    previous_reference_digest = (
        _digest(reference_preview_path) if reference_preview_path.is_file() else None
    )
    previous_render_digest = (
        _digest(render_audit_path) if render_audit_path.is_file() else None
    )
    render_command = runner.run(
        "render",
        [
            sys.executable,
            str(Path(__file__).resolve().with_name("render_preview.py")),
            str(display_path),
            "--out",
            str(preview_path),
            "--report",
            str(render_audit_path),
            "--reference-view",
            reference_view,
            "--reference-out",
            str(reference_preview_path),
        ],
        cwd=workspace,
        timeout_seconds=options.check_timeout_seconds,
    )
    _stage_record(result, "render", render_command)
    if render_command.returncode != 0:
        _record_command_failure(
            result,
            render_command,
            stage="render",
            failure_code="VISUAL.RENDER_FAILED",
            timeout_code="VISUAL.RENDER_TIMEOUT",
            internal_code="INTERNAL.RENDER_ERROR",
        )
    elif (
        not _report_changed(preview_path, previous_preview_digest)
        or not _report_changed(reference_preview_path, previous_reference_digest)
        or not _report_changed(render_audit_path, previous_render_digest)
    ):
        _issue(
            result,
            code="INTERNAL.RENDER_ERROR",
            stage="render",
            message=(
                "renderer did not regenerate preview, matched view, and render evidence"
            ),
        )
    else:
        try:
            render_evidence = _load_json(render_audit_path, "render evidence")
            bound_meshes = render_evidence.get("meshes")
            display_hash = _digest(display_path)
            bound = isinstance(bound_meshes, list) and any(
                isinstance(item, dict)
                and Path(str(item.get("path", ""))).resolve() == display_path
                and item.get("sha256") == display_hash
                for item in bound_meshes
            )
            preview_reference = render_evidence.get("preview")
            preview_bound = (
                isinstance(preview_reference, dict)
                and Path(str(preview_reference.get("path", ""))).resolve()
                == preview_path
                and preview_reference.get("sha256") == _digest(preview_path)
            )
            matched_reference = render_evidence.get("matched_view")
            matched_bound = (
                isinstance(matched_reference, dict)
                and matched_reference.get("name") == reference_view
                and Path(str(matched_reference.get("path", ""))).resolve()
                == reference_preview_path
                and matched_reference.get("sha256")
                == _digest(reference_preview_path)
            )
            if (
                render_evidence.get("schema") != "evidence-render/v2"
                or not bound
                or not preview_bound
                or not matched_bound
            ):
                raise ValueError(
                    "render evidence is not hash-bound to the current display GLB and preview"
                )
            result["artifacts"]["preview"] = _artifact(preview_path)
            result["artifacts"]["referencePreview"] = _artifact(
                reference_preview_path
            )
            result["artifacts"]["renderEvidence"] = _artifact(render_audit_path)
        except Exception as error:
            _issue(
                result,
                code="VISUAL.RENDER_EVIDENCE_INVALID",
                stage="render",
                message=error,
            )

    for warning in report.get("warnings", []):
        _issue(
            result,
            code="BUILD.WARNING",
            stage="build-report",
            severity="warning",
            message=warning,
        )

    freshness_path = output_dir / f"{model}_freshness-audit.json"
    freshness_inputs = _freshness_candidates(
        intent_path=intent_path,
        scene_path=scene_path,
        source_path=source_path,
        report=report,
        report_dir=report_dir,
        result_artifacts=result["artifacts"],
        log_path=log_path,
    )
    freshness_command = runner.run(
        "freshness",
        [
            sys.executable,
            str(Path(__file__).resolve().with_name("freshness_check.py")),
            "--after",
            str(marker_path),
            *[str(path) for path in freshness_inputs],
        ],
        cwd=workspace,
        timeout_seconds=options.check_timeout_seconds,
    )
    _stage_record(result, "freshness", freshness_command)
    freshness = _json_from_tail(freshness_command.output_tail)
    if isinstance(freshness, dict):
        freshness_evidence = {
            **freshness,
            "schema": "evidence-cad-compile-freshness/v1",
        }
        _write_json(freshness_path, freshness_evidence)
        result["artifacts"]["freshnessAudit"] = _artifact(freshness_path)
    if (
        freshness_command.returncode != 0
        or not isinstance(freshness, dict)
        or freshness.get("pass") is not True
    ):
        stale = []
        if isinstance(freshness, dict) and isinstance(
            freshness.get("artifacts"), list
        ):
            stale = [
                item.get("path")
                for item in freshness["artifacts"]
                if isinstance(item, dict) and item.get("fresh") is not True
            ]
        _issue(
            result,
            code="FRESHNESS.CHECK_FAILED",
            stage="freshness",
            message=(
                f"stale or missing artifacts: {stale}"
                if stale
                else "freshness checker did not return passing evidence"
            ),
        )
    return _finish(result, result_path=result_path, log_path=log_path)


def _positive_timeout(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path, help="semantic scene produced by source")
    parser.add_argument("--marker", required=True, type=Path)
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument(
        "--source-timeout-seconds", type=_positive_timeout, default=1_800.0
    )
    parser.add_argument(
        "--backend-timeout-seconds", type=_positive_timeout, default=1_800.0
    )
    parser.add_argument(
        "--check-timeout-seconds", type=_positive_timeout, default=600.0
    )
    parser.add_argument("--consistency-samples", type=int, default=1_024)
    args = parser.parse_args(argv)
    if args.consistency_samples < 32:
        parser.error("--consistency-samples must be at least 32")
    try:
        result = compile_cad(
            CompileOptions(
                workspace=args.workspace,
                marker=args.marker,
                intent=args.intent,
                scene=args.scene,
                source=args.source,
                output_dir=args.output_dir,
                report=args.report,
                result=args.result,
                log=args.log,
                source_timeout_seconds=args.source_timeout_seconds,
                backend_timeout_seconds=args.backend_timeout_seconds,
                check_timeout_seconds=args.check_timeout_seconds,
                consistency_samples=args.consistency_samples,
            )
        )
    except (ConfigurationError, OSError, ValueError) as error:
        result = {
            "artifacts": {},
            "backend": None,
            "deliveryReady": False,
            "issues": [
                {
                    "code": "CONFIG.INVALID",
                    "message": _short_message(error),
                    "repairHint": _repair_hint("CONFIG.INVALID"),
                    "severity": "error",
                    "stage": "configuration",
                }
            ],
            "model": None,
            "omittedErrorCount": 0,
            "omittedIssueCount": 0,
            "pass": False,
            "schema": RESULT_SCHEMA,
            "status": "failed",
            "visualReviewRequired": True,
        }
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
