from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from tbdaq.adapters.endaq import EndaqAdapter, _format_bytes
from tbdaq.config import EndaqConfig


def subprocess_completed(returncode: int, stdout: str, stderr: str):
    return CompletedProcess([], returncode, stdout, stderr)


class _FakeCommand:
    def __init__(self, *, acknowledgement: bool = False, dismounted: bool = True):
        self.acknowledgement = acknowledgement
        self.dismounted = dismounted

    def startRecording(self, **_kwargs):
        return self.acknowledgement

    def awaitReboot(self, **_kwargs):
        return self.dismounted


class _FakeDevice:
    def __init__(self, command: _FakeCommand):
        self.command = command


class EndaqAdapterTests(unittest.TestCase):
    def test_format_bytes_includes_grouped_exact_value_and_gib(self) -> None:
        self.assertEqual(
            _format_bytes(7_585_497_088),
            "7,585,497,088 bytes (7.06 GiB)",
        )

    def test_dismount_proves_start_even_when_library_returns_false(self) -> None:
        adapter = EndaqAdapter(EndaqConfig(enabled=True))
        adapter._device = _FakeDevice(_FakeCommand(acknowledgement=False))
        with (
            patch.object(adapter, "_refresh_mounted_device"),
            patch.object(adapter, "_check_storage_capacity"),
            patch.object(adapter, "_list_ide_files", return_value=[]),
            patch.object(adapter, "_clean_unmount", return_value="/dev/fake"),
            patch.object(adapter, "_await_block_disconnect"),
        ):
            result = adapter.start()
        self.assertTrue(result.ok)
        self.assertFalse(result.start_library_acknowledged)
        self.assertTrue(adapter.needs_stop)
        self.assertIn("clean_unmount", result.phase_timings_s)

    def test_clean_unmount_flushes_and_unmounts_mount_path(self) -> None:
        adapter = EndaqAdapter(EndaqConfig(enabled=True, mount_path="/mnt/endaq"))
        adapter._mount_path = "/mnt/endaq"
        findmnt = subprocess_completed(0, "/dev/sda1 vfat\n", "")
        umount = subprocess_completed(0, "", "")
        with (
            patch("tbdaq.adapters.endaq.os.sync") as sync,
            patch("tbdaq.adapters.endaq.subprocess.run", side_effect=[findmnt, umount]) as run,
            patch("tbdaq.adapters.endaq.os.path.realpath", return_value="/dev/sda1"),
        ):
            source = adapter._clean_unmount()
        self.assertEqual(source, "/dev/sda1")
        sync.assert_called_once_with()
        self.assertEqual(run.call_args_list[1].args[0], ["umount", "/mnt/endaq"])

    def test_clean_unmount_failure_is_actionable(self) -> None:
        adapter = EndaqAdapter(EndaqConfig(enabled=True, mount_path="/mnt/endaq"))
        adapter._mount_path = "/mnt/endaq"
        findmnt = subprocess_completed(0, "/dev/sda1 vfat\n", "")
        umount = subprocess_completed(32, "", "must be superuser")
        with (
            patch("tbdaq.adapters.endaq.os.sync"),
            patch("tbdaq.adapters.endaq.subprocess.run", side_effect=[findmnt, umount]),
            patch("tbdaq.adapters.endaq.os.path.realpath", return_value="/dev/sda1"),
            self.assertRaisesRegex(RuntimeError, "fstab 'users' option"),
        ):
            adapter._clean_unmount()

    def test_offload_preserves_and_verifies_all_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recorder = root / "recorder"
            output = root / "output"
            recorder.mkdir()
            old = recorder / "old.IDE"
            first = recorder / "new1.IDE"
            second = recorder / "new2.IDE"
            old.write_bytes(b"old")
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            adapter = EndaqAdapter(EndaqConfig(enabled=True))
            adapter._data_dir = str(recorder)
            adapter._files_before_start = {str(old)}
            adapter._offload(str(output))

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertEqual(len(adapter.result.ide_paths), 2)
            self.assertEqual(len(adapter.result.ide_sha256_by_path), 2)
            self.assertTrue(adapter.result.multiple_new_ide_files)
            self.assertEqual((output / "new1.IDE").read_bytes(), b"first")
            self.assertEqual((output / "new2.IDE").read_bytes(), b"second")

    def test_verified_delete_policy_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recorder = root / "recorder"
            recorder.mkdir()
            source = recorder / "new.IDE"
            source.write_bytes(b"recording")
            adapter = EndaqAdapter(
                EndaqConfig(enabled=True, delete_after_verified_offload=True)
            )
            adapter._data_dir = str(recorder)
            adapter._offload(str(root / "output"))
            self.assertFalse(source.exists())
            self.assertEqual(adapter.result.recorder_files_deleted, [str(source)])

if __name__ == "__main__":
    unittest.main()
