from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bambu_profile = load_module("bambu_profile", SKILL / "bambu_profile.py")
qa_check = load_module("qa_check", SKILL / "qa_check.py")


class BambuProfileTests(unittest.TestCase):
    def test_resolves_single_and_dual_tool_limits(self):
        catalog = bambu_profile.load_catalog()
        mini = bambu_profile.resolve_profile(
            catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        self.assertEqual(mini["machine"]["selected_tool"]["height_mm"], 180)
        self.assertEqual(mini["derived"]["process_wall_target_mm"], 0.87)

        h2d = bambu_profile.resolve_profile(
            catalog, machine_name="Bambu Lab H2D", nozzle=0.4, tool_index=1
        )
        polygon = np.asarray(h2d["machine"]["selected_tool"]["polygon_mm"])
        self.assertEqual(float(np.ptp(polygon[:, 0])), 325.0)
        self.assertEqual(h2d["machine"]["selected_tool"]["height_mm"], 325)

    def test_default_is_explicitly_marked_as_assumed(self):
        profile = bambu_profile.resolve_profile(
            bambu_profile.load_catalog(), machine_name=None, nozzle=0.4, tool_index=0
        )
        self.assertEqual(profile["machine"]["id"], "a1-mini")
        self.assertTrue(profile["selection"]["assumed_default_machine"])


class PrintabilityGeometryTests(unittest.TestCase):
    def setUp(self):
        self.catalog = bambu_profile.load_catalog()

    def test_bed_fit_allows_xy_rotation_and_rejects_overflow(self):
        h2s = bambu_profile.resolve_profile(
            self.catalog, machine_name="h2s", nozzle=0.4, tool_index=0
        )
        passed, observed = qa_check.check_bed_fit([310, 330, 20], h2s)
        self.assertTrue(passed)
        self.assertTrue(observed["selected"]["rotated_xy_90deg"])

        mini = bambu_profile.resolve_profile(
            self.catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        passed, _ = qa_check.check_bed_fit([181, 170, 20], mini)
        self.assertFalse(passed)

    def test_excluded_bed_area_can_be_avoided_by_placement(self):
        placement = qa_check.find_bed_placement(
            250,
            250,
            [[0, 0], [256, 0], [256, 256], [0, 256]],
            [[[0, 0], [18, 0], [18, 28], [0, 28]]],
        )
        self.assertIsNone(placement)
        placement = qa_check.find_bed_placement(
            220,
            220,
            [[0, 0], [256, 0], [256, 256], [0, 256]],
            [[[0, 0], [18, 0], [18, 28], [0, 28]]],
        )
        self.assertIsNotNone(placement)

    def test_wall_thickness_sampling_detects_thin_plate(self):
        mesh = trimesh.creation.box(extents=[10, 10, 0.5])
        mesh.apply_translation([0, 0, 0.25])
        observed = qa_check.thickness_observation(
            mesh, target_mm=0.87, sample_limit=128, report=None
        )
        self.assertLess(observed["p05_mm"], 0.87)
        self.assertAlmostEqual(observed["minimum_mm"], 0.5, places=4)

    def test_local_thin_region_is_not_hidden_by_area_weighted_p05(self):
        profile = bambu_profile.resolve_profile(
            self.catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        base = trimesh.creation.box(extents=[40, 24, 1.0])
        base.apply_translation([0, 0, 0.5])
        local_wall = trimesh.creation.box(extents=[20, 0.6, 0.6])
        local_wall.apply_translation([0, 0, 1.3])
        mesh = trimesh.util.concatenate([base, local_wall])
        report = {
            "events": [],
            "features": {
                "local-wall": {
                    "role": "wall",
                    "bbox_mm": {
                        "min": [-10, -0.3, 1.0],
                        "max": [10, 0.3, 1.6],
                        "size": [20, 0.6, 0.6],
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            report_path = root / "report.json"
            mesh_path = root / "local-thin.stl"
            profile_path.write_text(bambu_profile.serialize(profile), encoding="utf-8")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            mesh.export(mesh_path)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(mesh_path),
                    "--profile",
                    str(profile_path),
                    "--report",
                    str(report_path),
                    "--components",
                    "2",
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            p05 = next(
                item for item in payload["checks"]
                if item["name"] == "printability_wall_thickness"
            )
            local = next(
                item for item in payload["checks"]
                if item["name"] == "printability_local_thin_region"
            )
            feature = next(
                item for item in payload["checks"]
                if item["name"] == "printability_feature_resolution"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(p05["status"], "pass")
            self.assertEqual(feature["status"], "pass")
            self.assertEqual(local["status"], "warning")
            self.assertEqual(local["observed"]["affected_feature_ids"], ["local-wall"])
            self.assertIn("printability_local_thin_region", payload["warnings"])

    def test_overhang_ignores_bed_face_and_finds_elevated_ceiling(self):
        base = trimesh.creation.box(extents=[10, 10, 2])
        base.apply_translation([0, 0, 1])
        safe = qa_check.overhang_observation(
            base, threshold_deg=30, build_plane_tolerance=0.5, report=None
        )
        self.assertEqual(safe["face_count"], 0)

        shelf = trimesh.creation.box(extents=[8, 8, 1])
        shelf.apply_translation([0, 0, 5.5])
        combined = trimesh.util.concatenate([base, shelf])
        risky = qa_check.overhang_observation(
            combined, threshold_deg=30, build_plane_tolerance=0.5, report=None
        )
        self.assertGreater(risky["face_count"], 0)
        self.assertEqual(risky["minimum_slope_deg"], 0.0)

    def test_feature_measurements_use_named_build_evidence(self):
        report = {
            "features": {
                "primary": {
                    "role": "envelope",
                    "bbox_mm": {"size": [40, 24, 8]},
                },
                "thin-logo": {
                    "role": "additive",
                    "bbox_mm": {"size": [8, 0.3, 0.6]},
                },
            },
            "events": [
                {
                    "id": "small-hole",
                    "kind": "cut",
                    "tool": {"bbox_mm": {"size": [0.35, 0.35, 10]}},
                }
            ],
        }
        measured = qa_check.feature_measurements(report)
        self.assertEqual(
            {item["feature_id"] for item in measured}, {"thin-logo", "small-hole"}
        )
        self.assertEqual(min(item["minimum_size_mm"] for item in measured), 0.3)

    def test_cli_keeps_warnings_non_blocking_and_bed_overflow_blocking(self):
        profile = bambu_profile.resolve_profile(
            self.catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        report = {
            "events": [],
            "features": {
                "plate": {
                    "role": "additive",
                    "bbox_mm": {"min": [0, 0, 0], "max": [40, 24, 0.5], "size": [40, 24, 0.5]},
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            report_path = root / "report.json"
            thin_path = root / "thin.stl"
            large_path = root / "large.stl"
            profile_path.write_text(bambu_profile.serialize(profile), encoding="utf-8")
            report_path.write_text(json.dumps(report), encoding="utf-8")

            thin = trimesh.creation.box(extents=[40, 24, 0.5])
            thin.apply_translation([0, 0, 0.25])
            thin.export(thin_path)
            warning_result = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(thin_path),
                    "--profile",
                    str(profile_path),
                    "--report",
                    str(report_path),
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            warning_payload = json.loads(warning_result.stdout)
            self.assertEqual(warning_result.returncode, 0)
            self.assertEqual(warning_payload["status"], "pass_with_warnings")
            self.assertIn("printability_wall_thickness", warning_payload["warnings"])

            large = trimesh.creation.box(extents=[181, 20, 8])
            large.apply_translation([0, 0, 4])
            large.export(large_path)
            fail_result = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(large_path),
                    "--profile",
                    str(profile_path),
                    "--report",
                    str(report_path),
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            fail_payload = json.loads(fail_result.stdout)
            self.assertEqual(fail_result.returncode, 1)
            self.assertIn("printability_bed_fit", fail_payload["errors"])


class ContractTests(unittest.TestCase):
    def test_hash_bound_example_profiles_use_stable_lf_bytes(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.json text eol=lf", attributes.splitlines())

        for skill_name in ("text-a3d", "text-a3d-color"):
            examples = ROOT / "skills" / skill_name / "examples"
            intent = json.loads(
                (examples / "intent.example.json").read_text(encoding="utf-8")
            )
            reference = intent["printability"]["profile"]
            payload = (examples / reference["path"]).read_bytes()
            self.assertNotIn(b"\r\n", payload, skill_name)
            self.assertEqual(sha256(payload).hexdigest(), reference["sha256"])

    def test_checked_in_example_contract_is_valid(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SKILL / "intent_contract.py"),
                str(SKILL / "examples" / "intent.example.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["pass"])


if __name__ == "__main__":
    unittest.main()
