"""Real curved BRep precision, cache isolation and open-face triangulation."""
import math
from pathlib import Path
import sys
import tempfile
import unittest

from build123d import Circle, Cylinder, Ellipse, Pos, RectangleRounded, Sphere, Unit, export_step, loft
import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.TopLoc import TopLoc_Location
import trimesh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "text-a3d"))
from brep_tessellation import tessellate_brep
from export_audit import BOUNDS_TOLERANCE_MM, _audit_step, _audit_stl, geometry_record
from geometry_binding import export_shape_stl
from mesh_normalization import canonical_mesh


def triangle_mesh(shape, linear=.02, angular=.1):
    vertices, faces = tessellate_brep(shape, linear, angular)
    return trimesh.Trimesh(vertices=[list(vertex) for vertex in vertices], faces=faces, process=False)


class BRepTessellationTests(unittest.TestCase):
    def test_finer_angle_ignores_existing_cache_and_leaves_original_unchanged(self):
        shape = Sphere(20)
        BRepTools.Clean_s(shape.wrapped)
        BRepMesh_IncrementalMesh(shape.wrapped, .1, False, .6, True)
        face = shape.faces()[0]
        cached = BRep_Tool.Triangulation_s(face.wrapped, TopLoc_Location())
        old_count, old_deflection = cached.NbTriangles(), cached.Deflection()
        original, volume = shape.wrapped, shape.volume
        # A linear-only cache check permits this old mesh despite the new angle.
        self.assertTrue(BRepTools.Triangulation_s(shape.wrapped, 1.0))
        _, old_faces = shape.tessellate(1.0, .05)
        self.assertEqual(len(old_faces), old_count)

        fresh = canonical_mesh(triangle_mesh(shape, 1.0, .05), "fresh angular precision")
        self.assertGreater(len(fresh.faces), old_count * 8)
        self.assertLess(abs(fresh.volume - shape.volume) / shape.volume, .002)
        after = BRep_Tool.Triangulation_s(face.wrapped, TopLoc_Location())
        self.assertEqual(after.NbTriangles(), old_count)
        self.assertEqual(after.Deflection(), old_deflection)
        self.assertTrue(shape.wrapped.IsSame(original))
        self.assertTrue(shape.is_valid)
        self.assertAlmostEqual(shape.volume, volume, places=9)

    def test_ellipse_loft_exports_match_brep_and_step_at_existing_bounds_tolerance(self):
        stations = [(0, 50, 42, 0), (8, 58, 48, 0), (21, 76, 59, 0),
                    (36, 81.2, 61.5, 0), (52, 81.2, 61.5, 0),
                    (67, 74, 58, 1), (80, 63, 51, 3), (95, 54, 46, 5)]

        def section(station, inset=0):
            z, width, depth, y = station
            return Pos(0, y, z) * Ellipse(width / 2 - inset, depth / 2 - inset)

        floor = tuple(a + (b - a) * 5 / 8 for a, b in zip(stations[0], stations[1]))
        inner = [floor, *stations[1:], (96, *stations[-1][1:])]
        shape = loft([section(s) for s in stations]) - loft([section(s, 4.2) for s in inner])
        expected = geometry_record(shape)
        with tempfile.TemporaryDirectory() as directory:
            stl, step = (Path(directory) / name for name in ("cup.stl", "cup.step"))
            export_step(shape, step, unit=Unit.MM)
            export_shape_stl(shape, stl, linear_tolerance_mm=.01, angular_tolerance_rad=.1)
            stl_audit, step_audit = _audit_stl(stl, expected, "cup"), _audit_step(step, expected, "cup")
            self.assertTrue(stl_audit["pass"], stl_audit["errors"])
            self.assertTrue(step_audit["pass"], step_audit["errors"])
            self.assertEqual(BOUNDS_TOLERANCE_MM, .05)
            for field in ("min", "max", "size"):
                np.testing.assert_allclose(
                    stl_audit["observed"]["boundsMm"][field], step_audit["observed"]["boundsMm"][field],
                    atol=BOUNDS_TOLERANCE_MM, rtol=0,
                )

    def test_smooth_loft_with_through_hole_retains_volume_and_winding(self):
        sections = [(0, 72, 48, 8), (10, 80, 56, 12),
                    (30, 78, 58, 14), (50, 60, 48, 10)]
        outer = loft([Pos(0, 0, z) * RectangleRounded(width, depth, radius)
                      for z, width, depth, radius in sections])
        shape = outer - Pos(0, 0, 25) * Cylinder(12, 70)
        mesh = canonical_mesh(triangle_mesh(shape), "curved hollow loft")
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertGreater(mesh.volume, 0)
        self.assertLess(abs(mesh.volume - shape.volume) / shape.volume, .0015)
        self.assertTrue(np.allclose(mesh.bounds[:, 2], [0, 50], atol=.02))

    def test_same_mm_precision_refines_a_larger_curved_object(self):
        counts = []
        for radius in (10, 100):
            mesh = triangle_mesh(Sphere(radius), .05, 1.0)
            counts.append(len(mesh.faces))
            # Face centroids are inside an inscribed sphere mesh. Their radial
            # distances measure actual chord error in millimetres at both scales.
            chord_errors = radius - np.linalg.norm(mesh.triangles_center, axis=1)
            self.assertLess(float(chord_errors.max()), .075)
        self.assertGreater(counts[1], counts[0] * 5)

    def test_open_display_face_preserves_annular_hole_and_placement(self):
        ring = Pos(7, -3, 11) * (Circle(20) - Circle(8))
        mesh = triangle_mesh(ring, .01, .1)
        self.assertFalse(mesh.is_watertight)
        self.assertTrue(np.allclose(mesh.vertices[:, 2], 11))
        expected_area = math.pi * (20 ** 2 - 8 ** 2)
        self.assertLess(abs(mesh.area - expected_area) / expected_area, .001)
        self.assertTrue(np.all(mesh.face_normals[:, 2] > .99))


if __name__ == "__main__":
    unittest.main()
