from __future__ import annotations

import contextlib
from hashlib import sha256
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from build123d import Align, Box, Pos
import numpy as np
import trimesh


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


bambu_profile = load_module("bambu_profile", SKILL / "bambu_profile.py")
qa_check = load_module("qa_check", SKILL / "qa_check.py")
cad_helpers = load_module("single_cad_helpers", SKILL / "cad_helpers.py")
assembly_check = load_module("single_assembly_check", SKILL / "assembly_check.py")
intent_contract = load_module("single_intent_contract", SKILL / "intent_contract.py")
step_check = load_module("single_step_check", SKILL / "step_check.py")

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

    def test_semantic_feature_placement_detects_wrong_edge_cut(self):
        intent = {
            "features": [
                {
                    "id": "charging-port",
                    "kind": "port",
                    "face": "bottom",
                    "direction": "-Z",
                    "edge_crossing": "forbidden",
                    "evidence": "port belongs on the bottom face",
                    "acceptance": "exits through z-min without touching front",
                }
            ]
        }
        report = {
            "shape": {
                "bbox_mm": {
                    "min": [-20, -10, 0],
                    "max": [20, 10, 40],
                    "size": [40, 20, 40],
                },
            },
            "features": {},
            "events": [
                {
                    "id": "charging-port",
                    "kind": "cut",
                    "tool": {
                        "bbox_mm": {
                            "min": [-3, -2, -1],
                            "max": [3, 2, 2],
                            "size": [6, 4, 3],
                        }
                    },
                }
            ],
        }
        good = qa_check.semantic_placement_observation(intent, report)
        self.assertEqual(good["offenders"], [])
        self.assertEqual(good["passed_feature_ids"], ["charging-port"])

        report["events"][0]["tool"]["bbox_mm"] = {
            "min": [-3, -11, -1],
            "max": [3, -8, 2],
            "size": [6, 3, 3],
        }
        bad = qa_check.semantic_placement_observation(intent, report)
        self.assertEqual(bad["offenders"][0]["feature_id"], "charging-port")
        self.assertEqual(bad["offenders"][0]["adjacent_external_faces"], ["front"])

    def test_semantic_feature_placement_skips_non_opening_face_hints(self):
        intent = {
            "features": [
                {
                    "id": "surface-logo",
                    "kind": "logo",
                    "face": "top",
                    "edge_crossing": "forbidden",
                }
            ]
        }
        report = {
            "shape": {
                "bbox_mm": {
                    "min": [-20, -10, 0],
                    "max": [20, 10, 40],
                    "size": [40, 20, 40],
                },
            },
            "features": {
                "surface-logo": {
                    "bbox_mm": {
                        "min": [-5, -5, 39],
                        "max": [5, 5, 39.5],
                        "size": [10, 10, 0.5],
                    }
                }
            },
        }
        observed = qa_check.semantic_placement_observation(intent, report)
        self.assertEqual(observed["examined"], 0)
        self.assertEqual(observed["offenders"], [])
        self.assertEqual(observed["skipped"][0]["feature_id"], "surface-logo")

    def test_cli_fails_when_critical_feature_has_no_build_evidence(self):
        profile = bambu_profile.resolve_profile(
            self.catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            intent_path = root / "intent.json"
            report_path = root / "report.json"
            mesh_path = root / "body.stl"
            profile_path.write_text(bambu_profile.serialize(profile), encoding="utf-8")
            profile_hash = sha256(profile_path.read_bytes()).hexdigest()
            intent_path.write_text(
                json.dumps({
                    "schema": "evidence-cad-intent/v4",
                    "part": "body",
                    "task_mode": "specification",
                    "representation": "full-3d",
                    "coordinate_system": COORDINATE_SYSTEM,
                    "reference_files": [],
                    "dimensions_mm": {
                        "x": {"value": 20, "source": "user", "confidence": "high"},
                        "y": {"value": 10, "source": "user", "confidence": "high"},
                        "z": {"value": 4, "source": "user", "confidence": "high"},
                    },
                    "features": [
                        {
                            "id": "missing-detail",
                            "kind": "detail",
                            "evidence": "fixture declares a critical detail",
                            "acceptance": "must appear in build evidence",
                        }
                    ],
                    "manufacturing": {"mode": "single-part"},
                    "printability": {
                        "profile": {
                            "path": profile_path.name,
                            "sha256": profile_hash,
                        },
                        "build_axis": "+Z",
                        "bed_contact": "z-min",
                        "support_policy": "support-free",
                        "minimum_wall_target_mm": 0.87,
                        "critical_features": ["missing-detail"],
                    },
                    "visual": {"required": False, "reference_view": "top", "landmarks": []},
                    "assumptions": [],
                }),
                encoding="utf-8",
            )
            report_path.write_text(
                json.dumps({"events": [], "features": {}}),
                encoding="utf-8",
            )
            mesh = trimesh.creation.box(extents=[20, 10, 4])
            mesh.apply_translation([0, 0, 2])
            mesh.export(mesh_path)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(mesh_path),
                    "--profile",
                    str(profile_path),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(report_path),
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            coverage = next(
                item for item in payload["checks"]
                if item["name"] == "printability_critical_feature_coverage"
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(coverage["status"], "fail")
            self.assertEqual(
                coverage["observed"]["missing_feature_ids"],
                ["missing-detail"],
            )

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


class SinglePartOrientationExportTests(unittest.TestCase):
    def setUp(self):
        cad_helpers._FEATURES.clear()
        cad_helpers._EVENTS.clear()
        cad_helpers._PARAMETERS.clear()

    def test_export_part_rotates_print_stl_but_keeps_semantic_step(self):
        profile = bambu_profile.resolve_profile(
            bambu_profile.load_catalog(),
            machine_name="a1-mini",
            nozzle=0.4,
            tool_index=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "tower_printer-profile.json"
            profile_path.write_text(bambu_profile.serialize(profile), encoding="utf-8")
            profile_hash = sha256(profile_path.read_bytes()).hexdigest()
            intent_path = root / "tower_intent.json"
            intent_path.write_text(
                json.dumps({
                    "schema": "evidence-cad-intent/v4",
                    "part": "tower",
                    "task_mode": "specification",
                    "representation": "full-3d",
                    "coordinate_system": COORDINATE_SYSTEM,
                    "reference_files": [],
                    "dimensions_mm": {
                        "x": {"value": 20, "source": "user", "confidence": "high"},
                        "y": {"value": 10, "source": "user", "confidence": "high"},
                        "z": {"value": 80, "source": "user", "confidence": "high"},
                    },
                    "features": [
                        {
                            "id": "tower-body",
                            "kind": "additive",
                            "evidence": "fixture body is a tall rectangular part",
                            "acceptance": "semantic body remains 20 x 10 x 80 mm",
                        }
                    ],
                    "manufacturing": {"mode": "single-part"},
                    "printability": {
                        "profile": {"path": profile_path.name, "sha256": profile_hash},
                        "build_axis": "+Z",
                        "bed_contact": "z-min",
                        "support_policy": "support-free",
                        "minimum_wall_target_mm": 0.87,
                        "critical_features": ["tower-body"],
                    },
                    "visual": {
                        "required": True,
                        "reference_view": "front",
                        "landmarks": ["tall semantic tower"],
                    },
                    "assumptions": [],
                }),
                encoding="utf-8",
            )
            body = Box(20, 10, 80, align=(Align.MIN, Align.MIN, Align.MIN))
            cad_helpers.observe(body, "tower-body", "additive")
            with contextlib.redirect_stdout(io.StringIO()):
                report = cad_helpers.export_part(
                    body,
                    "tower",
                    str(root),
                    intent_path=str(intent_path),
                    source_path=__file__,
                )

            self.assertEqual(report["shape"]["bbox_mm"]["size"], [20.0, 10.0, 80.0])
            self.assertEqual(report["print"]["bbox_mm"]["size"], [20.0, 80.0, 10.0])
            self.assertEqual(
                report["print_orientation"]["selected"]["name"],
                "rotate-x--90",
            )
            self.assertEqual(
                report["print"]["transform"]["rotate_degrees_xyz"],
                [-90.0, 0.0, 0.0],
            )

            mesh = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(root / "tower.stl"),
                    "--profile",
                    str(profile_path),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(root / "tower_report.json"),
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(mesh.returncode, 0, mesh.stdout + mesh.stderr)
            mesh_payload = json.loads(mesh.stdout)
            dimension_z = next(
                item for item in mesh_payload["checks"]
                if item["name"] == "dimension_z"
            )
            self.assertEqual(dimension_z["expected"]["value"], 10.0)

            step = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "step_check.py"),
                    str(root / "tower-assemble.step"),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(root / "tower_report.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(step.returncode, 0, step.stdout + step.stderr)
            step_payload = json.loads(step.stdout)
            dimension_z = next(
                item for item in step_payload["checks"]
                if item["name"] == "dimension_z"
            )
            self.assertEqual(dimension_z["expected"]["value"], 80.0)

    def test_orientation_candidates_include_top_down_and_scale_evidence(self):
        profile = bambu_profile.resolve_profile(
            bambu_profile.load_catalog(),
            machine_name="a1-mini",
            nozzle=0.4,
            tool_index=0,
        )
        oversized = Box(40, 20, 220, align=(Align.MIN, Align.MIN, Align.MIN))
        candidates = cad_helpers._orientation_candidates(oversized, profile)
        by_name = {item["name"]: item for item in candidates}
        self.assertIn("rotate-x-180", by_name)
        top_down = by_name["rotate-x-180"]
        self.assertEqual(top_down["bed_contact_semantic_face"], "top")
        self.assertFalse(top_down["uniform_scale_to_fit_profile"]["fits_without_scaling"])
        self.assertAlmostEqual(
            top_down["uniform_scale_to_fit_profile"]["scale"],
            180 / 220,
            places=6,
        )


class SingleMaterialAssemblyTests(unittest.TestCase):
    def setUp(self):
        cad_helpers._FEATURES.clear()
        cad_helpers._EVENTS.clear()
        cad_helpers._PARAMETERS.clear()

    def test_export_assembly_writes_print_stls_step_masters_and_auditable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "case_intent.json"
            intent_path.write_text(
                json.dumps({
                    "schema": "evidence-cad-intent/v4",
                    "part": "case",
                    "coordinate_system": COORDINATE_SYSTEM,
                    "manufacturing": {
                        "mode": "multipart",
                        "parts": [
                            {
                                "name": "lower-shell",
                                "role": "main sleeve",
                                "acceptance": "one printable lower shell",
                            },
                            {
                                "name": "top-lid",
                                "role": "separate lid cap",
                                "acceptance": "one printable top lid",
                            },
                        ],
                        "interfaces": [
                            {
                                "id": "lid-tab-slot",
                                "between": ["lower-shell", "top-lid"],
                                "connection": "tab-slot",
                                "assembly_axis": "+Z",
                                "clearance_mm": 0.3,
                                "engagement_mm": 2.0,
                                "features": ["lid-tab", "lid-slot"],
                                "acceptance": "2 mm printable tab enters the lid slot with 0.3 mm clearance",
                            }
                        ],
                    },
                }),
                encoding="utf-8",
            )
            tab = Pos(0, 0, 4) * Box(
                6, 3, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            lower = (
                Box(20, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
                + tab
            )
            lid_blank = Pos(0, 0, 4) * Box(
                20, 10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            slot = Pos(0, 0, 3.9) * Box(
                6.3, 3.3, 2.2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            lid = cad_helpers.checked_cut(
                lid_blank,
                slot,
                "lid-slot",
                part_name="top-lid",
            )
            cad_helpers.observe(
                lower,
                "lower-shell-envelope",
                "part",
                part_name="lower-shell",
            )
            cad_helpers.observe(
                tab,
                "lid-tab",
                "interface",
                part_name="lower-shell",
            )
            cad_helpers.observe(
                lid,
                "top-lid-envelope",
                "part",
                part_name="top-lid",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                report = cad_helpers.export_assembly(
                    {"top-lid": lid, "lower-shell": lower},
                    "case",
                    str(root),
                    intent_path=str(intent_path),
                    source_path=__file__,
                )

            self.assertEqual(report["schema"], "evidence-cad-assembly-build/v3")
            self.assertEqual(report["assembly"]["shape"]["solid_count"], 2)
            self.assertEqual(report["print_plate"]["solid_count"], 2)
            self.assertEqual(
                sorted(report["overlaps_mm3"]),
                ["lower-shell&top-lid"],
            )
            self.assertEqual(
                sorted(report["parts"]),
                ["lower-shell", "top-lid"],
            )
            self.assertTrue((root / "case-lower-shell.stl").is_file())
            self.assertTrue((root / "case-top-lid.stl").is_file())
            self.assertTrue((root / "case.stl").is_file())
            self.assertTrue((root / "case-assemble.step").is_file())
            self.assertTrue((root / "case-display.glb").is_file())

            audit = assembly_check.audit_report(
                root / "case_report.json",
                print_stl=root / "case.stl",
                max_overlap_mm3=0.01,
            )
            self.assertTrue(audit["pass"], audit)
            assemble_audit = step_check.audit_step(
                root / "case-assemble.step",
                expect_solids=2,
                expect_x=20,
                expect_y=10,
                expect_z=6,
            )
            self.assertTrue(assemble_audit["pass"], assemble_audit)
            assemble_cli = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "step_check.py"),
                    str(root / "case-assemble.step"),
                    "--report",
                    str(root / "case_report.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                assemble_cli.returncode,
                0,
                assemble_cli.stdout + assemble_cli.stderr,
            )
            assemble_cli_payload = json.loads(assemble_cli.stdout)
            expected_solids = next(
                item for item in assemble_cli_payload["checks"]
                if item["name"] == "expected_solids"
            )
            self.assertEqual(expected_solids["observed"], 2)
            self.assertEqual(
                [
                    item["feature_id"]
                    for item in qa_check.feature_measurements(report, "top-lid")
                ],
                ["top-lid-envelope", "lid-slot"],
            )

            top_lid = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(root / "case-top-lid.stl"),
                    "--report",
                    str(root / "case_report.json"),
                    "--components",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(top_lid.returncode, 0, top_lid.stdout + top_lid.stderr)
            self.assertEqual(json.loads(top_lid.stdout)["report_part"], "top-lid")

            print_plate = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(root / "case.stl"),
                    "--report",
                    str(root / "case_report.json"),
                    "--components",
                    "2",
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                print_plate.returncode,
                0,
                print_plate.stdout + print_plate.stderr,
            )

            preview_report = root / "case_views.json"
            preview = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "render_preview.py"),
                    str(root / "case-display.glb"),
                    "--out",
                    str(root / "case_views.png"),
                    "--report",
                    str(preview_report),
                    "--size",
                    "320",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
            self.assertTrue((root / "case_views.png").is_file())
            preview_payload = json.loads(preview_report.read_text(encoding="utf-8"))
            self.assertEqual(len(preview_payload["meshes"]), 1)
            self.assertEqual(preview_payload["dimensions_mm"], [20.0, 10.0, 6.0])
            self.assertTrue(
                all("preview_color_rgb" in item for item in preview_payload["meshes"])
            )

            report_path = root / "case_report.json"
            incomplete = json.loads(report_path.read_text(encoding="utf-8"))
            incomplete["overlaps_mm3"] = {}
            report_path.write_text(json.dumps(incomplete), encoding="utf-8")
            incomplete_audit = assembly_check.audit_report(
                report_path,
                print_stl=root / "case.stl",
            )
            self.assertFalse(incomplete_audit["pass"])
            self.assertIn("part_overlaps", incomplete_audit["errors"])

    def test_export_assembly_requires_matching_multipart_intent_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "case_intent.json"
            intent_path.write_text(
                json.dumps({
                    "schema": "evidence-cad-intent/v4",
                    "part": "case",
                    "coordinate_system": COORDINATE_SYSTEM,
                    "manufacturing": {
                        "mode": "multipart",
                        "parts": [
                            {
                                "name": "lower-shell",
                                "role": "main sleeve",
                                "acceptance": "one printable lower shell",
                            },
                            {
                                "name": "wrong-lid",
                                "role": "separate lid cap",
                                "acceptance": "one printable top lid",
                            },
                        ],
                        "interfaces": [
                            {
                                "id": "lid-tab-slot",
                                "between": ["lower-shell", "wrong-lid"],
                                "connection": "tab-slot",
                                "assembly_axis": "+Z",
                                "clearance_mm": 0.3,
                                "engagement_mm": 2.0,
                                "features": ["lid-tab", "lid-slot"],
                                "acceptance": "2 mm printable tab enters the lid slot with 0.3 mm clearance",
                            }
                        ],
                    },
                }),
                encoding="utf-8",
            )
            lower = Box(20, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
            lid = Pos(0, 0, 6) * Box(
                20, 10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            with self.assertRaisesRegex(
                cad_helpers.BuildInvariantError,
                "part names do not match",
            ):
                cad_helpers.export_assembly(
                    {"lower-shell": lower, "top-lid": lid},
                    "case",
                    str(root),
                    intent_path=str(intent_path),
                    source_path=__file__,
                )

    def test_print_plate_separates_touching_assembly_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "touching_intent.json"
            intent_path.write_text(
                json.dumps({
                    "schema": "evidence-cad-intent/v4",
                    "part": "touching",
                    "coordinate_system": COORDINATE_SYSTEM,
                    "manufacturing": {
                        "mode": "multipart",
                        "parts": [
                            {"name": "base", "role": "base", "acceptance": "base"},
                            {"name": "lid", "role": "lid", "acceptance": "lid"},
                        ],
                        "interfaces": [
                            {
                                "id": "glue-contact",
                                "between": ["base", "lid"],
                                "connection": "glue-face",
                                "assembly_axis": "+Z",
                                "clearance_mm": 0.0,
                                "engagement_mm": 1.0,
                                "features": ["base-glue-face", "lid-glue-face"],
                                "acceptance": "flat mating faces align before glue-up",
                            }
                        ],
                    },
                }),
                encoding="utf-8",
            )
            base = Box(10, 10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
            lid = Pos(0, 0, 2) * Box(
                10, 10, 1, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            cad_helpers.observe(base, "base", "part", part_name="base")
            cad_helpers.observe(
                base,
                "base-glue-face",
                "interface",
                part_name="base",
            )
            cad_helpers.observe(lid, "lid", "part", part_name="lid")
            cad_helpers.observe(
                lid,
                "lid-glue-face",
                "interface",
                part_name="lid",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                cad_helpers.export_assembly(
                    {"base": base, "lid": lid},
                    "touching",
                    str(root),
                    intent_path=str(intent_path),
                    source_path=__file__,
                )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "qa_check.py"),
                    str(root / "touching.stl"),
                    "--report",
                    str(root / "touching_report.json"),
                    "--components",
                    "2",
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertNotIn("connected_components", payload["errors"])


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

    def test_multipart_contract_validates_parts_and_interfaces(self):
        example_path = SKILL / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))
        data["manufacturing"] = {
            "mode": "multipart",
            "decision": "top lid is a functional cover that needs a printable connector",
            "parts": [
                {
                    "name": "lower-shell",
                    "role": "main sleeve",
                    "acceptance": "one printable lower shell",
                },
                {
                    "name": "top-lid",
                    "role": "separate lid cap",
                    "acceptance": "one printable top lid",
                },
            ],
            "interfaces": [
                {
                    "id": "lid-tab-slot",
                    "between": ["lower-shell", "top-lid"],
                    "connection": "tab-slot",
                    "assembly_axis": "+Z",
                    "clearance_mm": 0.3,
                    "engagement_mm": 2.0,
                    "features": ["lid-tab", "lid-slot"],
                    "acceptance": "2 mm tab enters the lid slot with 0.3 mm clearance",
                }
            ],
        }
        data["features"].extend([
            {
                "id": "lid-tab",
                "kind": "interface",
                "evidence": "lower shell carries the printable tab",
                "acceptance": "tab is wide enough for the selected nozzle",
            },
            {
                "id": "lid-slot",
                "kind": "interface",
                "evidence": "top lid carries the matching slot",
                "acceptance": "slot includes the declared clearance",
            },
        ])
        self.assertEqual(intent_contract.validate(data, example_path.parent), [])

        data["manufacturing"]["interfaces"][0]["between"] = [
            "lower-shell",
            "missing-lid",
        ]
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(any("unknown parts" in error for error in errors), errors)

        data["manufacturing"]["interfaces"][0]["between"] = [
            "lower-shell",
            "lower-shell",
        ]
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(any("distinct parts" in error for error in errors), errors)

        data["manufacturing"]["interfaces"][0].pop("features")
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(any("modeled connector feature IDs" in error for error in errors), errors)

    def test_flat_semantic_feature_fields_are_validated(self):
        example_path = SKILL / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))
        self.assertEqual(intent_contract.validate(data, example_path.parent), [])

        data["features"][1]["direction"] = "+Y"
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(any("direction must be one of" in item for item in errors))

        data["features"][1]["direction"] = "through-Z"
        data["features"][1].pop("edge_crossing")
        errors = intent_contract.validate(data, example_path.parent)
        self.assertIn("features[1].edge_crossing is required for kind hole", errors)

    def test_contract_requires_manufacturing_decision(self):
        example_path = SKILL / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))
        data.pop("manufacturing")
        errors = intent_contract.validate(data, example_path.parent)
        self.assertIn("manufacturing must be an object", errors)

    def test_contract_rejects_old_schema_and_mode_specific_fields(self):
        example_path = SKILL / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))
        data["schema"] = "evidence-cad-intent/v3"
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(any("evidence-cad-intent/v4" in error for error in errors))

        data["schema"] = "evidence-cad-intent/v4"
        data["manufacturing"] = {
            "mode": "single-part",
            "parts": [{"name": "ignored"}],
            "interfaces": [],
        }
        errors = intent_contract.validate(data, example_path.parent)
        self.assertIn("manufacturing.parts is only valid for multipart", errors)
        self.assertIn("manufacturing.interfaces is only valid for multipart", errors)


if __name__ == "__main__":
    unittest.main()
