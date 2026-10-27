# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""updater.py: version compare, the source-checkout case and the checksum check, with GitHub replaced by stand-ins.
Downloads nothing and starts no Setup.

  python -m unittest discover -s tests -v
"""
import hashlib
import importlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

importlib.import_module("sdk_stub")  # puts the repo on sys.path, keeps logs out of the checkout
import updater


class Reply(io.BytesIO):
    """What urlopen hands back: a body to read, headers, and a context manager."""
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def release(version="0.3.0", digest=True):
    body = {"tag_name": "v" + version, "html_url": updater.PAGES + "tag/v" + version,
            "assets": [{"name": updater.SETUP, "browser_download_url": "https://example.invalid/setup.exe", "size": 9}]}
    if digest:
        body["assets"][0]["digest"] = "sha256:" + "ab" * 32
    return Reply(json.dumps(body).encode())


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.here = tempfile.mkdtemp()
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.here, True)
        self.addCleanup(shutil.rmtree, self.temp, True)
        patch = mock.patch.object(tempfile, "tempdir", self.temp)  # downloads and cleanup stay in a scratch folder
        patch.start()
        self.addCleanup(patch.stop)

    def installed(self, version):
        with open(os.path.join(self.here, "version.txt"), "w", encoding="ascii") as f:
            f.write(version + "\n")

    def test_versions_compare_as_numbers(self):
        self.assertGreater(updater.key("0.10.0"), updater.key("0.9.9"))
        self.assertGreater(updater.key("v1.0.0"), updater.key("0.99.0"))
        self.assertEqual(updater.key("v0.3.0"), updater.key("0.3.0"))

    def test_source_checkout_asks_nobody(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(updater.check(self.here), {"current": None, "available": False, "source": True})
        urlopen.assert_not_called()

    def test_newer_release_is_offered(self):
        self.installed("0.2.1")
        with mock.patch("urllib.request.urlopen", return_value=release("0.3.0")):
            info = updater.check(self.here)
        self.assertTrue(info["available"])
        self.assertEqual((info["current"], info["latest"]), ("0.2.1", "0.3.0"))
        self.assertEqual(info["release"]["sha256"], "ab" * 32)

    def test_same_or_older_release_is_not_offered(self):
        self.installed("0.3.0")
        for v in ("0.3.0", "0.2.9"):
            with mock.patch("urllib.request.urlopen", return_value=release(v)):
                self.assertFalse(updater.check(self.here)["available"], v)

    def test_release_without_checksum_is_not_offered(self):
        self.installed("0.2.1")
        with mock.patch("urllib.request.urlopen", return_value=release("0.3.0", digest=False)):
            info = updater.check(self.here)
        self.assertFalse(info["available"])
        with self.assertRaises(ValueError):
            updater.download(info["release"])

    def test_matching_download_is_kept(self):
        body = b"setup exe"
        rel = {"version": "0.3.0", "url": "https://example.invalid/setup.exe", "sha256": hashlib.sha256(body).hexdigest()}
        seen = []
        with mock.patch("urllib.request.urlopen", return_value=Reply(body)):
            path = updater.download(rel, seen.append)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), body)
        self.assertTrue(path.startswith(self.temp))
        self.assertLessEqual(max(seen), 99)  # 100 is left for Setup actually starting

    def test_mismatched_download_is_refused_and_removed(self):
        rel = {"version": "0.3.0", "url": "https://example.invalid/setup.exe", "sha256": "00" * 32}
        with mock.patch("urllib.request.urlopen", return_value=Reply(b"not what was published")):
            with self.assertRaisesRegex(ValueError, "checksum"):
                updater.download(rel)
        left = [f for _, _, files in os.walk(self.temp) for f in files]
        self.assertEqual(left, [])

    def test_earlier_downloads_are_cleaned_up(self):
        old = tempfile.mkdtemp(prefix=updater.PREFIX)
        other = tempfile.mkdtemp(prefix="someone-else-")
        updater.cleanup()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(other))


if __name__ == "__main__":
    unittest.main()
