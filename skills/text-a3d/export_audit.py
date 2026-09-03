"""Independent read-back audit for BRep exporter delivery artifacts.

The BRep exporters must not trust a successful writer call.  This module opens
the bytes that were actually written with independent readers and compares the
observed geometry with the kernel-side geometry that was meant to be exported.
"""

from __future__ import annotations

from hashlib import sha256
import math
from pathlib import Path
from typing import Any

from build123d import import_step
import numpy as np
import trimesh

from mesh_topology import MeshTopologyError, physical_body_count


EXPORT_AUDIT_SCHEMA = "evidence-export-audit/v1"
BOUNDS_TOLERANCE_MM = 0.05
VOLUME_ABSOLUTE_TOLERANCE_MM3 = 0.05
VOLUME_RELATIVE_TOLERANCE = 0.005


class ExportAuditError(RuntimeError):
    """Raised when a written artifact cannot be independently verified."""


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _valid(shape: Any) -> bool:
    value = shape.is_valid
    return bool(value() if callable(value) else value)


def geometry_record(shape: Any) -> dict:
    """Return the canonical expected-geometry record used by this audit."""

    bounds = shape.bounding_box()
    minimum = [float(bounds.min.X), float(bounds.min.Y), float(bounds.min.Z)]
    maximum = [float(bounds.max.X), float(bounds.max.Y), float(bounds.max.Z)]
    return {
        "bodyCount": len(shape.solids()),
        "boundsMm": {
            "min": minimum,
            "max": maximum,
            "size": [high - low for low, high in zip(minimum, maximum)],
        },
        "valid": _valid(shape),
        "volumeMm3": float(shape.volume),
    }


def _mesh_record(mesh: trimesh.Trimesh) -> dict:
    bounds = np.asarray(mesh.bounds, dtype=float)
    try:
        body_count = physical_body_count(mesh)
    except MeshTopologyError:
        body_count = None
    return {
        "bodyCount": body_count,
        "boundsMm": {
            "min": bounds[0].tolist(),
            "max": bounds[1].tolist(),
            "size": (bounds[1] - bounds[0]).tolist(),
        },
        "faceCount": int(len(mesh.faces)),
        "valid": bool(
            not mesh.is_empty
            and mesh.is_volume
            and mesh.is_watertight
            and mesh.is_winding_consistent
        ),
        "volumeMm3": float(mesh.volume),
        "vertexCount": int(len(mesh.vertices)),
        "watertight": bool(mesh.is_watertight),
        "windingConsistent": bool(mesh.is_winding_consistent),
    }


def _geometry_errors(observed: dict, expected: dict, label: str) -> list[str]:
    errors: list[str] = []
    if observed.get("valid") is not True:
        errors.append(f"{label} is not a valid closed volume")
    if observed.get("bodyCount") != expected.get("bodyCount"):
        errors.append(
            f"{label} body count {observed.get('bodyCount')} does not match "
            f"expected {expected.get('bodyCount')}"
        )
    observed_bounds = observed.get("boundsMm", {})
    expected_bounds = expected.get("boundsMm", {})
    for field in ("min", "max", "size"):
        actual = observed_bounds.get(field)
        wanted = expected_bounds.get(field)
        if (
            not isinstance(actual, list)
            or not isinstance(wanted, list)
            or len(actual) != 3
            or len(wanted) != 3
            or any(
                not math.isfinite(float(value))
                for value in [*actual, *wanted]
            )
            or any(
                abs(float(left) - float(right)) > BOUNDS_TOLERANCE_MM
                for left, right in zip(actual, wanted)
            )
        ):
            errors.append(
                f"{label} {field} bounds do not match within "
                f"{BOUNDS_TOLERANCE_MM} mm"
            )
    actual_volume = observed.get("volumeMm3")
    expected_volume = expected.get("volumeMm3")
    if not isinstance(actual_volume, (int, float)) or not isinstance(
        expected_volume, (int, float)
    ):
        errors.append(f"{label} volume is unavailable")
    else:
        tolerance = max(
            VOLUME_ABSOLUTE_TOLERANCE_MM3,
            abs(float(expected_volume)) * VOLUME_RELATIVE_TOLERANCE,
        )
        if abs(float(actual_volume) - float(expected_volume)) > tolerance:
            errors.append(
                f"{label} volume {actual_volume:.6f} does not match expected "
                f"{expected_volume:.6f} within {tolerance:.6f} mm^3"
            )
    return errors


