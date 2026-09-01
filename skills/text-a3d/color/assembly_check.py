"""Cross-check region report against colors and object names stored in a 3MF."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

if __package__:
    from .export_3mf import inspect_color_archive
else:
    from export_3mf import inspect_color_archive


COLOR_BUILD_SCHEMA = "evidence-color-build/v5"
UNIFIED_ASSEMBLY_SCHEMA = "evidence-cad-assembly-build/v3"


def _expected_colors(report: dict) -> dict[str, str]:
    if report.get("schema") == UNIFIED_ASSEMBLY_SCHEMA:
        values = report.get("part_colors", {})
        return {
            name: color.upper()
            for name, color in values.items()
            if isinstance(name, str) and isinstance(color, str)
        } if isinstance(values, dict) else {}
    values = report.get("regions", {})
    return {
        name: item["color"].upper()
        for name, item in values.items()
        if isinstance(name, str)
        and isinstance(item, dict)
        and isinstance(item.get("color"), str)
    } if isinstance(values, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("three_mf")
    parser.add_argument("--max-overlap", type=float, default=0.01)
    parser.add_argument("--out")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    archive = inspect_color_archive(args.three_mf)
    archive_hash = sha256(Path(args.three_mf).read_bytes()).hexdigest()
    expected = _expected_colors(report)
    region_inventory = archive.get("regions") or archive.get("objects", [])
    observed = {
        item["name"]: (item["color"] or "").upper() for item in region_inventory
    }
    package_mode = report.get(
        "print_package_mode",
        "separate_parts"
        if report.get("schema") == UNIFIED_ASSEMBLY_SCHEMA
        else "co_print_body",
    )
    build_items = archive.get("build_items", [])
    checks = [
        {
            "name": "build_report_schema",
            "pass": report.get("schema") in {
                COLOR_BUILD_SCHEMA,
                UNIFIED_ASSEMBLY_SCHEMA,
            },
            "expected": [COLOR_BUILD_SCHEMA, UNIFIED_ASSEMBLY_SCHEMA],
            "observed": report.get("schema"),
        },
        {
            "name": "archive_provenance",
            "pass": report.get("artifacts", {}).get("3mf", {}).get("sha256")
            == archive_hash,
            "expected": report.get("artifacts", {}).get("3mf", {}).get("sha256"),
            "observed": archive_hash,
        },
        {
            "name": "print_package_mode",
            "pass": archive.get("package_mode") == package_mode,
            "expected": package_mode,
            "observed": archive.get("package_mode"),
        },
        {
            "name": "print_package_co_print_build_item",
            "pass": package_mode != "co_print_body"
            or (
                archive.get("build_item_count") == 1
                and bool(build_items)
                and build_items[0].get("object_kind") == "mesh"
            ),
            "expected": (
                "single top-level mesh build item"
                if package_mode == "co_print_body"
                else "separate top-level part build items allowed"
            ),
            "observed": {
                "build_item_count": archive.get("build_item_count"),
                "top_level_kinds": [
                    item.get("object_kind")
                    for item in build_items
                ],
            },
        },
        {
            "name": "print_package_separate_part_build_items",
            "pass": package_mode != "separate_parts"
            or (
                archive.get("build_item_count") == len(expected)
                and bool(build_items)
                and all(
                    item.get("object_kind") == "mesh" for item in build_items
                )
            ),
            "expected": (
                {
                    "build_item_count": len(expected),
                    "top_level_kind": "mesh",
                }
                if package_mode == "separate_parts"
                else "co-print package"
            ),
            "observed": {
                "build_item_count": archive.get("build_item_count"),
                "top_level_kinds": [
                    item.get("object_kind") for item in build_items
                ],
            },
        },
        {
            "name": "region_names",
            "pass": set(expected) == set(observed),
            "expected": sorted(expected),
            "observed": sorted(observed),
        },
        {
            "name": "region_colors",
            "pass": expected == observed,
            "expected": expected,
            "observed": observed,
        },
    ]
    for pair, volume in report.get("overlaps_mm3", {}).items():
        checks.append({
            "name": f"overlap:{pair}",
            "pass": float(volume) <= args.max_overlap,
            "observed": volume,
            "expected": f"<= {args.max_overlap}",
        })
    coverage = report.get("parent_coverage")
    if coverage:
        checks.append({
            "name": "parent_coverage",
            "pass": float(coverage["error_mm3"]) <= args.max_overlap,
            "observed": coverage["error_mm3"],
            "expected": f"<= {args.max_overlap}",
        })

    material = report.get("material_semantics")
    checks.append({
        "name": "material_plan_present",
        "pass": isinstance(material, dict),
        "expected": "evidence-color-material-plan/v1",
        "observed": material.get("schema") if isinstance(material, dict) else None,
    })
    if isinstance(material, dict):
        checks.append({
            "name": "material_plan_schema",
            "pass": material.get("schema") == "evidence-color-material-plan/v1",
            "expected": "evidence-color-material-plan/v1",
            "observed": material.get("schema"),
        })
        material_regions = {
            item.get("name"): item for item in material.get("regions", [])
        }
        checks.append({
            "name": "material_region_names",
            "pass": set(material_regions) == set(expected),
            "expected": sorted(expected),
            "observed": sorted(material_regions),
        })
        checks.append({
            "name": "material_region_colors",
            "pass": all(
                str(material_regions[name].get("color", "")).upper() == color
                for name, color in expected.items()
                if name in material_regions
            ),
            "expected": expected,
            "observed": {
                name: str(item.get("color", "")).upper()
                for name, item in material_regions.items()
            },
        })

    result = {
        "archive": archive,
        "checks": checks,
        "pass": all(check["pass"] for check in checks),
        "requires_manual_slicer_assignment": bool(
            material and material.get("requires_manual_slicer_assignment")
        ),
        "schema": "color-assembly-audit/v4",
    }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
