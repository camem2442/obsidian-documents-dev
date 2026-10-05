import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from _scripts_2.apps.documents.common.file_hash import clear_sha_cache, get_file_sha256


class FileHashTests(unittest.TestCase):
    def setUp(self):
        clear_sha_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(clear_sha_cache)
        self.path = Path(self.tmp.name) / "fixture.bin"

    def test_empty(self):
        self.path.write_bytes(b"")
        self.assertEqual(get_file_sha256(self.path), hashlib.sha256(b"").hexdigest())

    def test_text(self):
        data = "hello 한글\n".encode()
        self.path.write_bytes(data)
        self.assertEqual(get_file_sha256(self.path), hashlib.sha256(data).hexdigest())

    def test_binary_repeat_and_clear(self):
        data = bytes(range(256))
        self.path.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        self.assertEqual(get_file_sha256(self.path), expected)
        self.assertEqual(get_file_sha256(self.path), expected)
        clear_sha_cache()
        self.assertEqual(get_file_sha256(self.path), expected)

    def test_changed_size(self):
        self.path.write_bytes(b"a")
        old_time = self.path.stat().st_mtime_ns
        get_file_sha256(self.path)
        self.path.write_bytes(b"longer")
        os.utime(self.path, ns=(old_time, old_time))
        self.assertEqual(get_file_sha256(self.path), hashlib.sha256(b"longer").hexdigest())

    def test_missing(self):
        self.assertEqual(get_file_sha256(self.path), "")


if __name__ == "__main__":
    unittest.main()
