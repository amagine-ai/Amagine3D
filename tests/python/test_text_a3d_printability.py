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
COLOR = SKILL / "color"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bambu_profile = load_module("bambu_profile", SKILL / "bambu_profile.py")
build_check = load_module("build_check", SKILL / "build_check.py")
qa_check = load_module("qa_check", SKILL / "qa_check.py")
cad_helpers = load_module("single_cad_helpers", SKILL / "cad_helpers.py")
assembly_check = load_module("single_assembly_check", SKILL / "assembly_check.py")
intent_contract = load_module("single_intent_contract", SKILL / "intent_contract.py")
step_check = load_module("single_step_check", SKILL / "step_check.py")
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


def _profile_reference() -> dict:
    path = SKILL / "examples" / "bambu-a1-mini-0.4-standard.example.json"
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}


def _write_brep_scene(
    root: Path,
    intent_path: Path,
    part_names: list[str],
    *,
    revision: str = "test-rev-001",
) -> Path:
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    single_owner = (
        intent.get("part")
        if intent.get("manufacturing", {}).get("mode") == "single-part"
        else None
    )
    scene_features = [
        (feature["id"], feature.get("part", single_owner))
        for feature in intent.get("features", [])
        if isinstance(feature, dict)
        and isinstance(feature.get("id"), str)
        and feature.get("part", single_owner) in part_names
    ]
    scene_path = root / f"{intent_path.stem}_scene.json"
    scene = {
        "schema": "evidence-semantic-scene/v1",
        "revision": revision,
        "intentRef": {
            "path": str(intent_path),
            "schema": "evidence-cad-intent/v4",
            "sha256": sha256(intent_path.read_bytes()).hexdigest(),
        },
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "materials": [],
        "parts": [
            {"id": part_name, "representationMaster": "brep"}
            for part_name in part_names
        ],
        "nodes": [
            {
                "id": feature_id.replace("/", "--"),
                "partId": owner,
                "featureId": feature_id,
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "roundedBox",
                    "parameters": {"sizeMm": [1, 1, 1], "radiusMm": 0.0},
                },
            }
            for feature_id, owner in scene_features
        ],
        "interfaces": [],
    }
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    return scene_path


def _qa_report_for_mesh(
    mesh_path: Path,
    profile_path: Path,
    *,
    part: str,
    features: dict | None = None,
    events: list | None = None,
    intent_path: Path | None = None,
) -> dict:
    mesh = trimesh.load(mesh_path, force="mesh", process=False)
    bounds = np.asarray(mesh.bounds, dtype=float)
    geometry = {
        "bodyCount": max(len(mesh.split(only_watertight=False)), 1),
        "boundsMm": {
            "min": bounds[0].tolist(),
            "max": bounds[1].tolist(),
            "size": (bounds[1] - bounds[0]).tolist(),
        },
        "isVolume": bool(mesh.is_volume),
        "valid": True,
        "volumeMm3": float(mesh.volume),
    }
    inputs = {
        "profile": {
            "path": str(profile_path),
            "schema": "evidence-bambu-printer-profile/v1",
            "sha256": sha256(profile_path.read_bytes()).hexdigest(),
        }
    }
    if intent_path is not None:
        inputs["intent"] = {
            "path": str(intent_path),
            "schema": "evidence-cad-intent/v4",
            "sha256": sha256(intent_path.read_bytes()).hexdigest(),
        }
    identity = np.eye(4).tolist()
    return {
        "schema": "evidence-a3d-build/v1",
        "backend": "brep-part",
        "part": part,
        "parts": {
            part: {
                "representationMaster": "brep",
                "semantic": geometry,
                "print": geometry,
            }
        },
        "inputs": inputs,
        "artifacts": {
            f"stl:{part}": {
                "coordinateFrame": "part-print",
                "path": str(mesh_path),
                "sha256": sha256(mesh_path.read_bytes()).hexdigest(),
            }
        },
        "coordinateFrames": {
            "part-print": {"partTransforms": {part: identity}},
            "plate-print": {"partTransforms": {part: identity}},
        },
        "features": features or {},
        "events": events or [],
    }


