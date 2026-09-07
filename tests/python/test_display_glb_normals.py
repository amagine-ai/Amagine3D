from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from display_glb import export_display_glb  # noqa: E402
from render_preview import _render_inputs, _surface_checks  # noqa: E402


def _attributes(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read actual GLB NORMAL accessors; lazy trimesh normals are not evidence."""

    payload = path.read_bytes()
    json_length = struct.unpack_from("<I", payload, 12)[0]
    tree = json.loads(payload[20 : 20 + json_length])
    binary_start = 20 + json_length + 8
    primitive = tree["meshes"][0]["primitives"][0]

    def read(accessor_id: int) -> np.ndarray:
        accessor = tree["accessors"][accessor_id]
        view = tree["bufferViews"][accessor["bufferView"]]
        offset = binary_start + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        columns = 3 if accessor["type"] == "VEC3" else 1
        return np.frombuffer(
            payload,
            dtype={5126: "<f4", 5125: "<u4"}[accessor["componentType"]],
            count=accessor["count"] * columns,
            offset=offset,
        ).reshape((-1, columns))

    return (
        read(primitive["attributes"]["POSITION"]),
        read(primitive["attributes"]["NORMAL"]),
        read(primitive["indices"]).reshape((-1, 3)),
    )


class DisplayGlbNormalsTests(unittest.TestCase):
    def _export(self, mesh: trimesh.Trimesh, root: Path) -> Path:
        path = root / "display.glb"
        before_vertices = mesh.vertices.copy()
        before_faces = mesh.faces.copy()
        result = export_display_glb([("body", mesh, {"baseColor": "#EDEDED"})], path)
        self.assertTrue(result["verified"])
        np.testing.assert_array_equal(mesh.vertices, before_vertices)
        np.testing.assert_array_equal(mesh.faces, before_faces)
        positions, normals, faces = _attributes(path)
        self.assertTrue(np.isfinite(normals).all())
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-6)
        np.testing.assert_allclose(positions[faces], before_vertices[before_faces], atol=1e-6)
        return path

    def test_sphere_exports_explicit_smooth_normals_and_identical_triangles(self) -> None:
        sphere = trimesh.creation.icosphere(subdivisions=2, radius=4.0)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = self._export(sphere, Path(directory))
            positions, normals, faces = _attributes(path)
            radial = positions / np.linalg.norm(positions, axis=1)[:, None]
            self.assertGreater(float(np.min(np.sum(normals * radial, axis=1))), 0.999)
            self.assertEqual(len(positions), len(sphere.vertices))
            # One triangle has distinct corner normals, unlike flat shading.
            self.assertGreater(np.linalg.norm(normals[faces[0, 0]] - normals[faces[0, 1]]), 0.1)

    def test_box_keeps_hard_edges_even_if_global_vertex_normals_were_cached(self) -> None:
        box = trimesh.creation.box(extents=[3, 4, 5])
        _ = box.vertex_normals
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = self._export(box, Path(directory))
            positions, normals, faces = _attributes(path)
            self.assertEqual(len(positions), 24)
            expected = np.repeat(box.face_normals[:, None, :], 3, axis=1)
            np.testing.assert_allclose(normals[faces], expected, atol=1e-6)

            # The PNG input path must preserve both splits and explicit normals.
            item = _render_inputs(path, (1, 2, 3))[0]
            self.assertEqual(len(item.mesh.vertices), 24)
            np.testing.assert_allclose(item.normal_vectors[item.normal_faces], expected, atol=1e-6)
            self.assertTrue(_surface_checks([item])["watertight"])


if __name__ == "__main__":
    unittest.main()
