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
PART_INSTALLATIONS = {"adhesive", "interface", "loose"}
INTERFACE_CONNECTIONS = {
    "collar-socket",
    "dovetail",
    "glue-face",
    "hinge-pin",
    "inset-pocket",
    "peg-socket",
    "pin-socket",
    "press-fit",
    "retained-slider",
    "self-tapping-screw",
    "snap-fit",
    "tab-slot",
    "threaded-insert",
}
ASSEMBLY_AXES = {"+X", "+Y", "+Z", "-X", "-Y", "-Z"}
INTENT_SCHEMA = "evidence-cad-intent/v4"
ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")
FEATURE_ID_PATTERN = re.compile(
    r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*"
    r"(?:/[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*)*"
)
HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")
MATERIAL_TRANSMISSIONS = {"opaque", "translucent", "transparent"}
REGION_CONTINUITY = {
    "continuous-core",
    "not-applicable",
    "separate-part",
    "surface-detail",
}
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


def _validate_self_tapping_fastening(
    interface: dict,
    index: int,
) -> list[str]:
    """Validate intent-side targets for a located plastic screw joint."""

    prefix = f"manufacturing.interfaces[{index}].fastening"
    fastening = interface.get("fastening")
    if not isinstance(fastening, dict):
        return [f"{prefix} is required for connection self-tapping-screw"]
    errors: list[str] = []
    screw_family = fastening.get("screw_family")
    if not isinstance(screw_family, str) or not screw_family.strip():
        errors.append(f"{prefix}.screw_family is required")
    nominal = fastening.get("nominal_diameter_mm")
    pilot = fastening.get("pilot_diameter_mm")
    clearance = fastening.get("clearance_diameter_mm")
    boss_outer = fastening.get("boss_outer_diameter_mm")
    closed_end = fastening.get("closed_end_mm")
    for key, value in (
        ("nominal_diameter_mm", nominal),
        ("pilot_diameter_mm", pilot),
        ("clearance_diameter_mm", clearance),
        ("boss_outer_diameter_mm", boss_outer),
        ("closed_end_mm", closed_end),
    ):
        if not _positive_number(value):
            errors.append(f"{prefix}.{key} must be positive")
    if all(_positive_number(value) for value in (pilot, nominal, clearance)):
        if not float(pilot) < float(nominal) < float(clearance):
            errors.append(
                f"{prefix} diameters must satisfy pilot < nominal < clearance"
            )
    if _positive_number(boss_outer) and _positive_number(pilot):
        if float(boss_outer) <= float(pilot):
            errors.append(
                f"{prefix}.boss_outer_diameter_mm must exceed pilot_diameter_mm"
            )
    head_diameter = fastening.get("head_recess_diameter_mm")
    head_depth = fastening.get("head_recess_depth_mm")
    minimum_land = fastening.get("minimum_cover_land_mm")
    if (head_diameter is None) != (head_depth is None):
        errors.append(
            f"{prefix} head recess diameter and depth must be declared together"
        )
    elif head_diameter is not None:
        if not _positive_number(head_diameter) or (
            _positive_number(clearance)
            and float(head_diameter) <= float(clearance)
        ):
            errors.append(
                f"{prefix}.head_recess_diameter_mm must exceed clearance_diameter_mm"
            )
        if not _positive_number(head_depth):
            errors.append(f"{prefix}.head_recess_depth_mm must be positive")
        if not _positive_number(minimum_land):
            errors.append(f"{prefix}.minimum_cover_land_mm must be positive")
    elif minimum_land is not None:
        errors.append(
            f"{prefix}.minimum_cover_land_mm requires a head recess"
        )

    interface_features = {
        item
        for item in interface.get("features", [])
        if isinstance(item, str)
    }
    locator_pairs = fastening.get("locator_pairs")
    locator_ids: list[str] = []
    locator_features: list[str] = []
    if not isinstance(locator_pairs, list) or not locator_pairs:
        errors.append(f"{prefix}.locator_pairs must declare at least one locating pair")
    else:
        for locator_index, locator in enumerate(locator_pairs):
            locator_prefix = f"{prefix}.locator_pairs[{locator_index}]"
            if not isinstance(locator, dict):
                errors.append(f"{locator_prefix} must be an object")
                continue
            locator_id = locator.get("id")
            if not isinstance(locator_id, str) or not ID_PATTERN.fullmatch(locator_id):
                errors.append(f"{locator_prefix}.id is invalid")
            else:
                locator_ids.append(locator_id)
            pair_features: list[str] = []
            for key in ("male_feature", "female_feature"):
                feature_id = locator.get(key)
                if not isinstance(feature_id, str) or not FEATURE_ID_PATTERN.fullmatch(feature_id):
                    errors.append(f"{locator_prefix}.{key} is invalid")
                else:
                    pair_features.append(feature_id)
                    locator_features.append(feature_id)
            if len(pair_features) == 2 and pair_features[0] == pair_features[1]:
                errors.append(
                    f"{locator_prefix} must name distinct male and female features"
                )
        if len(locator_ids) != len(set(locator_ids)):
            errors.append(f"{prefix}.locator_pairs ids must be unique")
        if len(locator_features) != len(set(locator_features)):
            errors.append(f"{prefix}.locator_pairs cannot reuse locating features")
        if not set(locator_features).issubset(interface_features):
            errors.append(
                f"{prefix}.locator_pairs features must be included in interface features"
            )

    fasteners = fastening.get("fasteners")
    if not isinstance(fasteners, list) or not fasteners:
        errors.append(f"{prefix}.fasteners must declare at least one screw axis")
        return errors

    fastener_ids: list[str] = []
    mapped_features: list[str] = []
    required_feature_keys = (
        "clearance_feature",
        "pilot_feature",
        "boss_feature",
    )
    for fastener_index, fastener in enumerate(fasteners):
        fastener_prefix = f"{prefix}.fasteners[{fastener_index}]"
        if not isinstance(fastener, dict):
            errors.append(f"{fastener_prefix} must be an object")
            continue
        fastener_id = fastener.get("id")
        if not isinstance(fastener_id, str) or not ID_PATTERN.fullmatch(fastener_id):
            errors.append(f"{fastener_prefix}.id is invalid")
        else:
            fastener_ids.append(fastener_id)
        local_features: list[str] = []
        for key in required_feature_keys:
            feature_id = fastener.get(key)
            if not isinstance(feature_id, str) or not FEATURE_ID_PATTERN.fullmatch(feature_id):
                errors.append(f"{fastener_prefix}.{key} is invalid")
            else:
                local_features.append(feature_id)
                mapped_features.append(feature_id)
        if "head_recess_feature" in fastener:
            errors.append(
                f"{fastener_prefix}.head_recess_feature is invalid; the head recess "
                "is part of the shared clearance cutter"
            )
        if len(local_features) != len(set(local_features)):
            errors.append(f"{fastener_prefix} feature IDs must be distinct")
        if not set(local_features).issubset(interface_features):
            errors.append(
                f"{fastener_prefix} features must be included in interface features"
            )
    if len(fastener_ids) != len(set(fastener_ids)):
        errors.append(f"{prefix}.fasteners ids must be unique")
    if len(mapped_features) != len(set(mapped_features)):
        errors.append(
            f"{prefix}.fasteners cannot reuse a hole or boss feature across axes"
        )
    shared_features = sorted(set(locator_features).intersection(mapped_features))
    if shared_features:
        errors.append(
            f"{prefix} locator and screw features must be independent: "
            + ", ".join(shared_features)
        )
    return errors


