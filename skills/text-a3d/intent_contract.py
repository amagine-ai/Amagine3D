"""Validate an independent modeling intent contract before geometry exists."""

from __future__ import annotations

import json
from hashlib import sha256
import math
from pathlib import Path
import re
import sys


MODES = {
    "inspect",
    "reference-inspired",
    "reference-reproduction",
    "recognizable-form",
    "specification",
}
REPRESENTATIONS = {"full-3d", "orthographic-solid", "relief", "surface-led"}
SOURCES = {"inferred", "reference", "standard", "user"}
CONFIDENCE = {"high", "low", "medium"}
MANUFACTURING_MODES = {"multipart", "single-part"}
INTENT_SCHEMA = "evidence-cad-intent/v4"
ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")


def _positive_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _non_negative_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _load_profile(reference: dict, base_dir: Path | None, errors: list[str]) -> dict | None:
    if not isinstance(reference, dict):
        errors.append("printability.profile must be an object")
        return None
    raw_path = reference.get("path")
    digest = reference.get("sha256")
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append("printability.profile.path is required")
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append("printability.profile.sha256 must be a lowercase SHA-256 digest")
    if base_dir is None:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    try:
        payload = path.read_bytes()
        profile = json.loads(payload)
    except Exception as error:
        errors.append(f"printability profile cannot be read: {error}")
        return None
    if not isinstance(profile, dict):
        errors.append("printability profile must contain a JSON object")
        return None
    if isinstance(digest, str) and sha256(payload).hexdigest() != digest:
        errors.append("printability profile hash does not match")
    if profile.get("schema") != "evidence-bambu-printer-profile/v1":
        errors.append("printability profile schema is unsupported")
    if profile.get("vendor") != "Bambu Lab":
        errors.append("printability profile vendor must be Bambu Lab")
    for key in ("single_line_floor_mm", "process_wall_target_mm"):
        if not _positive_number(profile.get("derived", {}).get(key)):
            errors.append(f"printability profile derived.{key} must be positive")
    return profile


def validate_manufacturing(manufacturing) -> list[str]:
    errors: list[str] = []
    if not isinstance(manufacturing, dict):
        return ["manufacturing must be an object"]
    mode = manufacturing.get("mode")
    if mode not in MANUFACTURING_MODES:
        errors.append("manufacturing.mode must be single-part or multipart")
    raw_parts = manufacturing.get("parts")
    part_names: set[str] = set()
    if mode == "single-part":
        if "parts" in manufacturing:
            errors.append("manufacturing.parts is only valid for multipart")
        if "interfaces" in manufacturing:
            errors.append("manufacturing.interfaces is only valid for multipart")
    elif mode == "multipart":
        if not isinstance(raw_parts, list) or len(raw_parts) < 2:
            errors.append("manufacturing.parts must declare at least two parts")
        else:
            names: list[str] = []
            for index, part in enumerate(raw_parts):
                if not isinstance(part, dict):
                    errors.append(f"manufacturing.parts[{index}] must be an object")
                    continue
                part_name = part.get("name")
                if not isinstance(part_name, str) or not ID_PATTERN.fullmatch(part_name):
                    errors.append(f"manufacturing.parts[{index}].name is invalid")
                else:
                    names.append(part_name)
                for key in ("role", "acceptance"):
                    if not isinstance(part.get(key), str) or not part[key].strip():
                        errors.append(f"manufacturing.parts[{index}].{key} is required")
            if len(names) != len(set(names)):
                errors.append("manufacturing part names must be unique")
            part_names = set(names)
        interfaces = manufacturing.get("interfaces")
        if not isinstance(interfaces, list) or not interfaces:
            errors.append("manufacturing.interfaces must declare at least one interface")
        else:
            interface_ids: list[str] = []
            for index, interface in enumerate(interfaces):
                if not isinstance(interface, dict):
                    errors.append(
                        f"manufacturing.interfaces[{index}] must be an object"
                    )
                    continue
                interface_id = interface.get("id")
                if not isinstance(interface_id, str) or not ID_PATTERN.fullmatch(
                    interface_id
                ):
                    errors.append(f"manufacturing.interfaces[{index}].id is invalid")
                else:
                    interface_ids.append(interface_id)
                between = interface.get("between")
                if (
                    not isinstance(between, list)
                    or len(between) != 2
                    or not all(isinstance(item, str) for item in between)
                ):
                    errors.append(
                        f"manufacturing.interfaces[{index}].between must name two parts"
                    )
                elif between[0] == between[1]:
                    errors.append(
                        f"manufacturing.interfaces[{index}].between must name two distinct parts"
                    )
                elif part_names and not set(between).issubset(part_names):
                    errors.append(
                        "manufacturing.interfaces"
                        f"[{index}].between references unknown parts"
                    )
                if (
                    "clearance_mm" in interface
                    and not _non_negative_number(interface.get("clearance_mm"))
                ):
                    errors.append(
                        f"manufacturing.interfaces[{index}].clearance_mm must be finite and non-negative"
                    )
                if (
                    not isinstance(interface.get("acceptance"), str)
                    or not interface["acceptance"].strip()
                ):
                    errors.append(
                        f"manufacturing.interfaces[{index}].acceptance is required"
                    )
            if len(interface_ids) != len(set(interface_ids)):
                errors.append("manufacturing interface ids must be unique")
    return errors


