"""Auditable intent lineage and semantic changes, independent of CAD execution.

Revision evidence records the author's stated source. It is not an automatic
verification of user authorization. Geometry and build reports never supply
replacement targets to this module.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


HISTORY_SCHEMA = "evidence-cad-intent-history/v1"
INTENT_SCHEMA = "evidence-cad-intent/v5"
REVISION_KINDS = {"parameter-adjustment", "target-change", "evidence-correction"}


class IntentRevisionError(ValueError):
    """A rejected revision may still have a verified legacy baseline to retain."""
    def __init__(self, message: str, verified_history: dict | None = None):
        super().__init__(message)
        self.verified_history = deepcopy(verified_history)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return result


def _canonical(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: _canonical(item, name) for name, item in sorted(value.items()) if name != "revision"}
    if isinstance(value, list):
        identity = "id" if key in {"features", "interfaces"} else "name"
        if value and all(isinstance(item, dict) and isinstance(item.get(identity), str) for item in value):
            return {item[identity]: _canonical(item) for item in sorted(value, key=lambda item: item[identity])}
        values = [_canonical(item) for item in value]
        if all(isinstance(item, str) for item in values):
            return sorted(values)
        return values
    return value


def semantic_diff(parent: dict, current: dict) -> list[dict]:
    """Order-independent field changes, with bounded parameters distinguished."""
    changes: list[dict] = []

    def visit(before: Any, after: Any, path: str) -> None:
        if before == after:
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before.keys() | after.keys()):
                visit(before.get(key), after.get(key), f"{path}/{key}")
            return
        classification = "target-change"
        fields = path.strip("/").split("/")
        if fields[0] == "assumptions" or path == "/visual/reference_view":
            classification = "metadata"
        if len(fields) == 3 and fields[0] == "dimensions_mm" and fields[2] == "value":
            original = parent.get("dimensions_mm", {}).get(fields[1], {})
            updated = current.get("dimensions_mm", {}).get(fields[1], {})
            constraint = original.get("constraint", {})
            if (
                constraint.get("kind") == "range"
                and constraint == updated.get("constraint")
                and isinstance(after, (int, float)) and not isinstance(after, bool)
                and constraint.get("min_mm", float("inf")) <= after <= constraint.get("max_mm", float("-inf"))
            ):
                classification = "parameter-adjustment"
        changes.append({"path": path, "before": before, "after": after, "classification": classification})

    visit(_canonical(parent), _canonical(current), "")
    return changes


def _parent_path(document: dict, base_dir: Path) -> Path:
    value = Path(document["revision"]["parent"]["path"])
    return value.resolve() if value.is_absolute() else (base_dir / value).resolve()


def validate_revision(document: dict, base_dir: Path | None = None) -> list[str]:
    revision = document.get("revision")
    if not isinstance(revision, dict):
        return ["revision must be an object"]
    errors: list[str] = []
    parent = revision.get("parent")
    if not isinstance(parent, dict) or not isinstance(parent.get("path"), str) or not parent["path"].strip():
        errors.append("revision.parent.path is required")
    if not isinstance(parent, dict) or not isinstance(parent.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", parent["sha256"]):
        errors.append("revision.parent.sha256 must be a lowercase SHA-256 digest")
    kind = revision.get("kind")
    if kind not in REVISION_KINDS:
        errors.append("revision.kind must be parameter-adjustment, target-change or evidence-correction")
    if not isinstance(revision.get("reason"), str) or not revision["reason"].strip():
        errors.append("revision.reason is required")
    required_evidence = {"target-change": "user-request", "evidence-correction": "external-evidence"}.get(kind)
    if required_evidence:
        evidence = revision.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("kind") != required_evidence or not isinstance(evidence.get("text"), str) or not evidence["text"].strip():
            errors.append(f"revision.evidence must record {required_evidence} and its text")
        elif kind == "evidence-correction" and (not isinstance(evidence.get("reference"), str) or not evidence["reference"].strip()):
            errors.append("revision.evidence.reference must identify the external evidence")
    if errors or base_dir is None:
        return errors
    try:
        path = _parent_path(document, base_dir)
        original = _read(path)
        if digest(path) != parent["sha256"]:
            return ["revision parent hash does not match its immutable file"]
        if original.get("schema") != INTENT_SCHEMA or original.get("part") != document.get("part"):
            return ["revision parent must be an intent for the same model"]
        if kind == "parameter-adjustment" and any(item["classification"] == "target-change" for item in semantic_diff(original, document)):
            errors.append("parameter-adjustment changes a fixed target, requirement, or allowed range; declare the request/evidence behind a target revision")
        if kind == "evidence-correction":
            reference = revision["evidence"]["reference"]
            evidence_path = Path(reference)
            if not evidence_path.is_absolute():
                evidence_path = base_dir / evidence_path
            if evidence_path.is_file():
                try:
                    evidence_document = _read(evidence_path)
                except (ValueError, OSError):
                    evidence_document = {}
                schema = evidence_document.get("schema", "")
                if isinstance(schema, str) and (schema.startswith("evidence-a3d-build/") or schema.startswith("evidence-cad-compile-result/") or schema.startswith("evidence-cad-repair-state/")):
                    errors.append("generated build/compile evidence cannot establish replacement intent targets")
    except (OSError, ValueError, KeyError) as error:
        errors.append(f"revision parent cannot be verified: {error}")
    return errors


def history_path(workspace: Path, model: str) -> Path:
    return workspace / f".{model}_intent-history.json"


def _entry(path: Path, document: dict, *, changes: list[dict] | None = None) -> dict:
    return {
        "path": str(path), "sha256": digest(path), "snapshot": deepcopy(document),
        "parentHash": document.get("revision", {}).get("parent", {}).get("sha256"),
        "changes": changes or [],
    }


def _inside(workspace: Path, path: Path) -> bool:
    return path.is_relative_to(workspace)


def load_history(workspace: Path, model: str) -> dict:
    """Read verified history, or migrate only the saved legacy input binding."""
    workspace = workspace.resolve()
    path = history_path(workspace, model)
    if path.exists():
        history = _read(path)
        if history.get("schema") != HISTORY_SCHEMA or history.get("model") != model or history.get("workspace") != str(workspace):
            raise ValueError("intent history identity cannot be verified")
    else:
        history = {"schema": HISTORY_SCHEMA, "workspace": str(workspace), "model": model, "entries": {}, "headHash": None}
        result_file = workspace / f"{model}_compile-result.json"
        repair_file = workspace / f"{model}_repair-state.json"
        report_file = workspace / f"{model}_report.json"
        if result_file.is_file() or repair_file.is_file() or report_file.is_file():
            prior_result = _read(result_file) if result_file.is_file() else {}
            prior_repair = _read(repair_file) if repair_file.is_file() else {}
            prior_report = _read(report_file) if report_file.is_file() else {}
            report_binding = prior_report.get("inputs", {}).get("intent", {})
            raw_path = prior_repair.get("intentPath") or prior_result.get("inputs", {}).get("intent") or report_binding.get("path")
            if not isinstance(raw_path, str):
                raise ValueError("existing intent history is unverified: no saved prior intent path; preserve and recover its binding before revising")
            previous_path = Path(raw_path)
            previous_path = previous_path.resolve() if previous_path.is_absolute() else (workspace / previous_path).resolve()
            if not _inside(workspace, previous_path):
                raise ValueError("prior intent binding is outside this session workspace")
            previous = _read(previous_path)
            previous_hash = digest(previous_path)
            expected_hash = prior_repair.get("intentHash") or report_binding.get("sha256")
            if expected_hash != previous_hash or previous.get("part") != model or previous.get("schema") != INTENT_SCHEMA:
                raise ValueError("existing intent history is unverified: prior immutable hash/model does not match")
            history["entries"][previous_hash] = _entry(previous_path, previous)
            history["headHash"] = previous_hash

    entries = history.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("intent history entries are invalid")
    head = history.get("headHash")
    if head and head not in entries:
        raise ValueError("intent history head snapshot is missing")
    # Existing paths remain immutable, even when called with a different source.
    for entry in entries.values():
        original_path = Path(entry["path"])
        if not _inside(workspace, original_path) or not original_path.is_file() or digest(original_path) != entry["sha256"] or _read(original_path) != entry["snapshot"]:
            raise ValueError("recorded immutable intent file changed or is unavailable")
    return history


def audit_lineage(workspace: Path, intent_path: Path, document: dict) -> tuple[dict, dict]:
    """Build a proposed history update. Caller publishes it atomically if valid.

    Unknown legacy history is rejected explicitly, never discarded or recovered
    by guessing intent filenames. Fresh models need no pre-existing history.
    """
    workspace, intent_path = workspace.resolve(), intent_path.resolve()
    model = document["part"]
    history = load_history(workspace, model)
    entries, head = history["entries"], history["headHash"]
    current_hash = digest(intent_path)
    changes: list[dict] = []
    ancestors: list[str] = []
    if current_hash in entries:
        if current_hash != head:
            raise ValueError("intent is an older revision; use a linked revision of the current target instead of silently reverting it")
    elif head:
        errors = validate_revision(document, intent_path.parent)
        if errors:
            raise IntentRevisionError("; ".join(errors), history)
        parent_path = _parent_path(document, intent_path.parent)
        if not _inside(workspace, parent_path):
            raise IntentRevisionError("revision parent must stay inside the session workspace", history)
        if document["revision"]["parent"]["sha256"] != head or parent_path != Path(entries[head]["path"]):
            raise IntentRevisionError("new intent must reference the current recorded parent path and hash", history)
        changes = semantic_diff(entries[head]["snapshot"], document)
        entries[current_hash] = _entry(intent_path, document, changes=changes)
        history["headHash"] = current_hash
    else:
        if "revision" in document:
            raise ValueError("revision parent has no verified session baseline; compile its root intent first")
        entries[current_hash] = _entry(intent_path, document)
        history["headHash"] = current_hash
    cursor = current_hash
    seen: set[str] = set()
    while cursor in entries and cursor not in seen:
        seen.add(cursor)
        ancestors.append(cursor)
        cursor = entries[cursor].get("parentHash")
    return history, {
        "schema": "evidence-cad-intent-revision-audit/v1", "verified": True,
        "model": model, "workspace": str(workspace), "intentHash": current_hash,
        "baselineHash": ancestors[-1], "ancestorHashes": ancestors,
        "changes": changes, "targetChanged": any(item["classification"] == "target-change" for item in changes),
        "evidenceStatus": "author-declared" if document.get("revision", {}).get("evidence") else "not-applicable",
    }
