from __future__ import annotations

import contextlib
from hashlib import sha256
import importlib.util
import io
from itertools import combinations
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from build123d import Align, Box, Pos
import trimesh
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


plate_layout = load_module("plate_layout_regression", SKILL / "plate_layout.py")
cad_helpers = load_module("cad_helpers_plate_regression", SKILL / "cad_helpers.py")
bambu_profile = load_module("bambu_profile_plate_regression", SKILL / "bambu_profile.py")
from tests.python.intent_fixture import write_intent as write_fixture_intent  # noqa: E402


COORDINATE_SYSTEM = {
    "back": "y-max",
    "bottom": "z-min",
    "front": "y-min",
    "left": "x-min",
    "right": "x-max",
    "top": "z-max",
    "x_positive": "right",
    "y_positive": "back",
    "z_positive": "top",
}


def _write_scene(root: Path, intent_path: Path) -> Path:
    scene_path = root / "shelf-case_scene.json"
    scene_path.write_text(json.dumps({
        "schema": "evidence-semantic-scene/v1",
        "revision": "plate-layout-test-001",
        "intentRef": {
            "path": str(intent_path),
            "schema": "evidence-cad-intent/v5",
            "sha256": sha256(intent_path.read_bytes()).hexdigest(),
        },
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "materials": [],
        "parts": [
            {"id": name, "representationMaster": "brep"}
            for name in ("large", "medium")
        ],
        "nodes": [
            {
                "id": feature_id,
                "partId": owner,
                "featureId": feature_id,
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "roundedBox",
                    "parameters": {"sizeMm": [1, 1, 1], "radiusMm": 0.0},
                },
            }
            for feature_id, owner in (
                ("large-envelope", "large"),
                ("large-face", "large"),
                ("medium-envelope", "medium"),
                ("medium-face", "medium"),
            )
        ],
        "interfaces": [
            {
                "id": "case-glue-face",
                "kind": "glue-face",
                "male": {
                    "partId": "large",
                    "featureId": "large-face",
                    "dimensionsMm": {"depth": 70.0},
                },
                "female": {
                    "partId": "medium",
                    "featureId": "medium-face",
                    "dimensionsMm": {"depth": 70.0},
                },
            }
        ],
    }), encoding="utf-8")
    return scene_path


class PlateLayoutAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.profile = bambu_profile.resolve_profile(
            bambu_profile.load_catalog(),
            machine_name="a1-mini",
            nozzle=0.4,
            tool_index=0,
        )

    def test_shelf_layout_fits_when_serial_x_does_not_and_is_deterministic(self):
        boxes = {
            "large": {"min": [0, 0, 0], "max": [100, 70, 5]},
            "medium": {"min": [200, -10, 2], "max": [290, 60, 7]},
        }
        # The previous layout required 100 + 5 + 90 = 195 mm on X.
        self.assertGreater(100 + 5 + 90, 180)
        result = plate_layout.pack_bboxes(boxes, self.profile, spacing_mm=5)
        reversed_result = plate_layout.pack_bboxes(
            dict(reversed(list(boxes.items()))), self.profile, spacing_mm=5
        )

        self.assertEqual(result["strategy"], "deterministic-bbox-shelf")
        self.assertEqual(result["scale"], 1.0)
        self.assertFalse(result["auto_scale"])
        self.assertTrue(result["fits"])
        self.assertEqual(result["bbox_overlaps"], [])
        self.assertEqual(result["order"], reversed_result["order"])
        self.assertEqual(result["transforms"], reversed_result["transforms"])
        self.assertEqual(len(result["shelves"]), 2)
        self.assertTrue(all(
            "rotate" not in record
            for record in result["parts"].values()
        ))

        first = result["parts"]["large"]["plate_bbox_mm"]
        second = result["parts"]["medium"]["plate_bbox_mm"]
        overlap_x = min(first["max"][0], second["max"][0]) - max(
            first["min"][0], second["min"][0]
        )
        overlap_y = min(first["max"][1], second["max"][1]) - max(
            first["min"][1], second["min"][1]
        )
        self.assertFalse(overlap_x > 0 and overlap_y > 0)

    def test_rotation_applies_to_geometry_and_transform_without_scaling(self):
        profile = json.loads(json.dumps(self.profile))
        profile["machine"]["selected_tool"]["polygon_mm"] = [[10, 20], [110, 20], [110, 80], [10, 80]]
        shape = Pos(-9, 13, -4) * Box(50, 90, 5, align=(Align.MIN, Align.MIN, Align.MIN))
        _, placed, transforms, layout = cad_helpers._print_plate({"panel": shape}, profile=profile)
        self.assertEqual(layout["rotations"]["panel"], [0, 0, 90])
        bounds = placed["panel"].bounding_box()
        self.assertAlmostEqual(bounds.min.X, 10)
        self.assertAlmostEqual(bounds.min.Y, 20)
        self.assertAlmostEqual(bounds.min.Z, 0)
        self.assertAlmostEqual(bounds.size.X, 90)
        self.assertAlmostEqual(bounds.size.Y, 50)
        self.assertAlmostEqual(placed["panel"].volume, shape.volume)
        from coordinate_frames import transform_bounds
        actual = transform_bounds([[-9, 13, -4], [41, 103, 1]], np.asarray(transforms["panel"]["matrix"]))
        np.testing.assert_allclose(actual[0], layout["parts"]["panel"]["plate_bbox_mm"]["min"], atol=1e-6)
        np.testing.assert_allclose(actual[1], layout["parts"]["panel"]["plate_bbox_mm"]["max"], atol=1e-6)

    def test_early_plan_honors_margin_plate_limit_and_distinguishes_oversize(self):
        boxes = {f"part-{i}": {"min": [0, 0, 0], "max": [100, 100, 5]} for i in range(3)}
        before = json.dumps(self.profile, sort_keys=True)
        restricted = plate_layout.plan_plates(boxes, self.profile, max_plates=1, edge_margin_mm=4)
        accepted = plate_layout.plan_plates(dict(reversed(list(boxes.items()))), self.profile, max_plates=3, edge_margin_mm=4)
        self.assertFalse(restricted["pass"])
        self.assertEqual(restricted["status"], "layout-not-found")
        self.assertTrue(accepted["pass"])
        self.assertFalse(accepted["manufacturingValidated"])
        self.assertEqual(restricted["plates"], accepted["plates"])
        self.assertEqual(len(accepted["plates"]), 3)
        self.assertEqual(before, json.dumps(self.profile, sort_keys=True))
        for plate in accepted["plates"]:
            for part in plate["layout"]["parts"].values():
                self.assertGreaterEqual(min(part["plate_bbox_mm"]["min"][:2]), 4)
                self.assertLessEqual(max(part["plate_bbox_mm"]["max"][:2]), 176)
        too_big = plate_layout.plan_plates({"piece": {"min": [0, 0, 0], "max": [181, 181, 5]}}, self.profile, max_plates=5)
        self.assertFalse(too_big["pass"])
        self.assertEqual(too_big["issues"][0]["kind"], "part-exceeds-volume")

    def test_recovers_unused_space_above_short_parts(self):
        # Vary sizes and counts: successful layouts must not depend on a product name.
        for large, small, count in (([108, 86, 5], 14, 9), ([100, 80, 7], 12, 12)):
            with self.subTest(large=large, small=small):
                boxes = {f"large-{i}": {"min": [0, 0, 0], "max": large} for i in range(2)}
                boxes.update({f"small-{i}": {"min": [-3, 7, -2], "max": [small - 3, small + 7, 2]} for i in range(count)})
                result = plate_layout.pack_bboxes(boxes, self.profile, spacing_mm=5)
                other = plate_layout.pack_bboxes(dict(reversed(list(boxes.items()))), self.profile, spacing_mm=5)
                self.assertEqual(result["transforms"], other["transforms"])
                self.assertEqual(result["scale"], 1)
                placed = [p["plate_bbox_mm"] for p in result["parts"].values()]
                for name, part in result["parts"].items():
                    self.assertEqual(part["plate_bbox_mm"]["size"], [boxes[name]["max"][i] - boxes[name]["min"][i] for i in range(3)])
                for p in placed:
                    self.assertGreaterEqual(min(p["min"]), 0)
                    self.assertLessEqual(max(p["max"][:2]), 180)
                for a, b in combinations(placed, 2):
                    self.assertTrue(any(a["max"][i] + 5 <= b["min"][i] or b["max"][i] + 5 <= a["min"][i] for i in (0, 1)))

    def test_free_rectangle_fallback_respects_exclusions_and_bed_edges(self):
        limits = {"bounds_mm": [10, 20, 110, 120], "excluded_bounds_mm": [[10, 20, 35, 120]]}
        parts = {"a": {"size": [70, 100, 5]}}
        result = plate_layout._pack_free_rectangles(["a"], parts, limits, 5)
        self.assertIsNotNone(result)
        self.assertEqual(result["placements"]["a"]["plate_bbox_xy_mm"], [40, 20, 110, 120])
        parts["a"]["size"][0] = 71
        self.assertIsNone(plate_layout._pack_free_rectangles(["a"], parts, limits, 5))

    def test_layout_fails_closed_instead_of_scaling(self):
        boxes = {
            name: {"min": [0, 0, 0], "max": [100, 100, 5]}
            for name in ("a", "b", "c")
        }
        with self.assertRaisesRegex(
            plate_layout.PlateLayoutError,
            r"failed at scale=1.*scaling is disabled",
        ):
            plate_layout.pack_bboxes(boxes, self.profile, spacing_mm=5)

        with self.assertRaisesRegex(
            plate_layout.PlateLayoutError,
            r"width 181>180.*scaling is disabled",
        ):
            plate_layout.pack_bboxes(
                {"wide": {"min": [0, 0, 0], "max": [181, 20, 5]}},
                self.profile,
            )


