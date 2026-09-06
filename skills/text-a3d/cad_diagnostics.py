"""Structured diagnostics shared by CAD authoring and compiler subprocesses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


SOURCE_DIAGNOSTICS_SCHEMA = "evidence-cad-source-diagnostics/v1"


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
