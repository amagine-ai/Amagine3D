from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

import numpy as np
import trimesh
from build123d import Align, Box, Cylinder, Pos, export_step


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import hybrid_compile  # noqa: E402
import build_check  # noqa: E402
import intent_contract  # noqa: E402
import scene_contract  # noqa: E402
import self_tapping_geometry  # noqa: E402
import shape_consistency  # noqa: E402
from tests.python.intent_fixture import (  # noqa: E402
    intent_ref as fixture_intent_ref,
    write_intent as write_fixture_intent,
)


def _canonical_part(report: dict, output: Path, part_id: str) -> trimesh.Trimesh:
    mesh = trimesh.load(output / f"{part_id}.stl", force="mesh")
    transform = np.asarray(
        report["coordinateFrames"]["part-print"]["partTransforms"][part_id],
        dtype=float,
    )
    mesh.apply_transform(np.linalg.inv(transform))
    return mesh


def _write_intent(
    root: Path,
    *,
    part: str = "companion",
    feature_owners: dict[str, str],
    color_regions: list[dict] | None = None,
    manufacturing: dict | None = None,
    dimensions_mm: tuple[float, float, float] = (40.0, 30.0, 20.0),
) -> dict:
    path, intent = write_fixture_intent(
        root,
        part=part,
        feature_owners=feature_owners,
        color_regions=color_regions,
        dimensions_mm=dimensions_mm,
        manufacturing=manufacturing,
    )
    intent["printability"]["print_package_mode"] = (
        "co_print_body"
        if intent["manufacturing"]["mode"] == "single-part"
        else "separate_parts"
    )
    path.write_text(json.dumps(intent), encoding="utf-8")
    return fixture_intent_ref(path, relative_to=root)


def _fixture(root: Path) -> tuple[dict, float]:
    source = root / "source"
    source.mkdir()
    housing = trimesh.creation.box(extents=[20, 20, 10])
    cutter = trimesh.creation.cylinder(radius=3, height=14, sections=48)
    button = trimesh.creation.cylinder(radius=2, height=3, sections=32)
    button.apply_translation([15, 0, 0])
    # A screen surface is intentionally non-volumetric: it belongs in the
    # visual GLB but must never become a printable solid.
    display_component = trimesh.Trimesh(
        vertices=np.asarray(
            [
                [-4.0, -3.0, 5.05],
                [4.0, -3.0, 5.05],
                [4.0, 3.0, 5.05],
                [-4.0, 3.0, 5.05],
            ]
        ),
        faces=np.asarray([[0, 1, 2], [0, 2, 3]]),
        process=False,
    )
    housing.export(source / "housing.stl")
    cutter.export(source / "cutter.stl")
    button.export(source / "button.stl")
    display_component.export(source / "display-component.ply")
    button_step = source / "button.step"
    export_step(
        Pos(15, 0, 0)
        * Cylinder(
            2,
            3,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        ),
        str(button_step),
    )

    scene = {
        "schema": "evidence-semantic-scene/v1",
        "revision": "hybrid-rev-001",
        "intentRef": _write_intent(
            root,
            dimensions_mm=(27.0, 20.0, 10.0),
            feature_owners={
                "housing/outer": "housing",
                "housing/through-hole": "housing",
                "controls/button": "button",
            },
        ),
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "parts": [
            {
                "id": "housing",
                "representationMaster": "mesh",
                "color": "#EEE8DC",
            },
            {
                "id": "button",
                "representationMaster": "brep",
                "artifacts": {
                    "masterStep": {
                        "path": "source/button.step",
                        "revision": "hybrid-rev-001",
                        "scale": 1.0,
                        "sha256": sha256(button_step.read_bytes()).hexdigest(),
                    }
                },
                "appearance": {
                    "baseColor": "#F06450",
                    "metallic": 0.0,
                    "roughness": 0.42,
                },
            },
        ],
        "nodes": [
            {
                "id": "housing-outer",
                "partId": "housing",
                "featureId": "housing/outer",
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {"sourceMesh": "source/housing.stl"},
                },
            },
            {
                "id": "housing-hole",
                "partId": "housing",
                "featureId": "housing/through-hole",
                "role": "cutter",
                "operation": "subtract",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {
                        "sourceMesh": {
                            "path": "source/cutter.stl",
                            "scale": 1.0,
                        }
                    },
                },
            },
            {
                "id": "button-body",
                "partId": "button",
                "featureId": "controls/button",
                "role": "separate",
                "operation": "none",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {"sourceMesh": "source/button.stl"},
                },
            },
            {
                "id": "hole-appearance",
                "partId": "housing",
                "featureId": "display/hole-appearance",
                "role": "display-only",
                "operation": "none",
                "physicalFeatureRef": "housing/through-hole",
                "recipe": {
                    "kind": "displayComponent",
                    "parameters": {
                        "sourceMesh": "source/display-component.ply",
                        "appearance": {
                            "baseColor": "#101418",
                            "metallic": 0.0,
                            "roughness": 0.2,
                        },
                    },
                },
            },
        ],
        "interfaces": [
            {
                "id": "button-housing-fixture",
                "kind": "glue-face",
                "male": {
                    "partId": "button",
                    "featureId": "controls/button",
                    "dimensionsMm": {"height": 3.0},
                },
                "female": {
                    "partId": "housing",
                    "featureId": "housing/outer",
                    "dimensionsMm": {"height": 3.0},
                },
            }
        ],
    }
    return scene, float(housing.volume)


def _color_region_fixture(root: Path) -> dict:
    source = root / "source"
    source.mkdir()
    body = trimesh.creation.box(extents=[20, 10, 6])
    left = trimesh.creation.box(extents=[10, 10, 6])
    left.apply_translation([-5, 0, 0])
    right = trimesh.creation.box(extents=[10, 10, 6])
    right.apply_translation([5, 0, 0])
    body.export(source / "body.stl")
    left.export(source / "left.stl")
    right.export(source / "right.stl")
    return {
        "schema": "evidence-semantic-scene/v1",
        "revision": "color-regions-001",
        "intentRef": _write_intent(
            root,
            dimensions_mm=(20.0, 10.0, 6.0),
            part="badge",
            feature_owners={"badge/body": "badge"},
            manufacturing={"mode": "single-part"},
            color_regions=[
                {
                    "name": "left",
                    "part": "badge",
                    "hex": "#112244",
                    "purpose": "structural navy half",
                    "boundary": "the complete left half-volume",
                    "evidence": "The left half is navy.",
                    "continuity": "continuous-core",
                    "material": {
                        "filament": "Bambu PLA Basic Navy",
                        "transmission": "opaque",
                    },
                },
                {
                    "name": "right",
                    "part": "badge",
                    "hex": "#F4F4F0",
                    "purpose": "contrasting white half",
                    "boundary": "the complete right half-volume",
                    "evidence": "The right half is warm white.",
                    "continuity": "continuous-core",
                    "material": {
                        "filament": "Bambu PLA Basic Jade White",
                        "transmission": "translucent",
                    },
                },
            ],
        ),
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        "materials": [
            {
                "id": "left",
                "color": "#112244",
            },
            {
                "id": "right",
                "color": "#F4F4F0",
            },
        ],
        "parts": [
            {
                "id": "badge",
                "representationMaster": "mesh",
                "materialId": "left",
                "colorRegions": [
                    {
                        "id": "left",
                        "materialId": "left",
                        "sourceMesh": "source/left.stl",
                    },
                    {
                        "id": "right",
                        "materialId": "right",
                        "sourceMesh": "source/right.stl",
                    },
                ],
            }
        ],
        "nodes": [
            {
                "id": "badge-body",
                "partId": "badge",
                "featureId": "badge/body",
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {"sourceMesh": "source/body.stl"},
                },
            }
        ],
        "interfaces": [],
    }


