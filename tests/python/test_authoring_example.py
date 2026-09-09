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
            self.assertEqual([intent["dimensions_mm"][axis]["value"] for axis in "xyz"], [80, 60, 16])
            module = next(feature for feature in intent["features"]
                          if feature["id"] == "module-space")
            self.assertEqual(module["part"], "frame")
            self.assertIn("50 x 30 x 5 mm", module["acceptance"])
            self.assertFalse((work / "installed_module_parameters.json").exists())

    @contextmanager
    def compile_example(self, example_name):
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
            np.testing.assert_allclose(tuple(assembly.bounding_box().size), [80, 60, 16], atol=1e-5)
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

    def test_surface_shell_recompiles_changed_walls_without_rewriting_intent(self):
        with self.compile_example("surface_shell") as work:
            intent_path = work / "surface_shell_intent.json"
            source_path = work / "surface_shell_build.py"
            scene_path = work / "surface_shell_scene.json"
            intent_hash = sha256(intent_path.read_bytes()).hexdigest()

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

            # A finishing edit changes the actual top section while every loft
            # control and target stays unchanged. The example must catch that
            # coupled change before exporting another draft as ready.
            finishing = '''    from cad_helpers import checked_fillet
    build.finish("surface-shell", lambda body: checked_fillet(
        body, [edge for edge in body.edges() if edge.bounding_box().min.Z > HEIGHT - 0.01],
        0.5, "test-rim-rounding", part_name="surface-shell"))
'''
            source = source_path.read_text()
            marker = "    # Any finishing belongs here"
            self.assertEqual(source.count(marker), 1)
            # Simultaneous shoulder drift must appear in that same feedback,
            # instead of being concealed behind the first failed top check.
            station = "(30.0, 100.0, 80.0, 14.0, 0.0, 0.0)"
            self.assertEqual(source.count(station), 1)
            source = source.replace(station, "(30.0, 100.2, 80.2, 14.0, 0.0, 0.0)")
            source_path.write_text(source.replace(marker, finishing + "\n" + marker))
            result = subprocess.run(
                [str(ROOT / "bin" / "a3d"), "draft", source_path.name, "--intent", intent_path.name],
                cwd=work, env={**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            draft = json.loads(result.stdout)
            self.assertEqual(draft["artifacts"], {})
            self.assertIn("Top outer width at z=90.0: expected 82.0, measured", draft["issues"][0]["detail"])
            self.assertIn("Envelope X: expected 100.0, measured", draft["issues"][0]["detail"])
            self.assertIn("Envelope Y: expected 80.0, measured", draft["issues"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
