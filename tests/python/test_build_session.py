"""Real BRep operations, immutable copies and exported final-part evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from build123d import Align, Box, Compound, Cylinder, Location, Pos, Rectangle, import_step
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

    def test_construction_operations_keep_real_events_without_binding_requirements(self):
        build = self.session()
        intent_before = build.intent_path.read_bytes()
        for operation in (build.add, build.cut):
            for owner in (None, "other"):
                with self.subTest(operation=operation.__name__, owner=owner), self.assertRaises(AuthoringError):
                    operation("stock", Box(1, 1, 1), part_name=owner)
        self.assertFalse(build._parts)
        build.add("stock", Box(10, 10, 10), part_name="unit-q")
        initial = build._evidence.events[0]
        self.assertEqual((initial["id"], initial["kind"], initial["part"]), ("stock", "add", "unit-q"))
        self.assertAlmostEqual(initial["added_mm3"], 1000)
        self.assertEqual(initial["tool"]["solid_count"], 1)
        build.cut("rough-opening", self.pocket(), part_name="unit-q")
        self.assertAlmostEqual(build.part("unit-q").volume, 992)
        # Reusing an unbound construction name remains an event, not a feature.
        build.add("stock", Pos(0, 0, 4)*Box(2, 2, 2), part_name="unit-q")
        self.assertAlmostEqual(build.part("unit-q").volume, 1000)
        self.assertFalse(build._features)
        self.assertFalse(build._evidence.features)
        with self.assertRaisesRegex(AuthoringError, "not declared"):
            build.observe("stock", part_name="unit-q")
        build.observe("body", role="solid")
        build.cut("pocket", self.pocket())
        with self.assertRaises(AuthoringError):
            build.add("body", Box(1, 1, 1), part_name="unit-q")
        report = self.export(build)
        self.assertEqual(build.intent_path.read_bytes(), intent_before)
        self.assertEqual([(e["id"], e["kind"]) for e in report["events"]],
                         [("stock", "add"), ("rough-opening", "cut"), ("stock", "union"), ("pocket", "cut")])
        self.assertAlmostEqual(report["events"][2]["added_mm3"], 8)
        self.assertTrue({"stock", "rough-opening"}.isdisjoint(report["features"]))
        self.assertEqual({n["featureId"] for n in json.loads(build.scene_path.read_text())["nodes"]},
                         {"body", "pocket"})
        self.assertAlmostEqual(import_step(build.out_dir/"unit-q.step").volume, 992, places=5)

    def test_construction_initial_add_checks_material_and_rolls_back(self):
        for phase in ("", "compile"):
            build = self.session("initial-"+(phase or "direct"))
            before = deepcopy(build._evidence)
            with self.subTest(phase=phase), patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": phase}):
                for minimum in (-1, float("nan"), float("inf"), True, "1"):
                    with self.subTest(minimum=minimum), self.assertRaises(cad_helpers.BuildInvariantError):
                        build.add("stock", Box(1, 1, 1), part_name="unit-q", min_added_mm3=minimum)
                for geometry in (Rectangle(1, 1), Compound(children=[Box(1, 1, 1), Pos(3, 0, 0)*Box(1, 1, 1)])):
                    with self.assertRaises(cad_helpers.BuildInvariantError):
                        build.add("stock", geometry, part_name="unit-q")
                output = io.StringIO()
                tiny = Box(.01, .01, .01)
                with redirect_stdout(output), self.assertRaises(cad_helpers.BuildInvariantError):
                    build.add("stock", tiny, part_name="unit-q")
                if phase:
                    issue = json.loads(output.getvalue())["issues"][0]
                    self.assertEqual(issue["code"], "SOURCE.INITIAL_ADD_BELOW_MINIMUM")
                    self.assertAlmostEqual(issue["observed"]["addedMm3"], tiny.volume, places=12)
                self.assertEvidenceEqual(build._evidence, before)
                self.assertFalse(build._parts)
                self.assertFalse(build._features)
                build.add("stock", tiny, part_name="unit-q", min_added_mm3=0)
                self.assertAlmostEqual(build.part("unit-q").volume, 1e-6, places=12)
                self.assertGreater(build._evidence.events[0]["added_mm3"], 0)
                self.assertEqual(build._evidence.events[0]["kind"], "add")
        # The already-declared first-add path retains its previous behavior.
        declared = self.session("declared-initial")
        declared.add("body", Box(.01, .01, .01), min_added_mm3=1)
        self.assertEqual(declared._evidence.events, [])

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
            for feature in ("pocket", "rough-cut"):
                build = self.session((phase or "direct")+"-"+feature)
                with self.subTest(phase=phase, feature=feature), patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": phase}):
                    build.add("body", Box(10, 10, 10))
                    before = deepcopy(build._evidence)
                    output = io.StringIO()
                    with redirect_stdout(output), self.assertRaises(cad_helpers.BuildInvariantError):
                        build.cut(feature, Pos(30, 0, 0) * Box(2, 2, 2), part_name="unit-q")
                    self.assertEvidenceEqual(build._evidence, before)
                    self.assertAlmostEqual(build.part("unit-q").volume, 1000)
                    if phase:
                        self.assertEqual(json.loads(output.getvalue())["issues"][0]["code"], "SOURCE.CUT_MISSED_OWNER")
                    build.cut(feature, self.pocket(), part_name="unit-q")
                    self.assertAlmostEqual(build.part("unit-q").volume, 992)
                    self.assertEqual(feature in build._features, feature == "pocket")

    def test_same_source_previews_without_intent_then_exports_with_contract(self):
        from cad_draft import run_draft

        source = self.work / "geometry.py"
        source.write_text('''from build123d import Box, Pos
from build_session import BuildSession
build = BuildSession(__file__, part_names=("unit-q",))
build.add("body", Box(10, 10, 10))
build.cut("pocket", Pos(0, 0, 5) * Box(2, 2, 4))
build.export()
''')
        source_bytes = source.read_bytes()
        draft = run_draft(source, workspace=self.work)
        self.assertEqual((draft["status"], draft["deliveryReady"]), ("draft", False))
        self.assertNotIn("intent", draft)
        self.assertAlmostEqual(import_step(draft["artifacts"]["step"]["path"]).volume, 992, places=5)
        self.assertFalse((self.work / "unit-q_report.json").exists())
        final = self.session("final")
        with patch.dict(os.environ, {
            "AMAGINE3D_INTENT_PATH": str(final.intent_path),
            "AMAGINE3D_OUTPUT_DIR": str(final.out_dir),
        }), redirect_stdout(io.StringIO()):
            runpy.run_path(str(source), run_name="__main__")
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertAlmostEqual(import_step(final.out_dir / "unit-q.step").volume, 992, places=5)
        self.assertTrue((final.out_dir / "unit-q_report.json").is_file())

    def test_draft_multipart_owners_and_incomplete_layout_remain_provisional(self):
        from cad_draft import run_draft

        source = self.work / "layout.py"
        source.write_text('''from build123d import Box, Pos
from build_session import BuildSession
from cad_helpers import checked_fillet
build = BuildSession(__file__, part_names=("base", "lid"))
build.add("plate", Box(20, 12, 3), part_name="base")
for index, x in enumerate((-4, 4)):
    build.cut(f"slot-{index}", Pos(x, 0, 1) * Box(2, 4, 4), part_name="base")
build.add("lid-body", Pos(0, 0, 8) * Box(20, 12, 2), part_name="lid")
build.finish("lid", lambda p: checked_fillet(p, p.edges(), .2, "lid-rounding"))
build.observe("lid-surface", part_name="lid")
build.export(draft_references={"module": Pos(0, 0, 4) * Box(4, 4, 2)})
''')
        result = run_draft(source, workspace=self.work)
        self.assertEqual(result["status"], "draft")
        self.assertEqual({o["name"] for o in result["objects"]}, {"base", "lid", "module"})
        self.assertEqual(len(import_step(result["artifacts"]["step"]["path"]).solids()), 3)
        expected = {"plate": {"owner": "base", "role": "solid"},
                    "slot-0": {"owner": "base", "role": "cutter"},
                    "slot-1": {"owner": "base", "role": "cutter"},
                    "lid-body": {"owner": "lid", "role": "solid"},
                    "lid-surface": {"owner": "lid", "role": "separate"}}
        self.assertEqual(result["constructionFeatures"], expected)
        manifest = json.loads(Path(result["geometry"]["path"]).read_text())
        self.assertEqual(manifest["constructionFeatures"], expected)
        self.assertEqual(manifest["constructionFeatureScope"], result["constructionFeatureScope"])
        self.assertIn("not intent requirements", result["constructionFeatureScope"])
        self.assertIn("finish operations and native edits are not registered", result["constructionFeatureScope"])
        self.assertNotIn("lid-rounding", result["constructionFeatures"])
        self.assertFalse(list(self.work.glob("*intent*")))
        with self.assertRaises(AuthoringError):
            BuildSession(source, part_names=("base", "lid"))
        with patch.dict(os.environ, {"AMAGINE3D_SOURCE_PHASE": "draft", "AMAGINE3D_DRAFT_DIR": str(self.work / "manual")}):
            build = BuildSession(source, part_names=("base", "lid"))
            with self.assertRaises(AuthoringError):
                build.add("ambiguous", Box(1, 1, 1))
            with self.assertRaises(AuthoringError):
                build.add("unknown", Box(1, 1, 1), part_name="other")
            self.assertFalse(build._parts)

    def test_intent_bound_source_draft_forces_isolated_output_and_final_owner_is_strict(self):
        from cad_draft import run_draft

        fixture = self.session("bound")
        source = fixture.out_dir / "build.py"
        source.write_text(f'''from build123d import Box, Pos
from build_session import BuildSession
build = BuildSession(__file__, out_dir={str(fixture.out_dir)!r}, scene_path={str(fixture.scene_path)!r})
build.add("initial-stock", Box(10, 10, 10), part_name="unit-q")
build.observe("body", role="solid")
build.cut("pocket", Pos(0, 0, 5) * Box(2, 2, 4))
build.export()
''')
        final_paths = [fixture.scene_path, fixture.out_dir / "unit-q.step", fixture.out_dir / "unit-q_report.json"]
        for p in final_paths:
            p.write_bytes(b"existing-final")
        result = run_draft(source, workspace=fixture.out_dir, intent=fixture.intent_path)
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["intent"]["path"], str(fixture.intent_path))
        self.assertNotIn("initial-stock", result["constructionFeatures"])
        self.assertTrue(all(p.read_bytes() == b"existing-final" for p in final_paths))
        self.assertFalse((Path(result["result"]).parent / "draft-scene.json").exists())
        with self.assertRaises(AuthoringError):
            BuildSession(source, intent_path=fixture.intent_path, part_names=("different",))
        with self.assertRaises(AuthoringError):
            fixture.add("body", Box(10, 10, 10), part_name="different")
        self.assertFalse(fixture._parts)

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
        for feature in ("pocket", "construction-boss"):
            build = self.session(feature)
            build.add("body", Box(10, 10, 10))
            before = deepcopy(build._evidence)
            with self.subTest(feature=feature), self.assertRaises(cad_helpers.BuildInvariantError):
                build.add(feature, Pos(30, 0, 0) * Box(2, 2, 2), part_name="unit-q")
            self.assertEvidenceEqual(build._evidence, before)
            build.add(feature, Pos(5, 0, 0) * Box(2, 2, 2), part_name="unit-q")
            self.assertAlmostEqual(build.part("unit-q").volume, 1004)
            self.assertEqual(build._evidence.events[0]["added_mm3"], 4)
            self.assertEqual(feature in build._features, feature == "pocket")

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
