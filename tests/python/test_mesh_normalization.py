from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from build123d import Box, fillet
import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import geometry_binding  # noqa: E402
from mesh_normalization import (  # noqa: E402
    MeshNormalizationError,
    canonical_mesh,
    normalized_stl_bytes,
)


def _read_stl(payload: bytes) -> trimesh.Trimesh:
    mesh = trimesh.load(BytesIO(payload), file_type="stl", process=False)
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    return mesh


def _subdivided_box() -> trimesh.Trimesh:
    """Closed box with two slivers that disappear at STL float32 precision."""
    mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
    mesh.apply_translation([15.0, 15.0, 15.0])
    a, b = mesh.edges[0]
    vertices = mesh.vertices.tolist()
    vertices.append((mesh.vertices[a] + 1e-8 * (mesh.vertices[b] - mesh.vertices[a])).tolist())
    inserted = len(vertices) - 1
    faces = []
    for face in mesh.faces:
        if a in face and b in face:
            for i in range(3):
                x, y, z = face[i], face[(i + 1) % 3], face[(i + 2) % 3]
                if {x, y} == {a, b}:
                    faces.extend([[x, inserted, z], [inserted, y, z]])
                    break
        else:
            faces.append(face.tolist())
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


class MeshNormalizationTests(unittest.TestCase):
    def assertClosed(self, mesh: trimesh.Trimesh) -> None:
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertTrue(mesh.is_volume)
        self.assertGreater(mesh.volume, 0)
        self.assertTrue(np.any(trimesh.triangles.cross(mesh.triangles) != 0, axis=1).all())

    def test_zero_area_triangles_are_removed_without_mutating_authored_mesh(self):
        authored = trimesh.creation.box(extents=[10.0, 12.0, 14.0])
        # Both repeated-index and distinct-index collinear triangles have no
        # surface. The latter must not be mistaken for a thin positive face.
        authored.vertices = np.vstack([authored.vertices, [[0, 0, 0], [1, 0, 0], [2, 0, 0]]])
        authored.faces = np.vstack([authored.faces, [[0, 0, 1], [8, 9, 10]]])
        original_vertices = authored.vertices.copy()
        original_faces = authored.faces.copy()

        normalized = canonical_mesh(authored)

        self.assertClosed(normalized)
        self.assertEqual(len(normalized.faces), 12)
        self.assertEqual(normalized.volume, 1680.0)
        np.testing.assert_array_equal(authored.vertices, original_vertices)
        np.testing.assert_array_equal(authored.faces, original_faces)

    def test_thin_positive_area_faces_are_not_removed_by_minimum_height(self):
        authored = trimesh.Trimesh(
            vertices=[[0, 0, 0], [10, 0, 0], [5, 1e-10, 0], [5, 0, 5]],
            faces=[[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]],
            process=False,
        )
        self.assertTrue((authored.area_faces > 0).all())
        self.assertFalse(authored.nondegenerate_faces().all())

        normalized = canonical_mesh(authored)

        self.assertClosed(normalized)
        self.assertEqual(len(normalized.faces), 4)
        np.testing.assert_array_equal(normalized.vertices, authored.vertices)
        self.assertAlmostEqual(normalized.volume, authored.volume, places=18)

    def test_true_hole_is_rejected_even_when_zero_area_faces_are_present(self):
        authored = trimesh.creation.box()
        authored.faces = np.vstack([authored.faces[:-1], [[0, 0, 1]]])
        with self.assertRaisesRegex(MeshNormalizationError, "must be watertight"):
            canonical_mesh(authored)
        with self.assertRaisesRegex(MeshNormalizationError, "must be watertight"):
            normalized_stl_bytes(authored)

    def test_inconsistent_winding_remains_rejected(self):
        authored = trimesh.creation.box()
        authored.faces[0] = authored.faces[0][::-1]
        self.assertTrue(authored.is_watertight)
        with self.assertRaisesRegex(MeshNormalizationError, "winding is inconsistent"):
            canonical_mesh(authored)

    def test_small_real_seam_is_not_hidden_by_coarser_vertex_welding(self):
        authored = trimesh.creation.box()
        authored.unmerge_vertices()
        authored.vertices[0, 0] += 1e-7
        with self.assertRaisesRegex(MeshNormalizationError, "must be watertight"):
            canonical_mesh(authored)

    def test_cached_visual_normals_do_not_split_physical_topology(self):
        authored = _read_stl(trimesh.creation.box().export(file_type="stl"))
        authored.unmerge_vertices()
        authored.vertex_normals = np.repeat(authored.face_normals, 3, axis=0)
        self.assertClosed(canonical_mesh(authored))

    def test_float32_collapsed_faces_are_absent_from_actual_stl_bytes(self):
        authored = _subdivided_box()
        self.assertClosed(authored)
        original = authored.vertices.copy()
        unnormalized = _read_stl(authored.export(file_type="stl"))
        self.assertEqual(int(np.count_nonzero(unnormalized.area_faces == 0)), 2)
        self.assertFalse(unnormalized.is_watertight)

        payload = normalized_stl_bytes(authored)
        readback = _read_stl(payload)

        self.assertClosed(readback)
        self.assertEqual(len(readback.faces), 12)
        np.testing.assert_array_equal(readback.bounds, authored.bounds)
        self.assertAlmostEqual(readback.volume, authored.volume, places=9)
        np.testing.assert_array_equal(authored.vertices, original)
        self.assertEqual(normalized_stl_bytes(readback), payload)

    def test_float32_loss_of_a_real_volume_is_rejected_before_file_publication(self):
        authored = trimesh.creation.box(extents=[10.0, 10.0, 1e-7])
        authored.apply_translation([0.0, 0.0, 10.0])
        self.assertClosed(authored)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "existing.stl"
            destination.write_bytes(b"previous successful artifact")
            with self.assertRaisesRegex(
                geometry_binding.GeometryBindingError, "after STL float32 quantization"
            ):
                with patch.object(geometry_binding, "shape_to_mesh", return_value=authored):
                    geometry_binding.export_shape_stl(Box(10.0, 10.0, 1.0), destination)
            self.assertEqual(destination.read_bytes(), b"previous successful artifact")

    def test_bound_brep_digest_identifies_the_normalized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "body.stl"
            node = geometry_binding.bind_brep_feature(
                node_id="body", feature_id="body/outer", role="solid",
                shape=Box(10.0, 10.0, 10.0), path=destination,
            )
            payload = destination.read_bytes()
            self.assertEqual(node["recipe"]["parameters"]["geometry"]["sha256"], sha256(payload).hexdigest())
            self.assertClosed(_read_stl(payload))

    def test_consumer_enclosure_fillets_bind_and_survive_stl_roundtrip(self):
        for dimensions, radius in (((150, 112, 118), 18), ((72, 3, 44), 1.2)):
            with self.subTest(dimensions=dimensions, radius=radius):
                box = Box(*dimensions)
                shape = fillet(box.edges(), radius)
                self.assertTrue(shape.is_valid)
                vertices, faces = shape.tessellate(0.02, 0.1)
                raw = trimesh.Trimesh(
                    vertices=[[v.X, v.Y, v.Z] for v in vertices], faces=faces, process=False,
                )
                raw.merge_vertices()
                # These fixtures have eight zero-area faces in the bundled
                # tessellator. Preserve the assertion if a future tessellator
                # eliminates them: only truly zero-area faces may disappear.
                zero_area_count = int(np.count_nonzero(raw.area_faces == 0))
                normalized = geometry_binding.shape_to_mesh(shape, "rounded enclosure")
                self.assertClosed(normalized)
                self.assertEqual(len(normalized.faces), len(raw.faces) - zero_area_count)
                np.testing.assert_allclose(normalized.bounds, raw.bounds, atol=0, rtol=0)
                self.assertAlmostEqual(normalized.volume, raw.volume, places=8)
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "rounded.stl"
                    geometry_binding.bind_brep_feature(
                        node_id="rounded", feature_id="body/rounded", role="solid",
                        shape=shape, path=destination,
                    )
                    self.assertClosed(_read_stl(destination.read_bytes()))


if __name__ == "__main__":
    unittest.main()
