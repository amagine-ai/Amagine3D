"""Keep the public starter executable through the real compiler, not a mock."""
import ast
from hashlib import sha256
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import trimesh
from build123d import import_step

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"


class PublicAuthoringExampleTests(unittest.TestCase):
    def test_installation_controls_import_without_geometry_and_do_not_rewrite_targets(self):
        temporary_root = ROOT / "workspace" / "skill-validation"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            work = Path(directory)
            env = {**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"}
            for name in ("installed_module_build.py", "installed_module_intent.py"):
                shutil.copyfile(SKILL / "examples" / name, work / name)
            source = work / "installed_module_build.py"
            source.write_text(source.read_text().replace('"width": 80.0', '"width": 79.0')
                              .replace('"module_width": 50.0', '"module_width": 49.0'))
            imported = subprocess.run([sys.executable, "-B", "-c", '''
import json, sys
from pathlib import Path
before = {p.name for p in Path('.').iterdir()}
from installed_module_build import P
assert not any(name == 'build123d' or name.startswith('build123d.') for name in sys.modules)
assert before == {p.name for p in Path('.').iterdir()}
print(json.dumps([P['width'], P['module_width']]))
'''], cwd=work, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(json.loads(imported.stdout), [79, 49])
            profile = subprocess.run(
                [str(ROOT / "bin" / "a3d"), "profile", "--machine", "a1-mini", "--nozzle", "0.4",
                 "--tool", "0", "--out", "installed_module_printer-profile.json"],
                cwd=work, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(profile.returncode, 0, profile.stdout + profile.stderr)
            generated = subprocess.run([sys.executable, "-B", "installed_module_intent.py"],
                cwd=work, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            intent = json.loads((work / "installed_module_intent.json").read_text())
            self.assertEqual([intent["dimensions_mm"][axis]["value"] for axis in "xyz"], [80, 16, 60])
            module = next(feature for feature in intent["features"]
                          if feature["id"] == "module-space")
            self.assertEqual(module["part"], "frame")
            self.assertIn("width 50 x height 30 x depth 5 mm", module["acceptance"])
            self.assertFalse((work / "installed_module_parameters.json").exists())

    @contextmanager
    def compile_example(self, example_name, *, source_changes=(), intent_changes=()):
        temporary_root = ROOT / "workspace" / "skill-validation"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            work = Path(directory)
            env = {**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"}

            def run(*args):
                result = subprocess.run(args, cwd=work, env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-1000:])
                return result

            cli = str(ROOT / "bin" / "a3d")
            for name in (f"{example_name}_intent.py", f"{example_name}_build.py"):
                shutil.copyfile(SKILL / "examples" / name, work / name)
            for suffix, changes in (("build", source_changes), ("intent", intent_changes)):
                path = work / f"{example_name}_{suffix}.py"
                text = path.read_text()
                for before, after in changes:
                    self.assertIn(before, text)
                    text = text.replace(before, after)
                path.write_text(text)
            source = work / f"{example_name}_build.py"
            source_hash = sha256(source.read_bytes()).hexdigest()
            draft = None
            if example_name in {"simple_brep", "installed_module"}:
                draft = json.loads(run(cli, "draft", source.name).stdout)
                self.assertFalse((work / f"{example_name}_intent.json").exists())
                self.assertFalse((work / f"{example_name}_printer-profile.json").exists())
                self.assertFalse((work / f"{example_name}_parameters.json").exists())
            run(cli, "profile", "--machine", "a1-mini", "--nozzle", "0.4", "--tool", "0", "--out", f"{example_name}_printer-profile.json")
            run(sys.executable, f"{example_name}_intent.py")
            intent = work / f"{example_name}_intent.json"
            intent_hash = sha256(intent.read_bytes()).hexdigest()
            run(cli, "intent", f"{example_name}_intent.json")
            if example_name == "installed_module":
                bound_draft = json.loads(run(cli, "draft", source.name, "--intent", f"{example_name}_intent.json").stdout)
                self.assertEqual(bound_draft["status"], "draft")
                self.assertEqual(bound_draft["objects"], draft["objects"])
                self.assertEqual(sha256(source.read_bytes()).hexdigest(), source_hash)
            if draft is not None:
                self.assertEqual(draft["status"], "draft")
                self.assertFalse(draft["deliveryReady"])
                self.assertFalse(list(work.glob("*_report.json")))
                self.assertFalse(list(work.glob("*_scene.json")))
            run(cli, "compile", f"{example_name}_scene.json", "--intent", f"{example_name}_intent.json", "--source", source.name, "--output-dir", ".")
            self.assertEqual(sha256(source.read_bytes()).hexdigest(), source_hash)
            self.assertEqual(sha256(intent.read_bytes()).hexdigest(), intent_hash)
            yield work

    def test_example_compiles_with_measured_interface_and_five_views(self):
        with self.compile_example("assembly") as work:
            result = json.loads((work / "assembly_compile-result.json").read_text())
            self.assertTrue(result["pass"])
            self.assertEqual(result["issues"], [])
            self.assertEqual(result["status"], "awaiting-visual-review")
            render = json.loads((work / "assembly_render.json").read_text())
            self.assertEqual(render["views"], ["isometric", "front", "side", "top", "bottom"])
            self.assertEqual(render["dimensions_mm"], [30.0, 24.0, 10.0])
            for name in ("assembly.3mf", "assembly-assemble.step", "assembly-holder.stl", "assembly-pin.stl"):
                self.assertGreater((work / name).stat().st_size, 0)

    def test_single_part_starter_exports_a_real_blind_pocket(self):
        with self.compile_example("simple_brep") as work:
            result = json.loads((work / "simple-brep_compile-result.json").read_text())
            self.assertTrue(result["pass"])
            report = json.loads((work / "simple-brep_report.json").read_text())
            self.assertEqual(report["backend"], "brep-part")
            for suffix in ("step", "stl", "3mf"):
                self.assertGreater((work / f"simple-brep.{suffix}").stat().st_size, 0)

            mesh = trimesh.load(work / "simple-brep.stl", force="mesh")
            transform = report["coordinateFrames"]["part-print"]["partTransforms"]["simple-brep"]
            mesh.apply_transform(np.linalg.inv(np.asarray(transform, dtype=float)))
            self.assertTrue(mesh.is_volume)
            self.assertEqual(len(mesh.split()), 1)
            np.testing.assert_allclose(mesh.extents, [40, 28, 10], atol=1e-5)
            # Read the exported geometry in semantic coordinates: the top is open
            # at its center, with a 4 mm floor rather than a cosmetic pocket mark.
            locations, _, _ = mesh.ray.intersects_location(
                [[0, 0, 20]], [[0, 0, -1]], multiple_hits=True
            )
            np.testing.assert_allclose(sorted(locations[:, 2]), [0, 4], atol=1e-5)

    def test_installed_module_exports_two_parts_with_installation_and_screw_proofs(self):
        with self.compile_example("installed_module") as work:
            result = json.loads((work / "installed-module_compile-result.json").read_text())
            self.assertTrue(result["pass"], result.get("issues"))
            report = json.loads((work / "installed-module_report.json").read_text())
            self.assertEqual(set(report["parts"]), {"frame", "cover"})
            assembly = import_step(work / "installed-module-assemble.step")
            self.assertTrue(assembly.is_valid)
            self.assertEqual(len(assembly.solids()), 2)
            np.testing.assert_allclose(tuple(assembly.bounding_box().size), [80, 16, 60], atol=1e-5)
            for part in ("frame", "cover"):
                solid = import_step(work / f"installed-module-{part}.step")
                self.assertTrue(solid.is_valid)
                self.assertEqual(len(solid.solids()), 1)
                mesh = trimesh.load(work / f"installed-module-{part}.stl", force="mesh")
                self.assertTrue(mesh.is_volume)
                self.assertEqual(len(mesh.split()), 1)

            def audit(name):
                record = result["artifacts"][name]
                path = Path(record["path"])
                self.assertEqual(record["sha256"], sha256(path.read_bytes()).hexdigest())
                evidence = json.loads(path.read_text())
                self.assertTrue(evidence["pass"], evidence.get("errors"))
                return evidence

            installation = audit("installationAudit")
            checks = {check["id"]: check for check in installation["checks"]}
            for name in ("insertion:frame", "support:frame", "free-travel:cover", "stop:cover",
                         "passage/clearance:obstacles:frame"):
                self.assertTrue(checks[f"module-space/{name}"]["pass"])
            assembly_audit = audit("assemblyAudit")
            interface_check = next(check for check in assembly_audit["checks"] if check["name"] == "interface_geometry")
            self.assertEqual(interface_check["status"], "pass")
            scene = json.loads((work / "installed_module_scene.json").read_text())
            fastening = next(interface for interface in scene["interfaces"] if interface["id"] == "cover-fastening")
            self.assertEqual(len(fastening["fasteners"]), 4)
            self.assertEqual([fastener["axis"]["direction"] for fastener in fastening["fasteners"]],
                             [[0, -1, 0]] * 4)
            self.assertEqual([fastener["axis"]["originMm"] for fastener in fastening["fasteners"]],
                             [[-33, 5, 7], [-33, 5, 53], [33, 5, 7], [33, 5, 53]])
            module_check = next(check for check in scene["installationChecks"]
                                if check["featureId"] == "module-space")
            self.assertEqual(module_check["withdrawalAxis"], [0, 1, 0])
            self.assertEqual(module_check["supportDirection"], [0, -1, 0])

    def test_module_resize_keeps_locator_in_material_and_screws_at_corners(self):
        # Specify the variant before any geometry runs. Its requirements remain
        # independent of the builder's construction controls and measurements.
        source_changes = (
            ('"width": 80.0, "height": 60.0, "depth": 16.0',
             '"width": 86.0, "height": 66.0, "depth": 18.0'),
            ('"module_width": 50.0, "module_height": 30.0',
             '"module_width": 54.0, "module_height": 34.0'),
        )
        intent_changes = (
            ('(80.0, 16.0, 60.0)', '(86.0, 18.0, 66.0)'),
            ('width 80 x height 60 x depth 16 mm', 'width 86 x height 66 x depth 18 mm'),
            ('width 50 x height 30 x depth 5 mm', 'width 54 x height 34 x depth 5 mm'),
        )
        with self.compile_example("installed_module", source_changes=source_changes,
                                  intent_changes=intent_changes) as work:
            result = json.loads((work / "installed-module_compile-result.json").read_text())
            self.assertTrue(result["pass"], result.get("issues"))
            assembly = import_step(work / "installed-module-assemble.step")
            self.assertTrue(assembly.is_valid)
            self.assertEqual(len(assembly.solids()), 2)
            np.testing.assert_allclose(tuple(assembly.bounding_box().size), [86, 18, 66], atol=1e-5)
            report = json.loads((work / "installed-module_report.json").read_text())
            cut = next(event for event in report["events"]
                       if event["kind"] == "cut" and event["id"] == "frame-locator")
            self.assertGreater(cut["removed_mm3"], 1)
            scene = json.loads((work / "installed_module_scene.json").read_text())
            screws = next(item for item in scene["interfaces"] if item["id"] == "cover-fastening")
            self.assertEqual([item["axis"]["originMm"] for item in screws["fasteners"]],
                             [[-36, 6, 7], [-36, 6, 59], [36, 6, 7], [36, 6, 59]])
            for name in ("installationAudit", "assemblyAudit"):
                artifact = result["artifacts"][name]
                path = Path(artifact["path"])
                self.assertEqual(artifact["sha256"], sha256(path.read_bytes()).hexdigest())
                evidence = json.loads(path.read_text())
                self.assertTrue(evidence["pass"], evidence.get("errors"))

            # A stale 54 x 34 locator now lies wholly in the enlarged cavity.
            # A valid-looking cutter and matching metadata must not hide a missed cut.
            source = work / "installed_module_build.py"
            text = source.read_text()
            for before, after in (
                ('CAVITY_X[1] - CAVITY_X[0] + 2*P["locator_land"]', '54.0'),
                ('CAVITY_Z[1] - CAVITY_Z[0] + 2*P["locator_land"]', '34.0'),
            ):
                self.assertIn(before, text)
                text = text.replace(before, after)
            source.write_text(text)
            intent = work / "installed_module_intent.json"
            intent_hash = sha256(intent.read_bytes()).hexdigest()
            failed = subprocess.run(
                [str(ROOT / "bin" / "a3d"), "draft", source.name, "--intent", intent.name],
                cwd=work, env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL),
                               "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=120,
            )
            self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            diagnostic = json.loads(failed.stdout)["issues"][0]["sourceIssue"]
            self.assertEqual(diagnostic["featureId"], "frame-locator")
            self.assertEqual(diagnostic["code"], "SOURCE.CUT_MISSED_OWNER")
            self.assertAlmostEqual(diagnostic["observed"]["removedMm3"], 0)
            self.assertEqual(sha256(intent.read_bytes()).hexdigest(), intent_hash)

    def test_surface_shell_recompiles_changed_walls_without_rewriting_intent(self):
        with self.compile_example("surface_shell") as work:
            intent_path = work / "surface_shell_intent.json"
            source_path = work / "surface_shell_build.py"
            scene_path = work / "surface_shell_scene.json"
            intent_hash = sha256(intent_path.read_bytes()).hexdigest()
            intent = json.loads(intent_path.read_text())
            top_width = intent["features"][0]["section_dimensions"][0]["outer_envelope"]["width_u_mm"]
            for dimension in (*intent["dimensions_mm"].values(), top_width):
                self.assertEqual(dimension["constraint"]["kind"], "range")
                self.assertAlmostEqual(dimension["constraint"]["min_mm"], dimension["value"] - 0.1)
                self.assertAlmostEqual(dimension["constraint"]["max_mm"], dimension["value"] + 0.1)

            def read_and_measure(inset):
                result = json.loads((work / "surface-shell_compile-result.json").read_text())
                self.assertTrue(result["pass"], result.get("issues"))
                report_path = work / "surface-shell_report.json"
                report = json.loads(report_path.read_text())
                self.assertEqual(report["backend"], "brep-part")
                report_hash = sha256(report_path.read_bytes()).hexdigest()
                self.assertEqual(result["artifacts"]["buildReport"]["sha256"], report_hash)
                scene = json.loads(scene_path.read_text())
                self.assertEqual(sha256(intent_path.read_bytes()).hexdigest(), intent_hash)
                self.assertEqual(scene["intentRef"]["sha256"], intent_hash)
                for name, path in (("intent", intent_path), ("source", source_path), ("scene", scene_path)):
                    self.assertEqual(Path(report["inputs"][name]["path"]).resolve(), path.resolve())
                    self.assertEqual(report["inputs"][name]["sha256"], sha256(path.read_bytes()).hexdigest())

                exports = {}
                for kind in ("step", "stl"):
                    record = report["artifacts"][f"{kind}:surface-shell"]
                    path = Path(record["path"])
                    if not path.is_absolute():
                        path = work / path
                    self.assertEqual(record["sha256"], sha256(path.read_bytes()).hexdigest())
                    exports[kind] = path
                audit_record = result["artifacts"]["stepAudit:surface-shell"]
                audit_path = Path(audit_record["path"])
                self.assertEqual(audit_record["sha256"], sha256(audit_path.read_bytes()).hexdigest())
                audit = json.loads(audit_path.read_text())
                self.assertEqual(audit["step"]["sha256"], sha256(exports["step"].read_bytes()).hexdigest())
                section = next(check for check in audit["checks"]
                               if check["name"] == "section:shell-surface:0:width_u_mm")
                self.assertTrue(section["pass"], section)
                self.assertEqual(section["expected"]["value_mm"], 82)
                self.assertAlmostEqual(section["observed"]["actual_mm"], 82, places=4)
                solid = import_step(exports["step"])
                self.assertTrue(solid.is_valid)
                self.assertEqual(len(solid.solids()), 1)
                bounds = solid.bounding_box()
                np.testing.assert_allclose(tuple(bounds.size), [100, 80, 90], atol=1e-5)
                # Probe the imported STEP, independently of source assertions:
                # there is material below the cavity and no central top cap.
                self.assertTrue(solid.is_inside((0, 0, 1.5)))
                self.assertFalse(solid.is_inside((0, 0, 3.1)))
                self.assertFalse(solid.is_inside((2, -1, 89.9)))

                mesh = trimesh.load(exports["stl"], force="mesh")
                transform = report["coordinateFrames"]["part-print"]["partTransforms"]["surface-shell"]
                mesh.apply_transform(np.linalg.inv(np.asarray(transform, dtype=float)))
                self.assertTrue(mesh.is_volume)
                self.assertEqual(len(mesh.split()), 1)
                np.testing.assert_allclose(mesh.extents, [100, 80, 90], atol=1e-5)
                origins = [[x, y, 100] for x, y in ((0, 0), (-15, -10), (-15, 10), (15, -10), (15, 10))]
                locations, rays, _ = mesh.ray.intersects_location(origins, [[0, 0, -1]] * len(origins), multiple_hits=True)
                for ray in range(len(origins)):
                    # A broad cavity stays open to its 3 mm floor; changing the
                    # inset may shrink the rim opening, but must not close it.
                    np.testing.assert_allclose(sorted(locations[rays == ray, 2]), [0, 3], atol=1e-5)
                walls, _, _ = mesh.ray.intersects_location([[100, 0, 45]], [[-1, 0, 0]], multiple_hits=True)
                np.testing.assert_allclose(sorted(walls[:, 0]), [-50, -50 + inset, 50 - inset, 50], atol=1e-5)
                return {"volume": solid.volume, "bounds": np.array([tuple(bounds.min), tuple(bounds.max)]),
                        "runId": result["runId"], "sourceHash": report["inputs"]["source"]["sha256"],
                        "sceneHash": report["inputs"]["scene"]["sha256"], "reportHash": report_hash}

            first = read_and_measure(3.0)
            # The ordinary-process callback must rebuild actual geometry, including
            # coupling from the offset z62 profile which fixes the lower Y bound.
            callback = subprocess.run([sys.executable, "-B", "-c", '''
import json
from pathlib import Path
before = {str(p): p.read_bytes() for p in Path('.').rglob('*') if p.is_file()}
from surface_shell_build import measure_finished
actual = measure_finished([100, 79.6, 81.8]).tolist()
assert before == {str(p): p.read_bytes() for p in Path('.').rglob('*') if p.is_file()}
print(json.dumps(actual))
'''], cwd=work,
                env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONPATH": str(SKILL),
                     "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
                capture_output=True, text=True, timeout=120)
            self.assertEqual(callback.returncode, 0, callback.stdout + callback.stderr)
            np.testing.assert_allclose(json.loads(callback.stdout), [100, 79.8, 81.8], atol=1e-5)
            source = source_path.read_text()
            # Change only the named numeric parameter, preserving all other
            # source text and the already-created immutable intent document.
            values = []
            for assignment in ast.walk(ast.parse(source)):
                if not isinstance(assignment, ast.Assign):
                    continue
                for target in assignment.targets:
                    if isinstance(target, ast.Name) and target.id == "WALL_INSET":
                        values.append(assignment.value)
                    elif isinstance(target, ast.Tuple) and isinstance(assignment.value, ast.Tuple):
                        values.extend(value for name, value in zip(target.elts, assignment.value.elts)
                                      if isinstance(name, ast.Name) and name.id == "WALL_INSET")
            self.assertEqual(len(values), 1)
            value = values[0]
            self.assertIsInstance(value, ast.Constant)
            self.assertEqual(value.value, 3.0)
            lines = source.encode().splitlines(keepends=True)
            start = sum(map(len, lines[:value.lineno - 1])) + value.col_offset
            end = sum(map(len, lines[:value.end_lineno - 1])) + value.end_col_offset
            source_path.write_bytes(source.encode()[:start] + b"4.0" + source.encode()[end:])
            self.assertEqual(sha256(intent_path.read_bytes()).hexdigest(), intent_hash)
            result = subprocess.run(
                [str(ROOT / "bin" / "a3d"), "compile", scene_path.name,
                 "--intent", intent_path.name,
                 "--source", source_path.name, "--output-dir", "."],
                cwd=work, env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-1000:])
            second = read_and_measure(4.0)
            self.assertGreater(second["volume"], first["volume"] + 1.0)
            np.testing.assert_allclose(second["bounds"], first["bounds"], atol=1e-5)
            for key in ("runId", "sourceHash", "sceneHash", "reportHash"):
                self.assertNotEqual(second[key], first[key], key)

            # A finishing edit changes the actual top section while the overall
            # bounds and immutable target stay unchanged. Preview is allowed;
            # final acceptance must fail against the exported STEP itself.
            finishing = '''    from cad_helpers import checked_fillet
    build.finish("surface-shell", lambda body: checked_fillet(
        body, [edge for edge in body.edges() if edge.bounding_box().min.Z > HEIGHT - 0.01],
        0.5, "test-rim-rounding", part_name="surface-shell"))
'''
            source = source_path.read_text()
            marker = "    # Any finishing belongs here"
            self.assertEqual(source.count(marker), 1)
            source_path.write_text(source.replace(marker, finishing + "\n" + marker))
            result = subprocess.run(
                [str(ROOT / "bin" / "a3d"), "draft", source_path.name, "--intent", intent_path.name],
                cwd=work, env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            draft = json.loads(result.stdout)
            self.assertEqual(draft["status"], "draft")
            self.assertFalse(draft["deliveryReady"])
            result = subprocess.run(
                [str(ROOT / "bin" / "a3d"), "compile", scene_path.name, "--intent", intent_path.name,
                 "--source", source_path.name, "--output-dir", "."],
                cwd=work, env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            failed = json.loads((work / "surface-shell_compile-result.json").read_text())
            self.assertFalse(failed["pass"])
            audit_record = failed["artifacts"]["stepAudit:surface-shell"]
            audit_path = Path(audit_record["path"])
            self.assertEqual(audit_record["sha256"], sha256(audit_path.read_bytes()).hexdigest())
            audit = json.loads(audit_path.read_text())
            self.assertEqual(audit["step"]["sha256"], sha256((work / "surface-shell.step").read_bytes()).hexdigest())
            self.assertIn("section:shell-surface:0:width_u_mm", audit["errors"])
            section = next(check for check in audit["checks"]
                           if check["name"] == "section:shell-surface:0:width_u_mm")
            self.assertLess(section["observed"]["actual_mm"], 81.9)
            self.assertEqual(section["expected"]["value_mm"], 82)
            np.testing.assert_allclose(audit["bounds_mm"]["size"], [100, 80, 90], atol=1e-5)
            self.assertEqual(sha256(intent_path.read_bytes()).hexdigest(), intent_hash)

            # Drift outside the agreed +/-0.1 mm envelope is rejected during
            # export. The offset neighbouring profile makes the Y envelope
            # grow by only half this station's depth change.
            # Absence of a later section audit does not mean it passed.
            station = "(30.0, 100.0, 80.0, 14.0, 0.0, 0.0)"
            source = source_path.read_text()
            self.assertEqual(source.count(station), 1)
            source_path.write_text(source.replace(station, "(30.0, 100.2, 80.4, 14.0, 0.0, 0.0)"))
            result = subprocess.run(
                [str(ROOT / "bin" / "a3d"), "compile", scene_path.name, "--intent", intent_path.name,
                 "--source", source_path.name, "--output-dir", "."],
                cwd=work, env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            failed = json.loads((work / "surface-shell_compile-result.json").read_text())
            self.assertFalse(failed["pass"])
            self.assertTrue(any(issue["stage"] == "source" for issue in failed["issues"]), failed["issues"])
            details = "\n".join(issue["message"] for issue in failed["issues"])
            self.assertIn("semantic envelope dimension x differs from intent", details)
            self.assertIn("semantic envelope dimension y differs from intent", details)
            self.assertNotIn("stepAudit:surface-shell", failed["artifacts"])
            self.assertEqual(sha256(intent_path.read_bytes()).hexdigest(), intent_hash)


if __name__ == "__main__":
    unittest.main()
