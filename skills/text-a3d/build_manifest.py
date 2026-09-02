"""Shared, backend-neutral build-manifest primitives for Amagine3D.

The compiler backends own geometry generation, but they all publish the same
top-level evidence contract.  This module deliberately contains only portable
JSON, hashing, and rigid-transform helpers so BRep, mesh, and color adapters can
share it without importing a geometry kernel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import uuid4

from material_plan import validate_material_plan, validate_material_sources


BUILD_SCHEMA = "evidence-a3d-build/v1"
BUILD_BACKENDS = {
    "brep-assembly",
    "brep-color-regions",
    "brep-part",
    "hybrid-mesh",
}
ARTIFACT_REQUIREMENTS = {"not-applicable", "required"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
EXPORT_AUDIT_SCHEMA = "evidence-export-audit/v1"
SEMANTIC_ENVELOPE_TOLERANCE_MM = 0.5
SEMANTIC_ARTIFACT_TOLERANCE_MM = 0.05
SEMANTIC_RECORD_TOLERANCE_MM = 0.0002


def digest_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def digest_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return digest_bytes(payload)


def artifact_record(path: Path, **fields: Any) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": digest_file(resolved),
        **fields,
    }


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"could not read {label}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def bind_inputs(
    *,
    intent_path: str,
    scene_path: str,
    expected_parts: set[str],
    source_path: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load and validate immutable intent/profile plus the mutable scene input."""

    intent_file = Path(intent_path).resolve()
    scene_file = Path(scene_path).resolve()
    if not intent_file.is_file():
        raise ValueError(f"intent contract not found: {intent_file}")
    if not scene_file.is_file():
        raise ValueError(f"semantic scene not found: {scene_file}")
    intent = _json_object(intent_file, "intent contract")
    from intent_contract import (
        physical_part_names as intent_physical_part_names,
        validate as validate_intent,
    )

    intent_errors = validate_intent(intent, intent_file.parent)
    if intent_errors:
        raise ValueError("invalid intent contract: " + "; ".join(intent_errors))
    scene = _json_object(scene_file, "semantic scene")

    from scene_contract import SCENE_SCHEMA, validate as validate_scene

    scene_errors = validate_scene(scene, scene_file.parent)
    if scene_errors:
        raise ValueError("invalid semantic scene: " + "; ".join(scene_errors))
    if scene.get("schema") != SCENE_SCHEMA:
        raise ValueError(f"semantic scene must use {SCENE_SCHEMA}")
    if scene.get("coordinateSystem", {}).get("up") != "Z":
        raise ValueError("BRep exporter requires semantic scene coordinateSystem.up='Z'")
    intent_ref = scene.get("intentRef", {})
    referenced_path = Path(str(intent_ref.get("path", "")))
    if not referenced_path.is_absolute():
        referenced_path = scene_file.parent / referenced_path
    if referenced_path.resolve() != intent_file:
        raise ValueError("semantic scene intentRef must bind the supplied intent path")
    if intent_ref.get("sha256") != digest_file(intent_file):
        raise ValueError("semantic scene intentRef hash does not match the supplied intent")

    scene_parts = {
        item.get("id")
        for item in scene.get("parts", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    intent_parts = intent_physical_part_names(intent)
    if intent_parts != expected_parts:
        raise ValueError(
            "immutable intent parts do not match exported physical parts: "
            f"expected {sorted(expected_parts)}, observed {sorted(intent_parts)}"
        )
    if scene_parts != expected_parts:
        raise ValueError(
            "semantic scene parts do not match exported physical parts: "
            f"expected {sorted(expected_parts)}, observed {sorted(scene_parts)}"
        )
    non_brep = sorted(
        item.get("id")
        for item in scene.get("parts", [])
        if isinstance(item, dict)
        and item.get("id") in expected_parts
        and item.get("representationMaster") != "brep"
    )
    if non_brep:
        raise ValueError(
            "BRep exporter requires representationMaster brep: " + ", ".join(non_brep)
        )

    profile_ref = intent.get("printability", {}).get("profile")
    if not isinstance(profile_ref, dict):
        raise ValueError("intent printability.profile is required")
    raw_profile_path = profile_ref.get("path")
    if not isinstance(raw_profile_path, str) or not raw_profile_path.strip():
        raise ValueError("intent printability.profile.path is required")
    profile_file = Path(raw_profile_path)
    if not profile_file.is_absolute():
        profile_file = intent_file.parent / profile_file
    profile_file = profile_file.resolve()
    if not profile_file.is_file():
        raise ValueError(f"printer profile not found: {profile_file}")
    profile = _json_object(profile_file, "printer profile")
    if profile.get("schema") != "evidence-bambu-printer-profile/v1":
        raise ValueError("printer profile schema is unsupported")
    profile_hash = digest_file(profile_file)
    if profile_ref.get("sha256") != profile_hash:
        raise ValueError("printer profile hash does not match the intent")

    inputs: dict[str, Any] = {
        "intent": {
            "path": str(intent_file),
            "schema": intent["schema"],
            "sha256": digest_file(intent_file),
        },
        "scene": {
            "path": str(scene_file),
            "revision": scene["revision"],
            "schema": scene["schema"],
            "sha256": digest_file(scene_file),
        },
        "profile": {
            "id": profile.get("id"),
            "path": str(profile_file),
            "schema": profile["schema"],
            "sha256": profile_hash,
        },
    }
    source_file = Path(source_path).resolve()
    if source_file.suffix.lower() != ".py" or not source_file.is_file():
        raise ValueError("source_path must reference an existing Python source file")
    inputs["source"] = {
        "path": str(source_file),
        "schema": "python-source/v1",
        "sha256": digest_file(source_file),
    }
    return intent, scene, profile, inputs


def new_run_id() -> str:
    return str(uuid4())


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def identity_matrix() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def matrix_list(value: Iterable[Iterable[float]]) -> list[list[float]]:
    return [
        [round(float(component), 12) for component in row]
        for row in value
    ]


def rigid_matrix_errors(value: Any, path: str = "transform") -> list[str]:
    if not (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(row, list) and len(row) == 4 for row in value)
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for row in value
            for item in row
        )
    ):
        return [f"{path} must be a finite 4x4 matrix"]

    import numpy as np

    matrix = np.asarray(value, dtype=float)
    errors: list[str] = []
    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-9):
        errors.append(f"{path} must be affine with last row [0, 0, 0, 1]")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-7):
        errors.append(f"{path} rotation must be orthonormal (scaling is forbidden)")
    if not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-7):
        errors.append(f"{path} rotation determinant must be +1")
    return errors


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _exact_fields(value: dict[str, Any], expected: set[str], path: str) -> list[str]:
    if set(value) == expected:
        return []
    return [f"{path} fields must be exactly {sorted(expected)}"]


