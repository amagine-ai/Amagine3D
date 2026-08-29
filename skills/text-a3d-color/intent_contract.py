"""Validate a color-aware evidence contract before region geometry exists."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import re
import sys


HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MODES = {
    "inspect",
    "recognizable-form",
    "reference-inspired",
    "reference-reproduction",
    "specification",
}
SOURCES = {"inferred", "reference", "standard", "user"}
CONFIDENCE = {"high", "low", "medium"}
TRANSMISSION = {"opaque", "translucent", "transparent"}
PRINT_PACKAGE_MODES = {"co_print_body", "separate_parts"}
REGION_CONTINUITY = {
    "continuous-core",
    "not-applicable",
    "separate-part",
    "surface-detail",
}
VIEWS = {"bottom", "front", "isometric", "side", "top"}
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
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


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
    errors: list[str] = []
    if data.get("schema") != "evidence-color-intent/v3":
        errors.append("schema must be evidence-color-intent/v3")
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", str(data.get("part", ""))):
        errors.append("part must be a lowercase filename-safe slug")
    if data.get("task_mode") not in MODES:
        errors.append("task_mode is invalid")
    if data.get("representation") not in {
        "full-3d", "orthographic-solid", "relief", "surface-led",
    }:
        errors.append("representation is invalid")
    errors.extend(validate_coordinate_system(data.get("coordinate_system")))

    dimensions = data.get("dimensions_mm")
    if not isinstance(dimensions, dict):
        errors.append("dimensions_mm is required")
    else:
        for axis in "xyz":
            item = dimensions.get(axis)
            if not isinstance(item, dict):
                errors.append(f"dimensions_mm.{axis} is required")
                continue
            if not _positive_number(item.get("value")):
                errors.append(f"dimensions_mm.{axis}.value must be positive")
            if item.get("source") not in SOURCES:
                errors.append(f"dimensions_mm.{axis}.source must be evidence-scoped")
            if item.get("confidence") not in CONFIDENCE:
                errors.append(f"dimensions_mm.{axis}.confidence is invalid")

    feature_ids: set[str] = set()
    features = data.get("features")
    if not isinstance(features, list) or not features:
        errors.append("features must be a non-empty list")
    else:
        ids = []
        for index, feature in enumerate(features):
            if not isinstance(feature, dict):
                errors.append(f"features[{index}] must be an object")
                continue
            feature_id = feature.get("id")
            ids.append(feature_id)
            if not re.fullmatch(
                r"[a-z0-9]+(?:[-_][a-z0-9]+)*", str(feature_id or "")
            ):
                errors.append(f"features[{index}].id is invalid")
            for key in ("evidence", "acceptance"):
                if not isinstance(feature.get(key), str) or not feature[key].strip():
                    errors.append(f"features[{index}].{key} is required")
            errors.extend(validate_feature_semantics(feature, index))
        if len(ids) != len(set(ids)):
            errors.append("feature ids must be unique")
        feature_ids = {item for item in ids if isinstance(item, str)}

    regions = data.get("color_regions")
    if not isinstance(regions, list) or len(regions) < 2:
        errors.append("color_regions must contain at least two regions")
    else:
        names = []
        for index, region in enumerate(regions):
            if not isinstance(region, dict):
                errors.append(f"color_regions[{index}] must be an object")
                continue
            names.append(region.get("name"))
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", str(region.get("name", ""))):
                errors.append(f"color_regions[{index}].name is invalid")
            if not HEX.fullmatch(str(region.get("hex", ""))):
                errors.append(f"color_regions[{index}].hex must be #RRGGBB")
            for key in ("purpose", "boundary", "evidence"):
                if not isinstance(region.get(key), str) or not region[key].strip():
                    errors.append(f"color_regions[{index}].{key} is required")
            continuity = region.get("continuity")
            if continuity is not None and continuity not in REGION_CONTINUITY:
                errors.append(f"color_regions[{index}].continuity is invalid")
            material = region.get("material")
            if material is not None and not isinstance(material, dict):
                errors.append(f"color_regions[{index}].material must be an object")
            elif isinstance(material, dict):
                transmission = material.get("transmission", "opaque")
                filament = material.get("filament")
                if transmission not in TRANSMISSION:
                    errors.append(
                        f"color_regions[{index}].material.transmission is invalid"
                    )
                if filament is not None and (
                    not isinstance(filament, str) or not filament.strip()
                ):
                    errors.append(
                        f"color_regions[{index}].material.filament must be a "
                        "non-empty string when present"
                    )
        if len(names) != len(set(names)):
            errors.append("color region names must be unique")

    visual = data.get("visual")
    if not isinstance(visual, dict) or visual.get("required") is not True:
        errors.append("visual.required must be true for color generation")
    elif not isinstance(visual.get("landmarks"), list) or not visual["landmarks"]:
        errors.append("visual.landmarks must be non-empty")
    elif visual.get("reference_view") not in VIEWS:
        errors.append("visual.reference_view is invalid")
    if not isinstance(data.get("palette_reduction"), dict):
        errors.append("palette_reduction decision is required")
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
        package_mode = printability.get("print_package_mode", "co_print_body")
        if package_mode not in PRINT_PACKAGE_MODES:
            errors.append("printability.print_package_mode is invalid")
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
        if not isinstance(critical, list) or not critical or not all(
            isinstance(item, str) and item.strip() for item in critical
        ):
            errors.append(
                "printability.critical_features must be a non-empty list of feature IDs"
            )
        elif len(critical) != len(set(critical)):
            errors.append("printability.critical_features must be unique")
        elif not set(critical).issubset(feature_ids):
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
        data, errors = {}, [str(error)]
    result = {
        "errors": errors,
        "intent": str(path.resolve()),
        "part": data.get("part"),
        "pass": not errors,
        "schema": "color-intent-validation/v3",
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
