# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""The diagnosis (diag.py) for the bundled-OpenRGB model, against the stub SDK server. Reads only; no OpenRGB, no hardware.

  python -m unittest discover -s tests -v
"""
import os
import tempfile
import time
import unittest
from unittest import mock

import sdk_stub as st
from sdk_stub import u
import diag
import rgb
import rgbcore_client as rc

REAL_SERVICE_EXIT_CODE = diag.service_exit_code  # Base replaces it with a stub

PER_LED = u.ModeFlags.HAS_PER_LED_COLOR
LOG = """[2026-10-23 10:00:00][Info] Starting
[2026-10-23 10:00:01][Info] [ResourceManager] Registering I2C interface: PawnIO SMBus PIIX4 Device 1022:790B Subsystem: 1043:8877
[2026-10-23 10:00:02][Warning] Could not open PawnIO
[2026-10-23 10:00:03][Error] Something failed
"""


def devices():
    dim = st.mode(0, "Static", u.ModeFlags.HAS_MODE_SPECIFIC_COLOR | u.ModeFlags.HAS_BRIGHTNESS,
                  u.ModeColors.MODE_SPECIFIC, [st.BLACK])
    dim.brightness_min, dim.brightness_max, dim.brightness = 0, 100, 0
    return st.make_devices() + [
        st.device("Aura", u.DeviceType.MOTHERBOARD, [st.mode(0, "Direct", PER_LED, u.ModeColors.PER_LED)], 0, 0,
                  zones=[st.zone("ARGB Header 1", 0, 0, 120)]),
        st.device("Dim", u.DeviceType.DRAM, [dim], 0, 2),
    ]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for obj, name, value in ((rgb, "service_state", lambda name: "RUNNING"), (rgb, "is_elevated", lambda: True),
                                 (diag, "_running_conflicts", lambda own=(): []), (diag, "HERE", self.tmp.name),
                                 (diag, "service_exit_code", lambda name: 0), (diag, "memory_integrity", lambda: False)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.core_dir = os.path.join(self.tmp.name, "openrgb")
        os.makedirs(self.core_dir)
        for f in ("OpenRGB.exe", "PawnIOLib.dll", "SmbusPIIX4.bin"):
            open(os.path.join(self.core_dir, f), "w").close()
        with open(os.path.join(self.core_dir, "fetched-from.txt"), "w") as f:
            f.write("https://example.invalid/OpenRGB_1.0.zip\r\nSHA-256 ABC\r\n")
        self.log_dir = os.path.join(self.tmp.name, "cache", "core", "logs")
        os.makedirs(self.log_dir)


class DaemonSideTest(Base):
    def setUp(self):
        super().setUp()
        self.srv = st.Server(devices())
        self.addCleanup(self.srv.stop)
        with mock.patch.object(rc.SdkBackend, "SETTLE", 0):
            self.core = rc.SdkBackend(exe=os.path.join(self.core_dir, "OpenRGB.exe"),
                                      config_dir=os.path.join(self.tmp.name, "cache", "core"), port=self.srv.port, spawn=False)
        self.addCleanup(self.core.shutdown)
        self.client = rc.Client(self.core)

    def test_report_for_a_working_daemon(self):
        old = os.path.join(self.log_dir, "OpenRGB_old.log")
        with open(old, "w") as f:
            f.write("[x][Error] old problem\n")
        os.utime(old, (time.time() - 100, time.time() - 100))
        with open(os.path.join(self.log_dir, "OpenRGB_new.log"), "w") as f:
            f.write(LOG)
        d = diag.collect(self.core, self.client)
        self.assertTrue(d["daemon"])
        self.assertTrue(d["core_log"].endswith("OpenRGB_new.log"))
        self.assertEqual(len(d["core_log_buses"]), 1)
        self.assertEqual(d["core_log_problems"][-1][-16:], "Something failed")
        t = diag.text(d)
        for want in ("Lighting daemon: pid", "running as admin", "build: https://example.invalid/OpenRGB_1.0.zip",
                     "protocol 3", "PawnIO driver: service RUNNING", "PawnIOLib.dll present, 1 module(s) SmbusPIIX4.bin",
                     "Devices: 5", "ARGB Header 1 (LINEAR, 0 LEDs, takes 0-120)", "OpenRGB log folder: " + self.log_dir,
                     "Registering I2C interface: PawnIO SMBus PIIX4"):
            self.assertIn(want, t)
        hints = "\n".join(d["hints"])
        self.assertIn("A service named PawnIO runs, but OpenRGB cannot open", hints)
        self.assertIn("Aura / ARGB Header 1 has 0 LEDs", hints)
        self.assertIn("Dim's mode 'Static' has its brightness at the minimum", hints)
        self.assertIn("Fan is in mode 'Rainbow', which takes no colour", hints)
        self.assertNotIn("not running as admin", hints)

    def test_no_logs_yet_and_not_elevated(self):
        with mock.patch.object(rgb, "is_elevated", lambda: False):
            d = diag.collect(self.core, self.client)
        self.assertIsNone(d["core_log"])
        self.assertIn("Newest OpenRGB log: none yet", diag.text(d))
        self.assertIn("NOT running as admin", diag.text(d))
        self.assertIn("not running as admin, so RAM", d["hints"][0])

    def test_device_list_failure_still_gives_a_report(self):
        with mock.patch.object(self.client, "update", side_effect=RuntimeError("OpenRGB stopped 3 times")):
            d = diag.collect(self.core, self.client)
        self.assertIn("did not answer the device list request (RuntimeError: OpenRGB stopped 3 times)", " ".join(d["hints"]))

    def test_restarts_are_reported(self):
        self.core._restarts = [time.time()]
        self.core.last_error = "OpenRGBDisconnected: "
        d = diag.collect(self.core, self.client)
        self.assertIn("stopped and was started again 1 time(s)", " ".join(d["hints"]))
        self.assertIn("last connection problem: OpenRGBDisconnected", diag.text(d))


class StartErrorTest(Base):
    def hint(self, err):
        d = diag.collect(None, None, start_error=err)
        self.assertEqual(d["devices"], [])
        self.assertIn("did not start: " + err, diag.text(d))
        return d["hints"][0]

    def test_each_start_error_gets_its_own_advice(self):
        self.assertIn("Close that program", self.hint("RuntimeError: Another program is using port 47826 on 127.0.0.1, "
                                                     "so OpenRGB cannot start its SDK server. Find it with: netstat -ano"))
        self.assertIn("exited while starting (OpenRGB exited with code 3", self.hint(
            "RuntimeError: OpenRGB exited with code 3 while starting. Its log is in x"))
        self.assertIn("did not open its SDK server on 127.0.0.1:47826 within 2 minutes", self.hint(
            "TimeoutError: OpenRGB did not open its SDK server on 127.0.0.1:47826 (nothing listening yet)"))
        self.assertIn("OpenRGB did not start: ValueError: odd.", self.hint("ValueError: odd"))
        os.remove(os.path.join(self.core_dir, "OpenRGB.exe"))
        self.assertIn("OpenRGB is missing", self.hint("FileNotFoundError: OpenRGB is missing: x"))

    def test_no_device_hint_is_not_added_on_top(self):
        d = diag.collect(None, None, start_error="TimeoutError: x")
        self.assertFalse(any("No device was found" in h for h in d["hints"]))


class LocalTest(Base):
    def test_daemon_not_reachable(self):
        with mock.patch.object(rgb, "service_state", lambda name: None):
            d = diag.collect_local("ConnectionError: refused")
        t = diag.text(d)
        self.assertIn("Lighting daemon: NOT REACHABLE (ConnectionError: refused)", t)
        self.assertIn("Devices: unknown", t)
        self.assertIn("schtasks /run /tn \"RGB Anywhere Lighting\"", d["hints"][0])
        self.assertIn("PawnIO driver is not installed", " ".join(d["hints"]))
        self.assertFalse(any("admin" in h for h in d["hints"]))  # unknown from here, so not claimed

    def stopped_pawnio(self, code, hvci):
        with mock.patch.object(rgb, "service_state", lambda name: "STOPPED"), \
                mock.patch.object(diag, "service_exit_code", lambda name: code), \
                mock.patch.object(diag, "memory_integrity", lambda: hvci):
            d = diag.collect_local("ConnectionError: refused")
        return d, " ".join(d["hints"])

    def test_pawnio_stopped_with_exit_code_31_says_rescan_first(self):
        d, hints = self.stopped_pawnio(31, True)
        self.assertIn("(last exit code 31)", hints)
        self.assertIn("press Rescan first", hints)
        self.assertIn("run 'sc start PawnIO' as admin and read the error", hints)
        self.assertIn("only turn it off", hints)  # Memory integrity is named, but not as the likely cause
        self.assertNotIn("Turn it off:", hints)
        self.assertIn("PawnIO exit code: 31; Memory integrity: on", diag.text(d))

    def test_pawnio_exit_code_31_without_memory_integrity_does_not_mention_it(self):
        for hvci in (False, None):
            _, hints = self.stopped_pawnio(31, hvci)
            self.assertIn("press Rescan first", hints)
            self.assertNotIn("Memory integrity", hints)

    def test_pawnio_marked_for_deletion(self):
        _, hints = self.stopped_pawnio(1072, True)
        self.assertIn("marked for deletion (exit code 1072)", hints)
        self.assertNotIn("Memory integrity", hints)

    def test_service_exit_code_is_read_by_shape_not_label(self):
        sc = ("SERVICE_NAME: PawnIO \n        TYPE               : 1  KERNEL_DRIVER  \n        STATE              : 1  STOPPED \n"
              "        WIN32_EXIT_CODE    : 31  (0x1f)\n        SERVICE_EXIT_CODE  : 0  (0x0)\n")
        for text, want in ((sc, 31), ("FEHLER 1060", None)):
            with mock.patch.object(diag.subprocess, "run", return_value=mock.Mock(stdout=text)):
                self.assertEqual(REAL_SERVICE_EXIT_CODE("PawnIO"), want)

    def test_rgb_diagnose_falls_back_to_the_local_report(self):
        with mock.patch.object(rgb, "daemon", side_effect=ConnectionError("no daemon")), \
                mock.patch.object(rgb.applog, "LOG_DIR", self.tmp.name), mock.patch("builtins.print") as out:
            self.assertEqual(rgb.diagnose(), 1)
        printed = "\n".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("NOT REACHABLE (ConnectionError: no daemon)", printed)
        self.assertTrue(any(f.startswith("diagnose-") for f in os.listdir(self.tmp.name)))


if __name__ == "__main__":
    unittest.main()
