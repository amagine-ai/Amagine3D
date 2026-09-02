from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
PROFILE = SKILL / "examples" / "bambu-a1-mini-0.4-standard.example.json"
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


def _default_manufacturing(feature_owners: dict[str, str]) -> dict[str, Any]:
    parts = sorted(set(feature_owners.values()))
    if len(parts) == 1:
        return {"mode": "single-part"}
    if len(parts) < 2:
        raise ValueError("a fixture intent must own at least one physical feature")
    first, second, *remaining = parts
    features_by_part = {
        part: [feature for feature, owner in feature_owners.items() if owner == part]
        for part in parts
    }
    return {
        "mode": "multipart",
        "parts": [
            {
                "name": part,
                "role": f"physical {part} test part",
                "acceptance": f"{part} remains a separately identified part",
                **({"installation": "loose"} if part in remaining else {}),
            }
            for part in parts
        ],
        "interfaces": [
            {
                "id": f"{first}-{second}-fixture",
                "between": [first, second],
                "connection": "glue-face",
                "assembly_axis": "+Z",
                "clearance_mm": 0.0,
                "engagement_mm": 1.0,
                "features": [features_by_part[first][0], features_by_part[second][0]],
                "acceptance": "fixture parts retain their declared ownership",
            }
        ],
    }


def write_intent(
    root: Path,
    *,
    part: str,
    feature_owners: dict[str, str],
    manufacturing: dict[str, Any] | None = None,
    color_regions: list[dict[str, Any]] | None = None,
    dimensions_mm: tuple[float, float, float] = (40.0, 30.0, 20.0),
    filename: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    manufacturing = manufacturing or _default_manufacturing(feature_owners)
    intent: dict[str, Any] = {
        "schema": "evidence-cad-intent/v4",
        "part": part,
        "task_mode": "specification",
        "representation": "full-3d",
        "coordinate_system": COORDINATE_SYSTEM,
        "dimensions_mm": {
            axis: {"value": value, "source": "inferred", "confidence": "medium"}
            for axis, value in zip("xyz", dimensions_mm, strict=True)
        },
        "features": [
            {
                "id": feature_id,
                "part": owner,
                "kind": "detail",
                "evidence": f"fixture requires {feature_id}",
                "acceptance": f"scene binds {feature_id} to {owner}",
            }
            for feature_id, owner in feature_owners.items()
        ],
        "manufacturing": manufacturing,
        "printability": {
            "profile": {
                "path": str(PROFILE),
                "sha256": sha256(PROFILE.read_bytes()).hexdigest(),
            },
            "build_axis": "+Z",
            "bed_contact": "z-min",
            "support_policy": "support-free",
            "minimum_wall_target_mm": 0.9,
            "critical_features": list(feature_owners),
        },
        "visual": {
            "required": True,
            "reference_view": "isometric",
            "landmarks": ["all declared physical features remain identifiable"],
        },
        "assumptions": ["fixture dimensions are test-only evidence"],
        "reference_files": [],
    }
    if color_regions is not None:
        intent["color_regions"] = color_regions
        intent["palette_reduction"] = {
            "applied": False,
            "reason": "fixture preserves every declared region",
        }
        intent["printability"]["print_package_mode"] = (
            "co_print_body"
            if manufacturing.get("mode") == "single-part"
            else "separate_parts"
        )
    path = root / (filename or f"{part}_intent.json")
    path.write_text(json.dumps(intent), encoding="utf-8")
    return path, intent


def intent_ref(path: Path, *, relative_to: Path | None = None) -> dict[str, str]:
    reference_path = (
        str(path.relative_to(relative_to)) if relative_to is not None else str(path)
    )
    return {
        "path": reference_path,
        "schema": "evidence-cad-intent/v4",
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def bind_scene_intent(
    root: Path,
    scene: dict[str, Any],
    *,
    part: str = "device",
    manufacturing: dict[str, Any] | None = None,
    color_regions: list[dict[str, Any]] | None = None,
    dimensions_mm: tuple[float, float, float] = (40.0, 30.0, 20.0),
) -> dict[str, Any]:
    feature_owners = {
        node["featureId"]: node["partId"]
        for node in scene.get("nodes", [])
        if isinstance(node, dict)
        and node.get("role") != "display-only"
        and isinstance(node.get("featureId"), str)
        and isinstance(node.get("partId"), str)
    }
    path, _ = write_intent(
        root,
        part=part,
        feature_owners=feature_owners,
        manufacturing=manufacturing,
        color_regions=color_regions,
        dimensions_mm=dimensions_mm,
    )
    scene["intentRef"] = intent_ref(path, relative_to=root)
    return scene
