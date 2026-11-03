# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Bad config.json, concurrent state saves and log redaction under load, all found in review."""
import json
import logging
import os
import tempfile
import threading
import unittest
from unittest import mock

import importlib
importlib.import_module("sdk_stub")  # puts the repo on sys.path, keeps logs out of the checkout
import applog
import rgb


class ConfigLoading(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            with mock.patch.object(rgb, "CONFIG", path):
                return rgb.load_config()

    def test_anything_but_an_object_gives_defaults(self):
        for text in ("[]", "null", '"x"', "5", "{broken"):
            self.assertEqual(self.load(text), {}, text)

    def test_keys_of_the_wrong_type_are_dropped_and_the_rest_kept(self):
        cfg = self.load(json.dumps({"ram_expected": "4", "group_overrides": [1], "helper_service": 7,
                                    "core_log_level": ["x"], "argb_leds": 30, "ram_same_model": True}))
        self.assertEqual(cfg, {"argb_leds": 30, "ram_same_model": True})

    def test_good_values_pass_through(self):
        good = {"ram_expected": 4, "group_overrides": {"mouse pad": "mouse"}, "helper_service": "Svc",
                "core_log_level": "debug", "preferred_monitor": "MSI"}
        self.assertEqual(self.load(json.dumps(good)), good)


class SaveState(unittest.TestCase):
    def test_concurrent_saves_leave_one_valid_file_and_no_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            errors = []

            def work(n):
                for i in range(40):
                    try:
                        rgb.save_state({"color": "%06X" % (n * 1000 + i), "mode": "static", "pad": "x" * 2000})
                    except Exception as e:  # noqa: BLE001
                        errors.append(e)

            with mock.patch.object(rgb, "STATE", path):
                threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            self.assertEqual(errors, [])
            with open(path) as f:
                self.assertIn("color", json.load(f))
            self.assertEqual(os.listdir(d), ["state.json"])


class Redaction(unittest.TestCase):
    def test_a_failing_redaction_withholds_the_line(self):
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "token is SECRETSECRET", (), None)
        with mock.patch.object(applog, "redact", side_effect=RuntimeError("boom")):
            applog._Redact().filter(rec)
        self.assertNotIn("SECRETSECRET", rec.getMessage())

    def test_registering_secrets_while_redacting_never_raises(self):
        errors = []

        def adder():
            for i in range(400):
                applog.add_secret("secret-%08d" % i)

        with mock.patch.object(applog, "_secrets", set()):
            t = threading.Thread(target=adder)
            t.start()
            while t.is_alive():
                try:
                    applog.redact("hello secret-00000001 world")
                except RuntimeError as e:
                    errors.append(e)
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
