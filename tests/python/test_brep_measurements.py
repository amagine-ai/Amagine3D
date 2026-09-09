"""Exact final-solid sections, frames, holes and STEP/CLI input binding."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
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
import step_check
from build_manifest import artifact_record
from tests.python.intent_fixture import intent_ref, write_intent


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

    def _section_audit_inputs(self, step_path, sections, *, owner="part", other_step=None,
                              dimensions=(54, 40, 10)):
        owners = {"mouth-outside": owner}
        if other_step is not None:
            owners["other-body"] = "other"
        intent_path, intent = write_intent(
            self.work, part=owner if other_step is None else "fixture-assembly",
            feature_owners=owners, dimensions_mm=dimensions)
        if sections is not None:
            intent["features"][0]["section_dimensions"] = deepcopy(sections)
        intent_path.write_text(json.dumps(intent))
        # Only STEP-check inputs are needed here; complete manifests are tested separately.
        report = {"schema": "evidence-a3d-build/v1",
                  "backend": "brep-part" if other_step is None else "brep-assembly",
                  "inputs": {"intent": intent_ref(intent_path)},
                  "parts": {part: {"semantic": {"boundsMm": {"size": list(dimensions)}}}
                            for part in set(owners.values())},
                  "artifacts": {f"step:{owner}": artifact_record(step_path, coordinateFrame="semantic")}}
        if other_step is not None:
            report["artifacts"]["step:other"] = artifact_record(other_step, coordinateFrame="semantic")
        report_path = self.work / "step-report.json"
        report_path.write_text(json.dumps(report))
        return intent_path, report_path

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

        raw_path = self.work / "unfilleted.step"
        export_step(box, raw_path)
        fixed = [{"plane": {"axis": "z", "coordinate_mm": 10},
                  "outer_envelope": {"width_u_mm": {"value": 54}}}]
        correct = deepcopy(fixed)
        correct[0]["outer_envelope"]["width_u_mm"]["value"] = 52
        ranged = deepcopy(correct)
        ranged[0]["outer_envelope"]["width_u_mm"]["constraint"] = {
            "kind": "range", "min_mm": 51.99, "max_mm": 52.01}
        outside_range = deepcopy(ranged)
        outside_range[0]["outer_envelope"]["width_u_mm"] = {
            "value": 53, "constraint": {"kind": "range", "min_mm": 52.9, "max_mm": 53.1}}
        empty = deepcopy(fixed)
        empty[0]["plane"]["coordinate_mm"] = 11
        # At 0.5 mm from a top fillet's center, the unit circle reaches
        # z=9+sqrt(1-0.5**2). Using an offset relative to the part's minimum
        # instead of these absolute x/y planes would incorrectly measure z=10.
        section_height = 9 + math.sqrt(3) / 2
        axes = [
            {"plane": {"axis": "x", "coordinate_mm": 26.5},
             "outer_envelope": {"width_u_mm": {"value": 40}, "depth_v_mm": {"value": section_height}}},
            {"plane": {"axis": "y", "coordinate_mm": -19.5},
             "outer_envelope": {"width_u_mm": {"value": 54}, "depth_v_mm": {"value": section_height}}},
        ]
        cases = [
            ("legacy", None, True, "part", None),
            ("unbound legacy report", None, True, "part", None),
            ("fixed mismatch", fixed, False, "part", None),
            ("report-only relative intent", fixed, False, "part", None),
            ("fixed correct", correct, True, "part", None),
            ("explicit range", ranged, True, "part", None),
            ("outside range", outside_range, False, "part", None),
            ("empty section", empty, False, "part", None),
            ("absolute x/y planes and u/v", axes, True, "part", None),
            ("physical part named assembly", fixed, False, "assembly", None),
            ("correct other part cannot substitute", fixed, False, "part", raw_path),
        ]
        for name, sections, expected, owner, other in cases:
            with self.subTest(name=name):
                intent_path, report_path = self._section_audit_inputs(
                    path, sections, owner=owner, other_step=other)
                argv = [sys.executable, step_check.__file__, str(path), "--report", str(report_path), "--tol", "10"]
                if name in {"report-only relative intent", "unbound legacy report"}:
                    report = json.loads(report_path.read_text())
                    if sections is None:
                        report["inputs"].pop("intent")
                    else:
                        report["inputs"]["intent"]["path"] = intent_path.name
                    report_path.write_text(json.dumps(report))
                else:
                    argv.extend(["--intent", str(intent_path)])
                checked = subprocess.run(
                    argv,
                    capture_output=True, text=True, encoding="utf-8", timeout=30)
                self.assertEqual(checked.returncode, 0 if expected else 1, checked.stdout + checked.stderr)
                payload = json.loads(checked.stdout)
                self.assertEqual(payload["pass"], expected)
                self.assertEqual(payload["step"]["sha256"], sha256(path.read_bytes()).hexdigest())
                self.assertTrue(all(check["pass"] for check in payload["checks"]
                                    if check["name"].startswith("dimension_")))
                local = [check for check in payload["checks"] if check["name"].startswith("section:")]
                if sections is None:
                    self.assertEqual(local, [])
                    continue
                if name == "absolute x/y planes and u/v":
                    self.assertEqual(len(local), 4, payload)
                    for check in local:
                        self.assertAlmostEqual(check["observed"]["actual_mm"], check["expected"]["value_mm"])
                        plane = check["observed"]["plane"]
                        self.assertEqual(plane["v_dir"], [0, 0, 1])
                        if ":0:" in check["name"]:
                            self.assertEqual(plane["origin_mm"], [26.5, 0, 0])
                            self.assertEqual(plane["u_dir"], [0, 1, 0])
                        else:
                            self.assertEqual(plane["origin_mm"], [0, -19.5, 0])
                            self.assertEqual(plane["u_dir"], [1, 0, 0])
                    continue
                self.assertEqual(len(local), 1, payload)
                check = local[0]
                self.assertEqual(check["observed"]["part"], owner)
                self.assertEqual(check["observed"]["coordinate_frame"], "semantic")
                self.assertEqual(check["expected"]["measurement_precision_mm"], 0.01)
                if name == "empty section":
                    self.assertIsNone(check["observed"]["actual_mm"])
                else:
                    self.assertAlmostEqual(check["observed"]["actual_mm"], 52)
                    if not expected and sections[0]["outer_envelope"]["width_u_mm"]["value"] == 54:
                        self.assertAlmostEqual(check["observed"]["delta_mm"], -2)

    def test_section_dimensions_compare_raw_values_at_the_selected_precision(self):
        width = 20.123456
        path = self.work / "precision.step"
        export_step(Box(width, 12, 10), path)
        cases = [
            ("default fixed inside", {"value": width - 0.009}, True, 0.01),
            ("default fixed outside", {"value": width - 0.011}, False, 0.01),
            ("default range inside", {"value": 20, "constraint": {
                "kind": "range", "min_mm": 19.9, "max_mm": width - 0.009}}, True, 0.01),
            ("default range outside", {"value": 20, "constraint": {
                "kind": "range", "min_mm": 19.9, "max_mm": width - 0.011}}, False, 0.01),
            ("strict range inside", {"value": 20, "measurement_precision_mm": 0.0001,
                "constraint": {"kind": "range", "min_mm": 19.9,
                               "max_mm": width - 0.00005}}, True, 0.0001),
            ("strict range outside", {"value": 20, "measurement_precision_mm": 0.0001,
                "constraint": {"kind": "range", "min_mm": 19.9,
                               "max_mm": width - 0.00015}}, False, 0.0001),
        ]
        for name, target, expected, precision in cases:
            with self.subTest(name=name):
                sections = [{"plane": {"axis": "z", "coordinate_mm": 0},
                             "outer_envelope": {"width_u_mm": target}}]
                intent_path, report_path = self._section_audit_inputs(
                    path, sections, dimensions=(width, 12, 10))
                with patch.object(sys, "argv", [step_check.__file__, str(path),
                        "--intent", str(intent_path), "--report", str(report_path)]), \
                        redirect_stdout(io.StringIO()) as output:
                    code = step_check.main()
                self.assertEqual(code, 0 if expected else 1, output.getvalue())
                audit = json.loads(output.getvalue())
                check = next(item for item in audit["checks"] if item["name"].startswith("section:"))
                self.assertEqual(check["pass"], expected)
                self.assertEqual(check["expected"]["measurement_precision_mm"], precision)
                self.assertAlmostEqual(check["observed"]["actual_mm"], width, places=6)
                self.assertGreater(abs(check["observed"]["actual_mm"] - round(width, 2)), 0.003)

    def test_step_section_audit_rejects_unbound_owner_frame_and_replaced_input(self):
        path = self.work / "unread.step"
        path.write_bytes(b"binding failures must occur before CAD import")
        sections = [{"plane": {"axis": "z", "coordinate_mm": 10},
                     "outer_envelope": {"width_u_mm": {"value": 54}}}]
        cases = {
            "wrong intent hash": "intent does not match",
            "removed section requirements": "intent does not match",
            "report-only bad intent hash": "intent does not match",
            "wrong semantic frame": "semantic STEP",
            "unknown owner": "declared physical owner",
            "unknown artifact": "declared physical part",
            "duplicate SHA alias": "exactly one",
            "missing report": "hash-bound build report",
            "multipart assembly owner": "owner assembly collides",
            "replaced after selection": "changed after build-report artifact selection",
        }
        for name, message in cases.items():
            with self.subTest(name=name):
                other_path = self.work / "other-unread.step"
                other_path.write_bytes(b"another owner's STEP")
                intent_path, report_path = self._section_audit_inputs(
                    path, sections, owner="assembly" if name == "multipart assembly owner" else "part",
                    other_step=other_path if name == "multipart assembly owner" else None)
                intent, report = json.loads(intent_path.read_text()), json.loads(report_path.read_text())
                if name in {"wrong intent hash", "report-only bad intent hash"}:
                    report["inputs"]["intent"]["sha256"] = "0" * 64
                elif name == "removed section requirements":
                    intent["features"][0].pop("section_dimensions")
                    intent_path = self.work / "substitute-legacy-intent.json"
                    intent_path.write_text(json.dumps(intent))
                elif name == "wrong semantic frame":
                    report["artifacts"]["step:part"]["coordinateFrame"] = "part-print"
                elif name == "unknown owner":
                    intent["features"][0]["part"] = "other"
                    intent_path.write_text(json.dumps(intent))
                    report["inputs"]["intent"] = intent_ref(intent_path)
                elif name in {"unknown artifact", "duplicate SHA alias"}:
                    report["artifacts"]["step:alias"] = deepcopy(report["artifacts"]["step:part"])
                    if name == "unknown artifact":
                        report["artifacts"]["step:part"]["sha256"] = "f" * 64
                report_path.write_text(json.dumps(report))
                argv = ["step_check.py", str(path)]
                if name != "report-only bad intent hash":
                    argv.extend(["--intent", str(intent_path)])
                if name != "missing report":
                    argv.extend(["--report", str(report_path)])
                digest_calls = 0
                real_digest = step_check._digest

                def digest(file):
                    nonlocal digest_calls
                    if name == "replaced after selection" and Path(file).resolve() == path:
                        digest_calls += 1
                        if digest_calls > 1:
                            return "f" * 64
                    return real_digest(file)

                with patch.object(sys, "argv", argv), patch.object(step_check, "_digest", digest), \
                     patch.object(step_check, "import_step") as importer, redirect_stdout(io.StringIO()) as output:
                    code = step_check.main()
                importer.assert_not_called()
                self.assertEqual(code, 1)
                payload = json.loads(output.getvalue())
                self.assertFalse(payload["pass"])
                self.assertIn(message, payload["checks"][0]["observed"])

    def test_step_changed_during_measurement_discards_result(self):
        path = self.work / "changing.step"
        export_step(Box(10, 10, 10), path)
        original = path.read_bytes()
        real_import = measurements.import_step

        def changed(file):
            part = real_import(file)
            changed_path = Path(file)
            changed_path.write_bytes(changed_path.read_bytes() + b"\n")
            return part

        with patch.object(measurements, "import_step", changed):
            with self.assertRaisesRegex(ValueError, "changed during measurement"):
                measurements.measure_step(path)

        path.write_bytes(original)
        intent_path, report_path = self._section_audit_inputs(
            path, [{"plane": {"axis": "z", "coordinate_mm": 0},
                    "outer_envelope": {"width_u_mm": {"value": 10}}}], dimensions=(10, 10, 10))
        with patch.object(sys, "argv", ["step_check.py", str(path), "--intent", str(intent_path),
                                       "--report", str(report_path)]), \
             patch.object(step_check, "import_step", changed), redirect_stdout(io.StringIO()) as output:
            code = step_check.main()
        self.assertEqual(code, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["pass"])
        self.assertEqual(payload["errors"], ["section_step_unchanged"])
        binding = next(check for check in payload["checks"] if check["name"] == "section_step_unchanged")
        self.assertEqual(binding["expected"], sha256(original).hexdigest())
        self.assertEqual(binding["observed"], sha256(path.read_bytes()).hexdigest())
        self.assertNotEqual(binding["expected"], binding["observed"])
        self.assertEqual(payload["step"]["sha256"], binding["observed"])

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

    def test_cli_rounds_length_readings_without_moving_planes_or_rounding_areas(self):
        path = self.work / "fractional.step"
        body = Pos(0.123456, -0.234567, 5.001) * (
            Box(20.123456, 12.654321, 10.002) - Cylinder(1.234567, 12))
        export_step(body, path)
        output_path = self.work / "fractional-measurements.json"
        # A real STEP read supplies the CLI; also retain that raw result to
        # verify the output projection cannot mutate a Python caller's data.
        original_measure = measurements.measure_step
        captured = []

        def capture(*args, **kwargs):
            result = original_measure(*args, **kwargs)
            captured.append((result, deepcopy(result)))
            return result

        with patch.object(measurements, "measure_step", side_effect=capture) as measured, \
                redirect_stdout(io.StringIO()) as output:
            code = measurements.main([str(path), "--workspace", str(self.work),
                "--section-z", "5.000001", "--section-z", "10.003", "--out", str(output_path)])
        self.assertEqual(code, 0)
        self.assertEqual(measured.call_count, 1)
        raw, raw_before = captured[0]
        report = json.loads(output_path.read_text())
        summary = json.loads(output.getvalue())
        self.assertEqual(raw, raw_before)
        self.assertEqual(report["length_report_resolution_mm"], 0.01)
        self.assertEqual(summary["length_report_resolution_mm"], 0.01)
        self.assertEqual(summary["fullResult"]["sha256"], sha256(output_path.read_bytes()).hexdigest())
        self.assertEqual(summary["world_bounds_mm"], report["world_bounds_mm"])
        self.assertEqual(report["world_bounds_mm"]["size"], [20.12, 12.65, 10.0])
        self.assertAlmostEqual(raw["world_bounds_mm"]["size"][0], 20.123456, places=6)
        for original, full, compact in zip(raw["sections"], report["sections"], summary["sections"]):
            self.assertEqual(full["plane"], original["plane"])
            self.assertEqual(compact["plane"], original["plane"])
            self.assertEqual(compact["outer_envelope"], full["outer_envelope"])
            self.assertAlmostEqual(full["sum_material_area_mm2"], original["sum_material_area_mm2"], places=8)
        section = report["sections"][0]
        self.assertEqual(section["outer_envelope"]["width_u_mm"], 20.12)
        self.assertEqual(section["outer_envelope"]["center_world_mm"], [0.12, -0.23, 5.0])
        island, raw_island = section["material_islands"][0], raw["sections"][0]["material_islands"][0]
        self.assertEqual(island["outer"]["perimeter_mm"], round(raw_island["outer"]["perimeter_mm"], 2))
        self.assertEqual(island["holes"][0]["perimeter_mm"], round(raw_island["holes"][0]["perimeter_mm"], 2))
        self.assertAlmostEqual(island["holes"][0]["enclosed_area_mm2"],
                               raw_island["holes"][0]["enclosed_area_mm2"], places=8)
        self.assertAlmostEqual(island["relative_area_error_estimate"],
                               raw_island["relative_area_error_estimate"], places=12)
        self.assertAlmostEqual(report["sum_solid_volume_mm3"], raw["sum_solid_volume_mm3"], places=8)
        self.assertEqual(report["sections"][1]["plane"]["origin_mm"][2], 10.003)
        self.assertEqual(report["sections"][1]["status"], "empty")
        self.assertIsNone(report["sections"][1]["outer_envelope"])

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
