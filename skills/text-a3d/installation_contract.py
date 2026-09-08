"""Optional, purpose-selected installation evidence; no component defaults."""
from hashlib import sha256
import math
from pathlib import Path
import re


CHECK_KINDS = {"clearance", "insertion", "support", "retention", "passage"}


def validate_requirements(value, path):
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or item not in CHECK_KINDS for item in value
    ):
        return [f"{path} must be a nonempty list from {sorted(CHECK_KINDS)}"]
    return [] if len(value) == len(set(value)) else [f"{path} must be unique"]


def _finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def validate_installations(records, intent, part_ids, base_dir=None):
    """Check required coverage, part ownership and hash-bound witness inputs."""
    errors = []
    targets = {
        feature["id"]: feature
        for feature in (intent or {}).get("features", [])
        if isinstance(feature, dict) and isinstance(feature.get("id"), str)
        and "installation_checks" in feature
    }
    if not isinstance(records, list):
        return ["installationChecks must be a list"]
    seen = set()
    for index, record in enumerate(records):
        path = f"installationChecks[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{path} must be an object")
            continue
        allowed = {"featureId", "envelope", "obstacleParts", "supportParts", "retainerParts",
                   "insertionEnvelope", "passageEnvelope", "passageParts", "withdrawalAxis",
                   "contactProbeMm", "maxOverlapMm3", "freeTravelMm", "stopTravelMm", "supportDirection"}
        if set(record) - allowed:
            errors.append(f"{path} has unknown fields: {sorted(set(record) - allowed)}")
        feature = record.get("featureId")
        if not isinstance(feature, str) or feature not in targets:
            errors.append(f"{path}.featureId must name an intent feature with installation_checks")
            continue
        if feature in seen:
            errors.append(f"{path}.featureId duplicates {feature}")
        seen.add(feature)
        groups = {}
        for field in ("obstacleParts", "supportParts", "retainerParts", "passageParts"):
            names = record.get(field, [])
            if not isinstance(names, list) or any(not isinstance(n, str) or n not in part_ids for n in names):
                errors.append(f"{path}.{field} must reference manufactured part IDs")
                names = []
            if len(names) != len(set(names)):
                errors.append(f"{path}.{field} must be unique")
            groups[field] = names
        receiving_parts = set(sum(groups.values(), []))
        owner = targets[feature].get("part", (intent or {}).get("part"))
        if owner not in receiving_parts:
            errors.append(f"{path} must check its receiving part {owner!r}")
        scope = {"clearance"} if any(groups[k] for k in ("obstacleParts", "supportParts", "retainerParts")) else set()
        if "clearance" in scope and owner not in set(groups["obstacleParts"] + groups["supportParts"] + groups["retainerParts"]):
            errors.append(f"{path} clearance must include receiving part {owner!r}")
        if groups["supportParts"]:
            scope.add("support")
        if groups["retainerParts"]:
            scope.add("retention")
        if "insertionEnvelope" in record and groups["obstacleParts"]:
            scope.add("insertion")
        if "passageEnvelope" in record and groups["passageParts"]:
            scope.add("passage")
        required = targets[feature]["installation_checks"]
        requirement_errors = validate_requirements(required, f"feature {feature}.installation_checks")
        errors.extend(requirement_errors)
        if not requirement_errors:
            missing = set(required) - scope
            if missing:
                errors.append(f"{path} missing required checks: {sorted(missing)}")
        if not scope:
            errors.append(f"{path} declares no geometric checks")
        if bool(groups["passageParts"]) != ("passageEnvelope" in record):
            errors.append(f"{path} passageEnvelope and passageParts must be supplied together")
        if "insertionEnvelope" in record and not groups["obstacleParts"]:
            errors.append(f"{path}.insertionEnvelope needs obstacleParts present during insertion")
        for field in ("envelope", "insertionEnvelope", "passageEnvelope"):
            if field != "envelope" and field not in record:
                continue
            binding = record.get(field)
            if not isinstance(binding, dict) or set(binding) != {"path", "sha256", "scale"}:
                errors.append(f"{path}.{field} must contain path, sha256 and scale")
                continue
            raw = binding.get("path")
            digest = binding.get("sha256")
            if not isinstance(raw, str) or not raw.strip() or Path(raw).suffix.lower() != ".stl":
                errors.append(f"{path}.{field}.path must name a witness STL")
                continue
            if binding.get("scale") != 1 or isinstance(binding.get("scale"), bool):
                errors.append(f"{path}.{field}.scale must equal 1")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"{path}.{field}.sha256 is invalid")
            if base_dir is not None:
                source = Path(base_dir) / raw
                try:
                    if sha256(source.read_bytes()).hexdigest() != digest:
                        errors.append(f"{path}.{field} hash mismatch")
                except OSError as error:
                    errors.append(f"{path}.{field} cannot be read: {error}")
        axis = record.get("withdrawalAxis")
        if not isinstance(axis, list) or len(axis) != 3 or not all(_finite(v) for v in axis) or sum(v*v for v in axis) <= 0:
            errors.append(f"{path}.withdrawalAxis must be a finite nonzero 3-vector")
        if "supportDirection" in record:
            support = record["supportDirection"]
            if not isinstance(support, list) or len(support) != 3 or not all(_finite(v) for v in support) or sum(v*v for v in support) <= 0:
                errors.append(f"{path}.supportDirection must be a finite nonzero 3-vector")
        for field in ("contactProbeMm", "maxOverlapMm3"):
            value = record.get(field)
            if not _finite(value) or value <= 0:
                errors.append(f"{path}.{field} must be finite and positive")
        if groups["retainerParts"]:
            free, stop = record.get("freeTravelMm"), record.get("stopTravelMm")
            if not _finite(free) or not _finite(stop) or not 0 <= free < stop:
                errors.append(f"{path} retention requires 0 <= freeTravelMm < stopTravelMm")
    for feature in sorted(set(targets) - seen):
        errors.append(f"installationChecks missing intent feature {feature!r}")
    return errors
