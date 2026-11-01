# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""diag.close_conflict with a fake process table. Starts and kills nothing.

  python -m unittest discover -s tests -v
"""
import importlib
import unittest
from unittest import mock

importlib.import_module("sdk_stub")  # puts the repo on sys.path
import diag  # noqa: E402


class Fake:
    """tasklist and taskkill over a set of running exe names; `stubborn` ones survive a polite close."""
    def __init__(self, running, stubborn=(), immortal=()):
        self.running, self.stubborn, self.immortal, self.calls = set(running), set(stubborn), set(immortal), []

    def run(self, cmd, **kw):
        if cmd[0] == "tasklist":
            out = "".join('"%s","1"\n' % e for e in sorted(self.running))
            return mock.Mock(stdout=out, stderr="")
        exe, force = cmd[2], "/F" in cmd
        self.calls.append((exe, force))
        if exe not in self.immortal and (force or exe not in self.stubborn):
            self.running.discard(exe)
        return mock.Mock(stdout="", stderr="")


class CloseConflictTest(unittest.TestCase):
    def close(self, fake, label="Logitech G HUB"):
        with mock.patch.object(diag.subprocess, "run", fake.run):
            return diag.close_conflict(label, sleep=lambda _: None)

    def test_it_closes_every_process_of_that_program(self):
        f = Fake({"lghub.exe", "lghub_agent.exe", "explorer.exe"})
        r = self.close(f)
        self.assertEqual((r["closed"], r["failed"]), (["lghub.exe", "lghub_agent.exe"], []))
        self.assertIn("explorer.exe", f.running)

    def test_it_asks_politely_before_forcing(self):
        f = Fake({"lghub.exe"}, stubborn={"lghub.exe"})
        self.close(f)
        self.assertEqual(f.calls, [("lghub.exe", False), ("lghub.exe", True)])

    def test_a_polite_close_is_not_forced(self):
        f = Fake({"lghub.exe"})
        self.close(f)
        self.assertEqual(f.calls, [("lghub.exe", False)])

    def test_what_cannot_be_closed_is_reported(self):
        f = Fake({"armourycrate.service.exe"}, immortal={"armourycrate.service.exe"})
        r = self.close(f, "ASUS Armoury Crate")
        self.assertEqual((r["closed"], r["failed"]), ([], ["armourycrate.service.exe"]))

    def test_openrgb_is_never_touched(self):
        f = Fake({"openrgb.exe"})
        r = self.close(f, "OpenRGB")
        self.assertEqual((r["closed"], f.calls), ([], []))


if __name__ == "__main__":
    unittest.main()
