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

from print_plates import print_plates

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
import tempfile
import time
from typing import Any, Callable, Iterable
from uuid import uuid4

import numpy as np

from capability_manifest import build_manifest as build_capability_manifest
from build_manifest import file_binding_errors
from coordinate_frames import transform_bounds, validated_rigid_matrix
from freshness_check import stable_file_snapshot
from intent_contract import validate as validate_intent
from intent_revision import IntentRevisionError, audit_lineage, history_path, load_history, semantic_diff
from scene_contract import validate as validate_scene
from source_preflight import audit as audit_source


RESULT_SCHEMA = "evidence-cad-compile-result/v1"
AGENT_SUMMARY_SCHEMA = "a3d-compile-summary/v1"
BUILD_SCHEMA = "evidence-a3d-build/v1"
REPAIR_STATE_SCHEMA = "evidence-cad-repair-state/v1"
SOURCE_DIAGNOSTICS_SCHEMA = "evidence-cad-source-diagnostics/v1"
MODEL_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
MAX_ISSUES = 40
MAX_MESSAGE_CHARS = 700
MAX_SUMMARY_CHARS = 12_000
MAX_SUMMARY_ISSUES = 5
THICKNESS_CHECKS = {"printability_wall_thickness", "printability_local_thin_region"}
MAX_LOG_TAIL_BYTES = 32_000
DEFAULT_COMPILE_TIMEOUT_SECONDS = 5_400.0
AGENT_ARTIFACT_KEYS = {
    "buildReport",
    "diagnosticPreview",
    "diagnosticReferencePreview",
    "diagnosticRenderEvidence",
    "diagnosticStep",
    "diagnosticGlb",
    "diagnosticSourceEvidence",
    "log",
    "preview",
    "referencePreview",
    "repairState",
    "intentRevision",
    "renderEvidence",
}


class ConfigurationError(ValueError):
    """Raised when the requested compile escapes the declared workspace."""


@dataclass(frozen=True)
class CompileOptions:
    workspace: Path
    marker: Path | None
    intent: Path
    scene: Path
    source: Path
    output_dir: Path
    result: Path | None = None
    log: Path | None = None
    compile_timeout_seconds: float = DEFAULT_COMPILE_TIMEOUT_SECONDS
    source_timeout_seconds: float = 1_800.0
    check_timeout_seconds: float = 600.0
    consistency_samples: int = 1_024


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    elapsed_ms: int
    output_tail: str
    timed_out: bool = False
    deadline_exhausted: bool = False


@dataclass(frozen=True)
class _CompileDeadline:
    expires_at: float
    clock: Callable[[], float]

    @classmethod
    def start(
        cls,
        timeout_seconds: float,
        clock: Callable[[], float],
    ) -> _CompileDeadline:
        return cls(expires_at=clock() + timeout_seconds, clock=clock)

    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - self.clock())


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


def _path_only_artifacts(artifacts: Any) -> dict[str, str]:
    """Project useful evidence paths without replaying hashes and metadata."""

    if not isinstance(artifacts, dict):
        return {}
    projected: dict[str, str] = {}
    for key in sorted(AGENT_ARTIFACT_KEYS):
        reference = artifacts.get(key)
        if isinstance(reference, dict) and isinstance(reference.get("path"), str):
            projected[key] = reference["path"]
    return projected


def _warning_groups(issues: Any) -> list[dict[str, Any]]:
    """Group repeated advisory findings while keeping their affected identities."""

    if not isinstance(issues, list):
        return []
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("severity") != "warning":
            continue
        key = tuple(
            str(issue.get(field, ""))
            for field in ("code", "check", "message", "repairHint")
        )
        group = groups.setdefault(
            key,
            {
                **issue,
                "id": issue.get("id") or _issue_identity(issue),
                "code": issue.get("code"),
                "count": 0,
                "message": issue.get("message", ""),
                "repairHint": issue.get("repairHint"),
                "severity": "warning",
            },
        )
        group["count"] += 1
        if issue.get("check"):
            group["check"] = issue["check"]
        for source, target in (
            ("id", "ids"),
            ("interfaceId", "interfaceIds"),
            ("part", "parts"),
            ("stage", "stages"),
        ):
            value = issue.get(source)
            if value is not None:
                values = list(group.get(target, []))
                if value not in values:
                    values.append(value)
                group[target] = values
    for group in groups.values():
        if group["count"] > 1:
            group["detailScope"] = "representative-issue"
            group["summaryTruncated"] = True
        for field in ("ids", "interfaceIds", "parts", "stages"):
            if field in group:
                group[field] = sorted(group[field])
    return list(groups.values())


def _summary_json(value: Any) -> str:
    """Use the actual stdout encoding when accounting for the context budget."""

    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _project_summary_value(value: Any, depth: int = 0) -> tuple[Any, bool]:
    """Bound nested previews; the persisted evidence is never passed through this."""

    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity"), True
    if isinstance(value, str):
        if len(value) > 240:
            return value[:80] + "\n…\n" + value[-157:], True
        return value, False
    if isinstance(value, (dict, list)):
        if not value:
            return value.copy(), False
        if depth >= 3:
            return "[details omitted]", True
        if isinstance(value, list):
            children = [_project_summary_value(item, depth + 1) for item in value[:6]]
            projected = [item for item, _ in children]
            cut = len(value) > 6 or any(cut for _, cut in children)
            while len(_summary_json(projected)) > 1_600:
                projected.pop()
                cut = True
            return projected, cut
        # Keep small measurements ahead of bulky nested reports or explanatory text.
        keys = [key for key in value if len(str(key)) <= 120]
        keys.sort(key=lambda key: not isinstance(value[key], (int, float, bool, type(None))))
        children = {key: _project_summary_value(value[key], depth + 1) for key in keys[:8]}
        projected = {key: item for key, (item, _) in children.items()}
        cut = len(children) < len(value) or any(cut for _, cut in children.values())
        while len(_summary_json(projected)) > 1_600:
            projected.popitem()
            cut = True
        return projected, cut
    return value, False


def _project_orientation_record(value: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict):
        return {}, True
    keys = (
        "name",
        "rotateDegreesXYZ",
        "bedContactSemanticFace",
        "dimensionsMm",
        "fitsProfile",
        "candidateCount",
        "rankingStrategy",
        "evidenceRole",
        "centerInsideContactBounds",
        "contactAreaMm2",
        "stabilityOffsetRatio",
        "overhangAreaMm2",
    )
    projected: dict[str, Any] = {}
    truncated = any(key not in keys for key in value)
    for key in keys:
        if key not in value:
            continue
        projected[key], cut = _project_summary_value(value[key])
        truncated |= cut
    return projected, truncated


