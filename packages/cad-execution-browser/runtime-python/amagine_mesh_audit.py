"""Deterministic STL integrity and dimensional checks for Amagine3D."""

from __future__ import annotations

import argparse
import json
import sys

import trimesh


def audit_mesh(
    stl_path: str,
    expected_components: int = 1,
    expected_dimensions: dict[str, float] | None = None,
    dimension_tolerance: float = 0.5,
    expected_volume: float | None = None,
    volume_tolerance_percent: float = 10.0,
) -> dict:
    checks: list[dict] = []

    def record(check_id: str, passed: bool, message: str, **values) -> None:
        checks.append({"id": check_id, "passed": bool(passed), "message": message, **values})

    mesh = trimesh.load_mesh(stl_path, force="mesh", process=True)
    face_count = int(len(mesh.faces))
    vertex_count = int(len(mesh.vertices))
    record("loaded", face_count > 0, f"Loaded {face_count} triangles and {vertex_count} vertices")
    watertight = bool(mesh.is_watertight)
    record("watertight", watertight, "Mesh is closed" if watertight else "Mesh has open boundaries")
    winding = bool(mesh.is_winding_consistent)
    record("winding", winding, "Triangle winding is consistent" if winding else "Triangle winding is inconsistent")
    volume = float(mesh.volume) if watertight else None
    record(
        "positive-volume",
        volume is not None and volume > 0.001,
        "Closed mesh has positive volume" if volume is not None and volume > 0.001 else "Positive volume is unavailable",
        actual=volume,
    )
    components = max(len(mesh.split(only_watertight=False)), 1)
    record(
        "component-count",
        components == expected_components,
        f"Found {components} connected components; expected {expected_components}",
        expected=expected_components,
        actual=components,
    )
    extents = [float(value) for value in mesh.bounding_box.extents]
    dimensions = dict(zip(("x", "y", "z"), extents, strict=True))
    for axis, expected in (expected_dimensions or {}).items():
        actual = dimensions[axis]
        record(
            f"dimension-{axis}",
            abs(actual - expected) <= dimension_tolerance,
            f"{axis.upper()} extent is {actual:.3f} mm; expected {expected:.3f} mm",
            expected=expected,
            actual=actual,
            tolerance=dimension_tolerance,
        )
    if expected_volume is not None:
        delta = float("inf") if volume is None else abs(volume - expected_volume) / expected_volume * 100
        record(
            "volume-target",
            delta <= volume_tolerance_percent,
            f"Volume deviation is {delta:.3f} percent",
            expected=expected_volume,
            actual=volume,
            tolerance=volume_tolerance_percent,
        )
    return {
        "passed": all(item["passed"] for item in checks),
        "path": stl_path,
        "dimensions_mm": {axis: round(value, 3) for axis, value in dimensions.items()},
        "volume_mm3": None if volume is None else round(volume, 3),
        "component_count": components,
        "watertight": watertight,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit an STL artifact")
    parser.add_argument("stl")
    parser.add_argument("--expect-x", type=float)
    parser.add_argument("--expect-y", type=float)
    parser.add_argument("--expect-z", type=float)
    parser.add_argument("--dimension-tolerance", type=float, default=0.5)
    parser.add_argument("--expect-volume", type=float)
    parser.add_argument("--volume-tolerance-percent", type=float, default=10.0)
    parser.add_argument("--expect-components", type=int, default=1)
    arguments = parser.parse_args()
    dimensions = {
        axis: value
        for axis in ("x", "y", "z")
        if (value := getattr(arguments, f"expect_{axis}")) is not None
    }
    try:
        result = audit_mesh(
            arguments.stl,
            expected_components=arguments.expect_components,
            expected_dimensions=dimensions,
            dimension_tolerance=arguments.dimension_tolerance,
            expected_volume=arguments.expect_volume,
            volume_tolerance_percent=arguments.volume_tolerance_percent,
        )
    except Exception as exc:
        result = {"passed": False, "path": arguments.stl, "error": str(exc), "checks": []}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
