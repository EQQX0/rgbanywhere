# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""The debug report (applog.build_report) in a temporary folder.

  python -m unittest discover -s tests -v
"""
import json
import logging
import os
import tempfile
import importlib
import unittest
import zipfile
from unittest import mock

importlib.import_module("sdk_stub")  # puts the repo on sys.path, keeps logs out of the checkout
import applog


class ReportTest(unittest.TestCase):
    def test_report_has_logs_openrgb_files_and_no_phone_token(self):
        with tempfile.TemporaryDirectory() as here:
            logs = os.path.join(here, "logs")
            core = os.path.join(here, "cache", "core")
            os.makedirs(os.path.join(core, "logs"))
            os.makedirs(logs)
            with open(os.path.join(logs, "rgb.log"), "w") as f:
                f.write("hello\n")
            for i in range(5):
                with open(os.path.join(core, "logs", "OpenRGB_2026102%d_100000.log" % i), "w") as f:
                    f.write("log %d\n" % i)
            for name in ("OpenRGB.json", "Configuration.json"):
                with open(os.path.join(core, name), "w") as f:
                    f.write("{}")
            with open(os.path.join(here, "state.json"), "w") as f:
                json.dump({"color": "FF0000", "phone_token": "secret-token-123"}, f)
            with mock.patch.object(applog, "HERE", here), mock.patch.object(applog, "LOG_DIR", logs), \
                    mock.patch.object(applog, "FAILED_DIR", os.path.join(logs, "failed")):
                path = applog.build_report("diagnosis text")
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                settings = z.read("settings.json").decode()
        self.assertIn("logs/rgb.log", names)
        self.assertEqual(sorted(n for n in names if n.startswith("core-logs/")),
                         ["core-logs/OpenRGB_20261022_100000.log", "core-logs/OpenRGB_20261023_100000.log",
                          "core-logs/OpenRGB_20261024_100000.log"])
        self.assertTrue({"core-config/OpenRGB.json", "core-config/Configuration.json", "diagnosis.txt"} <= names)
        self.assertNotIn("secret-token-123", settings)


class RedactTest(unittest.TestCase):
    def test_token_in_a_traceback_is_masked(self):
        logs = tempfile.TemporaryDirectory()
        self.addCleanup(logs.cleanup)
        h = logging.FileHandler(os.path.join(logs.name, "t.log"), encoding="utf-8")
        h.addFilter(applog._Redact())
        h.setFormatter(logging.Formatter("%(message)s"))
        lg = logging.getLogger("rgb.test_redact")
        lg.propagate = False
        lg.addHandler(h)
        self.addCleanup(lg.removeHandler, h)
        self.addCleanup(h.close)
        try:
            raise OSError("GET http://192.168.1.5:47822/?t=abcdefgh12345678 failed")
        except OSError:
            lg.exception("phone request failed")
        h.flush()
        with open(os.path.join(logs.name, "t.log"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("phone request failed", text)
        self.assertIn("OSError", text)
        self.assertNotIn("abcdefgh12345678", text)


if __name__ == "__main__":
    unittest.main()