def _agent_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Project evidence into a bounded decision view, including JSON overhead."""

    issues = result.get("issues")
    errors = (
        [
            {
                **issue,
                "id": issue.get("id") or _issue_identity(issue),
            }
            for issue in issues
            if isinstance(issue, dict) and issue.get("severity") != "warning"
        ]
        if isinstance(issues, list)
        else []
    )
    warnings = _warning_groups(issues)
    summary: dict[str, Any] = {
        "artifacts": {},
        "backend": _project_summary_value(result.get("backend"))[0],
        "deliveryReady": result.get("deliveryReady", False),
        "issueCounts": {
            "errors": len(errors) + int(result.get("omittedErrorCount", 0) or 0),
            "omitted": int(result.get("omittedIssueCount", 0) or 0),
            "warnings": sum(group["count"] for group in warnings),
        },
        "issues": [],
        "model": _project_summary_value(result.get("model"))[0],
        "pass": result.get("pass", False),
        "resultSchema": RESULT_SCHEMA,
        "runId": _project_summary_value(result.get("runId"))[0],
        "schema": AGENT_SUMMARY_SCHEMA,
        "status": _project_summary_value(result.get("status", "failed"))[0],
        "visualReviewRequired": result.get("visualReviewRequired", True),
        "diagnostics": {
            "maxOutputChars": MAX_SUMMARY_CHARS,
            "shownIssueGroups": 0,
            "omittedIssueGroups": len(errors) + len(warnings),
            "truncated": False,
            "readMore": "a3d diagnose RESULT.json; use --id ID --field FIELD for paged detail",
        },
    }
    diagnostics = summary["diagnostics"]
    diagnostics["truncated"] = any(
        _project_summary_value(result.get(key))[1]
        for key in ("backend", "model", "runId", "status")
    )

    def add(target: dict[str, Any], key: str, value: Any) -> bool:
        target[key] = value
        if len(_summary_json(summary)) <= MAX_SUMMARY_CHARS:
            return True
        del target[key]
        diagnostics["truncated"] = True
        return False

    shown: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for issue in [*errors, *warnings][:MAX_SUMMARY_ISSUES]:
        message = str(issue.get("message", "")).strip() or "unspecified failure"
        item: dict[str, Any] = {"message": _short_message(message), "summaryTruncated": False}
        cut = len(message) > MAX_MESSAGE_CHARS or bool(issue.get("summaryTruncated"))
        for key in ("id", "code", "severity", "stage", "part", "check", "count", "detailScope"):
            if key in issue:
                item[key], shortened = _project_summary_value(issue[key])
                cut |= shortened
        summary["issues"].append(item)
        if len(_summary_json(summary)) > MAX_SUMMARY_CHARS:
            summary["issues"].pop()
            break
        item["summaryTruncated"] = cut
        shown.append((issue, item))
        # Reserve the first cause before evidence paths, then keep the result
        # location ahead of secondary findings. Paths are exact or omitted.
        if len(shown) == 1 and result.get("result"):
            add(summary, "result", result["result"])

    if not shown and result.get("result"):
        add(summary, "result", result["result"])

    diagnostics["shownIssueGroups"] = len(shown)
    diagnostics["omittedIssueGroups"] -= len(shown)
    diagnostics["truncated"] |= bool(diagnostics["omittedIssueGroups"])

    # Add localization and numeric evidence after reserving space for the other causes.
    detail_order = ("featureId", "nodeId", "interfaceId", "ownerPartId", "field", "blockedBy",
                    "repairHint", "observed", "expected", "actual")
    for issue, item in shown:
        boolean_witness = _boolean_witness(issue)
        if boolean_witness:
            item["summaryTruncated"] |= not add(item, "booleanWitness", boolean_witness)
        witness = _thickness_witness(issue)
        if witness:
            projected, cut = _project_summary_value(witness)
            added = add(item, "witness", projected)
            item["summaryTruncated"] |= cut or not added
    comparisons = result.get("warningComparisons", [])
    if comparisons:
        add(summary, "warningComparisons", [])
        for comparison in comparisons[:MAX_SUMMARY_ISSUES]:
            if "warningComparisons" not in summary:
                break
            projected, cut = _project_summary_value(comparison)
            summary["warningComparisons"].append(projected)
            if len(_summary_json(summary)) > MAX_SUMMARY_CHARS:
                summary["warningComparisons"].pop()
                diagnostics["truncated"] = True
                break
            diagnostics["truncated"] |= cut
        diagnostics["truncated"] |= len(summary.get("warningComparisons", [])) < len(comparisons)

    # Reserve actionable locations and comparisons before bulky observation trees.
    for issue, item in shown:
        for key in dict.fromkeys((*detail_order, *issue.keys())):
            if key in item or key not in issue:
                continue
            projected, cut = _project_summary_value(issue[key])
            if len(str(key)) > 120 or not add(item, key, projected):
                cut = True
            item["summaryTruncated"] |= cut
        diagnostics["truncated"] |= item["summaryTruncated"]

    for key, path in _path_only_artifacts(result.get("artifacts")).items():
        add(summary["artifacts"], key, path)
    for key in (
        "colors",
        "deliverables",
        "physicalParts",
        "printOrientationEvidence",
        "repairDelta",
    ):
        value = result.get(key)
        if not value:
            continue
        if key == "printOrientationEvidence" and isinstance(value, dict):
            if not add(summary, key, {}):
                continue
            part_names = sorted(name for name in value if isinstance(name, str))
            diagnostics["truncated"] |= len(part_names) != len(value)
            for part_name in part_names:
                if len(part_name) > 120:
                    diagnostics["truncated"] = True
                    continue
                projected, cut = _project_orientation_record(value[part_name])
                if not projected or not add(summary[key], part_name, projected):
                    diagnostics["truncated"] = True
                    continue
                diagnostics["truncated"] |= cut
        elif key == "deliverables" and isinstance(value, dict):
            if not add(summary, key, {}):
                continue
            for name, path in value.items():
                if not add(summary[key], name, path):
                    break
        else:
            projected, cut = _project_summary_value(value)
            add(summary, key, projected)
            diagnostics["truncated"] |= cut
    return summary


def _boolean_witness(issue: dict[str, Any]) -> dict[str, Any]:
    """Keep one actionable boolean measurement ahead of generic depth limits."""
    observed = issue.get("observed")
    witness = observed.get("booleanWitness") if isinstance(observed, dict) else None
    if not isinstance(witness, dict) or witness.get("operation") not in {"union", "cut"}:
        return {}
    keys = ("operation", "coordinateFrame", "units", "ownerPartId", "bodySolidCount",
            "operandSolidCount", "unconnectedComponentCount", "unresolvedComponentCount",
            "intersectionVolumeMm3", "removedMm3", "operandInsideOwnerBounds")
    compact = {key: _project_summary_value(witness[key])[0] for key in keys if key in witness}
    raw_components = witness.get("components")
    components = [item for item in raw_components if isinstance(item, dict)] if isinstance(raw_components, list) else []
    if components:
        component = components[0]
        if witness["operation"] == "union":
            component = next((item for item in components if item.get("connectedToOwner") is False), component)
        keys = ("solidIndex", "connectedToOwner", "relation", "gapMm", "surfaceDistanceMm",
                "ownerPointMm", "operandPointMm", "intersectionVolumeMm3", "measurementError")
        compact["component"] = {key: _project_summary_value(component[key])[0]
                                for key in keys if key in component}
    return compact


def _thickness_witness(issue: dict[str, Any]) -> dict[str, Any]:
    observed = issue.get("observed", {})
    if issue.get("check") not in THICKNESS_CHECKS or not isinstance(observed, dict):
        return {}
    sample = (observed.get("sampling") or {}).get("minimum_sample") or {}
    context = observed.get("measurement_context") or {}
    frame = context.get("coordinate_frame", {})
    if not isinstance(sample.get("point_mm"), list):
        return {}
    return {
        "pointMm": sample["point_mm"], "semanticPointMm": sample.get("semantic_point_mm"),
        "coordinateFrame": frame.get("name", "mesh-local"),
        "frameStatus": frame.get("status", "unbound"),
        "artifactKey": frame.get("artifact_key"),
        "method": context.get("method", "unknown"),
        "minimumMm": observed.get("minimum_mm"),
        "featureCandidates": {"ids": observed.get("affected_feature_ids", []),
                              "basis": "bbox intersection candidates; not root causes"},
    }


def _selected_orientation_evidence(orientation: Any) -> dict[str, Any]:
    if not isinstance(orientation, dict):
        return {}
    selected = orientation.get("selected")
    candidates = orientation.get("candidates")
    if not isinstance(selected, dict) or not isinstance(candidates, list):
        return {}
    rotation = selected.get("rotate_degrees_xyz")
    dimensions = selected.get("print_dimensions_mm", selected.get("dimensions_mm"))
    metrics = selected.get("orientation_metrics")
    if (
        not isinstance(selected.get("name"), str)
        or not isinstance(rotation, list)
        or len(rotation) != 3
        or not isinstance(dimensions, list)
        or len(dimensions) != 3
        or not isinstance(metrics, dict)
    ):
        return {}
    return {
        "bedContactSemanticFace": selected.get("bed_contact_semantic_face"),
        "candidateCount": len(candidates),
        "centerInsideContactBounds": metrics.get("center_inside_contact_bounds"),
        "contactAreaMm2": metrics.get("contact_area_mm2"),
        "dimensionsMm": dimensions,
        "evidenceRole": "automatic-ranked-export-pose",
        "fitsProfile": selected.get("fits_profile"),
        "name": selected["name"],
        "overhangAreaMm2": metrics.get("overhang_area_mm2"),
        "rankingStrategy": orientation.get("strategy"),
        "rotateDegreesXYZ": rotation,
        "stabilityOffsetRatio": metrics.get("stability_offset_ratio"),
    }


def _print_orientation_evidence(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    backend = report.get("backendData")
    if not isinstance(backend, dict):
        return {}
    records: dict[str, dict[str, Any]] = {}
    direct = _selected_orientation_evidence(backend.get("printOrientation"))
    part = report.get("part")
    if direct and isinstance(part, str) and part:
        records[part] = direct

    raw_plates = backend.get("printPlates")
    plates = raw_plates if isinstance(raw_plates, list) else [backend.get("printPlate")]
    for plate in plates:
        if not isinstance(plate, dict):
            continue
        geometry = plate.get("geometry", plate)
        layout = geometry.get("layout") if isinstance(geometry, dict) else None
        orientations = layout.get("orientations") if isinstance(layout, dict) else None
        if not isinstance(orientations, dict):
            continue
        for part_name, orientation in orientations.items():
            evidence = _selected_orientation_evidence(orientation)
            if isinstance(part_name, str) and part_name and evidence:
                records[part_name] = evidence
    return dict(sorted(records.items()))


def _report_agent_facts(report: dict[str, Any]) -> dict[str, Any]:
    """Extract compact delivery facts from a validated full build report."""

    artifacts = report.get("artifacts")
    deliverables: dict[str, str] = {}
    colors: dict[str, str] = {}
    if isinstance(artifacts, dict):
        for key, reference in artifacts.items():
            is_deliverable = (
                key in {"3mf", "glb:display", "stl"}
                or key.startswith("step:")
                or key.startswith("stl:")
            )
            path = reference.get("path") if isinstance(reference, dict) else None
            if (
                is_deliverable
                and isinstance(path, str)
                and ".amagine3d-internal" not in path
            ):
                deliverables[key] = path
        display = artifacts.get("glb:display")
        readback = display.get("readbackBaseColors") if isinstance(display, dict) else None
        if isinstance(readback, dict):
            colors = {
                str(key): str(value)
                for key, value in sorted(readback.items())
                if isinstance(value, str)
            }
    parts = report.get("parts")
    return {
        "colors": colors,
        "deliverables": dict(sorted(deliverables.items())),
        "physicalParts": (
            sorted(str(key) for key in parts) if isinstance(parts, dict) else []
        ),
        "printOrientationEvidence": _print_orientation_evidence(report),
    }


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
    """Require BRep masters and select compilation through the build source."""

    parts = scene.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("scene parts must be a non-empty list")
    if any(
        not isinstance(part, dict) or part.get("representationMaster") != "brep"
        for part in parts
    ):
        raise ValueError("scene representation masters must be brep")
    return "brep-source"


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


def _run_with_deadline(
    runner: Any,
    deadline: _CompileDeadline,
    stage: str,
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    env_extra: dict[str, str] | None = None,
) -> CommandResult:
    """Clamp one subprocess to the remaining whole-compile budget."""

    remaining = deadline.remaining_seconds()
    if remaining <= 0:
        return CommandResult(
            returncode=None,
            elapsed_ms=0,
            output_tail="compile aggregate deadline was exhausted before this stage",
            timed_out=True,
            deadline_exhausted=True,
        )
    effective_timeout = min(timeout_seconds, remaining)
    aggregate_limited = effective_timeout < timeout_seconds
    command = runner.run(
        stage,
        argv,
        cwd=cwd,
        timeout_seconds=effective_timeout,
        env_extra=env_extra,
    )
    if not (command.timed_out and aggregate_limited):
        return command
    return CommandResult(
        returncode=command.returncode,
        elapsed_ms=command.elapsed_ms,
        output_tail=command.output_tail,
        timed_out=True,
        deadline_exhausted=True,
    )


def _short_message(value: Any) -> str:
    message = str(value).strip() or "unspecified failure"
    if len(message) <= MAX_MESSAGE_CHARS:
        return message
    # Tracebacks end with the actionable exception. Keep that tail in the
    # terminal view while the persisted issue and diagnose retain every byte.
    head = MAX_MESSAGE_CHARS // 3
    return message[:head] + "\n…\n" + message[-(MAX_MESSAGE_CHARS - head - 3):]


def _repair_hint(code: str) -> str:
    if code.startswith("CONTRACT.INTENT"):
        return (
            "Preserve the recorded target and repair its declared contract fields. "
            "For a real target change, link the prior intent and record the request "
            "or external evidence; do not change targets to match a failed build."
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
    if code.startswith("INTERFACE."):
        return (
            "Repair the named interface from its intent clearance, engagement, "
            "axis, feature ownership, and actual geometry evidence; do not insert "
            "product-specific fallback dimensions."
        )
    if code == "QA.INSTALLATION_FAILED":
        return (
            "Compare the named component/path witness with the final receiving part "
            "in their shared assembly frame. Correct the aperture, support, retention "
            "or insertion geometry for the required behavior; keep witnesses faithful "
            "to the component and do not drop declared checks to clear the failure."
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
    if code.startswith("COMPILE."):
        return (
            "Use the reported stage evidence to remove avoidable retries or split an "
            "oversized build; do not weaken geometry or QA to fit the time budget."
        )
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
    details: dict[str, Any] | None = None,
) -> None:
    if (not repair_hint and code == "QA.STEP_FAILED" and isinstance(check, str)
            and re.fullmatch(r"section:[^:]+:\d+:(?:width_u_mm|depth_v_mm)", check)):
        repair_hint = (
            "Adjust the construction controls toward the declared nominal dimension. "
            "Stop when this raw STEP check passes; preserve form, walls, floor and function."
        )
    if not repair_hint and check in THICKNESS_CHECKS:
        repair_hint = (
            "Locate the sample or risk region in final STEP before editing: max-sphere "
            "can reflect curvature or a free rim. Feature IDs/bounds are candidates. "
            "Repair confirmed thickness defects; preserve targets, form and function."
        )
    issue = {
        "code": code,
        "message": str(message).strip() or "unspecified failure",
        "repairHint": repair_hint or _repair_hint(code),
        "severity": severity,
        "stage": stage,
    }
    if check:
        issue["check"] = check
    if part:
        issue["part"] = part
    detail_fields = {
        "actual",
        "artifact",
        "blockedBy",
        "bounds",
        "componentCount",
        "components",
        "coordinateFrame",
        "endpoint",
        "expected",
        "featureBounds",
        "featureId",
        "features",
        "field",
        "interfaceId",
        "nodeId",
        "observed",
        "offenderId",
        "ownerBounds",
        "ownerPartId",
        "path",
        "scope",
        "causeId",
        "status",
        "target",
    }
    for key, value in (details or {}).items():
        if key in detail_fields:
            issue[key] = value
    # Context limits belong to the terminal projection, never the saved evidence.
    result["issues"].append(issue)


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
            **(
                {"timeoutScope": "compile"}
                if command.deadline_exhausted
                else {}
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


def _is_deferred_source_failure(
    command: CommandResult,
    report_path: Path,
) -> bool:
    """Allow a checked-operation diagnostic candidate to reach later audits."""

    if command.timed_out or command.returncode in {None, 0} or not report_path.is_file():
        return False
    payload = _json_from_tail(command.output_tail)
    return bool(
        isinstance(payload, dict)
        and payload.get("schema") == SOURCE_DIAGNOSTICS_SCHEMA
        and payload.get("pass") is False
        and isinstance(payload.get("issues"), list)
        and payload["issues"]
    )


def _record_structured_issues(
    result: dict[str, Any],
    payload: dict[str, Any],
    *,
    stage: str,
    default_code: str,
    default_part: str | None = None,
) -> tuple[int, int]:
    raw_issues = payload.get("issues")
    if not isinstance(raw_issues, list):
        return 0, 0
    recorded = 0
    error_count = 0
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        severity = raw.get("severity")
        if severity not in {"error", "warning"}:
            severity = "error"
        code = raw.get("code")
        if not isinstance(code, str) or not code.strip():
            code = default_code
        check = raw.get("check")
        typed_check = check if isinstance(check, str) and check else None
        typed_part = raw.get("part")
        if not isinstance(typed_part, str) or not typed_part:
            typed_part = raw.get("partId")
        if not isinstance(typed_part, str) or not typed_part:
            typed_part = default_part
        message = raw.get("message")
        if not isinstance(message, str) or not message.strip():
            message = typed_check or code
        repair_hint = raw.get("repairHint")
        _issue(
            result,
            code=code,
            stage=stage,
            message=message,
            severity=severity,
            check=typed_check,
            part=typed_part,
            repair_hint=(
                repair_hint
                if isinstance(repair_hint, str) and repair_hint.strip()
                else None
            ),
            details=raw,
        )
        recorded += 1
        error_count += int(severity == "error")
    return recorded, error_count


def _check_details(payload: dict[str, Any], name: str) -> dict[str, Any]:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return {}
    for record in checks:
        if not isinstance(record, dict):
            continue
        identity = record.get("name", record.get("check", record.get("code")))
        if identity == name:
            return record
    return {}


def _valid_staged_file(path: Path) -> bool:
    return (
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_nlink == 1
    )


def _replace_file(source: Path, destination: Path) -> None:
    source.replace(destination)


def _publish_render_bundle(
    *,
    staged_preview: Path,
    staged_reference: Path,
    preview_path: Path,
    reference_preview_path: Path,
    render_audit_path: Path,
    render_evidence: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Publish immutable run images, then atomically move the evidence pointer."""

    if preview_path.exists() or reference_preview_path.exists():
        raise FileExistsError("run-scoped render artifacts already exist")
    try:
        _replace_file(staged_preview, preview_path)
        _replace_file(staged_reference, reference_preview_path)
        published = {
            **render_evidence,
            "runId": run_id,
            "preview": {
                **render_evidence["preview"],
                "path": str(preview_path),
            },
            "matched_view": {
                **render_evidence["matched_view"],
                "path": str(reference_preview_path),
            },
        }
        _write_json(render_audit_path, published)
        return published
    except Exception:
        # Both destinations are unique to this run ID, so only this failed
        # publication can own them.  Never leave half-published images for UI
        # discovery while the previous canonical evidence remains authoritative.
        preview_path.unlink(missing_ok=True)
        reference_preview_path.unlink(missing_ok=True)
        raise


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
    if command.deadline_exhausted:
        _issue(
            result,
            code="COMPILE.DEADLINE_EXCEEDED",
            stage=stage,
            message="the aggregate compile deadline was exhausted",
            part=part,
        )
        return
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
    if isinstance(parsed, dict):
        _, structured_errors = _record_structured_issues(
            result,
            parsed,
            stage=stage,
            default_code=failure_code,
            default_part=part,
        )
        if structured_errors:
            return
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
    deadline: _CompileDeadline,
    *,
    name: str,
    argv: list[str],
    cwd: Path,
    timeout_seconds: float,
    output_path: Path,
    artifact_name: str,
    failure_code: str,
    expected_schema: str,
    part: str | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        dir=output_path.parent,
        prefix=f".{output_path.stem}-{name.replace(':', '-')}-",
    ) as staging_directory:
        staged_path = Path(staging_directory) / output_path.name
        command = _run_with_deadline(
            runner,
            deadline,
            name,
            [*argv, "--out", str(staged_path)],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
        _stage_record(result, name, command)
        if command.timed_out:
            _record_command_failure(
                result,
                stage=name,
                command=command,
                failure_code=failure_code,
                timeout_code="QA.TIMEOUT",
                internal_code="INTERNAL.QA_ERROR",
                part=part,
            )
            return
        if _valid_staged_file(staged_path):
            try:
                payload = _load_json(staged_path, f"{name} output")
            except ValueError as error:
                _issue(
                    result,
                    code="INTERNAL.QA_ERROR",
                    stage=name,
                    message=error,
                    part=part,
                )
                return
            if payload.get("schema") != expected_schema:
                _issue(
                    result,
                    code="INTERNAL.QA_ERROR",
                    stage=name,
                    message=(
                        f"checker emitted unsupported evidence schema "
                        f"{payload.get('schema')!r}; expected {expected_schema!r}"
                    ),
                    part=part,
                    details={
                        "actual": payload.get("schema"),
                        "expected": expected_schema,
                    },
                )
                return
            staged_path.replace(output_path)
    if payload is not None:
        result["artifacts"][artifact_name] = _artifact(output_path)
        _capture_thickness_measurements(result, payload, stage=name, part=part)
        recorded, structured_errors = _record_structured_issues(
            result,
            payload,
            stage=name,
            default_code=failure_code,
            default_part=part,
        )
        errors = payload.get("errors")
        issues = payload.get("issues")
        malformed_error_fields = (
            errors is not None and not isinstance(errors, list)
        ) or (
            issues is not None and not isinstance(issues, list)
        )
        contradictory_success = (
            command.returncode == 0
            and payload.get("pass") is True
            and (
                malformed_error_fields
                or (isinstance(errors, list) and bool(errors))
                or (
                    isinstance(issues, list)
                    and any(
                        isinstance(issue, dict)
                        and issue.get("severity") == "error"
                        for issue in issues
                    )
                )
            )
        )
        if contradictory_success:
            _issue(
                result,
                code="INTERNAL.QA_ERROR",
                stage=name,
                message=(
                    "checker reported pass=true with malformed or non-empty "
                    "error evidence"
                ),
                part=part,
                details={
                    "actual": {
                        "errors": errors,
                        "issues": issues,
                        "pass": payload.get("pass"),
                        "returnCode": command.returncode,
                    },
                    "expected": {
                        "errors": [],
                        "issuesWithSeverityError": 0,
                        "pass": True,
                        "returnCode": 0,
                    },
                },
            )
        if (command.returncode != 0 or payload.get("pass") is not True) and not structured_errors:
            if isinstance(errors, list) and errors:
                for error in errors:
                    if isinstance(error, dict):
                        message = error.get("message", error.get("check", error))
                        check = error.get("check")
                        details = error
                    else:
                        message = error
                        check = str(error)
                        details = _check_details(payload, check)
                    _issue(
                        result,
                        code=failure_code,
                        stage=name,
                        message=f"automated check failed: {message}",
                        check=check if isinstance(check, str) else None,
                        part=part,
                        details=details,
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
        structured_warnings = recorded - structured_errors
        if isinstance(warnings, list) and not structured_warnings:
            for warning in warnings:
                if isinstance(warning, dict):
                    message = warning.get("message", warning.get("check", warning))
                    check = warning.get("check")
                    details = warning
                else:
                    message = warning
                    check = str(warning)
                    details = _check_details(payload, check)
                _issue(
                    result,
                    code="QA.WARNING",
                    stage=name,
                    severity="warning",
                    message=f"automated check warning: {message}",
                    check=check if isinstance(check, str) else None,
                    part=part,
                    details=details,
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
    report_path: Path,
    report: dict[str, Any],
    report_dir: Path,
    result_artifacts: dict[str, Any],
) -> list[Path]:
    # Freshness applies to outputs of this compile attempt, not immutable inputs.
    # Hash bindings validate intent/source/profile/geometry inputs independently.
    # The canonical compile log is intentionally excluded: freshness_check's
    # stdout is appended to that same file by CommandRunner, so sampling it
    # would mutate it before the evidence can be validated.
    candidates = {report_path}
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


def _current_file_binding(path: Path) -> dict[str, Any] | None:
    snapshot = stable_file_snapshot(path)
    if snapshot.get("exists") is not True or snapshot.get("stable") is not True:
        return None
    return {
        "mtime_ns": snapshot["mtime_ns"],
        "sha256": snapshot["sha256"],
        "size": snapshot["size"],
    }


def _compile_input_binding(path: Path, label: str) -> dict[str, Any]:
    snapshot = _current_file_binding(path)
    if snapshot is None:
        raise ConfigurationError(f"{label} input must be a stable regular file: {path}")
    return {"path": str(path), **snapshot}


def _record_changed_inputs(result: dict[str, Any], *, stage: str) -> set[str]:
    """Bind the run to the bytes selected before execution, not a later rewrite."""
    changed = set()
    for label, expected in result["inputBindings"].items():
        path = Path(expected["path"])
        current = _current_file_binding(path)
        before = {key: value for key, value in expected.items() if key != "path"}
        if current == before:
            continue
        changed.add(label)
        if label == "intent":
            result["intentRevision"]["verified"] = False
        _issue(
            result, code="CONTRACT.INTENT_MUTATED" if label == "intent" else "BUILD.INPUT_CHANGED",
            stage=stage, message=f"{label} input changed or became unstable during this compile: {path}",
            repair_hint="Keep the selected inputs unchanged while compile runs, then rerun with the intended files.",
            details={"field": label, "expected": before, "observed": current},
        )
    return changed


def _validate_diagnostic_candidate(
    candidate: Any, *, workspace: Path, run_id: str, marker_path: Path,
    expected_inputs: dict[str, tuple[Path, dict | None]], scene_path: Path,
) -> dict[str, dict]:
    """Validate a failed source's independent diagnostic exports, never a report."""
    if not isinstance(candidate, dict) or candidate.get("runId") != run_id or candidate.get("manufacturingValidated") is not False:
        raise ValueError("diagnostic candidate must identify this failed compile run")
    bindings = candidate.get("inputBindings")
    artifacts = candidate.get("artifacts")
    if not isinstance(bindings, dict) or not isinstance(artifacts, dict):
        raise ValueError("diagnostic candidate input/artifact bindings are missing")
    marker_mtime = marker_path.stat().st_mtime_ns
    expected = {**expected_inputs, "scene": (scene_path, None)}
    before: dict[Path, dict] = {}
    for name, (path, original) in expected.items():
        reference = bindings.get(name)
        snapshot = _current_file_binding(path)
        if not _valid_staged_file(path) or snapshot is None or not _binding_matches(reference, path, workspace):
            raise ValueError(f"diagnostic {name} input is missing, linked, unstable, or hash-mismatched")
        if name != "scene" and original is None:
            raise ValueError(f"diagnostic {name} had no stable binding before source execution")
        if original is not None and snapshot != original:
            raise ValueError(f"diagnostic {name} changed during source execution")
        if name == "scene" and snapshot["mtime_ns"] < marker_mtime:
            raise ValueError("diagnostic scene predates this compile attempt")
        before[path] = snapshot
    scene = _load_json(scene_path, "diagnostic scene")
    if validate_scene(scene, scene_path.parent):
        raise ValueError("diagnostic scene is not valid for its current intent")
    if not _binding_matches(scene.get("intentRef"), expected_inputs["intent"][0], scene_path.parent):
        raise ValueError("diagnostic scene references a different intent")
    published: dict[str, dict] = {}
    for key, field, suffix in (("step", "diagnosticStep", ".step"), ("glb", "diagnosticGlb", ".glb"), ("preview", "diagnosticPreview", ".png")):
        reference = artifacts.get(key)
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
            raise ValueError(f"diagnostic {key} artifact is missing")
        raw_path = Path(reference["path"])
        unresolved = raw_path if raw_path.is_absolute() else workspace / raw_path
        path = _workspace_path(workspace, raw_path, f"diagnostic {key}", must_exist=True)
        snapshot = _current_file_binding(path)
        if (
            not _valid_staged_file(unresolved) or snapshot is None
            or path.suffix.lower() != suffix or run_id not in path.name
            or snapshot["mtime_ns"] < marker_mtime or snapshot["size"] <= 0
            or reference.get("sha256") != snapshot["sha256"]
        ):
            raise ValueError(f"diagnostic {key} is stale, linked, unstable, or hash-mismatched")
        before[path] = snapshot
        published[field] = {"path": str(path), "sha256": snapshot["sha256"]}
    # No partial bundle is exposed if anything changes during validation.
    if any(_current_file_binding(path) != snapshot for path, snapshot in before.items()):
        raise ValueError("diagnostic bundle changed while verifying provenance")
    return published


def _validate_freshness_evidence(
    payload: Any,
    *,
    marker_path: Path,
    candidates: list[Path],
) -> list[str]:
    """Bind freshness stdout to this attempt and its exact artifact set."""

    if not isinstance(payload, dict):
        return ["freshness checker did not return a JSON object"]
    errors: list[str] = []
    marker = payload.get("marker")
    marker_resolved: Path | None = None
    if isinstance(marker, str) and marker.strip():
        marker_resolved = Path(marker).resolve()
    if marker_resolved != marker_path.resolve():
        errors.append("freshness marker does not match the current compile attempt")
    marker_binding = _current_file_binding(marker_path)
    if marker_binding is None:
        errors.append("current compile freshness marker is missing")
    actual_marker_mtime = (
        marker_binding.get("mtime_ns") if marker_binding is not None else None
    )
    marker_mtime = payload.get("marker_mtime_ns")
    if (
        actual_marker_mtime is None
        or not isinstance(marker_mtime, int)
        or isinstance(marker_mtime, bool)
        or marker_mtime != actual_marker_mtime
    ):
        errors.append("freshness marker timestamp is not bound to the current marker")
    if marker_binding is not None and (
        payload.get("marker_size") != marker_binding["size"]
        or payload.get("marker_sha256") != marker_binding["sha256"]
    ):
        errors.append("freshness marker size or SHA-256 does not match the current marker")
    if payload.get("pass") is not True:
        errors.append("freshness checker did not report pass=true")

    expected = {path.resolve() for path in candidates}
    observed: set[Path] = set()
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("freshness artifacts must be a list")
        artifacts = []
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            errors.append(f"freshness artifact {index} must be an object")
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"freshness artifact {index} has no path")
            continue
        path = Path(raw_path).resolve()
        if path in observed:
            errors.append(f"freshness artifact is duplicated: {path}")
            continue
        observed.add(path)
        if path not in expected:
            errors.append(f"freshness artifact was not requested: {path}")
            continue
        current = _current_file_binding(path)
        evidence_mtime = item.get("mtime_ns")
        evidence_size = item.get("size")
        evidence_sha = item.get("sha256")
        evidence_binding_valid = (
            actual_marker_mtime is not None
            and current is not None
            and isinstance(evidence_mtime, int)
            and not isinstance(evidence_mtime, bool)
            and isinstance(evidence_size, int)
            and not isinstance(evidence_size, bool)
            and isinstance(evidence_sha, str)
            and item.get("stable") is True
            and evidence_mtime >= actual_marker_mtime
            and evidence_mtime == current["mtime_ns"]
            and evidence_size == current["size"]
            and evidence_sha == current["sha256"]
        )
        if (
            item.get("exists") is not True
            or item.get("fresh") is not True
            or not evidence_binding_valid
        ):
            errors.append(
                f"freshness artifact is missing, stale, unstable, or changed: {path}"
            )
    missing = sorted(str(path) for path in expected - observed)
    if missing:
        errors.append(f"freshness evidence omitted requested artifacts: {missing}")
    return errors


def _compile_deadline_exceeded(result: dict[str, Any]) -> bool:
    return any(
        issue.get("code") == "COMPILE.DEADLINE_EXCEEDED"
        for issue in result.get("issues", [])
    )


def _issue_identity(issue: dict[str, Any]) -> str:
    """Return a stable identity for comparing one diagnostic across attempts."""

    identity_fields = (
        "code",
        "stage",
        "check",
        "part",
        "featureId",
        "nodeId",
        "interfaceId",
        "offenderId",
        "target",
        "path",
    )
    identity = {
        field: issue[field]
        for field in identity_fields
        if issue.get(field) is not None
    }
    if issue.get("causeId"):
        identity = {"code": issue.get("code"), "causeId": issue["causeId"]}
    elif issue.get("check") == "intent_report_feature_ownership":
        # This observation examines the whole build report, even when invoked
        # during per-part QA. Its consumers are impacts, not separate causes.
        offenders = issue.get("observed", {}).get("offenders", [])
        identity = {
            "code": issue.get("code"), "check": issue.get("check"),
            "offenders": sorted({
                (item.get("feature_id"), item.get("expected_part"), item.get("observed_part"), item.get("source"))
                for item in offenders if isinstance(item, dict)
            }, key=str),
        }
    if not any(field in identity for field in identity_fields[2:]):
        if not issue.get("causeId"):
            # Tracebacks and run prefixes change without changing the exception.
            lines = str(issue.get("message", "")).strip().splitlines()
            message = lines[-1].strip() if lines else ""
            message = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<run>", message, flags=re.I)
            message = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+(?:Z| UTC)?", "<time>", message)
            message = re.sub(r"0x[0-9a-fA-F]+", "<address>", message)
            message = re.sub(r"\bline \d+\b", "line <n>", message)
            identity["message"] = message
    payload = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:16]


