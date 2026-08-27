from __future__ import annotations

import contextlib
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from build123d import Align, Box, Pos
from PIL import Image
import trimesh


ROOT = Path(__file__).resolve().parents[2]
COLOR = ROOT / "skills" / "text-a3d-color"
SINGLE = ROOT / "skills" / "text-a3d"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


color_profile = load_module("color_bambu_profile", COLOR / "bambu_profile.py")
color_intent = load_module("color_intent_contract", COLOR / "intent_contract.py")
color_qa = load_module("color_qa_check", COLOR / "qa_check.py")
color_step_check = load_module("color_step_check", COLOR / "step_check.py")
single_intent = load_module("single_intent_contract", SINGLE / "intent_contract.py")

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


def _glb_vertex_colors(path: Path) -> set[tuple[int, int, int]]:
    if path.read_bytes()[:4] != b"glTF":
        raise AssertionError(f"{path} is not a binary glTF file")
    scene = trimesh.load(path, force="scene", process=False)
    colors: set[tuple[int, int, int]] = set()
    for mesh in scene.geometry.values():
        face_colors = getattr(mesh.visual, "face_colors", None)
        if face_colors is not None and len(face_colors):
            colors.add(tuple(int(value) for value in face_colors[0][:3]))
    return colors


class IndependentColorProfileTests(unittest.TestCase):
    def test_color_skill_owns_its_profile_catalog(self):
        self.assertEqual(color_profile.CATALOG_PATH.parent.parent, COLOR)
        catalog = color_profile.load_catalog()
        mini = color_profile.resolve_profile(
            catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        h2d = color_profile.resolve_profile(
            catalog, machine_name="h2d", nozzle=0.4, tool_index=1
        )
        self.assertEqual(mini["derived"]["process_wall_target_mm"], 0.87)
        self.assertEqual(h2d["machine"]["selected_tool"]["height_mm"], 325)

class PixelAnalyzerTests(unittest.TestCase):
    def test_native_one_pixel_cells_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.png"
            image = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
            image.putpixel((0, 0), (0, 255, 255, 255))
            image.putpixel((1, 0), (0, 255, 255, 255))
            image.putpixel((0, 1), (120, 70, 20, 255))
            image.putpixel((1, 1), (120, 70, 20, 255))
            image.putpixel((2, 1), (120, 70, 20, 255))
            image.putpixel((4, 4), (0, 255, 255, 255))
            image.save(path)

            for index, skill in enumerate((SINGLE, COLOR)):
                analyzer = load_module(
                    f"reference_analyze_{index}", skill / "reference_analyze.py"
                )
                result = analyzer.analyze(path)
                self.assertEqual(result["mode"], "pixel-art")
                self.assertEqual(result["pixel_grid"]["cell_px"], 1)
                self.assertEqual(len(result["pixel_grid"]["cells"]), 6)


class ColorPipelineTests(unittest.TestCase):
    def setUp(self):
        if str(COLOR) not in sys.path:
            sys.path.insert(0, str(COLOR))
        self.cad_helpers = load_module(
            "color_cad_helpers_test", COLOR / "cad_helpers.py"
        )

    def _build_fixture(
        self,
        root: Path,
        *,
        print_package_mode: str | None = None,
        red_continuity: str | None = None,
    ) -> tuple[dict, Path, Path]:
        self.cad_helpers._FEATURES.clear()
        self.cad_helpers._EVENTS.clear()

        left = Box(10, 10, 2, align=(Align.MIN, Align.MIN, Align.MIN))
        right = Pos(10, 0, 0) * Box(
            10, 10, 2, align=(Align.MIN, Align.MIN, Align.MIN)
        )
        parent = left + right
        detail = Box(0.3, 2, 0.6, align=(Align.MIN, Align.MIN, Align.MIN))
        self.cad_helpers.observe(parent, "complete-parent", "parent")
        self.cad_helpers.observe(detail, "thin-color-detail", "additive")
        cut_tool = Pos(9, 4, 0) * Box(
            2, 2, 2, align=(Align.MIN, Align.MIN, Align.MIN)
        )
        self.cad_helpers.checked_cut(parent, cut_tool, "center-slot")

        profile = color_profile.resolve_profile(
            color_profile.load_catalog(),
            machine_name="a1-mini",
            nozzle=0.4,
            tool_index=0,
        )
        profile_path = root / "tile_printer-profile.json"
        profile_path.write_text(color_profile.serialize(profile), encoding="utf-8")
        profile_hash = sha256(profile_path.read_bytes()).hexdigest()
        intent = {
            "schema": "evidence-color-intent/v3",
            "part": "tile",
            "task_mode": "specification",
            "representation": "full-3d",
            "coordinate_system": COORDINATE_SYSTEM,
            "reference_files": [],
            "dimensions_mm": {
                "x": {"value": 20, "source": "user", "confidence": "high"},
                "y": {"value": 10, "source": "user", "confidence": "high"},
                "z": {"value": 2, "source": "user", "confidence": "high"},
            },
            "features": [
                {
                    "id": "complete-parent",
                    "evidence": "fixture observes the complete parent before region export",
                    "acceptance": "the parent observation is accepted as critical build evidence",
                },
                {
                    "id": "thin-color-detail",
                    "evidence": "fixture includes a deliberately thin detail",
                    "acceptance": "detail remains named in printability evidence",
                },
                {
                    "id": "center-slot",
                    "evidence": "fixture cuts a slot through the center",
                    "acceptance": "2 mm cut tool intersects the parent",
                },
            ],
            "color_regions": [
                {
                    "name": "red",
                    "hex": "#CC2233",
                    "purpose": "left field",
                    "boundary": "X 0 through 10 mm",
                    "evidence": "fixture specification",
                },
                {
                    "name": "blue",
                    "hex": "#2255CC",
                    "purpose": "right field",
                    "boundary": "X 10 through 20 mm",
                    "evidence": "fixture specification",
                    "material": {"transmission": "translucent"},
                },
            ],
            "palette_reduction": {"applied": False, "reason": "two colors"},
            "printability": {
                "profile": {"path": profile_path.name, "sha256": profile_hash},
                "build_axis": "+Z",
                "bed_contact": "z-min",
                "support_policy": "support-free",
                "minimum_wall_target_mm": 0.87,
                "critical_features": [
                    "complete-parent",
                    "thin-color-detail",
                    "center-slot",
                ],
            },
            "visual": {
                "required": True,
                "reference_view": "bottom",
                "landmarks": ["red and blue meet at center"],
            },
            "assumptions": [],
        }
        if print_package_mode is not None:
            intent["printability"]["print_package_mode"] = print_package_mode
        if red_continuity is not None:
            intent["color_regions"][0]["continuity"] = red_continuity
        intent_path = root / "tile_intent.json"
        intent_path.write_text(json.dumps(intent), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            report = self.cad_helpers.export_regions(
                {"red": (left, "#CC2233"), "blue": (right, "#2255CC")},
                "tile",
                str(root),
                parent=parent,
                intent_path=str(intent_path),
                source_path=__file__,
            )
        return report, profile_path, intent_path

    def test_v5_report_print_package_display_glb_step_master_and_material_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, _, intent_path = self._build_fixture(root)
            self.assertEqual(report["schema"], "evidence-color-build/v5")
            self.assertEqual(report["print_package_mode"], "co_print_body")
            archive = report["three_mf"]["inspection"]
            self.assertEqual(archive["package_mode"], "co_print_body")
            self.assertEqual(archive["build_item_count"], 1)
            self.assertEqual(archive["component_object_count"], 0)
            self.assertEqual(archive["object_count"], 1)
            self.assertEqual(
                [item["object_kind"] for item in archive["build_items"]],
                ["mesh"],
            )
            self.assertEqual(
                {item["name"] for item in archive["regions"]},
                {"red", "blue"},
            )
            self.assertTrue(all(
                item["kind"] == "mesh-region" and item["triangle_range"]["count"] > 0
                for item in archive["regions"]
            ))
            self.assertIn("events", report)
            self.assertIn("bbox_mm", report["features"]["thin-color-detail"])
            self.assertIn("bbox_mm", report["events"][0]["tool"])
            self.assertTrue((root / "tile.stl").is_file())
            self.assertFalse((root / "tile-manufacturing.stl").exists())
            self.assertFalse((root / "tile-region-red.stl").exists())
            self.assertFalse((root / "tile-region-blue.stl").exists())
            self.assertTrue(
                (root / ".amagine3d-internal" / "tile" / "tile-region-red.stl").is_file()
            )
            self.assertTrue(
                (
                    root
                    / ".amagine3d-internal"
                    / "tile"
                    / "semantic"
                    / "tile-region-red.stl"
                ).is_file()
            )
            self.assertIn("semantic", report["internal_region_meshes"])
            self.assertIn("red", report["internal_region_meshes"]["semantic"])
            self.assertNotIn("stl:region:red", report["artifacts"])
            self.assertNotIn("region_topology", report["coordinates"])
            self.assertTrue((root / "tile-assemble.step").is_file())
            self.assertTrue((root / "tile-display.glb").is_file())
            self.assertEqual(
                _glb_vertex_colors(root / "tile-display.glb"),
                {(204, 34, 51), (34, 85, 204)},
            )
            plan = json.loads((root / "tile_material-plan.json").read_text())
            self.assertFalse(plan["requires_manual_slicer_assignment"])
            self.assertEqual(plan["archive_omits"], ["filament", "transmission"])
            assemble = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "step_check.py"),
                    str(root / "tile-assemble.step"),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(root / "tile_report.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assemble_audit = json.loads(assemble.stdout)
            self.assertEqual(assemble.returncode, 0, assemble.stdout + assemble.stderr)
            self.assertTrue(assemble_audit["pass"], assemble_audit)
            checks = {item["name"]: item for item in assemble_audit["checks"]}
            self.assertEqual(checks["expected_solids"]["observed"], 2)
            self.assertEqual(checks["dimension_x"]["expected"]["value"], 20.0)
            self.assertEqual(checks["dimension_y"]["expected"]["value"], 10.0)
            self.assertEqual(checks["dimension_z"]["expected"]["value"], 2.0)

            assembly = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "assembly_check.py"),
                    str(root / "tile_report.json"),
                    str(root / "tile.3mf"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assembly_payload = json.loads(assembly.stdout)
            self.assertEqual(assembly.returncode, 0, assembly.stdout + assembly.stderr)
            self.assertEqual(assembly_payload["schema"], "color-assembly-audit/v4")
            self.assertFalse(assembly_payload["requires_manual_slicer_assignment"])

    def test_color_regions_require_a_parent_manufacturing_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = Pos(-5, 0, 0) * Box(
                10, 10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            right = Pos(5, 0, 0) * Box(
                10, 10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
            _, _, intent_path = self._build_fixture(root)
            with self.assertRaisesRegex(
                self.cad_helpers.RegionInvariantError,
                "requires parent",
            ):
                self.cad_helpers.export_regions(
                    {"red": (left, "#CC2233"), "blue": (right, "#2255CC")},
                    "tile",
                    str(root),
                    intent_path=str(intent_path),
                )

    def test_separate_parts_mode_keeps_multiple_top_level_build_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, _, _ = self._build_fixture(
                root, print_package_mode="separate_parts"
            )
            archive = report["three_mf"]["inspection"]
            self.assertEqual(report["print_package_mode"], "separate_parts")
            self.assertEqual(archive["package_mode"], "separate_parts")
            self.assertEqual(archive["build_item_count"], 2)
            self.assertEqual(
                [item["object_kind"] for item in archive["build_items"]],
                ["mesh", "mesh"],
            )
            self.assertEqual(
                {item["name"] for item in archive["regions"]},
                {"red", "blue"},
            )

    def test_continuous_core_region_cannot_be_split_across_solids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, profile_path, intent_path = self._build_fixture(
                root, red_continuity="continuous-core"
            )
            self.assertEqual(report["regions"]["red"]["continuity"], "continuous-core")
            report["regions"]["red"]["solid_count"] = 2
            report_path = root / "tile_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(root / "tile.3mf"),
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
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            checks = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(checks["region_continuity"]["status"], "fail")
            self.assertEqual(
                checks["region_continuity"]["observed"]["offenders"][0]["region"],
                "red",
            )

    def test_manufacturing_qa_rejects_unbound_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, profile_path, intent_path = self._build_fixture(root)
            report_path = root / "tile_report.json"
            base_command = [
                sys.executable,
                str(COLOR / "qa_check.py"),
                str(root / "tile.stl"),
                "--profile",
                str(profile_path),
                "--intent",
                str(intent_path),
                "--report",
                str(report_path),
            ]

            report["artifacts"]["stl"]["sha256"] = "0" * 64
            report_path.write_text(json.dumps(report), encoding="utf-8")
            unbound = subprocess.run(
                base_command, check=False, capture_output=True, text=True
            )
            self.assertEqual(unbound.returncode, 2)
            self.assertIn("manufacturing STL", json.loads(unbound.stdout)["error"])

    def test_every_declared_critical_feature_requires_build_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, profile_path, intent_path = self._build_fixture(root)
            intent = json.loads(intent_path.read_text())
            intent["features"].append({
                "id": "unobserved-interface-wall",
                "evidence": "fixture declares another functional feature",
                "acceptance": "must appear in the build evidence",
            })
            intent["printability"]["critical_features"].append(
                "unobserved-interface-wall"
            )
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            report["intent"]["sha256"] = sha256(intent_path.read_bytes()).hexdigest()
            report_path = root / "tile_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(root / "tile.stl"),
                    "--profile",
                    str(profile_path),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(report_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            coverage = next(
                item for item in payload["checks"]
                if item["name"] == "printability_critical_feature_coverage"
            )
            self.assertEqual(coverage["status"], "fail")
            self.assertEqual(
                coverage["observed"]["missing_feature_ids"],
                ["unobserved-interface-wall"],
            )

    def test_region_topology_and_manufacturing_printability_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, profile_path, intent_path = self._build_fixture(root)
            report_path = root / "tile_report.json"

            region = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(
                        root
                        / ".amagine3d-internal"
                        / "tile"
                        / "tile-region-red.stl"
                    ),
                    "--topology-only",
                    "--region",
                    "red",
                    "--components",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            region_payload = json.loads(region.stdout)
            self.assertEqual(region.returncode, 0, region.stdout + region.stderr)
            self.assertEqual(region_payload["scope"], "topology")
            self.assertFalse(any(
                item["category"] == "printability"
                for item in region_payload["checks"]
            ))

            manufacturing = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(root / "tile.stl"),
                    "--profile",
                    str(profile_path),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(report_path),
                    "--components",
                    str(report["manufacturing"]["solid_count"]),
                    "--require-z0",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            manufacturing_payload = json.loads(manufacturing.stdout)
            self.assertEqual(
                manufacturing.returncode,
                0,
                manufacturing.stdout + manufacturing.stderr,
            )
            self.assertEqual(manufacturing_payload["scope"], "manufacturing")
            feature = next(
                item for item in manufacturing_payload["checks"]
                if item["name"] == "printability_feature_resolution"
            )
            self.assertEqual(feature["status"], "warning")
            self.assertEqual(
                feature["observed"]["offenders"][0]["feature_id"],
                "thin-color-detail",
            )
            coverage = next(
                item for item in manufacturing_payload["checks"]
                if item["name"] == "printability_critical_feature_coverage"
            )
            self.assertEqual(coverage["status"], "pass")
            self.assertIn(
                "complete-parent", coverage["observed"]["observed_feature_ids"]
            )
            self.assertNotIn(
                "complete-parent", coverage["observed"]["measured_feature_ids"]
            )

            package = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(root / "tile.3mf"),
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
            package_payload = json.loads(package.stdout)
            self.assertEqual(package.returncode, 0, package.stdout + package.stderr)
            self.assertEqual(package_payload["scope"], "print-package")
            self.assertEqual(
                package_payload["schema"],
                "evidence-color-print-package-audit/v1",
            )
            checks = {item["name"]: item for item in package_payload["checks"]}
            self.assertEqual(checks["print_package_mode"]["status"], "pass")
            self.assertEqual(
                checks["print_package_co_print_build_item"]["status"],
                "pass",
            )
            self.assertEqual(checks["region_names"]["status"], "pass")
            self.assertEqual(checks["region_colors"]["status"], "pass")
            self.assertEqual(checks["build_plane_z0"]["status"], "pass")
            self.assertEqual(checks["printability_bed_fit"]["status"], "pass")
            self.assertEqual(
                checks["print_package_matches_stl_bounds"]["status"],
                "pass",
            )

    def test_export_selects_low_profile_print_orientation_for_tall_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.cad_helpers._FEATURES.clear()
            self.cad_helpers._EVENTS.clear()
            self.cad_helpers._PARAMETERS.clear()

            lower = Box(20, 10, 40, align=(Align.MIN, Align.MIN, Align.MIN))
            upper = Pos(0, 0, 40) * Box(
                20, 10, 40, align=(Align.MIN, Align.MIN, Align.MIN)
            )
            parent = lower + upper
            self.cad_helpers.observe(parent, "complete-parent", "parent")
            self.cad_helpers.observe(lower, "lower-region", "region")
            self.cad_helpers.observe(upper, "upper-region", "region")

            profile = color_profile.resolve_profile(
                color_profile.load_catalog(),
                machine_name="a1-mini",
                nozzle=0.4,
                tool_index=0,
            )
            profile_path = root / "tower_printer-profile.json"
            profile_path.write_text(color_profile.serialize(profile), encoding="utf-8")
            profile_hash = sha256(profile_path.read_bytes()).hexdigest()
            intent = {
                "schema": "evidence-color-intent/v3",
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
                        "id": "complete-parent",
                        "evidence": "fixture parent is the complete tower",
                        "acceptance": "parent covers both regions",
                    },
                    {
                        "id": "lower-region",
                        "evidence": "lower half is red",
                        "acceptance": "lower half is observed",
                    },
                    {
                        "id": "upper-region",
                        "evidence": "upper half is blue",
                        "acceptance": "upper half is observed",
                    },
                ],
                "color_regions": [
                    {
                        "name": "lower",
                        "hex": "#CC2233",
                        "purpose": "lower half",
                        "boundary": "Z 0 through 40 mm",
                        "evidence": "fixture specification",
                    },
                    {
                        "name": "upper",
                        "hex": "#2255CC",
                        "purpose": "upper half",
                        "boundary": "Z 40 through 80 mm",
                        "evidence": "fixture specification",
                    },
                ],
                "palette_reduction": {"applied": False, "reason": "two colors"},
                "printability": {
                    "profile": {"path": profile_path.name, "sha256": profile_hash},
                    "build_axis": "+Z",
                    "bed_contact": "z-min",
                    "support_policy": "support-free",
                    "minimum_wall_target_mm": 0.87,
                    "critical_features": [
                        "complete-parent",
                        "lower-region",
                        "upper-region",
                    ],
                },
                "visual": {
                    "required": True,
                    "reference_view": "front",
                    "landmarks": ["two stacked color regions"],
                },
                "assumptions": [],
            }
            intent_path = root / "tower_intent.json"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                report = self.cad_helpers.export_regions(
                    {"lower": (lower, "#CC2233"), "upper": (upper, "#2255CC")},
                    "tower",
                    str(root),
                    parent=parent,
                    intent_path=str(intent_path),
                    source_path=__file__,
                )
            self.assertEqual(
                report["print_orientation"]["selected"]["name"],
                "rotate-x--90",
            )
            self.assertEqual(
                report["print_orientation"]["selected"]["bed_contact_semantic_face"],
                "back",
            )
            self.assertEqual(
                report["manufacturing"]["bbox_mm"]["size"],
                [20.0, 80.0, 10.0],
            )
            self.assertEqual(report["semantic"]["shape"]["bbox_mm"]["size"], [20.0, 10.0, 80.0])
            self.assertEqual(report["assembly"]["shape"]["bbox_mm"]["size"], [20.0, 10.0, 80.0])

            package = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "qa_check.py"),
                    str(root / "tower.3mf"),
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
            self.assertEqual(package.returncode, 0, package.stdout + package.stderr)
            package_payload = json.loads(package.stdout)
            dimensions = package_payload["mesh"]["dimensions_mm"]
            self.assertEqual(dimensions, [20.0, 80.0, 10.0])

            step = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "step_check.py"),
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

            assemble = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "step_check.py"),
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
            self.assertEqual(assemble.returncode, 0, assemble.stdout + assemble.stderr)

    def test_orientation_candidates_include_top_down_and_scale_evidence(self):
        profile = color_profile.resolve_profile(
            color_profile.load_catalog(),
            machine_name="a1-mini",
            nozzle=0.4,
            tool_index=0,
        )
        oversized = Box(40, 20, 220, align=(Align.MIN, Align.MIN, Align.MIN))
        candidates = self.cad_helpers._orientation_candidates(oversized, profile)
        by_name = {item["name"]: item for item in candidates}
        self.assertIn("rotate-x-180", by_name)
        top_down = by_name["rotate-x-180"]
        self.assertEqual(top_down["bed_contact_semantic_face"], "top")
        self.assertFalse(top_down["uniform_scale_to_fit_profile"]["fits_without_scaling"])
        self.assertTrue(top_down["fits_profile"])
        self.assertTrue(top_down["requires_uniform_scale"])
        self.assertAlmostEqual(
            top_down["uniform_scale_to_fit_profile"]["scale"],
            180 / 220,
            places=6,
        )
        self.assertAlmostEqual(top_down["scale_to_apply"], 180 / 220, places=12)
        self.assertEqual(
            top_down["print_dimensions_mm"],
            [round(40 * 180 / 220, 5), round(20 * 180 / 220, 5), 180.0],
        )

    def test_bottom_matched_view_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._build_fixture(root)
            report_path = root / "render.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "render_preview.py"),
                    "--part",
                    f"{root / '.amagine3d-internal' / 'tile' / 'tile-region-red.stl'}=#CC2233",
                    "--part",
                    f"{root / '.amagine3d-internal' / 'tile' / 'tile-region-blue.stl'}=#2255CC",
                    "--out",
                    str(root / "views.png"),
                    "--reference-view",
                    "bottom",
                    "--reference-out",
                    str(root / "bottom.png"),
                    "--report",
                    str(report_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            render = json.loads(report_path.read_text())
            self.assertEqual(render["matched_view"]["name"], "bottom")
            self.assertEqual(
                [region["name"] for region in render["regions"]],
                ["red", "blue"],
            )
            self.assertTrue((root / "bottom.png").is_file())


class ColorContractTests(unittest.TestCase):
    def test_checked_in_v3_example_is_valid(self):
        result = subprocess.run(
            [
                sys.executable,
                str(COLOR / "intent_contract.py"),
                str(COLOR / "examples" / "intent.example.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["pass"])

    def test_non_opaque_regions_leave_filament_choice_to_user(self):
        example_path = COLOR / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text())
        data["color_regions"][0].pop("material", None)
        data["color_regions"][1]["material"] = {"transmission": "translucent"}
        errors = color_intent.validate(data, example_path.parent)
        self.assertFalse(any("filament" in item for item in errors))
        self.assertFalse(any("material" in item for item in errors))

    def test_flat_semantic_feature_fields_are_validated(self):
        example_path = COLOR / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text())
        self.assertEqual(color_intent.validate(data, example_path.parent), [])

        data["features"][0].update({
            "kind": "port",
            "face": "bottom",
            "direction": "-Y",
            "edge_crossing": "forbidden",
        })
        errors = color_intent.validate(data, example_path.parent)
        self.assertTrue(any("direction must be one of" in item for item in errors))

        data["features"][0]["direction"] = "-Z"
        data["features"][0].pop("edge_crossing")
        errors = color_intent.validate(data, example_path.parent)
        self.assertIn("features[0].edge_crossing is required for kind port", errors)

    def test_semantic_feature_placement_detects_wrong_edge_cut(self):
        intent = {
            "features": [
                {
                    "id": "charging-port",
                    "kind": "port",
                    "face": "bottom",
                    "direction": "-Z",
                    "edge_crossing": "forbidden",
                }
            ]
        }
        report = {
            "assembly": {
                "shape": {
                    "bbox_mm": {
                        "min": [-20, -10, 0],
                        "max": [20, 10, 40],
                        "size": [40, 20, 40],
                    },
                }
            },
            "events": [
                {
                    "id": "charging-port",
                    "kind": "cut",
                    "tool": {
                        "bbox_mm": {
                            "min": [-3, -11, -1],
                            "max": [3, -8, 2],
                            "size": [6, 3, 3],
                        }
                    },
                }
            ],
            "features": {},
        }
        observed = color_qa.semantic_placement_observation(intent, report)
        self.assertEqual(observed["offenders"][0]["feature_id"], "charging-port")
        self.assertEqual(observed["offenders"][0]["adjacent_external_faces"], ["front"])

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
            "assembly": {
                "shape": {
                    "bbox_mm": {
                        "min": [-20, -10, 0],
                        "max": [20, 10, 40],
                        "size": [40, 20, 40],
                    },
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
        observed = color_qa.semantic_placement_observation(intent, report)
        self.assertEqual(observed["examined"], 0)
        self.assertEqual(observed["offenders"], [])
        self.assertEqual(observed["skipped"][0]["feature_id"], "surface-logo")

    def test_single_color_contract_accepts_bottom_matched_view(self):
        example_path = SINGLE / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text())
        data["visual"] = {
            "required": True,
            "reference_view": "bottom",
            "landmarks": ["appearance-bearing face at Z0"],
        }
        self.assertEqual(single_intent.validate(data, example_path.parent), [])


if __name__ == "__main__":
    unittest.main()