def _multipart_color_fixture(root: Path) -> dict:
    source = root / "source"
    source.mkdir()
    badge = trimesh.creation.box(extents=[20, 10, 6])
    badge_left = trimesh.creation.box(extents=[10, 10, 6])
    badge_left.apply_translation([-5, 0, 0])
    badge_right = trimesh.creation.box(extents=[10, 10, 6])
    badge_right.apply_translation([5, 0, 0])
    button = trimesh.creation.box(extents=[6, 10, 4])
    button.apply_translation([13, 0, 0])
    for name, mesh in (
        ("badge", badge),
        ("badge-left", badge_left),
        ("badge-right", badge_right),
        ("button", button),
    ):
        mesh.export(source / f"{name}.stl")
    button_step = source / "button.step"
    export_step(
        Pos(13, 0, 0)
        * Box(
            6,
            10,
            4,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        ),
        str(button_step),
    )

    color_regions = [
        {
            "name": "badge-left",
            "part": "badge",
            "hex": "#112244",
            "purpose": "structural navy half",
            "boundary": "the complete left half-volume of badge",
            "evidence": "The left badge half is navy.",
            "continuity": "continuous-core",
            "material": {
                "filament": "Bambu PLA Basic Navy",
                "transmission": "opaque",
            },
        },
        {
            "name": "badge-right",
            "part": "badge",
            "hex": "#F4F4F0",
            "purpose": "contrasting white half",
            "boundary": "the complete right half-volume of badge",
            "evidence": "The right badge half is warm white.",
            "continuity": "continuous-core",
            "material": {
                "filament": "Bambu PLA Basic Jade White",
                "transmission": "translucent",
            },
        },
        {
            "name": "button",
            "part": "button",
            "hex": "#E85B4A",
            "purpose": "whole-part control color",
            "boundary": "the complete separately printable button",
            "evidence": "The button is a single coral material.",
            "continuity": "separate-part",
            "material": {
                "filament": "Bambu PLA Basic Red",
                "transmission": "transparent",
            },
        },
    ]
    manufacturing = {
        "mode": "multipart",
        "parts": [
            {
                "name": "badge",
                "role": "two-color badge body",
                "acceptance": "The badge is exported as one physical part.",
            },
            {
                "name": "button",
                "role": "separate whole-color button",
                "acceptance": "The button remains a separate physical part.",
            },
        ],
        "interfaces": [
            {
                "id": "badge-button-interface",
                "between": ["badge", "button"],
                "connection": "glue-face",
                "assembly_axis": "+X",
                "engagement_mm": 1.0,
                "features": ["badge-body", "button-body"],
                "acceptance": "The button is installed on the badge face.",
            }
        ],
    }
    return {
        "schema": "evidence-semantic-scene/v1",
        "revision": "multipart-color-regions-001",
        "intentRef": _write_intent(
            root,
            dimensions_mm=(26.0, 10.0, 6.0),
            part="control-panel",
            feature_owners={"badge-body": "badge", "button-body": "button"},
            color_regions=color_regions,
            manufacturing=manufacturing,
        ),
        "units": "mm",
        "coordinateSystem": {"handedness": "right", "up": "Z"},
        # Filament and transmission intentionally live only in the immutable
        # intent. The compiler must propagate them into the material plan.
        "materials": [
            {"id": "badge-left", "color": "#112244"},
            {"id": "badge-right", "color": "#F4F4F0"},
            {"id": "button", "color": "#E85B4A"},
        ],
        "parts": [
            {
                "id": "badge",
                "representationMaster": "mesh",
                "materialId": "badge-left",
                "colorRegions": [
                    {
                        "id": "badge-left",
                        "materialId": "badge-left",
                        "sourceMesh": "source/badge-left.stl",
                    },
                    {
                        "id": "badge-right",
                        "materialId": "badge-right",
                        "sourceMesh": "source/badge-right.stl",
                    },
                ],
            },
            {
                "id": "button",
                "representationMaster": "brep",
                "materialId": "button",
                "artifacts": {
                    "masterStep": {
                        "path": "source/button.step",
                        "revision": "multipart-color-regions-001",
                        "scale": 1.0,
                        "sha256": sha256(button_step.read_bytes()).hexdigest(),
                    }
                },
            },
        ],
        "nodes": [
            {
                "id": "badge-body",
                "partId": "badge",
                "featureId": "badge-body",
                "role": "solid",
                "operation": "union",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {"sourceMesh": "source/badge.stl"},
                },
            },
            {
                "id": "button-body",
                "partId": "button",
                "featureId": "button-body",
                "role": "separate",
                "operation": "none",
                "recipe": {
                    "kind": "sourceMesh",
                    "parameters": {"sourceMesh": "source/button.stl"},
                },
            },
        ],
        "interfaces": [
            {
                "id": "badge-button-interface",
                "kind": "glue-face",
                "male": {
                    "partId": "badge",
                    "featureId": "badge-body",
                    "dimensionsMm": {"depth": 10.0},
                },
                "female": {
                    "partId": "button",
                    "featureId": "button-body",
                    "dimensionsMm": {"depth": 10.0},
                },
            }
        ],
    }


def _read_bound_intent(scene: dict, root: Path) -> tuple[Path, dict]:
    path = root / scene["intentRef"]["path"]
    return path, json.loads(path.read_text(encoding="utf-8"))


def _rewrite_bound_intent(scene: dict, root: Path, intent: dict) -> None:
    path = root / scene["intentRef"]["path"]
    path.write_text(json.dumps(intent), encoding="utf-8")
    scene["intentRef"]["sha256"] = sha256(path.read_bytes()).hexdigest()


def _write_scene(root: Path, scene: dict) -> Path:
    path = root / "scene.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    return path