def _audit_stl(path: Path, expected: dict, label: str) -> dict:
    loaded = trimesh.load(path, force="mesh", process=True)
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise ExportAuditError(f"{label} did not read back as a non-empty STL mesh")
    observed = _mesh_record(loaded)
    errors = _geometry_errors(observed, expected, label)
    return {
        "errors": errors,
        "expected": expected,
        "observed": observed,
        "pass": not errors,
        "path": str(path.resolve()),
        "sha256": _digest(path),
        "type": "stl",
    }


def _audit_step(path: Path, expected: dict, label: str) -> dict:
    try:
        shape = import_step(str(path))
    except Exception as error:
        raise ExportAuditError(f"{label} STEP read-back failed: {error}") from error
    observed = geometry_record(shape)
    errors = _geometry_errors(observed, expected, label)
    return {
        "errors": errors,
        "expected": expected,
        "observed": observed,
        "pass": not errors,
        "path": str(path.resolve()),
        "reader": "build123d-occt",
        "sha256": _digest(path),
        "type": "step",
    }


def _audit_glb(path: Path, expected_nodes: list[str], label: str) -> dict:
    try:
        scene = trimesh.load(path, force="scene", process=False)
    except Exception as error:
        raise ExportAuditError(f"{label} GLB read-back failed: {error}") from error
    if not isinstance(scene, trimesh.Scene):
        raise ExportAuditError(f"{label} did not read back as a GLB scene")
    geometry = list(scene.geometry.values())
    nodes = sorted(str(item) for item in scene.graph.nodes_geometry)
    errors: list[str] = []
    if len(geometry) != len(expected_nodes):
        errors.append(
            f"{label} geometry count {len(geometry)} does not match expected "
            f"node count {len(expected_nodes)}"
        )
    if len(nodes) != len(expected_nodes):
        errors.append(
            f"{label} drawable node count {len(nodes)} does not match expected "
            f"{len(expected_nodes)}"
        )
    if any(
        not isinstance(mesh, trimesh.Trimesh)
        or mesh.is_empty
        or len(mesh.faces) == 0
        or not np.isfinite(mesh.vertices).all()
        for mesh in geometry
    ):
        errors.append(f"{label} contains empty or non-finite display geometry")
    missing = sorted(set(expected_nodes) - set(nodes))
    if missing:
        errors.append(f"{label} is missing named display nodes {missing}")
    return {
        "errors": errors,
        "expectedNodes": sorted(expected_nodes),
        "observed": {
            "geometryCount": len(geometry),
            "nodes": nodes,
        },
        "pass": not errors,
        "path": str(path.resolve()),
        "reader": "trimesh-gltf",
        "sha256": _digest(path),
        "type": "glb",
    }


def audit_exports(
    *,
    stls: dict[str, tuple[Path, dict]],
    steps: dict[str, tuple[Path, dict]],
    glb: tuple[Path, list[str]],
) -> dict:
    """Read back every BRep-backend delivery mesh and fail as one unit."""

    records: dict[str, dict] = {}
    try:
        for key, (path, expected) in sorted(stls.items()):
            records[key] = _audit_stl(path.resolve(), expected, key)
        for key, (path, expected) in sorted(steps.items()):
            records[key] = _audit_step(path.resolve(), expected, key)
        records["glb:display"] = _audit_glb(
            glb[0].resolve(), list(glb[1]), "glb:display"
        )
    except (OSError, ExportAuditError) as error:
        raise ExportAuditError(str(error)) from error
    errors = [
        error
        for record in records.values()
        for error in record.get("errors", [])
    ]
    result = {
        "artifacts": records,
        "errors": errors,
        "pass": not errors,
        "schema": EXPORT_AUDIT_SCHEMA,
    }
    if errors:
        raise ExportAuditError("; ".join(errors))
    return result


def validate_export_audit(data: Any) -> list[str]:
    """Validate the immutable inline/file export-audit contract."""

    if not isinstance(data, dict):
        return ["export audit must be an object"]
    errors: list[str] = []
    if data.get("schema") != EXPORT_AUDIT_SCHEMA:
        errors.append(f"schema must be {EXPORT_AUDIT_SCHEMA}")
    if data.get("pass") is not True:
        errors.append("pass must be true")
    if data.get("errors") != []:
        errors.append("errors must be an empty array")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("artifacts must be a non-empty object")
    else:
        for key, record in artifacts.items():
            if not isinstance(record, dict):
                errors.append(f"artifacts.{key} must be an object")
                continue
            if record.get("pass") is not True or record.get("errors") != []:
                errors.append(f"artifacts.{key} must pass without errors")
            if not isinstance(record.get("path"), str) or not record["path"].strip():
                errors.append(f"artifacts.{key}.path must be a non-empty string")
            digest = record.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                errors.append(f"artifacts.{key}.sha256 must be a SHA-256")
    return errors
