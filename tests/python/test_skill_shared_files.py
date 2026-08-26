from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SINGLE = ROOT / "skills" / "text-a3d"
COLOR = ROOT / "skills" / "text-a3d-color"


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
                    f"{relative} drifted between the single- and multi-color skills",
                )


if __name__ == "__main__":
    unittest.main()
