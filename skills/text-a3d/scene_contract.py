"""Validate the mutable semantic model graph derived from a separate intent.

The intent contract records what the user asked for and why.  This scene
contract records the geometry graph that an agent may revise while modelling:
parts, boolean nodes, interface dimensions, and physical/display artifacts.
Keeping the two documents separate prevents an implementation edit from
silently rewriting the evidence that motivated it.
"""

from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

from capability_registry import capability_for_connection, connection_kinds
from intent_contract import (
    feature_owner_map as intent_feature_owner_map,
    physical_part_names as intent_physical_part_names,
    validate as validate_intent,
)


SCENE_SCHEMA = "evidence-semantic-scene/v1"
INTENT_SCHEMAS = {"evidence-cad-intent/v5"}
REPRESENTATION_MASTERS = {"brep", "mesh"}
ROLES = {"solid", "cutter", "separate", "display-only"}
DISPLAY_COMPONENT_KIND = "displayComponent"
BREP_GEOMETRY_RECIPE_KIND = "brepGeometry"
MESH_GEOMETRY_RECIPE_KIND = "meshGeometry"
PHYSICAL_GEOMETRY_RECIPE_KINDS = {
    BREP_GEOMETRY_RECIPE_KIND,
    MESH_GEOMETRY_RECIPE_KIND,
}
SELF_TAPPING_RECIPE_KIND = "selfTappingScrewPair"
SELF_TAPPING_OUTPUTS = {
    "clearance-cutter",
    "pilot-cutter",
    "receiver-boss",
}
SELF_TAPPING_LOCATOR_KINDS = {"collar-socket", "pin-socket"}
ROLE_OPERATIONS = {
    "solid": "union",
    "cutter": "subtract",
    "separate": "none",
    "display-only": "none",
}
ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*")
FEATURE_ID_PATTERN = re.compile(
    r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*"
    r"(?:/[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*)*"
)
TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._/-]*")
REVISION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
HEX_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")
INTENT_ONLY_FIELDS = {
    "assumptions",
    "dimensions_mm",
    "features",
    "manufacturing",
    "printability",
    "reference_files",
    "representation",
    "task_mode",
    "visual",
}


def _validate_interface_intent_alignment(
    intent: dict[str, Any] | None,
    interfaces: Any,
    nodes_by_feature: dict[str, dict],
    errors: list[str],
) -> None:
    """Validate ordinary interfaces independently from the authoring helper."""

    if not isinstance(intent, dict):
        return
    manufacturing = intent.get("manufacturing")
    if not isinstance(manufacturing, dict):
        return
    raw_targets = manufacturing.get("interfaces")
    targets = raw_targets if isinstance(raw_targets, list) else []
    implementations = interfaces if isinstance(interfaces, list) else []
    target_by_id = {
        item.get("id"): item
        for item in targets
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    locator_by_id: dict[str, tuple[dict, dict]] = {}
    for target in targets:
        if not (
            isinstance(target, dict)
            and target.get("connection") == "self-tapping-screw"
        ):
            continue
        fastening = target.get("fastening")
        locator_pairs = (
            fastening.get("locator_pairs") if isinstance(fastening, dict) else None
        )
        for locator in locator_pairs if isinstance(locator_pairs, list) else []:
            if isinstance(locator, dict) and isinstance(locator.get("id"), str):
                locator_by_id[locator["id"]] = (target, locator)
    implementation_by_id = {
        item.get("id"): item
        for item in implementations
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    expected_ids = set(target_by_id) | set(locator_by_id)
    if expected_ids != set(implementation_by_id):
        errors.append(
            "scene interface ids must exactly match immutable intent: "
            f"expected {sorted(expected_ids)}, observed {sorted(implementation_by_id)}"
        )
    for interface_id in sorted(set(target_by_id) & set(implementation_by_id)):
        target = target_by_id[interface_id]
        implementation = implementation_by_id[interface_id]
        path = f"interfaces[{interface_id}]"
        connection = target.get("connection")
        if implementation.get("kind") != connection:
            errors.append(
                f"{path}.kind must match immutable intent connection {connection!r}"
            )
        if connection == "self-tapping-screw":
            continue
        capability = capability_for_connection(str(connection))
        if capability is None:
            errors.append(f"{path}.kind has no registered proof capability")
            continue
        endpoints = {
            name: implementation.get(name)
            for name in ("male", "female")
            if isinstance(implementation.get(name), dict)
        }
        expected_features = {
            item for item in target.get("features", []) if isinstance(item, str)
        }
        observed_features = {
            endpoint.get("featureId")
            for endpoint in endpoints.values()
            if isinstance(endpoint.get("featureId"), str)
        }
        if observed_features != expected_features:
            errors.append(
                f"{path} endpoints must exactly match immutable features "
                f"{sorted(expected_features)}"
            )
        expected_parts = {
            item for item in target.get("between", []) if isinstance(item, str)
        }
        observed_parts = {
            endpoint.get("partId")
            for endpoint in endpoints.values()
            if isinstance(endpoint.get("partId"), str)
        }
        if observed_parts != expected_parts:
            errors.append(
                f"{path} endpoint owners must exactly match immutable parts "
                f"{sorted(expected_parts)}"
            )
        endpoint_roles = capability.get("endpointRoles", {})
        for endpoint_name, endpoint in endpoints.items():
            feature_id = endpoint.get("featureId")
            node = nodes_by_feature.get(feature_id)
            allowed_roles = set(endpoint_roles.get(endpoint_name, ()))
            if allowed_roles and isinstance(node, dict) and node.get("role") not in allowed_roles:
                errors.append(
                    f"{path}.{endpoint_name}.featureId must reference a scene node "
                    f"with role in {sorted(allowed_roles)}"
                )
        female = endpoints.get("female")
        derived = female.get("derivedDimensionsMm") if isinstance(female, dict) else None
        raw_clearances = target.get("clearances_mm")
        clearances = raw_clearances if isinstance(raw_clearances, dict) else {}
        derived_fields = set(derived) if isinstance(derived, dict) else set()
        if derived_fields != set(clearances):
            errors.append(
                f"{path}.female.derivedDimensionsMm fields must exactly match "
                f"immutable clearances_mm: expected {sorted(clearances)}, "
                f"observed {sorted(derived_fields)}"
            )
        if isinstance(derived, dict):
            for field in sorted(set(derived) & set(clearances)):
                rule = derived.get(field)
                offset = rule.get("offsetMm") if isinstance(rule, dict) else None
                clearance = clearances[field]
                if _number(offset) and _number(clearance) and (
                    float(offset) != float(clearance)
                ):
                    errors.append(
                        f"{path}.female.derivedDimensionsMm.{field}.offsetMm must "
                        f"exactly match immutable clearances_mm.{field} "
                        f"{float(clearance):g}"
                    )

    for interface_id in sorted(set(locator_by_id) & set(implementation_by_id)):
        parent, locator = locator_by_id[interface_id]
        implementation = implementation_by_id[interface_id]
        path = f"interfaces[{interface_id}]"
        if implementation.get("kind") not in SELF_TAPPING_LOCATOR_KINDS:
            errors.append(
                f"{path}.kind must be one of the locator kinds declared for "
                "self-tapping interfaces"
            )
        endpoints = {
            name: implementation.get(name)
            for name in ("male", "female")
            if isinstance(implementation.get(name), dict)
        }
        expected_features = {
            item
            for item in (
                locator.get("male_feature"),
                locator.get("female_feature"),
            )
            if isinstance(item, str)
        }
        observed_features = {
            endpoint.get("featureId")
            for endpoint in endpoints.values()
            if isinstance(endpoint.get("featureId"), str)
        }
        if observed_features != expected_features:
            errors.append(
                f"{path} endpoints must exactly match immutable locator features "
                f"{sorted(expected_features)}"
            )
        expected_parts = {
            item for item in parent.get("between", []) if isinstance(item, str)
        }
        observed_parts = {
            endpoint.get("partId")
            for endpoint in endpoints.values()
            if isinstance(endpoint.get("partId"), str)
        }
        if observed_parts != expected_parts:
            errors.append(
                f"{path} endpoint owners must exactly match immutable parts "
                f"{sorted(expected_parts)}"
            )
        for endpoint_name, allowed_roles in (
            ("male", {"separate", "solid"}),
            ("female", {"cutter"}),
        ):
            endpoint = endpoints.get(endpoint_name)
            feature_id = endpoint.get("featureId") if isinstance(endpoint, dict) else None
            node = nodes_by_feature.get(feature_id)
            if isinstance(node, dict) and node.get("role") not in allowed_roles:
                errors.append(
                    f"{path}.{endpoint_name}.featureId must reference a scene node "
                    f"with role in {sorted(allowed_roles)}"
                )


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _positive_number(value: Any) -> bool:
    return _number(value) and value > 0


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def _valid_feature_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and FEATURE_ID_PATTERN.fullmatch(value) is not None
    )


def _validate_json_value(value: Any, path: str, errors: list[str]) -> None:
    """Reject non-JSON and non-finite recipe values before serialization."""

    if value is None or isinstance(value, (str, bool)):
        return
    if _number(value):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]", errors)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} keys must be strings")
                continue
            _validate_json_value(item, f"{path}.{key}", errors)
        return
    errors.append(f"{path} must contain JSON-compatible finite values")


