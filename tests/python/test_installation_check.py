from pathlib import Path
import json
import sys
import tempfile
import unittest

from build123d import Align, Box, Pos, Rot

SKILL = Path(__file__).resolve().parents[2] / "skills" / "text-a3d"
sys.path.insert(0, str(SKILL))
from installation_check import check_installation, InstallationCheckError
from geometry_binding import shape_to_mesh


def box(w, d, h, z=0):
    return Pos(0, 0, z) * Box(w, d, h, align=(Align.CENTER, Align.CENTER, Align.MIN))


class InstallationTests(unittest.TestCase):
    def test_supported_retained_board_and_insertion_work_in_rotated_frames(self):
        for rotation, axis in ((Rot(), (0, 0, 1)), (Rot(Y=90), (1, 0, 0))):
            for mesh in (False, True):
                with self.subTest(axis=axis, mesh=mesh):
                    def geometry(shape):
                        shape = rotation * shape
                        return shape_to_mesh(shape, "test") if mesh else shape
                    board = geometry(box(20, 30, 1.6, 2))
                    seat = geometry(box(24, 34, 2))
                    lid = geometry(box(24, 34, 2, 3.9))
                    result = check_installation(board, {"seat": seat},
                        insertion_envelope=geometry(box(20, 30, 30, 2)),
                        supports={"seat": seat}, retainers={"lid": lid},
                        withdrawal_axis=axis, free_travel_mm=0.29, stop_travel_mm=0.35)
                    self.assertTrue(result["pass"])

    def test_blocked_path_and_floating_seat_are_distinct_failures(self):
        board = box(20, 30, 1.6, 2)
        overhead = box(24, 34, 2, 8)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            with self.assertRaises(InstallationCheckError):
                check_installation(board, {"overhead": overhead},
                    insertion_envelope=box(20, 30, 20, 2),
                    supports={"floating-seat": box(24, 34, 1)}, out_path=output)
            evidence = json.loads(output.read_text())
            failed = {item["id"] for item in evidence["checks"] if not item["pass"]}
            self.assertEqual(failed, {"insertion:overhead", "support:floating-seat"})

    def test_real_clash_fails_but_omitted_checks_do_not_become_requirements(self):
        board = box(20, 30, 1.6, 2)
        self.assertTrue(check_installation(board, {"floor": box(30, 40, 2)})["pass"])
        with self.assertRaises(InstallationCheckError):
            check_installation(board, {"obstruction": box(4, 4, 3)})

    def test_missing_geometry_or_retention_targets_are_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            check_installation(box(2, 2, 2), {})
        with self.assertRaises(ValueError):
            check_installation(box(2, 2, 2), {}, retainers={"lid": box(4, 4, 1, 3)})


if __name__ == "__main__":
    unittest.main()
