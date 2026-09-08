"""Keep the public starter executable through the real compiler, not a mock."""
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


if __name__ == "__main__":
    unittest.main()
