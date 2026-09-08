"""Structured diagnostics shared by CAD authoring and compiler subprocesses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import os
from pathlib import Path
from uuid import uuid4
from typing import Any


SOURCE_DIAGNOSTICS_SCHEMA = "evidence-cad-source-diagnostics/v1"


def write_source_diagnostics(payload: Mapping[str, Any]) -> None:
    """Keep diagnostics outside truncated tool output, bound to one managed run."""
    destination = os.environ.get("AMAGINE3D_SOURCE_DIAGNOSTICS_PATH")
    phase = os.environ.get("AMAGINE3D_SOURCE_PHASE")
    run_id = os.environ.get("AMAGINE3D_DRAFT_RUN_ID" if phase == "draft" else "AMAGINE3D_COMPILE_RUN_ID")
    if phase not in {"compile", "draft"} or not destination or not run_id:
        return
    path = Path(destination)
    prior = {}
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("runId") == run_id:
                prior = value
        except (ValueError, OSError):
            pass
    issues = []
    seen = set()
    for issue in [*prior.get("issues", []), *payload.get("issues", [])]:
        key = json.dumps(issue, sort_keys=True, allow_nan=False)
        if key not in seen:
            issues.append(issue)
            seen.add(key)
    merged = {**prior, **payload, "schema": SOURCE_DIAGNOSTICS_SCHEMA,
              "runId": run_id, "issues": issues}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        temporary.write_text(json.dumps(merged, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class CadDiagnosticError(RuntimeError):
    """An actionable CAD failure with machine-readable evidence."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        check: str,
        part: str | None = None,
        observed: Any = None,
        expected: Any = None,
        repair_hint: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("diagnostic code must be a non-empty string")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("diagnostic message must be a non-empty string")
        if not isinstance(check, str) or not check.strip():
            raise ValueError("diagnostic check must be a non-empty string")
        self.code = code.strip()
        self.check = check.strip()
        self.part = part.strip() if isinstance(part, str) and part.strip() else None
        self.observed = observed
        self.expected = expected
        self.repair_hint = (
            repair_hint.strip()
            if isinstance(repair_hint, str) and repair_hint.strip()
            else None
        )
        self.details = dict(details or {})
        super().__init__(message.strip())

    def to_issue(self) -> dict[str, Any]:
        issue = {
            **self.details,
            "check": self.check,
            "code": self.code,
            "message": str(self),
            "severity": "error",
        }
        if self.part is not None:
            issue["part"] = self.part
        if self.observed is not None:
            issue["observed"] = self.observed
        if self.expected is not None:
            issue["expected"] = self.expected
        if self.repair_hint is not None:
            issue["repairHint"] = self.repair_hint
        return issue


def source_diagnostics_payload(
    diagnostics: Iterable[CadDiagnosticError | Mapping[str, Any]],
) -> dict[str, Any]:
    """Create the JSON payload understood by ``cad_compile`` source handling."""

    issues = [
        diagnostic.to_issue()
        if isinstance(diagnostic, CadDiagnosticError)
        else dict(diagnostic)
        for diagnostic in diagnostics
    ]
    return {
        "issues": issues,
        "pass": not any(issue.get("severity", "error") == "error" for issue in issues),
        "schema": SOURCE_DIAGNOSTICS_SCHEMA,
    }
