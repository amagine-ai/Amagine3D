"""Deterministic authoring helpers for the canonical intent and scene contracts.

This module deliberately has no intermediate document format.  It writes the
same evidence-cad-intent/v4 and evidence-semantic-scene/v1 JSON documents that
the validators and compilers consume.  The compact inputs only remove fields
whose values are mechanically implied by an explicit semantic decision.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from intent_contract import (
    COORDINATE_SYSTEM,
    INTENT_SCHEMA,
    feature_owner_map,
    physical_part_names,
    validate as validate_intent,
)
from scene_contract import (
    ROLE_OPERATIONS,
    SCENE_SCHEMA,
    validate as validate_scene,
)


SCENE_COORDINATE_SYSTEM = {"handedness": "right", "up": "Z"}


class AuthoringError(ValueError):
    """Raised before an invalid or semantically ambiguous document is written."""

    def __init__(self, stage: str, errors: Sequence[str]):
        self.stage = stage
        self.errors = tuple(str(error) for error in errors)
        super().__init__(f"{stage} authoring failed: " + "; ".join(self.errors))


def _records(value: Sequence[Mapping[str, Any]], path: str) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AuthoringError(path, ["must be a sequence of objects"])
    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise AuthoringError(path, [f"{path}[{index}] must be an object"])
        records.append(deepcopy(dict(item)))
    return records


def _digest(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise AuthoringError("file binding", [f"cannot read {path}: {error}"]) from error


def _stored_path(path: Path, base_dir: Path) -> str:
    """Prefer a relocatable path while retaining access to an external input."""

    try:
        return os.path.relpath(path, base_dir)
    except ValueError:
        return str(path)


def _resolve_input_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _profile_wall_target(path: Path) -> float:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
        value = profile["derived"]["process_wall_target_mm"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise AuthoringError(
            "profile",
            [f"cannot read derived.process_wall_target_mm from {path}: {error}"],
        ) from error
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not float(value) > 0
    ):
        raise AuthoringError(
            "profile",
            [f"derived.process_wall_target_mm must be positive in {path}"],
        )
    return float(value)


def _write_json(
    path: Path,
    document: Mapping[str, Any],
    *,
    immutable: bool = False,
) -> None:
    if immutable and path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AuthoringError(
                "immutable intent",
                [f"cannot verify existing {path}: {error}"],
            ) from error
        if existing != document:
            raise AuthoringError(
                "immutable intent",
                [
                    f"{path} already exists with different content; start a new "
                    "intent file for a changed target"
                ],
            )
        return

    temporary: Path | None = None
    try:
        payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise AuthoringError("write", [f"cannot write {path}: {error}"]) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _expand_intent_parts(
    *,
    top_level_part: str,
    manufacturing_mode: str,
    parts: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(parts, Mapping) or not parts:
        raise AuthoringError("intent", ["parts must map explicit part names to definitions"])

    features: list[dict[str, Any]] = []
    manufacturing_parts: list[dict[str, Any]] = []
    for part_name, raw_part in parts.items():
        if not isinstance(part_name, str) or not part_name:
            raise AuthoringError("intent", ["parts keys must be non-empty strings"])
        if not isinstance(raw_part, Mapping):
            raise AuthoringError("intent", [f"parts[{part_name!r}] must be an object"])
        part_record = deepcopy(dict(raw_part))
        nested_features = _records(
            part_record.pop("features", ()), f"parts[{part_name!r}].features"
        )
        declared_name = part_record.pop("name", part_name)
        if declared_name != part_name:
            raise AuthoringError(
                "intent",
                [f"parts[{part_name!r}].name conflicts with its nested owner"],
            )
        for feature in nested_features:
            declared_owner = feature.get("part", part_name)
            if declared_owner != part_name:
                raise AuthoringError(
                    "intent",
                    [
                        f"feature {feature.get('id')!r} declares owner "
                        f"{declared_owner!r} inside part {part_name!r}"
                    ],
                )
            feature["part"] = part_name
            features.append(feature)

        if manufacturing_mode == "multipart":
            manufacturing_parts.append({"name": part_name, **part_record})
        elif part_record:
            raise AuthoringError(
                "intent",
                [
                    f"single-part definition {part_name!r} only accepts nested "
                    f"features; unsupported fields: {sorted(part_record)}"
                ],
            )

    if manufacturing_mode == "single-part":
        if list(parts) != [top_level_part]:
            raise AuthoringError(
                "intent",
                [
                    "single-part authoring requires exactly one nested part whose "
                    "name equals the top-level part"
                ],
            )
        manufacturing = {"mode": "single-part"}
    elif manufacturing_mode == "multipart":
        manufacturing = {"mode": "multipart", "parts": manufacturing_parts}
    else:
        raise AuthoringError(
            "intent",
            ["manufacturing_mode must be explicitly single-part or multipart"],
        )
    return features, manufacturing


def _expand_intent_interfaces(
    raw_interfaces: Sequence[Mapping[str, Any]],
    owners: Mapping[str, str],
) -> list[dict[str, Any]]:
    interfaces = _records(raw_interfaces, "interfaces")
    expanded: list[dict[str, Any]] = []
    for index, interface in enumerate(interfaces):
        feature_ids = interface.get("features")
        if not isinstance(feature_ids, list) or not feature_ids:
            raise AuthoringError(
                "intent",
                [f"interfaces[{index}].features must explicitly name connector features"],
            )
        endpoint_parts: list[str] = []
        for feature_id in feature_ids:
            owner = owners.get(feature_id) if isinstance(feature_id, str) else None
            if owner is None:
                raise AuthoringError(
                    "intent",
                    [
                        f"interfaces[{index}] references feature {feature_id!r} "
                        "without an explicit nested part owner"
                    ],
                )
            if owner not in endpoint_parts:
                endpoint_parts.append(owner)
        if len(endpoint_parts) != 2:
            raise AuthoringError(
                "intent",
                [
                    f"interfaces[{index}].features must resolve to exactly two "
                    f"physical parts, observed {endpoint_parts}"
                ],
            )
        declared_between = interface.pop("between", endpoint_parts)
        if not (
            isinstance(declared_between, list)
            and len(declared_between) == 2
            and set(declared_between) == set(endpoint_parts)
        ):
            raise AuthoringError(
                "intent",
                [f"interfaces[{index}].between conflicts with feature ownership"],
            )
        interface["between"] = endpoint_parts
        expanded.append(interface)
    return expanded


def write_intent(
    path: str | Path,
    *,
    profile_path: str | Path,
    part: str,
    task_mode: str,
    representation: str,
    dimensions_mm: Mapping[str, Any],
    manufacturing_mode: str,
    parts: Mapping[str, Mapping[str, Any]],
    support_policy: str,
    minimum_wall_target_mm: float | None = None,
    critical_features: Sequence[str],
    reference_view: str,
    landmarks: Sequence[str],
    assumptions: Sequence[str],
    interfaces: Sequence[Mapping[str, Any]] = (),
    reference_files: Sequence[Mapping[str, Any]] = (),
    color_regions: Sequence[Mapping[str, Any]] | None = None,
    palette_reduction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one validated canonical intent from explicit semantic decisions.

    `parts` owns its features by nesting.  For multipart designs, interface
    `between` values are derived from the explicitly named feature IDs.  This
    function does not choose a manufacturing mode, part, feature, fit, color,
    visual landmark, acceptance criterion, or printability policy.
    """

    if os.environ.get("AMAGINE3D_SOURCE_PHASE") == "compile":
        raise AuthoringError(
            "immutable intent",
            [
                "write_intent is forbidden inside a cad_compile build source; "
                "create and validate intent in a separate contract-only authoring step"
            ],
        )

    destination = Path(path).resolve()
    profile = _resolve_input_path(profile_path)
    features, manufacturing = _expand_intent_parts(
        top_level_part=part,
        manufacturing_mode=manufacturing_mode,
        parts=parts,
    )
    owners = {
        feature.get("id"): feature.get("part")
        for feature in features
        if isinstance(feature.get("id"), str) and isinstance(feature.get("part"), str)
    }
    expanded_interfaces = _expand_intent_interfaces(interfaces, owners)
    if manufacturing_mode == "single-part" and expanded_interfaces:
        raise AuthoringError("intent", ["single-part intent cannot declare interfaces"])
    if manufacturing_mode == "multipart":
        manufacturing["interfaces"] = expanded_interfaces

    printability: dict[str, Any] = {
        "profile": {
            "path": _stored_path(profile, destination.parent),
            "sha256": _digest(profile),
        },
        "build_axis": "+Z",
        "bed_contact": "z-min",
        "support_policy": support_policy,
        "minimum_wall_target_mm": (
            _profile_wall_target(profile)
            if minimum_wall_target_mm is None
            else minimum_wall_target_mm
        ),
        "critical_features": list(critical_features),
    }
    document: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "part": part,
        "task_mode": task_mode,
        "representation": representation,
        "reference_files": _records(reference_files, "reference_files"),
        "coordinate_system": deepcopy(COORDINATE_SYSTEM),
        "dimensions_mm": deepcopy(dict(dimensions_mm)),
        "features": features,
        "manufacturing": manufacturing,
        "printability": printability,
        "visual": {
            "required": True,
            "reference_view": reference_view,
            "landmarks": list(landmarks),
        },
        "assumptions": list(assumptions),
    }
    if color_regions is not None:
        if palette_reduction is None:
            raise AuthoringError(
                "intent",
                ["palette_reduction is an explicit decision required with color_regions"],
            )
        document["color_regions"] = _records(color_regions, "color_regions")
        document["palette_reduction"] = deepcopy(dict(palette_reduction))
        printability["print_package_mode"] = (
            "co_print_body"
            if manufacturing_mode == "single-part"
            else "separate_parts"
        )

    errors = validate_intent(document, destination.parent)
    if errors:
        raise AuthoringError("intent", errors)
    _write_json(destination, document, immutable=True)
    return document


