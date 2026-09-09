"""Real geometry and malformed-inventory coverage for multiple print beds."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

import numpy as np
from build123d import Box, Pos

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "text-a3d"))
import bambu_profile
import cad_helpers
from build_manifest import _geometry_errors
from print_plates import plate_collection_errors, print_plates
from qa_check import _affected_features
from color.qa_check import _affected


class PrintPlateTests(unittest.TestCase):
    def setUp(self):
        self.profile = bambu_profile.resolve_profile(bambu_profile.load_catalog(), machine_name="a1-mini", nozzle=0.4, tool_index=0)

    def test_flat_parts_keep_broad_contact_on_separate_plates(self):
        parts = {"a": Box(170, 170, 30), "b": Pos(0, 0, 30) * Box(170, 170, 30)}
        poses, oriented, plates = cad_helpers._assembly_print_plates(parts, self.profile)
        self.assertEqual(len(plates), 2)
        self.assertEqual({name for _, placed, _, _ in plates for name in placed}, set(parts))
        for shape, placed, transforms, layout in plates:
            self.assertEqual(len(shape.solids()), 1)
            self.assertAlmostEqual(shape.bounding_box().min.Z, 0)
            self.assertAlmostEqual(shape.bounding_box().size.Z, 30)
            self.assertLessEqual(shape.bounding_box().max.X, 180)
            self.assertLessEqual(shape.bounding_box().max.Y, 180)
            name = next(iter(placed))
            self.assertAlmostEqual(poses[name]["selected"]["orientation_metrics"]["contact_area_mm2"], 170 * 170)

    def test_explicit_one_plate_limit_and_oversized_parts_still_fail(self):
        with self.assertRaises(cad_helpers.BuildInvariantError):
            cad_helpers._assembly_print_plates({"a": Box(170,170,170), "b": Box(170,170,170)}, self.profile,
                intent_data={"printability": {"max_plates": 1}})
        with self.assertRaises(cad_helpers.BuildInvariantError):
            cad_helpers._assembly_print_plates({"a": Box(200,200,200)}, self.profile)

    def report(self):
        identity = np.eye(4).tolist()
        geometry = {"bodyCount": 1, "boundsMm": {"min": [0,0,0], "max": [1,1,1], "size": [1,1,1]}, "isVolume": True, "valid": True, "volumeMm3": 1}
        plates = [{"id": pid, "parts": [owner], "stlKey": stl, "threeMfKey": mf,
            "geometry": {**geometry, "layout": {"transforms": {owner: identity}}}}
            for pid, owner, stl, mf in [("01", "a", "stl", "3mf"), ("02", "b", "plate:02:stl", "plate:02:3mf")]]
        return {"backend": "brep-assembly", "parts": {p: {"semantic": geometry} for p in ("a", "b")},
            "backendData": {"printPlate": plates[0]["geometry"], "printPlates": plates},
            "artifacts": {key: {"path": key, "coordinateFrame": "plate-print", **({"verified": True, "validator": "lib3mf"} if key.endswith("3mf") else {})}
                for plate in plates for key in (plate["stlKey"], plate["threeMfKey"])},
            "coordinateFrames": {"plate-print": {"partTransforms": {p: identity for p in ("a", "b")}}},
            "features": {p: {"part": p, "bbox_mm": {"min": [0,0,0], "max": [1,1,1]}} for p in ("a", "b")}}

    def test_inventory_errors_fail_closed(self):
        original = self.report()
        self.assertEqual(plate_collection_errors(original, _geometry_errors), [])
        for invalid in (None, {}, [None, {}], [original["backendData"]["printPlates"][0]]):
            report = deepcopy(original)
            report["backendData"]["printPlates"] = invalid
            print_plates(report)
            self.assertTrue(plate_collection_errors(report, _geometry_errors))
        for field, value in (("id", "03"), ("parts", ["a"]), ("parts", []), ("stlKey", []), ("geometry", None)):
            report = deepcopy(original)
            report["backendData"]["printPlates"][1][field] = value
            self.assertTrue(plate_collection_errors(report, _geometry_errors), (field, value))
        for field, value in (("path", []), ("path", "stl"), ("verified", False)):
            report = deepcopy(original)
            report["artifacts"]["plate:02:3mf"][field] = value
            self.assertTrue(plate_collection_errors(report, _geometry_errors))

    def test_risk_attribution_cannot_cross_local_plate_coordinates(self):
        report = self.report()
        for affected in (_affected_features, _affected):
            self.assertEqual(affected(np.array([[0,0,0],[1,1,1]]), report, artifact_key="plate:02:stl")["feature_ids"], ["b"])


if __name__ == "__main__":
    unittest.main()
