from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "text-a3d"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import cad_compile  # noqa: E402
import freshness_check  # noqa: E402


class StableFreshnessSnapshotTests(unittest.TestCase):
    def test_file_state_can_ignore_cross_api_change_time_difference(self) -> None:
        baseline = mock.Mock(
            st_mode=stat.S_IFREG,
            st_nlink=1,
            st_dev=1,
            st_ino=2,
            st_size=3,
            st_mtime_ns=4,
            st_ctime_ns=5,
        )
        path_state = mock.Mock(
            st_mode=stat.S_IFREG,
            st_nlink=1,
            st_dev=1,
            st_ino=2,
            st_size=3,
            st_mtime_ns=4,
            st_ctime_ns=6,
        )

        self.assertFalse(freshness_check._same_file_state(baseline, path_state))
        self.assertTrue(
            freshness_check._same_file_state(
                baseline,
                path_state,
                compare_change_time=False,
            )
        )

    def test_regular_file_snapshot_binds_size_mtime_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.bin"
            path.write_bytes(b"stable artifact")

            snapshot = freshness_check.stable_file_snapshot(path)

            self.assertTrue(snapshot["exists"])
            self.assertTrue(snapshot["stable"])
            self.assertEqual(snapshot["size"], path.stat().st_size)
            self.assertEqual(snapshot["mtime_ns"], path.stat().st_mtime_ns)
            self.assertRegex(str(snapshot["sha256"]), r"^[0-9a-f]{64}$")

    @unittest.skipUnless(os.name == "posix", "POSIX link semantics regression")
    def test_symlink_and_hardlink_are_not_stable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            hardlink = root / "hardlink.bin"
            symlink = root / "symlink.bin"
            source.write_bytes(b"one physical file")
            os.link(source, hardlink)
            symlink.symlink_to(source)

            self.assertFalse(
                freshness_check.stable_file_snapshot(source)["stable"]
            )
            self.assertFalse(
                freshness_check.stable_file_snapshot(symlink)["stable"]
            )
            self.assertIsNone(cad_compile._current_file_binding(hardlink))

    def test_in_place_change_with_restored_mtime_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.bin"
            original = b"original payload"
            replacement = b"changed! payload"
            self.assertEqual(len(original), len(replacement))
            path.write_bytes(original)
            original_stat = path.stat()
            original_read = freshness_check.os.read
            mutated = False

            def mutate_after_read(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                chunk = original_read(descriptor, size)
                if chunk and not mutated:
                    mutated = True
                    path.write_bytes(replacement)
                    os.utime(
                        path,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )
                return chunk

            with mock.patch.object(
                freshness_check.os,
                "read",
                side_effect=mutate_after_read,
            ):
                snapshot = freshness_check.stable_file_snapshot(path)

            self.assertTrue(mutated)
            self.assertTrue(snapshot["exists"])
            self.assertFalse(snapshot["stable"])
            self.assertIsNone(snapshot["sha256"])

    def test_windows_rechecks_digest_when_change_time_does_not_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.bin"
            original = b"original payload"
            replacement = b"changed! payload"
            path.write_bytes(original)
            original_stat = path.stat()
            original_read = freshness_check.os.read
            mutated = False

            def mutate_after_read(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                chunk = original_read(descriptor, size)
                if chunk and not mutated:
                    mutated = True
                    path.write_bytes(replacement)
                    os.utime(
                        path,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )
                return chunk

            with (
                mock.patch.object(freshness_check.os, "name", "nt"),
                mock.patch.object(
                    freshness_check.os,
                    "read",
                    side_effect=mutate_after_read,
                ),
            ):
                snapshot = freshness_check.stable_file_snapshot(path)

            self.assertTrue(mutated)
            self.assertTrue(snapshot["exists"])
            self.assertFalse(snapshot["stable"])
            self.assertIsNone(snapshot["sha256"])


if __name__ == "__main__":
    unittest.main()
