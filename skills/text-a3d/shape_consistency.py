"""Compare display and manufacturing meshes without becoming a CAD compiler.

The checker accepts either two mesh files directly or a semantic scene manifest
whose parts mark a physical GLB and manufacturing STL.  It checks revision and
scale metadata, dimensions, and sampled bidirectional point-to-surface distance.
It deliberately does not repair, rescale, boolean, or otherwise mutate geometry.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import trimesh

from scene_contract import validate as validate_scene


REPORT_SCHEMA = "evidence-shape-consistency/v1"
MANIFEST_REPORT_SCHEMA = "evidence-shape-consistency-manifest/v1"


class ConsistencyError(RuntimeError):
    """Raised for an unusable manifest or mesh artifact."""


def _vector(values: Iterable[float]) -> list[float]:
    return [round(float(value), 9) for value in values]


def _scene_mesh(
    scene: trimesh.Scene,
    *,
    node_names: list[str] | None,
    context: str,
) -> trimesh.Trimesh:
    available = list(scene.graph.nodes_geometry)
    selected = available if node_names is None else node_names
    if not selected:
        raise ConsistencyError(f"{context} contains no geometry nodes")
    missing = sorted(set(selected) - set(available))
    if missing:
        raise ConsistencyError(
            f"{context} is missing requested GLB nodes: {', '.join(missing)}"
        )

    meshes: list[trimesh.Trimesh] = []
    for node_name in selected:
        transform, geometry_name = scene.graph[node_name]
        if geometry_name is None or geometry_name not in scene.geometry:
            raise ConsistencyError(f"{context} node {node_name!r} has no mesh")
        mesh = scene.geometry[geometry_name].copy()
        if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
            raise ConsistencyError(f"{context} node {node_name!r} is not a mesh")
        mesh.apply_transform(np.asarray(transform, dtype=float))
        meshes.append(mesh)
    return trimesh.util.concatenate(meshes)


def load_artifact(spec: dict[str, Any], base_dir: Path) -> trimesh.Trimesh:
    """Load one GLB/STL artifact and place it in the canonical scene frame."""

    raw_path = spec.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConsistencyError("artifact.path is required")
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        raise ConsistencyError(f"artifact does not exist: {path}")

    try:
        loaded = trimesh.load(path, force="scene", process=False)
    except Exception as error:
        raise ConsistencyError(f"cannot load mesh artifact {path}: {error}") from error
    if isinstance(loaded, trimesh.Trimesh):
        mesh = loaded
    elif isinstance(loaded, trimesh.Scene):
        names = spec.get("nodeNames")
        mesh = _scene_mesh(
            loaded,
            node_names=list(names) if isinstance(names, list) else None,
            context=str(path),
        )
    else:
        raise ConsistencyError(f"artifact is not a supported mesh or scene: {path}")

    transform = spec.get("toCanonicalTransform")
    if transform is not None:
        matrix = np.asarray(transform, dtype=float)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ConsistencyError(
                f"artifact toCanonicalTransform must be a finite 4x4 matrix: {path}"
            )
        mesh.apply_transform(matrix)
    if mesh.is_empty or len(mesh.faces) == 0:
        raise ConsistencyError(f"artifact contains no triangle faces: {path}")
    if not np.isfinite(mesh.vertices).all():
        raise ConsistencyError(f"artifact contains non-finite vertices: {path}")
    # Binary STL commonly reloads as independent triangle corners.  Weld only
    # coincident vertices so topology summaries (body count, watertightness,
    # volume) describe the same unchanged surface used by distance checks.
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    return mesh


def _mesh_summary(mesh: trimesh.Trimesh, label: str) -> dict[str, Any]:
    bounds = np.asarray(mesh.bounds, dtype=float)
    size = bounds[1] - bounds[0]
    body_count = max(len(mesh.split(only_watertight=False)), 1)
    volume = float(mesh.volume) if mesh.is_watertight else None
    return {
        "bodyCount": body_count,
        "boundsMm": {
            "max": _vector(bounds[1]),
            "min": _vector(bounds[0]),
            "size": _vector(size),
        },
        "faces": int(len(mesh.faces)),
        "label": label,
        "volumeMm3": round(volume, 9) if volume is not None else None,
        "vertices": int(len(mesh.vertices)),
        "watertight": bool(mesh.is_watertight),
    }


def _closest_distances(
    target: trimesh.Trimesh,
    points: np.ndarray,
) -> np.ndarray:
    try:
        _, distances, _ = trimesh.proximity.closest_point(target, points)
    except (ImportError, ModuleNotFoundError):
        # The repository pins rtree, but the naive fallback keeps the utility
        # usable for small test fixtures and minimal environments.
        _, distances, _ = trimesh.proximity.closest_point_naive(target, points)
    distances = np.asarray(distances, dtype=float)
    if not np.isfinite(distances).all():
        raise ConsistencyError("surface-distance query returned non-finite values")
    return distances


def _distance_summary(distances: np.ndarray) -> dict[str, float]:
    return {
        "maxMm": round(float(np.max(distances)), 9),
        "meanMm": round(float(np.mean(distances)), 9),
        "p95Mm": round(float(np.percentile(distances, 95)), 9),
        "p99Mm": round(float(np.percentile(distances, 99)), 9),
        "rmsMm": round(float(math.sqrt(np.mean(np.square(distances)))), 9),
    }


def compare_meshes(
    mesh_a: trimesh.Trimesh,
    mesh_b: trimesh.Trimesh,
    *,
    label_a: str = "display",
    label_b: str = "manufacturing",
    revision_a: str | None = None,
    revision_b: str | None = None,
    scale_a: float = 1.0,
    scale_b: float = 1.0,
    dimension_tolerance_mm: float = 0.1,
    surface_p99_tolerance_mm: float = 0.1,
    surface_max_tolerance_mm: float = 0.25,
    sample_count: int = 5000,
    seed: int = 17,
) -> dict[str, Any]:
    """Compare two already-canonicalized triangle meshes."""

    if sample_count < 32:
        raise ConsistencyError("sample_count must be at least 32")
    for name, value in (
        ("dimension_tolerance_mm", dimension_tolerance_mm),
        ("surface_p99_tolerance_mm", surface_p99_tolerance_mm),
        ("surface_max_tolerance_mm", surface_max_tolerance_mm),
    ):
        if not math.isfinite(value) or value < 0:
            raise ConsistencyError(f"{name} must be finite and non-negative")
    if mesh_a.is_empty or mesh_b.is_empty:
        raise ConsistencyError("both meshes must be non-empty")
    if mesh_a.area <= 0 or mesh_b.area <= 0:
        raise ConsistencyError("both meshes must have positive surface area")

    summary_a = _mesh_summary(mesh_a, label_a)
    summary_b = _mesh_summary(mesh_b, label_b)
    size_a = np.asarray(summary_a["boundsMm"]["size"], dtype=float)
    size_b = np.asarray(summary_b["boundsMm"]["size"], dtype=float)
    dimension_delta = np.abs(size_a - size_b)

    points_a, _ = trimesh.sample.sample_surface(mesh_a, sample_count, seed=seed)
    points_b, _ = trimesh.sample.sample_surface(mesh_b, sample_count, seed=seed + 1)
    a_to_b = _closest_distances(mesh_b, points_a)
    b_to_a = _closest_distances(mesh_a, points_b)
    combined = np.concatenate((a_to_b, b_to_a))
    distance = {
        "aToB": _distance_summary(a_to_b),
        "bToA": _distance_summary(b_to_a),
        "bidirectional": _distance_summary(combined),
        "sampleCountPerDirection": sample_count,
        "seed": seed,
    }

    revision_checked = revision_a is not None or revision_b is not None
    revision_match = (
        revision_a is not None
        and revision_b is not None
        and revision_a == revision_b
    )
    scale_values_valid = all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in (scale_a, scale_b)
    )
    scale_match = bool(
        scale_values_valid
        and math.isclose(float(scale_a), 1.0, abs_tol=1e-12)
        and math.isclose(float(scale_b), 1.0, abs_tol=1e-12)
    )
    checks = [
        {
            "name": "revision_match",
            "pass": revision_match if revision_checked else None,
            "observed": {"a": revision_a, "b": revision_b},
            "required": revision_checked,
        },
        {
            "name": "unit_scale",
            "pass": scale_match,
            "observed": {"a": scale_a, "b": scale_b},
            "target": 1.0,
        },
        {
            "name": "dimensions",
            "pass": bool(float(np.max(dimension_delta)) <= dimension_tolerance_mm),
            "observedDeltaMm": _vector(dimension_delta),
            "toleranceMm": dimension_tolerance_mm,
        },
        {
            "name": "surface_p99",
            "pass": bool(
                distance["bidirectional"]["p99Mm"] <= surface_p99_tolerance_mm
            ),
            "observedMm": distance["bidirectional"]["p99Mm"],
            "toleranceMm": surface_p99_tolerance_mm,
        },
        {
            "name": "surface_max",
            "pass": bool(
                distance["bidirectional"]["maxMm"] <= surface_max_tolerance_mm
            ),
            "observedMm": distance["bidirectional"]["maxMm"],
            "toleranceMm": surface_max_tolerance_mm,
        },
    ]
    required_checks = [item for item in checks if item.get("required", True)]
    passed = all(item["pass"] is True for item in required_checks)

    volume_delta = None
    volume_delta_percent = None
    if summary_a["volumeMm3"] is not None and summary_b["volumeMm3"] is not None:
        volume_delta = abs(summary_a["volumeMm3"] - summary_b["volumeMm3"])
        denominator = max(
            abs(summary_a["volumeMm3"]), abs(summary_b["volumeMm3"]), 1e-12
        )
        volume_delta_percent = volume_delta / denominator * 100.0

    return {
        "checks": checks,
        "dimensionDeltaMm": _vector(dimension_delta),
        "meshes": {"a": summary_a, "b": summary_b},
        "pass": passed,
        "revision": {
            "a": revision_a,
            "b": revision_b,
            "checked": revision_checked,
            "match": revision_match if revision_checked else None,
        },
        "scale": {"a": scale_a, "b": scale_b, "pass": scale_match},
        "schema": REPORT_SCHEMA,
        "surfaceDistance": distance,
        "volumeDeltaMm3": round(volume_delta, 9) if volume_delta is not None else None,
        "volumeDeltaPercent": (
            round(volume_delta_percent, 9)
            if volume_delta_percent is not None
            else None
        ),
    }


def compare_manifest(
    manifest: dict[str, Any],
    *,
    base_dir: Path,
    part_ids: list[str] | None = None,
    dimension_tolerance_mm: float = 0.1,
    surface_p99_tolerance_mm: float = 0.1,
    surface_max_tolerance_mm: float = 0.25,
    sample_count: int = 5000,
    seed: int = 17,
) -> dict[str, Any]:
    """Compare every requested part with marked physical GLB and STL artifacts."""

    errors = validate_scene(manifest, base_dir)
    if errors:
        raise ConsistencyError("invalid semantic scene: " + "; ".join(errors))
    parts = {part["id"]: part for part in manifest["parts"]}
    if part_ids is not None:
        missing = sorted(set(part_ids) - set(parts))
        if missing:
            raise ConsistencyError(f"unknown requested parts: {', '.join(missing)}")
        selected_ids = part_ids
    else:
        selected_ids = list(parts)

    results: dict[str, Any] = {}
    skipped: list[str] = []
    for index, part_id in enumerate(selected_ids):
        part = parts[part_id]
        artifacts = part.get("artifacts")
        if artifacts is None:
            skipped.append(part_id)
            continue
        if not isinstance(artifacts, dict) or not {
            "physicalGlb",
            "manufacturingStl",
        }.issubset(artifacts):
            raise ConsistencyError(
                f"part {part_id} must mark both physicalGlb and manufacturingStl"
            )
        physical = artifacts["physicalGlb"]
        manufacturing = artifacts["manufacturingStl"]
        mesh_a = load_artifact(physical, base_dir)
        mesh_b = load_artifact(manufacturing, base_dir)
        result = compare_meshes(
            mesh_a,
            mesh_b,
            label_a=f"{part_id}:physicalGlb",
            label_b=f"{part_id}:manufacturingStl",
            revision_a=physical.get("revision"),
            revision_b=manufacturing.get("revision"),
            scale_a=physical.get("scale", 1.0),
            scale_b=manufacturing.get("scale", 1.0),
            dimension_tolerance_mm=dimension_tolerance_mm,
            surface_p99_tolerance_mm=surface_p99_tolerance_mm,
            surface_max_tolerance_mm=surface_max_tolerance_mm,
            sample_count=sample_count,
            seed=seed + index * 2,
        )
        result["representationMaster"] = part["representationMaster"]
        results[part_id] = result
    if not results:
        raise ConsistencyError("no selected part marks both physical GLB and STL")
    return {
        "parts": results,
        "pass": all(result["pass"] for result in results.values()),
        "revision": manifest["revision"],
        "schema": MANIFEST_REPORT_SCHEMA,
        "skippedParts": skipped,
    }


def _artifact_spec(
    path: str,
    *,
    node_names: list[str] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"path": path}
    if node_names:
        result["nodeNames"] = node_names
    return result


def _emit_report(report: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(report, indent=2) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--part", action="append", dest="parts")
    parser.add_argument("--mesh-a")
    parser.add_argument("--mesh-b")
    parser.add_argument("--node-a", action="append")
    parser.add_argument("--node-b", action="append")
    parser.add_argument("--revision-a")
    parser.add_argument("--revision-b")
    parser.add_argument("--scale-a", type=float, default=1.0)
    parser.add_argument("--scale-b", type=float, default=1.0)
    parser.add_argument("--dimension-tolerance-mm", type=float, default=0.1)
    parser.add_argument("--surface-p99-tolerance-mm", type=float, default=0.1)
    parser.add_argument("--surface-max-tolerance-mm", type=float, default=0.25)
    parser.add_argument("--sample-count", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the JSON report to this path",
    )
    args = parser.parse_args(argv)

    try:
        if args.manifest is not None:
            if args.mesh_a is not None or args.mesh_b is not None:
                raise ConsistencyError("use either --manifest or --mesh-a/--mesh-b")
            manifest_path = args.manifest.resolve()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report = compare_manifest(
                manifest,
                base_dir=manifest_path.parent,
                part_ids=args.parts,
                dimension_tolerance_mm=args.dimension_tolerance_mm,
                surface_p99_tolerance_mm=args.surface_p99_tolerance_mm,
                surface_max_tolerance_mm=args.surface_max_tolerance_mm,
                sample_count=args.sample_count,
                seed=args.seed,
            )
        else:
            if args.parts:
                raise ConsistencyError("--part is only valid with --manifest")
            if args.mesh_a is None or args.mesh_b is None:
                raise ConsistencyError(
                    "direct mode requires both --mesh-a and --mesh-b"
                )
            mesh_a = load_artifact(
                _artifact_spec(args.mesh_a, node_names=args.node_a), Path.cwd()
            )
            mesh_b = load_artifact(
                _artifact_spec(args.mesh_b, node_names=args.node_b), Path.cwd()
            )
            report = compare_meshes(
                mesh_a,
                mesh_b,
                revision_a=args.revision_a,
                revision_b=args.revision_b,
                scale_a=args.scale_a,
                scale_b=args.scale_b,
                dimension_tolerance_mm=args.dimension_tolerance_mm,
                surface_p99_tolerance_mm=args.surface_p99_tolerance_mm,
                surface_max_tolerance_mm=args.surface_max_tolerance_mm,
                sample_count=args.sample_count,
                seed=args.seed,
            )
    except Exception as error:
        report = {
            "error": str(error),
            "pass": False,
            "schema": MANIFEST_REPORT_SCHEMA if args.manifest else REPORT_SCHEMA,
        }
        _emit_report(report, args.output)
        return 2
    _emit_report(report, args.output)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
