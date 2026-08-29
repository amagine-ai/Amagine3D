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
INTERFACE_CONNECTIONS = {
    "dovetail",
    "glue-face",
    "peg-socket",
    "pin-socket",
    "press-fit",
    "snap-fit",
    "tab-slot",
    "threaded-insert",
}
ASSEMBLY_AXES = {"+X", "+Y", "+Z", "-X", "-Y", "-Z"}
INTENT_SCHEMA = "evidence-cad-intent/v4"
ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")
FEATURE_KINDS = {
    "additive",
    "button",
    "cavity",
    "clearance",
    "control",
    "cutout",
    "detail",
    "envelope",
    "fastener",
    "hole",
    "interface",
    "logo",
    "mount",
    "part",
    "port",
    "recess",
    "region",
    "seam",
    "slot",
    "surface",
    "window",
}
PLACED_OPENING_KINDS = {
    "cavity",
    "cutout",
    "hole",
    "port",
    "recess",
    "slot",
    "window",
}
FACES = {"back", "bottom", "front", "internal", "left", "multiple", "right", "top"}
DIRECTIONS = {
    "+X",
    "+Y",
    "+Z",
    "-X",
    "-Y",
    "-Z",
    "multiple",
    "none",
    "surface-normal",
    "through-X",
    "through-Y",
    "through-Z",
}
EDGE_CROSSING = {"allowed", "forbidden", "not-applicable", "required"}
FACE_DIRECTIONS = {
    "back": {"+Y", "through-Y"},
    "bottom": {"-Z", "through-Z"},
    "front": {"-Y", "through-Y"},
    "left": {"-X", "through-X"},
    "right": {"+X", "through-X"},
    "top": {"+Z", "through-Z"},
}
COORDINATE_SYSTEM = {
    "back": "y-max",
    "bottom": "z-min",
    "front": "y-min",
    "left": "x-min",
    "right": "x-max",
    "top": "z-max",
    "x_positive": "right",
    "y_positive": "back",
    "z_positive": "top",
}


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


def validate_manufacturing(
    manufacturing,
    feature_ids: set[str] | None = None,
) -> list[str]:
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
                connection = interface.get("connection")
                if connection not in INTERFACE_CONNECTIONS:
                    errors.append(
                        f"manufacturing.interfaces[{index}].connection is invalid"
                    )
                assembly_axis = interface.get("assembly_axis")
                if assembly_axis not in ASSEMBLY_AXES:
                    errors.append(
                        f"manufacturing.interfaces[{index}].assembly_axis is invalid"
                    )
                if (
                    "clearance_mm" not in interface
                    or not _non_negative_number(interface.get("clearance_mm"))
                ):
                    errors.append(
                        f"manufacturing.interfaces[{index}].clearance_mm must be finite and non-negative"
                    )
                if not _positive_number(interface.get("engagement_mm")):
                    errors.append(
                        f"manufacturing.interfaces[{index}].engagement_mm must be positive"
                    )
                interface_features = interface.get("features")
                if not isinstance(interface_features, list) or not interface_features:
                    errors.append(
                        f"manufacturing.interfaces[{index}].features must reference modeled connector feature IDs"
                    )
                elif not all(
                    isinstance(item, str) and ID_PATTERN.fullmatch(item)
                    for item in interface_features
                ):
                    errors.append(
                        f"manufacturing.interfaces[{index}].features must contain valid feature IDs"
                    )
                elif feature_ids is not None and not set(interface_features).issubset(
                    feature_ids
                ):
                    errors.append(
                        "manufacturing.interfaces"
                        f"[{index}].features reference unknown feature IDs"
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


def validate_coordinate_system(coordinate_system) -> list[str]:
    if not isinstance(coordinate_system, dict):
        return ["coordinate_system must declare the object semantic frame"]
    errors = []
    for key, expected in COORDINATE_SYSTEM.items():
        if coordinate_system.get(key) != expected:
            errors.append(f"coordinate_system.{key} must be {expected}")
    return errors


def validate_feature_semantics(feature: dict, index: int) -> list[str]:
    errors: list[str] = []
    feature_id = f"features[{index}]"
    kind = feature.get("kind")
    face = feature.get("face")
    direction = feature.get("direction")
    edge_crossing = feature.get("edge_crossing")

    if kind is not None and kind not in FEATURE_KINDS:
        errors.append(f"{feature_id}.kind is invalid")
    if face is not None and face not in FACES:
        errors.append(f"{feature_id}.face is invalid")
    if direction is not None and direction not in DIRECTIONS:
        errors.append(f"{feature_id}.direction is invalid")
    if edge_crossing is not None and edge_crossing not in EDGE_CROSSING:
        errors.append(f"{feature_id}.edge_crossing is invalid")
    if direction is not None and face is None:
        errors.append(f"{feature_id}.direction requires face")
    if edge_crossing is not None and face is None:
        errors.append(f"{feature_id}.edge_crossing requires face")
    if kind in PLACED_OPENING_KINDS:
        for key, value in (
            ("face", face),
            ("direction", direction),
            ("edge_crossing", edge_crossing),
        ):
            if value is None:
                errors.append(f"{feature_id}.{key} is required for kind {kind}")
    if (
        face in FACE_DIRECTIONS
        and direction not in {None, "none", "surface-normal"}
        and direction not in FACE_DIRECTIONS[face]
    ):
        expected = ", ".join(sorted(FACE_DIRECTIONS[face]))
        errors.append(f"{feature_id}.direction must be one of {expected} for {face}")
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
    errors.extend(validate_coordinate_system(data.get("coordinate_system")))

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
                if not ID_PATTERN.fullmatch(feature_id):
                    errors.append(f"features[{index}].id is invalid")
                ids.append(feature_id)
            errors.extend(validate_feature_semantics(feature, index))
        if len(ids) != len(set(ids)):
            errors.append("feature ids must be unique")
    feature_ids = (
        {item for item in ids if isinstance(item, str)}
        if isinstance(features, list)
        else set()
    )

    errors.extend(validate_manufacturing(data.get("manufacturing"), feature_ids))

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
