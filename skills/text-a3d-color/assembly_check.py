"""Cross-check region report against colors and object names stored in a 3MF."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from export_3mf import inspect_color_archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("three_mf")
    parser.add_argument("--max-overlap", type=float, default=0.01)
    parser.add_argument("--out")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    archive = inspect_color_archive(args.three_mf)
    expected = {
        name: item["color"].upper() for name, item in report.get("regions", {}).items()
    }
    observed = {
        item["name"]: (item["color"] or "").upper() for item in archive["objects"]
    }
    checks = [
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

    result = {
        "archive": archive,
        "checks": checks,
        "pass": all(check["pass"] for check in checks),
        "schema": "color-assembly-audit/v2",
    }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
