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
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from build123d import Align, Box, Pos
import numpy as np
from PIL import Image
import trimesh


ROOT = Path(__file__).resolve().parents[2]
SINGLE = ROOT / "skills" / "text-a3d"
COLOR = SINGLE / "color"
if str(SINGLE) not in sys.path:
    sys.path.insert(0, str(SINGLE))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


color_profile = load_module("shared_bambu_profile", SINGLE / "bambu_profile.py")
build_check = load_module("color_build_check", SINGLE / "build_check.py")
single_intent = load_module("single_intent_contract", SINGLE / "intent_contract.py")
color_intent = single_intent
color_qa = load_module("color_qa_check", COLOR / "qa_check.py")
color_step_check = load_module("color_step_check", SINGLE / "step_check.py")

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


def _write_brep_scene(root: Path, intent_path: Path, part_name: str) -> Path:
    scene_path = root / f"{intent_path.stem}_scene.json"
    scene = {
        "schema": "evidence-semantic-scene/v1",
        "revision": "color-test-rev-001",
        "intentRef": {
            "path": str(intent_path),
            "schema": "evidence-cad-intent/v5",
            "sha256": sha256(intent_path.read_bytes()).hexdigest(),
        },
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "materials": [],
        "parts": [{"id": part_name, "representationMaster": "brep"}],
        "nodes": [{
            "id": f"{part_name}-body",
            "partId": part_name,
            "featureId": "complete-parent",
            "role": "solid",
            "operation": "union",
            "recipe": {
                "kind": "roundedBox",
                "parameters": {"sizeMm": [1, 1, 1], "radiusMm": 0.0},
            },
        }],
        "interfaces": [],
    }
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    return scene_path


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


