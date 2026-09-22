from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tbdaq.storage import StorageError, validate_output_storage


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_nfs_mount_is_accepted(self) -> None:
        mount_path = self.root.resolve()
        result = validate_output_storage(
            self.root,
            mount_lines=lambda: [
                f"42 1 0:42 / {mount_path} rw - nfs4 192.168.0.189:/data rw"
            ],
        )
        self.assertEqual(result.mode, "nfs")
        self.assertEqual(result.filesystem_type, "nfs4")

    def test_local_storage_requires_explicit_override(self) -> None:
        with self.assertRaisesRegex(StorageError, "must be an active NFS"):
            validate_output_storage(self.root, mount_lines=lambda: [])

        result = validate_output_storage(
            self.root, allow_local_output=True, mount_lines=lambda: []
        )
        self.assertEqual(result.mode, "local_override")


if __name__ == "__main__":
    unittest.main()
