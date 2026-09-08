"""Deterministic authoring helpers for the canonical intent and scene contracts.

This module deliberately has no intermediate document format.  It writes the
same evidence-cad-intent/v5 and evidence-semantic-scene/v1 JSON documents that
the validators and compilers consume.  The compact inputs only remove fields
whose values are mechanically implied by an explicit semantic decision.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from capability_registry import capability_for_connection, connection_kinds
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
    interface_alignment_issues,
    validate as validate_scene,
)


SCENE_COORDINATE_SYSTEM = {"handedness": "right", "up": "Z"}


class AuthoringError(ValueError):
    """Raised before an invalid or semantically ambiguous document is written."""

    def __init__(
        self, stage: str, errors: Sequence[str], *,
        issues: Sequence[Mapping[str, Any]] = (),
    ):
        self.stage = stage
        self.errors = tuple(str(error) for error in errors)
        self.issues = tuple(deepcopy(dict(issue)) for issue in issues)
        super().__init__(f"{stage} authoring failed: " + "; ".join(self.errors))
        diagnostic_path = os.environ.get("AMAGINE3D_SOURCE_DIAGNOSTICS_PATH")
        run_id = os.environ.get("AMAGINE3D_COMPILE_RUN_ID")
        if os.environ.get("AMAGINE3D_SOURCE_PHASE") == "compile" and diagnostic_path and run_id:
            self._persist_diagnostics()

    def _persist_diagnostics(self) -> None:
        """Preserve complete diagnostics across the compiler subprocess boundary."""
        records = self.issues or tuple({
            "code": "AUTHORING.INVALID", "path": self.stage, "message": message,
        } for message in self.errors)
        issues = [{"check": "authoring", "severity": "error", **item,
                   **({"observed": item["actual"]} if "actual" in item else {})}
                  for item in records]

        def json_value(value: Any) -> Any:
            if isinstance(value, float) and not math.isfinite(value):
                return repr(value)
            if isinstance(value, Mapping):
                return {str(key): json_value(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [json_value(item) for item in value]
            return value

        from cad_diagnostics import write_source_diagnostics

        try:
            write_source_diagnostics({"pass": False, "issues": json_value(issues)})
        except (OSError, TypeError, ValueError) as error:
            # Preserve the original authoring failure if the diagnostic sink is unavailable.
            self.diagnostics_write_error = str(error)


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
    revision: Mapping[str, Any] | None = None,
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

    if revision is not None:
        document["revision"] = deepcopy(dict(revision))

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
    clearances_mm: Mapping[str, float],
    female_dimensions_mm: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Declare a paired scene interface without duplicating owners or dimensions.

    The caller still chooses the interface kind, endpoints, male dimensions, and
    every named dimension clearance.  `write_scene` resolves endpoint owners from immutable
    intent and derives the matching female dimensions and provenance records.
    """

    if kind == "self-tapping-screw":
        raise AuthoringError(
            "scene interface",
            ["self-tapping-screw keeps its existing explicit canonical structure"],
        )
    interface = {
        "id": id,
        "kind": kind,
        "male": {
            "featureId": male_feature,
            "dimensionsMm": deepcopy(dict(male_dimensions_mm)),
        },
        "female": {
            "featureId": female_feature,
            "clearancesMm": deepcopy(dict(clearances_mm)),
        },
    }
    if female_dimensions_mm is not None:
        interface["female"]["dimensionsMm"] = deepcopy(dict(female_dimensions_mm))
    _paired_dimensions(interface, "paired_interface")
    return interface