def _aggregate_issues(issues: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], dict] = {}
    for issue in issues:
        issue_id = _issue_identity(issue)
        key = (issue_id, issue.get("severity", "error"))
        if key not in groups:
            groups[key] = {**issue, "id": issue_id, "occurrenceCount": 0, "affectedParts": [], "affectedStages": []}
        group = groups[key]
        group["occurrenceCount"] += 1
        for source, target in (("part", "affectedParts"), ("stage", "affectedStages")):
            if issue.get(source) is not None and issue[source] not in group[target]:
                group[target].append(issue[source])
    for group in groups.values():
        group["affectedParts"].sort()
        group["affectedStages"].sort()
    return list(groups.values())


def _repair_issue_record(issue: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "code",
        "stage",
        "check",
        "part",
        "featureId",
        "nodeId",
        "interfaceId",
        "blockedBy",
        "affectedParts",
        "affectedStages",
        "causeId",
    )
    return {
        "id": _issue_identity(issue),
        **{
            field: issue[field]
            for field in fields
            if issue.get(field) is not None
        },
    }


def _capture_thickness_measurements(
    result: dict[str, Any], payload: dict[str, Any], *, stage: str, part: str | None,
) -> None:
    """Keep actual check observations, including passes; missing warnings prove nothing."""
    if payload.get("schema") != "evidence-mesh-audit/v3":
        return
    mesh_path = Path(payload["stl"]) if isinstance(payload.get("stl"), str) else None
    mesh_hash = _digest(mesh_path) if mesh_path and mesh_path.is_file() else None
    for check in payload.get("checks", []):
        if not isinstance(check, dict) or check.get("name") not in THICKNESS_CHECKS:
            continue
        observed = check.get("observed")
        if check.get("status") not in {"pass", "warning"} or not isinstance(observed, dict):
            continue
        identity = {"code": "QA.WARNING", "stage": stage, "part": part, "check": check["name"]}
        sampling = observed.get("sampling", {})
        result.setdefault("warningMeasurements", []).append({
            **identity, "id": _issue_identity(identity), "status": check["status"],
            "context": observed.get("measurement_context"),
            "expected": check.get("expected"),
            "profileHash": (payload.get("printer_profile") or {}).get("sha256"),
            "measurements": {
                key: observed[key] for key in
                ("minimum_mm", "p05_mm", "violating_area_ratio", "violating_count", "sample_count")
                if isinstance(observed.get(key), (int, float))
                and not isinstance(observed[key], bool) and math.isfinite(observed[key])
            },
            "sampling": {key: value for key, value in sampling.items() if key != "minimum_sample"},
            "minimumSample": sampling.get("minimum_sample"),
            "riskBoundsMm": observed.get("risk_bounds_mm"),
            "meshHash": mesh_hash, "runId": result.get("runId"),
        })


