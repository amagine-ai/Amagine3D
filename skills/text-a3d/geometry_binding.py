"""Bind authored BRep geometry to one semantic-scene feature.

The build source owns the geometry object.  This module writes its canonical
triangle artifact and returns the matching scene node from that same object, so
the modeler never has to repeat dimensions or invent an intermediate STEP.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import trimesh
import numpy as np
from brep_tessellation import tessellate_brep

from mesh_normalization import (
    MeshNormalizationError,
    canonical_mesh,
    normalized_stl_bytes,
)
from scene_contract import (
    BREP_GEOMETRY_RECIPE_KIND,
    DISPLAY_COMPONENT_KIND,
    FEATURE_ID_PATTERN,
    ID_PATTERN,
)


PHYSICAL_ROLES = {"cutter", "separate", "solid"}


class GeometryBindingError(ValueError):
    """Raised when authored geometry cannot become a canonical feature mesh."""


@dataclass(frozen=True)
class BrepFeature:
    """One owned geometry object reused by a boolean, observation, and scene node.

    The handle carries geometry, not acceptance values. A bound cutter is still
    only a witness; final geometry checks independently verify its effect.
    """

    id: str
    part: str
    role: str
    shape: Any

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not FEATURE_ID_PATTERN.fullmatch(self.id):
            raise GeometryBindingError("feature id must be a valid semantic feature ID")
        if not isinstance(self.part, str) or not ID_PATTERN.fullmatch(self.part):
            raise GeometryBindingError("feature part must be a valid part ID")
        if not isinstance(self.role, str) or self.role not in PHYSICAL_ROLES:
            raise GeometryBindingError(f"role must be one of {sorted(PHYSICAL_ROLES)}")

    def cut_from(self, body: Any, *, min_removed_mm3: float = 0.001) -> Any:
        """Return the checked subtraction using this exact cutter and identity."""
        if self.role != "cutter":
            raise GeometryBindingError("cut_from requires a feature with role 'cutter'")
        from cad_helpers import checked_cut

        return checked_cut(body, self.shape, self.id, min_removed_mm3,
                           part_name=self.part)

    def bind(self, path: str | Path, *, node_id: str | None = None,
             linear_tolerance_mm: float = 0.02,
             angular_tolerance_rad: float = 0.1) -> dict[str, Any]:
        """Record and tessellate the same object without repeating its owner or ID."""
        from cad_helpers import observe

        node = bind_brep_feature(
            node_id=node_id or f"{self.part}-{self.id.replace('/', '-')}-node",
            feature_id=self.id, role=self.role, shape=self.shape, path=path,
            linear_tolerance_mm=linear_tolerance_mm,
            angular_tolerance_rad=angular_tolerance_rad,
        )
        observe(self.shape, self.id, role=self.role, part_name=self.part)
        return {**node, "partId": self.part}


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
        vertices, faces = tessellate_brep(shape, linear, angular)
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
    return _write_payload(payload, path)


def _write_payload(payload: bytes, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _display_triangle_mesh(mesh: Any, context: str) -> trimesh.Trimesh:
    """Validate a visual surface without imposing manufacturing volume rules."""
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty or not len(mesh.faces):
        raise GeometryBindingError(f"{context} must contain triangle faces")
    if not np.isfinite(mesh.vertices).all():
        raise GeometryBindingError(f"{context} contains non-finite vertices")
    if not np.all(np.any(trimesh.triangles.cross(mesh.triangles) != 0.0, axis=1)):
        raise GeometryBindingError(f"{context} contains zero-area triangles")
    return mesh.copy()


def bind_display_component(
    *,
    node_id: str,
    feature_id: str,
    physical_feature_ref: str,
    shape: Any,
    path: str | Path,
    appearance: dict[str, Any],
    linear_tolerance_mm: float = 0.02,
    angular_tolerance_rad: float = 0.1,
) -> dict[str, Any]:
    """Bind a BRep or triangle surface as a non-manufactured scene component.

    The receiving part and its physical feature remain the scene's source of
    ownership. The source mesh uses assembly coordinates and unit scale. PLY is
    recommended for visual surfaces; STL sources are also supported. Neither
    a closed volume nor a printable dummy is required for a display component.
    """
    from display_glb import DisplayGlbError, appearance as normalize_appearance

    for value, pattern, label in (
        (node_id, ID_PATTERN, "node_id"),
        (feature_id, FEATURE_ID_PATTERN, "feature_id"),
        (physical_feature_ref, FEATURE_ID_PATTERN, "physical_feature_ref"),
    ):
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise GeometryBindingError(f"{label} is invalid")
    if feature_id == physical_feature_ref:
        raise GeometryBindingError("display feature must differ from its physical reference")
    if not isinstance(appearance, dict):
        raise GeometryBindingError("appearance must be an object")
    try:
        visual = normalize_appearance(
            appearance.get("baseColor"),
            metallic=appearance.get("metallic", 0.0),
            roughness=appearance.get("roughness", 0.58),
        )
    except DisplayGlbError as error:
        raise GeometryBindingError(str(error)) from error
    linear = _positive_finite(linear_tolerance_mm, "linear_tolerance_mm")
    angular = _positive_finite(angular_tolerance_rad, "angular_tolerance_rad")
    destination = Path(path).expanduser().resolve()
    file_type = destination.suffix.lower().lstrip(".")
    if file_type not in {"ply", "stl"}:
        raise GeometryBindingError("display source path must end in .ply or .stl")
    if isinstance(shape, trimesh.Trimesh):
        mesh = _display_triangle_mesh(shape, f"display component {feature_id}")
    else:
        try:
            valid_value = shape.is_valid
            if not bool(valid_value() if callable(valid_value) else valid_value):
                raise ValueError("invalid BRep")
            vertices, faces = tessellate_brep(shape, linear, angular)
            mesh = trimesh.Trimesh(
                vertices=[[vertex.X, vertex.Y, vertex.Z] for vertex in vertices],
                faces=faces,
                process=False,
            )
        except Exception as error:
            raise GeometryBindingError(
                f"display component {feature_id} could not be tessellated: {error}"
            ) from error
        mesh = _display_triangle_mesh(mesh, f"display component {feature_id}")
    try:
        payload = mesh.export(file_type=file_type)
        if not isinstance(payload, bytes):
            raise ValueError("exporter did not return binary mesh data")
        readback = trimesh.load(BytesIO(payload), file_type=file_type, process=False)
        _display_triangle_mesh(readback, f"display component {feature_id} readback")
    except (ValueError, TypeError, OSError) as error:
        raise GeometryBindingError(f"cannot export display component {feature_id}: {error}") from error
    digest = _write_payload(payload, destination)
    return {
        "id": node_id,
        "featureId": feature_id,
        "role": "display-only",
        "operation": "none",
        "physicalFeatureRef": physical_feature_ref,
        "recipe": {
            "kind": DISPLAY_COMPONENT_KIND,
            "parameters": {
                "sourceMesh": {"path": str(destination), "scale": 1.0, "sha256": digest},
                "appearance": visual,
            },
        },
    }