def _paired_dimensions(
    interface: Mapping[str, Any],
    context: str,
) -> tuple[dict[str, float], dict[str, float], dict[str, dict[str, Any]]]:
    male = interface.get("male")
    female = interface.get("female")
    if not isinstance(male, Mapping) or not isinstance(female, Mapping):
        raise AuthoringError(
            "scene interface",
            [f"{context} requires male and female endpoints"],
        )
    male_dimensions = male.get("dimensionsMm")
    clearances = female.get("clearancesMm")
    capability = capability_for_connection(str(interface.get("kind")))
    if capability is None:
        allowed = sorted(connection_kinds() - {"self-tapping-screw"})
        message = f"{context}.kind must be one of {allowed}; observed {interface.get('kind')!r}"
        raise AuthoringError("scene interface", [message], issues=[{
            "code": "INTERFACE.KIND_INVALID", "path": f"{context}.kind",
            "actual": interface.get("kind"), "expected": allowed, "message": message,
        }])
    requires_clearance = "clearance" in capability.get("geometryChecks", ())
    if requires_clearance and "dimensionsMm" in female:
        raise AuthoringError(
            "scene interface",
            [
                f"{context}.female.dimensionsMm is forbidden; female dimensions "
                "are derived only from male dimensions and clearances"
            ],
        )
    issues: list[dict[str, Any]] = []

    def invalid(path: str, actual: Any, expected: Any, message: str) -> None:
        issues.append({"code": "INTERFACE.DIMENSION_INVALID", "path": path,
                       "actual": actual, "expected": expected, "message": message})

    if not isinstance(male_dimensions, Mapping) or not male_dimensions:
        invalid(f"{context}.male.dimensionsMm", male_dimensions, "non-empty object",
                f"{context}.male.dimensionsMm must be non-empty")
        male_dimensions = {}
    if not isinstance(clearances, Mapping):
        invalid(f"{context}.female.clearancesMm", clearances, "object",
                f"{context}.female.clearancesMm must be an object")
        clearances = {}
    if requires_clearance and not clearances:
        invalid(f"{context}.female.clearancesMm", clearances, "non-empty named dimension deltas",
                f"{context}.female.clearancesMm must be non-empty for this interface capability")
    if not requires_clearance and clearances:
        invalid(f"{context}.female.clearancesMm", clearances, {},
                f"{context}.female.clearancesMm must be empty for surface contact")

    canonical_male: dict[str, float] = {}
    female_dimensions: dict[str, float] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for field, male_value in male_dimensions.items():
        if (
            not isinstance(field, str)
            or not field
            or not isinstance(male_value, (int, float))
            or isinstance(male_value, bool)
            or not math.isfinite(float(male_value))
            or float(male_value) <= 0
        ):
            invalid(f"{context}.male.dimensionsMm.{field}", male_value, "finite positive number",
                    f"{context}.male dimension {field!r} must be finite and positive")
            continue
        canonical_male[field] = float(male_value)
    for field, clearance in clearances.items():
        if field not in canonical_male:
            invalid(f"{context}.female.clearancesMm.{field}", clearance, sorted(canonical_male),
                    f"{context} cannot derive female {field!r} because the male dimension is missing")
            continue
        if (
            not isinstance(clearance, (int, float))
            or isinstance(clearance, bool)
            or not math.isfinite(float(clearance))
            or float(clearance) < 0
        ):
            invalid(f"{context}.female.clearancesMm.{field}", clearance, "finite non-negative number",
                    f"{context} clearance {field!r} must be finite and non-negative")
            continue
        female_dimensions[field] = canonical_male[field] + float(clearance)
        provenance[field] = {
            "from": f"male.{field}",
            "offsetMm": float(clearance),
        }
    if not requires_clearance:
        # Shared profiles are a geometry-driving convenience, never a clearance proof.
        contact_dimensions = female.get("dimensionsMm", canonical_male)
        if not isinstance(contact_dimensions, Mapping) or not contact_dimensions:
            invalid(f"{context}.female.dimensionsMm", contact_dimensions, "non-empty object",
                    f"{context}.female.dimensionsMm must be non-empty for surface contact")
        else:
            for field, value in contact_dimensions.items():
                if not isinstance(field, str) or not field or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or value <= 0:
                    invalid(f"{context}.female.dimensionsMm.{field}", value, "finite positive number",
                            f"{context}.female dimension {field!r} must be finite and positive")
                else:
                    female_dimensions[field] = float(value)
        provenance = {}
    if issues:
        raise AuthoringError("scene interface", [item["message"] for item in issues], issues=issues)
    return canonical_male, female_dimensions, provenance


