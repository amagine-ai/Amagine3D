"""Conservative topology normalization for physical meshes, STL, and 3MF.

Tessellators and float32 artifact coordinates can leave triangles with no
surface area. Removing those triangles is safe only when the remaining surface still
passes the full closed-volume checks. This module never fills holes, remeshes,
or relaxes the existing vertex-welding precision to make a mesh pass.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import numpy as np
import trimesh


class MeshNormalizationError(ValueError):
    """A physical mesh cannot be normalized without losing its validity."""


def _positive_area_faces(mesh: trimesh.Trimesh) -> np.ndarray:
    # Unlike nondegenerate_faces()'s default minimum-height filter, this keeps
    # every positive-area triangle, however thin. Avoid squaring the cross
    # product, which can underflow for very small but nonzero areas.
    return np.any(trimesh.triangles.cross(mesh.triangles) != 0.0, axis=1)


def _weld_vertices(mesh: trimesh.Trimesh) -> None:
    # Keep trimesh's existing default position precision. Visual seams and
    # cached normals must not prevent coincident physical vertices from welding.
    mesh.merge_vertices(merge_tex=True, merge_norm=True)


def canonical_mesh(mesh: Any, context: str = "mesh") -> trimesh.Trimesh:
    """Copy, weld, remove only zero-area faces, then require a closed volume."""

    if not isinstance(mesh, trimesh.Trimesh):
        raise MeshNormalizationError(f"{context} must be a trimesh.Trimesh")
    result = mesh.copy()
    if result.is_empty or len(result.faces) == 0:
        raise MeshNormalizationError(f"{context} contains no triangle faces")
    if not np.isfinite(result.vertices).all():
        raise MeshNormalizationError(f"{context} contains non-finite vertices")
    _weld_vertices(result)
    result.update_faces(_positive_area_faces(result))
    result.remove_unreferenced_vertices()
    if len(result.faces) == 0:
        raise MeshNormalizationError(f"{context} contains no positive-area triangle faces")
    if not result.is_watertight:
        raise MeshNormalizationError(f"{context} must be watertight")
    if not result.is_winding_consistent:
        raise MeshNormalizationError(f"{context} winding is inconsistent")
    # A closed but flattened surface can have zero volume. Trimesh also derives
    # its undefined center of mass while computing volume; report the explicit
    # validation error below instead of leaking a numpy divide warning.
    with np.errstate(divide="ignore", invalid="ignore"):
        volume = float(result.volume)
    if volume < 0:
        result.invert()
        volume = -volume
    if not np.isfinite(volume) or volume <= 0 or not result.is_volume:
        raise MeshNormalizationError(f"{context} must be a positive volume")
    return result


def float32_mesh(
    mesh: trimesh.Trimesh, context: str = "mesh", *, format_name: str = "artifact"
) -> trimesh.Trimesh:
    """Normalize at the precision used by binary STL and lib3mf positions."""
    quantized = canonical_mesh(mesh, context)
    with np.errstate(over="ignore", invalid="ignore"):
        quantized.vertices = quantized.vertices.astype(np.float32).astype(np.float64)
    return canonical_mesh(
        quantized, f"{context} after {format_name} float32 quantization"
    )


def normalized_stl_bytes(mesh: trimesh.Trimesh, context: str = "mesh") -> bytes:
    """Return validated binary STL, including normalization after quantization.

    Binary STL stores float32 coordinates. Quantize *before* the final cleanup
    so triangles collapsed by serialization are absent from the actual bytes,
    not merely hidden by a repair when someone later loads the file. Reject
    geometry whose quantized surface is no longer a closed positive volume.
    """

    quantized = float32_mesh(mesh, context, format_name="STL")
    payload = quantized.export(file_type="stl")
    if not isinstance(payload, bytes):
        raise MeshNormalizationError("STL exporter did not return binary data")

    # Validate the serialized artifact itself, without the loader's automatic
    # repair. Any newly collapsed face is a failed export, not a successful
    # in-memory cleanup that leaves a damaged STL on disk.
    readback = trimesh.load(BytesIO(payload), file_type="stl", process=False)
    if not isinstance(readback, trimesh.Trimesh):
        raise MeshNormalizationError(f"{context} STL readback is not a triangle mesh")
    _weld_vertices(readback)
    if not _positive_area_faces(readback).all():
        raise MeshNormalizationError(f"{context} STL readback contains zero-area faces")
    canonical_mesh(readback, f"{context} STL readback")
    return payload
