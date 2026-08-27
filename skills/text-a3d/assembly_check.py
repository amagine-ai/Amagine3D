"""Audit a single-material multipart assembly report."""

from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
from itertools import combinations
import json
import math
from pathlib import Path
import sys


def _load_local_module(module_name: str, filename: str):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_intent_contract = _load_local_module(
    "_text_a3d_intent_contract_for_assembly_check",
    "intent_contract.py",
)
INTENT_SCHEMA = _intent_contract.INTENT_SCHEMA
validate_manufacturing = _intent_contract.validate_manufacturing


BUILD_SCHEMA = "evidence-cad-assembly-build/v3"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _artifact_path(report_dir: Path, reference: dict) -> Path:
    raw = reference.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("artifact reference is missing a path")
    path = Path(raw)
    return path if path.is_absolute() else report_dir / path


def _finite_non_negative(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _interface_feature_ids(manufacturing) -> set[str]:
    if not isinstance(manufacturing, dict):
        return set()
    features: set[str] = set()
    for interface in manufacturing.get("interfaces", []):
        if not isinstance(interface, dict):
            continue
        for feature_id in interface.get("features", []):
            if isinstance(feature_id, str):
                features.add(feature_id)
    return features


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, passed: bool, observed, expected=None) -> None:
        self.checks.append({
            "name": name,
            "observed": observed,
            "pass": bool(passed),
            "status": "pass" if passed else "fail",
            **({"expected": expected} if expected is not None else {}),
        })

    @property
    def passed(self) -> bool:
        return all(item["pass"] for item in self.checks)


def _check_reference(
    audit: Audit,
    report_dir: Path,
    name: str,
    reference,
) -> Path | None:
    if not isinstance(reference, dict):
        audit.add(name, False, "missing", "file with matching SHA-256")
        return None
    try:
        path = _artifact_path(report_dir, reference)
        exists = path.is_file()
        actual_hash = _digest(path) if exists else None
        expected_hash = reference.get("sha256")
        matches = (
            exists
            and isinstance(expected_hash, str)
            and expected_hash == actual_hash
        )
        audit.add(
            name,
            matches,
            {
                "exists": exists,
                "path": str(path),
                "sha256": expected_hash,
            },
            "existing file whose SHA-256 matches the report",
        )
        return path if matches else None
    except Exception as error:
        audit.add(name, False, str(error), "hashable artifact")
        return None


