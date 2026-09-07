"""Display-only corner normals; canonical geometry is never modified."""

from __future__ import annotations

import numpy as np
import trimesh


# Fine tessellation on a curved surface should shade continuously, while real
# corners (including a box's 90 degree edges) remain visibly sharp.
CREASE_ANGLE_DEGREES = 30.0


def shading_normals(
    mesh: trimesh.Trimesh, *, use_existing: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """Return unit normals and per-face normal indices without moving vertices.

    glTF importers store explicitly authored NORMAL data in trimesh's cache.
    Read that cache without invoking vertex_normals, which would silently
    generate normals averaged across every shared edge.
    """

    faces = np.asarray(mesh.faces, dtype=np.int64)
    if use_existing:
        existing = mesh._cache.cache.get("vertex_normals")
        if existing is not None:
            normals = np.asarray(existing, dtype=np.float64)
            lengths = np.linalg.norm(normals, axis=1)
            if (
                normals.shape == mesh.vertices.shape
                and np.isfinite(normals).all()
                and np.all(lengths > 1e-12)
            ):
                return normals / lengths[:, None], faces

    adjacency = mesh.face_adjacency
    smooth = mesh.face_adjacency_angles < np.radians(CREASE_ANGLE_DEGREES)
    adjacent_faces = adjacency[smooth]
    shared_edges = mesh.face_adjacency_edges[smooth]

    # Each graph node is one triangle corner. Connect only corners sharing a
    # smooth edge, so a distant smooth path elsewhere on a part cannot erase a
    # local hard edge. Disconnected shells are never welded for appearance.
    links = []
    for edge_end in range(2):
        vertex_ids = shared_edges[:, edge_end]
        first_corner = np.argmax(faces[adjacent_faces[:, 0]] == vertex_ids[:, None], axis=1)
        second_corner = np.argmax(faces[adjacent_faces[:, 1]] == vertex_ids[:, None], axis=1)
        links.append(
            np.column_stack(
                (adjacent_faces[:, 0] * 3 + first_corner,
                 adjacent_faces[:, 1] * 3 + second_corner)
            )
        )
    labels = trimesh.graph.connected_component_labels(
        np.concatenate(links), node_count=faces.size
    )
    normal_faces = labels.reshape((-1, 3))
    normals = trimesh.geometry.weighted_vertex_normals(
        int(labels.max()) + 1, normal_faces, mesh.face_normals, mesh.face_angles
    )
    return normals, normal_faces


def mesh_with_display_normals(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Split display vertices at creases; keep every triangle's exact position."""

    normals, normal_faces = shading_normals(mesh)
    _, first_corner = np.unique(normal_faces, return_index=True)
    vertex_ids = np.asarray(mesh.faces).reshape(-1)[first_corner]
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices)[vertex_ids],
        faces=normal_faces,
        vertex_normals=normals,
        metadata=mesh.metadata.copy(),
        process=False,
    )
