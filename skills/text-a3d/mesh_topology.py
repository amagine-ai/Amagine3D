"""Manufacturing topology facts for oriented watertight meshes."""

from __future__ import annotations

import manifold3d
import numpy as np
import trimesh


class MeshTopologyError(ValueError):
    """Raised when material-body topology cannot be evaluated."""


def mesh_topology_facts(mesh: trimesh.Trimesh) -> dict:
    """Return stable manufacturing facts without attempting mesh repair."""

    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        return {
            "bodyCount": 0,
            "boundaryEdgeCount": 0,
            "faceCount": 0,
            "isVolume": False,
            "nonManifoldEdgeCount": 0,
            "surfaceComponentCount": 0,
            "vertexCount": 0,
            "watertight": False,
            "windingConsistent": False,
        }
    inverse = np.asarray(mesh.edges_unique_inverse, dtype=np.int64)
    edge_uses = np.bincount(inverse, minlength=len(mesh.edges_unique))
    try:
        body_count = physical_body_count(mesh)
    except MeshTopologyError:
        body_count = None
    return {
        "bodyCount": body_count,
        "boundaryEdgeCount": int(np.count_nonzero(edge_uses == 1)),
        "faceCount": int(len(mesh.faces)),
        "isVolume": bool(mesh.is_volume),
        "nonManifoldEdgeCount": int(np.count_nonzero(edge_uses > 2)),
        "surfaceComponentCount": int(
            len(mesh.split(only_watertight=False))
        ),
        "vertexCount": int(len(mesh.vertices)),
        "watertight": bool(mesh.is_watertight),
        "windingConsistent": bool(mesh.is_winding_consistent),
    }


def physical_body_count(mesh: trimesh.Trimesh) -> int:
    """Count positive material bodies without counting negative cavity skins."""

    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        return 0
    try:
        body = manifold3d.Manifold(
            mesh=manifold3d.Mesh(
                vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
                tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
            )
        )
    except Exception as error:
        raise MeshTopologyError(
            f"Manifold conversion failed: {error}"
        ) from error
    if body.status() != manifold3d.Error.NoError:
        raise MeshTopologyError(f"Manifold conversion failed with {body.status()}")
    return sum(component.volume() > 0 for component in body.decompose())