def paired_interface(
    *,
    id: str,
    kind: str,
    male_feature: str,
    male_dimensions_mm: Mapping[str, float],
    female_feature: str,
    female_offsets_mm: Mapping[str, float],
    female_dimensions_mm: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Declare a paired scene interface without duplicating owners or dimensions.

    The caller still chooses the interface kind, endpoints, male dimensions, and
    every fit offset.  `write_scene` resolves endpoint owners from immutable
    intent and derives the matching female dimensions and provenance records.
    """

    if kind == "self-tapping-screw":
        raise AuthoringError(
            "scene interface",
            ["self-tapping-screw keeps its existing explicit canonical structure"],
        )
    return {
        "id": id,
        "kind": kind,
        "male": {
            "featureId": male_feature,
            "dimensionsMm": deepcopy(dict(male_dimensions_mm)),
        },
        "female": {
            "featureId": female_feature,
            "offsetsMm": deepcopy(dict(female_offsets_mm)),
            "dimensionsMm": deepcopy(dict(female_dimensions_mm or {})),
        },
    }


def _expand_paired_interfaces(
    raw_interfaces: Sequence[Mapping[str, Any]],
    owners: Mapping[str, str],
) -> list[dict[str, Any]]:
    interfaces = _records(raw_interfaces, "paired_interfaces")
    expanded: list[dict[str, Any]] = []
    for index, interface in enumerate(interfaces):
        interface_id = interface.get("id")
        kind = interface.get("kind")
        if kind == "self-tapping-screw":
            raise AuthoringError(
                "scene interface",
                [
                    f"paired_interfaces[{index}] cannot abbreviate "
                    "self-tapping-screw semantics"
                ],
            )
        canonical: dict[str, Any] = {"id": interface_id, "kind": kind}
        for endpoint_name in ("male", "female"):
            endpoint = interface.get(endpoint_name)
            if not isinstance(endpoint, Mapping):
                raise AuthoringError(
                    "scene interface",
                    [f"paired_interfaces[{index}].{endpoint_name} is required"],
                )
            endpoint = deepcopy(dict(endpoint))
            feature_id = endpoint.get("featureId")
            owner = owners.get(feature_id) if isinstance(feature_id, str) else None
            if owner is None:
                raise AuthoringError(
                    "scene interface",
                    [
                        f"paired_interfaces[{index}].{endpoint_name}.featureId "
                        "must resolve to immutable intent ownership"
                    ],
                )
            declared_owner = endpoint.pop("partId", owner)
            if declared_owner != owner:
                raise AuthoringError(
                    "scene interface",
                    [
                        f"paired_interfaces[{index}].{endpoint_name}.partId "
                        "conflicts with immutable intent ownership"
                    ],
                )
            endpoint["partId"] = owner
            canonical[endpoint_name] = endpoint

        male_dimensions = canonical["male"].get("dimensionsMm")
        female = canonical["female"]
        offsets = female.pop("offsetsMm", None)
        female_dimensions = female.get("dimensionsMm")
        if not isinstance(male_dimensions, Mapping) or not male_dimensions:
            raise AuthoringError(
                "scene interface",
                [f"paired_interfaces[{index}].male.dimensionsMm must be non-empty"],
            )
        if not isinstance(offsets, Mapping) or not offsets:
            raise AuthoringError(
                "scene interface",
                [f"paired_interfaces[{index}].female.offsetsMm must be non-empty"],
            )
        if not isinstance(female_dimensions, Mapping):
            raise AuthoringError(
                "scene interface",
                [f"paired_interfaces[{index}].female.dimensionsMm must be an object"],
            )
        female_dimensions = deepcopy(dict(female_dimensions))
        derived: dict[str, dict[str, Any]] = {}
        for field, offset in offsets.items():
            if field not in male_dimensions:
                raise AuthoringError(
                    "scene interface",
                    [
                        f"paired_interfaces[{index}] cannot derive female {field!r} "
                        "because the male dimension is missing"
                    ],
                )
            male_value = male_dimensions[field]
            if (
                not isinstance(male_value, (int, float))
                or isinstance(male_value, bool)
                or not isinstance(offset, (int, float))
                or isinstance(offset, bool)
            ):
                raise AuthoringError(
                    "scene interface",
                    [
                        f"paired_interfaces[{index}] dimension {field!r} and its "
                        "offset must be finite numbers"
                    ],
                )
            derived_value = float(male_value) + float(offset)
            declared_value = female_dimensions.get(field, derived_value)
            if not isinstance(declared_value, (int, float)) or isinstance(
                declared_value, bool
            ):
                raise AuthoringError(
                    "scene interface",
                    [f"paired_interfaces[{index}].female dimension {field!r} is invalid"],
                )
            if abs(float(declared_value) - derived_value) > 1e-9:
                raise AuthoringError(
                    "scene interface",
                    [
                        f"paired_interfaces[{index}].female dimension {field!r} "
                        "conflicts with the explicit offset"
                    ],
                )
            female_dimensions[field] = derived_value
            derived[field] = {"from": f"male.{field}", "offsetMm": offset}
        female["dimensionsMm"] = female_dimensions
        female["derivedDimensionsMm"] = derived
        expanded.append(canonical)
    return expanded


def _validate_interface_alignment(
    intent: Mapping[str, Any],
    interfaces: Sequence[Mapping[str, Any]],
) -> None:
    """Bind every scene interface to the immutable multipart declaration."""

    manufacturing = intent.get("manufacturing")
    if not isinstance(manufacturing, Mapping):
        raise AuthoringError("scene interface", ["intent manufacturing is invalid"])
    if manufacturing.get("mode") != "multipart":
        if interfaces:
            raise AuthoringError(
                "scene interface",
                ["single-part intent cannot declare scene interfaces"],
            )
        return

    raw_intent_interfaces = manufacturing.get("interfaces")
    if not isinstance(raw_intent_interfaces, Sequence) or isinstance(
        raw_intent_interfaces, (str, bytes)
    ):
        raw_intent_interfaces = []
    intent_interfaces = {
        item.get("id"): item
        for item in raw_intent_interfaces
        if isinstance(item, Mapping)
        and isinstance(item.get("id"), str)
    }
    scene_interfaces = {
        item.get("id"): item
        for item in interfaces
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if set(scene_interfaces) != set(intent_interfaces):
        raise AuthoringError(
            "scene interface",
            [
                "scene interface ids must exactly match immutable intent: "
                f"expected {sorted(intent_interfaces)}, observed {sorted(scene_interfaces)}"
            ],
        )

    for interface_id, target in intent_interfaces.items():
        scene_interface = scene_interfaces[interface_id]
        expected_kind = target.get("connection")
        if scene_interface.get("kind") != expected_kind:
            raise AuthoringError(
                "scene interface",
                [
                    f"interface {interface_id!r}.kind must match immutable "
                    f"connection {expected_kind!r}"
                ],
            )
        if expected_kind == "self-tapping-screw":
            continue

        endpoints = [
            scene_interface.get(name)
            for name in ("male", "female")
        ]
        observed_features = {
            endpoint.get("featureId")
            for endpoint in endpoints
            if isinstance(endpoint, Mapping)
            and isinstance(endpoint.get("featureId"), str)
        }
        expected_features = {
            item
            for item in target.get("features", [])
            if isinstance(item, str)
        }
        if observed_features != expected_features:
            raise AuthoringError(
                "scene interface",
                [
                    f"interface {interface_id!r} endpoints must exactly match "
                    f"immutable features {sorted(expected_features)}"
                ],
            )
        observed_parts = {
            endpoint.get("partId")
            for endpoint in endpoints
            if isinstance(endpoint, Mapping)
            and isinstance(endpoint.get("partId"), str)
        }
        expected_parts = {
            item for item in target.get("between", []) if isinstance(item, str)
        }
        if observed_parts != expected_parts:
            raise AuthoringError(
                "scene interface",
                [
                    f"interface {interface_id!r} endpoint owners must exactly match "
                    f"immutable parts {sorted(expected_parts)}"
                ],
            )


def _intent_materials(intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for region in intent.get("color_regions", []):
        if not isinstance(region, Mapping):
            continue
        material = region.get("material")
        record: dict[str, Any] = {
            "id": region.get("name"),
            "color": str(region.get("hex", "")).upper(),
        }
        if isinstance(material, Mapping):
            for field in ("filament", "transmission"):
                if field in material:
                    record[field] = material[field]
        materials.append(record)
    return materials


def _merge_materials(
    intent: Mapping[str, Any],
    explicit: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    materials = _intent_materials(intent)
    by_id = {
        material.get("id"): material
        for material in materials
        if isinstance(material.get("id"), str)
    }
    for material in _records(explicit, "materials"):
        material_id = material.get("id")
        immutable = by_id.get(material_id)
        if immutable is not None:
            for field, value in immutable.items():
                if field in material and material[field] != value:
                    raise AuthoringError(
                        "scene material",
                        [
                            f"material {material_id!r}.{field} conflicts with "
                            "immutable intent color evidence"
                        ],
                    )
            immutable.update(material)
        else:
            materials.append(material)
            if isinstance(material_id, str):
                by_id[material_id] = material
    return materials


def _expand_scene_parts(
    raw_parts: Mapping[str, Mapping[str, Any]],
    intent: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(raw_parts, Mapping) or not raw_parts:
        raise AuthoringError("scene", ["parts must map explicit part IDs to definitions"])
    intent_regions = {
        region.get("name"): region
        for region in intent.get("color_regions", [])
        if isinstance(region, Mapping) and isinstance(region.get("name"), str)
    }
    regions_by_part: dict[str, list[Mapping[str, Any]]] = {}
    for region in intent_regions.values():
        owner = region.get("part")
        if isinstance(owner, str):
            regions_by_part.setdefault(owner, []).append(region)

    parts: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for part_id, raw_part in raw_parts.items():
        if not isinstance(raw_part, Mapping):
            raise AuthoringError("scene", [f"parts[{part_id!r}] must be an object"])
        part = deepcopy(dict(raw_part))
        nested_nodes = _records(part.pop("nodes", ()), f"parts[{part_id!r}].nodes")
        declared_id = part.pop("id", part_id)
        if declared_id != part_id:
            raise AuthoringError(
                "scene", [f"parts[{part_id!r}].id conflicts with its nested owner"]
            )
        if "representationMaster" not in part:
            raise AuthoringError(
                "scene",
                [
                    f"parts[{part_id!r}].representationMaster must be chosen "
                    "explicitly as brep or mesh"
                ],
            )

        raw_regions = part.get("colorRegions", [])
        if raw_regions:
            canonical_regions: list[dict[str, Any]] = []
            for index, region in enumerate(
                _records(raw_regions, f"parts[{part_id!r}].colorRegions")
            ):
                region_id = region.get("id")
                immutable = intent_regions.get(region_id)
                if immutable is not None and immutable.get("part") != part_id:
                    raise AuthoringError(
                        "scene material",
                        [
                            f"color region {region_id!r} belongs to immutable part "
                            f"{immutable.get('part')!r}, not {part_id!r}"
                        ],
                    )
                if immutable is not None:
                    declared_material = region.get("materialId", region_id)
                    if declared_material != region_id:
                        raise AuthoringError(
                            "scene material",
                            [
                                f"color region {region_id!r}.materialId conflicts "
                                "with its deterministic intent material"
                            ],
                        )
                    region["materialId"] = region_id
                elif "materialId" not in region:
                    raise AuthoringError(
                        "scene material",
                        [
                            f"parts[{part_id!r}].colorRegions[{index}] needs an "
                            "explicit materialId because intent does not bind it"
                        ],
                    )
                canonical_regions.append(region)
            part["colorRegions"] = canonical_regions
        elif "materialId" not in part:
            owned_regions = regions_by_part.get(part_id, [])
            if len(owned_regions) == 1 and owned_regions[0].get("name") == part_id:
                part["materialId"] = part_id

        parts.append({"id": part_id, **part})
        for node in nested_nodes:
            declared_owner = node.get("partId", part_id)
            if declared_owner != part_id:
                raise AuthoringError(
                    "scene",
                    [
                        f"node {node.get('id')!r} declares partId {declared_owner!r} "
                        f"inside part {part_id!r}"
                    ],
                )
            role = node.get("role")
            operation = ROLE_OPERATIONS.get(role)
            if operation is None:
                raise AuthoringError(
                    "scene",
                    [f"node {node.get('id')!r} must choose a supported role"],
                )
            declared_operation = node.get("operation", operation)
            if declared_operation != operation:
                raise AuthoringError(
                    "scene",
                    [
                        f"node {node.get('id')!r}.operation conflicts with role "
                        f"{role!r}"
                    ],
                )
            node["partId"] = part_id
            node["operation"] = operation
            nodes.append(node)
    return parts, nodes


def write_scene(
    path: str | Path,
    *,
    intent_path: str | Path,
    parts: Mapping[str, Mapping[str, Any]],
    paired_interfaces: Sequence[Mapping[str, Any]] = (),
    interfaces: Sequence[Mapping[str, Any]] = (),
    materials: Sequence[Mapping[str, Any]] = (),
    revision: str | None = None,
) -> dict[str, Any]:
    """Write one validated canonical semantic scene directly.

    Part nesting supplies node ownership; node role supplies operation.  Paired
    interfaces derive endpoint ownership and female dimensions from explicit
    offsets.  Representation masters, recipes, fit offsets, source meshes, and
    optional raw interface structures remain caller decisions.
    """

    destination = Path(path).resolve()
    immutable_path = _resolve_input_path(intent_path)
    try:
        intent = json.loads(immutable_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuthoringError(
            "scene", [f"cannot read immutable intent {immutable_path}: {error}"]
        ) from error
    intent_errors = validate_intent(intent, immutable_path.parent)
    if intent_errors:
        raise AuthoringError("scene intent", intent_errors)

    owners = feature_owner_map(intent)
    canonical_parts, nodes = _expand_scene_parts(parts, intent)
    canonical_interfaces = _expand_paired_interfaces(paired_interfaces, owners)
    canonical_interfaces.extend(_records(interfaces, "interfaces"))
    _validate_interface_alignment(intent, canonical_interfaces)
    intent_ref = {
        "path": _stored_path(immutable_path, destination.parent),
        "schema": INTENT_SCHEMA,
        "sha256": _digest(immutable_path),
    }
    body: dict[str, Any] = {
        "units": "mm",
        "coordinateSystem": deepcopy(SCENE_COORDINATE_SYSTEM),
        "intentRef": intent_ref,
        "materials": _merge_materials(intent, materials),
        "parts": canonical_parts,
        "nodes": nodes,
        "interfaces": canonical_interfaces,
    }
    if revision is None:
        revision_payload = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        revision = f"rev-{sha256(revision_payload).hexdigest()[:16]}"
    document = {"schema": SCENE_SCHEMA, "revision": revision, **body}

    expected_parts = physical_part_names(intent)
    observed_parts = set(parts)
    if observed_parts != expected_parts:
        raise AuthoringError(
            "scene",
            [
                "parts must exactly match immutable intent ownership: "
                f"expected {sorted(expected_parts)}, observed {sorted(observed_parts)}"
            ],
        )
    errors = validate_scene(document, destination.parent)
    if errors:
        raise AuthoringError("scene", errors)
    _write_json(destination, document)
    return document