def _warning_coordinate_matrix(record: dict[str, Any]):
    context = record.get("context") or {}
    if not isinstance(context, dict):
        return None
    frame = context.get("coordinate_frame") or {}
    if not isinstance(frame, dict):
        return None
    if (frame.get("status") != "bound" or frame.get("part") != record.get("part")
            or frame.get("name") not in {"part-print", "plate-print"}
            or not frame.get("artifact_key")):
        return None
    # The v1 thickness context uses mm and a unit-scale rigid matrix. Keep any
    # explicit unit/scale metadata subject to that same contract.
    if any(item.get("units", "mm") != "mm" or item.get("scale", 1) != 1
           for item in (context, frame)):
        return None
    matrix, _ = validated_rigid_matrix(frame.get("semantic_to_mesh"))
    if matrix is not None and not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), rtol=0, atol=1e-7):
        return None
    return matrix


def _warning_semantic_risk(record: dict[str, Any], matrix):
    """Normalize the measured point and all risk-AABB corners, never a claimed location."""
    if matrix is None:
        return None
    try:
        point = np.asarray((record.get("minimumSample") or {}).get("point_mm"), dtype=float)
        if point.shape != (3,) or not np.isfinite(point).all():
            return None
        inverse = np.eye(4)
        inverse[:3, :3] = matrix[:3, :3].T
        inverse[:3, 3] = -inverse[:3, :3] @ matrix[:3, 3]
        values = [inverse[:3, :3] @ point + inverse[:3, 3]]
        if record.get("riskBoundsMm") is not None:
            values.append(transform_bounds(record["riskBoundsMm"], inverse).ravel())
        return np.concatenate(values)
    except (TypeError, ValueError):
        return None


