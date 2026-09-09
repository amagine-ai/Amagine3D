"""Directional opening evidence when protrusions make the owner AABB misleading."""
from hashlib import sha256
from pathlib import Path

import numpy as np
import trimesh

from coordinate_frames import validated_rigid_matrix


def local_opening_evidence(report, owner, bounds, axis, side, mesh_cache):
    """Sample a checked cut's interior toward the declared exterior direction.

    This supplements the coarse AABB check, not full passage or installation QA.
    Use only hash-bound per-part geometry restored to its semantic coordinates.
    """
    if owner not in mesh_cache:
        artifacts = report.get("artifacts", {})
        reference = artifacts.get("stl:" + owner)
        if reference is None and len(report.get("parts", {})) == 1:
            reference = artifacts.get("stl")
        if not isinstance(reference, dict):
            return None
        path = Path(reference.get("path", ""))
        if not path.is_absolute() or not path.is_file():
            return None
        if sha256(path.read_bytes()).hexdigest() != reference.get("sha256"):
            raise ValueError("opening placement mesh hash differs from build report")
        frame = reference.get("coordinateFrame")
        if frame == "semantic":
            matrix = np.eye(4)
        else:
            matrix, error = validated_rigid_matrix(
                report.get("coordinateFrames", {}).get(frame, {}).get("partTransforms", {}).get(owner)
            )
            if error:
                raise ValueError("opening placement has no valid semantic transform: " + error)
        mesh = trimesh.load(path, force="mesh")
        mesh.apply_transform(np.linalg.inv(matrix))
        if not mesh.is_watertight or not mesh.is_volume:
            raise ValueError("opening placement requires a closed oriented owner mesh")
        mesh_cache[owner] = mesh
    mesh = mesh_cache[owner]
    transverse = [i for i in range(3) if i != axis]
    origins = np.tile(np.mean(bounds, axis=0), (9, 1))
    for i, (u, v) in enumerate((u, v) for u in (.25, .5, .75) for v in (.25, .5, .75)):
        for coordinate, fraction in zip(transverse, (u, v)):
            origins[i, coordinate] = bounds[0, coordinate] + fraction * (bounds[1, coordinate] - bounds[0, coordinate])
    directions = np.zeros_like(origins)
    directions[:, axis] = -1 if side == "min" else 1
    clear = ~mesh.contains(origins) & ~mesh.ray.intersects_any(origins, directions)
    return {"method": "semantic-owner-exterior-rays", "sample_count": len(origins),
            "clear_sample_count": int(clear.sum()), "reaches_exterior": bool(clear.all())}
