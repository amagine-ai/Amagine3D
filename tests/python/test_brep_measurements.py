"""Exact final-solid sections, frames, holes and STEP/CLI input binding."""
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from build123d import (
    Box, Compound, Cylinder, Face, Location, Plane, Pos, Rectangle, Spline, Wire,
    export_step, extrude,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "text-a3d"))
import brep_measurements as measurements


class BrepMeasurementsTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "workspace" / "skill-validation"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def assertVectorClose(self, actual, expected, places=6):
        self.assertEqual(len(actual), len(expected))
        for value, target in zip(actual, expected):
            self.assertAlmostEqual(value, target, places=places)

    def test_box_with_two_holes_keeps_outer_center_distinct_from_material_centroid(self):
        part = Pos(0, 0, 5) * Box(20, 12, 10)
        part -= Pos(-5, 0, 5) * Cylinder(1, 12)
        part -= Pos(5, 0, 5) * Cylinder(2, 12)
        result = measurements.measure_section(part, Plane.XY.offset(5), label="opening")
        self.assertEqual((result["status"], result["material_island_count"], result["hole_count"]),
                         ("material", 1, 2))
        self.assertEqual(result["label"], "opening")
        self.assertEqual(result["units"], {"length": "mm", "area": "mm2"})
        self.assertVectorClose(result["plane"]["origin_mm"], [0, 0, 5])
        envelope = result["outer_envelope"]
        self.assertAlmostEqual(envelope["width_u_mm"], 20)
        self.assertAlmostEqual(envelope["depth_v_mm"], 12)
        self.assertVectorClose(envelope["center_world_mm"], [0, 0, 5])
        self.assertAlmostEqual(result["sum_material_area_mm2"], 240 - 5 * math.pi)
        island = result["material_islands"][0]
        self.assertLess(island["material_centroid_world_mm"][0], 0)
        self.assertAlmostEqual(island["outer"]["enclosed_area_mm2"], 240)
        self.assertVectorClose(sorted(hole["enclosed_area_mm2"] for hole in island["holes"]),
                               [math.pi, 4 * math.pi])

    def test_slot_material_islands_and_overlapping_solid_areas_are_not_unioned(self):
        split = Box(20, 12, 10) - Box(2, 14, 12)
        result = measurements.measure_section(split, Plane.XY)
        self.assertEqual(result["material_island_count"], 2)
        self.assertEqual(result["hole_count"], 0)
        self.assertAlmostEqual(result["sum_material_area_mm2"], 18 * 12)
        self.assertAlmostEqual(result["outer_envelope"]["width_u_mm"], 20)
        overlapping = Compound(children=[Box(10, 10, 10), Pos(5, 0, 0) * Box(10, 10, 10)])
        result = measurements.measure_section(overlapping, Plane.XY)
        self.assertEqual({i["solid_index"] for i in result["material_islands"]}, {0, 1})
        self.assertAlmostEqual(result["sum_material_area_mm2"], 200)
        self.assertAlmostEqual(result["outer_envelope"]["width_u_mm"], 15)

    def test_transformed_plane_preserves_local_measurements_and_world_center(self):
        base = Box(20, 12, 10) - Cylinder(2, 12)
        location = Location((31, -17, 9), (20, 30, 40))
        moved = location * base
        before = (moved.volume, list(moved.center()))
        plane = Plane.XY.moved(location)
        result = measurements.measure_section(moved, plane)
        self.assertAlmostEqual(result["outer_envelope"]["width_u_mm"], 20)
        self.assertAlmostEqual(result["outer_envelope"]["depth_v_mm"], 12)
        self.assertVectorClose(result["outer_envelope"]["center_world_mm"], [31, -17, 9])
        self.assertVectorClose(result["plane"]["normal_dir"], list(plane.z_dir))
        self.assertAlmostEqual(result["sum_material_area_mm2"], 240 - 4 * math.pi)
        self.assertAlmostEqual(moved.volume, before[0])
        self.assertVectorClose(list(moved.center()), before[1])

    def test_parallel_sections_show_actual_width_and_center_displacement(self):
        # Two connected levels intentionally have different outer dimensions and centers.
        part = Pos(0, 0, 2) * Box(50, 40, 4) + Pos(0, 5, 9) * Box(54, 30, 10)
        foot = measurements.measure_section(part, Plane.XY.offset(0))
        upper = measurements.measure_section(part, Plane.XY.offset(14))
        self.assertAlmostEqual(foot["outer_envelope"]["width_u_mm"], 50)
        self.assertAlmostEqual(upper["outer_envelope"]["width_u_mm"], 54)
        delta = [b - a for a, b in zip(foot["outer_envelope"]["center_world_mm"],
                                      upper["outer_envelope"]["center_world_mm"])]
        self.assertVectorClose(delta, [0, 5, 14])

    def test_equivalent_plane_with_distant_in_plane_origin_still_intersects(self):
        part = Pos(0, 0, 5) * Box(20, 12, 10)
        plane = Plane(origin=(10000, -20000, 5), x_dir=(1, 0, 0), z_dir=(0, 0, 1))
        result = measurements.measure_section(part, plane)
        self.assertEqual(result["status"], "material")
        self.assertVectorClose(result["outer_envelope"]["center_world_mm"], [0, 0, 5])
        self.assertVectorClose(result["outer_envelope"]["center_uv_mm"], [-10000, 20000])
        self.assertAlmostEqual(result["outer_envelope"]["width_u_mm"], 20)
        self.assertAlmostEqual(result["sum_material_area_mm2"], 240)

    def test_end_face_and_outside_sections_do_not_silently_shift_the_plane(self):
        part = Pos(0, 0, 5) * Box(20, 12, 10)
        for z in (0, 10):
            result = measurements.measure_section(part, Plane.XY.offset(z))
            self.assertEqual(result["status"], "material")
            self.assertAlmostEqual(result["sum_material_area_mm2"], 240)
            self.assertVectorClose(result["outer_envelope"]["center_world_mm"], [0, 0, z])
        for z in (-0.001, 10.001):
            result = measurements.measure_section(part, Plane.XY.offset(z))
            self.assertEqual(result["status"], "empty")
            self.assertEqual(result["sum_material_area_mm2"], 0)
            self.assertEqual(result["material_islands"], [])
            self.assertIsNone(result["outer_envelope"])
            self.assertEqual(result["plane"]["origin_mm"][2], z)

    def test_invalid_non_solid_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "solid"):
            measurements.measure_section(Rectangle(10, 10), Plane.XY)
        with self.assertRaisesRegex(ValueError, "plane"):
            measurements.measure_section(Box(10, 10, 10), None)

    def test_step_readback_measures_finished_fillet_and_binds_input_bytes(self):
        box = Pos(0, 0, 5) * Box(54, 40, 10)
        top_edges = [edge for edge in box.edges() if abs(edge.center().Z - 10) < 1e-8]
        finished = box.fillet(1, top_edges)
        path = self.work / "finished.step"
        export_step(finished, path)
        result = measurements.measure_step(path, {"foot": Plane.XY, "mouth": Plane.XY.offset(10)})
        self.assertEqual(result["input"]["sha256"], sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result["coordinate_frame"], "input-part")
        self.assertEqual(result["solid_count"], 1)
        foot, mouth = result["sections"]
        self.assertAlmostEqual(foot["outer_envelope"]["width_u_mm"], 54)
        self.assertAlmostEqual(mouth["outer_envelope"]["width_u_mm"], 52)
        self.assertAlmostEqual(result["world_bounds_mm"]["size"][0], 54)

    def test_step_changed_during_measurement_discards_result(self):
        path = self.work / "changing.step"
        export_step(Box(10, 10, 10), path)
        real_import = measurements.import_step

        def changed(file):
            part = real_import(file)
            file.write_bytes(file.read_bytes() + b"\n")
            return part

        with patch.object(measurements, "import_step", changed):
            with self.assertRaisesRegex(ValueError, "changed during measurement"):
                measurements.measure_step(path)

    def test_trimmed_spline_section_area_uses_adaptive_integration(self):
        angles = [i * 2 * math.pi / 24 for i in range(24)]
        points = [((20 + 4 * math.cos(5 * t)) * math.cos(t),
                   (14 + 2 * math.sin(3 * t)) * math.sin(t), 0) for t in angles]
        edge = Spline(*points, periodic=True)
        part = extrude(Face(Wire([edge])), amount=10)
        result = measurements.measure_section(part, Plane.XY.offset(5))
        # Independent dense polygon integral: default non-adaptive surface
        # quadrature is off by ~0.64 mm² on this closed spline.
        xy = [tuple(edge.position_at(i / 4096))[:2] for i in range(4096)]
        polygon_area = abs(sum(x1 * y2 - y1 * x2 for (x1, y1), (x2, y2)
                               in zip(xy, xy[1:] + xy[:1]))) / 2
        self.assertAlmostEqual(result["sum_material_area_mm2"], polygon_area, delta=0.001)
        self.assertAlmostEqual(result["material_islands"][0]["outer"]["enclosed_area_mm2"],
                               result["sum_material_area_mm2"], places=6)

    def test_cli_explicit_axes_output_and_close_section_labels(self):
        path = self.work / "box.step"
        export_step(Pos(0, 0, 5) * Box(20, 12, 10), path)
        out = self.work / "measurements.json"
        with redirect_stdout(io.StringIO()) as output:
            result = measurements.main([str(path), "--workspace", str(self.work),
                                        "--section-x", "0", "--section-y", "0", "--section-z", "5",
                                        "--section-z", "5.000001", "--out", str(out)])
        self.assertEqual(result, 0)
        report = json.loads(out.read_text())
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["input"]["sha256"], sha256(path.read_bytes()).hexdigest())
        self.assertEqual(summary["fullResult"], {"path": str(out), "sha256": sha256(out.read_bytes()).hexdigest()})
        self.assertEqual(summary["sections"][0]["outer_envelope"], report["sections"][0]["outer_envelope"])
        self.assertEqual(len(report["sections"]), 4)
        x, y, z, nearby = report["sections"]
        self.assertVectorClose([x["outer_envelope"]["width_u_mm"], x["outer_envelope"]["depth_v_mm"]], [12, 10])
        self.assertVectorClose([y["outer_envelope"]["width_u_mm"], y["outer_envelope"]["depth_v_mm"]], [20, 10])
        self.assertVectorClose(y["plane"]["v_dir"], [0, 0, 1])
        self.assertVectorClose([z["outer_envelope"]["width_u_mm"], z["outer_envelope"]["depth_v_mm"]], [20, 12])
        self.assertNotEqual(z["label"], nearby["label"])

    def test_cli_rejects_escaped_paths_symlinks_overwrite_and_nonfinite_cuts(self):
        path = self.work / "box.step"
        export_step(Box(10, 10, 10), path)
        original = path.read_bytes()
        inner = self.work / "inner"
        inner.mkdir()
        (inner / "escape.step").symlink_to(path)
        hardlink = self.work / "same-file.step"
        hardlink.hardlink_to(path)
        attempts = [
            ["../box.step", "--workspace", str(inner)],
            ["escape.step", "--workspace", str(inner)],
            [str(path), "--workspace", str(self.work), "--out", "../escape.json"],
            [str(path), "--workspace", str(self.work), "--out", str(path)],
            [str(path), "--workspace", str(self.work), "--out", str(hardlink)],
            [str(path), "--workspace", str(self.work), "--section-z", "nan"],
        ]
        for args in attempts:
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    measurements.main(args)
                self.assertEqual(error.exception.code, 2)
        self.assertEqual(path.read_bytes(), original)
        with redirect_stdout(io.StringIO()) as output:
            measurements.main([str(path), "--workspace", str(self.work)])
        self.assertEqual(json.loads(output.getvalue())["sections"], [])
        self.assertTrue((self.work / "box_measurements.json").is_file())

    def test_cli_summary_caps_sections_but_full_report_retains_every_plane(self):
        path = self.work / "box.step"
        export_step(Box(20, 12, 30), path)
        args = [str(path), "--workspace", str(self.work)]
        for z in range(10):
            args.extend(["--section-z", str(z)])
        with redirect_stdout(io.StringIO()) as output:
            measurements.main(args)
        summary = json.loads(output.getvalue())
        self.assertEqual((summary["section_count"], summary["returned_section_count"], summary["omitted_section_count"]),
                         (10, 8, 2))
        self.assertEqual(len(summary["sections"]), 8)
        self.assertNotIn("material_islands", summary["sections"][0])
        full = json.loads(Path(summary["fullResult"]["path"]).read_text())
        self.assertEqual(len(full["sections"]), 10)
        self.assertEqual(full["sections"][-1]["plane"]["origin_mm"][2], 9)

    @unittest.skipUnless(sys.platform == "darwin", "requires the macOS sandbox")
    def test_cli_measures_when_sandbox_denies_sessions_parent_metadata(self):
        sessions = self.work / "sessions"
        current = sessions / "current"
        current.mkdir(parents=True)
        path = current / "box.step"
        export_step(Box(20, 12, 10), path)
        (current / "alias.step").symlink_to("box.step")
        profile = self.work / "sandbox.sb"
        profile.write_text("\n".join([
            "(version 1)", "(allow default)",
            f"(deny file-read-metadata (literal {json.dumps(str(sessions.resolve()))}))",
        ]))
        sandbox = ["/usr/bin/sandbox-exec", "-f", str(profile), sys.executable, "-B"]
        # Prove the sandbox denies the ancestor metadata that strict realpath
        # needs; a permissive local run would not reproduce this failure.
        for target in (current, path):
            probe = subprocess.run(sandbox + ["-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).resolve(strict=True)", str(target)],
                cwd=current, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(probe.returncode, 0)
            self.assertIn("PermissionError", probe.stderr)
            self.assertIn("Operation not permitted", probe.stderr)
        for value in ("box.step", str(path), "alias.step"):
            with self.subTest(input=value):
                result = subprocess.run(sandbox + [measurements.__file__, value, "--workspace", str(current),
                    "--section-z", "0", "--out", "measurements.json"],
                    cwd=current, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads(result.stdout)
                self.assertEqual(summary["input"]["sha256"], sha256(path.read_bytes()).hexdigest())
                self.assertAlmostEqual(summary["sections"][0]["outer_envelope"]["width_u_mm"], 20)
                full_path = Path(summary["fullResult"]["path"])
                self.assertEqual(summary["fullResult"]["sha256"], sha256(full_path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
