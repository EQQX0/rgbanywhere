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
                    mock.patch.object(applog, "DATA_DIR", here), mock.patch.object(applog, "SERVICE_LOG_DIR", logs), \
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
        self.assertIn('"color": "FF0000"', settings)
        self.assertFalse([n for n in names if n.startswith("service-logs/")])  # one folder: nothing twice

    def test_installed_report_has_the_daemons_logs_and_the_users_settings(self):
        with tempfile.TemporaryDirectory() as root:
            here, data = os.path.join(root, "pf"), os.path.join(root, "data")
            logs, service = os.path.join(data, "logs"), os.path.join(here, "logs")
            for d in (logs, service):
                os.makedirs(d)
            with open(os.path.join(service, "rgb.log"), "w") as f:
                f.write("daemon\n")
            with open(os.path.join(data, "state.json"), "w") as f:
                json.dump({"color": "00FF00"}, f)
            with mock.patch.object(applog, "HERE", here), mock.patch.object(applog, "LOG_DIR", logs), \
                    mock.patch.object(applog, "DATA_DIR", data), mock.patch.object(applog, "SERVICE_LOG_DIR", service), \
                    mock.patch.object(applog, "FAILED_DIR", os.path.join(logs, "failed")):
                path = applog.build_report()
            self.assertEqual(os.path.dirname(path), logs)  # written where the app may write
            with zipfile.ZipFile(path) as z:
                self.assertIn("service-logs/rgb.log", z.namelist())
                self.assertIn('"color": "00FF00"', z.read("settings.json").decode())


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




class FoldersTest(unittest.TestCase):
    """Where the app, the elevated daemon and a source checkout keep their files (applog.folders)."""
    ENV = {"ProgramFiles": "/pf", "LOCALAPPDATA": "/home/u/local"}

    def test_installed_in_program_files(self):
        here = "/pf/RGB Anywhere"
        app = applog.folders(here, self.ENV, is_admin=False)
        self.assertTrue(app["protected"])
        self.assertEqual(app["data"], "/home/u/local/RGB Anywhere")  # settings: the user's own folder
        self.assertEqual(app["logs"], "/home/u/local/RGB Anywhere/logs")
        daemon = applog.folders(here, self.ENV, is_admin=True)
        self.assertEqual(daemon["logs"], "/pf/RGB Anywhere/logs")  # never a folder a normal process controls
        self.assertEqual(daemon["data"], app["data"])  # it reads the same settings
        self.assertEqual(app["events"], sorted(["/home/u/local/RGB Anywhere/logs/events.jsonl",
                                                "/pf/RGB Anywhere/logs/events.jsonl"]))

    def test_source_checkout_keeps_everything_together(self):
        here = "/home/u/src/rgbanywhere"
        f = applog.folders(here, self.ENV, is_admin=True)
        self.assertFalse(f["protected"])
        self.assertEqual((f["data"], f["logs"]), (here, here + "/logs"))
        self.assertEqual(applog.folders(here, self.ENV, is_admin=False)["logs"], here + "/logs")

    def test_a_folder_that_only_starts_like_program_files_is_not_protected(self):
        self.assertFalse(applog.folders("/pf-evil/RGB Anywhere", self.ENV, is_admin=True)["protected"])

    def test_overrides(self):
        f = applog.folders("/pf/RGB Anywhere", dict(self.ENV, RGB_DATA_DIR="/d", RGB_LOG_DIR="/l"), is_admin=True)
        self.assertEqual((f["data"], f["logs"], f["events"]), ("/d", "/l", ["/l/events.jsonl"]))


if __name__ == "__main__":
    unittest.main()
