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

    def test_horizontal_withdrawal_can_have_vertical_support(self):
        component = box(2, 3, 1, 1)
        floor = box(10, 10, 1)
        stop = Pos(2, 0, 1) * Box(1, 4, 2)
        result = check_installation(
            component, {"floor": floor}, supports={"floor": floor},
            retainers={"stop": stop}, withdrawal_axis=(1, 0, 0),
            support_direction=(0, 0, -1), free_travel_mm=0.4, stop_travel_mm=0.6,
        )
        self.assertTrue(result["pass"])
        self.assertEqual(result["supportDirection"], [0.0, 0.0, -1.0])

    def test_retainer_in_middle_of_free_travel_cannot_be_skipped(self):
        component = Box(0.2, 1, 1)
        stop = Pos(0.5, 0, 0) * Box(0.1, 2, 2) + Pos(2, 0, 0) * Box(0.1, 2, 2)
        for mesh in (False, True):
            with self.subTest(mesh=mesh):
                geometry = lambda shape: shape_to_mesh(shape, "test") if mesh else shape
                with self.assertRaises(InstallationCheckError) as failure:
                    check_installation(
                        geometry(component), {}, retainers={"stop": geometry(stop)},
                        withdrawal_axis=(1, 0, 0), free_travel_mm=1, stop_travel_mm=2,
                    )
                failed = {c["id"] for c in failure.exception.report["checks"] if not c["pass"]}
                self.assertEqual(failed, {"free-travel:stop"})

    def test_unrelated_or_incomplete_insertion_envelope_fails_coverage(self):
        component = box(2, 2, 2)
        floor = box(8, 8, 1, -1)
        for path in (Pos(20, 0, 0) * box(2, 2, 10), box(1, 2, 10)):
            with self.subTest(path=path.bounding_box().size.X):
                with self.assertRaises(InstallationCheckError) as failure:
                    check_installation(component, {"floor": floor}, insertion_envelope=path)
                failed = {c["id"] for c in failure.exception.report["checks"] if not c["pass"]}
                self.assertEqual(failed, {"insertion:coverage"})

    def test_continuous_sweep_preserves_concave_component_clearance(self):
        component = box(5, 5, 1) - Pos(0, 0.5, 0) * box(3, 4, 1)
        # A peg occupies the U-shaped recess throughout travel. A whole-shape
        # convex hull would fill that recess and report a false obstruction.
        retainer = box(0.5, 0.5, 4) + box(6, 6, 0.5, 2)
        result = check_installation(
            component, {}, retainers={"peg-and-stop": retainer},
            free_travel_mm=0.8, stop_travel_mm=1.1,
        )
        self.assertTrue(result["pass"])


if __name__ == "__main__":
    unittest.main()
