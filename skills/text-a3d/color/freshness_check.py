"""Mark a generation run and verify that its artifacts were rewritten."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a generation marker or verify artifact freshness"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mark", help="Create or replace this run marker")
    mode.add_argument("--after", help="Require every artifact to be newer than this marker")
    parser.add_argument("artifacts", nargs="*", help="Artifacts checked with --after")
    args = parser.parse_args()

    if args.mark:
        if args.artifacts:
            parser.error("--mark does not accept artifact paths")
        marker = Path(args.mark)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("generation started\n", encoding="utf-8")
        print(json.dumps({"marker": str(marker), "status": "marked"}))
        return 0

    if not args.artifacts:
        parser.error("--after requires at least one artifact path")

    marker = Path(args.after)
    if not marker.is_file():
        print(json.dumps({"error": "marker_missing", "marker": str(marker)}))
        return 1

    marker_mtime_ns = marker.stat().st_mtime_ns
    checks = []
    passed = True
    for raw_path in args.artifacts:
        path = Path(raw_path)
        exists = path.is_file()
        mtime_ns = path.stat().st_mtime_ns if exists else None
        fresh = exists and mtime_ns is not None and mtime_ns >= marker_mtime_ns
        checks.append({
            "path": str(path),
            "exists": exists,
            "fresh": fresh,
            "mtime_ns": mtime_ns,
        })
        passed = passed and fresh

    print(json.dumps({
        "pass": passed,
        "marker": str(marker),
        "marker_mtime_ns": marker_mtime_ns,
        "artifacts": checks,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