def _warning_measurement_history(
    result: dict[str, Any], previous: dict[str, Any] | None, *,
    intent_hash: str | None, workspace: str,
) -> list[dict[str, Any]]:
    """Compare independently sampled observations, never infer a repair's cause."""
    same_scope = bool(previous and intent_hash and result.get("model")
                      and previous.get("intentHash") == intent_hash
                      and previous.get("model") == result.get("model")
                      and previous.get("workspace") == workspace)
    prior = {item["id"]: item for item in (previous or {}).get("warningMeasurements", [])
             if same_scope and isinstance(item, dict) and isinstance(item.get("id"), str)}
    current = {item["id"]: item for item in result.get("warningMeasurements", [])}
    comparisons = []
    for identity in sorted(prior.keys() | current.keys()):
        old, new = prior.get(identity), current.get(identity)
        if not any(item and item.get("status") == "warning" for item in (old, new)):
            continue
        record = new or old
        assert record is not None
        old_context = (old or {}).get("context") or {}
        context = (new or {}).get("context") or {}
        old_context = old_context if isinstance(old_context, dict) else {}
        context = context if isinstance(context, dict) else {}
        frame = context.get("coordinate_frame") or {}
        old_frame = old_context.get("coordinate_frame") or {}
        frame = frame if isinstance(frame, dict) else {}
        old_frame = old_frame if isinstance(old_frame, dict) else {}
        old_matrix, matrix = (_warning_coordinate_matrix(item or {}) for item in (old, new))
        basis_changed = ({key: value for key, value in old_context.items() if key != "coordinate_frame"}
                         != {key: value for key, value in context.items() if key != "coordinate_frame"})
        reasons = []
        if old and new:
            if (not context or basis_changed
                    or {key: value for key, value in old_frame.items() if key != "semantic_to_mesh"}
                    != {key: value for key, value in frame.items() if key != "semantic_to_mesh"}):
                reasons.append("measurement context changed or missing")
            if old_matrix is None or matrix is None:
                reasons.append("coordinate frame binding is missing or not a unit-scale rigid mm transform")
            elif not np.allclose(old_matrix[:3, :3], matrix[:3, :3], rtol=0, atol=1e-8):
                reasons.append("print rotation changed")
            if (not new.get("part") or frame.get("part") != new.get("part")
                    or not new.get("profileHash") or new.get("expected") is None):
                reasons.append("part, profile or target binding is missing")
            if old.get("expected") != new.get("expected") or old.get("profileHash") != new.get("profileHash"):
                reasons.append("target or profile changed")
        comparable = bool(old and new and not reasons)
        status = ("not_remeasured" if new is None else "first_measurement" if old is None
                  else "measured" if comparable else "not_comparable")
        before = (old or {}).get("measurements", {})
        after = (new or {}).get("measurements", {})
        old_risk, risk = (_warning_semantic_risk(item or {}, transform)
                          for item, transform in ((old, old_matrix), (new, matrix)))
        risk_changed = None
        if comparable and old_risk is not None and risk is not None:
            # QA reports risk bounds to five decimal places; this is comparison
            # precision, not a change to any physical audit threshold.
            risk_changed = old_risk.shape != risk.shape or not np.allclose(old_risk, risk, rtol=0, atol=1e-5)
        comparisons.append({
            "id": identity, "part": record.get("part"), "check": record["check"],
            "status": status, "previousRunId": (old or {}).get("runId"),
            "measurements": {
                key: [before.get(key), after.get(key),
                      round(after[key] - before[key], 8) if comparable and key in before and key in after else None]
                for key in ("minimum_mm", "p05_mm", "violating_area_ratio") if key in before or key in after
            },
            "changes": {
                "samplingChanged": old.get("sampling") != new.get("sampling") if old and new else None,
                "riskLocationChanged": risk_changed,
                "meshChanged": old["meshHash"] != new["meshHash"] if old and new and old.get("meshHash") and new.get("meshHash") else None,
                "coordinateFrameChanged": old_frame != frame if old and new else None,
                "translationNormalized": bool(comparable and not np.array_equal(old_matrix[:3, 3], matrix[:3, 3])) if old and new else None,
                "measurementBasisChanged": basis_changed if old and new else None,
                "currentCheckStatus": (new or {}).get("status", "not_remeasured"),
                "notComparableReasons": reasons,
            },
            "basis": "values=[previous,current,delta]; riskLocationChanged uses assembly-semantic (1e-5 mm precision); independent samples, not causal evidence",
        })
    result["warningComparisons"] = comparisons
    # A source/QA failure must not erase the last measured warning or mark it resolved.
    return list({**prior, **current}.values())


def _read_previous_repair_state(
    path: Path,
    *,
    intent_hash: str | None,
    lineage: dict | None = None,
) -> dict[str, Any] | None:
    try:
        previous = _load_json(path, "repair state")
    except (OSError, ValueError):
        return None
    if previous.get("schema") != REPAIR_STATE_SCHEMA:
        return None
    if previous.get("intentHash") != intent_hash:
        if not lineage or not lineage.get("verified") or previous.get("intentHash") not in lineage.get("ancestorHashes", []):
            return None
        previous_workspace = previous.get("workspace")
        if isinstance(previous_workspace, str):
            previous_workspace = str(Path(previous_workspace).resolve())
        if previous.get("model") not in {None, lineage.get("model")} or previous_workspace not in {None, lineage.get("workspace")}:
            return None
    return previous


