#!/usr/bin/env python3
"""Benchmark small, medium, and larger STL inputs on the CPU renderer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from cpu_z_buffer import (  # noqa: E402
    DEFAULT_MATERIAL,
    MeshInput,
    RenderLimits,
    load_mesh,
    render_contact_sheet,
    render_view,
)
import trimesh  # noqa: E402


def _models(large_subdivisions: int) -> dict[str, trimesh.Trimesh]:
    return {
        "small": trimesh.creation.box(extents=(4.0, 3.0, 2.0)),
        "medium": trimesh.creation.icosphere(subdivisions=3, radius=2.0),
        "large": trimesh.creation.icosphere(
            subdivisions=large_subdivisions, radius=2.0
        ),
    }


def _benchmark(path: Path, resolution: int, limits: RenderLimits) -> dict[str, object]:
    tracemalloc.reset_peak()
    loaded = load_mesh(path)
    inputs = [MeshInput(path.stem, loaded, DEFAULT_MATERIAL, path)]

    single_started = time.perf_counter()
    single = render_view(inputs, "isometric", resolution, limits=limits)
    single_seconds = time.perf_counter() - single_started

    contact = render_contact_sheet(
        inputs,
        resolution,
        title=path.stem,
        limits=limits,
    )
    _, traced_peak = tracemalloc.get_traced_memory()
    return {
        "contact_sheet_seconds": round(contact.elapsed_seconds, 6),
        "peak_memory_bytes": max(
            int(traced_peak), single.stats.buffer_bytes, contact.peak_buffer_bytes
        ),
        "single_view_seconds": round(single_seconds, 6),
        "triangle_count": len(loaded.faces),
        "view_count": len(contact.stats),
        "views": {stat.view: stat.to_dict() for stat in contact.stats},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=int, default=480)
    parser.add_argument("--large-subdivisions", type=int, default=5)
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.resolution < 320:
        parser.error("--resolution must be at least 320")
    if args.large_subdivisions < 4 or args.large_subdivisions > 6:
        parser.error("--large-subdivisions must be between 4 and 6")

    limits = RenderLimits()
    results: dict[str, object] = {
        "default_parallel_views": False,
        "default_processes": 1,
        "resolution": args.resolution,
        "renderer": "cpu-z-buffer/v1",
        "supersample": 1,
        "workloads": {},
    }
    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="amagine-cpu-render-") as temporary:
        temporary_root = Path(temporary)
        for name, mesh in _models(args.large_subdivisions).items():
            path = temporary_root / f"{name}.stl"
            mesh.export(path)
            results["workloads"][name] = _benchmark(path, args.resolution, limits)
    tracemalloc.stop()

    payload = json.dumps(results, indent=2)
    if args.out:
        destination = Path(args.out).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