def _validate_transform(value: Any, path: str, errors: list[str]) -> None:
    if not (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(row, list) and len(row) == 4 for row in value)
        and all(_number(item) for row in value for item in row)
    ):
        errors.append(f"{path} must be a finite 4x4 matrix")
        return
    if any(
        abs(float(value[3][index]) - expected) > 1e-9
        for index, expected in enumerate((0, 0, 0, 1))
    ):
        errors.append(f"{path} must be an affine transform with last row [0, 0, 0, 1]")
        return

    # A canonicalization transform may rotate and translate, but it may not
    # conceal a scale or reflection between display and manufacturing output.
    columns = [[float(value[row][column]) for row in range(3)] for column in range(3)]
    for index, column in enumerate(columns):
        length = math.sqrt(sum(component * component for component in column))
        if abs(length - 1.0) > 1e-7:
            errors.append(f"{path} axis {index} must have unit scale")
    for left in range(3):
        for right in range(left + 1, 3):
            dot = sum(columns[left][i] * columns[right][i] for i in range(3))
            if abs(dot) > 1e-7:
                errors.append(f"{path} rotation axes must be orthogonal")
    a, b, c = columns
    determinant = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - b[0] * (a[1] * c[2] - a[2] * c[1])
        + c[0] * (a[1] * b[2] - a[2] * b[1])
    )
    if abs(determinant - 1.0) > 1e-7:
        errors.append(f"{path} rotation determinant must be +1")


