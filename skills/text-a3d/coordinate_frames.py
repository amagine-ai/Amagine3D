"""Shared rigid coordinate-frame transforms for exporters and QA."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


MATRIX_CONVENTION = "column-vector: target = matrix @ source"


def rigid_matrix(
    *,
    rotate_degrees_xyz: list[float] | tuple[float, float, float] = (0.0, 0.0, 0.0),
    translate_mm: list[float] | tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> list[list[float]]:
    """Return a homogeneous matrix for the exporters' rotate-then-translate rule."""

    rotation = np.asarray(rotate_degrees_xyz, dtype=float)
    translation = np.asarray(translate_mm, dtype=float)
    if rotation.shape != (3,) or translation.shape != (3,):
        raise ValueError("rotation and translation must contain three values")
    if not np.isfinite(rotation).all() or not np.isfinite(translation).all():
        raise ValueError("rotation and translation must be finite")

    rx, ry, rz = np.radians(rotation)
    sx, sy, sz = math.sin(rx), math.sin(ry), math.sin(rz)
    cx, cy, cz = math.cos(rx), math.cos(ry), math.cos(rz)
    rotate_x = np.asarray([
        [1.0, 0.0, 0.0],
        [0.0, cx, -sx],
        [0.0, sx, cx],
    ])
    rotate_y = np.asarray([
        [cy, 0.0, sy],
        [0.0, 1.0, 0.0],
        [-sy, 0.0, cy],
    ])
    rotate_z = np.asarray([
        [cz, -sz, 0.0],
        [sz, cz, 0.0],
        [0.0, 0.0, 1.0],
    ])
    matrix = np.eye(4, dtype=float)
    matrix[:3, :3] = rotate_z @ rotate_y @ rotate_x
    matrix[:3, 3] = translation
    matrix[np.abs(matrix) < 1e-12] = 0.0
    return matrix.round(10).tolist()


def rigid_transform(
    from_frame: str,
    to_frame: str,
    *,
    rotate_degrees_xyz: list[float] | tuple[float, float, float] = (0.0, 0.0, 0.0),
    translate_mm: list[float] | tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict[str, Any]:
    if not isinstance(from_frame, str) or not from_frame:
        raise ValueError("from_frame is required")
    if not isinstance(to_frame, str) or not to_frame:
        raise ValueError("to_frame is required")
    return {
        "from": from_frame,
        "to": to_frame,
        "matrix": rigid_matrix(
            rotate_degrees_xyz=rotate_degrees_xyz,
            translate_mm=translate_mm,
        ),
        "matrix_convention": MATRIX_CONVENTION,
    }


def validated_rigid_matrix(raw: Any) -> tuple[np.ndarray | None, str | None]:
    """Fail closed on absent, scaled, reflected, or malformed transforms."""

    try:
        matrix = np.asarray(raw, dtype=float)
    except (TypeError, ValueError):
        return None, "coordinate transform matrix is invalid"
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        return None, "coordinate transform matrix must be finite 4x4"
    if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-8):
        return None, "coordinate transform must be affine"
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-7):
        return None, "coordinate transform must not contain scale or shear"
    if not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-7):
        return None, "coordinate transform must be a proper rigid transform"
    return matrix, None


def transform_bounds(bounds: Any, matrix: np.ndarray) -> np.ndarray:
    """Transform all eight AABB corners and return their target-frame AABB."""

    source = np.asarray(bounds, dtype=float)
    if source.shape != (2, 3) or not np.isfinite(source).all():
        raise ValueError("bounds must be a finite 2x3 array")
    corners = np.asarray([
        [x, y, z, 1.0]
        for x in (source[0, 0], source[1, 0])
        for y in (source[0, 1], source[1, 1])
        for z in (source[0, 2], source[1, 2])
    ])
    transformed = (matrix @ corners.T).T[:, :3]
    return np.asarray([transformed.min(axis=0), transformed.max(axis=0)])


def bounds_overlap(first: np.ndarray, second: np.ndarray) -> bool:
    return bool(
        np.all(first[1] >= second[0])
        and np.all(second[1] >= first[0])
    )
