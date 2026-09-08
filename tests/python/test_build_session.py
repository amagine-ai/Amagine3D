"""Real BRep operations, immutable copies and exported final-part evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from build123d import Align, Box, Cylinder, Location, Pos, Rectangle, import_step
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
sys.path.insert(0, str(SKILL))
from authoring import AuthoringError, paired_interface, write_intent
from build_session import BuildSession
import cad_helpers


class BuildSessionTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "workspace" / "skill-validation"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def session(self, directory="a", feature_ids=("body", "pocket")):
        work = self.work / directory
        work.mkdir()
        intent = work / "intent.json"
        features = [{"id": name, "evidence": "test specification", "acceptance": "retains requested geometry"}
                    for name in feature_ids]
        write_intent(
            intent, profile_path=SKILL / "examples" / "bambu-a1-mini-0.4-standard.example.json",
            part="unit-q", task_mode="specification", representation="full-3d",
            dimensions_mm={axis: {"value": 10.0, "source": "user", "confidence": "high"} for axis in "xyz"},
            manufacturing_mode="single-part", parts={"unit-q": {"features": features}},
            support_policy="support-free", minimum_wall_target_mm=0.9,
            critical_features=list(feature_ids), reference_view="isometric",
            landmarks=["cube with a top opening"], assumptions=[],
        )
        return BuildSession(__file__, intent_path=intent, out_dir=work)

    def export(self, build, **kwargs):
        with redirect_stdout(io.StringIO()):
            return build.export(**kwargs)

    def assertEvidenceEqual(self, actual, expected):
        for field in ("events", "features", "parameters", "issues"):
            self.assertEqual(getattr(actual, field), getattr(expected, field), field)

    @staticmethod
    def pocket():
        return Pos(0, 0, 5) * Box(2, 2, 4)

    def test_actual_cut_and_copies_drive_exported_scene_and_evidence(self):
        build = self.session(feature_ids=("envelope/q", "opening/r"))
        intent_bytes = build.intent_path.read_bytes()
        body, cutter = Box(10, 10, 10), self.pocket()
        returned = build.add("envelope/q", body)
        build.cut("opening/r", cutter)
        for shape in (body, cutter, returned, build.part("unit-q")):
            shape.move(Location((100, 0, 0)))
        report = self.export(build)
        self.assertEqual(build.intent_path.read_bytes(), intent_bytes)
        scene = json.loads(build.scene_path.read_text())
        nodes = {node["featureId"]: node for node in scene["nodes"]}
        self.assertEqual(set(nodes), {"envelope/q", "opening/r"})
        cut = next(event for event in report["events"] if event["kind"] == "cut")
        self.assertEqual((cut["id"], cut["part"]), ("opening/r", "unit-q"))
        self.assertAlmostEqual(cut["removed_mm3"], 8)
        self.assertAlmostEqual(report["features"]["envelope/q"]["volume_mm3"], 1000)
        tool = trimesh.load_mesh(nodes["opening/r"]["recipe"]["parameters"]["geometry"]["path"])
        np.testing.assert_allclose(tool.bounds, [[-1, -1, 3], [1, 1, 7]])
        np.testing.assert_allclose(tool.bounds[0], cut["tool"]["bbox_mm"]["min"])
        self.assertAlmostEqual(import_step(build.out_dir / "unit-q.step").volume, 992, places=5)
        mesh = trimesh.load_mesh(build.out_dir / "unit-q.stl")
        self.assertTrue(mesh.is_volume)
        self.assertAlmostEqual(mesh.volume, 992, places=4)
        final = [r for r in report["features"].values() if r["role"] == "separate"]
        self.assertEqual(len(final), 1)
        self.assertAlmostEqual(final[0]["volume_mm3"], 992)

    def test_finish_commits_fillet_before_cut_to_real_step_and_stl(self):
        build = self.session(feature_ids=("body", "rim", "pocket", "foot"))
        original = Box(10, 10, 10)
        build.add("body", original)
        copied_before = build.part("unit-q")
        retained = []

        def soften(shape):
            retained.append(shape)
            return cad_helpers.checked_fillet(shape, shape.edges(), 1, "rim")

        rounded = build.finish("unit-q", soften)
        rounded_volume = rounded.volume
        self.assertLess(rounded_volume, 1000)
        self.assertAlmostEqual(original.volume, 1000)
        self.assertAlmostEqual(copied_before.volume, 1000)
        for shape in [rounded, retained[0], copied_before]:
            shape.move(Location((100, 0, 0)))
        build.cut("pocket", self.pocket())
        build.observe("foot")
        report = self.export(build)
        step = import_step(build.out_dir / "unit-q.step")
        mesh = trimesh.load_mesh(build.out_dir / "unit-q.stl")
        self.assertAlmostEqual(step.volume, rounded_volume - 8, places=5)
        self.assertAlmostEqual(mesh.volume, step.volume, delta=0.5)
        self.assertGreater(sum(face.geom_type.name != "PLANE" for face in step.faces()), 0)
        np.testing.assert_allclose(list(build.part("unit-q").bounding_box().min), [-5, -5, -5], atol=1e-6)
        finish = next(event for event in report["events"] if event["kind"] == "fillet")
        self.assertEqual(finish["part"], "unit-q")
        self.assertEqual(finish["actual_mm"], 1)
        nodes = {n["featureId"]: n for n in json.loads(build.scene_path.read_text())["nodes"]}
        for feature in ("rim", "foot"):
            self.assertEqual((nodes[feature]["role"], nodes[feature]["operation"]), ("separate", "none"))
        self.assertAlmostEqual(report["features"]["body"]["volume_mm3"], 1000)

    def test_failed_cut_rolls_back_and_same_feature_can_be_retried(self):
        for phase in ("", "compile"):
            build = self.session(phase or "direct")
            with self.subTest(phase=phase), patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": phase}):
                build.add("body", Box(10, 10, 10))
                before = deepcopy(build._evidence)
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(cad_helpers.BuildInvariantError):
                    build.cut("pocket", Pos(30, 0, 0) * Box(2, 2, 2))
                self.assertEvidenceEqual(build._evidence, before)
                self.assertAlmostEqual(build.part("unit-q").volume, 1000)
                if phase:
                    self.assertEqual(json.loads(output.getvalue())["issues"][0]["code"], "SOURCE.CUT_MISSED_OWNER")
                build.cut("pocket", self.pocket())
                self.assertAlmostEqual(build.part("unit-q").volume, 992)

    def test_implementation_finish_id_keeps_real_evidence_without_new_contract_feature(self):
        build = self.session()
        intent_before = build.intent_path.read_bytes()
        build.add("body", Box(10, 10, 10))
        build.finish("unit-q", lambda p: cad_helpers.checked_fillet(p, p.edges(), 1, "body-rounding"))
        build.cut("pocket", self.pocket())
        report = self.export(build)
        self.assertEqual(build.intent_path.read_bytes(), intent_before)
        event = next(e for e in report["events"] if e["id"] == "body-rounding")
        self.assertEqual((event["kind"], event["part"], event["actual_mm"]), ("fillet", "unit-q", 1))
        self.assertNotIn("body-rounding", report["features"])
        self.assertEqual({n["featureId"] for n in json.loads(build.scene_path.read_text())["nodes"]},
                         {"body", "pocket"})
        self.assertLess(import_step(build.out_dir / "unit-q.step").volume, 992)

    def test_failed_finish_preserves_geometry_and_diagnostic_in_compile_mode(self):
        build = self.session(feature_ids=("body", "rim"))
        build.add("body", Box(10, 10, 10))
        before = deepcopy(build._evidence)
        output = io.StringIO()
        with patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": "compile"}):
            with redirect_stdout(output), self.assertRaises(cad_helpers.BuildInvariantError):
                build.finish("unit-q", lambda p: cad_helpers.checked_fillet(p, [], 1, "rim"))
        self.assertEqual(json.loads(output.getvalue())["issues"][0]["code"], "SOURCE.CHECKED_FILLET_FAILED")
        self.assertEvidenceEqual(build._evidence, before)
        self.assertAlmostEqual(build.part("unit-q").volume, 1000)
        build.finish("unit-q", lambda p: cad_helpers.checked_fillet(p, p.edges(), 1, "rim"))
        self.assertLess(build.part("unit-q").volume, 1000)

    def test_callback_exception_does_not_commit_mutation_or_success_event(self):
        build = self.session(feature_ids=("body", "rim"))
        build.add("body", Box(10, 10, 10))
        before = deepcopy(build._evidence)
        failure = RuntimeError("callback failed")

        def fail(shape):
            cad_helpers.checked_fillet(shape, shape.edges(), 1, "rim")
            shape.move(Location((100, 0, 0)))
            raise failure

        with self.assertRaises(RuntimeError) as raised:
            build.finish("unit-q", fail)
        self.assertIs(raised.exception, failure)
        self.assertEvidenceEqual(build._evidence, before)
        self.assertAlmostEqual(build.part("unit-q").bounding_box().min.X, -5)
        build.finish("unit-q", lambda p: cad_helpers.checked_fillet(p, p.edges(), 1, "rim"))

    def test_invalid_finish_and_foreign_owner_roll_back(self):
        build = self.session(feature_ids=("body", "rim"))
        build.add("body", Box(10, 10, 10))
        before = deepcopy(build._evidence)
        with self.assertRaises(cad_helpers.BuildInvariantError):
            build.finish("unit-q", lambda p: Rectangle(10, 10))
        with self.assertRaises(AuthoringError):
            build.finish("unit-q", lambda p: cad_helpers.checked_fillet(p, p.edges(), 1, "rim", part_name="other"))
        self.assertEvidenceEqual(build._evidence, before)
        self.assertAlmostEqual(build.part("unit-q").volume, 1000)

    def test_nested_sessions_and_capture_failure_restore_all_state(self):
        first, second = self.session("a"), self.session("b")
        legacy = deepcopy(cad_helpers._evidence())
        with first.capture():
            cad_helpers.parameter("width", 10, min_value=1, max_value=20, step=1)
            with second.capture():
                cad_helpers.parameter("width", 8, min_value=1, max_value=20, step=1)
                second.add("body", Box(8, 8, 8))
            first.add("body", Box(10, 10, 10))
        with self.assertRaises(RuntimeError):
            with first.capture():
                first.cut("pocket", self.pocket())
                cad_helpers.parameter("length", 4, min_value=1, max_value=10, step=1)
                raise RuntimeError()
        self.assertAlmostEqual(first.part("unit-q").volume, 1000)
        first.cut("pocket", self.pocket())
        second.cut("pocket", Pos(0, 0, 4) * Box(2, 2, 4))
        report = self.export(first)
        self.assertEqual(report["backendData"]["parameters"]["width"]["value"], 10)
        self.assertNotIn("length", report["backendData"]["parameters"])
        self.assertEqual(second._evidence.parameters["width"]["value"], 8)
        self.assertEvidenceEqual(cad_helpers._evidence(), legacy)

    def test_duplicate_observation_and_repeated_export_do_not_add_material(self):
        build = self.session(feature_ids=("body", "pocket", "foot"))
        build.add("body", Box(10, 10, 10))
        build.cut("pocket", self.pocket())
        with self.assertRaises(AuthoringError):
            build.add("body", Box(20, 20, 20))
        with self.assertRaises(cad_helpers.BuildInvariantError):
            build.observe("foot", Rectangle(4, 4))
        build.observe("foot")
        with self.assertRaises(AuthoringError):
            build.observe("foot")
        before = deepcopy(build._evidence)
        first, second = self.export(build), self.export(build)
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(first["features"], second["features"])
        self.assertEvidenceEqual(build._evidence, before)
        self.assertAlmostEqual(build.part("unit-q").volume, 992)

    def test_addition_measures_actual_connected_material(self):
        build = self.session()
        build.add("body", Box(10, 10, 10))
        before = deepcopy(build._evidence)
        with self.assertRaises(cad_helpers.BuildInvariantError):
            build.add("pocket", Pos(30, 0, 0) * Box(2, 2, 2))
        self.assertEvidenceEqual(build._evidence, before)
        build.add("pocket", Pos(5, 0, 0) * Box(2, 2, 2))
        self.assertAlmostEqual(build.part("unit-q").volume, 1004)
        self.assertEqual(build._evidence.events[0]["added_mm3"], 4)

    def test_contained_boss_observation_does_not_add_material(self):
        build = self.session(feature_ids=("body", "boss"))
        build.add("body", Box(10, 10, 10))
        boss = Cylinder(1, 6)
        with self.assertRaises(AuthoringError):
            build.observe("boss", boss, role="cutter")
        build.observe("boss", boss, role="solid")
        report = self.export(build)
        self.assertAlmostEqual(build.part("unit-q").volume, 1000)
        self.assertAlmostEqual(import_step(build.out_dir / "unit-q.step").volume, 1000, places=5)
        self.assertEqual(report["events"], [])
        self.assertEqual(report["features"]["boss"]["role"], "solid")
        self.assertAlmostEqual(report["features"]["boss"]["volume_mm3"], boss.volume, places=4)
        node = next(n for n in json.loads(build.scene_path.read_text())["nodes"] if n["featureId"] == "boss")
        self.assertEqual(node["role"], "solid")

    def test_two_part_export_keeps_existing_paired_interface_contract(self):
        intent = self.work / "pair-intent.json"
        write_intent(
            intent, profile_path=SKILL / "examples" / "bambu-a1-mini-0.4-standard.example.json",
            part="fit-coupon", task_mode="specification", representation="full-3d",
            dimensions_mm={axis: {"value": value, "source": "user", "confidence": "high"}
                           for axis, value in zip("xyz", (30, 24, 10))}, manufacturing_mode="multipart",
            parts={owner: {"role": "mating member", "acceptance": "retains interface", "features": [
                {"id": feature, "evidence": "test geometry", "acceptance": "retains geometry"}
                for feature in features]}
                for owner, features in (("holder", ("body", "socket", "rim")), ("pin", ("pin-body",)))},
            interfaces=[{"id": "locating-fit", "connection": "pin-socket", "assembly_axis": "-Z",
                         "clearances_mm": {"diameter": 0.4}, "engagement_mm": 4.0,
                         "features": ["pin-body", "socket"], "acceptance": "retains clearance"}],
            support_policy="support-free", minimum_wall_target_mm=0.9,
            critical_features=["socket", "pin-body", "rim"], reference_view="isometric",
            landmarks=["pin and socket"], assumptions=[])
        build = BuildSession(__file__, intent_path=intent, out_dir=self.work)
        align = (Align.CENTER, Align.CENTER, Align.MIN)
        build.add("body", Box(30, 24, 6, align=align))
        build.finish("holder", lambda p: cad_helpers.checked_fillet(p, p.edges(), 0.5, "rim"))
        build.cut("socket", Pos(0, 0, 2) * Cylinder(3.2, 5, align=align))
        build.add("pin-body", Pos(0, 0, 2) * Cylinder(3, 8, align=align))
        interface = paired_interface(id="locating-fit", kind="pin-socket", male_feature="pin-body",
                                     male_dimensions_mm={"diameter": 6}, female_feature="socket",
                                     clearances_mm={"diameter": 0.4})
        report = self.export(build, paired_interfaces=[interface])
        self.assertEqual(set(report["parts"]), {"holder", "pin"})
        self.assertEqual(len(import_step(self.work / "fit-coupon-assemble.step").solids()), 2)
        self.assertEqual(next(e for e in report["events"] if e["kind"] == "fillet")["part"], "holder")
        scene = json.loads(build.scene_path.read_text())
        self.assertEqual(scene["interfaces"][0]["male"]["partId"], "pin")
        self.assertEqual(scene["interfaces"][0]["female"]["partId"], "holder")

    def test_finish_cannot_reenter_and_leave_an_observation_on_failure(self):
        build = self.session(feature_ids=("body", "foot"))
        build.add("body", Box(10, 10, 10))
        before = deepcopy(build._evidence)

        def reenter(shape):
            build.observe("foot", shape)
            return shape

        with self.assertRaises(AuthoringError):
            build.finish("unit-q", reenter)
        self.assertEvidenceEqual(build._evidence, before)
        self.assertAlmostEqual(build.part("unit-q").volume, 1000)
        build.observe("foot")

    def test_conflicting_screw_references_fail_without_changing_usable_geometry(self):
        build = self.session()
        build.add("body", Box(10, 10, 10))
        build.cut("pocket", self.pocket())
        report = self.export(build)
        previous_step = (build.out_dir / "unit-q.step").read_bytes()
        before = deepcopy(build._evidence)
        references = [{"id": "joint", "kind": "self-tapping-screw", "fasteners": [
            {"id": axis, "cover": {"featureId": "pocket"}} for axis in ("axis-one", "axis-two")]}]
        with self.assertRaises(AuthoringError):
            self.export(build, interfaces=references)
        self.assertEqual((build.out_dir / "unit-q.step").read_bytes(), previous_step)
        self.assertEvidenceEqual(build._evidence, before)
        self.assertAlmostEqual(build.part("unit-q").volume, report["features"]["body"]["volume_mm3"] - 8)


if __name__ == "__main__":
    unittest.main()
