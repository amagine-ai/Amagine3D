"""Keep the public starter executable through the real compiler, not a mock."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"


class PublicAuthoringExampleTests(unittest.TestCase):
    def test_example_compiles_with_measured_interface_and_five_views(self):
        temporary_root = ROOT / "workspace" / "skill-validation"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            work = Path(directory)
            env = {**os.environ, "AMAGINE3D_SKILL_DIR": str(SKILL), "PYTHONDONTWRITEBYTECODE": "1"}

            def run(*args):
                result = subprocess.run(args, cwd=work, env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-1000:])

            cli = str(ROOT / "bin" / "a3d")
            run(cli, "mark", "--mark", ".assembly.generation-start")
            run(cli, "profile", "--machine", "a1-mini", "--nozzle", "0.4", "--tool", "0", "--out", "assembly_printer-profile.json")
            for name in ("assembly_intent.py", "assembly_build.py"):
                shutil.copyfile(SKILL / "examples" / name, work / name)
            run(sys.executable, "assembly_intent.py")
            run(cli, "intent", "assembly_intent.json")
            run(cli, "compile", "assembly_scene.json", "--marker", ".assembly.generation-start", "--intent", "assembly_intent.json", "--source", "assembly_build.py", "--output-dir", ".")
            result = json.loads((work / "assembly_compile-result.json").read_text())
            self.assertTrue(result["pass"])
            self.assertEqual(result["issues"], [])
            self.assertEqual(result["status"], "awaiting-visual-review")
            render = json.loads((work / "assembly_render.json").read_text())
            self.assertEqual(render["views"], ["isometric", "front", "side", "top", "bottom"])
            self.assertEqual(render["dimensions_mm"], [30.0, 24.0, 10.0])
            for name in ("assembly.3mf", "assembly-assemble.step", "assembly-holder.stl", "assembly-pin.stl"):
                self.assertGreater((work / name).stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
