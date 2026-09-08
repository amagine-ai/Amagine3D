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

            cli = str(ROOT / "bin" / "a3d")
            run(cli, "mark", "--mark", f".{example_name}.generation-start")
            run(cli, "profile", "--machine", "a1-mini", "--nozzle", "0.4", "--tool", "0", "--out", f"{example_name}_printer-profile.json")
            for name in (f"{example_name}_intent.py", f"{example_name}_build.py"):
                shutil.copyfile(SKILL / "examples" / name, work / name)
            run(sys.executable, f"{example_name}_intent.py")
            run(cli, "intent", f"{example_name}_intent.json")
            run(cli, "compile", f"{example_name}_scene.json", "--marker", f".{example_name}.generation-start", "--intent", f"{example_name}_intent.json", "--source", f"{example_name}_build.py", "--output-dir", ".")
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
                 "--marker", ".surface_shell.generation-start", "--intent", intent_path.name,
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


if __name__ == "__main__":
    unittest.main()
