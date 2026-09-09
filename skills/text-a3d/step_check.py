"""Audit STEP assembly masters with the local OCCT CAD kernel."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import tempfile

from build123d import Plane, export_stl, import_step
import trimesh

from build_manifest import (
    SEMANTIC_ARTIFACT_TOLERANCE_MM,
    BREP_ENVELOPE_TOLERANCE_MM,
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


def _bound_section_features(intent: dict | None, intent_path: str | None,
                            report: dict | None, artifact_key: str | None) -> list[dict]:
    """Select requirements on this hash-bound semantic part; never its print pose."""
    from intent_contract import feature_owner_map, physical_part_names, validate_section_dimensions

    features = intent.get("features", []) if isinstance(intent, dict) else []
    requested = [feature for feature in features if isinstance(feature, dict) and "section_dimensions" in feature]
    if not requested:
        return []
    errors = [error for index, feature in enumerate(features) if isinstance(feature, dict)
              for error in validate_section_dimensions(feature, index)]
    if errors:
        raise ValueError("; ".join(errors))
    if not isinstance(report, dict) or not intent_path:
        raise ValueError("section dimensions require the hash-bound build report and intent")
    if report.get("inputs", {}).get("intent", {}).get("sha256") != _digest(Path(intent_path)):
        raise ValueError("section dimension intent does not match the build-report hash")
    owners = feature_owner_map(intent)
    parts = physical_part_names(intent)
    for feature in requested:
        owner = owners.get(feature.get("id"))
        if owner not in parts:
            raise ValueError("section dimension feature must have a declared physical owner")
        if owner == "assembly" and report.get("backend") == "brep-assembly":
            raise ValueError("section dimension owner assembly collides with the aggregate STEP artifact")
        artifact = report.get("artifacts", {}).get(f"step:{owner}", {})
        if artifact.get("coordinateFrame") != "semantic":
            raise ValueError(f"section dimensions for {owner} require its semantic STEP artifact")
    owner = artifact_key.removeprefix("step:") if artifact_key else None
    # A physical part may itself be named assembly; its own requirements still apply.
    if owner in parts:
        return [dict(feature, part=owner) for feature in requested if owners.get(feature["id"]) == owner]
    # Public compile separately audits every owner STEP; a union can hide a wrong part.
    if artifact_key == "step:assembly" and report.get("backend") == "brep-assembly":
        return []
    raise ValueError("section dimension STEP artifact must identify a declared physical part")


def _audit_section_dimensions(audit, shape, features: list[dict]) -> None:
    from brep_measurements import measure_section
    from intent_contract import dimension_limits

    for feature in features:
        for index, requirement in enumerate(feature["section_dimensions"]):
            axis, coordinate = requirement["plane"]["axis"], requirement["plane"]["coordinate_mm"]
            normal = {"x": (1, 0, 0), "y": (0, -1, 0), "z": (0, 0, 1)}[axis]
            plane = Plane(origin=tuple(coordinate if a == axis else 0 for a in "xyz"),
                          x_dir=(0, 1, 0) if axis == "x" else (1, 0, 0), z_dir=normal)
            name = f"section:{feature['id']}:{index}"
            try:
                measured = measure_section(shape, plane, label=name)
            except Exception as error:
                audit.add(name, False, {"part": feature["part"], "error": str(error)})
                continue
            for metric, item in requirement["outer_envelope"].items():
                target = {"dimensions_mm": {"x": item}}
                lower, upper = dimension_limits(target, "x")
                accepted_lower, accepted_upper = dimension_limits(target, "x", BREP_ENVELOPE_TOLERANCE_MM)
                envelope = measured["outer_envelope"]
                actual = envelope[metric] if envelope is not None else None
                audit.add(f"{name}:{metric}", actual is not None and accepted_lower <= actual <= accepted_upper,
                          {"part": feature["part"], "coordinate_frame": "semantic", "plane": measured["plane"],
                           "outer_envelope": envelope, "actual_mm": actual,
                           "delta_mm": actual - item["value"] if actual is not None else None,
                           "material_island_count": measured["material_island_count"], "hole_count": measured["hole_count"]},
                          {"value_mm": item["value"], "min_mm": lower, "max_mm": upper,
                           "measurement_precision_mm": BREP_ENVELOPE_TOLERANCE_MM})


def audit_step(
    step_path: Path,
    *,
    expect_solids: int | None = None,
    expect_x: float | None = None,
    expect_y: float | None = None,
    expect_z: float | None = None,
    tolerance: float = 0.5,
    section_features: list[dict] | None = None,
    expected_step_sha256: str | None = None,
) -> dict:
    audit = Audit()
    input_hash = _digest(step_path)
    if expected_step_sha256 is not None and input_hash != expected_step_sha256:
        raise ValueError("STEP changed after build-report artifact selection")
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
    _audit_section_dimensions(audit, shape, section_features or [])
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
    output_hash = _digest(step_path)
    if section_features:
        audit.add("section_step_unchanged", output_hash == input_hash, output_hash, input_hash)
    return {
        "bounds_mm": bounds,
        "checks": audit.checks,
        "errors": [item["name"] for item in audit.checks if item["status"] == "fail"],
        "pass": audit.passed,
        "schema": "evidence-step-audit/v1",
        "solid_count": solid_count,
        "step": {"path": str(step_path.resolve()), "sha256": output_hash},
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
        report = _load_json(args.report)
        if report is not None and report.get("schema") != BUILD_SCHEMA:
            raise ValueError(f"build report must use {BUILD_SCHEMA}")
        intent_path = args.intent
        inputs = report.get("inputs") if isinstance(report, dict) else None
        binding = inputs.get("intent") if isinstance(inputs, dict) else None
        if isinstance(binding, dict):
            if intent_path is None:
                bound_path = Path(binding["path"])
                if not bound_path.is_absolute():
                    bound_path = Path(args.report).resolve().parent / bound_path
                intent_path = str(bound_path)
            # Parse exactly the bytes whose hash was checked. A missing flag or
            # substitute legacy intent must not discard the bound requirements.
            raw_intent = Path(intent_path).read_bytes()
            if sha256(raw_intent).hexdigest() != binding.get("sha256"):
                raise ValueError("section dimension intent does not match the build-report hash")
            intent = json.loads(raw_intent)
            if not isinstance(intent, dict):
                raise ValueError("bound intent must contain a JSON object")
        else:
            intent = _load_json(intent_path)
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
        section_features = _bound_section_features(intent, intent_path, report, artifact_key)
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
            section_features=section_features,
            expected_step_sha256=report["artifacts"][artifact_key]["sha256"] if section_features else None,
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
