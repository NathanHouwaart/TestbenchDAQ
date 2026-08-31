from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tbdaq.adapters.endaq import EndaqAdapter
from tbdaq.config import EndaqConfig


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
    def test_dismount_proves_start_even_when_library_returns_false(self) -> None:
        adapter = EndaqAdapter(EndaqConfig(enabled=True))
        adapter._device = _FakeDevice(_FakeCommand(acknowledgement=False))
        with (
            patch.object(adapter, "_refresh_mounted_device"),
            patch.object(adapter, "_check_storage_capacity"),
            patch.object(adapter, "_list_ide_files", return_value=[]),
        ):
            result = adapter.start()
        self.assertTrue(result.ok)
        self.assertFalse(result.start_library_acknowledged)
        self.assertTrue(adapter.needs_stop)

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
