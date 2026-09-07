"""Audit the unified material plan against a verified colored 3MF package."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

SKILL_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SKILL_DIR.parent
for directory in (SKILL_DIR, SKILL_ROOT):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from build_check import audit as audit_build
from build_manifest import BUILD_SCHEMA
from material_plan import MATERIAL_PLAN_SCHEMA, validate_material_plan
from export_3mf import inspect_color_archive

SUPPORTED_BACKENDS = {
    "brep-part",
    "brep-assembly",
    "brep-color-regions",
    "hybrid-mesh",
}


def _expected_colors(report: dict) -> dict[str, str]:
    plan = report["materialPlan"]
    materials = {item["id"]: item["color"] for item in plan["materials"]}
    values: dict[str, str] = {}
    for assignment in plan["assignments"]:
        if assignment["scope"] == "volumetric-region":
            name = f"{assignment['part']}/{assignment['region']}"
        else:
            name = assignment["region"] or assignment["part"]
        values[name] = materials[assignment["materialId"]]
    return values


def _check(name: str, passed: bool, observed, expected=None) -> dict:
    return {
        "name": name,
        "pass": bool(passed),
        "observed": observed,
        **({"expected": expected} if expected is not None else {}),
    }


def audit(report_path: Path, three_mf_path: Path, max_overlap: float) -> dict:
    report_path = report_path.resolve()
    three_mf_path = three_mf_path.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("build report must contain an object")
    archive = inspect_color_archive(str(three_mf_path))
    archive_hash = sha256(three_mf_path.read_bytes()).hexdigest()
    manifest_errors = audit_build(report_path)["errors"]
    plan = report.get("materialPlan")
    plan_errors = validate_material_plan(plan)
    expected = _expected_colors(report) if not plan_errors else {}
    inventory = archive.get("regions")
    if not isinstance(inventory, list) or not inventory:
        raise ValueError("3MF archive must expose non-empty volumetric regions")
    observed = {
        item["name"]: str(item.get("color") or "").upper()
        for item in inventory
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    backend = report.get("backend")
    package_mode = plan.get("packageMode") if isinstance(plan, dict) else None
    assignments = plan.get("assignments", []) if isinstance(plan, dict) else []
    expected_part_count = len({
        item.get("part") for item in assignments if isinstance(item, dict)
    })
    build_items = archive.get("build_items", [])
    artifact = report.get("artifacts", {}).get("3mf", {})

    checks = [
        _check(
            "build_report",
            report.get("schema") == BUILD_SCHEMA
            and backend in SUPPORTED_BACKENDS
            and report.get("pass") is True,
            {"backend": backend, "pass": report.get("pass"), "schema": report.get("schema")},
            {"backends": sorted(SUPPORTED_BACKENDS), "schema": BUILD_SCHEMA},
        ),
        _check("build_manifest", not manifest_errors, manifest_errors, []),
        _check("material_plan", not plan_errors, plan_errors, {"schema": MATERIAL_PLAN_SCHEMA}),
        _check(
            "archive_provenance",
            artifact.get("sha256") == archive_hash
            and artifact.get("verified") is True
            and artifact.get("validator") == "lib3mf"
            and artifact.get("coordinateFrame") == "plate-print",
            {
                "coordinateFrame": artifact.get("coordinateFrame"),
                "sha256": archive_hash,
                "validator": artifact.get("validator"),
                "verified": artifact.get("verified"),
            },
            "hash-bound plate-print 3MF independently verified by lib3mf",
        ),
        _check("print_package_mode", archive.get("package_mode") == package_mode, archive.get("package_mode"), package_mode),
        _check(
            "print_package_build_items",
            (
                package_mode == "co_print_body"
                and archive.get("build_item_count") == 1
                and len(build_items) == 1
                and build_items[0].get("object_kind") == "components"
                and archive.get("component_object_count") == 1
            )
            or (package_mode == "separate_parts" and archive.get("build_item_count") == expected_part_count)
            and all(
                item.get("object_kind") in {"components", "mesh"}
                for item in build_items
            ),
            {
                "build_item_count": archive.get("build_item_count"),
                "top_level_kinds": [item.get("object_kind") for item in build_items],
            },
            {
                "build_item_count": 1 if package_mode == "co_print_body" else expected_part_count,
                "top_level_kinds": ["components"],
            },
        ),
        _check("region_names", set(observed) == set(expected), sorted(observed), sorted(expected)),
        _check("region_colors", observed == expected, observed, expected),
    ]

    backend_data = report.get("backendData", {})
    overlaps = backend_data.get("overlapsMm3")
    if backend == "hybrid-mesh":
        overlaps = backend_data.get("assembly", {}).get("overlapsMm3")
    if isinstance(overlaps, dict):
        for pair, volume in overlaps.items():
            checks.append(_check(
                f"overlap:{pair}",
                isinstance(volume, (int, float)) and not isinstance(volume, bool) and float(volume) <= max_overlap,
                volume,
                f"<= {max_overlap}",
            ))
    coverage = backend_data.get("parentCoverage")
    if isinstance(coverage, dict):
        error = coverage.get("error_mm3")
        checks.append(_check(
            "parent_coverage",
            isinstance(error, (int, float)) and float(error) <= max_overlap,
            error,
            f"<= {max_overlap}",
        ))

    return {
        "archive": archive,
        "checks": checks,
        "pass": all(check["pass"] for check in checks),
        "schema": "evidence-assembly-audit/v1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("three_mf")
    parser.add_argument("--max-overlap", type=float, default=0.01)
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        result = audit(Path(args.report), Path(args.three_mf), args.max_overlap)
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
    raise SystemExit(main())
