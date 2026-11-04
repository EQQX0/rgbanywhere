# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""original.json holds the lighting from before EqRGB. A failed read must never lead to it being overwritten."""
import builtins
import json
import os
import tempfile
import unittest
from unittest import mock

import rgb


class Backend:
    def __init__(self, snaps):
        self.snaps = snaps

    def snapshot(self):
        return self.snaps


def snap(name, color):
    return {"key": [name, "usb"], "name": name, "mode": "Direct", "color": color}


class RememberOriginals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "original.json")
        self.saved = [snap("Keyboard", "FF0000")]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.saved, f)
        # the lights have since been changed by us
        self.backend = Backend([snap("Keyboard", "0000FF"), snap("Mouse", "0000FF")])

    def test_a_transient_read_error_leaves_the_saved_originals_alone(self):
        real_open = builtins.open

        def locked(file, *a, **kw):
            if file == self.path:
                raise PermissionError("locked by another process")
            return real_open(file, *a, **kw)

        with mock.patch.object(rgb, "ORIGINAL", self.path), mock.patch("builtins.open", locked):
            self.assertEqual(rgb.remember_originals(self.backend), 0)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.saved)

    def test_a_corrupt_file_is_kept_aside_not_silently_replaced(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with mock.patch.object(rgb, "ORIGINAL", self.path):
            rgb.remember_originals(self.backend)
        kept = [n for n in os.listdir(self.tmp.name) if n.startswith("original.json.corrupt")]
        self.assertEqual(len(kept), 1)
        with open(os.path.join(self.tmp.name, kept[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{not json")

    def test_a_missing_file_still_saves_everything(self):
        os.remove(self.path)
        with mock.patch.object(rgb, "ORIGINAL", self.path):
            self.assertEqual(rgb.remember_originals(self.backend), 2)


if __name__ == "__main__":
    unittest.main()