def validate(data: dict, base_dir: Path | None = None) -> list[str]:
    if not isinstance(data, dict):
        return ["intent must contain a JSON object"]
    errors: list[str] = []
    if data.get("schema") != INTENT_SCHEMA:
        errors.append(f"schema must be {INTENT_SCHEMA}")
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", str(data.get("part", ""))):
        errors.append("part must be a lowercase filename-safe slug")
    if data.get("task_mode") not in MODES:
        errors.append(f"task_mode must be one of {sorted(MODES)}")
    if data.get("representation") not in REPRESENTATIONS:
        errors.append(f"representation must be one of {sorted(REPRESENTATIONS)}")

    dimensions = data.get("dimensions_mm")
    if not isinstance(dimensions, dict):
        errors.append("dimensions_mm must define x, y, and z evidence")
    else:
        for axis in "xyz":
            item = dimensions.get(axis)
            if not isinstance(item, dict):
                errors.append(f"dimensions_mm.{axis} is missing")
                continue
            if not _positive_number(item.get("value")):
                errors.append(f"dimensions_mm.{axis}.value must be positive")
            if item.get("source") not in SOURCES:
                errors.append(f"dimensions_mm.{axis}.source must be evidence-scoped")
            if item.get("confidence") not in CONFIDENCE:
                errors.append(f"dimensions_mm.{axis}.confidence is invalid")

    features = data.get("features")
    ids: list[str] = []
    if not isinstance(features, list) or not features:
        errors.append("features must be a non-empty list")
    else:
        for index, feature in enumerate(features):
            if not isinstance(feature, dict):
                errors.append(f"features[{index}] must be an object")
                continue
            for key in ("id", "evidence", "acceptance"):
                if not isinstance(feature.get(key), str) or not feature[key].strip():
                    errors.append(f"features[{index}].{key} is required")
            feature_id = feature.get("id")
            if isinstance(feature_id, str) and feature_id.strip():
                ids.append(feature_id)
        if len(ids) != len(set(ids)):
            errors.append("feature ids must be unique")
    feature_ids = (
        {item for item in ids if isinstance(item, str)}
        if isinstance(features, list)
        else set()
    )

    errors.extend(validate_manufacturing(data.get("manufacturing")))

    visual = data.get("visual")
    if not isinstance(visual, dict) or not isinstance(visual.get("required"), bool):
        errors.append("visual.required must be boolean")
    elif visual["required"]:
        if visual.get("reference_view") not in {
            "bottom", "front", "isometric", "side", "top",
        }:
            errors.append("visual.reference_view is required for visual validation")
        if not isinstance(visual.get("landmarks"), list) or not visual["landmarks"]:
            errors.append("visual.landmarks must be non-empty when visual is required")

    if not isinstance(data.get("assumptions"), list):
        errors.append("assumptions must be a list")
    if not isinstance(data.get("reference_files"), list):
        errors.append("reference_files must be a list")

    printability = data.get("printability")
    if not isinstance(printability, dict):
        errors.append("printability must define a Bambu manufacturing plan")
    else:
        profile = _load_profile(printability.get("profile"), base_dir, errors)
        if printability.get("build_axis") != "+Z":
            errors.append("printability.build_axis must be +Z")
        if printability.get("bed_contact") != "z-min":
            errors.append("printability.bed_contact must be z-min")
        if printability.get("support_policy") not in {
            "support-free",
            "supports-allowed",
            "supports-required",
        }:
            errors.append("printability.support_policy is invalid")
        target = printability.get("minimum_wall_target_mm")
        if not _positive_number(target):
            errors.append("printability.minimum_wall_target_mm must be positive")
        elif profile is not None:
            process_target = profile["derived"]["process_wall_target_mm"]
            if target + 1e-9 < process_target:
                errors.append(
                    "printability.minimum_wall_target_mm must meet the selected "
                    f"process wall target ({process_target:g} mm)"
                )
        critical = printability.get("critical_features")
        if not isinstance(critical, list) or not all(
            isinstance(item, str) and item.strip() for item in critical
        ):
            errors.append("printability.critical_features must be a list of feature IDs")
        elif feature_ids and not set(critical).issubset(feature_ids):
            errors.append(
                "printability.critical_features must reference declared feature IDs"
            )
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} intent.json")
        return 2
    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        errors = validate(data, path.resolve().parent)
    except Exception as error:
        errors = [str(error)]
        data = {}
    result = {
        "errors": errors,
        "intent": str(path.resolve()),
        "part": data.get("part") if isinstance(data, dict) else None,
        "pass": not errors,
        "schema": "intent-validation/v4",
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
