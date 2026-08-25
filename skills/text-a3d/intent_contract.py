"""Validate an independent modeling intent contract before geometry exists."""

from __future__ import annotations

import json
from hashlib import sha256
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


def validate(data: dict, base_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    if data.get("schema") != "evidence-cad-intent/v3":
        errors.append("schema must be evidence-cad-intent/v3")
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
    if not isinstance(features, list) or not features:
        errors.append("features must be a non-empty list")
    else:
        ids = []
        for index, feature in enumerate(features):
            if not isinstance(feature, dict):
                errors.append(f"features[{index}] must be an object")
                continue
            ids.append(feature.get("id"))
            for key in ("id", "evidence", "acceptance"):
                if not isinstance(feature.get(key), str) or not feature[key].strip():
                    errors.append(f"features[{index}].{key} is required")
        if len(ids) != len(set(ids)):
            errors.append("feature ids must be unique")

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
        "part": data.get("part"),
        "pass": not errors,
        "schema": "intent-validation/v3",
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