def paired_dimensions(interface: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Return geometry-driving endpoint dimensions from one paired declaration."""

    male, female, _ = _paired_dimensions(interface, "paired_interface")
    return {"female": female, "male": male}


def _expand_paired_interfaces(
    raw_interfaces: Sequence[Mapping[str, Any]],
    owners: Mapping[str, str],
) -> list[dict[str, Any]]:
    interfaces = _records(raw_interfaces, "paired_interfaces")
    expanded: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, interface in enumerate(interfaces):
        context = f"paired_interfaces[{index}]"
        canonical: dict[str, Any] = {"id": interface.get("id"), "kind": interface.get("kind")}
        endpoint_issues = []
        for endpoint_name in ("male", "female"):
            raw_endpoint = interface.get(endpoint_name)
            endpoint = deepcopy(dict(raw_endpoint)) if isinstance(raw_endpoint, Mapping) else {}
            feature_id = endpoint.get("featureId")
            owner = owners.get(feature_id) if isinstance(feature_id, str) else None
            if owner is None:
                path = f"{context}.{endpoint_name}.featureId"
                endpoint_issues.append({
                    "code": "INTERFACE.ENDPOINT_FEATURE_REQUIRED", "path": path,
                    "actual": feature_id, "expected": sorted(owners),
                    "message": f"{path} must resolve to immutable intent ownership; observed {feature_id!r}; allowed features: {sorted(owners)}",
                })
                continue
            declared_owner = endpoint.get("partId", owner)
            if declared_owner != owner:
                path = f"{context}.{endpoint_name}.partId"
                endpoint_issues.append({
                    "code": "INTERFACE.ENDPOINT_OWNER_MISMATCH", "path": path,
                    "actual": declared_owner, "expected": owner,
                    "message": f"{path} conflicts with immutable intent ownership: expected {owner!r}, observed {declared_owner!r}",
                })
            endpoint["partId"] = owner
            canonical[endpoint_name] = endpoint
        if endpoint_issues:
            issues.extend(endpoint_issues)
            continue
        try:
            if canonical["kind"] == "self-tapping-screw":
                raise AuthoringError("scene interface", [f"{context} cannot abbreviate self-tapping-screw semantics"])
            male_dimensions, female_dimensions, derived = _paired_dimensions(canonical, context)
        except AuthoringError as error:
            issues.extend(error.issues or ({
                "code": "INTERFACE.DECLARATION_INVALID", "path": context,
                "message": message,
            } for message in error.errors))
            continue
        canonical["male"]["dimensionsMm"] = male_dimensions
        female = canonical["female"]
        female.pop("clearancesMm")
        female["dimensionsMm"] = female_dimensions
        female["derivedDimensionsMm"] = derived
        expanded.append(canonical)
    if issues:
        raise AuthoringError("scene interface", [item["message"] for item in issues], issues=issues)
    return expanded


def _validate_interface_alignment(
    intent: Mapping[str, Any],
    interfaces: Sequence[Mapping[str, Any]],
) -> None:
    """Keep all independent field diagnostics in one authoring failure."""
    issues = interface_alignment_issues(dict(intent), list(interfaces))
    if issues:
        raise AuthoringError(
            "scene interface", [issue["message"] for issue in issues], issues=issues
        )


def _intent_materials(intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for region in intent.get("color_regions", []):
        if not isinstance(region, Mapping):
            continue
        record: dict[str, Any] = {
            "id": region.get("name"),
            "color": str(region.get("hex", "")).upper(),
        }
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
                    "explicitly as brep"
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
                    [f"node {node.get('id')!r} must choose a supported role from {sorted(ROLE_OPERATIONS)}; observed {role!r}"],
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
    installation_checks: Sequence[Mapping[str, Any]] = (),
    revision: str | None = None,
) -> dict[str, Any]:
    """Write one validated canonical semantic scene directly.

    Part nesting supplies node ownership; node role supplies operation.  Paired
    interfaces derive endpoint ownership and female dimensions from explicit
    named clearances. Parts use BRep masters; recipes, fit clearances, display
    sources, and optional raw interface structures remain caller decisions.
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
    declared_interfaces = _records(paired_interfaces, "paired_interfaces")
    raw_interfaces = _records(interfaces, "interfaces")
    for interface in declared_interfaces:
        for endpoint_name in ("male", "female"):
            endpoint = interface.get(endpoint_name)
            if isinstance(endpoint, dict):
                feature_id = endpoint.get("featureId")
                if isinstance(feature_id, str) and feature_id in owners:
                    endpoint.setdefault("partId", owners[feature_id])
    declaration_issues = interface_alignment_issues(
        intent, declared_interfaces + raw_interfaces, check_dimensions=False
    )
    if declaration_issues:
        raise AuthoringError("scene interface", [item["message"] for item in declaration_issues],
                             issues=declaration_issues)
    canonical_parts, nodes = _expand_scene_parts(parts, intent)
    canonical_interfaces = _expand_paired_interfaces(declared_interfaces, owners)
    canonical_interfaces.extend(raw_interfaces)
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
    if installation_checks:
        body["installationChecks"] = _records(installation_checks, "installation_checks")
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