def _bounds_errors(value: Any, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors = _exact_fields(value, {"max", "min", "size"}, path)
    vectors: dict[str, list[float]] = {}
    for field in ("min", "max", "size"):
        vector = value.get(field)
        if not (
            isinstance(vector, list)
            and len(vector) == 3
            and all(_finite_number(item) for item in vector)
        ):
            errors.append(f"{path}.{field} must be a finite three-number vector")
            continue
        vectors[field] = [float(item) for item in vector]
    if set(vectors) == {"min", "max", "size"}:
        if any(item <= 0 for item in vectors["size"]):
            errors.append(f"{path}.size components must be positive")
        for index in range(3):
            observed = vectors["max"][index] - vectors["min"][index]
            tolerance = max(2e-4, abs(vectors["size"][index]) * 1e-7)
            if not math.isclose(observed, vectors["size"][index], abs_tol=tolerance):
                errors.append(f"{path}.size must equal max - min")
                break
    return errors


def _union_bounds_from_parts(parts: Any) -> dict[str, list[float]] | None:
    if not isinstance(parts, dict) or not parts:
        return None
    minimums: list[list[float]] = []
    maximums: list[list[float]] = []
    for record in parts.values():
        semantic = record.get("semantic") if isinstance(record, dict) else None
        bounds = semantic.get("boundsMm") if isinstance(semantic, dict) else None
        if _bounds_errors(bounds, "semantic bounds"):
            return None
        minimums.append([float(item) for item in bounds["min"]])
        maximums.append([float(item) for item in bounds["max"]])
    minimum = [min(values) for values in zip(*minimums)]
    maximum = [max(values) for values in zip(*maximums)]
    return {
        "max": [round(value, 9) for value in maximum],
        "min": [round(value, 9) for value in minimum],
        "size": [round(high - low, 9) for low, high in zip(minimum, maximum)],
    }


def semantic_assembly_record(
    parts: dict[str, Any],
    intent_sha256: str,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Bind the final union of semantic physical-part bounds to one intent hash."""

    bounds = _union_bounds_from_parts(parts)
    if bounds is None:
        raise ValueError("semantic assembly requires valid final semantic part bounds")
    if not isinstance(intent_sha256, str) or not SHA256_PATTERN.fullmatch(intent_sha256):
        raise ValueError("semantic assembly requires a lowercase intent SHA-256")
    errors = semantic_envelope_errors(bounds, intent)
    if errors:
        raise ValueError("; ".join(errors))
    return {"boundsMm": bounds, "intentSha256": intent_sha256}


def semantic_envelope_errors(bounds_mm: Any, intent: Any) -> list[str]:
    """Compare a final semantic assembly envelope with immutable intent."""

    dimensions = intent.get("dimensions_mm") if isinstance(intent, dict) else None
    observed_size = bounds_mm.get("size") if isinstance(bounds_mm, dict) else None
    if not isinstance(dimensions, dict):
        return ["intent dimensions_mm is unavailable"]
    errors: list[str] = []
    for axis_index, axis in enumerate("xyz"):
        target = dimensions.get(axis)
        expected = target.get("value") if isinstance(target, dict) else None
        observed = (
            observed_size[axis_index]
            if isinstance(observed_size, list) and len(observed_size) == 3
            else None
        )
        if not _finite_number(expected) or not _finite_number(observed):
            errors.append(f"semantic envelope dimension {axis} is unavailable")
        elif abs(float(observed) - float(expected)) > SEMANTIC_ENVELOPE_TOLERANCE_MM:
            errors.append(
                f"semantic envelope dimension {axis} differs from intent by more "
                f"than {SEMANTIC_ENVELOPE_TOLERANCE_MM} mm: "
                f"expected {float(expected)}, observed {float(observed)}"
            )
    return errors


def _bounds_match(left: Any, right: Any, tolerance_mm: float) -> bool:
    if _bounds_errors(left, "left bounds") or _bounds_errors(right, "right bounds"):
        return False
    return all(
        abs(float(left[field][axis]) - float(right[field][axis])) <= tolerance_mm
        for field in ("min", "max", "size")
        for axis in range(3)
    )


def _semantic_assembly_errors(
    value: Any,
    *,
    parts: Any,
    intent_sha256: Any,
) -> list[str]:
    path = "backendData.semanticAssembly"
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors = _exact_fields(value, {"boundsMm", "intentSha256"}, path)
    errors.extend(_bounds_errors(value.get("boundsMm"), f"{path}.boundsMm"))
    if value.get("intentSha256") != intent_sha256:
        errors.append(f"{path}.intentSha256 must match inputs.intent.sha256")
    expected = _union_bounds_from_parts(parts)
    if expected is None:
        errors.append(f"{path}.boundsMm cannot be derived from parts[*].semantic")
    elif not _bounds_match(
        value.get("boundsMm"), expected, SEMANTIC_RECORD_TOLERANCE_MM
    ):
        errors.append(
            f"{path}.boundsMm must equal the union of parts[*].semantic.boundsMm"
        )
    return errors


def _geometry_errors(value: Any, path: str) -> list[str]:
    expected = {"bodyCount", "boundsMm", "isVolume", "valid", "volumeMm3"}
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors = _exact_fields(value, expected, path)
    body_count = value.get("bodyCount")
    if not (
        isinstance(body_count, int)
        and not isinstance(body_count, bool)
        and body_count >= 1
    ):
        errors.append(f"{path}.bodyCount must be a positive integer")
    errors.extend(_bounds_errors(value.get("boundsMm"), f"{path}.boundsMm"))
    if value.get("isVolume") is not True:
        errors.append(f"{path}.isVolume must be true")
    if value.get("valid") is not True:
        errors.append(f"{path}.valid must be true")
    volume = value.get("volumeMm3")
    if not _finite_number(volume) or float(volume) <= 0:
        errors.append(f"{path}.volumeMm3 must be positive and finite")
    return errors


def _raw_brep_shape_errors(value: Any, path: str) -> list[str]:
    expected = {"bbox_mm", "solid_count", "valid", "volume_mm3"}
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors = _exact_fields(value, expected, path)
    solid_count = value.get("solid_count")
    if not (
        isinstance(solid_count, int)
        and not isinstance(solid_count, bool)
        and solid_count >= 1
    ):
        errors.append(f"{path}.solid_count must be a positive integer")
    errors.extend(_bounds_errors(value.get("bbox_mm"), f"{path}.bbox_mm"))
    if value.get("valid") is not True:
        errors.append(f"{path}.valid must be true")
    volume = value.get("volume_mm3")
    if not _finite_number(volume) or float(volume) <= 0:
        errors.append(f"{path}.volume_mm3 must be positive and finite")
    return errors


def _orientation_errors(value: Any, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors = _exact_fields(value, {"candidates", "selected", "strategy"}, path)
    if not isinstance(value.get("candidates"), list) or not value["candidates"]:
        errors.append(f"{path}.candidates must be a non-empty list")
    if not isinstance(value.get("selected"), dict) or not value["selected"]:
        errors.append(f"{path}.selected must be a non-empty object")
    if not isinstance(value.get("strategy"), str) or not value["strategy"].strip():
        errors.append(f"{path}.strategy must be a non-empty string")
    return errors


def _parameter_errors(value: Any, path: str = "backendData.parameters") -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors: list[str] = []
    required = {
        "affects",
        "default",
        "group",
        "label",
        "maximum",
        "minimum",
        "step",
        "unit",
        "value",
    }
    optional = {"group_zh", "label_zh"}
    for parameter_id, descriptor in value.items():
        item_path = f"{path}.{parameter_id}"
        if not isinstance(parameter_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]*", parameter_id
        ):
            errors.append(f"{item_path} has an invalid parameter id")
        if not isinstance(descriptor, dict):
            errors.append(f"{item_path} must be an object")
            continue
        fields = set(descriptor)
        if not required.issubset(fields) or fields - required - optional:
            errors.append(f"{item_path} has unsupported or missing fields")
        affects = descriptor.get("affects")
        if not isinstance(affects, list) or any(
            not isinstance(feature_id, str) or not feature_id for feature_id in affects
        ):
            errors.append(f"{item_path}.affects must be a list of non-empty strings")
        if not isinstance(descriptor.get("label"), str) or not descriptor["label"].strip():
            errors.append(f"{item_path}.label must be a non-empty string")
        for field in ("group", "unit"):
            observed = descriptor.get(field)
            if observed is not None and (
                not isinstance(observed, str) or not observed.strip()
            ):
                errors.append(f"{item_path}.{field} must be null or a non-empty string")
        for field in optional:
            if field in descriptor and (
                not isinstance(descriptor[field], str) or not descriptor[field].strip()
            ):
                errors.append(f"{item_path}.{field} must be a non-empty string")
        numbers = {
            field: descriptor.get(field)
            for field in ("default", "maximum", "minimum", "step", "value")
        }
        if any(not _finite_number(observed) for observed in numbers.values()):
            errors.append(f"{item_path} numeric fields must be finite")
            continue
        minimum = float(numbers["minimum"])
        maximum = float(numbers["maximum"])
        default = float(numbers["default"])
        step = float(numbers["step"])
        current = float(numbers["value"])
        if minimum > maximum or step <= 0:
            errors.append(f"{item_path} has invalid bounds or step")
        if not minimum <= default <= maximum or not minimum <= current <= maximum:
            errors.append(f"{item_path} default and value must be inside bounds")
        if step > 0:
            quotient = (current - minimum) / step
            if not math.isclose(quotient, round(quotient), abs_tol=1e-8):
                errors.append(f"{item_path}.value must align with step")
    return errors


def _hybrid_part_errors(value: dict[str, Any], part_id: str) -> list[str]:
    path = f"parts.{part_id}"
    master = value.get("representationMaster")
    expected = {
        "appearance",
        "bodyCount",
        "boundsMm",
        "colorRegions",
        "cutterNodeIds",
        "isVolume",
        "materialId",
        "orientation",
        "path",
        "positiveNodeIds",
        "print",
        "printTransform",
        "representationMaster",
        "semantic",
        "sha256",
        "triangles",
        "vertices",
        "volumeMm3",
        "volumeRemovedMm3",
        "watertight",
        "windingConsistent",
    }
    if master == "brep":
        expected.update({"masterStep", "stepConsistency"})
    errors = _exact_fields(value, expected, path)
    errors.extend(_geometry_errors(value.get("print"), f"{path}.print"))
    errors.extend(_geometry_errors(value.get("semantic"), f"{path}.semantic"))
    errors.extend(_bounds_errors(value.get("boundsMm"), f"{path}.boundsMm"))
    for field in ("bodyCount", "triangles", "vertices"):
        observed = value.get(field)
        minimum = 1 if field == "bodyCount" else 4
        if not (
            isinstance(observed, int)
            and not isinstance(observed, bool)
            and observed >= minimum
        ):
            errors.append(f"{path}.{field} must be an integer >= {minimum}")
    for field in ("isVolume", "watertight", "windingConsistent"):
        if value.get(field) is not True:
            errors.append(f"{path}.{field} must be true")
    for field in ("volumeMm3",):
        observed = value.get(field)
        if not _finite_number(observed) or float(observed) <= 0:
            errors.append(f"{path}.{field} must be positive and finite")
    removed = value.get("volumeRemovedMm3")
    if not _finite_number(removed) or float(removed) < 0:
        errors.append(f"{path}.volumeRemovedMm3 must be finite and non-negative")
    for field in ("path", "materialId"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{path}.{field} must be a non-empty string")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        errors.append(f"{path}.sha256 must be a lowercase SHA-256")
    errors.extend(rigid_matrix_errors(value.get("printTransform"), f"{path}.printTransform"))
    appearance = value.get("appearance")
    if not isinstance(appearance, dict):
        errors.append(f"{path}.appearance must be an object")
    else:
        errors.extend(
            _exact_fields(appearance, {"baseColor", "metallic", "roughness"}, f"{path}.appearance")
        )
        if not isinstance(appearance.get("baseColor"), str) or not re.fullmatch(
            r"#[0-9A-F]{6}", appearance["baseColor"]
        ):
            errors.append(f"{path}.appearance.baseColor must be uppercase #RRGGBB")
        for field in ("metallic", "roughness"):
            observed = appearance.get(field)
            if not _finite_number(observed) or not 0 <= float(observed) <= 1:
                errors.append(f"{path}.appearance.{field} must be between 0 and 1")
    for field, allow_empty in (("cutterNodeIds", True), ("positiveNodeIds", False)):
        observed = value.get(field)
        if not (
            isinstance(observed, list)
            and (allow_empty or bool(observed))
            and all(isinstance(item, str) and item for item in observed)
        ):
            qualifier = "a list" if allow_empty else "a non-empty list"
            errors.append(f"{path}.{field} must be {qualifier} of non-empty strings")
    if not isinstance(value.get("orientation"), dict) or not value["orientation"]:
        errors.append(f"{path}.orientation must be a non-empty object")
    regions = value.get("colorRegions")
    if not isinstance(regions, list):
        errors.append(f"{path}.colorRegions must be a list")
    else:
        region_ids: set[str] = set()
        for index, region in enumerate(regions):
            region_path = f"{path}.colorRegions[{index}]"
            if not isinstance(region, dict):
                errors.append(f"{region_path} must be an object")
                continue
            errors.extend(
                _exact_fields(
                    region,
                    {"bodyCount", "id", "isVolume", "materialId", "sourceMesh", "valid"},
                    region_path,
                )
            )
            region_id = region.get("id")
            if not isinstance(region_id, str) or not region_id.strip() or region_id in region_ids:
                errors.append(f"{region_path}.id must be a unique non-empty string")
            elif isinstance(region_id, str):
                region_ids.add(region_id)
            if not isinstance(region.get("materialId"), str) or not region["materialId"].strip():
                errors.append(f"{region_path}.materialId must be a non-empty string")
            if region.get("isVolume") is not True or region.get("valid") is not True:
                errors.append(f"{region_path} must be a valid volume")
            if not isinstance(region.get("sourceMesh"), dict):
                errors.append(f"{region_path}.sourceMesh must be an object")
    if master == "brep":
        master_step = value.get("masterStep")
        if not isinstance(master_step, dict) or set(master_step) != {
            "coordinateFrame",
            "path",
            "role",
            "sha256",
            "solidCount",
            "validator",
            "verifiedBrep",
        }:
            errors.append(f"{path}.masterStep has unsupported or missing fields")
        elif (
            master_step.get("coordinateFrame") != "semantic"
            or master_step.get("role") != "brep-master"
            or master_step.get("solidCount") != 1
            or master_step.get("validator") != "build123d-occt"
            or master_step.get("verifiedBrep") is not True
        ):
            errors.append(f"{path}.masterStep must be an OCCT-verified single-solid BRep master")
        step_consistency = value.get("stepConsistency")
        if not isinstance(step_consistency, dict) or set(step_consistency) != {
            "comparison",
            "pass",
            "step",
        }:
            errors.append(f"{path}.stepConsistency has unsupported or missing fields")
        elif step_consistency.get("pass") is not True or step_consistency.get("step") != master_step:
            errors.append(f"{path}.stepConsistency must pass and bind masterStep exactly")
    return errors


def _overlap_errors(value: Any, part_ids: set[str], path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    expected = {
        f"{left}&{right}"
        for index, left in enumerate(sorted(part_ids))
        for right in sorted(part_ids)[index + 1 :]
    }
    errors: list[str] = []
    if set(value) != expected:
        errors.append(f"{path} keys must exactly cover every physical-part pair")
    if any(not _finite_number(item) or float(item) < 0 for item in value.values()):
        errors.append(f"{path} values must be finite and non-negative")
    return errors


def _backend_data_errors(
    value: Any,
    *,
    backend: Any,
    part_ids: set[str],
    parts: Any,
    intent_sha256: Any,
    requires_three_mf: bool,
    revision: Any,
) -> list[str]:
    if not isinstance(value, dict):
        return ["backendData must be an object"]
    expected_by_backend = {
        "brep-part": {
            "exportAudit",
            "parameters",
            "printOrientation",
            "semanticAssembly",
        },
        "brep-color-regions": {
            "assembly",
            "exportAudit",
            "internalRegionMeshes",
            "overlapsMm3",
            "parameters",
            "parentCoverage",
            "printOrientation",
            "printPackageMode",
            "printPlate",
            "regions",
            "semanticAssembly",
            "threeMf",
        },
        "hybrid-mesh": {
            "assembly",
            "printPackageMode",
            "printPlate",
            "semanticAssembly",
            "stepConsistency",
            "threeMf",
        },
    }
    if backend == "brep-assembly":
        expected = {
            "assembly",
            "exportAudit",
            "overlapsMm3",
            "parameters",
            "printPlate",
            "semanticAssembly",
        }
        if requires_three_mf:
            expected.update({"internalPartMeshes", "partColors", "printPackageMode", "threeMf"})
    else:
        expected = expected_by_backend.get(backend)
    if expected is None:
        return []
    errors = _exact_fields(value, expected, "backendData")
    errors.extend(
        _semantic_assembly_errors(
            value.get("semanticAssembly"),
            parts=parts,
            intent_sha256=intent_sha256,
        )
    )

    if backend in {"brep-part", "brep-assembly", "brep-color-regions"}:
        if not isinstance(value.get("exportAudit"), dict):
            errors.append("backendData.exportAudit must be an object")
        errors.extend(_parameter_errors(value.get("parameters")))
    if backend in {"brep-part", "brep-color-regions"}:
        errors.extend(_orientation_errors(value.get("printOrientation"), "backendData.printOrientation"))

    if backend == "brep-assembly":
        assembly = value.get("assembly")
        if not isinstance(assembly, dict):
            errors.append("backendData.assembly must be an object")
        else:
            errors.extend(_exact_fields(assembly, {"maxOverlapMm3", "shape"}, "backendData.assembly"))
            threshold = assembly.get("maxOverlapMm3")
            if not _finite_number(threshold) or float(threshold) < 0:
                errors.append("backendData.assembly.maxOverlapMm3 must be finite and non-negative")
            errors.extend(_raw_brep_shape_errors(assembly.get("shape"), "backendData.assembly.shape"))
        errors.extend(_overlap_errors(value.get("overlapsMm3"), part_ids, "backendData.overlapsMm3"))
        print_plate = value.get("printPlate")
        if not isinstance(print_plate, dict):
            errors.append("backendData.printPlate must be an object")
        else:
            errors.extend(
                _exact_fields(
                    print_plate,
                    {"bodyCount", "boundsMm", "isVolume", "layout", "valid", "volumeMm3"},
                    "backendData.printPlate",
                )
            )
            geometry = {key: print_plate.get(key) for key in {"bodyCount", "boundsMm", "isVolume", "valid", "volumeMm3"}}
            errors.extend(_geometry_errors(geometry, "backendData.printPlate"))
            if not isinstance(print_plate.get("layout"), dict):
                errors.append("backendData.printPlate.layout must be an object")
        if requires_three_mf:
            if value.get("printPackageMode") != "separate_parts":
                errors.append("backendData.printPackageMode must be separate_parts")
            colors = value.get("partColors")
            if not isinstance(colors, dict) or set(colors) != part_ids or any(
                not isinstance(color, str) or not re.fullmatch(r"#[0-9A-F]{6}", color)
                for color in colors.values()
            ):
                errors.append("backendData.partColors must exactly map parts to uppercase #RRGGBB")
            meshes = value.get("internalPartMeshes")
            plate_meshes = meshes.get("plate-print") if isinstance(meshes, dict) else None
            if not isinstance(meshes, dict) or set(meshes) != {"plate-print"} or not isinstance(plate_meshes, dict) or set(plate_meshes) != part_ids:
                errors.append("backendData.internalPartMeshes must exactly cover every part in plate-print")
            if not isinstance(value.get("threeMf"), dict):
                errors.append("backendData.threeMf must be an object")

    if backend == "brep-color-regions":
        if value.get("printPackageMode") != "co_print_body":
            errors.append("backendData.printPackageMode must be co_print_body")
        assembly = value.get("assembly")
        if not isinstance(assembly, dict) or set(assembly) != {"shape"}:
            errors.append("backendData.assembly must contain exactly shape")
        else:
            errors.extend(_raw_brep_shape_errors(assembly.get("shape"), "backendData.assembly.shape"))
        errors.extend(_geometry_errors(value.get("printPlate"), "backendData.printPlate"))
        for field in ("internalRegionMeshes", "overlapsMm3", "parentCoverage", "regions", "threeMf"):
            if not isinstance(value.get(field), dict):
                errors.append(f"backendData.{field} must be an object")

    if backend == "hybrid-mesh":
        expected_mode = "co_print_body" if len(part_ids) == 1 else "separate_parts"
        if value.get("printPackageMode") != expected_mode:
            errors.append(f"backendData.printPackageMode must be {expected_mode}")
        assembly = value.get("assembly")
        if not isinstance(assembly, dict):
            errors.append("backendData.assembly must be an object")
        else:
            errors.extend(
                _exact_fields(assembly, {"maxOverlapMm3", "overlapsMm3"}, "backendData.assembly")
            )
            threshold = assembly.get("maxOverlapMm3")
            if not _finite_number(threshold) or float(threshold) < 0:
                errors.append("backendData.assembly.maxOverlapMm3 must be finite and non-negative")
            errors.extend(_overlap_errors(assembly.get("overlapsMm3"), part_ids, "backendData.assembly.overlapsMm3"))
        print_plate = value.get("printPlate")
        if not isinstance(print_plate, dict):
            errors.append("backendData.printPlate must be an object")
        else:
            errors.extend(
                _exact_fields(print_plate, {"boundsMm", "layout", "valid", "volumeMm3"}, "backendData.printPlate")
            )
            errors.extend(_bounds_errors(print_plate.get("boundsMm"), "backendData.printPlate.boundsMm"))
            if print_plate.get("valid") is not True:
                errors.append("backendData.printPlate.valid must be true")
            volume = print_plate.get("volumeMm3")
            if not _finite_number(volume) or float(volume) <= 0:
                errors.append("backendData.printPlate.volumeMm3 must be positive and finite")
            if not isinstance(print_plate.get("layout"), dict):
                errors.append("backendData.printPlate.layout must be an object")
        step_consistency = value.get("stepConsistency")
        if not isinstance(step_consistency, dict) or set(step_consistency) != {"parts", "pass", "revision", "schema"}:
            errors.append("backendData.stepConsistency has unsupported or missing fields")
        else:
            if step_consistency.get("schema") != "evidence-step-consistency/v1" or step_consistency.get("pass") is not True or step_consistency.get("revision") != revision:
                errors.append("backendData.stepConsistency must be a passing current-revision report")
            if not isinstance(step_consistency.get("parts"), dict):
                errors.append("backendData.stepConsistency.parts must be an object")
        if not isinstance(value.get("threeMf"), dict):
            errors.append("backendData.threeMf must be an object")
    return errors


def validate_manifest(data: Any) -> list[str]:
    """Validate the one backend-neutral contract and its artifact matrix."""

    if not isinstance(data, dict):
        return ["build manifest must be an object"]
    errors: list[str] = []
    if data.get("schema") != BUILD_SCHEMA:
        errors.append(f"schema must be {BUILD_SCHEMA}")
    for field in ("runId", "builtAt", "revision", "part"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            errors.append(f"{field} must be a non-empty string")
    backend = data.get("backend")
    if backend not in BUILD_BACKENDS:
        errors.append(f"backend must be one of {sorted(BUILD_BACKENDS)}")
    else:
        expected_top_level = {
            "artifactMatrix",
            "artifacts",
            "autoScale",
            "backend",
            "backendData",
            "builtAt",
            "coordinateFrames",
            "features",
            "inputs",
            "part",
            "parts",
            "pass",
            "revision",
            "runId",
            "scale",
            "schema",
            "warnings",
        }
        if backend.startswith("brep-"):
            expected_top_level.add("events")
        if backend in {"brep-assembly", "brep-color-regions", "hybrid-mesh"}:
            expected_top_level.add("materialPlan")
        if backend == "hybrid-mesh":
            expected_top_level.update({
                "excludedDisplayNodes",
                "excludedFromManufacturingNodes",
                "fastenerGeometryChecks",
                "fastenerGroups",
                "includedDisplayNodes",
            })
        if set(data) != expected_top_level:
            errors.append(
                f"top-level fields must be exactly {sorted(expected_top_level)} "
                f"for {backend}"
            )
    if data.get("scale") != 1.0:
        errors.append("scale must be 1.0")
    if data.get("autoScale") is not False:
        errors.append("autoScale must be false")
    if not isinstance(data.get("pass"), bool):
        errors.append("pass must be boolean")

    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        errors.append("inputs must be an object")
    else:
        expected_input_schemas = {
            "intent": "evidence-cad-intent/v4",
            "scene": "evidence-semantic-scene/v1",
            "profile": "evidence-bambu-printer-profile/v1",
        }
        for name, expected_schema in expected_input_schemas.items():
            record = inputs.get(name)
            if not isinstance(record, dict):
                errors.append(f"inputs.{name} must be an object")
                continue
            if not isinstance(record.get("path"), str) or not record["path"].strip():
                errors.append(f"inputs.{name}.path must be a non-empty string")
            digest = record.get("sha256")
            if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                errors.append(f"inputs.{name}.sha256 must be a lowercase SHA-256")
            if record.get("schema") != expected_schema:
                errors.append(f"inputs.{name}.schema must be {expected_schema}")
        scene_input = inputs.get("scene")
        if (
            isinstance(scene_input, dict)
            and scene_input.get("revision") != data.get("revision")
        ):
            errors.append("inputs.scene.revision must match build revision")
        source = inputs.get("source")
        if source is not None:
            if not isinstance(source, dict):
                errors.append("inputs.source must be an object when present")
            else:
                if not isinstance(source.get("path"), str) or not source["path"].strip():
                    errors.append("inputs.source.path must be a non-empty string")
                if source.get("schema") != "python-source/v1":
                    errors.append("inputs.source.schema must be python-source/v1")
                digest = source.get("sha256")
                if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                    errors.append("inputs.source.sha256 must be a lowercase SHA-256")
        if backend in {"brep-part", "brep-assembly", "brep-color-regions"} and not isinstance(
            source, dict
        ):
            errors.append(f"{backend} requires inputs.source")
        expected_inputs = set(expected_input_schemas)
        if backend in {"brep-part", "brep-assembly", "brep-color-regions"}:
            expected_inputs.add("source")
        if backend == "hybrid-mesh":
            expected_inputs.add("geometry")
            geometry = inputs.get("geometry")
            if not isinstance(geometry, dict) or not geometry:
                errors.append("hybrid-mesh requires non-empty inputs.geometry")
            else:
                for source_id, record in geometry.items():
                    path = f"inputs.geometry.{source_id}"
                    if not isinstance(source_id, str) or not source_id.strip():
                        errors.append("inputs.geometry keys must be non-empty strings")
                    if not isinstance(record, dict):
                        errors.append(f"{path} must be an object")
                        continue
                    if record.get("schema") != "mesh-source/v1":
                        errors.append(f"{path}.schema must be mesh-source/v1")
                    if not isinstance(record.get("path"), str) or not record["path"].strip():
                        errors.append(f"{path}.path must be a non-empty string")
                    digest = record.get("sha256")
                    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                        errors.append(f"{path}.sha256 must be a lowercase SHA-256")
        if set(inputs) != expected_inputs:
            errors.append(
                f"inputs must contain exactly {sorted(expected_inputs)} for {backend}"
            )

    parts = data.get("parts")
    part_ids: set[str] = set()
    if not isinstance(parts, dict) or not parts:
        errors.append("parts must be a non-empty object")
    else:
        for part_id, record in parts.items():
            if not isinstance(part_id, str) or not part_id.strip():
                errors.append("parts keys must be non-empty strings")
                continue
            part_ids.add(part_id)
            if not isinstance(record, dict):
                errors.append(f"parts.{part_id} must be an object")
                continue
            if record.get("representationMaster") not in {"brep", "mesh"}:
                errors.append(
                    f"parts.{part_id}.representationMaster must be brep or mesh"
                )
            if backend == "hybrid-mesh":
                errors.extend(_hybrid_part_errors(record, part_id))
            elif backend in {"brep-part", "brep-assembly", "brep-color-regions"}:
                errors.extend(
                    _exact_fields(
                        record,
                        {"print", "representationMaster", "semantic"},
                        f"parts.{part_id}",
                    )
                )
                errors.extend(_geometry_errors(record.get("print"), f"parts.{part_id}.print"))
                errors.extend(
                    _geometry_errors(record.get("semantic"), f"parts.{part_id}.semantic")
                )

        masters = {
            record.get("representationMaster")
            for record in parts.values()
            if isinstance(record, dict)
        }
        if backend == "brep-part" and (len(parts) != 1 or masters != {"brep"}):
            errors.append("brep-part requires exactly one brep-master part")
        if backend == "brep-color-regions" and (
            len(parts) != 1 or masters != {"brep"}
        ):
            errors.append(
                "brep-color-regions requires exactly one brep-master part"
            )
        if backend == "brep-assembly" and (len(parts) < 2 or masters != {"brep"}):
            errors.append("brep-assembly requires at least two brep-master parts")
        if backend == "hybrid-mesh" and "mesh" not in masters:
            errors.append("hybrid-mesh requires at least one mesh-master part")

    frames = data.get("coordinateFrames")
    if not isinstance(frames, dict):
        errors.append("coordinateFrames must be an object")
    else:
        for frame_name in ("part-print", "plate-print"):
            frame = frames.get(frame_name)
            transforms = frame.get("partTransforms") if isinstance(frame, dict) else None
            if not isinstance(transforms, dict):
                errors.append(
                    f"coordinateFrames.{frame_name}.partTransforms must be an object"
                )
                continue
            transform_ids = set(transforms)
            if part_ids and transform_ids != part_ids:
                errors.append(
                    f"coordinateFrames.{frame_name}.partTransforms keys must "
                    "exactly match parts"
                )
            for part_id, transform in transforms.items():
                errors.extend(
                    rigid_matrix_errors(
                        transform,
                        f"coordinateFrames.{frame_name}.partTransforms.{part_id}",
                    )
                )

    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("artifacts must be a non-empty object")
    else:
        for name, record in artifacts.items():
            if not isinstance(record, dict):
                errors.append(f"artifacts.{name} must be an object")
                continue
            if not isinstance(record.get("path"), str) or not record["path"].strip():
                errors.append(f"artifacts.{name}.path must be a non-empty string")
            digest = record.get("sha256")
            if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                errors.append(f"artifacts.{name}.sha256 must be a lowercase SHA-256")
        expected_frames = {
            "3mf": "plate-print",
            "glb:display": "semantic",
            "stl": "plate-print",
        }
        for name, expected_frame in expected_frames.items():
            record = artifacts.get(name)
            if isinstance(record, dict) and record.get("coordinateFrame") != expected_frame:
                errors.append(
                    f"artifacts.{name}.coordinateFrame must be {expected_frame}"
                )
        for part_id in part_ids:
            stl = artifacts.get(f"stl:{part_id}")
            if isinstance(stl, dict) and stl.get("coordinateFrame") != "part-print":
                errors.append(
                    f"artifacts.stl:{part_id}.coordinateFrame must be part-print"
                )
            step = artifacts.get(f"step:{part_id}")
            if isinstance(step, dict) and step.get("coordinateFrame") != "semantic":
                errors.append(
                    f"artifacts.step:{part_id}.coordinateFrame must be semantic"
                )

        if backend in {"brep-part", "brep-assembly", "brep-color-regions"}:
            if "exportAudit" not in artifacts:
                errors.append(f"{backend} requires artifacts.exportAudit")
            backend_data = data.get("backendData")
            export_audit = (
                backend_data.get("exportAudit")
                if isinstance(backend_data, dict)
                else None
            )
            if not isinstance(export_audit, dict):
                errors.append(f"{backend} requires backendData.exportAudit")
            else:
                if export_audit.get("schema") != EXPORT_AUDIT_SCHEMA:
                    errors.append(
                        f"backendData.exportAudit.schema must be {EXPORT_AUDIT_SCHEMA}"
                    )
                if export_audit.get("pass") is not True:
                    errors.append("backendData.exportAudit.pass must be true")
                if export_audit.get("errors") != []:
                    errors.append("backendData.exportAudit.errors must be empty")
                audited = export_audit.get("artifacts")
                if not isinstance(audited, dict) or not audited:
                    errors.append(
                        "backendData.exportAudit.artifacts must be a non-empty object"
                    )
                else:
                    expected_audited = {
                        key
                        for key in artifacts
                        if key == "glb:display"
                        or key == "stl"
                        or key.startswith("stl:")
                        or key.startswith("step:")
                        or key.startswith("plate-stl:")
                        or key.startswith("region:")
                    }
                    if set(audited) != expected_audited:
                        errors.append(
                            "backendData.exportAudit.artifacts must exactly cover "
                            "every owned STL, STEP, and display GLB artifact"
                        )
                    for key, audit_record in audited.items():
                        label = f"backendData.exportAudit.artifacts.{key}"
                        if not isinstance(audit_record, dict):
                            errors.append(f"{label} must be an object")
                            continue
                        if audit_record.get("pass") is not True:
                            errors.append(f"{label}.pass must be true")
                        if audit_record.get("errors") != []:
                            errors.append(f"{label}.errors must be empty")
                        artifact = artifacts.get(key)
                        if isinstance(artifact, dict):
                            if audit_record.get("path") != artifact.get("path"):
                                errors.append(
                                    f"{label}.path must match artifacts.{key}.path"
                                )
                            if audit_record.get("sha256") != artifact.get("sha256"):
                                errors.append(
                                    f"{label}.sha256 must match artifacts.{key}.sha256"
                                )

    matrix = data.get("artifactMatrix")
    matrix_parts = matrix.get("parts") if isinstance(matrix, dict) else None
    if not isinstance(matrix_parts, dict) or not matrix_parts:
        errors.append("artifactMatrix.parts must be a non-empty object")
    else:
        if part_ids and set(matrix_parts) != part_ids:
            errors.append("artifactMatrix.parts keys must exactly match parts")
        required_three_mf = False
        three_mf_statuses: set[str] = set()
        for part_id, requirements in matrix_parts.items():
            path = f"artifactMatrix.parts.{part_id}"
            if not isinstance(requirements, dict):
                errors.append(f"{path} must be an object")
                continue
            if set(requirements) != {"glb", "step", "stl", "threeMf"}:
                errors.append(
                    f"{path} must contain exactly glb, step, stl, and threeMf"
                )
                continue
            for kind, status in requirements.items():
                if status not in ARTIFACT_REQUIREMENTS:
                    errors.append(
                        f"{path}.{kind} must be required or not-applicable"
                    )
            if requirements.get("glb") != "required":
                errors.append(f"{path}.glb must be required")
            if requirements.get("stl") != "required":
                errors.append(f"{path}.stl must be required")
            master = (
                parts.get(part_id, {}).get("representationMaster")
                if isinstance(parts, dict)
                and isinstance(parts.get(part_id), dict)
                else None
            )
            expected_step = "required" if master == "brep" else "not-applicable"
            if requirements.get("step") != expected_step:
                errors.append(
                    f"{path}.step must be {expected_step} for a {master} master"
                )
            required_three_mf = (
                required_three_mf or requirements.get("threeMf") == "required"
            )
            status = requirements.get("threeMf")
            if isinstance(status, str):
                three_mf_statuses.add(status)

        if backend in {"brep-color-regions", "hybrid-mesh"} and (
            three_mf_statuses != {"required"}
        ):
            errors.append(f"{backend} requires 3MF for every physical part")
        if backend == "brep-part" and three_mf_statuses != {"not-applicable"}:
            errors.append("brep-part must mark 3MF not-applicable")
        if backend == "brep-assembly" and len(three_mf_statuses) > 1:
            errors.append(
                "brep-assembly cannot mix required and not-applicable 3MF statuses"
            )

        artifact_keys = set(artifacts) if isinstance(artifacts, dict) else set()
        for common_key in ("glb:display",):
            if common_key not in artifact_keys:
                errors.append(f"artifacts.{common_key} is required")
        for part_id in part_ids:
            if f"stl:{part_id}" not in artifact_keys:
                errors.append(f"artifacts.stl:{part_id} is required")
            master = parts[part_id].get("representationMaster")
            step_key = f"step:{part_id}"
            if master == "brep" and step_key not in artifact_keys:
                errors.append(f"artifacts.{step_key} is required for a brep master")
            if master == "mesh" and step_key in artifact_keys:
                errors.append(f"artifacts.{step_key} is forbidden for a mesh master")
        expected_artifact_keys = {
            "glb:display",
            *(f"stl:{part_id}" for part_id in part_ids),
        }
        if backend == "brep-part":
            expected_artifact_keys.update({
                "exportAudit",
                *(f"step:{part_id}" for part_id in part_ids),
            })
        elif backend == "brep-color-regions":
            expected_artifact_keys.update({
                "exportAudit",
                *(f"step:{part_id}" for part_id in part_ids),
            })
            plan = data.get("materialPlan")
            assignments = plan.get("assignments", []) if isinstance(plan, dict) else []
            for assignment in assignments:
                if (
                    isinstance(assignment, dict)
                    and assignment.get("scope") == "brep-region"
                    and isinstance(assignment.get("region"), str)
                    and assignment["region"].strip()
                ):
                    expected_artifact_keys.add(
                        f"region:{assignment['region']}:semantic"
                    )
                    expected_artifact_keys.add(
                        f"region:{assignment['region']}:print"
                    )
        elif backend == "brep-assembly":
            expected_artifact_keys.update({
                "exportAudit",
                "step:assembly",
                "stl",
                *(f"step:{part_id}" for part_id in part_ids),
            })
            if required_three_mf:
                expected_artifact_keys.update(
                    f"plate-stl:{part_id}" for part_id in part_ids
                )
        elif backend == "hybrid-mesh":
            expected_artifact_keys.update({
                "boundScene",
                "shapeConsistency",
                "stepConsistency",
                "stl",
                *(
                    f"step:{part_id}"
                    for part_id in part_ids
                    if isinstance(parts.get(part_id), dict)
                    and parts[part_id].get("representationMaster") == "brep"
                ),
            })
        if required_three_mf:
            expected_artifact_keys.update({"3mf", "materialPlan"})
        if artifact_keys != expected_artifact_keys:
            errors.append(
                f"artifacts must contain exactly {sorted(expected_artifact_keys)} "
                f"for {backend}"
            )
        if required_three_mf and "3mf" not in artifact_keys:
            errors.append("artifacts.3mf is required by artifactMatrix")
        if required_three_mf and "materialPlan" not in artifact_keys:
            errors.append("artifacts.materialPlan is required with 3MF")
        if required_three_mf and not isinstance(data.get("materialPlan"), dict):
            errors.append("materialPlan must be an object with 3MF")
        if required_three_mf:
            three_mf = artifacts.get("3mf") if isinstance(artifacts, dict) else None
            if not isinstance(three_mf, dict) or three_mf.get("verified") is not True:
                errors.append("artifacts.3mf.verified must be true")
            if not isinstance(three_mf, dict) or three_mf.get("validator") != "lib3mf":
                errors.append("artifacts.3mf.validator must be lib3mf")
            plan = data.get("materialPlan")
            errors.extend(
                f"materialPlan: {error}" for error in validate_material_plan(plan)
            )
            if isinstance(plan, dict):
                if plan.get("part") != data.get("part"):
                    errors.append("materialPlan.part must match build part")
                assignments = plan.get("assignments", [])
                assigned_parts = {
                    item.get("part")
                    for item in assignments
                    if isinstance(item, dict)
                }
                if not assigned_parts or not assigned_parts.issubset(part_ids):
                    errors.append(
                        "materialPlan assignments must reference build parts"
                    )
                scopes = {
                    item.get("scope")
                    for item in assignments
                    if isinstance(item, dict)
                }
                expected_scopes = {
                    "brep-color-regions": {"brep-region"},
                    "brep-assembly": {"whole-part"},
                    "hybrid-mesh": {"volumetric-region", "whole-part"},
                }.get(backend, set())
                if not scopes or not scopes.issubset(expected_scopes):
                    errors.append(
                        f"materialPlan assignment scopes are invalid for {backend}"
                    )
            backend_data = data.get("backendData")
            package_mode = (
                backend_data.get("printPackageMode")
                if isinstance(backend_data, dict)
                else None
            )
            if isinstance(plan, dict) and plan.get("packageMode") != package_mode:
                errors.append(
                    "materialPlan.packageMode must match backendData.printPackageMode"
                )
        if not required_three_mf and "3mf" in artifact_keys:
            errors.append(
                "artifactMatrix must mark threeMf required when artifacts.3mf exists"
            )
        if not required_three_mf and (
            "materialPlan" in artifact_keys or data.get("materialPlan") is not None
        ):
            errors.append("materialPlan is forbidden when 3MF is not required")

        errors.extend(
            _backend_data_errors(
                data.get("backendData"),
                backend=backend,
                part_ids=part_ids,
                parts=parts,
                intent_sha256=(
                    inputs.get("intent", {}).get("sha256")
                    if isinstance(inputs, dict)
                    and isinstance(inputs.get("intent"), dict)
                    else None
                ),
                requires_three_mf=required_three_mf,
                revision=data.get("revision"),
            )
        )

    return errors


def file_binding_errors(data: Any, base_dir: Path) -> list[str]:
    """Verify that every declared input/artifact still matches its SHA-256."""

    if not isinstance(data, dict):
        return ["build manifest must be an object"]
    errors: list[str] = []
    records: list[tuple[str, Any]] = []
    inputs = data.get("inputs")
    if isinstance(inputs, dict):
        records.extend(
            (f"inputs.{name}", record)
            for name, record in inputs.items()
            if name != "geometry"
        )
        geometry = inputs.get("geometry")
        if isinstance(geometry, dict):
            records.extend(
                (f"inputs.geometry.{name}", record)
                for name, record in geometry.items()
            )
    artifacts = data.get("artifacts")
    if isinstance(artifacts, dict):
        records.extend(
            (f"artifacts.{name}", record) for name, record in artifacts.items()
        )
    for label, record in records:
        if not isinstance(record, dict):
            continue
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"{label}.path is required for file verification")
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = base_dir / path
        try:
            observed = digest_file(path.resolve())
        except OSError as error:
            errors.append(f"{label} cannot be read: {error}")
            continue
        if record.get("sha256") != observed:
            errors.append(f"{label}.sha256 does not match {path.resolve()}")
    return errors


def _bound_json(reference: Any, base_dir: Path) -> dict[str, Any] | None:
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
        return None
    path = Path(reference["path"])
    if not path.is_absolute():
        path = base_dir / path
    try:
        value = json.loads(path.resolve().read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def semantic_evidence_errors(data: Any, base_dir: Path) -> list[str]:
    """Verify final semantic bounds against hash-bound intent and geometry evidence."""

    if not isinstance(data, dict):
        return ["build manifest must be an object"]
    errors: list[str] = []
    backend_data = data.get("backendData")
    semantic_assembly = (
        backend_data.get("semanticAssembly")
        if isinstance(backend_data, dict)
        else None
    )
    semantic_bounds = (
        semantic_assembly.get("boundsMm")
        if isinstance(semantic_assembly, dict)
        else None
    )
    inputs = data.get("inputs")
    intent_reference = inputs.get("intent") if isinstance(inputs, dict) else None
    intent = _bound_json(intent_reference, base_dir)
    scene_reference = inputs.get("scene") if isinstance(inputs, dict) else None
    scene = _bound_json(scene_reference, base_dir)
    if isinstance(data.get("materialPlan"), dict):
        errors.extend(validate_material_sources(data["materialPlan"], intent, scene))
    if not isinstance(semantic_bounds, dict):
        errors.append("backendData.semanticAssembly.boundsMm is unavailable")
    else:
        errors.extend(semantic_envelope_errors(semantic_bounds, intent))

    parts = data.get("parts")
    backend = data.get("backend")
    if backend in {"brep-part", "brep-assembly", "brep-color-regions"}:
        export_audit = (
            backend_data.get("exportAudit")
            if isinstance(backend_data, dict)
            else None
        )
        audited = export_audit.get("artifacts") if isinstance(export_audit, dict) else None
        if isinstance(parts, dict) and isinstance(audited, dict):
            for part_id, part in parts.items():
                semantic = part.get("semantic") if isinstance(part, dict) else None
                step = audited.get(f"step:{part_id}")
                observed = step.get("observed") if isinstance(step, dict) else None
                if not isinstance(semantic, dict) or not isinstance(observed, dict) or not _bounds_match(
                    semantic.get("boundsMm"),
                    observed.get("boundsMm"),
                    SEMANTIC_ARTIFACT_TOLERANCE_MM,
                ):
                    errors.append(
                        f"parts.{part_id}.semantic.boundsMm does not match its "
                        "hash-bound STEP readback"
                    )
            assembly_key = "step:assembly" if backend == "brep-assembly" else None
            if assembly_key is not None:
                assembly_step = audited.get(assembly_key)
                observed = (
                    assembly_step.get("observed")
                    if isinstance(assembly_step, dict)
                    else None
                )
                if not isinstance(observed, dict) or not _bounds_match(
                    semantic_bounds,
                    observed.get("boundsMm"),
                    SEMANTIC_ARTIFACT_TOLERANCE_MM,
                ):
                    errors.append(
                        "backendData.semanticAssembly.boundsMm does not match the "
                        "hash-bound assembly STEP readback"
                    )
    elif backend == "hybrid-mesh":
        artifacts = data.get("artifacts")
        reference = (
            artifacts.get("shapeConsistency")
            if isinstance(artifacts, dict)
            else None
        )
        consistency = _bound_json(reference, base_dir)
        evidence_parts = (
            consistency.get("parts") if isinstance(consistency, dict) else None
        )
        if (
            not isinstance(consistency, dict)
            or consistency.get("schema")
            != "evidence-shape-consistency-manifest/v1"
            or consistency.get("pass") is not True
            or consistency.get("revision") != data.get("revision")
            or consistency.get("skippedParts") != []
            or not isinstance(evidence_parts, dict)
            or not isinstance(parts, dict)
            or set(evidence_parts) != set(parts)
        ):
            errors.append(
                "Hybrid shape-consistency artifact must exactly cover every physical part"
            )
        else:
            for part_id, part in parts.items():
                semantic = part.get("semantic") if isinstance(part, dict) else None
                evidence = evidence_parts.get(part_id)
                meshes = evidence.get("meshes") if isinstance(evidence, dict) else None
                manufacturing = meshes.get("b") if isinstance(meshes, dict) else None
                if (
                    not isinstance(evidence, dict)
                    or evidence.get("pass") is not True
                    or not isinstance(semantic, dict)
                    or not isinstance(manufacturing, dict)
                    or not _bounds_match(
                        semantic.get("boundsMm"),
                        manufacturing.get("boundsMm"),
                        SEMANTIC_ARTIFACT_TOLERANCE_MM,
                    )
                ):
                    errors.append(
                        f"parts.{part_id}.semantic.boundsMm does not match its "
                        "hash-bound Hybrid shape-consistency artifact"
                    )
    return errors