class SharedColorProfileTests(unittest.TestCase):
    def test_color_backend_uses_the_root_profile_catalog(self):
        self.assertEqual(color_profile.CATALOG_PATH.parent.parent, SINGLE)
        catalog = color_profile.load_catalog()
        mini = color_profile.resolve_profile(
            catalog, machine_name="a1-mini", nozzle=0.4, tool_index=0
        )
        h2d = color_profile.resolve_profile(
            catalog, machine_name="h2d", nozzle=0.4, tool_index=1
        )
        self.assertEqual(mini["derived"]["process_wall_target_mm"], 0.87)
        self.assertEqual(
            mini["derived"]["rotation_safe_envelope"]["max_spatial_diagonal_mm"],
            180.0,
        )
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

            analyzer = load_module(
                "shared_reference_analyze", SINGLE / "reference_analyze.py"
            )
            result = analyzer.analyze(path)
            self.assertEqual(result["schema"], "evidence-reference-analysis/v1")
            self.assertEqual(result["source"]["path"], str(path.resolve()))
            self.assertEqual(result["source"]["sha256"], result["image"]["sha256"])
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

    def test_overlap_volume_handles_disjoint_solid_color_regions(self):
        left = Box(1, 1, 1).solids()[0]
        disjoint = (Pos(3, 0, 0) * Box(1, 1, 1)).solids()[0]
        overlapping = (Pos(0.5, 0, 0) * Box(1, 1, 1)).solids()[0]

        self.assertEqual(self.cad_helpers._intersection_volume(left, disjoint), 0.0)
        self.assertAlmostEqual(
            self.cad_helpers._intersection_volume(left, overlapping), 0.5
        )

    def test_3mf_writer_and_cli_require_an_explicit_package_mode(self):
        exporter = self.cad_helpers._export_3mf
        self.assertFalse(hasattr(exporter, "write_3mf"))
        self.assertFalse(hasattr(exporter, "verify_3mf"))
        self.assertEqual(exporter._archive_package_mode(None), "invalid")
        with self.assertRaisesRegex(TypeError, "package_mode"):
            exporter.write_color_archive([], "unused.3mf")

        missing = subprocess.run(
            [
                sys.executable,
                str(COLOR / "export_3mf.py"),
                "unused.3mf",
                "unused.stl=#CC2233",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--package-mode", missing.stdout)

        for legacy_args in (
            ["--verify", "unused.3mf"],
            ["--separate-parts", "unused.3mf", "unused.stl=#CC2233"],
        ):
            with self.subTest(legacy_args=legacy_args):
                legacy = subprocess.run(
                    [
                        sys.executable,
                        str(COLOR / "export_3mf.py"),
                        *legacy_args,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(legacy.returncode, 2)
                self.assertIn("--package-mode", legacy.stdout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh_path = root / "body.stl"
            archive_path = root / "body.3mf"
            trimesh.creation.box(extents=(2, 3, 4)).export(mesh_path)
            explicit = subprocess.run(
                [
                    sys.executable,
                    str(COLOR / "export_3mf.py"),
                    "--package-mode",
                    "co_print_body",
                    str(archive_path),
                    f"{mesh_path}=#CC2233",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                explicit.returncode,
                0,
                explicit.stdout + explicit.stderr,
            )
            self.assertEqual(
                exporter.inspect_color_archive(str(archive_path))["package_mode"],
                "co_print_body",
            )
            inspection = exporter.inspect_color_archive(str(archive_path))
            self.assertEqual(inspection["component_object_count"], 1)
            self.assertEqual(inspection["object_count"], 1)
            self.assertEqual(
                inspection["build_items"][0]["object_kind"], "components"
            )
            self.assertTrue(inspection["lib3mf"]["verified"])

    def test_color_archive_rejects_open_regions_and_missing_region_metadata(self):
        exporter = self.cad_helpers._export_3mf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            open_mesh_path = root / "open.stl"
            trimesh.Trimesh(
                vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                faces=[[0, 1, 2]],
                process=False,
            ).export(open_mesh_path)
            with self.assertRaisesRegex(ValueError, "closed volumetric mesh"):
                exporter.write_color_archive(
                    [(open_mesh_path, "#CC2233", "open")],
                    str(root / "open.3mf"),
                    package_mode="co_print_body",
                    package_name="open",
                )

            mesh_path = root / "body.stl"
            archive_path = root / "body.3mf"
            trimesh.creation.box(extents=(2, 3, 4)).export(mesh_path)
            exporter.write_color_archive(
                [(mesh_path, "#CC2233", "body")],
                str(archive_path),
                package_mode="co_print_body",
                package_name="body",
            )
            with ZipFile(archive_path) as archive:
                members = {
                    name: archive.read(name) for name in archive.namelist()
                }
            model_name = next(
                name for name in members if name.lower().endswith(".model")
            )
            root_xml = ElementTree.fromstring(members[model_name])
            for child in list(root_xml):
                if child.tag.rsplit("}", 1)[-1] == "metadata":
                    root_xml.remove(child)
            members[model_name] = ElementTree.tostring(
                root_xml, encoding="utf-8", xml_declaration=True
            )
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, "missing required color-region"):
                exporter.inspect_color_archive(str(archive_path))

            exporter.write_color_archive(
                [(mesh_path, "#CC2233", "body")],
                str(archive_path),
                package_mode="co_print_body",
                package_name="body",
            )
            with ZipFile(archive_path) as archive:
                members = {
                    name: archive.read(name) for name in archive.namelist()
                }
            root_xml = ElementTree.fromstring(members[model_name])
            metadata = next(
                child
                for child in root_xml
                if child.tag.rsplit("}", 1)[-1] == "metadata"
                and child.attrib.get("name", "").endswith(
                    "amagine3d-color-regions"
                )
            )
            payload = json.loads(metadata.text)
            payload["regions"] = []
            metadata.text = json.dumps(payload)
            members[model_name] = ElementTree.tostring(
                root_xml, encoding="utf-8", xml_declaration=True
            )
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, "must contain regions"):
                exporter.inspect_color_archive(str(archive_path))

            exporter.write_color_archive(
                [(mesh_path, "#CC2233", "body")],
                str(archive_path),
                package_mode="co_print_body",
                package_name="body",
            )
            with ZipFile(archive_path) as archive:
                members = {
                    name: archive.read(name) for name in archive.namelist()
                }
            root_xml = ElementTree.fromstring(members[model_name])
            for child in list(root_xml):
                if child.tag.rsplit("}", 1)[-1] == "build":
                    root_xml.remove(child)
            members[model_name] = ElementTree.tostring(
                root_xml, encoding="utf-8", xml_declaration=True
            )
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, "explicit build items"):
                exporter.inspect_color_archive(str(archive_path))
            with self.assertRaisesRegex(ValueError, "explicit build items"):
                exporter.load_color_archive_mesh(str(archive_path))

            exporter.write_color_archive(
                [(mesh_path, "#CC2233", "body")],
                str(archive_path),
                package_mode="co_print_body",
                package_name="body",
            )
            with ZipFile(archive_path) as archive:
                members = {
                    name: archive.read(name) for name in archive.namelist()
                }
            root_xml = ElementTree.fromstring(members[model_name])
            mesh_object_id = next(
                element.attrib["id"]
                for element in root_xml.iter()
                if element.tag.rsplit("}", 1)[-1] == "object"
                and any(
                    child.tag.rsplit("}", 1)[-1] == "mesh"
                    for child in element
                )
            )
            build_item = next(
                element
                for element in root_xml.iter()
                if element.tag.rsplit("}", 1)[-1] == "item"
            )
            build_item.attrib["objectid"] = mesh_object_id
            members[model_name] = ElementTree.tostring(
                root_xml, encoding="utf-8", xml_declaration=True
            )
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, "region/material/build graph"):
                exporter.inspect_color_archive(str(archive_path))

    def test_color_qa_never_infers_package_mode_from_the_build_report(self):
        self.assertEqual(color_qa.print_package_mode(None), "invalid")
        self.assertEqual(color_qa.print_package_mode({"printability": {}}), "invalid")
        self.assertEqual(
            color_qa.print_package_mode({
                "printability": {"print_package_mode": "co_print_body"}
            }),
            "co_print_body",
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
            "schema": "evidence-cad-intent/v5",
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
                    "kind": "envelope",
                    "evidence": "fixture observes the complete parent before region export",
                    "acceptance": "the parent observation is accepted as critical build evidence",
                },
                {
                    "id": "thin-color-detail",
                    "kind": "detail",
                    "evidence": "fixture includes a deliberately thin detail",
                    "acceptance": "detail remains named in printability evidence",
                },
                {
                    "id": "center-slot",
                    "kind": "detail",
                    "evidence": "fixture cuts a slot through the center",
                    "acceptance": "2 mm cut tool intersects the parent",
                },
            ],
            "color_regions": [
                {
                    "name": "red",
                    "part": "tile",
                    "hex": "#CC2233",
                    "purpose": "left field",
                    "boundary": "X 0 through 10 mm",
                    "evidence": "fixture specification",
                },
                {
                    "name": "blue",
                    "part": "tile",
                    "hex": "#2255CC",
                    "purpose": "right field",
                    "boundary": "X 10 through 20 mm",
                    "evidence": "fixture specification",
                    "material": {"transmission": "translucent"},
                },
            ],
            "palette_reduction": {"applied": False, "reason": "two colors"},
            "manufacturing": {"mode": "single-part"},
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
                "print_package_mode": "co_print_body",
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
        scene_path = _write_brep_scene(root, intent_path, "tile")
        with contextlib.redirect_stdout(io.StringIO()):
            report = self.cad_helpers.export_regions(
                {"red": (left, "#CC2233"), "blue": (right, "#2255CC")},
                "tile",
                str(root),
                parent=parent,
                intent_path=str(intent_path),
                scene_path=str(scene_path),
                source_path=__file__,
            )
        manifest_audit = build_check.audit(root / "tile_report.json")
        self.assertTrue(manifest_audit["pass"], manifest_audit)
        return report, profile_path, intent_path

    def test_unified_report_print_package_display_glb_step_master_and_material_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, _, intent_path = self._build_fixture(root)
            self.assertEqual(report["schema"], "evidence-a3d-build/v1")
            self.assertEqual(report["backend"], "brep-color-regions")
            self.assertTrue(report["backendData"]["exportAudit"]["pass"])
            self.assertEqual(
                len(report["backendData"]["exportAudit"]["artifacts"]), 7
            )
            self.assertTrue((root / "tile_export-audit.json").is_file())
            self.assertEqual(report["backendData"]["printPackageMode"], "co_print_body")
            archive = report["backendData"]["threeMf"]["inspection"]
            self.assertEqual(archive["package_mode"], "co_print_body")
            self.assertEqual(archive["build_item_count"], 1)
            self.assertEqual(archive["component_object_count"], 1)
            self.assertEqual(archive["object_count"], 2)
            self.assertEqual(
                [item["object_kind"] for item in archive["build_items"]],
                ["components"],
            )
            self.assertEqual(
                {item["name"] for item in archive["regions"]},
                {"red", "blue"},
            )
            self.assertTrue(all(
                item["kind"] == "mesh"
                and item["topology"] == {
                    "body_count": 1,
                    "is_volume": True,
                    "watertight": True,
                }
                for item in archive["regions"]
            ))
            self.assertTrue(archive["lib3mf"]["verified"])
            self.assertEqual(
                {item["name"] for item in archive["lib3mf"]["mesh_objects"]},
                {"red", "blue"},
            )
            self.assertEqual(
                archive["lib3mf"]["build_items"][0]["kind"], "components"
            )
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
            meshes = report["backendData"]["internalRegionMeshes"]
            self.assertIn("semantic", meshes)
            self.assertIn("red", meshes["semantic"])
            self.assertIn("region:red:semantic", report["artifacts"])
            self.assertIn("region:red:print", report["artifacts"])
            self.assertTrue((root / "tile.step").is_file())
            self.assertTrue((root / "tile-display.glb").is_file())
            self.assertEqual(
                _glb_vertex_colors(root / "tile-display.glb"),
                {(204, 34, 51), (34, 85, 204)},
            )
            plan = json.loads((root / "tile_material-plan.json").read_text())
            self.assertTrue(plan["requiresManualSlicerAssignment"])
            self.assertEqual(
                plan["archiveOmits"],
                ["filament", "transmission", "slicer-filament-slot"],
            )
            assemble = subprocess.run(
                [
                    sys.executable,
                    str(SINGLE / "step_check.py"),
                    str(root / "tile.step"),
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
            self.assertEqual(
                assembly_payload["schema"], "evidence-assembly-audit/v1"
            )
            self.assertTrue(assembly_payload["requiresManualSlicerAssignment"])

    def test_material_plan_artifact_must_equal_inline_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, _, _ = self._build_fixture(root)
            material_path = root / "tile_material-plan.json"
            external = json.loads(material_path.read_text(encoding="utf-8"))
            external["part"] = "different-part"
            material_path.write_text(json.dumps(external), encoding="utf-8")
            report["artifacts"]["materialPlan"]["sha256"] = sha256(
                material_path.read_bytes()
            ).hexdigest()
            report_path = root / "tile_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            result = build_check.audit(report_path)
            self.assertFalse(result["pass"])
            self.assertIn(
                "materialPlan artifact content does not match inline materialPlan",
                result["errors"],
            )

    def test_export_audit_artifact_must_equal_inline_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, _, _ = self._build_fixture(root)
            audit_path = root / "tile_export-audit.json"
            external = json.loads(audit_path.read_text(encoding="utf-8"))
            external["artifacts"]["stl:tile"]["observed"]["volumeMm3"] = -1
            audit_path.write_text(json.dumps(external), encoding="utf-8")
            report["artifacts"]["exportAudit"]["sha256"] = sha256(
                audit_path.read_bytes()
            ).hexdigest()
            report_path = root / "tile_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            result = build_check.audit(report_path)
            self.assertFalse(result["pass"])
            self.assertIn(
                "exportAudit artifact content does not match backendData.exportAudit",
                result["errors"],
            )

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
                    scene_path=str(root / "tile_intent_scene.json"),
                    source_path=__file__,
                )

    def test_single_body_color_rejects_separate_parts_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                self.cad_helpers.RegionInvariantError,
                "single-part colors require printability.print_package_mode co_print_body",
            ):
                self._build_fixture(root, print_package_mode="separate_parts")

    def test_continuous_core_region_cannot_be_split_across_solids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, profile_path, intent_path = self._build_fixture(
                root, red_continuity="continuous-core"
            )
            regions = report["backendData"]["regions"]
            self.assertEqual(regions["red"]["continuity"], "continuous-core")
            regions["red"]["solid_count"] = 2
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

            report["artifacts"]["stl:tile"]["sha256"] = "0" * 64
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
            report["inputs"]["intent"]["sha256"] = sha256(intent_path.read_bytes()).hexdigest()
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
                    str(report["parts"]["tile"]["print"]["bodyCount"]),
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
                "schema": "evidence-cad-intent/v5",
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
                        "kind": "envelope",
                        "evidence": "fixture parent is the complete tower",
                        "acceptance": "parent covers both regions",
                    },
                    {
                        "id": "lower-region",
                        "kind": "region",
                        "evidence": "lower half is red",
                        "acceptance": "lower half is observed",
                    },
                    {
                        "id": "upper-region",
                        "kind": "region",
                        "evidence": "upper half is blue",
                        "acceptance": "upper half is observed",
                    },
                ],
                "color_regions": [
                    {
                        "name": "lower",
                        "part": "tower",
                        "hex": "#CC2233",
                        "purpose": "lower half",
                        "boundary": "Z 0 through 40 mm",
                        "evidence": "fixture specification",
                    },
                    {
                        "name": "upper",
                        "part": "tower",
                        "hex": "#2255CC",
                        "purpose": "upper half",
                        "boundary": "Z 40 through 80 mm",
                        "evidence": "fixture specification",
                    },
                ],
                "palette_reduction": {"applied": False, "reason": "two colors"},
                "manufacturing": {"mode": "single-part"},
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
                    "print_package_mode": "co_print_body",
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
            scene_path = _write_brep_scene(root, intent_path, "tower")
            with contextlib.redirect_stdout(io.StringIO()):
                report = self.cad_helpers.export_regions(
                    {"lower": (lower, "#CC2233"), "upper": (upper, "#2255CC")},
                    "tower",
                    str(root),
                    parent=parent,
                    intent_path=str(intent_path),
                    scene_path=str(scene_path),
                    source_path=__file__,
                )
            self.assertEqual(
                report["backendData"]["printOrientation"]["selected"]["name"],
                "rotate-x--90",
            )
            self.assertEqual(
                report["backendData"]["printOrientation"]["selected"]["bed_contact_semantic_face"],
                "back",
            )
            self.assertEqual(
                report["parts"]["tower"]["print"]["boundsMm"]["size"],
                [20.0, 80.0, 10.0],
            )
            self.assertEqual(report["parts"]["tower"]["semantic"]["boundsMm"]["size"], [20.0, 10.0, 80.0])

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
                    str(SINGLE / "step_check.py"),
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

            assemble = subprocess.run(
                [
                    sys.executable,
                    str(SINGLE / "step_check.py"),
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
            self.assertEqual(assemble.returncode, 0, assemble.stdout + assemble.stderr)

    def test_orientation_candidates_keep_scale_as_repair_evidence_only(self):
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
        self.assertFalse(top_down["fits_profile"])
        self.assertTrue(top_down["requires_uniform_scale"])
        self.assertAlmostEqual(
            top_down["uniform_scale_to_fit_profile"]["scale"],
            180 / 220,
            places=6,
        )
        self.assertNotIn("scale_to_apply", top_down)
        self.assertEqual(
            top_down["print_dimensions_mm"],
            [40.0, 20.0, 220.0],
        )

    def test_bottom_matched_view_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._build_fixture(root)
            report_path = root / "render.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SINGLE / "render_preview.py"),
                    str(root / "tile-display.glb"),
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
            self.assertEqual(render["schema"], "evidence-render/v2")
            self.assertEqual(render["matched_view"]["name"], "bottom")
            self.assertEqual(
                {tuple(mesh["preview_color_rgb"]) for mesh in render["meshes"]},
                {(204, 34, 51), (34, 85, 204)},
            )
            self.assertTrue((root / "bottom.png").is_file())


class ColorContractTests(unittest.TestCase):
    def test_removed_manufacturing_stl_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "body.stl"
            model.write_text("solid body\nendsolid body\n", encoding="utf-8")
            report = {
                "artifacts": {
                    "stl:manufacturing": {
                        "path": str(model),
                        "sha256": sha256(model.read_bytes()).hexdigest(),
                    }
                },
                "parts": {"body": {}},
            }
            with self.assertRaisesRegex(ValueError, "manufacturing STL"):
                color_qa._report_artifact_key(report, model, root)

    def test_color_risk_attribution_uses_artifact_frame_matrix(self):
        matrix = [
            [0.0, -1.0, 0.0, 10.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        report = {
            "part": "body",
            "artifacts": {
                "stl:body": {"coordinateFrame": "part-print"},
            },
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
                "part-print": {"partTransforms": {"body": matrix}},
            },
        }
        observed = color_qa._affected(
            np.asarray([[8.2, 0.2, 0.2], [9.8, 0.8, 0.8]]),
            report,
            artifact_key="stl:body",
            part_name="body",
        )
        self.assertEqual(observed, {
            "feature_ids": ["rotated-feature"],
            "status": "evaluated",
        })

    def test_checked_in_root_v5_color_example_is_valid(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SINGLE / "intent_contract.py"),
                str(SINGLE / "examples" / "intent.example.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["pass"])

    def test_non_opaque_regions_leave_filament_choice_to_user(self):
        example_path = SINGLE / "examples" / "intent.example.json"
        data = json.loads(example_path.read_text())
        data["color_regions"][0].pop("material", None)
        data["color_regions"][1]["material"] = {"transmission": "translucent"}
        errors = color_intent.validate(data, example_path.parent)
        self.assertFalse(any("filament" in item for item in errors))
        self.assertFalse(any("material" in item for item in errors))

    def test_flat_semantic_feature_fields_are_validated(self):
        example_path = SINGLE / "examples" / "intent.example.json"
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
            "backendData": {
                "semanticAssembly": {
                    "boundsMm": {
                        "min": [-20, -10, 0],
                        "max": [20, 10, 40],
                        "size": [40, 20, 40],
                    }
                }
            },
            "part": "fixture",
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

    def test_plate_semantic_placement_uses_declared_owner_bounds(self):
        intent = {
            "part": "device",
            "manufacturing": {"mode": "multipart"},
            "features": [
                {
                    "id": "rear-port",
                    "kind": "port",
                    "part": "shell",
                    "face": "back",
                    "edge_crossing": "allowed",
                }
            ],
        }
        report = {
            "part": "device",
            "backendData": {
                "semanticAssembly": {
                    "boundsMm": {
                        "min": [0, 0, 0],
                        "max": [20, 30, 20],
                        "size": [20, 30, 20],
                    }
                }
            },
            "parts": {
                "shell": {
                    "semantic": {
                        "boundsMm": {
                            "min": [0, 0, 0],
                            "max": [20, 10, 20],
                            "size": [20, 10, 20],
                        }
                    }
                },
                "base": {
                    "semantic": {
                        "boundsMm": {
                            "min": [0, 0, 0],
                            "max": [20, 30, 5],
                            "size": [20, 30, 5],
                        }
                    }
                },
            },
            "features": {},
            "events": [
                {
                    "id": "rear-port",
                    "kind": "cut",
                    "part": "shell",
                    "tool": {
                        "bbox_mm": {
                            "min": [5, 9.8, 5],
                            "max": [10, 10.2, 10],
                            "size": [5, 0.4, 5],
                        }
                    },
                }
            ],
        }

        observed = color_qa.semantic_placement_observation(intent, report)
        self.assertEqual(observed["offenders"], [])
        self.assertEqual(observed["observations"][0]["owner_part"], "shell")
        report["parts"]["base"]["semantic"]["boundsMm"]["max"][1] = 40
        self.assertEqual(
            color_qa.semantic_placement_observation(intent, report)["offenders"],
            [],
        )

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
            "backendData": {
                "semanticAssembly": {
                    "boundsMm": {
                        "min": [-20, -10, 0],
                        "max": [20, 10, 40],
                        "size": [40, 20, 40],
                    }
                }
            },
            "part": "fixture",
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
