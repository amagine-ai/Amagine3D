"""Audit STEP assembly masters with the local OCCT CAD kernel."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import tempfile

from build123d import export_stl, import_step
import trimesh

from build_manifest import (
    SEMANTIC_ARTIFACT_TOLERANCE_MM,
    SEMANTIC_ENVELOPE_TOLERANCE_MM,
)


BUILD_SCHEMA = "evidence-a3d-build/v1"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load_json(path: str | None) -> dict | None:
    if path is None:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _intent_dimensions(intent: dict | None) -> tuple[float, float, float] | None:
    if not isinstance(intent, dict):
        return None
    dimensions = intent.get("dimensions_mm")
    if not isinstance(dimensions, dict):
        return None
    values = []
    for axis in "xyz":
        record = dimensions.get(axis)
        if not isinstance(record, dict):
            return None
        value = record.get("value")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            return None
        values.append(float(value))
    return tuple(values)


def _nested_int(data: dict, keys: tuple[str, ...]) -> int | None:
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _artifact_key(report: dict, step_path: Path) -> str | None:
    if not step_path.is_file():
        return None
    digest = _digest(step_path)
    matches = [
        key
        for key, record in report.get("artifacts", {}).items()
        if key.startswith("step:")
        and isinstance(record, dict)
        and record.get("sha256") == digest
    ]
    return matches[0] if len(matches) == 1 else None


def _report_dimensions(
    report: dict | None,
    artifact_key: str | None,
) -> tuple[float, float, float] | None:
    if not isinstance(report, dict) or artifact_key is None:
        return None
    if artifact_key == "step:assembly":
        record = report.get("backendData", {}).get("semanticAssembly", {})
    else:
        part_name = artifact_key.removeprefix("step:")
        record = report.get("parts", {}).get(part_name, {}).get("semantic", {})
    value = record.get("boundsMm", {}).get("size") if isinstance(record, dict) else None
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        dimensions = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return dimensions if all(math.isfinite(item) and item > 0 for item in dimensions) else None


def _report_expected_solids(
    report: dict | None,
    artifact_key: str | None,
) -> int | None:
    if not isinstance(report, dict):
        return None
    backend = report.get("backend")
    if artifact_key == "step:assembly" and backend == "brep-assembly":
        return len(report.get("parts", {}))
    if artifact_key and artifact_key.startswith("step:"):
        if backend == "brep-color-regions":
            value = _nested_int(
                report,
                ("backendData", "assembly", "shape", "solid_count"),
            )
            if value is not None:
                return value
            regions = report.get("backendData", {}).get("regions")
            return len(regions) if isinstance(regions, dict) and regions else None
        return 1
    return None


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
    parser.add_argument("--intent")
    parser.add_argument("--report")
    parser.add_argument("--expect-solids", type=int)
    parser.add_argument("--expect-x", type=float)
    parser.add_argument("--expect-y", type=float)
    parser.add_argument("--expect-z", type=float)
    parser.add_argument("--tol", type=float)
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        intent = _load_json(args.intent)
        report = _load_json(args.report)
        if report is not None and report.get("schema") != BUILD_SCHEMA:
            raise ValueError(f"build report must use {BUILD_SCHEMA}")
        step_path = Path(args.step)
        artifact_key = _artifact_key(report, step_path) if report is not None else None
        if report is not None and artifact_key is None:
            raise ValueError("STEP is not bound to exactly one build-report artifact")
        dimensions = _report_dimensions(report, artifact_key) or _intent_dimensions(intent)
        tolerance = args.tol
        if tolerance is None:
            tolerance = (
                SEMANTIC_ARTIFACT_TOLERANCE_MM
                if report is not None
                else SEMANTIC_ENVELOPE_TOLERANCE_MM
            )
        expect_solids = args.expect_solids
        if expect_solids is None:
            expect_solids = _report_expected_solids(report, artifact_key)
        result = audit_step(
            step_path,
            expect_solids=expect_solids,
            expect_x=args.expect_x if args.expect_x is not None else (
                dimensions[0] if dimensions is not None else None
            ),
            expect_y=args.expect_y if args.expect_y is not None else (
                dimensions[1] if dimensions is not None else None
            ),
            expect_z=args.expect_z if args.expect_z is not None else (
                dimensions[2] if dimensions is not None else None
            ),
            tolerance=tolerance,
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
