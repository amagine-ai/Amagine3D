"""Evidence-oriented mesh audit for a single exported printable solid."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import trimesh


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, passed: bool, observed, expected=None) -> None:
        self.checks.append({
            "name": name,
            "pass": bool(passed),
            "observed": observed,
            **({"expected": expected} if expected is not None else {}),
        })

    @property
    def passed(self) -> bool:
        return all(item["pass"] for item in self.checks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stl")
    parser.add_argument("--expect-x", type=float)
    parser.add_argument("--expect-y", type=float)
    parser.add_argument("--expect-z", type=float)
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--components", type=int, default=1)
    parser.add_argument("--expect-volume", type=float)
    parser.add_argument("--vol-tol-pct", type=float, default=10.0)
    parser.add_argument("--require-z0", action="store_true")
    parser.add_argument("--max-degenerate-ratio", type=float, default=0.001)
    parser.add_argument("--out")
    args = parser.parse_args()

    audit = Audit()
    try:
        mesh = trimesh.load(args.stl, force="mesh", process=True)
    except Exception as error:
        print(json.dumps({"pass": False, "error": str(error)}))
        return 2

    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    audit.add("finite_vertices", bool(np.isfinite(vertices).all()), len(vertices))
    audit.add("has_triangles", len(faces) >= 4, len(faces), ">= 4")
    audit.add("watertight", mesh.is_watertight, mesh.is_watertight, True)
    audit.add(
        "consistent_winding", mesh.is_winding_consistent,
        mesh.is_winding_consistent, True,
    )

    areas = np.asarray(mesh.area_faces)
    degenerate = int(np.count_nonzero(~np.isfinite(areas) | (areas <= 1e-10)))
    ratio = degenerate / max(len(areas), 1)
    audit.add(
        "degenerate_faces",
        ratio <= args.max_degenerate_ratio,
        {"count": degenerate, "ratio": round(ratio, 8)},
        {"max_ratio": args.max_degenerate_ratio},
    )

    components = len(mesh.split(only_watertight=False)) or 1
    audit.add("connected_components", components == args.components, components, args.components)
    volume = float(mesh.volume) if mesh.is_watertight else None
    audit.add("positive_volume", volume is not None and volume > 1e-6, volume, "> 0")

    bounds = np.asarray(mesh.bounds, dtype=float)
    dims = bounds[1] - bounds[0]
    for index, axis in enumerate("xyz"):
        expected = getattr(args, f"expect_{axis}")
        if expected is not None:
            audit.add(
                f"dimension_{axis}",
                abs(float(dims[index]) - expected) <= args.tol,
                round(float(dims[index]), 5),
                {"value": expected, "tolerance": args.tol},
            )
    if args.require_z0:
        audit.add(
            "build_plane_z0",
            abs(float(bounds[0, 2])) <= args.tol,
            round(float(bounds[0, 2]), 5),
            {"value": 0.0, "tolerance": args.tol},
        )
    if args.expect_volume is not None and volume is not None:
        delta = abs(volume - args.expect_volume) / max(args.expect_volume, 1e-9) * 100
        audit.add(
            "volume_target",
            delta <= args.vol_tol_pct,
            {"value": round(volume, 5), "delta_percent": round(delta, 5)},
            {"value": args.expect_volume, "tolerance_percent": args.vol_tol_pct},
        )

    result = {
        "checks": audit.checks,
        "mesh": {
            "bounds_mm": bounds.round(5).tolist(),
            "dimensions_mm": dims.round(5).tolist(),
            "faces": len(faces),
            "surface_area_mm2": round(float(mesh.area), 5),
            "vertices": len(vertices),
            "volume_mm3": round(volume, 5) if volume is not None else None,
        },
        "pass": audit.passed,
        "schema": "evidence-mesh-audit/v2",
        "stl": str(Path(args.stl).resolve()),
    }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if audit.passed else 1


if __name__ == "__main__":
    sys.exit(main())