def _write_repair_state(
    result: dict[str, Any],
    *,
    result_path: Path,
) -> Path:
    """Persist a compact factual ledger without imposing a workflow state machine."""

    intent_path = Path(str(result.get("inputs", {}).get("intent", "")))
    source_path = Path(str(result.get("inputs", {}).get("source", "")))
    intent_hash = _digest(intent_path) if intent_path.is_file() else None
    source_hash = _digest(source_path) if source_path.is_file() else None
    state_path = result_path.with_name(
        f"{result.get('model', 'cad')}_repair-state.json"
    )
    if result.get("inputs", {}).get("workspace"):
        state_path = Path(result["inputs"]["workspace"]).resolve() / state_path.name
    previous = _read_previous_repair_state(
        state_path,
        intent_hash=intent_hash,
        lineage=result.get("intentRevision"),
    )

    error_issues = [
        issue
        for issue in result.get("issues", [])
        if isinstance(issue, dict) and issue.get("severity") == "error"
    ]
    blocked = [
        _repair_issue_record(issue)
        for issue in error_issues
        if issue.get("blockedBy") is not None or issue.get("status") == "blocked"
    ]
    failed = [
        _repair_issue_record(issue)
        for issue in error_issues
        if issue.get("blockedBy") is None and issue.get("status") != "blocked"
    ]
    current_failed = {item["id"] for item in failed}
    current_blocked = {item["id"] for item in blocked}
    previous_failed = {
        item.get("id")
        for item in (previous or {}).get("failed", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    previous_blocked = {
        item.get("id")
        for item in (previous or {}).get("blocked", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    known_resolved = {
        item
        for item in (previous or {}).get("knownResolvedIssueIds", [])
        if isinstance(item, str)
    }
    current_snapshot = _load_json(intent_path, "intent") if intent_path.is_file() else None
    target_changed = result.get("intentRevision", {}).get("targetChanged", False)
    if previous and isinstance(previous.get("intentSnapshot"), dict) and isinstance(current_snapshot, dict):
        target_changed = any(
            change["classification"] == "target-change"
            for change in semantic_diff(previous["intentSnapshot"], current_snapshot)
        )
    changed_scope = bool(
        previous and previous.get("intentHash") != intent_hash
        and target_changed
    )
    disappeared = (previous_failed | previous_blocked) - current_failed - current_blocked
    if not changed_scope:
        evaluated_stages = {
            stage.get("name") for stage in result.get("stages", [])
            if isinstance(stage, dict) and stage.get("status") in {"pass", "fail"}
        }
        for artifact, stage in (("sourcePreflight", "source-preflight"), ("intentValidation", "intent-validation"), ("sceneValidation", "scene-validation")):
            if artifact in result.get("artifacts", {}):
                evaluated_stages.add(stage)
        previous_records = {
            item["id"]: item for category in ("failed", "blocked")
            for item in (previous or {}).get(category, [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        for issue_id in sorted(disappeared):
            record = previous_records[issue_id]
            if record.get("stage") not in evaluated_stages:
                blocked.append({**record, "blockedBy": "NOT_REEVALUATED"})
                current_blocked.add(issue_id)
        disappeared -= current_blocked
    # Changed requirements are not evidence that the previous defect was fixed.
    scope_changed = disappeared if changed_scope else set()
    resolved = disappeared - scope_changed
    known_resolved.update(resolved)
    delta = {
        "new": sorted(
            current_failed
            - previous_failed
            - previous_blocked
            - known_resolved
        ),
        "newlyUnblocked": sorted(current_failed & previous_blocked),
        "regressed": sorted(current_failed & known_resolved),
        "remaining": sorted(current_failed & previous_failed),
        "resolved": sorted(resolved),
        "scope_changed": sorted(scope_changed),
    }
    state = {
        "blocked": blocked,
        "delta": delta,
        "failed": failed,
        "intentHash": intent_hash,
        "intentPath": str(intent_path.resolve()),
        "intentSnapshot": current_snapshot,
        "intentLineage": result.get("intentRevision"),
        "model": result.get("model"),
        "workspace": str(Path(result.get("inputs", {}).get("workspace", result_path.parent)).resolve()),
        "knownScopeChangedIssueIds": sorted(set((previous or {}).get("knownScopeChangedIssueIds", [])) | scope_changed),
        "knownResolvedIssueIds": sorted(known_resolved),
        "passedStages": sorted(
            stage.get("name")
            for stage in result.get("stages", [])
            if isinstance(stage, dict)
            and stage.get("status") == "pass"
            and isinstance(stage.get("name"), str)
        ),
        "previousRunId": (previous or {}).get("runId"),
        "runId": result.get("runId"),
        "schema": REPAIR_STATE_SCHEMA,
        "sourceHash": source_hash,
        "updatedAt": result.get("finishedAt"),
    }
    state["warningMeasurements"] = _warning_measurement_history(
        result, previous, intent_hash=intent_hash, workspace=state["workspace"],
    )
    _write_json(state_path, state)
    result["repairDelta"] = delta
    return state_path


def _finish(
    result: dict[str, Any],
    *,
    result_path: Path,
    log_path: Path,
) -> dict[str, Any]:
    result["finishedAt"] = _utc_now()
    result["issueOccurrences"] = result["issues"]
    result["issues"] = _aggregate_issues(result["issues"])
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
    try:
        if result.get("intentRevision", {}).get("verified") is not False:
            repair_state_path = _write_repair_state(result, result_path=result_path)
            result["artifacts"]["repairState"] = _artifact(repair_state_path)
    except Exception as error:
        _issue(
            result,
            code="INTERNAL.REPAIR_STATE_UNAVAILABLE",
            stage="repair-state",
            severity="warning",
            message=error,
        )
    _write_json(result_path, result)
    compact = {
        "artifacts": {
            key: value
            for key, value in result["artifacts"].items()
            if key
            in {
                "buildAudit",
                "buildReport",
                "capabilities",
                "diagnosticPreview",
                "diagnosticReferencePreview",
                "diagnosticRenderEvidence",
                "diagnosticStep",
                "diagnosticGlb",
                "diagnosticSourceEvidence",
                "freshnessAudit",
                "log",
                "preview",
                "referencePreview",
                "repairState",
                "intentRevision",
                "renderEvidence",
                "sourcePreflight",
            }
        },
        "backend": result.get("backend"),
        "capabilityFingerprint": result.get("capabilityFingerprint"),
        "deliveryReady": False,
        "issues": result["issues"],
        "model": result.get("model"),
        "omittedErrorCount": result["omittedErrorCount"],
        "omittedIssueCount": result["omittedIssueCount"],
        "pass": result["pass"],
        "repairDelta": result.get("repairDelta", {}),
        "warningComparisons": result.get("warningComparisons", []),
        "result": {"path": str(result_path)},
        "runId": result.get("runId"),
        "schema": RESULT_SCHEMA,
        "status": result["status"],
        "visualReviewRequired": True,
    }
    for key in (
        "colors",
        "deliverables",
        "physicalParts",
        "printOrientationEvidence",
    ):
        if result.get(key):
            compact[key] = result[key]
    return compact


def compile_cad(
    options: CompileOptions,
    *,
    runner_factory: Any = CommandRunner,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Execute one evidence build and return its compact result."""

    workspace = options.workspace.resolve()
    if not workspace.is_dir():
        raise ConfigurationError(f"workspace is not a directory: {workspace}")
    for label, timeout in (
        ("compile timeout", options.compile_timeout_seconds),
        ("source timeout", options.source_timeout_seconds),
        ("check timeout", options.check_timeout_seconds),
    ):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ConfigurationError(f"{label} must be finite and positive")
    deadline = _CompileDeadline.start(options.compile_timeout_seconds, monotonic)
    if options.consistency_samples < 32:
        raise ConfigurationError("consistency samples must be at least 32")
    intent_path = _workspace_path(
        workspace, options.intent, "intent", must_exist=True
    )
    # Retain old CLI invocations as provenance only. Input timestamps do not
    # establish authorship; the compiler owns its separate output-attempt marker.
    marker_path = (_workspace_path(workspace, options.marker, "legacy generation marker", must_exist=True)
                   if options.marker is not None else None)
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

    input_bindings = {label: _compile_input_binding(path, label)
                      for label, path in (("intent", intent_path), ("source", source_path))}
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
        Path(f"{model}_report.json"),
        "build report",
    )
    run_id = str(uuid4())
    attempt_marker_path = output_dir / f".{model}-compile-{run_id}.start"
    attempt_marker_path.write_text(f"compileRunId={run_id}\n", encoding="utf-8")
    runner = runner_factory(log_path)
    result: dict[str, Any] = {
        "artifacts": {},
        "backend": None,
        "deliveryReady": False,
        "inputs": {
            "workspace": str(workspace),
            "intent": str(intent_path),
            "marker": str(marker_path) if marker_path is not None else None,
            "attemptMarker": str(attempt_marker_path),
            "scene": str(scene_path),
            "source": str(source_path),
        },
        "inputBindings": input_bindings,
        "inputBindingPolicy": "stable-inputs-per-compile/v1",
        "issues": [],
        "model": model,
        "omittedErrorCount": 0,
        "intentRevision": {"verified": False},
        "omittedIssueCount": 0,
        "runId": run_id,
        "schema": RESULT_SCHEMA,
        "stages": [],
        "startedAt": _utc_now(),
        "visualReviewRequired": True,
    }

    # Capture a verified legacy baseline before any failed attempt can replace
    # the old result pointer, including failures during contract validation.
    try:
        prior_history = load_history(workspace, model)
        if prior_history.get("headHash") and not history_path(workspace, model).exists():
            _write_json(history_path(workspace, model), prior_history)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _issue(result, code="CONTRACT.INTENT_HISTORY_UNVERIFIED", stage="intent-revision", message=error)
        return _finish(result, result_path=result_path, log_path=log_path)

    capability_path = output_dir / f"{model}_capabilities.json"
    try:
        capability_manifest = build_capability_manifest()
        _write_json(capability_path, capability_manifest)
        result["artifacts"]["capabilities"] = _artifact(capability_path)
        result["capabilityFingerprint"] = capability_manifest["fingerprint"]
    except Exception as error:
        _issue(
            result,
            code="INTERNAL.CAPABILITY_DISCOVERY_FAILED",
            stage="capability-discovery",
            message=error,
        )
        return _finish(result, result_path=result_path, log_path=log_path)

    try:
        intent = _load_json(intent_path, "intent contract")
        intent_errors = validate_intent(intent, intent_path.parent)
    except Exception as error:
        intent_errors = [str(error)]
    intent_audit_path = output_dir / f"{model}_intent-validation.json"
    _validation_artifact(
        intent_audit_path,
        schema="intent-validation/v5",
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

    diagnostic_input_paths = {"intent": intent_path, "source": source_path,
                              "profile": _resolve_reference(intent["printability"]["profile"], intent_path.parent, "profile")}
    profile_path = diagnostic_input_paths["profile"]
    try:
        input_bindings["profile"] = _compile_input_binding(profile_path, "profile")
    except ConfigurationError as error:
        _issue(result, code="BUILD.INPUT_CHANGED", stage="input-binding", message=error)
        return _finish(result, result_path=result_path, log_path=log_path)
    if input_bindings["profile"]["sha256"] != intent["printability"]["profile"]["sha256"]:
        _issue(result, code="BUILD.INPUT_CHANGED", stage="input-binding",
               message="profile bytes no longer match the validated intent binding")
        return _finish(result, result_path=result_path, log_path=log_path)
    if _record_changed_inputs(result, stage="input-binding"):
        return _finish(result, result_path=result_path, log_path=log_path)

    try:
        history, revision_audit = audit_lineage(workspace, intent_path, intent)
        _write_json(history_path(workspace, model), history)
        revision_audit_path = output_dir / f"{model}_intent-revision-audit.json"
        _write_json(revision_audit_path, revision_audit)
        result["artifacts"]["intentRevision"] = _artifact(revision_audit_path)
        result["intentRevision"] = revision_audit
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, IntentRevisionError) and error.verified_history:
            _write_json(history_path(workspace, model), error.verified_history)
        _issue(result, code="CONTRACT.INTENT_REVISION_UNVERIFIED", stage="intent-revision", message=error)
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

    if _record_changed_inputs(result, stage="input-binding"):
        return _finish(result, result_path=result_path, log_path=log_path)
    diagnostic_inputs = {name: (path, {key: value for key, value in input_bindings[name].items() if key != "path"})
                         for name, path in diagnostic_input_paths.items()}
    report_path.unlink(missing_ok=True)
    source_diagnostics_path = output_dir / f".{model}-source-diagnostics-{run_id}.json"
    source_command = _run_with_deadline(
        runner,
        deadline,
        "source",
        [sys.executable, str(source_path)],
        cwd=workspace,
        timeout_seconds=options.source_timeout_seconds,
        env_extra={
            "AMAGINE3D_INTENT_PATH": str(intent_path),
            "AMAGINE3D_OUTPUT_DIR": str(output_dir),
            "AMAGINE3D_COMPILE_RUN_ID": run_id,
            "AMAGINE3D_SCENE_PATH": str(scene_path),
            "AMAGINE3D_SOURCE_PHASE": "compile",
            "AMAGINE3D_SOURCE_DIAGNOSTICS_PATH": str(source_diagnostics_path),
            "PYTHONPATH": str(Path(__file__).resolve().parent)
            + (
                os.pathsep + os.environ["PYTHONPATH"]
                if os.environ.get("PYTHONPATH")
                else ""
            ),
        },
    )
    _stage_record(result, "source", source_command)
    changed_inputs = _record_changed_inputs(result, stage="source")
    intent_unchanged = "intent" not in changed_inputs
    if source_command.timed_out or source_command.returncode != 0:
        structured_errors = 0
        if source_diagnostics_path.exists():
            try:
                payload = _load_json(source_diagnostics_path, "source diagnostics")
                binding = _current_file_binding(source_diagnostics_path)
                if (
                    not _valid_staged_file(source_diagnostics_path) or binding is None
                    or binding["mtime_ns"] < attempt_marker_path.stat().st_mtime_ns
                    or payload.get("schema") != SOURCE_DIAGNOSTICS_SCHEMA
                    or payload.get("runId") != run_id or payload.get("pass") is not False
                ):
                    raise ValueError("source diagnostics are not bound to this compile attempt")
                result["artifacts"]["sourceDiagnostics"] = _artifact(source_diagnostics_path)
                _, structured_errors = _record_structured_issues(result, payload, stage="source", default_code="SOURCE.EXECUTION_FAILED")
                if "diagnosticCandidate" in payload and intent_unchanged and not source_command.timed_out:
                    try:
                        candidate = payload["diagnosticCandidate"]
                        diagnostic_artifacts = _validate_diagnostic_candidate(
                            candidate, workspace=workspace, run_id=run_id,
                            marker_path=attempt_marker_path, expected_inputs=diagnostic_inputs,
                            scene_path=scene_path,
                        )
                        diagnostic_evidence_path = output_dir / f"{model}-{run_id}-diagnostic-evidence.json"
                        _write_json(diagnostic_evidence_path, {
                            "schema": "evidence-cad-source-diagnostic-artifacts/v1",
                            "purpose": "diagnostic", "manufacturingValidated": False,
                            "runId": run_id, "inputBindings": candidate["inputBindings"],
                            "artifacts": diagnostic_artifacts,
                        })
                        result["artifacts"].update(diagnostic_artifacts)
                        result["artifacts"]["diagnosticSourceEvidence"] = _artifact(diagnostic_evidence_path)
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        _issue(result, code="SOURCE.DIAGNOSTIC_PROVENANCE_INVALID", stage="source", severity="warning", message=error)
            except (OSError, ValueError) as error:
                _issue(result, code="INTERNAL.SOURCE_DIAGNOSTICS_INVALID", stage="source", severity="warning", message=error)
        if not structured_errors or source_command.timed_out:
            _record_command_failure(
                result,
                source_command,
                stage="source",
                failure_code="SOURCE.EXECUTION_FAILED",
                timeout_code="SOURCE.TIMEOUT",
                internal_code="INTERNAL.SOURCE_RUNNER_ERROR",
            )
        if not _is_deferred_source_failure(source_command, report_path):
            return _finish(result, result_path=result_path, log_path=log_path)
    if changed_inputs:
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
    if not report_path.is_file():
        _issue(
            result,
            code="BUILD.REPORT_MISSING",
            stage="build-report",
            message=f"compiler did not produce the declared report: {report_path}",
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
    if report.get("runId") != run_id:
        _issue(
            result,
            code="BUILD.REPORT_STALE",
            stage="build-report",
            message=(
                "build report is not bound to the current compile run: "
                f"expected {run_id}, observed {report.get('runId')!r}"
            ),
        )
        return _finish(result, result_path=result_path, log_path=log_path)
    result["artifacts"]["buildReport"] = _artifact(report_path)
    report_backend = report.get("backend")
    allowed_backends = {"brep-part", "brep-assembly", "brep-color-regions"}
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
        deadline,
        name="build-check",
        argv=[
            sys.executable,
            str(Path(__file__).resolve().with_name("build_check.py")),
            str(report_path),
        ],
        cwd=workspace,
        timeout_seconds=options.check_timeout_seconds,
        output_path=build_audit_path,
        artifact_name="buildAudit",
        failure_code="BUILD.REPORT_INVALID",
        expected_schema="evidence-a3d-build-audit/v1",
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
    result.update(_report_agent_facts(report))
    profile_path = _resolve_reference(report_inputs["profile"], report_dir, "profile")

    # Render only current, validated source/scene/report geometry, before QA.
    # The diagnostic bundle never replaces the last successful render pointer.
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
    preview_path = output_dir / f"{model}_{run_id}_views.png"
    reference_view = intent["visual"]["reference_view"]
    reference_preview_path = (
        output_dir / f"{model}_{run_id}_{reference_view}-view.png"
    )
    render_audit_path = output_dir / f"{model}_{run_id}_diagnostic-render.json"
    with tempfile.TemporaryDirectory(
        dir=output_dir,
        prefix=f".{model}-render-",
    ) as render_directory:
        render_stage = Path(render_directory)
        staged_preview = render_stage / preview_path.name
        staged_reference = render_stage / reference_preview_path.name
        staged_evidence = render_stage / render_audit_path.name
        render_command = _run_with_deadline(
            runner,
            deadline,
            "render",
            [
                sys.executable,
                str(Path(__file__).resolve().with_name("render_preview.py")),
                str(display_path),
                "--out",
                str(staged_preview),
                "--report",
                str(staged_evidence),
                "--reference-view",
                reference_view,
                "--reference-out",
                str(staged_reference),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
        )
        _stage_record(result, "render", render_command)
        if render_command.timed_out or render_command.returncode != 0:
            _record_command_failure(
                result,
                render_command,
                stage="render",
                failure_code="VISUAL.RENDER_FAILED",
                timeout_code="VISUAL.RENDER_TIMEOUT",
                internal_code="INTERNAL.RENDER_ERROR",
            )
        elif not all(
            _valid_staged_file(path)
            for path in (staged_preview, staged_reference, staged_evidence)
        ):
            _issue(
                result,
                code="INTERNAL.RENDER_ERROR",
                stage="render",
                message="renderer did not produce its complete run-scoped evidence bundle",
            )
        else:
            try:
                render_evidence = _load_json(staged_evidence, "render evidence")
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
                    == staged_preview
                    and preview_reference.get("sha256") == _digest(staged_preview)
                )
                matched_reference = render_evidence.get("matched_view")
                matched_bound = (
                    isinstance(matched_reference, dict)
                    and matched_reference.get("name") == reference_view
                    and Path(str(matched_reference.get("path", ""))).resolve()
                    == staged_reference
                    and matched_reference.get("sha256") == _digest(staged_reference)
                )
                if (
                    render_evidence.get("schema") != "evidence-render/v2"
                    or display_reference.get("sha256") != display_hash
                    or not bound
                    or not preview_bound
                    or not matched_bound
                ):
                    raise ValueError(
                        "render evidence is not hash-bound to the current display GLB and previews"
                    )
                _publish_render_bundle(
                    staged_preview=staged_preview,
                    staged_reference=staged_reference,
                    preview_path=preview_path,
                    reference_preview_path=reference_preview_path,
                    render_audit_path=render_audit_path,
                    render_evidence={
                        **render_evidence,
                        "purpose": "diagnostic",
                        "inputs": report["inputs"],
                    },
                    run_id=run_id,
                )
                result["artifacts"]["diagnosticPreview"] = _artifact(preview_path)
                result["artifacts"]["diagnosticReferencePreview"] = _artifact(
                    reference_preview_path
                )
                result["artifacts"]["diagnosticRenderEvidence"] = _artifact(render_audit_path)
            except Exception as error:
                _issue(
                    result,
                    code="VISUAL.RENDER_EVIDENCE_INVALID",
                    stage="render",
                    message=error,
                )

    if _compile_deadline_exceeded(result):
        return _finish(result, result_path=result_path, log_path=log_path)

    # Installation evidence is opt-in by functional requirement, independent of
    # component appearance and evaluated against the final semantic STEP parts.
    if scene.get("installationChecks"):
        _run_json_check(
            result, runner, deadline, name="installation-qa",
            argv=[sys.executable, str(Path(__file__).resolve().with_name("installation_audit.py")), str(report_path)],
            cwd=workspace, timeout_seconds=options.check_timeout_seconds,
            output_path=output_dir / f"{model}_installation-audit.json",
            artifact_name="installationAudit", failure_code="QA.INSTALLATION_FAILED",
            expected_schema="evidence-installation-audit/v1",
        )
        if _compile_deadline_exceeded(result):
            return _finish(result, result_path=result_path, log_path=log_path)

    # Interface and assembly evidence is the cheapest high-value multipart gate.
    # Run it before per-artifact QA so disconnected structures fail in one report.
    if len(parts) > 1 and "stl" in artifacts:
        assembly_audit_path = output_dir / f"{model}_assembly-audit.json"
        _run_json_check(
            result,
            runner,
            deadline,
            name="assembly-qa",
            argv=[
                sys.executable,
                str(Path(__file__).resolve().with_name("assembly_check.py")),
                str(report_path),
                str(_resolve_reference(artifacts["stl"], report_dir, "stl")),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=assembly_audit_path,
            artifact_name="assemblyAudit",
            failure_code="QA.ASSEMBLY_FAILED",
            expected_schema="evidence-assembly-audit/v1",
        )

    mesh_script = Path(__file__).resolve().with_name("qa_check.py")
    mesh_targets: list[tuple[str, Path, int]] = []
    for part_id in sorted(parts):
        key = f"stl:{part_id}"
        if key in artifacts:
            mesh_targets.append(
                (part_id, _resolve_reference(artifacts[key], report_dir, key), 1)
            )
    if "stl" in artifacts:
        for plate in print_plates(report):
            key = plate["stlKey"]
            target = "plate" if plate["id"] == "01" else f"plate-{plate['id']}"
            mesh_targets.append((target, _resolve_reference(artifacts[key], report_dir, key), len(plate["parts"])))
    for target, mesh_path, components in mesh_targets:
        audit_path = output_dir / f"{model}_{target}-mesh-audit.json"
        _run_json_check(
            result,
            runner,
            deadline,
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
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=audit_path,
            artifact_name=f"meshAudit:{target}",
            failure_code="QA.MESH_FAILED",
            expected_schema="evidence-mesh-audit/v3",
            part=target if target in parts else None,
        )
        if _compile_deadline_exceeded(result):
            return _finish(result, result_path=result_path, log_path=log_path)

    for artifact_key in sorted(
        key for key in artifacts if key.startswith("step:")
    ):
        token = artifact_key.removeprefix("step:")
        step_path = _resolve_reference(artifacts[artifact_key], report_dir, artifact_key)
        audit_path = output_dir / f"{model}_{token}-step-audit.json"
        _run_json_check(
            result,
            runner,
            deadline,
            name=f"step-qa:{token}",
            argv=[
                sys.executable,
                str(Path(__file__).resolve().with_name("step_check.py")),
                str(step_path),
                "--intent",
                str(intent_path),
                "--report",
                str(report_path),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=audit_path,
            artifact_name=f"stepAudit:{token}",
            failure_code="QA.STEP_FAILED",
            expected_schema="evidence-step-audit/v1",
            part=token if token != "assembly" else None,
        )
        if _compile_deadline_exceeded(result):
            return _finish(result, result_path=result_path, log_path=log_path)

    for plate in (print_plates(report) if "3mf" in artifacts else []):
        tag = "" if len(print_plates(report)) == 1 else f":{plate['id']}"
        file_tag = "" if not tag else f"_plate-{plate['id']}"
        three_mf_key = plate["threeMfKey"]
        three_mf_path = _resolve_reference(artifacts[three_mf_key], report_dir, three_mf_key)
        color_audit_path = output_dir / f"{model}{file_tag}_color-audit.json"
        _run_json_check(
            result,
            runner,
            deadline,
            name="color-qa" + tag,
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
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=color_audit_path,
            artifact_name="colorAudit" + tag,
            failure_code="QA.COLOR_FAILED",
            expected_schema="evidence-color-print-package-audit/v1",
        )
        if _compile_deadline_exceeded(result):
            return _finish(result, result_path=result_path, log_path=log_path)
        package_audit_path = output_dir / f"{model}{file_tag}_color-assembly-audit.json"
        _run_json_check(
            result,
            runner,
            deadline,
            name="color-assembly-qa" + tag,
            argv=[
                sys.executable,
                str(Path(__file__).resolve().parent / "color" / "assembly_check.py"),
                str(report_path),
                str(three_mf_path),
            ],
            cwd=workspace,
            timeout_seconds=options.check_timeout_seconds,
            output_path=package_audit_path,
            artifact_name="colorAssemblyAudit" + tag,
            failure_code="QA.COLOR_ASSEMBLY_FAILED",
            expected_schema="evidence-assembly-audit/v1",
        )
        if _compile_deadline_exceeded(result):
            return _finish(result, result_path=result_path, log_path=log_path)

    if any(issue["severity"] == "error" for issue in result["issues"]):
        return _finish(result, result_path=result_path, log_path=log_path)

    for warning in report.get("warnings", []):
        _issue(
            result,
            code="BUILD.WARNING",
            stage="build-report",
            severity="warning",
            message=warning,
        )

    if _record_changed_inputs(result, stage="input-binding"):
        return _finish(result, result_path=result_path, log_path=log_path)
    freshness_path = output_dir / f"{model}_freshness-audit.json"
    freshness_inputs = _freshness_candidates(
        report_path=report_path,
        report=report,
        report_dir=report_dir,
        result_artifacts=result["artifacts"],
    )
    freshness_command = _run_with_deadline(
        runner,
        deadline,
        "freshness",
        [
            sys.executable,
            str(Path(__file__).resolve().with_name("freshness_check.py")),
            "--after",
            str(attempt_marker_path),
            *[str(path) for path in freshness_inputs],
        ],
        cwd=workspace,
        timeout_seconds=options.check_timeout_seconds,
    )
    _stage_record(result, "freshness", freshness_command)
    if freshness_command.timed_out:
        _record_command_failure(
            result,
            stage="freshness",
            command=freshness_command,
            failure_code="FRESHNESS.CHECK_FAILED",
            timeout_code="FRESHNESS.TIMEOUT",
            internal_code="INTERNAL.FRESHNESS_ERROR",
        )
    else:
        freshness = _json_from_tail(freshness_command.output_tail)
        freshness_errors = _validate_freshness_evidence(
            freshness,
            marker_path=attempt_marker_path,
            candidates=freshness_inputs,
        )
        if freshness_command.returncode != 0:
            freshness_errors.insert(
                0,
                f"freshness checker exited with code {freshness_command.returncode}",
            )
        if not freshness_errors and isinstance(freshness, dict):
            freshness_evidence = {
                **freshness,
                "runId": run_id,
                "schema": "evidence-cad-compile-freshness/v1",
            }
            _write_json(freshness_path, freshness_evidence)
            result["artifacts"]["freshnessAudit"] = _artifact(freshness_path)
        if freshness_errors:
            _issue(
                result,
                code="FRESHNESS.CHECK_FAILED",
                stage="freshness",
                message="; ".join(freshness_errors),
            )
    _record_changed_inputs(result, stage="input-binding")
    if (
        not any(issue["severity"] == "error" for issue in result["issues"])
        and result.get("omittedErrorCount", 0) == 0
    ):
        diagnostic = result["artifacts"].get("diagnosticRenderEvidence")
        if diagnostic is not None:
            # Publish the canonical pointer only after all QA and freshness pass.
            # Its image paths remain immutable and specific to this run.
            try:
                binding_errors = file_binding_errors(report, report_dir)
                binding_errors.extend(file_binding_errors(
                    {"artifacts": result["artifacts"]}, workspace
                ))
                # Witness files are scene inputs, not manufacturing artifacts.
                # Recheck them before promoting the successful preview too.
                if scene.get("installationChecks"):
                    from installation_contract import validate_installations
                    binding_errors.extend(validate_installations(
                        scene["installationChecks"], intent, set(parts), scene_path.parent
                    ))
                if binding_errors:
                    raise ValueError("; ".join(binding_errors))
                evidence = _load_json(Path(diagnostic["path"]), "diagnostic render")
                evidence["purpose"] = "automated-checks-passed"
                canonical_render = output_dir / f"{model}_render.json"
                # Prepare all metadata before replacing the previous successful
                # pointer. A failed write or read must leave it intact.
                with tempfile.TemporaryDirectory(dir=output_dir, prefix=f".{model}-publish-") as publish_directory:
                    staged_pointer = Path(publish_directory) / f"{model}.publish.json"
                    _write_json(staged_pointer, evidence)
                    pointer_reference = _artifact(staged_pointer)
                    pointer_reference["path"] = str(canonical_render)
                    promoted_artifacts = dict(result["artifacts"])
                    promoted_artifacts["renderEvidence"] = pointer_reference
                    promoted_artifacts["preview"] = promoted_artifacts.pop("diagnosticPreview")
                    promoted_artifacts["referencePreview"] = promoted_artifacts.pop("diagnosticReferencePreview")
                    del promoted_artifacts["diagnosticRenderEvidence"]
                    staged_pointer.replace(canonical_render)
                    result["artifacts"] = promoted_artifacts
            except Exception as error:
                _issue(result, code="VISUAL.RENDER_EVIDENCE_INVALID", stage="render", message=error)
    return _finish(result, result_path=result_path, log_path=log_path)


def _positive_timeout(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path, help="semantic scene produced by source")
    parser.add_argument("--marker", type=Path,
                        help="optional legacy provenance file; its timestamp is not an input freshness requirement")
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--result", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument(
        "--compile-timeout-seconds",
        type=_positive_timeout,
        default=DEFAULT_COMPILE_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--source-timeout-seconds", type=_positive_timeout, default=1_800.0
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
                result=args.result,
                log=args.log,
                compile_timeout_seconds=args.compile_timeout_seconds,
                source_timeout_seconds=args.source_timeout_seconds,
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
            "runId": str(uuid4()),
            "schema": RESULT_SCHEMA,
            "status": "failed",
            "visualReviewRequired": True,
        }
    print(_summary_json(_agent_summary(result)), end="")
    return 0 if result.get("pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