class ExportAssemblyPlateLayoutTests(unittest.TestCase):
    def setUp(self):
        cad_helpers._FEATURES.clear()
        cad_helpers._EVENTS.clear()
        cad_helpers._PARAMETERS.clear()

    def test_export_assembly_uses_bound_profile_shelf_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = bambu_profile.resolve_profile(
                bambu_profile.load_catalog(),
                machine_name="a1-mini",
                nozzle=0.4,
                tool_index=0,
            )
            profile_path = root / "a1-mini-profile.json"
            profile_path.write_text(
                bambu_profile.serialize(profile), encoding="utf-8"
            )
            manufacturing = {
                    "mode": "multipart",
                    "parts": [
                        {"name": "large", "role": "shell", "acceptance": "solid"},
                        {"name": "medium", "role": "lid", "acceptance": "solid"},
                    ],
                    "interfaces": [
                        {
                            "id": "case-glue-face",
                            "between": ["large", "medium"],
                            "connection": "glue-face",
                            "assembly_axis": "+Z",
                            "engagement_mm": 1.0,
                            "features": ["large-face", "medium-face"],
                            "acceptance": "flat faces align for assembly",
                        }
                    ],
            }
            intent_path, intent = write_fixture_intent(
                root,
                part="shelf-case",
                feature_owners={
                    "large-envelope": "large",
                    "large-face": "large",
                    "medium-envelope": "medium",
                    "medium-face": "medium",
                },
                manufacturing=manufacturing,
                dimensions_mm=(290.0, 70.0, 5.0),
            )
            intent["printability"]["profile"] = {
                "path": str(profile_path),
                "sha256": sha256(profile_path.read_bytes()).hexdigest(),
            }
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            scene_path = _write_scene(root, intent_path)

            large = Box(100, 70, 5, align=(Align.MIN, Align.MIN, Align.MIN))
            medium = Pos(200, 0, 0) * Box(
                90, 70, 5, align=(Align.MIN, Align.MIN, Align.MIN)
            )
            cad_helpers.observe(large, "large-envelope", "part", part_name="large")
            cad_helpers.observe(large, "large-face", "interface", part_name="large")
            cad_helpers.observe(
                medium, "medium-envelope", "part", part_name="medium"
            )
            cad_helpers.observe(
                medium, "medium-face", "interface", part_name="medium"
            )
            with contextlib.redirect_stdout(io.StringIO()):
                report = cad_helpers.export_assembly(
                    {"medium": medium, "large": large},
                    "shelf-case",
                    str(root),
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    source_path=__file__,
                )

            layout = report["coordinateFrames"]["plate-print"]["layout"]
            self.assertEqual(layout["strategy"], "deterministic-bbox-shelf")
            self.assertEqual(layout["bed"]["size_mm"], [180.0, 180.0])
            self.assertEqual(layout["bbox_overlaps"], [])
            self.assertEqual(report["scale"], 1.0)
            self.assertTrue(all(
                len(transform) == 4 and transform[3] == [0.0, 0.0, 0.0, 1.0]
                for transform in report["coordinateFrames"]["plate-print"]["partTransforms"].values()
            ))

            plate_mesh = trimesh.load(root / "shelf-case.stl", force="mesh")
            self.assertLessEqual(float(plate_mesh.extents[0]), 180.0)
            self.assertLessEqual(float(plate_mesh.extents[1]), 180.0)
            self.assertAlmostEqual(float(plate_mesh.extents[0]), 100.0, places=4)
            self.assertAlmostEqual(float(plate_mesh.extents[1]), 145.0, places=4)
            components = plate_mesh.split(only_watertight=False)
            self.assertEqual(len(components), 2)
            left, right = components
            left_bounds = left.bounds
            right_bounds = right.bounds
            overlap_x = min(left_bounds[1, 0], right_bounds[1, 0]) - max(
                left_bounds[0, 0], right_bounds[0, 0]
            )
            overlap_y = min(left_bounds[1, 1], right_bounds[1, 1]) - max(
                left_bounds[0, 1], right_bounds[0, 1]
            )
            self.assertFalse(overlap_x > 0 and overlap_y > 0)

            # A current packing failure preserves geometry, but never produces
            # a successful manufacturing manifest in the failed output folder.
            failed_dir = root / "failed-layout"
            diagnostics = failed_dir / "source-diagnostics.json"
            failure = cad_helpers.PlateLayoutError("heuristic layout not found", kind="layout-not-found")
            wrapped = cad_helpers.BuildInvariantError(str(failure))
            wrapped.__cause__ = failure
            run_id = str(uuid4())
            with patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": "compile",
                "AMAGINE3D_COMPILE_RUN_ID": run_id, "AMAGINE3D_SOURCE_DIAGNOSTICS_PATH": str(diagnostics),
                "AMAGINE3D_OUTPUT_DIR": str(failed_dir)}), patch.object(cad_helpers, "_print_plate", side_effect=wrapped), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(cad_helpers.BuildInvariantError):
                    cad_helpers.export_assembly({"medium": medium, "large": large}, "shelf-case", str(failed_dir),
                        intent_path=str(intent_path), scene_path=str(scene_path), source_path=__file__)
            payload = json.loads(diagnostics.read_text())
            self.assertFalse(payload["pass"])
            self.assertEqual(payload["issues"][0]["code"], "SOURCE.PLATE_LAYOUT_FAILED")
            candidate = payload["diagnosticCandidate"]
            self.assertEqual(candidate["runId"], run_id)
            self.assertFalse(candidate["manufacturingValidated"])
            self.assertEqual(candidate["inputBindings"]["intent"]["sha256"], sha256(intent_path.read_bytes()).hexdigest())
            for artifact in candidate["artifacts"].values():
                path = Path(artifact["path"])
                self.assertEqual(artifact["sha256"], sha256(path.read_bytes()).hexdigest())
            self.assertFalse((failed_dir / "shelf-case_report.json").exists())
            self.assertFalse((failed_dir / "shelf-case.3mf").exists())


if __name__ == "__main__":
    unittest.main()
