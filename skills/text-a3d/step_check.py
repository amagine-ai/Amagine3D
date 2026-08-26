"""Audit STEP assembly masters with the local OCCT CAD kernel."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from build123d import export_stl, import_step
import trimesh


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _valid(shape) -> bool:
    value = shape.is_valid
    return bool(value() if callable(value) else value)


def _bbox(shape) -> dict:
    box = shape.bounding_box()
    return {
        "min": [round(box.min.X, 5), round(box.min.Y, 5), round(box.min.Z, 5)],
        "max": [round(box.max.X, 5), round(box.max.Y, 5), round(box.max.Z, 5)],
        "size": [
            round(box.max.X - box.min.X, 5),
            round(box.max.Y - box.min.Y, 5),
            round(box.max.Z - box.min.Z, 5),
        ],
    }


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


def audit_step(
    step_path: Path,
    *,
    expect_solids: int | None = None,
    expect_x: float | None = None,
    expect_y: float | None = None,
    expect_z: float | None = None,
    tolerance: float = 0.5,
) -> dict:
    audit = Audit()
    shape = import_step(str(step_path))
    solid_count = len(shape.solids())
    valid = _valid(shape)
    bounds = _bbox(shape)
    dimensions = bounds["size"]
    audit.add("readable_step", True, str(step_path.resolve()))
    audit.add("valid_brep", valid, valid, True)
    audit.add("solid_count", solid_count > 0, solid_count, "> 0")
    if expect_solids is not None:
        audit.add("expected_solids", solid_count == expect_solids, solid_count, expect_solids)
    for index, (axis, expected) in enumerate(
        (("x", expect_x), ("y", expect_y), ("z", expect_z))
    ):
        if expected is None:
            continue
        audit.add(
            f"dimension_{axis}",
            abs(float(dimensions[index]) - expected) <= tolerance,
            dimensions[index],
            {"value": expected, "tolerance": tolerance},
        )
    with tempfile.TemporaryDirectory() as directory:
        mesh_path = Path(directory) / "step-meshability.stl"
        export_stl(shape, str(mesh_path), tolerance=0.05, angular_tolerance=0.2)
        mesh = trimesh.load(mesh_path, force="mesh", process=True)
        meshable = isinstance(mesh, trimesh.Trimesh) and not mesh.is_empty
        audit.add(
            "meshable_for_display",
            meshable,
            {
                "faces": int(len(mesh.faces)) if meshable else 0,
                "vertices": int(len(mesh.vertices)) if meshable else 0,
            },
            "non-empty STL preview mesh",
        )
    return {
        "bounds_mm": bounds,
        "checks": audit.checks,
        "errors": [item["name"] for item in audit.checks if item["status"] == "fail"],
        "pass": audit.passed,
        "schema": "evidence-step-audit/v1",
        "solid_count": solid_count,
        "step": {"path": str(step_path.resolve()), "sha256": _digest(step_path)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step")
    parser.add_argument("--expect-solids", type=int)
    parser.add_argument("--expect-x", type=float)
    parser.add_argument("--expect-y", type=float)
    parser.add_argument("--expect-z", type=float)
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        result = audit_step(
            Path(args.step),
            expect_solids=args.expect_solids,
            expect_x=args.expect_x,
            expect_y=args.expect_y,
            expect_z=args.expect_z,
            tolerance=args.tol,
        )
    except Exception as error:
        result = {
            "checks": [{
                "name": "readable_step",
                "observed": str(error),
                "pass": False,
                "status": "fail",
            }],
            "errors": ["readable_step"],
            "pass": False,
            "schema": "evidence-step-audit/v1",
            "step": {"path": str(Path(args.step).resolve())},
        }
    payload = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
