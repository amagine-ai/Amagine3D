"""Bind authored Mesh or BRep geometry to one semantic-scene feature.

The build source owns the geometry object.  This module writes its canonical
triangle artifact and returns the matching scene node from that same object, so
the modeler never has to repeat dimensions or invent an intermediate STEP.
"""

from __future__ import annotations

from hashlib import sha256
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import trimesh

from mesh_normalization import (
    MeshNormalizationError,
    canonical_mesh,
    normalized_stl_bytes,
)
from scene_contract import (
    BREP_GEOMETRY_RECIPE_KIND,
    MESH_GEOMETRY_RECIPE_KIND,
)


PHYSICAL_ROLES = {"cutter", "separate", "solid"}


class GeometryBindingError(ValueError):
    """Raised when authored geometry cannot become a canonical feature mesh."""


def _positive_finite(value: float, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise GeometryBindingError(f"{name} must be finite and positive")
    return float(value)


def _canonical_mesh(mesh: Any, context: str) -> trimesh.Trimesh:
    try:
        return canonical_mesh(mesh, context)
    except MeshNormalizationError as error:
        raise GeometryBindingError(str(error)) from error


def shape_to_mesh(
    shape: Any,
    context: str,
    *,
    linear_tolerance_mm: float = 0.02,
    angular_tolerance_rad: float = 0.1,
) -> trimesh.Trimesh:
    """Tessellate valid build123d geometry at unit scale."""

    linear = _positive_finite(linear_tolerance_mm, "linear_tolerance_mm")
    angular = _positive_finite(angular_tolerance_rad, "angular_tolerance_rad")
    try:
        valid_value = shape.is_valid
        valid = bool(valid_value() if callable(valid_value) else valid_value)
        solid_count = len(shape.solids())
    except Exception as error:
        raise GeometryBindingError(
            f"{context} is not inspectable build123d geometry: {error}"
        ) from error
    if not valid:
        raise GeometryBindingError(f"{context} is not a valid BRep")
    if solid_count < 1:
        raise GeometryBindingError(f"{context} contains no BRep solids")
    try:
        vertices, faces = shape.tessellate(linear, angular)
        mesh = trimesh.Trimesh(
            vertices=[[vertex.X, vertex.Y, vertex.Z] for vertex in vertices],
            faces=faces,
            process=False,
        )
    except Exception as error:
        raise GeometryBindingError(
            f"{context} could not be tessellated: {error}"
        ) from error
    return _canonical_mesh(mesh, context)


def _write_stl(mesh: trimesh.Trimesh, path: Path) -> str:
    if path.suffix.lower() != ".stl":
        raise GeometryBindingError("bound geometry path must end in .stl")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = normalized_stl_bytes(mesh, f"bound geometry {path.name}")
    except MeshNormalizationError as error:
        raise GeometryBindingError(str(error)) from error
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
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
        raise GeometryBindingError(f"cannot write bound geometry {path}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return sha256(payload).hexdigest()


def export_shape_stl(
    shape: Any,
    path: str | Path,
    *,
    linear_tolerance_mm: float = 0.01,
    angular_tolerance_rad: float = 0.1,
) -> str:
    """Atomically export a BRep as a normalized, validated binary STL."""

    destination = Path(path).expanduser().resolve()
    mesh = shape_to_mesh(
        shape,
        f"STL geometry {destination.name}",
        linear_tolerance_mm=linear_tolerance_mm,
        angular_tolerance_rad=angular_tolerance_rad,
    )
    return _write_stl(mesh, destination)


def _feature_node(
    *,
    node_id: str,
    feature_id: str,
    role: str,
    mesh: trimesh.Trimesh,
    path: str | Path,
    recipe_kind: str,
    tessellation: dict[str, float] | None = None,
) -> dict[str, Any]:
    if role not in PHYSICAL_ROLES:
        raise GeometryBindingError(
            f"role must be one of {sorted(PHYSICAL_ROLES)}"
        )
    destination = Path(path).expanduser().resolve()
    digest = _write_stl(mesh, destination)
    parameters: dict[str, Any] = {
        "geometry": {
            "path": str(destination),
            "scale": 1.0,
            "sha256": digest,
        }
    }
    if tessellation is not None:
        parameters["tessellation"] = tessellation
    return {
        "id": node_id,
        "featureId": feature_id,
        "role": role,
        "recipe": {"kind": recipe_kind, "parameters": parameters},
    }


def bind_mesh_feature(
    *,
    node_id: str,
    feature_id: str,
    role: str,
    mesh: trimesh.Trimesh,
    path: str | Path,
) -> dict[str, Any]:
    """Persist one already-canonical Mesh and return its physical scene node."""

    return _feature_node(
        node_id=node_id,
        feature_id=feature_id,
        role=role,
        mesh=_canonical_mesh(mesh, f"feature {feature_id}"),
        path=path,
        recipe_kind=MESH_GEOMETRY_RECIPE_KIND,
    )


def bind_brep_feature(
    *,
    node_id: str,
    feature_id: str,
    role: str,
    shape: Any,
    path: str | Path,
    linear_tolerance_mm: float = 0.02,
    angular_tolerance_rad: float = 0.1,
) -> dict[str, Any]:
    """Persist a direct BRep tessellation without creating an intermediate STEP."""

    linear = _positive_finite(linear_tolerance_mm, "linear_tolerance_mm")
    angular = _positive_finite(angular_tolerance_rad, "angular_tolerance_rad")
    return _feature_node(
        node_id=node_id,
        feature_id=feature_id,
        role=role,
        mesh=shape_to_mesh(
            shape,
            f"feature {feature_id}",
            linear_tolerance_mm=linear,
            angular_tolerance_rad=angular,
        ),
        path=path,
        recipe_kind=BREP_GEOMETRY_RECIPE_KIND,
        tessellation={
            "angularToleranceRad": angular,
            "linearToleranceMm": linear,
        },
    )