def audit_report(
    report_path: Path,
    *,
    print_stl: Path,
    max_overlap_mm3: float | None = None,
) -> dict:
    report = _load_json(report_path)
    report_dir = report_path.resolve().parent
    audit = Audit()

    audit.add(
        "schema",
        report.get("schema") == BUILD_SCHEMA,
        report.get("schema"),
        BUILD_SCHEMA,
    )
    parts = report.get("parts")
    part_map = parts if isinstance(parts, dict) else {}
    part_names = sorted(part_map)
    audit.add("part_count", len(part_names) >= 2, len(part_names), ">= 2")
    for part_name in part_names:
        record = part_map[part_name]
        valid = isinstance(record, dict) and record.get("valid") is True
        solid_count = record.get("solid_count") if isinstance(record, dict) else None
        audit.add(
            f"part:{part_name}:valid_single_solid",
            valid and solid_count == 1,
            {"solid_count": solid_count, "valid": valid},
            {"solid_count": 1, "valid": True},
        )

    manufacturing = report.get("manufacturing")
    manufacturing_errors = validate_manufacturing(manufacturing)
    declared_names: list[str] = []
    if isinstance(manufacturing, dict):
        declared_names = sorted(
            item.get("name")
            for item in manufacturing.get("parts", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
    audit.add(
        "manufacturing_contract",
        isinstance(manufacturing, dict)
        and manufacturing.get("mode") == "multipart"
        and not manufacturing_errors
        and declared_names == part_names,
        {
            "mode": (
                manufacturing.get("mode") if isinstance(manufacturing, dict) else None
            ),
            "parts": declared_names,
            "validation_errors": manufacturing_errors,
        },
        {"mode": "multipart", "parts": part_names},
    )

    _check_reference(audit, report_dir, "source_binding", report.get("source"))
    intent_path = _check_reference(
        audit,
        report_dir,
        "intent_binding",
        report.get("intent"),
    )
    if intent_path is not None:
        try:
            intent = _load_json(intent_path)
            intent_matches = (
                intent.get("schema") == INTENT_SCHEMA
                and intent.get("part") == report.get("part")
                and intent.get("manufacturing") == manufacturing
            )
            audit.add(
                "intent_contract",
                intent_matches,
                {
                    "part": intent.get("part"),
                    "schema": intent.get("schema"),
                },
                {"part": report.get("part"), "schema": INTENT_SCHEMA},
            )
        except Exception as error:
            audit.add("intent_contract", False, str(error), "matching intent contract")
    else:
        audit.add("intent_contract", False, "unavailable", "matching intent contract")

    artifacts = report.get("artifacts")
    artifact_map = artifacts if isinstance(artifacts, dict) else {}
    expected_keys = {f"stl:{part_name}" for part_name in part_names} | {
        "glb:display",
        "stl",
        "step:assemble",
    }
    audit.add(
        "artifact_keys",
        expected_keys.issubset(set(artifact_map)),
        sorted(artifact_map),
        sorted(expected_keys),
    )
    for key in sorted(expected_keys):
        _check_reference(
            audit,
            report_dir,
            f"artifact:{key}",
            artifact_map.get(key),
        )

    assembly = report.get("assembly")
    assembly_shape = assembly.get("shape") if isinstance(assembly, dict) else None
    assembly_solids = (
        assembly_shape.get("solid_count") if isinstance(assembly_shape, dict) else None
    )
    audit.add(
        "assembly_solid_count",
        assembly_solids == len(part_names),
        assembly_solids,
        len(part_names),
    )
    print_plate = report.get("print_plate")
    print_solids = (
        print_plate.get("solid_count") if isinstance(print_plate, dict) else None
    )
    audit.add(
        "print_plate_solid_count",
        print_solids == len(part_names),
        print_solids,
        len(part_names),
    )
    print_ref = artifact_map.get("stl")
    expected_hash = (
        print_ref.get("sha256") if isinstance(print_ref, dict) else None
    )
    supplied_exists = print_stl.is_file()
    supplied_hash = _digest(print_stl) if supplied_exists else None
    audit.add(
        "print_stl_binding",
        supplied_exists and expected_hash == supplied_hash,
        {"path": str(print_stl), "sha256": supplied_hash},
        "supplied print STL matches report artifact",
    )

    assembly_policy = report.get("assembly")
    recorded_limit = (
        assembly_policy.get("max_overlap_mm3")
        if isinstance(assembly_policy, dict)
        else None
    )
    policy_valid = _finite_non_negative(recorded_limit)
    override_valid = max_overlap_mm3 is None or _finite_non_negative(max_overlap_mm3)
    effective_limit = (
        min(float(recorded_limit), float(max_overlap_mm3))
        if policy_valid and max_overlap_mm3 is not None and override_valid
        else float(recorded_limit) if policy_valid else None
    )
    audit.add(
        "overlap_policy",
        policy_valid and override_valid,
        {
            "audit_max_overlap_mm3": max_overlap_mm3,
            "report_max_overlap_mm3": recorded_limit,
        },
        "finite non-negative threshold; audit override may only tighten it",
    )

    overlaps = report.get("overlaps_mm3")
    expected_pairs = {
        f"{left}&{right}" for left, right in combinations(part_names, 2)
    }
    overlap_map = overlaps if isinstance(overlaps, dict) else {}
    keys_match = set(overlap_map) == expected_pairs
    values_valid = all(_finite_non_negative(value) for value in overlap_map.values())
    offenders = (
        {
            key: value
            for key, value in overlap_map.items()
            if value > effective_limit
        }
        if values_valid and effective_limit is not None
        else {}
    )
    audit.add(
        "part_overlaps",
        isinstance(overlaps, dict)
        and keys_match
        and values_valid
        and effective_limit is not None
        and not offenders,
        {
            "keys": sorted(overlap_map),
            "max_overlap_mm3": (
                max(overlap_map.values(), default=0) if values_valid else None
            ),
            "offenders": offenders,
        },
        {
            "keys": sorted(expected_pairs),
            "max_overlap_mm3": effective_limit,
        },
    )

    features = report.get("features")
    feature_map = features if isinstance(features, dict) else {}
    feature_owners = {
        record.get("part")
        for record in feature_map.values()
        if isinstance(record, dict)
    }
    features_valid = (
        bool(feature_map)
        and feature_owners == set(part_names)
        and all(
            isinstance(record, dict) and record.get("part") in part_map
            for record in feature_map.values()
        )
    )
    events = report.get("events")
    event_list = events if isinstance(events, list) else []
    events_valid = isinstance(events, list) and all(
        isinstance(event, dict) and event.get("part") in part_map
        for event in event_list
    )
    interface_features = _interface_feature_ids(manufacturing)
    observed_feature_ids = set(feature_map) | {
        event.get("id") for event in event_list if isinstance(event.get("id"), str)
    }
    missing_interface_features = sorted(interface_features - observed_feature_ids)
    audit.add(
        "interface_feature_evidence",
        bool(interface_features) and not missing_interface_features,
        {
            "missing_feature_ids": missing_interface_features,
            "observed_feature_ids": sorted(observed_feature_ids),
            "required_feature_ids": sorted(interface_features),
        },
        "each assembly interface names modeled connector features",
    )
    audit.add(
        "part_evidence_ownership",
        features_valid and events_valid,
        {
            "event_count": len(event_list),
            "feature_count": len(feature_map),
            "feature_owners": sorted(
                item for item in feature_owners if isinstance(item, str)
            ),
        },
        {"feature_owners": part_names},
    )

    return {
        "checks": audit.checks,
        "errors": [item["name"] for item in audit.checks if item["status"] == "fail"],
        "pass": audit.passed,
        "report": str(report_path.resolve()),
        "schema": "cad-assembly-audit/v2",
        "status": "pass" if audit.passed else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("print_stl")
    parser.add_argument("--max-overlap", type=float)
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        result = audit_report(
            Path(args.report),
            print_stl=Path(args.print_stl),
            max_overlap_mm3=args.max_overlap,
        )
    except Exception as error:
        result = {"error": str(error), "pass": False}
        print(json.dumps(result, indent=2))
        return 2
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
