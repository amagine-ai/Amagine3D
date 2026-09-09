"""Exercise real assembly exports: manufacturing rotation must not move STEP."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import unittest

import numpy as np
import trimesh
from build123d import Align, Box, Pos, fillet, import_step

SKILL = Path(__file__).resolve().parents[2] / "skills" / "text-a3d"
sys.path.insert(0, str(SKILL))
import bambu_profile
import cad_helpers
from authoring import write_intent, write_scene
from geometry_binding import bind_brep_feature


class AssemblyOrientationTests(unittest.TestCase):
    def test_joint_poses_fit_when_independent_flat_poses_do_not(self):
        profile = bambu_profile.resolve_profile(bambu_profile.load_catalog(), machine_name="a1-mini", nozzle=0.4, tool_index=0)
        parts = {"left": Box(100, 100, 20), "right": Pos(200, 0, 0) * Box(100, 100, 20)}
        originals = {name: shape.bounding_box() for name, shape in parts.items()}
        preferred = {name: cad_helpers._apply_print_orientation(shape, cad_helpers._select_print_orientation(shape, profile)) for name, shape in parts.items()}
        with self.assertRaises(cad_helpers.BuildInvariantError):
            cad_helpers._print_plate(preferred, profile=profile)
        orientations, oriented, (plate, placed, transforms, layout) = cad_helpers._assembly_print_layout(parts, profile)
        self.assertGreater(layout["orientationSearch"]["attempts"], 1)
        self.assertLessEqual(plate.bounding_box().size.X, 180)
        self.assertLessEqual(plate.bounding_box().size.Y, 180)
        for name, pose in orientations.items():
            self.assertTrue(pose["selected"]["orientation_metrics"]["center_inside_contact_bounds"])
            self.assertGreater(pose["selected"]["orientation_metrics"]["contact_area_mm2"], 0)
            self.assertAlmostEqual(placed[name].volume, parts[name].volume, places=5)
            self.assertAlmostEqual(placed[name].bounding_box().min.Z, 0, places=5)
            current = parts[name].bounding_box()
            np.testing.assert_allclose(tuple(current.min), tuple(originals[name].min))
            np.testing.assert_allclose(tuple(current.max), tuple(originals[name].max))
        # Insertion order does not change the selected physical layout.
        repeated = cad_helpers._assembly_print_layout(dict(reversed(list(parts.items()))), profile)
        self.assertEqual(layout, repeated[2][3])

    def test_joint_search_preserves_failure_when_stable_parts_cannot_share_bed(self):
        profile = bambu_profile.resolve_profile(bambu_profile.load_catalog(), machine_name="a1-mini", nozzle=0.4, tool_index=0)
        with self.assertRaises(cad_helpers.BuildInvariantError) as raised:
            cad_helpers._assembly_print_layout({"a": Box(170, 170, 170), "b": Box(170, 170, 170)}, profile)
        self.assertEqual(raised.exception.__cause__.kind, "layout-not-found")

    def test_joint_search_does_not_pack_curved_covers_on_noncontacting_edges(self):
        profile = bambu_profile.resolve_profile(bambu_profile.load_catalog(), machine_name="a1-mini", nozzle=0.4, tool_index=0)
        profile["machine"]["selected_tool"]["polygon_mm"] = [[0,0],[110,0],[110,110],[0,110]]
        envelope = Box(80, 50, 100)
        cover = fillet(envelope.edges(), 16) & (Pos(0,24,0)*Box(100,12,120))
        cover += Pos(0,10,0)*Box(10,20,20)
        candidates = cad_helpers._orientation_candidates(cover, profile)
        standing = next(c for c in candidates if c["name"] == "identity")
        self.assertEqual(standing["orientation_metrics"]["contact_area_mm2"], 0)
        unsafe = cad_helpers._apply_print_orientation(cover, {"selected":standing})
        # An unsafe packing exists, but the automatic fallback must reject it.
        cad_helpers._print_plate({"a":unsafe,"b":unsafe}, profile=profile)
        with self.assertRaises(cad_helpers.BuildInvariantError):
            cad_helpers._assembly_print_layout({"a":cover,"b":cover}, profile)

    def test_joint_search_does_not_mask_unrelated_geometry_failures(self):
        with patch.object(cad_helpers, "_print_plate", side_effect=cad_helpers.BuildInvariantError("unrelated geometry error")):
            with self.assertRaisesRegex(cad_helpers.BuildInvariantError, "unrelated geometry error"):
                cad_helpers._assembly_print_layout({"a": Box(10, 10, 10)}, {})

    def test_lid_flips_for_stl_and_3mf_while_semantic_step_stays_assembled(self):
        cad_helpers._FEATURES.clear()
        cad_helpers._EVENTS.clear()
        cad_helpers._PARAMETERS.clear()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = bambu_profile.resolve_profile(bambu_profile.load_catalog(), machine_name="a1-mini", nozzle=0.4, tool_index=0)
            profile_path = root / "profile.json"
            profile_path.write_text(bambu_profile.serialize(profile))
            align = (Align.CENTER, Align.CENTER, Align.MIN)
            tray = Box(40, 30, 12, align=align) - Pos(0, 0, 2) * Box(36, 26, 11, align=align)
            lid = Pos(0, 0, 12) * Box(40, 30, 2, align=align)
            for x in (-10, 10):
                for y in (-8, 8):
                    lid += Pos(x, y, 5) * Box(2, 2, 8, align=align)
            cad_helpers.observe(tray, "tray", role="solid", part_name="tray")
            cad_helpers.observe(lid, "lid", role="solid", part_name="lid")
            intent = root / "case-intent.json"
            write_intent(intent, profile_path=profile_path, part="case", task_mode="specification",
                representation="full-3d", dimensions_mm={a: {"value": v, "source": "user", "confidence": "high"} for a, v in zip("xyz", (40, 30, 14))},
                manufacturing_mode="multipart",
                parts={p: {"role": p, "installation": "loose", "acceptance": "intentional loose cover placement for orientation test", "features": [{"id": p, "kind": "envelope", "evidence": "test solid", "acceptance": "preserve semantic geometry"}]} for p in ("tray", "lid")},
                interfaces=[{"id": "resting-cover", "connection": "glue-face", "assembly_axis": "+Z", "engagement_mm": 1.0, "features": ["tray", "lid"], "acceptance": "face contact without interpenetration"}],
                support_policy="supports-allowed", critical_features=[], reference_view="isometric", landmarks=["tray with a loose top lid"], assumptions=["test assembly"], minimum_wall_target_mm=1.2)
            scene = root / "case-scene.json"
            write_scene(scene, intent_path=intent, parts={p: {"representationMaster": "brep", "nodes": [bind_brep_feature(node_id=p, feature_id=p, role="solid", shape=s, path=root/f"{p}-bound.stl")]} for p,s in (("tray",tray),("lid",lid))}, interfaces=[{"id": "resting-cover", "kind": "glue-face", "male": {"partId": "tray", "featureId": "tray", "dimensionsMm": {"depth": 30}}, "female": {"partId": "lid", "featureId": "lid", "dimensionsMm": {"depth": 30}}}])
            with contextlib.redirect_stdout(io.StringIO()):
                result = cad_helpers.export_assembly({"tray": tray, "lid": lid}, "case", str(root), intent_path=str(intent), scene_path=str(scene), source_path=__file__)
            selected = result["backendData"]["printPlate"]["layout"]["orientations"]["lid"]["selected"]
            self.assertEqual(selected["rotate_degrees_xyz"], [180.0, 0.0, 0.0])
            self.assertAlmostEqual(selected["orientation_metrics"]["overhang_area_mm2"], 0)
            exported_step = import_step(root / "case-lid.step")
            self.assertAlmostEqual(exported_step.bounding_box().min.Z, 5)
            self.assertAlmostEqual(exported_step.bounding_box().max.Z, 14)
            self.assertEqual(len(import_step(root / "case-assemble.step").solids()), 2)
            semantic = None
            for frame, artifact in (("part-print", "stl:lid"), ("plate-print", "plate-stl:lid")):
                mesh = trimesh.load(result["artifacts"][artifact]["path"], force="mesh")
                matrix = np.asarray(result["coordinateFrames"][frame]["partTransforms"]["lid"])
                self.assertAlmostEqual(np.linalg.det(matrix[:3,:3]), 1)
                self.assertAlmostEqual(mesh.bounds[0, 2], 0, places=5)
                mesh.apply_transform(np.linalg.inv(matrix))
                np.testing.assert_allclose(mesh.bounds, [[-20,-15,5],[20,15,14]], atol=1e-5)
                if semantic is not None:
                    self.assertAlmostEqual(mesh.volume, semantic.volume, places=3)
                semantic = mesh
            self.assertTrue(result["artifacts"]["3mf"]["verified"])


if __name__ == "__main__":
    unittest.main()
