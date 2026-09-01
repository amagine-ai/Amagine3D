from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SINGLE = ROOT / "skills" / "text-a3d"
COLOR = SINGLE / "color"


class SharedSkillFileTests(unittest.TestCase):
    def test_intentionally_shared_files_do_not_drift(self):
        shared = (
            "compare_silhouette.py",
            "cpu_z_buffer.py",
            "freshness_check.py",
            "reference_analyze.py",
        )
        for relative in shared:
            with self.subTest(path=relative):
                self.assertEqual(
                    (SINGLE / relative).read_bytes(),
                    (COLOR / relative).read_bytes(),
                    f"{relative} drifted between the single-material and color modes",
                )

    def test_color_runtime_is_a_namespace_not_a_competing_import_root(self):
        command = (
            "import sys; "
            f"sys.path.insert(0, {str(SINGLE)!r}); "
            "import cad_helpers; "
            "from color import cad_helpers as color_helpers; "
            "assert hasattr(cad_helpers, 'export_assembly'); "
            "assert hasattr(color_helpers, 'export_regions'); "
            "assert cad_helpers.__file__ != color_helpers.__file__"
        )
        completed = subprocess.run(
            [sys.executable, "-c", command],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
