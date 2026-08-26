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

    def _build_fixture(self, root: Path) -> tuple[dict, Path, Path]:
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
                    "material": {"transmission": "opaque"},
                },
                {
                    "name": "blue",
                    "hex": "#2255CC",
                    "purpose": "right field",
                    "boundary": "X 10 through 20 mm",
                    "evidence": "fixture specification",
                    "material": {
                        "transmission": "translucent",
                        "filament": "translucent blue PLA",
                    },
                },
            ],
            "palette_reduction": {"applied": False, "reason": "two filaments"},
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
            report, _, _ = self._build_fixture(root)
            self.assertEqual(report["schema"], "evidence-color-build/v5")
            self.assertIn("events", report)
            self.assertIn("bbox_mm", report["features"]["thin-color-detail"])
            self.assertIn("bbox_mm", report["events"][0]["tool"])
            self.assertTrue((root / "tile-manufacturing.stl").is_file())
            self.assertTrue((root / "tile-region-red.stl").is_file())
            self.assertTrue((root / "tile-region-blue.stl").is_file())
            self.assertTrue((root / "tile-assemble.step").is_file())
            self.assertTrue((root / "tile-display.glb").is_file())
            self.assertEqual(
                _glb_vertex_colors(root / "tile-display.glb"),
                {(204, 34, 51), (34, 85, 204)},
            )
            plan = json.loads((root / "tile_material-plan.json").read_text())
            self.assertTrue(plan["requires_manual_slicer_assignment"])
            self.assertEqual(plan["archive_omits"], ["filament", "transmission"])
            assemble_audit = color_step_check.audit_step(
                root / "tile-assemble.step",
                expect_solids=2,
                expect_x=20,
                expect_y=10,
                expect_z=2,
            )
            self.assertTrue(assemble_audit["pass"], assemble_audit)

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
            self.assertTrue(assembly_payload["requires_manual_slicer_assignment"])

    def test_manufacturing_qa_rejects_unbound_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, profile_path, intent_path = self._build_fixture(root)
            report_path = root / "tile_report.json"
            base_command = [
                sys.executable,
                str(COLOR / "qa_check.py"),
                str(root / "tile-manufacturing.stl"),
                "--profile",
                str(profile_path),
                "--intent",
                str(intent_path),
                "--report",
                str(report_path),
            ]

            report["artifacts"]["stl:manufacturing"]["sha256"] = "0" * 64
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
                    str(root / "tile-manufacturing.stl"),
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
                    str(root / "tile-region-red.stl"),
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
                    str(root / "tile-manufacturing.stl"),
                    "--profile",
                    str(profile_path),
                    "--intent",
                    str(intent_path),
                    "--report",
                    str(report_path),
                    "--components",
                    str(report["manufacturing"]["solid_count"]),
                    "--expect-x",
                    "20",
                    "--expect-y",
                    "10",
                    "--expect-z",
                    "2",
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
                    f"{root / 'tile-region-red.stl'}=#CC2233",
                    "--part",
                    f"{root / 'tile-region-blue.stl'}=#2255CC",
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

    def test_non_opaque_regions_require_a_filament_assignment(self):
        example_path = COLOR / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text())
        data["color_regions"][1]["material"] = {"transmission": "translucent"}
        errors = color_intent.validate(data, example_path.parent)
        self.assertTrue(any("filament is required" in item for item in errors))

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