def validate_manufacturing(
    manufacturing,
    feature_ids: set[str] | None = None,
    feature_owners: dict[str, str] | None = None,
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
        interface_exempt_parts: set[str] = set()
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
                    installation = part.get("installation", "interface")
                    if installation not in PART_INSTALLATIONS:
                        errors.append(
                            f"manufacturing.parts[{index}].installation must be "
                            "interface, adhesive, or loose"
                        )
                    elif installation != "interface":
                        interface_exempt_parts.add(part_name)
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
            connected_parts: set[str] = set()
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
                else:
                    connected_parts.update(between)
                connection = interface.get("connection")
                if connection not in INTERFACE_CONNECTIONS:
                    errors.append(
                        f"manufacturing.interfaces[{index}].connection is invalid"
                    )
                if connection == "self-tapping-screw":
                    errors.extend(_validate_self_tapping_fastening(interface, index))
                elif "fastening" in interface:
                    errors.append(
                        f"manufacturing.interfaces[{index}].fastening is only valid "
                        "for connection self-tapping-screw"
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
                    isinstance(item, str) and FEATURE_ID_PATTERN.fullmatch(item)
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
                elif (
                    feature_owners is not None
                    and isinstance(between, list)
                    and len(between) == 2
                    and all(isinstance(item, str) for item in between)
                ):
                    wrong_owner = sorted(
                        feature_id
                        for feature_id in interface_features
                        if feature_owners.get(feature_id) not in set(between)
                    )
                    if wrong_owner:
                        errors.append(
                            "manufacturing.interfaces"
                            f"[{index}].features must be owned by a part named in "
                            f"between: {', '.join(wrong_owner)}"
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
            unconnected = sorted(
                part_names - connected_parts - interface_exempt_parts
            )
            if unconnected:
                errors.append(
                    "manufacturing.parts require a declared interface or explicit "
                    "adhesive/loose installation: " + ", ".join(unconnected)
                )
    return errors


def validate_color_regions(
    color_regions,
    manufacturing,
    intent_part: str | None = None,
) -> list[str]:
    """Validate globally named material regions owned by physical parts."""
    if color_regions is None:
        return []
    errors: list[str] = []
    if not isinstance(manufacturing, dict) or manufacturing.get("mode") not in MANUFACTURING_MODES:
        errors.append("color_regions require a valid manufacturing.mode")
        return errors
    if not isinstance(color_regions, list) or len(color_regions) < 2:
        return ["color_regions must contain at least two regions when declared"]

    names: list[str] = []
    owners: list[str] = []
    declared_parts = {
        item.get("name")
        for item in manufacturing.get("parts", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if manufacturing.get("mode") == "single-part" and isinstance(intent_part, str):
        declared_parts = {intent_part}
    for index, region in enumerate(color_regions):
        prefix = f"color_regions[{index}]"
        if not isinstance(region, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = region.get("name")
        if not isinstance(name, str) or not ID_PATTERN.fullmatch(name):
            errors.append(f"{prefix}.name is invalid")
        else:
            names.append(name)
        owner = region.get("part")
        if not isinstance(owner, str) or not ID_PATTERN.fullmatch(owner):
            errors.append(f"{prefix}.part is required")
        elif owner not in declared_parts:
            errors.append(f"{prefix}.part must reference the owning physical part")
        else:
            owners.append(owner)
        color = region.get("hex")
        if not isinstance(color, str) or not HEX_COLOR_PATTERN.fullmatch(color):
            errors.append(f"{prefix}.hex must be #RRGGBB")
        for key in ("purpose", "boundary", "evidence"):
            value = region.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.{key} is required")
        acceptance = region.get("acceptance")
        if acceptance is not None and (
            not isinstance(acceptance, str) or not acceptance.strip()
        ):
            errors.append(f"{prefix}.acceptance must be a non-empty string when present")
        continuity = region.get("continuity")
        if continuity is not None and continuity not in REGION_CONTINUITY:
            errors.append(f"{prefix}.continuity is invalid")
        material = region.get("material")
        if material is not None and not isinstance(material, dict):
            errors.append(f"{prefix}.material must be an object")
        elif isinstance(material, dict):
            transmission = material.get("transmission", "opaque")
            if transmission not in MATERIAL_TRANSMISSIONS:
                errors.append(f"{prefix}.material.transmission is invalid")
            filament = material.get("filament")
            if filament is not None and (
                not isinstance(filament, str) or not filament.strip()
            ):
                errors.append(
                    f"{prefix}.material.filament must be a non-empty string"
                )

    if len(names) != len(set(names)):
        errors.append("color region names must be unique")
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


def validate_feature_ownership(
    features,
    manufacturing,
    intent_part: str | None,
) -> tuple[list[str], dict[str, str]]:
    """Make feature ownership explicit for multipart intent v4 contracts."""

    errors: list[str] = []
    owners: dict[str, str] = {}
    if not isinstance(features, list) or not isinstance(manufacturing, dict):
        return errors, owners
    mode = manufacturing.get("mode")
    part_names = {
        item.get("name")
        for item in manufacturing.get("parts", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        feature_id = feature.get("id")
        owner = feature.get("part")
        if mode == "multipart":
            if not isinstance(owner, str) or not ID_PATTERN.fullmatch(owner):
                errors.append(f"features[{index}].part is required for multipart")
                continue
            if part_names and owner not in part_names:
                errors.append(
                    f"features[{index}].part must reference manufacturing.parts"
                )
                continue
        elif mode == "single-part":
            if owner is None:
                owner = intent_part
            elif owner != intent_part:
                errors.append(
                    f"features[{index}].part must equal the top-level intent part"
                )
                continue
        if (
            isinstance(feature_id, str)
            and feature_id.strip()
            and isinstance(owner, str)
            and owner.strip()
        ):
            owners[feature_id] = owner
    return errors, owners


def physical_part_names(data: dict) -> set[str]:
    """Return the physical part identity set of a validated v4 intent."""
    manufacturing = data.get("manufacturing")
    if not isinstance(manufacturing, dict):
        return set()
    if manufacturing.get("mode") == "single-part":
        part = data.get("part")
        return {part} if isinstance(part, str) and part else set()
    if manufacturing.get("mode") == "multipart":
        return {
            item["name"]
            for item in manufacturing.get("parts", [])
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and item["name"]
        }
    return set()


def feature_owner_map(data: dict) -> dict[str, str]:
    """Return feature-to-physical-part ownership for a validated v4 intent."""
    parts = physical_part_names(data)
    single_owner = next(iter(parts)) if len(parts) == 1 else None
    owners: dict[str, str] = {}
    for feature in data.get("features", []):
        if not isinstance(feature, dict) or not isinstance(feature.get("id"), str):
            continue
        owner = feature.get("part")
        if owner is None:
            owner = single_owner
        if isinstance(owner, str):
            owners[feature["id"]] = owner
    return owners


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
                if not FEATURE_ID_PATTERN.fullmatch(feature_id):
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

    manufacturing = data.get("manufacturing")
    ownership_errors, feature_owners = validate_feature_ownership(
        features,
        manufacturing,
        data.get("part") if isinstance(data.get("part"), str) else None,
    )
    errors.extend(ownership_errors)
    errors.extend(
        validate_manufacturing(
            manufacturing,
            feature_ids,
            feature_owners,
        )
    )
    errors.extend(
        validate_color_regions(
            data.get("color_regions"),
            manufacturing,
            data.get("part") if isinstance(data.get("part"), str) else None,
        )
    )
    if data.get("color_regions") is not None and not isinstance(
        data.get("palette_reduction"), dict
    ):
        errors.append("palette_reduction decision is required when color_regions are declared")

    visual = data.get("visual")
    if not isinstance(visual, dict) or visual.get("required") is not True:
        errors.append("visual.required must be true for CAD work")
    else:
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
        if data.get("color_regions") is not None:
            manufacturing_mode = (
                manufacturing.get("mode") if isinstance(manufacturing, dict) else None
            )
            package_mode = printability.get("print_package_mode")
            expected_package_mode = (
                "co_print_body"
                if manufacturing_mode == "single-part"
                else "separate_parts"
            )
            if package_mode != expected_package_mode:
                errors.append(
                    f"{manufacturing_mode} colors require "
                    f"printability.print_package_mode {expected_package_mode}"
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
