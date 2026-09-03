from __future__ import annotations

from pathlib import Path
import sys
import unittest

import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import mesh_topology  # noqa: E402


class MeshTopologyTests(unittest.TestCase):
    def test_closed_cavity_is_one_material_body(self) -> None:
        hollow = trimesh.boolean.difference(
            [
                trimesh.creation.box(extents=[20, 20, 20]),
                trimesh.creation.box(extents=[16, 16, 16]),
            ],
            engine="manifold",
            check_volume=True,
        )
        self.assertIsInstance(hollow, trimesh.Trimesh)
        self.assertEqual(len(hollow.split(only_watertight=False)), 2)
        self.assertEqual(mesh_topology.physical_body_count(hollow), 1)

    def test_disconnected_solids_are_two_material_bodies(self) -> None:
        left = trimesh.creation.box(extents=[4, 4, 4])
        right = left.copy()
        left.apply_translation([-5, 0, 0])
        right.apply_translation([5, 0, 0])
        combined = trimesh.util.concatenate([left, right])

        self.assertEqual(mesh_topology.physical_body_count(combined), 2)

    def test_open_surface_has_no_manufacturing_body_count(self) -> None:
        open_box = trimesh.creation.box(extents=[4, 4, 4])
        open_box.update_faces(range(len(open_box.faces) - 1))
        with self.assertRaises(mesh_topology.MeshTopologyError):
            mesh_topology.physical_body_count(open_box)

        facts = mesh_topology.mesh_topology_facts(open_box)
        self.assertFalse(facts["watertight"])
        self.assertFalse(facts["isVolume"])
        self.assertIsNone(facts["bodyCount"])
        self.assertGreater(facts["boundaryEdgeCount"], 0)
        self.assertEqual(facts["nonManifoldEdgeCount"], 0)


if __name__ == "__main__":
    unittest.main()
