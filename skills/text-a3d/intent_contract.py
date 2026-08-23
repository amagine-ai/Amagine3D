"""Validate an independent modeling intent contract before geometry exists."""

from __future__ import annotations

import json
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


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("schema") != "evidence-cad-intent/v2":
        errors.append("schema must be evidence-cad-intent/v2")
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
            if not isinstance(item.get("value"), (int, float)) or item["value"] <= 0:
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
        if visual.get("reference_view") not in {"front", "isometric", "side", "top"}:
            errors.append("visual.reference_view is required for visual validation")
        if not isinstance(visual.get("landmarks"), list) or not visual["landmarks"]:
            errors.append("visual.landmarks must be non-empty when visual is required")

    if not isinstance(data.get("assumptions"), list):
        errors.append("assumptions must be a list")
    if not isinstance(data.get("reference_files"), list):
        errors.append("reference_files must be a list")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} intent.json")
        return 2
    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        errors = validate(data)
    except Exception as error:
        errors = [str(error)]
        data = {}
    result = {
        "errors": errors,
        "intent": str(path.resolve()),
        "part": data.get("part"),
        "pass": not errors,
        "schema": "intent-validation/v2",
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