class HybridCompileTests(unittest.TestCase):
    def test_compile_collects_all_independent_missed_cutters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            source = root / "source"

            for name, offset in (("miss-a", 40.0), ("miss-b", -40.0)):
                cutter = trimesh.creation.cylinder(radius=2, height=14, sections=32)
                cutter.apply_translation([offset, 0, 0])
                cutter.export(source / f"{name}.stl")

            existing_cutter = next(
                node for node in scene["nodes"] if node["id"] == "housing-hole"
            )
            existing_cutter["recipe"]["parameters"]["sourceMesh"]["path"] = (
                "source/miss-a.stl"
            )
            scene["nodes"].append(
                {
                    "id": "housing-second-hole",
                    "partId": "housing",
                    "featureId": "housing/second-hole",
                    "role": "cutter",
                    "operation": "subtract",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {
                            "sourceMesh": {
                                "path": "source/miss-b.stl",
                                "scale": 1.0,
                            }
                        },
                    },
                }
            )
            _, intent = _read_bound_intent(scene, root)
            intent["features"].append(
                {
                    "id": "housing/second-hole",
                    "part": "housing",
                    "kind": "detail",
                    "evidence": "fixture requires a second housing cutter",
                    "acceptance": "scene binds the second cutter to housing",
                }
            )
            intent["printability"]["critical_features"].append(
                "housing/second-hole"
            )
            _rewrite_bound_intent(scene, root, intent)

            with self.assertRaises(hybrid_compile.CompileDiagnosticsError) as raised:
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )

            issues = raised.exception.issues
            missed = [
                issue
                for issue in issues
                if issue["code"] == "BACKEND.CUTTER_MISSED_OWNER"
            ]
            self.assertEqual(
                {issue["featureId"] for issue in missed},
                {"housing/through-hole", "housing/second-hole"},
            )
            self.assertEqual(len(missed), 2)
            self.assertTrue(all(issue["partId"] == "housing" for issue in missed))

    def test_compile_collects_all_invalid_physical_source_meshes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            source = root / "source"

            for name, extents in (
                ("open-housing", [20, 20, 10]),
                ("open-button", [4, 4, 3]),
            ):
                mesh = trimesh.creation.box(extents=extents)
                mesh.update_faces(np.arange(len(mesh.faces) - 1))
                mesh.remove_unreferenced_vertices()
                mesh.export(source / f"{name}.stl")

            next(
                node for node in scene["nodes"] if node["id"] == "housing-outer"
            )["recipe"]["parameters"]["sourceMesh"] = "source/open-housing.stl"
            next(
                node for node in scene["nodes"] if node["id"] == "button-body"
            )["recipe"]["parameters"]["sourceMesh"] = "source/open-button.stl"

            with self.assertRaises(hybrid_compile.CompileDiagnosticsError) as raised:
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )

            invalid = [
                issue
                for issue in raised.exception.issues
                if issue["code"] == "BACKEND.PHYSICAL_NODE_INVALID"
            ]
            self.assertEqual(
                {issue["nodeId"] for issue in invalid},
                {"housing-outer", "button-body"},
            )
            self.assertEqual(len(invalid), 2)

    def test_hollow_mesh_is_one_material_body_and_keeps_its_cavity(self):
        outer = trimesh.creation.box(extents=[20, 20, 20])
        inner = trimesh.creation.box(extents=[16, 16, 16])
        hollow = trimesh.boolean.difference(
            [outer, inner],
            engine="manifold",
            check_volume=True,
        )
        self.assertIsInstance(hollow, trimesh.Trimesh)
        self.assertEqual(len(hollow.split(only_watertight=False)), 2)

        compiled = hybrid_compile._union([hollow], "shell")

        self.assertEqual(hybrid_compile.physical_body_count(compiled), 1)
        self.assertAlmostEqual(compiled.volume, hollow.volume, places=6)
        self.assertEqual(len(compiled.split(only_watertight=False)), 2)

    def test_volumetric_color_regions_round_trip_materials_and_build_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene = _color_region_fixture(root)
            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=root / "artifacts",
                source_scene=_write_scene(root, scene),
                consistency_samples=64,
            )
            self.assertTrue(report["pass"], report)
            report_path = str(
                root / "artifacts" / f"{report['part']}_report.json"
            )
            self.assertEqual(report["schema"], "evidence-a3d-build/v1")
            self.assertEqual(report["backend"], "hybrid-mesh")
            self.assertNotIn("source", report)
            self.assertEqual(report["inputs"]["scene"]["schema"], scene["schema"])
            self.assertEqual(
                report["artifactMatrix"]["parts"]["badge"]["step"],
                "not-applicable",
            )
            three_mf = report["artifacts"]["3mf"]
            self.assertTrue(three_mf["verified"])
            self.assertEqual(three_mf["buildItemCount"], 1)
            self.assertEqual(three_mf["objectCount"], 3)
            self.assertEqual(
                three_mf["readbackColors"], ["#112244FF", "#F4F4F0FF"]
            )
            self.assertEqual(
                [item["region"] for item in three_mf["assignments"]],
                ["left", "right"],
            )
            self.assertEqual(
                [item["materialId"] for item in three_mf["assignments"]],
                ["left", "right"],
            )
            self.assertEqual(
                report["materialPlan"]["sourceBindings"],
                [
                    {
                        "color": "#112244",
                        "filament": "Bambu PLA Basic Navy",
                        "materialId": "left",
                        "materialStatus": "declared",
                        "part": "badge",
                        "region": "left",
                        "scope": "volumetric-region",
                        "sourceId": "left",
                        "sourceKind": "intent-color-region",
                        "transmission": "opaque",
                    },
                    {
                        "color": "#F4F4F0",
                        "filament": "Bambu PLA Basic Jade White",
                        "materialId": "right",
                        "materialStatus": "declared",
                        "part": "badge",
                        "region": "right",
                        "scope": "volumetric-region",
                        "sourceId": "right",
                        "sourceKind": "intent-color-region",
                        "transmission": "translucent",
                    },
                ],
            )
            materials = {
                item["id"]: item for item in report["materialPlan"]["materials"]
            }
            self.assertEqual(materials["left"]["filament"], "Bambu PLA Basic Navy")
            self.assertEqual(materials["left"]["transmission"], "opaque")
            self.assertEqual(
                materials["right"]["filament"], "Bambu PLA Basic Jade White"
            )
            self.assertEqual(materials["right"]["transmission"], "translucent")
            self.assertTrue(report["materialPlan"]["requiresManualSlicerAssignment"])
            glb = report["artifacts"]["glb:display"]
            expected_region_nodes = [
                "badge--region--left",
                "badge--region--right",
            ]
            self.assertEqual(glb["physicalNodeNames"], expected_region_nodes)
            self.assertEqual(
                glb["physicalPartNodeNames"], {"badge": expected_region_nodes}
            )
            self.assertEqual(
                glb["regionNodeNames"], {"badge": expected_region_nodes}
            )
            self.assertEqual(
                glb["readbackBaseColors"],
                {
                    "badge--region--left": "#112244",
                    "badge--region--right": "#F4F4F0",
                },
            )
            display_scene = trimesh.load(
                glb["path"], force="scene", process=False
            )
            self.assertEqual(
                sorted(display_scene.graph.nodes_geometry), expected_region_nodes
            )
            bound = json.loads(
                Path(report["artifacts"]["boundScene"]["path"]).read_text()
            )
            self.assertEqual(
                bound["parts"][0]["artifacts"]["physicalGlb"]["nodeNames"],
                expected_region_nodes,
            )
            self.assertEqual(len(report["parts"]), 1)
            self.assertEqual(three_mf["buildItemCount"], 1)
            self.assertEqual(
                report["coordinateFrames"]["plate-print"]["profileId"],
                "bbl-a1-mini-0.4-t0-standard",
            )
            self.assertFalse(
                report["coordinateFrames"]["plate-print"]["layout"]["auto_scale"]
            )
            intent_path, intent = _read_bound_intent(scene, root)
            profile_path = Path(intent["printability"]["profile"]["path"])
            if not profile_path.is_absolute():
                profile_path = intent_path.parent / profile_path
            for command in (
                [
                    sys.executable,
                    str(SKILL / "color" / "assembly_check.py"),
                    report_path,
                    report["artifacts"]["3mf"]["path"],
                ],
                [
                    sys.executable,
                    str(SKILL / "color" / "qa_check.py"),
                    report["artifacts"]["3mf"]["path"],
                    "--intent",
                    str(intent_path),
                    "--profile",
                    str(profile_path),
                    "--report",
                    report_path,
                ],
            ):
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )

    def test_build_check_rejects_stale_or_malformed_shape_consistency(self):
        def move_manufacturing_bounds(payload: dict) -> None:
            bounds = payload["parts"]["badge"]["meshes"]["b"]["boundsMm"]
            bounds["max"][0] += 0.2
            bounds["size"][0] += 0.2

        def replace_schema(payload: dict) -> None:
            payload["schema"] = "evidence-shape-consistency-manifest/v0"

        def remove_part(payload: dict) -> None:
            payload["parts"].pop("badge")

        def claim_skipped_part(payload: dict) -> None:
            payload["skippedParts"] = ["badge"]

        mutations = {
            "manufacturing-bounds": move_manufacturing_bounds,
            "schema": replace_schema,
            "parts": remove_part,
            "skipped-parts": claim_skipped_part,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scene = _color_region_fixture(root)
                report = hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )
                report_path = root / "artifacts" / f"{report['part']}_report.json"
                self.assertTrue(build_check.audit(report_path)["pass"])

                shape_path = Path(report["artifacts"]["shapeConsistency"]["path"])
                shape_payload = json.loads(shape_path.read_text(encoding="utf-8"))
                mutate(shape_payload)
                shape_path.write_text(json.dumps(shape_payload), encoding="utf-8")
                report["artifacts"]["shapeConsistency"]["sha256"] = sha256(
                    shape_path.read_bytes()
                ).hexdigest()
                report_path.write_text(json.dumps(report), encoding="utf-8")

                audit = build_check.audit(report_path)
                self.assertFalse(audit["pass"], audit)

    def test_compile_rejects_semantic_envelope_outside_immutable_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            _, intent = _read_bound_intent(scene, root)
            intent["dimensions_mm"]["x"]["value"] = 28.0
            _rewrite_bound_intent(scene, root, intent)
            output = root / "artifacts"

            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "semantic envelope dimension x differs from intent",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=output,
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )
            self.assertFalse((output / "companion_report.json").exists())

    def test_multipart_allows_multiple_regions_on_one_mesh_part_and_whole_brep_part(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene = _multipart_color_fixture(root)
            intent_path, intent = _read_bound_intent(scene, root)
            self.assertEqual(
                intent_contract.validate_color_regions(
                    intent["color_regions"],
                    intent["manufacturing"],
                    intent["part"],
                ),
                [],
            )

            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=root / "artifacts",
                source_scene=_write_scene(root, scene),
                consistency_samples=64,
            )
            self.assertTrue(report["pass"], report)
            report_path = str(
                root / "artifacts" / f"{report['part']}_report.json"
            )
            self.assertEqual(report["artifacts"]["3mf"]["buildItemCount"], 2)
            self.assertEqual(report["artifacts"]["3mf"]["objectCount"], 4)
            self.assertEqual(
                [
                    (
                        item["part"],
                        item["region"],
                        item["scope"],
                        item["materialId"],
                        item["sourceKind"],
                        item["sourceId"],
                    )
                    for item in report["materialPlan"]["sourceBindings"]
                ],
                [
                    (
                        "badge",
                        "badge-left",
                        "volumetric-region",
                        "badge-left",
                        "intent-color-region",
                        "badge-left",
                    ),
                    (
                        "badge",
                        "badge-right",
                        "volumetric-region",
                        "badge-right",
                        "intent-color-region",
                        "badge-right",
                    ),
                    (
                        "button",
                        None,
                        "whole-part",
                        "button",
                        "intent-color-region",
                        "button",
                    ),
                ],
            )
            self.assertEqual(
                [
                    (item["part"], item["region"], item["scope"])
                    for item in report["artifacts"]["3mf"]["assignments"]
                ],
                [
                    ("badge", "badge-left", "volumetric-region"),
                    ("badge", "badge-right", "volumetric-region"),
                    ("button", None, "whole-part"),
                ],
            )
            material_by_id = {
                item["id"]: item for item in report["materialPlan"]["materials"]
            }
            self.assertEqual(
                {
                    key: (
                        value["filament"],
                        value["transmission"],
                        value["fieldStatus"],
                    )
                    for key, value in material_by_id.items()
                },
                {
                    "badge-left": (
                        "Bambu PLA Basic Navy",
                        "opaque",
                        {
                            "color": "declared",
                            "filament": "declared",
                            "transmission": "declared",
                        },
                    ),
                    "badge-right": (
                        "Bambu PLA Basic Jade White",
                        "translucent",
                        {
                            "color": "declared",
                            "filament": "declared",
                            "transmission": "declared",
                        },
                    ),
                    "button": (
                        "Bambu PLA Basic Red",
                        "transparent",
                        {
                            "color": "declared",
                            "filament": "declared",
                            "transmission": "declared",
                        },
                    ),
                },
            )
            for checker, artifact in (
                (SKILL / "assembly_check.py", report["artifacts"]["stl"]["path"]),
                (
                    SKILL / "color" / "assembly_check.py",
                    report["artifacts"]["3mf"]["path"],
                ),
            ):
                completed = subprocess.run(
                    [sys.executable, str(checker), report_path, artifact],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
            profile_path = Path(intent["printability"]["profile"]["path"])
            if not profile_path.is_absolute():
                profile_path = intent_path.parent / profile_path
            color_qa = subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "color" / "qa_check.py"),
                    report["artifacts"]["3mf"]["path"],
                    "--intent",
                    str(intent_path),
                    "--profile",
                    str(profile_path),
                    "--report",
                    report_path,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                color_qa.returncode,
                0,
                color_qa.stdout + color_qa.stderr,
            )
            self.assertEqual(
                report["artifactMatrix"]["parts"]["badge"]["step"],
                "not-applicable",
            )
            self.assertEqual(
                report["artifactMatrix"]["parts"]["button"]["step"],
                "required",
            )

    def test_scene_color_binding_rejects_id_owner_color_and_material_mismatches(self):
        mutations = {
            "region-id": lambda scene, intent: scene["parts"][0]["colorRegions"][0].update(
                {"id": "unknown-left"}
            ),
            "owner": lambda scene, intent: intent["color_regions"][0].update(
                {"part": "button"}
            ),
            "color": lambda scene, intent: scene["materials"][0].update(
                {"color": "#000000"}
            ),
            "filament": lambda scene, intent: scene["materials"][0].update(
                {"filament": "Different PLA"}
            ),
            "transmission": lambda scene, intent: scene["materials"][1].update(
                {"transmission": "opaque"}
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scene = _multipart_color_fixture(root)
                _, intent = _read_bound_intent(scene, root)
                mutate(scene, intent)
                _rewrite_bound_intent(scene, root, intent)
                with self.assertRaises(hybrid_compile.CompileError):
                    hybrid_compile.compile_scene(
                        scene,
                        base_dir=root,
                        output_dir=root / "artifacts",
                        source_scene=_write_scene(root, scene),
                        consistency_samples=64,
                    )

    def test_omitted_intent_material_fields_are_explicitly_proposed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene = _color_region_fixture(root)
            _, intent = _read_bound_intent(scene, root)
            intent["color_regions"][1].pop("material")
            _rewrite_bound_intent(scene, root, intent)

            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=root / "artifacts",
                source_scene=_write_scene(root, scene),
                consistency_samples=64,
            )
            material = next(
                item
                for item in report["materialPlan"]["materials"]
                if item["id"] == "right"
            )
            self.assertEqual(material["status"], "declared")
            self.assertIsNone(material["filament"])
            self.assertIsNone(material["transmission"])
            self.assertEqual(
                material["fieldStatus"],
                {
                    "color": "declared",
                    "filament": "proposed",
                    "transmission": "proposed",
                },
            )
            binding = next(
                item
                for item in report["materialPlan"]["sourceBindings"]
                if item["region"] == "right"
            )
            self.assertEqual(binding["materialStatus"], "declared")
            self.assertIsNone(binding["filament"])
            self.assertIsNone(binding["transmission"])

    def test_hybrid_rejects_internal_regions_on_a_brep_master(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene = _color_region_fixture(root)
            step_path = root / "source" / "badge.step"
            export_step(
                Box(
                    20,
                    10,
                    6,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                ),
                str(step_path),
            )
            scene["parts"][0]["representationMaster"] = "brep"
            scene["parts"][0]["artifacts"] = {
                "masterStep": {
                    "path": "source/badge.step",
                    "revision": scene["revision"],
                    "scale": 1.0,
                    "sha256": sha256(step_path.read_bytes()).hexdigest(),
                }
            }
            scene["parts"][0].pop("colorRegions")
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "BRep part badge declares multiple internal intent color regions",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )

    def test_color_regions_fail_closed_when_they_do_not_cover_the_part(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene = _color_region_fixture(root)
            incomplete_right = trimesh.creation.box(extents=[8, 10, 6])
            incomplete_right.apply_translation([6, 0, 0])
            incomplete_right.export(root / "source" / "right.stl")
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "must exactly partition",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )
    def test_clearance_volume_probe_rejects_a_skin_with_only_a_center_pinhole(self):
        cover = trimesh.creation.box(extents=[10.0, 10.0, 4.0])
        cover.apply_translation([0.0, 0.0, -2.0])
        short_cutter = trimesh.creation.cylinder(
            radius=1.7,
            height=3.4,
            sections=64,
        )
        short_cutter.apply_translation([0.0, 0.0, -1.7])
        pinhole = trimesh.creation.cylinder(
            radius=0.25,
            height=6.0,
            sections=64,
        )
        pinhole.apply_translation([0.0, 0.0, -2.0])
        pinholed_skin, _ = hybrid_compile._difference(
            cover,
            [short_cutter, pinhole],
            "pinholed-skin-cover",
        )
        self.assertEqual(
            self_tapping_geometry._axis_triangle_intersections(
                pinholed_skin,
                np.zeros(3),
                np.asarray([0.0, 0.0, 1.0]),
            ),
            [],
        )
        passage_probe = self_tapping_geometry._segment_cylinder(
            1.68,
            np.asarray([0.0, 0.0, -4.05]),
            np.asarray([0.0, 0.0, 0.05]),
        )
        self.assertGreater(
            hybrid_compile._intersection_volume(
                pinholed_skin,
                passage_probe,
                "pinholed clearance",
            ),
            0.1,
        )

        through_cutter = trimesh.creation.cylinder(
            radius=1.7,
            height=6.0,
            sections=64,
        )
        through_cutter.apply_translation([0.0, 0.0, -2.0])
        open_cover, _ = hybrid_compile._difference(
            cover,
            [through_cutter],
            "open-cover",
        )
        self.assertLessEqual(
            hybrid_compile._intersection_volume(
                open_cover,
                passage_probe,
                "open clearance",
            ),
            self_tapping_geometry._probe_tolerance(passage_probe),
        )

    def test_head_recess_witness_requires_the_full_minimum_floor(self):
        cover = trimesh.creation.box(extents=[10.0, 10.0, 2.4])
        cover.apply_translation([0.0, 0.0, -1.2])
        through = self_tapping_geometry._segment_cylinder(
            1.7,
            np.asarray([0.0, 0.0, -3.0]),
            np.asarray([0.0, 0.0, 0.5]),
        )
        shallow_floor_recess = self_tapping_geometry._segment_cylinder(
            3.0,
            np.asarray([0.0, 0.0, -3.0]),
            np.asarray([0.0, 0.0, -0.6]),
        )
        insufficient_floor, _ = hybrid_compile._difference(
            cover,
            [through, shallow_floor_recess],
            "insufficient-floor-cover",
        )
        floor_witness = self_tapping_geometry._segment_annulus(
            1.72,
            2.98,
            np.asarray([0.0, 0.0, -0.78]),
            np.asarray([0.0, 0.0, -0.02]),
        )
        self.assertGreater(
            hybrid_compile._missing_volume(
                floor_witness,
                insufficient_floor,
                "insufficient head recess floor",
            ),
            0.1,
        )

        exact_floor_recess = self_tapping_geometry._segment_cylinder(
            3.0,
            np.asarray([0.0, 0.0, -3.0]),
            np.asarray([0.0, 0.0, -0.8]),
        )
        exact_floor, _ = hybrid_compile._difference(
            cover,
            [through, exact_floor_recess],
            "exact-floor-cover",
        )
        self.assertLessEqual(
            hybrid_compile._missing_volume(
                floor_witness,
                exact_floor,
                "exact head recess floor",
            ),
            self_tapping_geometry._probe_tolerance(floor_witness),
        )

    def test_shared_self_tapping_recipe_places_actual_geometry_on_one_axis(self):
        scene = {
            "interfaces": [
                {
                    "id": "housing-base-service-joint",
                    "kind": "self-tapping-screw",
                    "fasteners": [
                        {
                            "id": "side-left",
                            "axis": {
                                "originMm": [5.0, 6.0, 7.0],
                                "direction": [0.0, 1.0, 0.0],
                            },
                            "screwFamily": "M3 plastic thread-forming/self-tapping",
                            "nominalDiameterMm": 3.0,
                            "cutterOvershootMm": 1.0,
                            "cover": {
                                "diameterMm": 3.4,
                                "thicknessMm": 2.4,
                            },
                            "receiver": {
                                "diameterMm": 2.6,
                                "bossOuterDiameterMm": 7.5,
                                "engagementMm": 6.0,
                                "tipClearanceMm": 0.8,
                                "closedEndMm": 1.2,
                                "minimumBossWallMm": 1.8,
                                "minimumRootEmbedMm": 0.4,
                            },
                        }
                    ],
                }
            ]
        }
        outputs, evidence = hybrid_compile._self_tapping_group(
            scene,
            "housing-base-service-joint",
            "side-left",
        )
        self.assertNotIn("minimum_cover_land_mm", evidence["cover"])
        origin = np.asarray([5.0, 6.0, 7.0])
        expected_diameters = {
            "clearance-cutter": 3.4,
            "pilot-cutter": 2.6,
            "receiver-boss": 7.5,
        }
        for name, mesh in outputs.items():
            relative = np.asarray(mesh.vertices) - origin
            # The declared +Y axis leaves X/Z as its radial plane.
            radial = relative[:, [0, 2]]
            radial_min = radial.min(axis=0)
            radial_max = radial.max(axis=0)
            np.testing.assert_allclose(
                (radial_min + radial_max) / 2,
                [0.0, 0.0],
                atol=1e-6,
            )
            np.testing.assert_allclose(
                radial_max - radial_min,
                [expected_diameters[name], expected_diameters[name]],
                atol=0.03,
            )
        self.assertEqual(evidence["axis"]["origin_mm"], [5.0, 6.0, 7.0])
        self.assertEqual(evidence["axis"]["direction"], [0.0, 1.0, 0.0])
        self.assertEqual(
            set(evidence["outputs"]),
            {"clearance-cutter", "pilot-cutter", "receiver-boss"},
        )

    def test_compiler_builds_fused_blind_aligned_two_screw_joint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            base = trimesh.creation.box(extents=[20.0, 20.0, 2.4])
            base.apply_translation([0.0, 0.0, -1.2])
            housing = trimesh.creation.box(extents=[20.0, 20.0, 1.0])
            housing.apply_translation([0.0, 0.0, 0.5])
            locator = trimesh.creation.cylinder(radius=2.0, height=1.0, sections=48)
            locator.apply_translation([0.0, 0.0, -0.5])
            socket = trimesh.creation.cylinder(radius=2.2, height=3.0, sections=48)
            socket.apply_translation([0.0, 0.0, 0.5])
            for name, mesh in (
                ("base", base),
                ("housing", housing),
                ("locator", locator),
                ("socket", socket),
            ):
                mesh.export(source / f"{name}.stl")

            nodes = [
                {
                    "id": "base-shell",
                    "partId": "base",
                    "featureId": "base-shell",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/base.stl"},
                    },
                },
                {
                    "id": "housing-shell",
                    "partId": "housing",
                    "featureId": "housing-shell",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/housing.stl"},
                    },
                },
                {
                    "id": "base-locator",
                    "partId": "base",
                    "featureId": "base-locator",
                    "role": "solid",
                    "operation": "union",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/locator.stl"},
                    },
                },
                {
                    "id": "housing-socket",
                    "partId": "housing",
                    "featureId": "housing-socket",
                    "role": "cutter",
                    "operation": "subtract",
                    "recipe": {
                        "kind": "sourceMesh",
                        "parameters": {"sourceMesh": "source/socket.stl"},
                    },
                },
            ]
            fasteners = []
            for side, x in (("left", -6.0), ("right", 6.0)):
                fastener_id = f"side-{side}"
                outputs = {
                    "clearance-cutter": ("base", "cutter"),
                    "pilot-cutter": ("housing", "cutter"),
                    "receiver-boss": ("housing", "solid"),
                }
                feature_ids = {}
                for output_name, (part_id, role) in outputs.items():
                    feature_id = f"{fastener_id}-{output_name}"
                    feature_ids[output_name] = feature_id
                    nodes.append({
                        "id": feature_id,
                        "partId": part_id,
                        "featureId": feature_id,
                        "role": role,
                        "operation": "subtract" if role == "cutter" else "union",
                        "recipe": {
                            "kind": "selfTappingScrewPair",
                            "parameters": {
                                "interfaceId": "housing-base-service-joint",
                                "fastenerId": fastener_id,
                                "output": output_name,
                            },
                        },
                    })
                fasteners.append({
                    "id": fastener_id,
                    "axis": {"originMm": [x, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]},
                    "screwFamily": "M3 plastic thread-forming/self-tapping",
                    "nominalDiameterMm": 3.0,
                    "cutterOvershootMm": 1.0,
                    "cover": {
                        "partId": "base",
                        "featureId": feature_ids["clearance-cutter"],
                        "diameterMm": 3.4,
                        "thicknessMm": 2.4,
                    },
                    "receiver": {
                        "partId": "housing",
                        "featureId": feature_ids["pilot-cutter"],
                        "bossFeatureId": feature_ids["receiver-boss"],
                        "diameterMm": 2.6,
                        "bossOuterDiameterMm": 7.5,
                        "engagementMm": 6.0,
                        "tipClearanceMm": 0.8,
                        "closedEndMm": 1.2,
                        "minimumBossWallMm": 1.8,
                        "minimumRootEmbedMm": 0.4,
                    },
                })
            intent_fasteners = [
                {
                    "id": item["id"],
                    "clearance_feature": item["cover"]["featureId"],
                    "pilot_feature": item["receiver"]["featureId"],
                    "boss_feature": item["receiver"]["bossFeatureId"],
                }
                for item in fasteners
            ]
            interface_features = ["base-locator", "housing-socket"] + [
                feature
                for item in intent_fasteners
                for feature in (
                    item["clearance_feature"],
                    item["pilot_feature"],
                    item["boss_feature"],
                )
            ]
            manufacturing = {
                "mode": "multipart",
                "parts": [
                    {"name": "base", "role": "service base", "acceptance": "one base"},
                    {"name": "housing", "role": "housing", "acceptance": "one housing"},
                ],
                "interfaces": [
                    {
                        "id": "housing-base-service-joint",
                        "between": ["base", "housing"],
                        "connection": "self-tapping-screw",
                        "assembly_axis": "+Z",
                        "engagement_mm": 6.0,
                        "features": interface_features,
                        "acceptance": "two aligned screw axes retain the base",
                        "fastening": {
                            "screw_family": "M3 plastic thread-forming/self-tapping",
                            "nominal_diameter_mm": 3.0,
                            "pilot_diameter_mm": 2.6,
                            "clearance_diameter_mm": 3.4,
                            "boss_outer_diameter_mm": 7.5,
                            "closed_end_mm": 1.2,
                            "cutter_overshoot_mm": 1.0,
                            "cover_thickness_mm": 2.4,
                            "pilot_tip_clearance_mm": 0.8,
                            "minimum_boss_wall_mm": 1.8,
                            "minimum_root_embed_mm": 0.4,
                            "locator_pairs": [
                                {
                                    "id": "housing-base-locator",
                                    "male_feature": "base-locator",
                                    "female_feature": "housing-socket",
                                }
                            ],
                            "fasteners": intent_fasteners,
                        },
                    }
                ],
            }
            feature_owners = {
                node["featureId"]: node["partId"] for node in nodes
            }
            scene = {
                "schema": "evidence-semantic-scene/v1",
                "revision": "fastener-compile-001",
                "intentRef": _write_intent(
                    root,
                    dimensions_mm=(20.0, 20.0, 10.400000095),
                    feature_owners=feature_owners,
                    manufacturing=manufacturing,
                ),
                "units": "mm",
                "coordinateSystem": {"handedness": "right", "up": "Z"},
                "parts": [
                    {"id": "housing", "representationMaster": "mesh"},
                    {"id": "base", "representationMaster": "mesh"},
                ],
                "nodes": nodes,
                "interfaces": [
                    {
                        "id": "housing-base-service-joint",
                        "kind": "self-tapping-screw",
                        "locatorInterfaceIds": ["housing-base-locator"],
                        "fasteners": fasteners,
                    },
                    {
                        "id": "housing-base-locator",
                        "kind": "collar-socket",
                        "male": {
                            "partId": "base",
                            "featureId": "base-locator",
                            "dimensionsMm": {"diameter": 4.0},
                        },
                        "female": {
                            "partId": "housing",
                            "featureId": "housing-socket",
                            "dimensionsMm": {"diameter": 4.4},
                            "derivedDimensionsMm": {
                                "diameter": {"from": "male.diameter", "offsetMm": 0.4}
                            },
                        },
                    },
                ],
            }
            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=root / "artifacts",
                source_scene=_write_scene(root, scene),
                consistency_samples=64,
            )
            self.assertTrue(report["pass"], report)
            self.assertEqual(
                set(report["fastenerGroups"]),
                {
                    "housing-base-service-joint/side-left",
                    "housing-base-service-joint/side-right",
                },
            )
            self.assertTrue(
                all(
                    item["pass"]
                    for item in report["fastenerGeometryChecks"].values()
                )
            )
            self.assertTrue(
                all(
                    item["checks"][
                        "clearance_cutter_volume_matches_recipe_controls"
                    ]
                    and item["checks"][
                        "clearance_cutter_bounds_match_recipe_controls"
                    ]
                    and item["checks"][
                        "pilot_cutter_volume_matches_recipe_controls"
                    ]
                    and item["checks"][
                        "pilot_cutter_bounds_match_recipe_controls"
                    ]
                    and item["checks"][
                        "receiver_boss_volume_matches_recipe_controls"
                    ]
                    and item["checks"][
                        "receiver_boss_bounds_match_recipe_controls"
                    ]
                    for item in report["fastenerGeometryChecks"].values()
                )
            )
            output = root / "artifacts"
            compiled_base = _canonical_part(report, output, "base")
            compiled_housing = _canonical_part(report, output, "housing")
            self.assertEqual(len(compiled_base.split()), 1)
            self.assertEqual(len(compiled_housing.split()), 1)
            for x in (-6.0, 6.0):
                self.assertFalse(bool(compiled_base.contains([[x, 0.0, -1.2]])[0]))
                self.assertFalse(bool(compiled_housing.contains([[x, 0.0, 3.0]])[0]))
                self.assertTrue(bool(compiled_housing.contains([[x, 0.0, 7.4]])[0]))
                self.assertTrue(bool(compiled_housing.contains([[x + 2.0, 0.0, 3.0]])[0]))

    def test_compiles_true_hole_excludes_display_and_binds_consistent_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, housing_volume = _fixture(root)
            intent_path = root / scene["intentRef"]["path"]
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            intent["printability"].pop("print_package_mode")
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            scene["intentRef"]["sha256"] = sha256(
                intent_path.read_bytes()
            ).hexdigest()
            scene_path = root / "scene.json"
            scene_path.write_text(json.dumps(scene), encoding="utf-8")
            output = root / "artifacts"

            report = hybrid_compile.compile_scene(
                scene,
                base_dir=root,
                output_dir=output,
                source_scene=scene_path,
                consistency_samples=256,
            )
            self.assertTrue(report["pass"], report)
            manifest_audit = build_check.audit(
                output / "companion_report.json"
            )
            self.assertTrue(manifest_audit["pass"], manifest_audit)
            self.assertEqual(report["scale"], 1.0)
            self.assertFalse(report["autoScale"])
            self.assertEqual(report["part"], "companion")
            self.assertEqual(
                {item["id"]: item["status"] for item in report["materialPlan"]["materials"]},
                {"proposed-button": "proposed", "proposed-housing": "proposed"},
            )
            self.assertEqual(
                {
                    (
                        item["part"],
                        item["materialId"],
                        item["sourceKind"],
                        item["sourceId"],
                    )
                    for item in report["materialPlan"]["sourceBindings"]
                },
                {
                    (
                        "button",
                        "proposed-button",
                        "scene-part-appearance",
                        "button",
                    ),
                    (
                        "housing",
                        "proposed-housing",
                        "scene-part-appearance",
                        "housing",
                    ),
                },
            )
            self.assertEqual(report["materialPlan"]["packageMode"], "separate_parts")
            self.assertEqual(
                set(report["artifacts"]),
                {
                    "stl",
                    "stl:housing",
                    "stl:button",
                    "step:button",
                    "3mf",
                    "glb:display",
                    "materialPlan",
                    "shapeConsistency",
                    "stepConsistency",
                    "boundScene",
                },
            )
            for key in ("stl", "3mf", "glb:display"):
                artifact = report["artifacts"][key]
                self.assertTrue(Path(artifact["path"]).is_file())
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                report["inputs"]["scene"]["path"], str(scene_path.resolve())
            )
            self.assertEqual(
                report["inputs"]["scene"]["sha256"],
                sha256(scene_path.read_bytes()).hexdigest(),
            )
            self.assertTrue((output / "companion_report.json").is_file())
            self.assertEqual(
                report["excludedDisplayNodes"],
                [
                    {
                        "nodeId": "hole-appearance",
                        "physicalFeatureRef": "housing/through-hole",
                        "reason": "display-only nodes never alter physical contours",
                    }
                ],
            )
            self.assertEqual(
                report["includedDisplayNodes"],
                [
                    {
                        "nodeId": "hole-appearance",
                        "physicalFeatureRef": "housing/through-hole",
                        "includedInDisplay": True,
                        "excludedFromManufacturing": True,
                        "reason": "display-only nodes never alter physical contours",
                    }
                ],
            )
            self.assertEqual(
                report["excludedFromManufacturingNodes"],
                report["includedDisplayNodes"],
            )

            housing = _canonical_part(report, output, "housing")
            self.assertTrue(housing.is_watertight)
            self.assertTrue(housing.is_volume)
            self.assertLess(float(housing.volume), housing_volume)
            self.assertAlmostEqual(
                housing_volume - float(housing.volume),
                np.pi * 3 * 3 * 10,
                delta=1.0,
            )
            self.assertFalse(bool(housing.contains([[0, 0, 0]])[0]))
            self.assertTrue(bool(housing.contains([[6, 0, 0]])[0]))
            locations, _, _ = housing.ray.intersects_location(
                [[0, 0, -20]], [[0, 0, 1]], multiple_hits=True
            )
            self.assertEqual(len(locations), 0, "centerline must pass through the hole")
            self.assertTrue(np.allclose(housing.extents, [20, 20, 10], atol=1e-5))

            button = _canonical_part(report, output, "button")
            self.assertTrue(button.is_volume)
            combined = trimesh.load(output / "companion.stl", force="mesh")
            self.assertEqual(len(combined.split()), 2)
            self.assertTrue((output / "companion-display.glb").is_file())
            display_scene = trimesh.load(
                output / "companion-display.glb", force="scene", process=False
            )
            self.assertIsInstance(display_scene, trimesh.Scene)
            self.assertEqual(
                sorted(display_scene.graph.nodes_geometry),
                ["button", "hole-appearance", "housing"],
            )
            self.assertEqual(
                report["artifacts"]["glb:display"]["physicalNodeNames"],
                ["button", "housing"],
            )
            self.assertEqual(
                report["artifacts"]["glb:display"]["displayOnlyNodeNames"],
                ["hole-appearance"],
            )
            self.assertFalse(any(output.glob("*.step")))

            three_mf = report["artifacts"]["3mf"]
            self.assertTrue(three_mf["verified"])
            self.assertEqual(
                three_mf["colorScope"], "part-or-volumetric-region"
            )
            self.assertEqual(
                three_mf["regionColoring"], "volumetric-components"
            )
            self.assertEqual(three_mf["objectCount"], 2)
            self.assertEqual(
                [(item["name"], item["color"]) for item in three_mf["objects"]],
                [("housing", "#EEE8DC"), ("button", "#F06450")],
            )
            with ZipFile(output / "companion.3mf") as archive:
                self.assertIn("3D/3dmodel.model", archive.namelist())

            bound = json.loads(
                (output / "companion_scene_artifacts.json").read_text()
            )
            self.assertEqual(scene_contract.validate(bound, output), [])
            self.assertEqual(bound["revision"], "hybrid-rev-001")
            for part in bound["parts"]:
                artifacts = part["artifacts"]
                self.assertEqual(artifacts["physicalGlb"]["scale"], 1.0)
                self.assertEqual(artifacts["manufacturingStl"]["scale"], 1.0)
                self.assertEqual(
                    artifacts["physicalGlb"]["revision"], "hybrid-rev-001"
                )
            consistency = shape_consistency.compare_manifest(
                bound,
                base_dir=output,
                sample_count=256,
                surface_p99_tolerance_mm=0.001,
                surface_max_tolerance_mm=0.001,
            )
            self.assertTrue(consistency["pass"], consistency)
            persisted = json.loads(
                (output / "companion_shape-consistency.json").read_text()
            )
            self.assertTrue(persisted["pass"])
            step_consistency_path = output / "companion_step-consistency.json"
            step_consistency = json.loads(step_consistency_path.read_text())
            self.assertTrue(step_consistency["pass"], step_consistency)
            self.assertEqual(set(step_consistency["parts"]), {"button"})
            self.assertTrue(step_consistency["parts"]["button"]["pass"])
            self.assertEqual(
                step_consistency["parts"]["button"]["step"]["validator"],
                "build123d-occt",
            )
            self.assertEqual(
                report["artifacts"]["stepConsistency"]["sha256"],
                sha256(step_consistency_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                report["backendData"]["stepConsistency"], step_consistency
            )
            self.assertTrue(
                any("OCCT-imported STEP master" in item for item in report["warnings"])
            )
            self.assertEqual(
                report["artifactMatrix"]["parts"]["button"]["step"],
                "required",
            )
            self.assertEqual(
                report["artifactMatrix"]["parts"]["housing"]["step"],
                "not-applicable",
            )

    def test_rejects_header_only_master_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            step_path = root / "source" / "button.step"
            step_path.write_text(
                "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\n"
                "END-ISO-10303-21;\n",
                encoding="ascii",
            )
            scene["parts"][1]["artifacts"]["masterStep"]["sha256"] = sha256(
                step_path.read_bytes()
            ).hexdigest()
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "failed OCCT import|exactly one solid",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )

    def test_rejects_master_step_that_does_not_match_compiled_semantic_mesh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            step_path = root / "source" / "button.step"
            export_step(
                Pos(15, 0, 0)
                * Box(
                    6,
                    6,
                    6,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                ),
                str(step_path),
            )
            scene["parts"][1]["artifacts"]["masterStep"]["sha256"] = sha256(
                step_path.read_bytes()
            ).hexdigest()
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "does not match its final compiled semantic mesh",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )

    def test_rejects_removed_display_proxy_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            proxy = trimesh.creation.box(extents=[8, 6, 0.2])
            proxy.export(root / "source" / "display-proxy.stl")
            display = scene["nodes"][3]
            display["recipe"] = {
                "kind": "displayProxy",
                "parameters": {
                    "sourceMesh": "source/display-proxy.stl",
                    "material": "dark-aperture",
                },
            }
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "recipe.kind must be displayComponent",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )

    def test_rejects_a_source_scene_that_does_not_match_the_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            stale_scene = json.loads(json.dumps(scene))
            stale_scene["revision"] = "stale-revision"
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "source scene file does not match",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, stale_scene),
                    consistency_samples=64,
                )

    def test_rejects_source_mesh_scale_instead_of_fitting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene, _ = _fixture(root)
            scene["nodes"][0]["recipe"]["parameters"]["sourceMesh"] = {
                "path": "source/housing.stl",
                "scale": 0.5,
            }
            with self.assertRaisesRegex(
                hybrid_compile.CompileError,
                "sourceMesh.scale must be 1",
            ):
                hybrid_compile.compile_scene(
                    scene,
                    base_dir=root,
                    output_dir=root / "artifacts",
                    source_scene=_write_scene(root, scene),
                    consistency_samples=64,
                )


if __name__ == "__main__":
    unittest.main()