def _glb_face_colors(path: Path) -> set[tuple[int, int, int]]:
    scene = trimesh.load(path, force="scene", process=False)
    return {
        tuple(int(value) for value in mesh.visual.face_colors[0][:3])
        for mesh in scene.geometry.values()
        if getattr(mesh.visual, "face_colors", None) is not None
        and len(mesh.visual.face_colors)
    }


class BambuProfileTests(unittest.TestCase):
    def test_resolves_single_and_dual_tool_limits(self):
        catalog = bambu_profile.load_catalog()
        mini = bambu_profile.resolve_profile(
            catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        self.assertEqual(mini["machine"]["selected_tool"]["height_mm"], 180)
        self.assertEqual(mini["derived"]["process_wall_target_mm"], 0.87)
        self.assertEqual(
            mini["derived"]["rotation_safe_envelope"],
            {
                "constraint": "sqrt(x_mm^2 + y_mm^2 + z_mm^2) <= max_spatial_diagonal_mm",
                "max_spatial_diagonal_mm": 180.0,
                "purpose": "choose inferred dimensions before modeling so arbitrary rigid rotations need no scaling",
                "requires_excluded_zone_placement_check": False,
                "usable_extent_mm": [180.0, 180.0, 180.0],
            },
        )

        h2d = bambu_profile.resolve_profile(
            catalog, machine_name="Bambu Lab H2D", nozzle=0.4, tool_index=1
        )
        polygon = np.asarray(h2d["machine"]["selected_tool"]["polygon_mm"])
        self.assertEqual(float(np.ptp(polygon[:, 0])), 325.0)
        self.assertEqual(h2d["machine"]["selected_tool"]["height_mm"], 325)
        self.assertEqual(
            h2d["derived"]["rotation_safe_envelope"]["max_spatial_diagonal_mm"],
            320.0,
        )

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
        features = {
                "local-wall": {
                    "part": "local-thin",
                    "role": "wall",
                    "bbox_mm": {
                        "min": [-10, -0.3, 1.0],
                        "max": [10, 0.3, 1.6],
                        "size": [20, 0.6, 0.6],
                    },
                },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            report_path = root / "report.json"
            mesh_path = root / "local-thin.stl"
            profile_path.write_text(bambu_profile.serialize(profile), encoding="utf-8")
            mesh.export(mesh_path)
            report = _qa_report_for_mesh(
                mesh_path,
                profile_path,
                part="local-thin",
                features=features,
            )
            report_path.write_text(json.dumps(report), encoding="utf-8")
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

    def test_risk_attribution_transforms_all_bbox_corners_into_print_frame(self):
        matrix = cad_helpers.rigid_transform(
            "semantic",
            "plate-print",
            rotate_degrees_xyz=[0, 0, 90],
            translate_mm=[10, 0, 0],
        )["matrix"]
        report = {
            "part": "body",
            "artifacts": {"stl": {"coordinateFrame": "plate-print"}},
            "features": {
                "rotated-feature": {
                    "part": "body",
                    "bbox_mm": {
                        "min": [0, 0, 0],
                        "max": [1, 2, 1],
                        "size": [1, 2, 1],
                    },
                }
            },
            "events": [],
            "coordinateFrames": {
                "part-print": {
                    "partTransforms": {"body": np.eye(4, dtype=float).tolist()}
                },
                "plate-print": {"partTransforms": {"body": matrix}},
            },
        }
        observed = qa_check._affected_features(
            np.asarray([[8.2, 0.2, 0.2], [9.8, 0.8, 0.8]]),
            report,
            artifact_key="stl",
        )
        self.assertEqual(observed, {
            "feature_ids": ["rotated-feature"],
            "status": "evaluated",
        })

        report["coordinateFrames"]["plate-print"]["partTransforms"]["body"][0][0] = 2
        rejected = qa_check._affected_features(
            np.asarray([[8.2, 0.2, 0.2], [9.8, 0.8, 0.8]]),
            report,
            artifact_key="stl",
        )
        self.assertEqual(rejected["status"], "not_evaluated")
        self.assertIn("scale or shear", rejected["reason"])

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
            "part": "fixture",
            "backendData": {
                "semanticAssembly": {
                    "boundsMm": {
                        "min": [-20, -10, 0],
                        "max": [20, 10, 40],
                        "size": [40, 20, 40],
                    }
                }
            },
            "parts": {
                "fixture": {
                    "semantic": {
                        "boundsMm": {
                            "min": [-20, -10, 0],
                            "max": [20, 10, 40],
                            "size": [40, 20, 40],
                        },
                    }
                }
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
            "part": "fixture",
            "backendData": {
                "semanticAssembly": {
                    "boundsMm": {
                        "min": [-20, -10, 0],
                        "max": [20, 10, 40],
                        "size": [40, 20, 40],
                    }
                }
            },
            "parts": {
                "fixture": {
                    "semantic": {
                        "boundsMm": {
                            "min": [-20, -10, 0],
                            "max": [20, 10, 40],
                            "size": [40, 20, 40],
                        },
                    }
                }
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
                    "visual": {
                        "required": True,
                        "reference_view": "top",
                        "landmarks": ["missing detail must be visually confirmed"],
                    },
                    "assumptions": [],
                }),
                encoding="utf-8",
            )
            mesh = trimesh.creation.box(extents=[20, 10, 4])
            mesh.apply_translation([0, 0, 2])
            mesh.export(mesh_path)
            report_path.write_text(
                json.dumps(
                    _qa_report_for_mesh(
                        mesh_path,
                        profile_path,
                        part="body",
                        intent_path=intent_path,
                    )
                ),
                encoding="utf-8",
            )

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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            thin_path = root / "thin.stl"
            large_path = root / "large.stl"
            profile_path.write_text(bambu_profile.serialize(profile), encoding="utf-8")

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

    def test_export_part_rejects_final_envelope_that_misses_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path, _ = write_fixture_intent(
                root,
                part="wrong-envelope",
                feature_owners={"body": "wrong-envelope"},
                dimensions_mm=(12.0, 10.0, 10.0),
            )
            scene_path = _write_brep_scene(root, intent_path, ["wrong-envelope"])
            body = Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
            cad_helpers.observe(body, "body", "envelope")

            with self.assertRaisesRegex(
                cad_helpers.BuildInvariantError,
                "semantic envelope dimension x differs from intent",
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    cad_helpers.export_part(
                        body,
                        "wrong-envelope",
                        str(root),
                        intent_path=str(intent_path),
                        scene_path=str(scene_path),
                        source_path=__file__,
                    )
            self.assertFalse((root / "wrong-envelope_report.json").exists())

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
            scene_path = _write_brep_scene(root, intent_path, ["tower"])
            body = Box(20, 10, 80, align=(Align.MIN, Align.MIN, Align.MIN))
            cad_helpers.observe(body, "tower-body", "additive")
            with contextlib.redirect_stdout(io.StringIO()):
                report = cad_helpers.export_part(
                    body,
                    "tower",
                    str(root),
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    source_path=__file__,
                )
            manifest_audit = build_check.audit(root / "tower_report.json")
            self.assertTrue(manifest_audit["pass"], manifest_audit)
            self.assertTrue(report["backendData"]["exportAudit"]["pass"])
            self.assertEqual(
                sorted(report["backendData"]["exportAudit"]["artifacts"]),
                ["glb:display", "step:tower", "stl:tower"],
            )
            self.assertTrue((root / "tower_export-audit.json").is_file())

            self.assertEqual(
                report["parts"]["tower"]["semantic"]["boundsMm"]["size"],
                [20.0, 10.0, 80.0],
            )
            self.assertEqual(
                report["parts"]["tower"]["print"]["boundsMm"]["size"],
                [20.0, 80.0, 10.0],
            )
            self.assertEqual(
                report["backendData"]["printOrientation"]["selected"]["name"],
                "rotate-x--90",
            )
            self.assertEqual(
                len(report["coordinateFrames"]["part-print"]["partTransforms"]["tower"]),
                4,
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
                    str(root / "tower.step"),
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
    def test_part_colored_assembly_requires_explicit_separate_parts_mode(self):
        intent = {
            "part": "case",
            "manufacturing": {
                "mode": "multipart",
                "parts": [
                    {"name": "base"},
                    {"name": "lid"},
                ],
            },
            "color_regions": [
                {
                    "name": "base",
                    "part": "base",
                    "hex": "#E8E0D4",
                    "purpose": "housing",
                    "boundary": "complete base",
                    "evidence": "fixture",
                },
                {
                    "name": "lid",
                    "part": "lid",
                    "hex": "#20242A",
                    "purpose": "cover",
                    "boundary": "complete lid",
                    "evidence": "fixture",
                },
            ],
            "printability": {},
        }
        with self.assertRaisesRegex(
            cad_helpers.BuildInvariantError,
            "require separate_parts",
        ):
            cad_helpers._part_color_plan(
                {"base": "#E8E0D4", "lid": "#20242A"},
                intent,
                {"base", "lid"},
            )

    def setUp(self):
        cad_helpers._FEATURES.clear()
        cad_helpers._EVENTS.clear()
        cad_helpers._PARAMETERS.clear()

    def test_export_assembly_writes_print_stls_step_masters_and_auditable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manufacturing = {
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
            }
            intent_path, _ = write_fixture_intent(
                root,
                part="case",
                feature_owners={
                    "lower-shell-envelope": "lower-shell",
                    "lid-tab": "lower-shell",
                    "top-lid-envelope": "top-lid",
                    "lid-slot": "top-lid",
                },
                manufacturing=manufacturing,
                dimensions_mm=(20.0, 10.0, 6.0),
            )
            scene_path = _write_brep_scene(
                root, intent_path, ["lower-shell", "top-lid"]
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
                    scene_path=str(scene_path),
                    source_path=__file__,
                )
            manifest_audit = build_check.audit(root / "case_report.json")
            self.assertTrue(manifest_audit["pass"], manifest_audit)
            self.assertTrue(report["backendData"]["exportAudit"]["pass"])
            self.assertIn("exportAudit", report["artifacts"])
            self.assertEqual(
                len(report["backendData"]["exportAudit"]["artifacts"]), 7
            )

            self.assertEqual(report["schema"], "evidence-a3d-build/v1")
            self.assertEqual(report["backend"], "brep-assembly")
            self.assertEqual(report["backendData"]["assembly"]["shape"]["solid_count"], 2)
            self.assertEqual(report["backendData"]["printPlate"]["bodyCount"], 2)
            self.assertEqual(
                sorted(report["backendData"]["overlapsMm3"]),
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
            self.assertFalse((root / "case.3mf").exists())
            self.assertFalse((root / "case_material-plan.json").exists())
            self.assertNotIn("part_colors", report)

            audit = assembly_check.audit_report(
                root / "case_report.json",
                print_stl=root / "case.stl",
                max_overlap_mm3=0.01,
            )
            self.assertTrue(audit["pass"], audit)
            self.assertEqual(audit["schema"], "evidence-assembly-audit/v1")
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
            self.assertEqual(len(preview_payload["meshes"]), 2)
            self.assertEqual(preview_payload["dimensions_mm"], [20.0, 10.0, 6.0])
            self.assertTrue(
                all("preview_color_rgb" in item for item in preview_payload["meshes"])
            )

            report_path = root / "case_report.json"
            incomplete = json.loads(report_path.read_text(encoding="utf-8"))
            incomplete["backendData"]["overlapsMm3"] = {}
            report_path.write_text(json.dumps(incomplete), encoding="utf-8")
            incomplete_audit = assembly_check.audit_report(
                report_path,
                print_stl=root / "case.stl",
            )
            self.assertFalse(incomplete_audit["pass"])
            self.assertIn("part_overlaps", incomplete_audit["errors"])

    def test_step_report_dimensions_separate_assembly_and_part_semantic_bounds(self):
        report = {
            "backendData": {
                "assembly": {
                    "shape": {
                        "bbox_mm": {"size": [999.0, 999.0, 999.0]},
                    }
                },
                "semanticAssembly": {
                    "boundsMm": {"size": [40.0, 30.0, 20.0]},
                },
            },
            "parts": {
                "lower-shell": {
                    "semantic": {"boundsMm": {"size": [40.0, 30.0, 12.0]}},
                }
            },
        }
        self.assertEqual(
            step_check._report_dimensions(report, "step:assembly"),
            (40.0, 30.0, 20.0),
        )
        self.assertEqual(
            step_check._report_dimensions(report, "step:lower-shell"),
            (40.0, 30.0, 12.0),
        )

    def test_export_assembly_part_colors_share_intent_geometry_and_print_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = (
                SKILL
                / "examples"
                / "bambu-a1-mini-0.4-standard.example.json"
            )
            intent_path = root / "case_intent.json"
            intent = {
                "schema": "evidence-cad-intent/v4",
                "part": "case",
                "task_mode": "specification",
                "representation": "full-3d",
                "coordinate_system": COORDINATE_SYSTEM,
                "dimensions_mm": {
                    "x": {"value": 20, "source": "user", "confidence": "high"},
                    "y": {"value": 10, "source": "user", "confidence": "high"},
                    "z": {"value": 6, "source": "user", "confidence": "high"},
                },
                "features": [
                    {
                        "id": "base-envelope",
                        "part": "base",
                        "kind": "envelope",
                        "evidence": "A lower housing is required.",
                        "acceptance": "One valid base solid is exported.",
                    },
                    {
                        "id": "lid-envelope",
                        "part": "lid",
                        "kind": "envelope",
                        "evidence": "A separate lid is required.",
                        "acceptance": "One valid lid solid is exported.",
                    },
                    {
                        "id": "base-glue-face",
                        "part": "base",
                        "kind": "interface",
                        "evidence": "The base has a mating face.",
                        "acceptance": "The base face aligns with the lid.",
                    },
                    {
                        "id": "lid-glue-face",
                        "part": "lid",
                        "kind": "interface",
                        "evidence": "The lid has a mating face.",
                        "acceptance": "The lid face aligns with the base.",
                    },
                ],
                "manufacturing": {
                    "mode": "multipart",
                    "parts": [
                        {"name": "base", "role": "housing", "acceptance": "one base"},
                        {"name": "lid", "role": "cover", "acceptance": "one lid"},
                    ],
                    "interfaces": [
                        {
                            "id": "case-glue-face",
                            "between": ["base", "lid"],
                            "connection": "glue-face",
                            "assembly_axis": "+Z",
                            "clearance_mm": 0.0,
                            "engagement_mm": 1.0,
                            "features": ["base-glue-face", "lid-glue-face"],
                            "acceptance": "The two flat mating faces align.",
                        }
                    ],
                },
                "color_regions": [
                    {
                        "name": "base",
                        "part": "base",
                        "hex": "#E8E0D4",
                        "purpose": "main housing color",
                        "boundary": "complete base physical part",
                        "evidence": "The housing is warm ivory.",
                        "acceptance": "The base is encoded as ivory.",
                        "material": {
                            "filament": "Ivory PLA",
                            "transmission": "opaque",
                        },
                    },
                    {
                        "name": "lid",
                        "part": "lid",
                        "hex": "#20242A",
                        "purpose": "contrasting cover color",
                        "boundary": "complete lid physical part",
                        "evidence": "The cover is dark graphite.",
                        "acceptance": "The lid is encoded as graphite.",
                        "material": {"transmission": "opaque"},
                    },
                ],
                "palette_reduction": {
                    "applied": False,
                    "reason": "Two requested colors map directly to the two physical parts.",
                },
                "printability": {
                    "profile": {
                        "path": str(profile_path),
                        "sha256": sha256(profile_path.read_bytes()).hexdigest(),
                    },
                    "build_axis": "+Z",
                    "bed_contact": "z-min",
                    "support_policy": "support-free",
                    "minimum_wall_target_mm": 0.9,
                    "critical_features": ["base-glue-face", "lid-glue-face"],
                    "print_package_mode": "separate_parts",
                },
                "visual": {
                    "required": True,
                    "reference_view": "front",
                    "landmarks": ["base and lid remain visually distinct"],
                },
                "assumptions": [],
                "reference_files": [],
            }
            self.assertEqual(intent_contract.validate(intent, root), [])
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            scene_path = _write_brep_scene(root, intent_path, ["base", "lid"])

            base = Box(20, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
            lid = Pos(0, 0, 4) * Box(
                16, 8, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            cad_helpers.observe(base, "base-envelope", "part", part_name="base")
            cad_helpers.observe(
                base, "base-glue-face", "interface", part_name="base"
            )
            cad_helpers.observe(lid, "lid-envelope", "part", part_name="lid")
            cad_helpers.observe(
                lid, "lid-glue-face", "interface", part_name="lid"
            )
            colors = {"base": "#e8e0d4", "lid": "#20242a"}
            with contextlib.redirect_stdout(io.StringIO()):
                report = cad_helpers.export_assembly(
                    {"lid": lid, "base": base},
                    "case",
                    str(root),
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    source_path=__file__,
                    part_colors=colors,
                )

            self.assertEqual(report["schema"], "evidence-a3d-build/v1")
            self.assertEqual(report["backend"], "brep-assembly")
            self.assertFalse(report["autoScale"])
            self.assertEqual(report["scale"], 1.0)
            self.assertEqual(
                report["backendData"]["partColors"],
                {"base": "#E8E0D4", "lid": "#20242A"},
            )
            self.assertEqual(report["backendData"]["printPackageMode"], "separate_parts")
            self.assertEqual(report["coordinateFrames"]["semantic"]["scale"], 1.0)
            self.assertEqual(report["coordinateFrames"]["part-print"]["scale"], 1.0)
            self.assertEqual(report["coordinateFrames"]["plate-print"]["scale"], 1.0)
            self.assertTrue(all(
                len(transform) == 4
                for transform in report["coordinateFrames"]["plate-print"]["partTransforms"].values()
            ))
            self.assertTrue((root / "case.3mf").is_file())
            self.assertTrue((root / "case_material-plan.json").is_file())
            self.assertEqual(
                report["backendData"]["threeMf"]["inspection"]["build_item_count"],
                2,
            )
            self.assertEqual(
                report["backendData"]["threeMf"]["inspection"]["package_mode"],
                "separate_parts",
            )
            self.assertEqual(
                {
                    item["name"]: item["color"]
                    for item in report["backendData"]["threeMf"]["inspection"]["regions"]
                },
                {"base": "#E8E0D4", "lid": "#20242A"},
            )
            self.assertEqual(
                _glb_face_colors(root / "case-display.glb"),
                {(232, 224, 212), (32, 36, 42)},
            )

            from color.export_3mf import load_color_archive_mesh

            package_mesh, _ = load_color_archive_mesh(str(root / "case.3mf"))
            plate_mesh = trimesh.load(root / "case.stl", force="mesh", process=False)
            np.testing.assert_allclose(package_mesh.bounds, plate_mesh.bounds, atol=1e-5)
            package_mesh.merge_vertices()
            plate_mesh.merge_vertices()
            self.assertEqual(len(package_mesh.split(only_watertight=False)), 2)
            self.assertEqual(len(plate_mesh.split(only_watertight=False)), 2)
            self.assertAlmostEqual(
                abs(float(package_mesh.volume)),
                abs(float(plate_mesh.volume)),
                places=3,
            )

            root_audit = assembly_check.audit_report(
                root / "case_report.json",
                print_stl=root / "case.stl",
            )
            self.assertTrue(root_audit["pass"], root_audit)
            color_assembly = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "assembly_check.py"),
                    str(root / "case_report.json"),
                    str(root / "case.3mf"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                color_assembly.returncode,
                0,
                color_assembly.stdout + color_assembly.stderr,
            )

            package_qa = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(root / "case.3mf"),
                    "--profile",
                    str(profile_path),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(root / "case_report.json"),
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                package_qa.returncode,
                0,
                package_qa.stdout + package_qa.stderr,
            )
            package_payload = json.loads(package_qa.stdout)
            self.assertEqual(package_payload["report_artifact"], "3mf")
            self.assertEqual(package_payload["report_coordinate_frame"], "plate-print")
            package_dimension_x = next(
                item
                for item in package_payload["checks"]
                if item["name"] == "dimension_x"
            )
            self.assertEqual(
                package_dimension_x["expected"]["value"],
                report["backendData"]["printPlate"]["boundsMm"]["size"][0],
            )
            self.assertNotEqual(package_dimension_x["expected"]["value"], 20.0)

            lid_qa = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(root / "case-lid.stl"),
                    "--profile",
                    str(profile_path),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(root / "case_report.json"),
                    "--components",
                    "1",
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(lid_qa.returncode, 0, lid_qa.stdout + lid_qa.stderr)
            lid_payload = json.loads(lid_qa.stdout)
            self.assertEqual(lid_payload["report_artifact"], "stl:lid")
            self.assertEqual(lid_payload["report_coordinate_frame"], "part-print")
            lid_dimension_x = next(
                item
                for item in lid_payload["checks"]
                if item["name"] == "dimension_x"
            )
            self.assertEqual(lid_dimension_x["expected"]["value"], 16.0)

            with self.assertRaisesRegex(
                cad_helpers.BuildInvariantError,
                "keys must exactly match",
            ):
                cad_helpers.export_assembly(
                    {"lid": lid, "base": base},
                    "case",
                    str(root),
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    source_path=__file__,
                    part_colors={"base": "#E8E0D4"},
                )
            with self.assertRaisesRegex(
                cad_helpers.BuildInvariantError,
                "must be #RRGGBB",
            ):
                cad_helpers.export_assembly(
                    {"lid": lid, "base": base},
                    "case",
                    str(root),
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    source_path=__file__,
                    part_colors={"base": "#E8E0D4", "lid": "#123"},
                )

    def test_export_assembly_requires_matching_multipart_intent_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manufacturing = {
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
            }
            intent_path, _ = write_fixture_intent(
                root,
                part="case",
                feature_owners={
                    "lower-shell-envelope": "lower-shell",
                    "lid-tab": "lower-shell",
                    "wrong-lid-envelope": "wrong-lid",
                    "lid-slot": "wrong-lid",
                },
                manufacturing=manufacturing,
            )
            scene_path = _write_brep_scene(
                root, intent_path, ["lower-shell", "wrong-lid"]
            )
            lower = Box(20, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
            lid = Pos(0, 0, 6) * Box(
                20, 10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            with self.assertRaisesRegex(
                cad_helpers.BuildInvariantError,
                "intent parts do not match exported physical parts",
            ):
                cad_helpers.export_assembly(
                    {"lower-shell": lower, "top-lid": lid},
                    "case",
                    str(root),
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    source_path=__file__,
                )

    def test_print_plate_separates_touching_assembly_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manufacturing = {
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
            }
            intent_path, _ = write_fixture_intent(
                root,
                part="touching",
                feature_owners={
                    "base": "base",
                    "base-glue-face": "base",
                    "lid": "lid",
                    "lid-glue-face": "lid",
                },
                manufacturing=manufacturing,
                dimensions_mm=(10.0, 10.0, 3.0),
                filename="touching_intent.json",
            )
            scene_path = _write_brep_scene(root, intent_path, ["base", "lid"])
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
                    scene_path=str(scene_path),
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
    def test_unified_color_regions_allow_multiple_regions_per_multipart_owner(self):
        example_path = SKILL / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text(encoding="utf-8"))
        data["manufacturing"] = {
            "mode": "multipart",
            "parts": [
                {"name": "body", "role": "body", "acceptance": "one body"},
                {"name": "lid", "role": "lid", "acceptance": "one lid"},
            ],
            "interfaces": [
                {
                    "id": "body-lid",
                    "between": ["body", "lid"],
                    "connection": "glue-face",
                    "assembly_axis": "+Z",
                    "clearance_mm": 0.0,
                    "engagement_mm": 1.0,
                    "features": ["primary-envelope", "mounting-hole"],
                    "acceptance": "faces align",
                }
            ],
        }
        data["features"][0]["part"] = "body"
        data["features"][1]["part"] = "lid"
        data["color_regions"] = [
            {
                "name": "body-shell",
                "part": "body",
                "hex": "#EFE8DC",
                "purpose": "main body",
                "boundary": "body volume excluding its inset mark",
                "evidence": "body is warm ivory",
            },
            {
                "name": "body-mark",
                "part": "body",
                "hex": "#B64536",
                "purpose": "body inset mark",
                "boundary": "shallow inset volume on the body face",
                "evidence": "body mark is red",
            },
            {
                "name": "lid-shell",
                "part": "lid",
                "hex": "#20242A",
                "purpose": "contrasting lid",
                "boundary": "complete lid part",
                "evidence": "lid is dark graphite",
                "material": {"transmission": "opaque"},
            },
        ]
        data["palette_reduction"] = {
            "applied": False,
            "reason": "Three requested regions remain independently represented.",
        }
        data["printability"]["print_package_mode"] = "separate_parts"
        self.assertEqual(intent_contract.validate(data, example_path.parent), [])

        data["color_regions"][1]["name"] = "body-shell"
        errors = intent_contract.validate(data, example_path.parent)
        self.assertIn("color region names must be unique", errors)
        data["color_regions"][1]["name"] = "body-mark"
        data["color_regions"][1]["part"] = "screen"
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(
            any("must reference the owning physical part" in item for item in errors),
            errors,
        )
        data["color_regions"][1]["part"] = "body"
        data["printability"]["print_package_mode"] = "co_print_body"
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(any("separate_parts" in item for item in errors), errors)

    def test_hash_bound_example_profiles_use_stable_lf_bytes(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.json text eol=lf", attributes.splitlines())

        for skill_dir in (ROOT / "skills" / "text-a3d",):
            examples = skill_dir / "examples"
            intent = json.loads(
                (examples / "intent.example.json").read_text(encoding="utf-8")
            )
            reference = intent["printability"]["profile"]
            payload = (examples / reference["path"]).read_bytes()
            self.assertNotIn(b"\r\n", payload, str(skill_dir))
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
        data.pop("color_regions")
        data.pop("palette_reduction")
        data["printability"].pop("print_package_mode")
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
                "part": "lower-shell",
                "kind": "interface",
                "evidence": "lower shell carries the printable tab",
                "acceptance": "tab is wide enough for the selected nozzle",
            },
            {
                "id": "lid-slot",
                "part": "top-lid",
                "kind": "interface",
                "evidence": "top lid carries the matching slot",
                "acceptance": "slot includes the declared clearance",
            },
        ])
        data["features"][0]["part"] = "lower-shell"
        data["features"][1]["part"] = "lower-shell"
        self.assertEqual(intent_contract.validate(data, example_path.parent), [])

        data["features"][0].pop("part")
        errors = intent_contract.validate(data, example_path.parent)
        self.assertIn("features[0].part is required for multipart", errors)
        data["features"][0]["part"] = "lower-shell"

        data["features"][-1]["part"] = "button"
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(
            any(
                "features[" in error
                and ".part must reference manufacturing.parts" in error
                for error in errors
            ),
            errors,
        )
        data["features"][-1]["part"] = "top-lid"

        data["manufacturing"]["parts"].append({
            "name": "button",
            "role": "separate tactile control",
            "acceptance": "one printable retained button",
        })
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(any("button" in error and "declared interface" in error for error in errors), errors)
        data["manufacturing"]["parts"][-1]["installation"] = "loose"
        self.assertEqual(intent_contract.validate(data, example_path.parent), [])
        data["features"][-1]["part"] = "button"
        errors = intent_contract.validate(data, example_path.parent)
        self.assertTrue(
            any("must be owned by a part named in between" in error for error in errors),
            errors,
        )
        data["features"][-1]["part"] = "top-lid"
        data["manufacturing"]["parts"].pop()

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
