"""Mesh audit tuned for one named color region of a printable assembly."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import trimesh


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stl")
    parser.add_argument("--region", default="unnamed")
    parser.add_argument("--expect-x", type=float)
    parser.add_argument("--expect-y", type=float)
    parser.add_argument("--expect-z", type=float)
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--components", type=int, default=1)
    parser.add_argument("--expect-volume", type=float)
    parser.add_argument("--vol-tol-pct", type=float, default=10.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    try:
        mesh = trimesh.load(args.stl, force="mesh", process=True)
    except Exception as error:
        print(json.dumps({"pass": False, "error": str(error)}))
        return 2

    checks: list[dict] = []

    def add(name: str, passed: bool, observed, expected=None):
        checks.append({
            "name": name,
            "pass": bool(passed),
            "observed": observed,
            **({"expected": expected} if expected is not None else {}),
        })

    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    add("finite_vertices", bool(np.isfinite(vertices).all()), len(vertices))
    add("watertight", mesh.is_watertight, mesh.is_watertight, True)
    add("consistent_winding", mesh.is_winding_consistent, mesh.is_winding_consistent, True)
    components = len(mesh.split(only_watertight=False)) or 1
    add("component_count", components == args.components, components, args.components)

    areas = np.asarray(mesh.area_faces)
    degenerate = int(np.count_nonzero(~np.isfinite(areas) | (areas <= 1e-10)))
    add("no_degenerate_faces", degenerate == 0, degenerate, 0)
    volume = float(mesh.volume) if mesh.is_watertight else None
    add("positive_volume", volume is not None and volume > 1e-6, volume, "> 0")

    bounds = np.asarray(mesh.bounds, dtype=float)
    dimensions = bounds[1] - bounds[0]
    for index, axis in enumerate("xyz"):
        expected = getattr(args, f"expect_{axis}")
        if expected is not None:
            add(
                f"dimension_{axis}",
                abs(float(dimensions[index]) - expected) <= args.tol,
                round(float(dimensions[index]), 5),
                {"value": expected, "tolerance": args.tol},
            )
    if args.expect_volume is not None and volume is not None:
        delta = abs(volume - args.expect_volume) / max(args.expect_volume, 1e-9) * 100
        add(
            "volume_target", delta <= args.vol_tol_pct,
            round(delta, 5), {"max_delta_percent": args.vol_tol_pct},
        )

    result = {
        "checks": checks,
        "mesh": {
            "bounds_mm": bounds.round(5).tolist(),
            "dimensions_mm": dimensions.round(5).tolist(),
            "faces": len(faces),
            "surface_area_mm2": round(float(mesh.area), 5),
            "vertices": len(vertices),
            "volume_mm3": round(volume, 5) if volume is not None else None,
        },
        "pass": all(item["pass"] for item in checks),
        "region": args.region,
        "schema": "evidence-color-region-audit/v2",
        "stl": str(Path(args.stl).resolve()),
    }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
