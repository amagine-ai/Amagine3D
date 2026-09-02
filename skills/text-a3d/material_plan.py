"""Build and validate the one manufactured-material plan used by every backend."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Iterable


MATERIAL_PLAN_SCHEMA = "evidence-color-material-plan/v1"
PACKAGE_MODES = {"co_print_body", "separate_parts"}
FIELD_STATUSES = {"declared", "proposed"}
MATERIAL_STATUSES = {"declared", "proposed"}
TRANSMISSIONS = {"opaque", "translucent", "transparent"}
SCOPES = {"brep-region", "volumetric-region", "whole-part"}
SOURCE_KINDS = {
    "intent-color-region",
    "scene-part-appearance",
    "scene-part-material",
}
HEX_COLOR = re.compile(r"#[0-9A-F]{6}")
DEFAULT_SCENE_PALETTE = (
    "#5B8FF9",
    "#61DDAA",
    "#65789B",
    "#F6BD16",
    "#7262FD",
    "#78D3F8",
    "#9661BC",
    "#F6903D",
)


def material_record(
    material_id: str,
    color: str,
    *,
    filament: str | None,
    transmission: str | None,
    color_status: str,
    filament_status: str,
    transmission_status: str,
) -> dict[str, Any]:
    field_status = {
        "color": color_status,
        "filament": filament_status,
        "transmission": transmission_status,
    }
    record = {
        "color": color.upper(),
        "fieldStatus": field_status,
        "filament": filament,
        "id": material_id,
        "status": (
            "declared"
            if any(value == "declared" for value in field_status.values())
            else "proposed"
        ),
        "transmission": transmission,
    }
    errors = validate_material(record, "material")
    if errors:
        raise ValueError("; ".join(errors))
    return record


def source_binding(
    *,
    material: dict[str, Any],
    part: str,
    region: str | None,
    scope: str,
    source_id: str,
    source_kind: str,
) -> dict[str, Any]:
    return {
        "color": material["color"],
        "filament": material["filament"],
        "materialId": material["id"],
        "materialStatus": material["status"],
        "part": part,
        "region": region,
        "scope": scope,
        "sourceId": source_id,
        "sourceKind": source_kind,
        "transmission": material["transmission"],
    }


def build_material_plan(
    *,
    part: str,
    package_mode: str,
    materials: Iterable[dict[str, Any]],
    assignments: Iterable[dict[str, Any]],
    source_bindings: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    plan = {
        "archiveEncodes": ["part", "region", "rgb"],
        "archiveOmits": [
            "filament",
            "transmission",
            "slicer-filament-slot",
        ],
        "assignments": list(assignments),
        "coordinateFrame": "plate-print",
        "materials": list(materials),
        "packageMode": package_mode,
        "part": part,
        # The archive intentionally has no slicer slot mapping, irrespective
        # of whether a real filament name was declared in intent.
        "requiresManualSlicerAssignment": True,
        "scale": 1.0,
        "schema": MATERIAL_PLAN_SCHEMA,
        "sourceBindings": list(source_bindings),
    }
    errors = validate_material_plan(plan)
    if errors:
        raise ValueError("; ".join(errors))
    return plan


def validate_material(value: Any, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors: list[str] = []
    if set(value) != {
        "color",
        "fieldStatus",
        "filament",
        "id",
        "status",
        "transmission",
    }:
        errors.append(f"{path} has unsupported or missing fields")
    if not isinstance(value.get("id"), str) or not value["id"].strip():
        errors.append(f"{path}.id must be a non-empty string")
    if not isinstance(value.get("color"), str) or not HEX_COLOR.fullmatch(
        value["color"]
    ):
        errors.append(f"{path}.color must be uppercase #RRGGBB")
    filament = value.get("filament")
    if filament is not None and (
        not isinstance(filament, str) or not filament.strip()
    ):
        errors.append(f"{path}.filament must be null or a non-empty string")
    transmission = value.get("transmission")
    if transmission is not None and transmission not in TRANSMISSIONS:
        errors.append(f"{path}.transmission is invalid")
    if value.get("status") not in MATERIAL_STATUSES:
        errors.append(f"{path}.status must be declared or proposed")
    statuses = value.get("fieldStatus")
    if not isinstance(statuses, dict) or set(statuses) != {
        "color",
        "filament",
        "transmission",
    }:
        errors.append(f"{path}.fieldStatus must cover every material field")
    elif any(status not in FIELD_STATUSES for status in statuses.values()):
        errors.append(f"{path}.fieldStatus values must be declared or proposed")
    else:
        expected_status = (
            "declared"
            if any(status == "declared" for status in statuses.values())
            else "proposed"
        )
        if value.get("status") != expected_status:
            errors.append(f"{path}.status does not match its field statuses")
        if filament is None and statuses["filament"] != "proposed":
            errors.append(f"{path}.filament null must be proposed")
        if transmission is None and statuses["transmission"] != "proposed":
            errors.append(f"{path}.transmission null must be proposed")
    return errors


def _validate_assignment(value: Any, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors = []
    if set(value) != {"materialId", "part", "region", "scope"}:
        errors.append(f"{path} has unsupported or missing fields")
    for field in ("materialId", "part"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{path}.{field} must be a non-empty string")
    region = value.get("region")
    if region is not None and (not isinstance(region, str) or not region.strip()):
        errors.append(f"{path}.region must be null or a non-empty string")
    if value.get("scope") not in SCOPES:
        errors.append(f"{path}.scope must be one of {sorted(SCOPES)}")
    if value.get("scope") != "whole-part" and region is None:
        errors.append(f"{path}.region is required for an internal region")
    if value.get("scope") == "whole-part" and region is not None:
        errors.append(f"{path}.region must be null for a whole-part assignment")
    return errors


def _validate_binding(value: Any, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors = []
    if set(value) != {
        "color",
        "filament",
        "materialId",
        "materialStatus",
        "part",
        "region",
        "scope",
        "sourceId",
        "sourceKind",
        "transmission",
    }:
        errors.append(f"{path} has unsupported or missing fields")
    errors.extend(
        _validate_assignment(
            {
                "materialId": value.get("materialId"),
                "part": value.get("part"),
                "region": value.get("region"),
                "scope": value.get("scope"),
            },
            path,
        )
    )
    if not isinstance(value.get("color"), str) or not HEX_COLOR.fullmatch(
        value["color"]
    ):
        errors.append(f"{path}.color must be uppercase #RRGGBB")
    if value.get("materialStatus") not in MATERIAL_STATUSES:
        errors.append(f"{path}.materialStatus is invalid")
    source_kind = value.get("sourceKind")
    source_id = value.get("sourceId")
    if source_kind not in SOURCE_KINDS:
        errors.append(f"{path}.sourceKind must be one of {sorted(SOURCE_KINDS)}")
    if not isinstance(source_id, str) or not source_id.strip():
        errors.append(f"{path}.sourceId must be a non-empty string")
    if source_kind == "intent-color-region" and value.get("materialStatus") != "declared":
        errors.append(f"{path} intent color sources must be declared")
    if source_kind in {"scene-part-appearance", "scene-part-material"}:
        if value.get("materialStatus") != "proposed":
            errors.append(f"{path} scene sources must be proposed")
        if value.get("scope") != "whole-part" or value.get("region") is not None:
            errors.append(f"{path} scene sources must bind one whole part")
    if value.get("filament") is not None and not isinstance(
        value.get("filament"), str
    ):
        errors.append(f"{path}.filament must be null or a string")
    if value.get("transmission") is not None and value.get(
        "transmission"
    ) not in TRANSMISSIONS:
        errors.append(f"{path}.transmission is invalid")
    return errors


def validate_material_plan(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["material plan must be an object"]
    errors: list[str] = []
    expected_fields = {
        "archiveEncodes",
        "archiveOmits",
        "assignments",
        "coordinateFrame",
        "materials",
        "packageMode",
        "part",
        "requiresManualSlicerAssignment",
        "scale",
        "schema",
        "sourceBindings",
    }
    if set(value) != expected_fields:
        errors.append("material plan has unsupported or missing fields")
    if value.get("schema") != MATERIAL_PLAN_SCHEMA:
        errors.append(f"schema must be {MATERIAL_PLAN_SCHEMA}")
    if value.get("packageMode") not in PACKAGE_MODES:
        errors.append(f"packageMode must be one of {sorted(PACKAGE_MODES)}")
    if value.get("coordinateFrame") != "plate-print":
        errors.append("coordinateFrame must be plate-print")
    if value.get("scale") != 1.0:
        errors.append("scale must be 1.0")
    if value.get("requiresManualSlicerAssignment") is not True:
        errors.append("requiresManualSlicerAssignment must be true")
    if value.get("archiveEncodes") != ["part", "region", "rgb"]:
        errors.append("archiveEncodes must be part, region, rgb")
    if value.get("archiveOmits") != [
        "filament",
        "transmission",
        "slicer-filament-slot",
    ]:
        errors.append("archiveOmits must declare every omitted material field")
    if not isinstance(value.get("part"), str) or not value["part"].strip():
        errors.append("part must be a non-empty string")

    materials = value.get("materials")
    material_ids: set[str] = set()
    if not isinstance(materials, list) or not materials:
        errors.append("materials must be a non-empty list")
    else:
        for index, material in enumerate(materials):
            errors.extend(validate_material(material, f"materials[{index}]"))
            if isinstance(material, dict) and isinstance(material.get("id"), str):
                if material["id"] in material_ids:
                    errors.append("material IDs must be unique")
                material_ids.add(material["id"])

    assignments = value.get("assignments")
    assignment_keys: set[tuple[Any, Any, Any]] = set()
    if not isinstance(assignments, list) or not assignments:
        errors.append("assignments must be a non-empty list")
    else:
        for index, assignment in enumerate(assignments):
            errors.extend(_validate_assignment(assignment, f"assignments[{index}]"))
            if (
                isinstance(assignment, dict)
                and assignment.get("materialId") not in material_ids
            ):
                errors.append(
                    f"assignments[{index}].materialId references an unknown material"
                )
            if isinstance(assignment, dict):
                key = (
                    assignment.get("part"),
                    assignment.get("region"),
                    assignment.get("scope"),
                )
                if key in assignment_keys:
                    errors.append("material assignments must have unique targets")
                assignment_keys.add(key)

    bindings = value.get("sourceBindings")
    if not isinstance(bindings, list) or not bindings:
        errors.append("sourceBindings must be a non-empty list")
    else:
        material_by_id = {
            item.get("id"): item
            for item in materials
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        } if isinstance(materials, list) else {}
        binding_keys: set[tuple[Any, Any, Any, Any]] = set()
        intent_source_ids: set[Any] = set()
        for index, binding in enumerate(bindings):
            path = f"sourceBindings[{index}]"
            errors.extend(_validate_binding(binding, path))
            if (
                isinstance(binding, dict)
                and binding.get("materialId") not in material_ids
            ):
                errors.append(
                    f"{path}.materialId references an unknown material"
                )
            if isinstance(binding, dict):
                material = material_by_id.get(binding.get("materialId"))
                if isinstance(material, dict):
                    expected = {
                        "color": material.get("color"),
                        "filament": material.get("filament"),
                        "materialStatus": material.get("status"),
                        "transmission": material.get("transmission"),
                    }
                    observed = {field: binding.get(field) for field in expected}
                    if observed != expected:
                        errors.append(
                            f"{path} does not match its material"
                        )
                binding_key = (
                    binding.get("materialId"),
                    binding.get("part"),
                    binding.get("region"),
                    binding.get("scope"),
                )
                if binding_key in binding_keys:
                    errors.append("source bindings must have unique assignment targets")
                binding_keys.add(binding_key)
                if binding.get("sourceKind") == "intent-color-region":
                    source_id = binding.get("sourceId")
                    if source_id in intent_source_ids:
                        errors.append("intent color regions cannot bind more than one assignment")
                    intent_source_ids.add(source_id)
        assignment_binding_keys = {
            (
                assignment.get("materialId"),
                assignment.get("part"),
                assignment.get("region"),
                assignment.get("scope"),
            )
            for assignment in assignments
            if isinstance(assignment, dict)
        } if isinstance(assignments, list) else set()
        if binding_keys != assignment_binding_keys:
            errors.append(
                "sourceBindings must cover every assignment exactly once with no unknown target"
            )
    return errors


def _scene_part_color(part: dict[str, Any]) -> str:
    """Resolve the deterministic color used by the Hybrid scene compiler."""

    appearance = part.get("appearance")
    appearance = appearance if isinstance(appearance, dict) else {}
    candidate = (
        part.get("color")
        if "color" in part
        else appearance.get("baseColor", appearance.get("color"))
    )
    if isinstance(candidate, str) and re.fullmatch(
        r"#[0-9A-Fa-f]{6}", candidate
    ):
        return candidate.upper()
    part_id = part.get("id")
    payload = json.dumps(
        part_id,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    index = int(sha256(payload).hexdigest()[:2], 16) % len(DEFAULT_SCENE_PALETTE)
    return DEFAULT_SCENE_PALETTE[index]


def validate_material_sources(
    value: Any,
    intent: Any,
    scene: Any,
) -> list[str]:
    """Verify every assignment against its immutable intent or bound scene source."""

    errors: list[str] = []
    structural_errors = validate_material_plan(value)
    if structural_errors:
        return [f"material plan is invalid: {error}" for error in structural_errors]
    if not isinstance(intent, dict):
        return ["hash-bound intent is unavailable for material source validation"]
    if not isinstance(scene, dict):
        return ["hash-bound scene is unavailable for material source validation"]

    materials = {
        material["id"]: material
        for material in value["materials"]
        if isinstance(material, dict) and isinstance(material.get("id"), str)
    }
    raw_regions = intent.get("color_regions", [])
    if not isinstance(raw_regions, list):
        return ["hash-bound intent color_regions must be a list when present"]
    intent_regions: dict[str, dict[str, Any]] = {}
    regions_by_part: dict[str, list[dict[str, Any]]] = {}
    for index, region in enumerate(raw_regions):
        if not isinstance(region, dict) or not isinstance(region.get("name"), str):
            errors.append(f"intent.color_regions[{index}] is invalid")
            continue
        region_id = region["name"]
        if region_id in intent_regions:
            errors.append(f"intent color region {region_id!r} is duplicated")
            continue
        intent_regions[region_id] = region
        owner = region.get("part")
        if isinstance(owner, str):
            regions_by_part.setdefault(owner, []).append(region)

    raw_scene_parts = scene.get("parts")
    raw_scene_materials = scene.get("materials", [])
    if not isinstance(raw_scene_parts, list):
        return errors + ["hash-bound scene parts must be a list"]
    if not isinstance(raw_scene_materials, list):
        return errors + ["hash-bound scene materials must be a list"]
    scene_parts: dict[str, dict[str, Any]] = {}
    for index, part in enumerate(raw_scene_parts):
        if not isinstance(part, dict) or not isinstance(part.get("id"), str):
            errors.append(f"scene.parts[{index}] is invalid")
            continue
        if part["id"] in scene_parts:
            errors.append(f"scene part {part['id']!r} is duplicated")
            continue
        scene_parts[part["id"]] = part
    scene_materials: dict[str, dict[str, Any]] = {}
    for index, material in enumerate(raw_scene_materials):
        if not isinstance(material, dict) or not isinstance(material.get("id"), str):
            errors.append(f"scene.materials[{index}] is invalid")
            continue
        if material["id"] in scene_materials:
            errors.append(f"scene material {material['id']!r} is duplicated")
            continue
        scene_materials[material["id"]] = material

    seen_intent_sources: set[str] = set()
    seen_scene_parts: set[str] = set()
    for index, binding in enumerate(value["sourceBindings"]):
        path = f"sourceBindings[{index}]"
        material = materials.get(binding["materialId"])
        if not isinstance(material, dict):
            continue
        statuses = material.get("fieldStatus")
        statuses = statuses if isinstance(statuses, dict) else {}
        source_kind = binding["sourceKind"]
        source_id = binding["sourceId"]
        part_id = binding["part"]

        if source_kind == "intent-color-region":
            region = intent_regions.get(source_id)
            if region is None:
                errors.append(f"{path}.sourceId references an unknown intent color region")
                continue
            seen_intent_sources.add(source_id)
            if region.get("part") != part_id:
                errors.append(f"{path}.part does not match its intent color region owner")
            if binding["scope"] == "whole-part":
                if binding["region"] is not None:
                    errors.append(f"{path}.region must be null for a whole-part source")
                if len(regions_by_part.get(part_id, [])) != 1:
                    errors.append(
                        f"{path} cannot bind multiple intent regions as one whole part"
                    )
            elif binding["region"] != source_id:
                errors.append(f"{path}.region must equal its intent color region ID")
            expected_color = region.get("hex")
            if (
                not isinstance(expected_color, str)
                or binding["color"] != expected_color.upper()
                or material.get("color") != expected_color.upper()
                or statuses.get("color") != "declared"
            ):
                errors.append(f"{path}.color does not match its intent color region")
            declared_material = region.get("material")
            declared_material = (
                declared_material if isinstance(declared_material, dict) else {}
            )
            for field in ("filament", "transmission"):
                if field in declared_material:
                    if (
                        binding.get(field) != declared_material[field]
                        or material.get(field) != declared_material[field]
                        or statuses.get(field) != "declared"
                    ):
                        errors.append(
                            f"{path}.{field} does not match its intent color region"
                        )
                elif statuses.get(field) != "proposed":
                    errors.append(
                        f"{path}.{field} must remain proposed when intent omits it"
                    )
            continue

        scene_part = scene_parts.get(part_id)
        if scene_part is None:
            errors.append(f"{path}.part references an unknown scene part")
            continue
        if part_id in regions_by_part:
            errors.append(
                f"{path} cannot use a scene material when intent declares that part's color"
            )
        if part_id in seen_scene_parts:
            errors.append(f"scene part {part_id!r} has more than one material source")
        seen_scene_parts.add(part_id)
        explicit_id = scene_part.get("materialId")
        if isinstance(explicit_id, str):
            if (
                source_kind != "scene-part-material"
                or source_id != explicit_id
                or binding["materialId"] != explicit_id
            ):
                errors.append(f"{path} does not identify the scene part material")
                continue
            scene_material = scene_materials.get(explicit_id)
            if scene_material is None:
                errors.append(f"{path}.sourceId references an unknown scene material")
                continue
            expected_fields = {
                "color": str(scene_material.get("color", "")).upper(),
                "filament": scene_material.get("filament"),
                "transmission": scene_material.get("transmission"),
            }
        else:
            if (
                source_kind != "scene-part-appearance"
                or source_id != part_id
                or binding["materialId"] != f"proposed-{part_id}"
            ):
                errors.append(f"{path} does not identify the scene part appearance")
                continue
            expected_fields = {
                "color": _scene_part_color(scene_part),
                "filament": None,
                "transmission": None,
            }
        if any(
            binding.get(field) != expected
            or material.get(field) != expected
            for field, expected in expected_fields.items()
        ):
            errors.append(f"{path} does not match its hash-bound scene material")
        if any(statuses.get(field) != "proposed" for field in expected_fields):
            errors.append(f"{path} scene material fields must all be proposed")

    missing_regions = sorted(set(intent_regions) - seen_intent_sources)
    if missing_regions:
        errors.append(
            "intent color regions lack source bindings: " + ", ".join(missing_regions)
        )
    return errors
