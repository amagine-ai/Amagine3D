"""Mark a generation run and verify that its artifacts were rewritten."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import stat
import sys
from pathlib import Path


def _missing_snapshot() -> dict[str, object]:
    return {
        "exists": False,
        "mtime_ns": None,
        "sha256": None,
        "size": None,
        "stable": False,
    }


def _same_file_state(
    left: os.stat_result,
    right: os.stat_result,
    *,
    compare_change_time: bool = True,
) -> bool:
    same_change_time = (
        left.st_ctime_ns == right.st_ctime_ns
        if compare_change_time
        else True
    )
    return (
        stat.S_ISREG(right.st_mode)
        and right.st_nlink == 1
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and same_change_time
    )


def stable_file_snapshot(path: Path) -> dict[str, object]:
    """Hash one regular file through one descriptor and bind it to its path."""

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            return _missing_snapshot()
        digest = sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
    except OSError:
        return _missing_snapshot()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    stable = _same_file_state(before, after) and _same_file_state(
        before,
        current,
        # CPython 3.12 deprecated Windows st_ctime as a creation-time alias.
        # Keep ctime protection between the two descriptor snapshots, but do
        # not compare that value across Windows fstat/lstat implementations.
        compare_change_time=os.name != "nt",
    )
    return {
        "exists": True,
        "mtime_ns": after.st_mtime_ns,
        "sha256": digest.hexdigest() if stable else None,
        "size": after.st_size,
        "stable": stable,
    }


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

    marker_snapshot = stable_file_snapshot(marker)
    if marker_snapshot["stable"] is not True:
        print(json.dumps({"error": "marker_unstable", "marker": str(marker)}))
        return 1
    marker_mtime_ns = marker_snapshot["mtime_ns"]
    checks = []
    passed = True
    for raw_path in args.artifacts:
        path = Path(raw_path)
        snapshot = stable_file_snapshot(path)
        mtime_ns = snapshot["mtime_ns"]
        fresh = (
            snapshot["exists"] is True
            and snapshot["stable"] is True
            and isinstance(mtime_ns, int)
            and isinstance(marker_mtime_ns, int)
            and mtime_ns >= marker_mtime_ns
        )
        checks.append({"path": str(path), "fresh": fresh, **snapshot})
        passed = passed and fresh

    print(json.dumps({
        "pass": passed,
        "marker": str(marker),
        "marker_mtime_ns": marker_mtime_ns,
        "marker_sha256": marker_snapshot["sha256"],
        "marker_size": marker_snapshot["size"],
        "artifacts": checks,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