def _validate_intent_ref(
    reference: Any,
    base_dir: Path | None,
    errors: list[str],
) -> dict[str, Any] | None:
    if not isinstance(reference, dict):
        errors.append("intentRef must reference the separate immutable intent contract")
        return None
    schema = reference.get("schema")
    if schema not in INTENT_SCHEMAS:
        errors.append(f"intentRef.schema must be one of {sorted(INTENT_SCHEMAS)}")
    raw_path = reference.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append("intentRef.path is required")
        return None
    digest = reference.get("sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        errors.append("intentRef.sha256 must be a lowercase SHA-256 digest")
    if base_dir is None:
        errors.append("base_dir is required to validate the referenced intent")
        return None

    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    try:
        payload = path.read_bytes()
        intent = json.loads(payload)
    except Exception as error:
        errors.append(f"intentRef cannot be read: {error}")
        return None
    if isinstance(digest, str) and SHA256_PATTERN.fullmatch(digest):
        if sha256(payload).hexdigest() != digest:
            errors.append("intentRef.sha256 does not match the referenced intent")
    if not isinstance(intent, dict) or intent.get("schema") != schema:
        errors.append("intentRef.schema does not match the referenced intent")
        return None
    intent_errors = validate_intent(intent, path.resolve().parent)
    errors.extend(f"intentRef: {error}" for error in intent_errors)
    if intent_errors:
        return None
    return intent


def _validate_artifact(
    artifact: Any,
    path: str,
    expected_suffixes: tuple[str, ...],
    revision: str,
    *,
    allow_node_names: bool,
) -> None:
    if not isinstance(artifact, dict):
        raise TypeError(f"{path} must be an object")
    raw_path = artifact.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{path}.path is required")
    if not raw_path.lower().endswith(expected_suffixes):
        suffixes = ", ".join(expected_suffixes)
        raise ValueError(f"{path}.path must end with one of {suffixes}")
    if artifact.get("revision") != revision:
        raise ValueError(f"{path}.revision must match scene revision")
    if not _positive_number(artifact.get("scale")):
        raise ValueError(f"{path}.scale must be positive")
    if abs(float(artifact["scale"]) - 1.0) > 1e-12:
        raise ValueError(f"{path}.scale must be 1; export-time fitting is forbidden")
    if "nodeNames" in artifact:
        names = artifact["nodeNames"]
        if not allow_node_names:
            raise ValueError(f"{path}.nodeNames is only valid for scene artifacts")
        if not isinstance(names, list) or not names or not all(
            isinstance(name, str) and name.strip() for name in names
        ):
            raise ValueError(f"{path}.nodeNames must be a non-empty string list")
        if len(names) != len(set(names)):
            raise ValueError(f"{path}.nodeNames must be unique")


def _validate_file_binding(
    artifact: Any,
    path: str,
    base_dir: Path | None,
    errors: list[str],
) -> None:
    """Require a content-addressed existing artifact when paths are resolvable."""

    if not isinstance(artifact, dict):
        return
    digest = artifact.get("sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        errors.append(f"{path}.sha256 must be a lowercase SHA-256 digest")
        return
    if base_dir is None:
        return
    raw_path = artifact.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return
    resolved = Path(raw_path)
    if not resolved.is_absolute():
        resolved = base_dir / resolved
    try:
        payload = resolved.read_bytes()
    except OSError as error:
        errors.append(f"{path} cannot be read: {error}")
        return
    if sha256(payload).hexdigest() != digest:
        errors.append(f"{path}.sha256 does not match the referenced artifact")


def _validate_source_mesh_spec(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        if not value.strip():
            errors.append(f"{path} must not be empty")
        return
    if not isinstance(value, dict):
        errors.append(f"{path} must be a path or object")
        return
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append(f"{path}.path is required")
    scale = value.get("scale", 1.0)
    if not _number(scale) or abs(float(scale) - 1.0) > 1e-12:
        errors.append(f"{path}.scale must be 1; scaling is forbidden")
    transform = value.get("toCanonicalTransform")
    if transform is not None:
        _validate_transform(transform, f"{path}.toCanonicalTransform", errors)


def _validate_bound_geometry(
    recipe: dict[str, Any],
    path: str,
    base_dir: Path | None,
    errors: list[str],
) -> None:
    kind = recipe.get("kind")
    parameters = recipe.get("parameters")
    if kind not in PHYSICAL_GEOMETRY_RECIPE_KINDS or not isinstance(parameters, dict):
        return
    expected_parameters = (
        {"geometry", "tessellation"}
        if kind == BREP_GEOMETRY_RECIPE_KIND
        else {"geometry"}
    )
    if set(parameters) != expected_parameters:
        errors.append(
            f"{path}.parameters must contain exactly {sorted(expected_parameters)}"
        )
    geometry = parameters.get("geometry")
    geometry_path = f"{path}.parameters.geometry"
    if not isinstance(geometry, dict):
        errors.append(f"{geometry_path} must be an object")
    else:
        if set(geometry) != {"path", "scale", "sha256"}:
            errors.append(
                f"{geometry_path} must contain exactly path, scale, and sha256"
            )
        _validate_source_mesh_spec(geometry, geometry_path, errors)
        raw_path = geometry.get("path")
        if isinstance(raw_path, str) and Path(raw_path).suffix.lower() != ".stl":
            errors.append(f"{geometry_path}.path must reference an STL")
        digest = geometry.get("sha256")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            errors.append(f"{geometry_path}.sha256 must be a lowercase SHA-256")
        if base_dir is not None:
            _validate_file_binding(geometry, geometry_path, base_dir, errors)
    if kind == BREP_GEOMETRY_RECIPE_KIND:
        tessellation = parameters.get("tessellation")
        tessellation_path = f"{path}.parameters.tessellation"
        if not isinstance(tessellation, dict) or set(tessellation) != {
            "angularToleranceRad",
            "linearToleranceMm",
        }:
            errors.append(
                f"{tessellation_path} must contain exactly angularToleranceRad "
                "and linearToleranceMm"
            )
        elif any(
            not _positive_number(tessellation.get(field))
            for field in ("angularToleranceRad", "linearToleranceMm")
        ):
            errors.append(f"{tessellation_path} values must be finite and positive")


def _dimensions(value: Any, path: str, errors: list[str]) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        errors.append(f"{path} must be a non-empty object")
        return {}
    result: dict[str, float] = {}
    for name, dimension in value.items():
        if not isinstance(name, str) or not TOKEN_PATTERN.fullmatch(name):
            errors.append(f"{path} dimension names must be stable tokens")
            continue
        if not _positive_number(dimension):
            errors.append(f"{path}.{name} must be positive")
            continue
        result[name] = float(dimension)
    return result


def _vector3(
    value: Any,
    path: str,
    errors: list[str],
    *,
    require_unit: bool = False,
) -> list[float] | None:
    if not (
        isinstance(value, list)
        and len(value) == 3
        and all(_number(item) for item in value)
    ):
        errors.append(f"{path} must be a finite three-component vector")
        return None
    result = [float(item) for item in value]
    if require_unit:
        length = math.sqrt(sum(item * item for item in result))
        if abs(length - 1.0) > 1e-7:
            errors.append(f"{path} must be a unit vector")
    return result


def _validate_axis_bound_feature(
    *,
    feature_id: Any,
    expected_part_id: Any,
    expected_role: str,
    interface_id: str,
    fastener_id: str,
    output: str,
    path: str,
    nodes_by_feature: dict[str, dict],
    errors: list[str],
) -> None:
    if feature_id not in nodes_by_feature:
        errors.append(f"{path} references an unknown scene feature")
        return
    node = nodes_by_feature[feature_id]
    if node.get("partId") != expected_part_id:
        errors.append(f"{path} belongs to a different part")
    if node.get("role") != expected_role:
        errors.append(f"{path} must reference a {expected_role} node")
    recipe = node.get("recipe")
    parameters = recipe.get("parameters") if isinstance(recipe, dict) else None
    if not isinstance(recipe, dict) or recipe.get("kind") != SELF_TAPPING_RECIPE_KIND:
        errors.append(
            f"{path} must use recipe.kind {SELF_TAPPING_RECIPE_KIND}"
        )
        return
    if not isinstance(parameters, dict):
        errors.append(f"{path} recipe parameters must be an object")
        return
    expected = {
        "fastenerId": fastener_id,
        "interfaceId": interface_id,
        "output": output,
    }
    observed = {key: parameters.get(key) for key in expected}
    if observed != expected:
        errors.append(f"{path} must bind the shared recipe output {expected}")
    forbidden = {
        "axisDirection",
        "axisOriginMm",
        "sourceMesh",
        "toCanonicalTransform",
        "transform",
    }.intersection(parameters)
    if forbidden:
        errors.append(
            f"{path} cannot declare independent source/axis transforms: "
            + ", ".join(sorted(forbidden))
        )


def _validate_self_tapping_recipe_nodes(
    interfaces: Any,
    nodes: Any,
    errors: list[str],
) -> None:
    """Require an exact three-node implementation for every declared screw axis."""

    expected: dict[tuple[str, str, str], tuple[Any, Any, str]] = {}
    if isinstance(interfaces, list):
        for interface in interfaces:
            if not (
                isinstance(interface, dict)
                and isinstance(interface.get("id"), str)
                and interface.get("kind") == "self-tapping-screw"
            ):
                continue
            interface_id = interface["id"]
            fasteners = interface.get("fasteners")
            if not isinstance(fasteners, list):
                continue
            for fastener in fasteners:
                if not isinstance(fastener, dict) or not isinstance(
                    fastener.get("id"), str
                ):
                    continue
                fastener_id = fastener["id"]
                cover = fastener.get("cover")
                receiver = fastener.get("receiver")
                cover = cover if isinstance(cover, dict) else {}
                receiver = receiver if isinstance(receiver, dict) else {}
                expected[(interface_id, fastener_id, "clearance-cutter")] = (
                    cover.get("featureId"),
                    cover.get("partId"),
                    "cutter",
                )
                expected[(interface_id, fastener_id, "pilot-cutter")] = (
                    receiver.get("featureId"),
                    receiver.get("partId"),
                    "cutter",
                )
                expected[(interface_id, fastener_id, "receiver-boss")] = (
                    receiver.get("bossFeatureId"),
                    receiver.get("partId"),
                    "solid",
                )

    observed: dict[tuple[str, str, str], list[tuple[int, dict]]] = {}
    if isinstance(nodes, list):
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            recipe = node.get("recipe")
            if not (
                isinstance(recipe, dict)
                and recipe.get("kind") == SELF_TAPPING_RECIPE_KIND
            ):
                continue
            path = f"nodes[{index}].recipe.parameters"
            parameters = recipe.get("parameters")
            if not isinstance(parameters, dict):
                continue
            required = {"interfaceId", "fastenerId", "output"}
            if set(parameters) != required:
                errors.append(
                    f"{path} must contain exactly interfaceId, fastenerId, and output"
                )
            interface_id = parameters.get("interfaceId")
            fastener_id = parameters.get("fastenerId")
            output = parameters.get("output")
            if not _valid_id(interface_id):
                errors.append(f"{path}.interfaceId is invalid")
            if not _valid_id(fastener_id):
                errors.append(f"{path}.fastenerId is invalid")
            if output not in SELF_TAPPING_OUTPUTS:
                errors.append(
                    f"{path}.output must be one of {sorted(SELF_TAPPING_OUTPUTS)}"
                )
            if (
                isinstance(interface_id, str)
                and isinstance(fastener_id, str)
                and isinstance(output, str)
            ):
                observed.setdefault((interface_id, fastener_id, output), []).append(
                    (index, node)
                )

    for key, nodes_for_key in observed.items():
        if key not in expected:
            for index, _ in nodes_for_key:
                errors.append(
                    f"nodes[{index}] declares an unbound {SELF_TAPPING_RECIPE_KIND} output"
                )
            continue
        if len(nodes_for_key) != 1:
            errors.append(
                f"self-tapping recipe {key[0]}/{key[1]}/{key[2]} must have exactly one node"
            )

    for key, (feature_id, part_id, role) in expected.items():
        nodes_for_key = observed.get(key, [])
        if len(nodes_for_key) != 1:
            if not nodes_for_key:
                errors.append(
                    f"self-tapping recipe {key[0]}/{key[1]}/{key[2]} is missing its node"
                )
            continue
        index, node = nodes_for_key[0]
        if node.get("featureId") != feature_id:
            errors.append(
                f"nodes[{index}].featureId must be the declared feature {feature_id!r}"
            )
        if node.get("partId") != part_id or node.get("role") != role:
            errors.append(
                f"nodes[{index}] must be the declared {role} output on part {part_id!r}"
            )


def _validate_self_tapping_scene_interface(
    *,
    interface: dict,
    path: str,
    nodes_by_feature: dict[str, dict],
    errors: list[str],
) -> None:
    """Validate one located screw joint with single-source fastener axes."""

    fasteners = interface.get("fasteners")
    if not isinstance(fasteners, list) or not fasteners:
        errors.append(f"{path}.fasteners must declare at least one screw axis")
        return
    joint_parts: set[Any] | None = None
    fastener_ids: list[str] = []
    used_features: list[str] = []
    interface_id = interface.get("id")
    for index, fastener in enumerate(fasteners):
        fastener_path = f"{path}.fasteners[{index}]"
        if not isinstance(fastener, dict):
            errors.append(f"{fastener_path} must be an object")
            continue
        fastener_id = fastener.get("id")
        if not _valid_id(fastener_id):
            errors.append(f"{fastener_path}.id is invalid")
            continue
        fastener_ids.append(fastener_id)
        axis = fastener.get("axis")
        if not isinstance(axis, dict):
            errors.append(f"{fastener_path}.axis must be an object")
        else:
            _vector3(axis.get("originMm"), f"{fastener_path}.axis.originMm", errors)
            _vector3(
                axis.get("direction"),
                f"{fastener_path}.axis.direction",
                errors,
                require_unit=True,
            )

        nominal = fastener.get("nominalDiameterMm")
        if not _positive_number(nominal):
            errors.append(f"{fastener_path}.nominalDiameterMm must be positive")
        screw_family = fastener.get("screwFamily")
        if not isinstance(screw_family, str) or not screw_family.strip():
            errors.append(f"{fastener_path}.screwFamily is required")
        overshoot = fastener.get("cutterOvershootMm")
        if not _positive_number(overshoot):
            errors.append(f"{fastener_path}.cutterOvershootMm must be positive")

        cover = fastener.get("cover")
        receiver = fastener.get("receiver")
        if not isinstance(cover, dict):
            errors.append(f"{fastener_path}.cover must be an object")
            cover = {}
        if not isinstance(receiver, dict):
            errors.append(f"{fastener_path}.receiver must be an object")
            receiver = {}
        for endpoint_name, endpoint in (("cover", cover), ("receiver", receiver)):
            if "originMm" in endpoint or "direction" in endpoint:
                errors.append(
                    f"{fastener_path}.{endpoint_name} must reference the shared "
                    "fastener.axis instead of declaring an independent axis"
                )
        cover_part = cover.get("partId")
        receiver_part = receiver.get("partId")
        if cover_part == receiver_part:
            errors.append(f"{fastener_path} must connect two distinct parts")
        fastener_parts = {cover_part, receiver_part}
        if (
            joint_parts is None
            and len(fastener_parts) == 2
            and all(isinstance(item, str) for item in fastener_parts)
        ):
            joint_parts = fastener_parts
        elif joint_parts is not None and fastener_parts != joint_parts:
            errors.append(
                f"{fastener_path} must connect the same two parts as every screw axis"
            )

        clearance_diameter = cover.get("diameterMm")
        pilot_diameter = receiver.get("diameterMm")
        cover_thickness = cover.get("thicknessMm")
        if not _positive_number(cover_thickness):
            errors.append(f"{fastener_path}.cover.thicknessMm must be positive")
        if not _positive_number(clearance_diameter):
            errors.append(f"{fastener_path}.cover.diameterMm must be positive")
        if not _positive_number(pilot_diameter):
            errors.append(f"{fastener_path}.receiver.diameterMm must be positive")
        if all(
            _positive_number(value)
            for value in (pilot_diameter, nominal, clearance_diameter)
        ) and not (
            float(pilot_diameter)
            < float(nominal)
            < float(clearance_diameter)
        ):
            errors.append(
                f"{fastener_path} diameters must satisfy pilot < nominal < clearance"
            )
        boss_outer = receiver.get("bossOuterDiameterMm")
        if not _positive_number(boss_outer):
            errors.append(
                f"{fastener_path}.receiver.bossOuterDiameterMm must be positive"
            )
        elif _positive_number(pilot_diameter) and float(boss_outer) <= float(
            pilot_diameter
        ):
            errors.append(
                f"{fastener_path}.receiver.bossOuterDiameterMm must exceed the pilot"
            )
        for key in (
            "closedEndMm",
            "engagementMm",
            "minimumBossWallMm",
            "tipClearanceMm",
        ):
            if not _positive_number(receiver.get(key)):
                errors.append(f"{fastener_path}.receiver.{key} must be positive")
        if "rootOverlapMm" in receiver:
            errors.append(
                f"{fastener_path}.receiver.rootOverlapMm is unsupported; "
                "use minimumRootEmbedMm"
            )
        if not _positive_number(receiver.get("minimumRootEmbedMm")):
            errors.append(
                f"{fastener_path}.receiver.minimumRootEmbedMm must be positive"
            )

        clearance_feature = cover.get("featureId")
        pilot_feature = receiver.get("featureId")
        boss_feature = receiver.get("bossFeatureId")
        for feature_id in (clearance_feature, pilot_feature, boss_feature):
            if isinstance(feature_id, str):
                used_features.append(feature_id)
        _validate_axis_bound_feature(
            feature_id=clearance_feature,
            expected_part_id=cover_part,
            expected_role="cutter",
            interface_id=interface_id,
            fastener_id=fastener_id,
            output="clearance-cutter",
            path=f"{fastener_path}.cover.featureId",
            nodes_by_feature=nodes_by_feature,
            errors=errors,
        )
        _validate_axis_bound_feature(
            feature_id=pilot_feature,
            expected_part_id=receiver_part,
            expected_role="cutter",
            interface_id=interface_id,
            fastener_id=fastener_id,
            output="pilot-cutter",
            path=f"{fastener_path}.receiver.featureId",
            nodes_by_feature=nodes_by_feature,
            errors=errors,
        )
        _validate_axis_bound_feature(
            feature_id=boss_feature,
            expected_part_id=receiver_part,
            expected_role="solid",
            interface_id=interface_id,
            fastener_id=fastener_id,
            output="receiver-boss",
            path=f"{fastener_path}.receiver.bossFeatureId",
            nodes_by_feature=nodes_by_feature,
            errors=errors,
        )

        head_diameter = cover.get("headRecessDiameterMm")
        head_depth = cover.get("headRecessDepthMm")
        minimum_land = cover.get("minimumResidualWallMm")
        if (head_diameter is None) != (head_depth is None):
            errors.append(
                f"{fastener_path}.cover head recess diameter and depth must be declared together"
            )
        elif head_diameter is not None:
            if not _positive_number(head_diameter) or (
                _positive_number(clearance_diameter)
                and float(head_diameter) <= float(clearance_diameter)
            ):
                errors.append(
                    f"{fastener_path}.cover.headRecessDiameterMm must exceed the clearance"
                )
            if not _positive_number(head_depth):
                errors.append(
                    f"{fastener_path}.cover.headRecessDepthMm must be positive"
                )
            if not _positive_number(minimum_land):
                errors.append(
                    f"{fastener_path}.cover.minimumResidualWallMm must be positive"
                )
            if all(
                _positive_number(value)
                for value in (head_depth, minimum_land, cover_thickness)
            ) and float(head_depth) + float(minimum_land) > float(cover_thickness):
                errors.append(
                    f"{fastener_path}.cover head recess leaves less than the minimum residual wall"
                )
        elif minimum_land is not None:
            errors.append(
                f"{fastener_path}.cover.minimumResidualWallMm requires a head recess"
            )
    if len(fastener_ids) != len(set(fastener_ids)):
        errors.append(f"{path}.fasteners ids must be unique")
    if len(used_features) != len(set(used_features)):
        errors.append(f"{path}.fasteners cannot reuse hole or boss features")


def _validate_self_tapping_locator_interfaces(
    interfaces: Any,
    nodes_by_feature: dict[str, dict],
    errors: list[str],
) -> None:
    """Bind each screw joint to one or more independently validated locators."""

    if not isinstance(interfaces, list):
        return
    interfaces_by_id = {
        item.get("id"): item
        for item in interfaces
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for index, interface in enumerate(interfaces):
        if not (
            isinstance(interface, dict)
            and interface.get("kind") == "self-tapping-screw"
        ):
            continue
        path = f"interfaces[{index}]"
        references = interface.get("locatorInterfaceIds")
        if not (
            isinstance(references, list)
            and references
            and all(_valid_id(item) for item in references)
        ):
            errors.append(
                f"{path}.locatorInterfaceIds must name at least one locator interface"
            )
            continue
        if len(references) != len(set(references)):
            errors.append(f"{path}.locatorInterfaceIds must be unique")

        joint_parts: set[Any] | None = None
        screw_features: set[str] = set()
        fasteners = interface.get("fasteners")
        if isinstance(fasteners, list):
            for fastener in fasteners:
                if not isinstance(fastener, dict):
                    continue
                cover = fastener.get("cover")
                receiver = fastener.get("receiver")
                if not isinstance(cover, dict) or not isinstance(receiver, dict):
                    continue
                screw_features.update(
                    feature_id
                    for feature_id in (
                        cover.get("featureId"),
                        receiver.get("featureId"),
                        receiver.get("bossFeatureId"),
                    )
                    if isinstance(feature_id, str)
                )
                pair = {cover.get("partId"), receiver.get("partId")}
                if (
                    joint_parts is None
                    and len(pair) == 2
                    and all(isinstance(item, str) for item in pair)
                ):
                    joint_parts = pair

        for reference in references:
            locator_path = f"{path}.locatorInterfaceIds[{reference}]"
            locator = interfaces_by_id.get(reference)
            if not isinstance(locator, dict):
                errors.append(f"{locator_path} references an unknown interface")
                continue
            if locator.get("kind") not in SELF_TAPPING_LOCATOR_KINDS:
                errors.append(
                    f"{locator_path} must reference one of "
                    f"{sorted(SELF_TAPPING_LOCATOR_KINDS)}"
                )
            endpoints = (locator.get("male"), locator.get("female"))
            locator_parts = {
                endpoint.get("partId")
                for endpoint in endpoints
                if isinstance(endpoint, dict)
            }
            if joint_parts is not None and locator_parts != joint_parts:
                errors.append(
                    f"{locator_path} must connect the same two parts as the screw joint"
                )
            for endpoint_name, endpoint, roles in (
                ("male", locator.get("male"), {"solid", "separate"}),
                ("female", locator.get("female"), {"cutter"}),
            ):
                if not isinstance(endpoint, dict):
                    continue
                feature_id = endpoint.get("featureId")
                node = nodes_by_feature.get(feature_id)
                if feature_id in screw_features:
                    errors.append(
                        f"{locator_path}.{endpoint_name} must be independent from "
                        "clearance, pilot, and boss features"
                    )
                if isinstance(node, dict) and node.get("role") not in roles:
                    errors.append(
                        f"{locator_path}.{endpoint_name} must reference a "
                        f"{' or '.join(sorted(roles))} node"
                    )


def _validate_self_tapping_intent_alignment(
    intent: dict[str, Any] | None,
    scene_interfaces: Any,
    errors: list[str],
) -> None:
    """Keep self-tapping implementation IDs and critical sizes bound to intent."""

    if not isinstance(intent, dict):
        return
    manufacturing = intent.get("manufacturing")
    if not isinstance(manufacturing, dict):
        return
    raw_intent_interfaces = manufacturing.get("interfaces")
    if not isinstance(raw_intent_interfaces, list):
        return
    intent_interfaces = {
        item.get("id"): item
        for item in raw_intent_interfaces
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item.get("connection") == "self-tapping-screw"
    }
    scene_items = scene_interfaces if isinstance(scene_interfaces, list) else []
    all_scene_by_id = {
        item.get("id"): item
        for item in scene_items
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
    }
    scene_by_id = {
        interface_id: item
        for interface_id, item in all_scene_by_id.items()
        if item.get("kind") == "self-tapping-screw"
    }
    if not intent_interfaces and not scene_by_id:
        return
    for interface_id in sorted(set(intent_interfaces) | set(scene_by_id)):
        intent_interface = intent_interfaces.get(interface_id)
        scene_interface = scene_by_id.get(interface_id)
        path = f"interfaces[{interface_id}]"
        if not isinstance(intent_interface, dict):
            errors.append(f"{path} has no matching self-tapping intent interface")
            continue
        if not isinstance(scene_interface, dict):
            errors.append(f"{path} is required by the self-tapping intent")
            continue
        fastening = intent_interface.get("fastening")
        if not isinstance(fastening, dict):
            continue
        raw_locator_pairs = fastening.get("locator_pairs")
        raw_locator_pairs = (
            raw_locator_pairs if isinstance(raw_locator_pairs, list) else []
        )
        locator_pair_ids = [
            item.get("id")
            for item in raw_locator_pairs
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if len(locator_pair_ids) != len(set(locator_pair_ids)):
            errors.append(f"{path}.locator_pairs ids must be unique")
        locator_pair_features = [
            item.get(key)
            for item in raw_locator_pairs
            if isinstance(item, dict)
            for key in ("male_feature", "female_feature")
            if isinstance(item.get(key), str)
        ]
        if len(locator_pair_features) != len(set(locator_pair_features)):
            errors.append(f"{path}.locator_pairs cannot reuse locating features")
        for index, item in enumerate(raw_locator_pairs):
            if not isinstance(item, dict):
                errors.append(f"{path}.locator_pairs[{index}] must be an object")
                continue
            for key in ("id", "male_feature", "female_feature"):
                if not _valid_id(item.get(key)):
                    errors.append(f"{path}.locator_pairs[{index}].{key} is invalid")
        intent_locator_pairs = {
            item.get("id"): item
            for item in raw_locator_pairs
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        scene_locator_ids = {
            item
            for item in scene_interface.get("locatorInterfaceIds", [])
            if isinstance(item, str)
        }
        if set(intent_locator_pairs) != scene_locator_ids:
            errors.append(
                f"{path} locator interface IDs must match immutable intent: "
                f"{sorted(intent_locator_pairs)}"
            )
        else:
            for locator_id, target in intent_locator_pairs.items():
                locator = all_scene_by_id.get(locator_id)
                if not isinstance(locator, dict):
                    continue
                for endpoint_name, key in (
                    ("male", "male_feature"),
                    ("female", "female_feature"),
                ):
                    endpoint = locator.get(endpoint_name)
                    observed = (
                        endpoint.get("featureId")
                        if isinstance(endpoint, dict)
                        else None
                    )
                    if observed != target.get(key):
                        errors.append(
                            f"{path}.locator_pairs[{locator_id}].{key} must match "
                            f"immutable intent value {target.get(key)!r}"
                        )

        raw_intent_fasteners = fastening.get("fasteners")
        raw_intent_fasteners = (
            raw_intent_fasteners if isinstance(raw_intent_fasteners, list) else []
        )
        intent_fastener_ids = [
            item.get("id")
            for item in raw_intent_fasteners
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if len(intent_fastener_ids) != len(set(intent_fastener_ids)):
            errors.append(f"{path}.fasteners ids must be unique")
        intent_fasteners = {
            item.get("id"): item
            for item in raw_intent_fasteners
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        scene_fasteners = {
            item.get("id"): item
            for item in scene_interface.get("fasteners", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if set(intent_fasteners) != set(scene_fasteners):
            errors.append(
                f"{path} fastener IDs must match immutable intent: "
                f"{sorted(intent_fasteners)}"
            )
            continue
        for fastener_id, target in intent_fasteners.items():
            implementation = scene_fasteners[fastener_id]
            cover = implementation.get("cover")
            receiver = implementation.get("receiver")
            cover = cover if isinstance(cover, dict) else {}
            receiver = receiver if isinstance(receiver, dict) else {}
            expected_features = {
                "clearance_feature": cover.get("featureId"),
                "pilot_feature": receiver.get("featureId"),
                "boss_feature": receiver.get("bossFeatureId"),
            }
            for key, observed in expected_features.items():
                if target.get(key) != observed:
                    errors.append(
                        f"{path}.fasteners[{fastener_id}].{key} must match "
                        f"immutable intent value {target.get(key)!r}"
                    )
            comparisons = {
                "screw_family": (
                    fastening.get("screw_family"),
                    implementation.get("screwFamily"),
                ),
                "nominal_diameter_mm": (
                    fastening.get("nominal_diameter_mm"),
                    implementation.get("nominalDiameterMm"),
                ),
                "clearance_diameter_mm": (
                    fastening.get("clearance_diameter_mm"),
                    cover.get("diameterMm"),
                ),
                "pilot_diameter_mm": (
                    fastening.get("pilot_diameter_mm"),
                    receiver.get("diameterMm"),
                ),
                "boss_outer_diameter_mm": (
                    fastening.get("boss_outer_diameter_mm"),
                    receiver.get("bossOuterDiameterMm"),
                ),
                "engagement_mm": (
                    intent_interface.get("engagement_mm"),
                    receiver.get("engagementMm"),
                ),
                "closed_end_mm": (
                    fastening.get("closed_end_mm"),
                    receiver.get("closedEndMm"),
                ),
                "cutter_overshoot_mm": (
                    fastening.get("cutter_overshoot_mm"),
                    implementation.get("cutterOvershootMm"),
                ),
                "cover_thickness_mm": (
                    fastening.get("cover_thickness_mm"),
                    cover.get("thicknessMm"),
                ),
                "pilot_tip_clearance_mm": (
                    fastening.get("pilot_tip_clearance_mm"),
                    receiver.get("tipClearanceMm"),
                ),
                "minimum_boss_wall_mm": (
                    fastening.get("minimum_boss_wall_mm"),
                    receiver.get("minimumBossWallMm"),
                ),
                "minimum_root_embed_mm": (
                    fastening.get("minimum_root_embed_mm"),
                    receiver.get("minimumRootEmbedMm"),
                ),
                "head_recess_diameter_mm": (
                    fastening.get("head_recess_diameter_mm"),
                    cover.get("headRecessDiameterMm"),
                ),
                "head_recess_depth_mm": (
                    fastening.get("head_recess_depth_mm"),
                    cover.get("headRecessDepthMm"),
                ),
                "minimum_cover_land_mm": (
                    fastening.get("minimum_cover_land_mm"),
                    cover.get("minimumResidualWallMm"),
                ),
            }
            for name, (expected, observed) in comparisons.items():
                if _number(expected) and _number(observed):
                    matches = float(expected) == float(observed)
                else:
                    matches = expected == observed
                if not matches:
                    errors.append(
                        f"{path}.fasteners[{fastener_id}].{name} must match "
                        f"immutable intent value {expected!r}"
                    )


def validate(data: dict, base_dir: Path | None = None) -> list[str]:
    """Return actionable validation errors for a semantic scene document."""

    if not isinstance(data, dict):
        return ["scene must contain a JSON object"]
    errors: list[str] = []
    if data.get("schema") != SCENE_SCHEMA:
        errors.append(f"schema must be {SCENE_SCHEMA}")
    revision = data.get("revision")
    if not isinstance(revision, str) or not REVISION_PATTERN.fullmatch(revision):
        errors.append("revision must be a stable non-empty token")
        revision = ""
    if data.get("units") != "mm":
        errors.append("units must be mm")
    coordinate_system = data.get("coordinateSystem")
    if not isinstance(coordinate_system, dict):
        errors.append("coordinateSystem must be an object")
    else:
        if coordinate_system.get("handedness") != "right":
            errors.append("coordinateSystem.handedness must be right")
        if coordinate_system.get("up") != "Z":
            errors.append("coordinateSystem.up must be Z")

    for field in sorted(INTENT_ONLY_FIELDS.intersection(data)):
        errors.append(
            f"{field} belongs in the immutable intent contract, not the mutable scene graph"
        )
    intent_data = _validate_intent_ref(data.get("intentRef"), base_dir, errors)

    materials = data.get("materials", [])
    material_ids: list[str] = []
    if not isinstance(materials, list):
        errors.append("materials must be a list")
    else:
        for index, material in enumerate(materials):
            path = f"materials[{index}]"
            if not isinstance(material, dict):
                errors.append(f"{path} must be an object")
                continue
            material_id = material.get("id")
            if not _valid_id(material_id):
                errors.append(f"{path}.id is invalid")
            else:
                material_ids.append(material_id)
            color = material.get("color")
            if not isinstance(color, str) or not HEX_COLOR_PATTERN.fullmatch(color):
                errors.append(f"{path}.color must be #RRGGBB")
        if len(material_ids) != len(set(material_ids)):
            errors.append("material ids must be unique")

    parts = data.get("parts")
    part_ids: list[str] = []
    part_by_id: dict[str, dict] = {}
    if not isinstance(parts, list) or not parts:
        errors.append("parts must be a non-empty list")
    else:
        for index, part in enumerate(parts):
            path = f"parts[{index}]"
            if not isinstance(part, dict):
                errors.append(f"{path} must be an object")
                continue
            part_id = part.get("id")
            if not _valid_id(part_id):
                errors.append(f"{path}.id is invalid")
            else:
                part_ids.append(part_id)
                part_by_id[part_id] = part
            if part.get("representationMaster") not in REPRESENTATION_MASTERS:
                errors.append(
                    f"{path}.representationMaster must be brep or mesh"
                )
            representation_master = part.get("representationMaster")
            material_id = part.get("materialId")
            if material_id is not None and material_id not in material_ids:
                errors.append(f"{path}.materialId references an unknown material")
            color_regions = part.get("colorRegions", [])
            region_ids: list[str] = []
            if not isinstance(color_regions, list):
                errors.append(f"{path}.colorRegions must be a list")
            else:
                if color_regions and representation_master != "mesh":
                    errors.append(
                        f"{path}.colorRegions is only supported for mesh-master parts"
                    )
                for region_index, region in enumerate(color_regions):
                    region_path = f"{path}.colorRegions[{region_index}]"
                    if not isinstance(region, dict):
                        errors.append(f"{region_path} must be an object")
                        continue
                    region_id = region.get("id")
                    if not _valid_id(region_id):
                        errors.append(f"{region_path}.id is invalid")
                    else:
                        region_ids.append(region_id)
                    if region.get("materialId") not in material_ids:
                        errors.append(
                            f"{region_path}.materialId references an unknown material"
                        )
                    _validate_source_mesh_spec(
                        region.get("sourceMesh"),
                        f"{region_path}.sourceMesh",
                        errors,
                    )
                if len(region_ids) != len(set(region_ids)):
                    errors.append(f"{path}.colorRegions ids must be unique")
            artifacts = part.get("artifacts")
            if artifacts is not None:
                if not isinstance(artifacts, dict):
                    errors.append(f"{path}.artifacts must be an object")
                else:
                    unknown = set(artifacts) - {
                        "masterStep",
                        "physicalGlb",
                        "manufacturingStl",
                    }
                    for key in sorted(unknown):
                        errors.append(f"{path}.artifacts.{key} is unsupported")
                    for key, suffixes, allow_names in (
                        ("masterStep", (".step", ".stp"), False),
                        ("physicalGlb", (".glb", ".gltf"), True),
                        ("manufacturingStl", (".stl",), False),
                    ):
                        if key not in artifacts:
                            continue
                        artifact_path = f"{path}.artifacts.{key}"
                        try:
                            _validate_artifact(
                                artifacts[key],
                                artifact_path,
                                suffixes,
                                revision,
                                allow_node_names=allow_names,
                            )
                        except (TypeError, ValueError) as error:
                            errors.append(str(error))
                        if key == "masterStep":
                            _validate_file_binding(
                                artifacts[key], artifact_path, base_dir, errors
                            )
                        transform = (
                            artifacts[key].get("toCanonicalTransform")
                            if isinstance(artifacts[key], dict)
                            else None
                        )
                        if transform is not None:
                            _validate_transform(
                                transform,
                                f"{artifact_path}.toCanonicalTransform",
                                errors,
                            )
            master_step = (
                artifacts.get("masterStep")
                if isinstance(artifacts, dict)
                else None
            )
            if (
                representation_master == "brep"
                and isinstance(artifacts, dict)
                and any(
                    key in artifacts
                    for key in ("manufacturingStl", "physicalGlb")
                )
                and not isinstance(master_step, dict)
            ):
                errors.append(
                    f"{path}.artifacts.masterStep is required once a brep part is bound"
                )
            if representation_master == "mesh" and isinstance(master_step, dict):
                errors.append(
                    f"{path}.artifacts.masterStep is forbidden for a mesh master"
                )
        if len(part_ids) != len(set(part_ids)):
            errors.append("part ids must be unique")

    intent_parts = (
        intent_physical_part_names(intent_data)
        if isinstance(intent_data, dict)
        else set()
    )
    scene_parts = set(part_ids)
    if intent_parts and scene_parts != intent_parts:
        errors.append(
            "scene parts must exactly match immutable intent physical parts: "
            f"expected {sorted(intent_parts)}, observed {sorted(scene_parts)}"
        )
    intent_feature_owners = (
        intent_feature_owner_map(intent_data)
        if isinstance(intent_data, dict)
        else {}
    )
    hybrid_scene = any(
        part.get("representationMaster") == "mesh"
        for part in part_by_id.values()
    )

    nodes = data.get("nodes")
    node_ids: list[str] = []
    feature_ids: list[str] = []
    physical_features: set[str] = set()
    nodes_by_feature: dict[str, dict] = {}
    physical_parts: set[str] = set()
    display_references: list[tuple[int, str, str | None]] = []
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
    else:
        for index, node in enumerate(nodes):
            path = f"nodes[{index}]"
            if not isinstance(node, dict):
                errors.append(f"{path} must be an object")
                continue
            node_id = node.get("id")
            if not _valid_id(node_id):
                errors.append(f"{path}.id is invalid")
            else:
                node_ids.append(node_id)
            part_id = node.get("partId")
            if not _valid_id(part_id):
                errors.append(f"{path}.partId is invalid")
            elif part_ids and part_id not in part_by_id:
                errors.append(f"{path}.partId references an unknown part")
            feature_id = node.get("featureId")
            if not _valid_feature_id(feature_id):
                errors.append(f"{path}.featureId is invalid")
            else:
                feature_ids.append(feature_id)
                nodes_by_feature[feature_id] = node
            role = node.get("role")
            if role not in ROLES:
                errors.append(f"{path}.role is invalid")
            else:
                expected_operation = ROLE_OPERATIONS[role]
                if node.get("operation") != expected_operation:
                    errors.append(
                        f"{path}.operation must be {expected_operation} for role {role}"
                    )
                if role in {"solid", "separate"} and isinstance(part_id, str):
                    physical_parts.add(part_id)
                if role != "display-only" and isinstance(feature_id, str):
                    physical_features.add(feature_id)
                    expected_owner = intent_feature_owners.get(feature_id)
                    if expected_owner is None:
                        errors.append(
                            f"{path}.featureId is not declared by immutable intent"
                        )
                    elif part_id != expected_owner:
                        errors.append(
                            f"{path}.partId must match immutable intent owner "
                            f"{expected_owner!r} for feature {feature_id!r}"
                        )

            recipe = node.get("recipe")
            if not isinstance(recipe, dict):
                errors.append(f"{path}.recipe must be an object")
            else:
                kind = recipe.get("kind")
                if not isinstance(kind, str) or not TOKEN_PATTERN.fullmatch(kind):
                    errors.append(f"{path}.recipe.kind is invalid")
                if not isinstance(recipe.get("parameters"), dict):
                    errors.append(f"{path}.recipe.parameters must be an object")
                else:
                    _validate_json_value(
                        recipe["parameters"], f"{path}.recipe.parameters", errors
                    )
                if role != "display-only" and kind == "sourceMesh":
                    errors.append(
                        f"{path}.recipe.kind sourceMesh is unsupported for physical "
                        "nodes; bind the authored object as meshGeometry or brepGeometry"
                    )
                if (
                    hybrid_scene
                    and role != "display-only"
                    and kind != SELF_TAPPING_RECIPE_KIND
                ):
                    if kind not in PHYSICAL_GEOMETRY_RECIPE_KINDS:
                        errors.append(
                            f"{path}.recipe.kind must be one of "
                            f"{sorted(PHYSICAL_GEOMETRY_RECIPE_KINDS)} in a hybrid scene"
                        )
                    else:
                        owner = part_by_id.get(part_id)
                        if (
                            isinstance(owner, dict)
                            and owner.get("representationMaster") == "brep"
                            and kind != BREP_GEOMETRY_RECIPE_KIND
                        ):
                            errors.append(
                                f"{path}.recipe.kind must be "
                                f"{BREP_GEOMETRY_RECIPE_KIND} for a BRep-master part"
                            )
                        _validate_bound_geometry(
                            recipe,
                            f"{path}.recipe",
                            base_dir,
                            errors,
                        )

            physical_ref = node.get("physicalFeatureRef")
            if role == "display-only":
                recipe_kind = (
                    recipe.get("kind") if isinstance(recipe, dict) else None
                )
                if physical_ref is not None:
                    if not _valid_feature_id(physical_ref):
                        errors.append(f"{path}.physicalFeatureRef is invalid")
                    else:
                        display_references.append((index, physical_ref, recipe_kind))
                if recipe_kind != DISPLAY_COMPONENT_KIND:
                    errors.append(
                        f"{path}.recipe.kind must be {DISPLAY_COMPONENT_KIND} "
                        "for display-only nodes"
                    )
                else:
                    if physical_ref is None:
                        errors.append(
                            f"{path}.physicalFeatureRef is required for "
                            "recipe.kind displayComponent"
                        )
                    parameters = recipe.get("parameters") if isinstance(recipe, dict) else None
                    source_mesh = (
                        parameters.get("sourceMesh")
                        if isinstance(parameters, dict)
                        else None
                    )
                    if not (
                        (isinstance(source_mesh, str) and source_mesh.strip())
                        or (
                            isinstance(source_mesh, dict)
                            and isinstance(source_mesh.get("path"), str)
                            and source_mesh["path"].strip()
                        )
                    ):
                        errors.append(
                            f"{path}.recipe.parameters.sourceMesh is required for "
                            "recipe.kind displayComponent"
                        )
                    appearance = (
                        parameters.get("appearance")
                        if isinstance(parameters, dict)
                        else None
                    )
                    if not isinstance(appearance, dict):
                        errors.append(
                            f"{path}.recipe.parameters.appearance is required for "
                            "recipe.kind displayComponent"
                        )
                    else:
                        base_color = appearance.get("baseColor")
                        if not isinstance(base_color, str) or not HEX_COLOR_PATTERN.fullmatch(
                            base_color
                        ):
                            errors.append(
                                f"{path}.recipe.parameters.appearance.baseColor "
                                "must be #RRGGBB"
                            )
                        for field in ("metallic", "roughness"):
                            value = appearance.get(field)
                            if value is not None and (
                                not _number(value) or not 0 <= float(value) <= 1
                            ):
                                errors.append(
                                    f"{path}.recipe.parameters.appearance.{field} "
                                    "must be between 0 and 1"
                                )
            elif physical_ref is not None:
                errors.append(
                    f"{path}.physicalFeatureRef is only valid for display-only nodes"
                )

        if len(node_ids) != len(set(node_ids)):
            errors.append("node ids must be unique")
        if len(feature_ids) != len(set(feature_ids)):
            errors.append("node feature ids must be unique")
        for index, feature_ref, recipe_kind in display_references:
            if feature_ref not in physical_features:
                errors.append(
                    f"nodes[{index}].physicalFeatureRef must reference a non-display feature"
                )
            elif recipe_kind == DISPLAY_COMPONENT_KIND:
                referenced = nodes_by_feature[feature_ref]
                if referenced.get("role") != "cutter":
                    errors.append(
                        f"nodes[{index}].physicalFeatureRef for recipe.kind "
                        "displayComponent must reference a cutter"
                    )
            expected_owner = intent_feature_owners.get(feature_ref)
            display_node = nodes[index]
            if expected_owner is None:
                errors.append(
                    f"nodes[{index}].physicalFeatureRef is not declared by immutable intent"
                )
            elif display_node.get("partId") != expected_owner:
                errors.append(
                    f"nodes[{index}].partId must match immutable intent owner "
                    f"{expected_owner!r} for physicalFeatureRef {feature_ref!r}"
                )
        for part_id in part_ids:
            if part_id not in physical_parts:
                errors.append(
                    f"part {part_id} must contain at least one solid or separate node"
                )

    interfaces = data.get("interfaces", [])
    interface_ids: list[str] = []
    if not isinstance(interfaces, list):
        errors.append("interfaces must be a list")
    else:
        for index, interface in enumerate(interfaces):
            path = f"interfaces[{index}]"
            if not isinstance(interface, dict):
                errors.append(f"{path} must be an object")
                continue
            interface_id = interface.get("id")
            if not _valid_id(interface_id):
                errors.append(f"{path}.id is invalid")
            else:
                interface_ids.append(interface_id)
            kind = interface.get("kind")
            if not isinstance(kind, str) or not TOKEN_PATTERN.fullmatch(kind):
                errors.append(f"{path}.kind is invalid")
            elif kind not in connection_kinds():
                errors.append(f"{path}.kind has no registered interface capability")
            if kind == "self-tapping-screw":
                if "male" in interface or "female" in interface:
                    errors.append(
                        f"{path} must reference independent locator interfaces with "
                        "locatorInterfaceIds instead of declaring male/female endpoints"
                    )
                _validate_self_tapping_scene_interface(
                    interface=interface,
                    path=path,
                    nodes_by_feature=nodes_by_feature,
                    errors=errors,
                )
                continue

            endpoints: dict[str, dict] = {}
            dimensions: dict[str, dict[str, float]] = {}
            for endpoint_name in ("male", "female"):
                endpoint = interface.get(endpoint_name)
                endpoint_path = f"{path}.{endpoint_name}"
                if not isinstance(endpoint, dict):
                    errors.append(f"{endpoint_path} must be an object")
                    continue
                endpoints[endpoint_name] = endpoint
                part_id = endpoint.get("partId")
                feature_id = endpoint.get("featureId")
                if part_id not in part_by_id:
                    errors.append(f"{endpoint_path}.partId references an unknown part")
                if feature_id not in nodes_by_feature:
                    errors.append(
                        f"{endpoint_path}.featureId references an unknown scene feature"
                    )
                elif nodes_by_feature[feature_id].get("partId") != part_id:
                    errors.append(
                        f"{endpoint_path}.featureId belongs to a different part"
                    )
                dimensions[endpoint_name] = _dimensions(
                    endpoint.get("dimensionsMm"),
                    f"{endpoint_path}.dimensionsMm",
                    errors,
                )
            if (
                "male" in endpoints
                and "female" in endpoints
                and endpoints["male"].get("partId") == endpoints["female"].get("partId")
            ):
                errors.append(f"{path} must connect two distinct parts")

            capability = (
                capability_for_connection(kind) if isinstance(kind, str) else None
            )
            requires_clearance = bool(
                capability and "clearance" in capability.get("geometryChecks", ())
            )
            female = endpoints.get("female")
            derived = female.get("derivedDimensionsMm") if female else None
            if requires_clearance and (not isinstance(derived, dict) or not derived):
                errors.append(
                    f"{path}.female.derivedDimensionsMm must derive at least one field from male"
                )
            elif derived is not None and not isinstance(derived, dict):
                errors.append(f"{path}.female.derivedDimensionsMm must be an object")
            elif isinstance(derived, dict):
                for field, rule in derived.items():
                    rule_path = f"{path}.female.derivedDimensionsMm.{field}"
                    if not isinstance(field, str) or not TOKEN_PATTERN.fullmatch(field):
                        errors.append(f"{rule_path} field name is invalid")
                        continue
                    if not isinstance(rule, dict):
                        errors.append(f"{rule_path} must be an object")
                        continue
                    source = rule.get("from")
                    expected_source = f"male.{field}"
                    if source != expected_source:
                        errors.append(f"{rule_path}.from must be {expected_source}")
                    offset = rule.get("offsetMm")
                    if not _number(offset):
                        errors.append(f"{rule_path}.offsetMm must be finite")
                        continue
                    male_value = dimensions.get("male", {}).get(field)
                    female_value = dimensions.get("female", {}).get(field)
                    if male_value is None:
                        errors.append(f"{rule_path} references a missing male dimension")
                    if female_value is None:
                        errors.append(f"{rule_path} has no matching female dimension")
                    if male_value is not None and female_value is not None:
                        expected = male_value + float(offset)
                        if abs(female_value - expected) > 1e-6:
                            errors.append(
                                f"{rule_path} expects {expected:g} mm but female "
                                f"declares {female_value:g} mm"
                            )
            if "fasteners" in interface:
                errors.append(
                    f"{path}.fasteners is only valid for kind self-tapping-screw"
                )
            if "locatorInterfaceIds" in interface:
                errors.append(
                    f"{path}.locatorInterfaceIds is only valid for kind self-tapping-screw"
                )
        if len(interface_ids) != len(set(interface_ids)):
            errors.append("interface ids must be unique")

    _validate_interface_intent_alignment(
        intent_data,
        interfaces,
        nodes_by_feature,
        errors,
    )
    _validate_self_tapping_locator_interfaces(interfaces, nodes_by_feature, errors)
    _validate_self_tapping_recipe_nodes(interfaces, nodes, errors)
    _validate_self_tapping_intent_alignment(intent_data, interfaces, errors)

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} scene.json")
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
        "pass": not errors,
        "revision": data.get("revision") if isinstance(data, dict) else None,
        "scene": str(path.resolve()),
        "schema": "semantic-scene-validation/v1",
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
