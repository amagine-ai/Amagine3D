"""Validate a color-aware evidence contract before region geometry exists."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("schema") != "evidence-color-intent/v2":
        errors.append("schema must be evidence-color-intent/v2")
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", str(data.get("part", ""))):
        errors.append("part must be a lowercase filename-safe slug")
    if data.get("representation") not in {
        "full-3d", "orthographic-solid", "relief", "surface-led",
    }:
        errors.append("representation is invalid")

    dimensions = data.get("dimensions_mm")
    if not isinstance(dimensions, dict):
        errors.append("dimensions_mm is required")
    else:
        for axis in "xyz":
            item = dimensions.get(axis)
            if not isinstance(item, dict) or not isinstance(item.get("value"), (int, float)):
                errors.append(f"dimensions_mm.{axis}.value is required")
            elif item["value"] <= 0:
                errors.append(f"dimensions_mm.{axis}.value must be positive")

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
        if len(names) != len(set(names)):
            errors.append("color region names must be unique")

    visual = data.get("visual")
    if not isinstance(visual, dict) or visual.get("required") is not True:
        errors.append("visual.required must be true for color generation")
    elif not isinstance(visual.get("landmarks"), list) or not visual["landmarks"]:
        errors.append("visual.landmarks must be non-empty")
    if not isinstance(data.get("palette_reduction"), dict):
        errors.append("palette_reduction decision is required")
    if not isinstance(data.get("assumptions"), list):
        errors.append("assumptions must be a list")
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
        data, errors = {}, [str(error)]
    result = {
        "errors": errors,
        "intent": str(path.resolve()),
        "part": data.get("part"),
        "pass": not errors,
        "schema": "color-intent-validation/v2",
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
