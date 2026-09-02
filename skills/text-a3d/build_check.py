"""Audit one unified Amagine3D build report and every bound file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_manifest import (
    file_binding_errors,
    semantic_evidence_errors,
    validate_manifest,
)


AUDIT_SCHEMA = "evidence-a3d-build-audit/v1"


def audit(report_path: Path) -> dict:
    resolved = report_path.resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as error:
        errors = [f"build report cannot be read: {error}"]
    else:
        errors = validate_manifest(value)
        if isinstance(value, dict) and value.get("pass") is not True:
            errors.append("build report pass must be true before delivery")
        errors.extend(file_binding_errors(value, resolved.parent))
        errors.extend(semantic_evidence_errors(value, resolved.parent))
        if isinstance(value, dict):
            inline_export_audit = value.get("backendData", {}).get("exportAudit")
            reference = value.get("artifacts", {}).get("exportAudit")
            raw_path = reference.get("path") if isinstance(reference, dict) else None
            if isinstance(inline_export_audit, dict) and isinstance(raw_path, str):
                audit_path = Path(raw_path)
                if not audit_path.is_absolute():
                    audit_path = resolved.parent / audit_path
                try:
                    audit_file = json.loads(
                        audit_path.resolve().read_text(encoding="utf-8")
                    )
                except Exception as error:
                    errors.append(f"exportAudit artifact cannot be read: {error}")
                else:
                    if audit_file != inline_export_audit:
                        errors.append(
                            "exportAudit artifact content does not match "
                            "backendData.exportAudit"
                        )
        if isinstance(value, dict) and isinstance(value.get("materialPlan"), dict):
            reference = value.get("artifacts", {}).get("materialPlan")
            raw_path = reference.get("path") if isinstance(reference, dict) else None
            if isinstance(raw_path, str) and raw_path.strip():
                material_path = Path(raw_path)
                if not material_path.is_absolute():
                    material_path = resolved.parent / material_path
                try:
                    material_file = json.loads(
                        material_path.resolve().read_text(encoding="utf-8")
                    )
                except Exception as error:
                    errors.append(f"materialPlan artifact cannot be read: {error}")
                else:
                    if material_file != value["materialPlan"]:
                        errors.append(
                            "materialPlan artifact content does not match inline materialPlan"
                        )
    return {
        "errors": errors,
        "pass": not errors,
        "report": str(resolved),
        "schema": AUDIT_SCHEMA,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.report)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
