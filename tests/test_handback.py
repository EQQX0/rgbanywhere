# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Handing devices back to their vendor app (G HUB, iCUE, Synapse...). Starts and stops nothing: the process calls are
replaced. Touches no hardware.

  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("RGB_LOG_DIR", tempfile.mkdtemp(prefix="rgb-test-logs-"))  # keep logs out of the checkout
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import handback  # noqa: E402
import rgb  # noqa: E402


class Calls:
    def __init__(self, running=(), installed=None):
        self.running, self.installed, self.log = set(running), installed, []

    def kwargs(self):
        return {"running": lambda: self.running, "kill": lambda p: self.log.append(("kill", p)),
                "launch": lambda exe: self.log.append(("launch", exe)), "sleep": lambda s: None,
                "find": lambda app: (self.installed or {}).get(app)}


class RestartTest(unittest.TestCase):
    def test_running_apps_are_restarted_and_others_started(self):
        c = Calls(running={"lghub.exe", "lghub_agent.exe"},
                  installed={"Logitech G HUB": r"C:\LGHUB\lghub.exe", "Corsair iCUE": r"C:\Corsair\iCUE.exe"})
        done = handback.restart(["Logitech G HUB", "Corsair iCUE", "Logitech G HUB"], **c.kwargs())
        self.assertEqual(done["restarted"], ["Logitech G HUB"])
        self.assertEqual(done["started"], ["Corsair iCUE"])
        self.assertEqual(c.log, [("launch", r"C:\Corsair\iCUE.exe"), ("kill", "lghub.exe"), ("kill", "lghub_agent.exe"),
                                 ("launch", r"C:\LGHUB\lghub.exe")])

    def test_apps_it_cannot_restart_are_named_and_missing_ones_reported(self):
        c = Calls(installed={})
        done = handback.restart(["ASUS Armoury Crate", "Razer Synapse", None], **c.kwargs())
        self.assertEqual((done["manual"], done["not_installed"], c.log), (["ASUS Armoury Crate"], ["Razer Synapse"], []))
        msg = handback.summary(done)
        self.assertIn("Open ASUS Armoury Crate", msg)
        self.assertIn("Razer Synapse isn't installed", msg)

    def test_a_failure_does_not_stop_the_others(self):
        c = Calls(installed={"NZXT CAM": "cam.exe", "SteelSeries GG": "gg.exe"})
        kw = c.kwargs()

        def launch(exe):
            if exe == "cam.exe":
                raise OSError("access denied")
            c.log.append(("launch", exe))
        kw["launch"] = launch
        done = handback.restart(["NZXT CAM", "SteelSeries GG"], **kw)
        self.assertEqual((done["failed"], done["started"]), (["NZXT CAM"], ["SteelSeries GG"]))

    def test_nothing_to_hand_back(self):
        self.assertEqual(handback.summary(handback.restart([], **Calls().kwargs())), "")


class FindTest(unittest.TestCase):
    def test_registry_entry_first_then_usual_folders(self):
        entries = [("Logitech G HUB", r'"D:\Apps\LGHUB\lghub.exe",0', ""), ("Other", "x.exe", "")]
        have = {r"D:\Apps\LGHUB\lghub.exe", r"E:\Corsair\iCUE.exe"}
        self.assertEqual(handback.find_exe("Logitech G HUB", entries, have.__contains__), r"D:\Apps\LGHUB\lghub.exe")
        # icon points elsewhere: the install folder is searched for a known exe
        entries = [("iCUE", r"C:\Windows\icon.ico", r"E:\Corsair")]
        self.assertEqual(handback.find_exe("Corsair iCUE", entries, lambda p: p.lower() == r"e:\corsair\icue.exe"),
                         r"E:\Corsair\icue.exe")
        self.assertIsNone(handback.find_exe("NZXT CAM", [], lambda p: False))


class RgbTest(unittest.TestCase):
    def test_handback_apps_from_a_restore(self):
        done = {"handed_back": [{"name": "Vengeance", "app": "Corsair iCUE"}, {"name": "G502", "app": "Logitech G HUB"},
                                {"name": "DIMM 2", "app": "Corsair iCUE"}, {"name": "Strip", "app": None}]}
        self.assertEqual(rgb.handback_apps(done), ["Corsair iCUE", "Logitech G HUB"])


if __name__ == "__main__":
    unittest.main()
