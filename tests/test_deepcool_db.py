# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""DeepCool's settings database (deepcool_db) and the cooler screen switches built on it. Touches no hardware.

  python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("RGB_LOG_DIR", tempfile.mkdtemp(prefix="rgb-test-logs-"))  # keep logs out of the checkout
os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="rgb-test-appdata-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cooler  # noqa: E402
import deepcool_db as db  # noqa: E402

DEVICE = "!mastercraft!abc123"
LAYOUTS = ["!l1!L1_abc123_displayConfig_0", "!l1!L1_abc123_displayConfig_1"]


def new_db():
    folder = tempfile.mkdtemp(prefix="rgb-test-db-")
    open(os.path.join(folder, "000001.log"), "wb").close()
    return folder


def seeded_db():
    folder = new_db()
    db.put(DEVICE, json.dumps({"options": {"screenStatus": 1, "screenBrightness": 30}}), folder)
    for k in LAYOUTS:
        db.put(k, json.dumps({"DataDisplay": True, "dataDisplayType": "Time"}), folder)
    return folder


class DatabaseTest(unittest.TestCase):
    def test_a_written_value_reads_back(self):
        folder = new_db()
        db.put("setting", '{"a":1}', folder)
        self.assertEqual(db.read_json("setting", folder), {"a": 1})

    def test_the_newest_write_wins(self):
        folder = new_db()
        for n in range(3):
            db.put("k", json.dumps({"n": n}), folder)
        self.assertEqual(db.read_json("k", folder), {"n": 2})

    def test_a_record_longer_than_a_block_survives(self):
        folder = new_db()
        db.put("small", "1", folder)
        big = "x" * (db.BLOCK * 2 + 123)
        db.put("big", big, folder)
        db.put("after", "2", folder)
        got = db.read_all(folder)
        self.assertEqual((got["small"], got["big"], got["after"]), ("1", big, "2"))

    def test_changing_one_field_keeps_the_rest(self):
        folder = seeded_db()
        db.set_json_field(DEVICE, ["options", "screenStatus"], 0, folder)
        self.assertEqual(db.read_json(DEVICE, folder), {"options": {"screenStatus": 0, "screenBrightness": 30}})

    def test_a_missing_key_is_an_error_not_a_blank_write(self):
        with self.assertRaises(KeyError):
            db.set_json_field("nope", ["a"], 1, new_db())

    def test_sequences_only_go_up(self):
        folder = new_db()
        with mock.patch.object(db, "_manifest_last_sequence", return_value=500):
            db.put("k", "1", folder)
        db.put("k", "2", folder)
        self.assertEqual(db.read_all(folder)["k"], "2")
        self.assertGreater(max(s for _, s, _, _ in db._batches(folder)), 500)

    def test_backup_copies_everything_but_the_lock(self):
        folder = seeded_db()
        open(os.path.join(folder, "LOCK"), "w").close()
        copy = db.backup(folder)
        self.assertEqual(sorted(os.listdir(copy)), ["000001.log"])


class ScreenSwitchTest(unittest.TestCase):
    def setUp(self):
        self.folder = seeded_db()
        for name in ("DB",):
            p = mock.patch.object(db, name, self.folder)
            p.start()
            self.addCleanup(p.stop)
        self.stop = mock.patch.object(cooler, "stop_app").start()
        self.start = mock.patch.object(cooler, "start_app").start()
        mock.patch.object(db, "backup").start()
        self.addCleanup(mock.patch.stopall)

    def test_flags_report_the_current_state(self):
        self.assertEqual(cooler.screen_flags(), {"power": True, "details": True})

    def test_power_off_and_back_on(self):
        self.assertFalse(cooler.screen_power(False))
        self.assertEqual(cooler.screen_flags()["power"], False)
        self.assertTrue(cooler.screen_power(True))
        self.assertEqual(cooler.screen_flags()["power"], True)

    def test_details_off_hides_every_layout(self):
        cooler.details(False)
        for k in LAYOUTS:
            self.assertFalse(db.read_json(k, self.folder)["DataDisplay"])
        self.assertFalse(cooler.screen_flags()["details"])

    def test_no_change_means_deepcool_is_left_running(self):
        cooler.screen_power(True)
        cooler.details(True)
        self.stop.assert_not_called()

    def test_deepcool_is_restarted_even_if_the_write_fails(self):
        with mock.patch.object(db, "set_json_field", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                cooler.screen_power(False)
        self.start.assert_called_once()

    def test_power_and_details_do_not_disturb_each_other(self):
        cooler.screen_power(False)
        self.assertTrue(cooler.screen_flags()["details"])
        cooler.details(False)
        self.assertEqual(cooler.screen_flags(), {"power": False, "details": False})


if __name__ == "__main__":
    unittest.main()
