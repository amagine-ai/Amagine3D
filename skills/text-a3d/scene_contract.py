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


SCENE_SCHEMA = "evidence-semantic-scene/v1"
INTENT_SCHEMAS = {"evidence-cad-intent/v4", "evidence-color-intent/v3"}
REPRESENTATION_MASTERS = {"brep", "mesh"}
ROLES = {"solid", "cutter", "separate", "display-only"}
DISPLAY_COMPONENT_KIND = "displayComponent"
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
) -> None:
    if not isinstance(reference, dict):
        errors.append("intentRef must reference the separate immutable intent contract")
        return
    schema = reference.get("schema")
    if schema not in INTENT_SCHEMAS:
        errors.append(f"intentRef.schema must be one of {sorted(INTENT_SCHEMAS)}")
    raw_path = reference.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append("intentRef.path is required")
        return
    digest = reference.get("sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        errors.append("intentRef.sha256 must be a lowercase SHA-256 digest")
    if base_dir is None:
        return

    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    try:
        payload = path.read_bytes()
        intent = json.loads(payload)
    except Exception as error:
        errors.append(f"intentRef cannot be read: {error}")
        return
    if isinstance(digest, str) and SHA256_PATTERN.fullmatch(digest):
        if sha256(payload).hexdigest() != digest:
            errors.append("intentRef.sha256 does not match the referenced intent")
    if not isinstance(intent, dict) or intent.get("schema") != schema:
        errors.append("intentRef.schema does not match the referenced intent")


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
        if coordinate_system.get("up") not in {"Y", "Z"}:
            errors.append("coordinateSystem.up must be Y or Z")

    for field in sorted(INTENT_ONLY_FIELDS.intersection(data)):
        errors.append(
            f"{field} belongs in the immutable intent contract, not the mutable scene graph"
        )
    _validate_intent_ref(data.get("intentRef"), base_dir, errors)

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
            artifacts = part.get("artifacts")
            if artifacts is not None:
                if not isinstance(artifacts, dict):
                    errors.append(f"{path}.artifacts must be an object")
                else:
                    unknown = set(artifacts) - {"physicalGlb", "manufacturingStl"}
                    for key in sorted(unknown):
                        errors.append(f"{path}.artifacts.{key} is unsupported")
                    for key, suffixes, allow_names in (
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
        if len(part_ids) != len(set(part_ids)):
            errors.append("part ids must be unique")

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
                if recipe_kind == DISPLAY_COMPONENT_KIND:
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

            female = endpoints.get("female")
            derived = female.get("derivedDimensionsMm") if female else None
            if not isinstance(derived, dict) or not derived:
                errors.append(
                    f"{path}.female.derivedDimensionsMm must derive at least one field from male"
                )
            else:
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
        if len(interface_ids) != len(set(interface_ids)):
            errors.append("interface ids must be unique")

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
