# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""A colour change end to end: rgb.apply -> Client -> RemoteBackend -> the daemon's socket handler -> SdkBackend ->
the stub SDK server. Starts no OpenRGB and touches no hardware.

  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

import sdk_stub as st
from sdk_stub import u
import rgb
import rgbcore_client as rc
import rgbd


def colours(dev):
    return [(c.red, c.green, c.blue) for c in dev.colors]


def mode_colours(m):
    return [(c.red, c.green, c.blue) for c in m.colors]


def devices():
    """The stub's devices plus one with only an effect mode, and an unsized ARGB header with a Static mode."""
    per_led = u.ModeFlags.HAS_PER_LED_COLOR
    wave = st.rainbow(1)
    wave.name = "Wave"
    return st.make_devices() + [
        st.device("Cycler", u.DeviceType.COOLER, [st.rainbow(0), wave], 0, 2),
        st.device("Aura", u.DeviceType.MOTHERBOARD,
                  [st.mode(0, "Direct", per_led, u.ModeColors.PER_LED)], 0, 0, zones=[st.zone("ARGB Header", 0, 0, 120)]),
    ]


class Through:
    """Mixin: self.client talks to the stub through whatever setUp built."""

    def apply(self, **state):
        st_ = {"color": "0000FF", "mode": "static", "groups": {}, "brightness": 100}
        st_.update(state)
        self.client.update()
        return rgb.apply(self.client, st_, log=lambda msg: None)

    def test_static_colour_reaches_every_kind_of_device(self):
        self.assertEqual(self.apply(color="FF8000"), 0)
        d = self.srv.devices
        self.assertEqual(mode_colours(d[0].modes[1]), [(255, 128, 0)])  # Static, mode colour
        self.assertEqual(d[0].active_mode, 1)
        self.assertEqual(colours(d[1]), [(255, 128, 0)] * 4)  # Direct, per LED
        self.assertEqual(d[2].active_mode, 0)  # effects only: left alone, and not a failure

    def test_group_colours_and_brightness(self):
        self.assertEqual(self.apply(color="FFFFFF", groups={"keyboard": "00FF00"}, brightness=50), 0)
        self.assertEqual(colours(self.srv.devices[1]), [(0, 128, 0)] * 4)
        self.assertEqual(mode_colours(self.srv.devices[0].modes[1]), [(128, 128, 128)])

    def test_off(self):
        self.assertEqual(self.apply(mode="off"), 0)
        d = self.srv.devices
        self.assertEqual((d[0].active_mode, colours(d[0])), (0, [(0, 0, 0)] * 3))  # Direct preferred, black
        self.assertEqual(colours(d[1]), [(0, 0, 0)] * 4)

    def test_prepare_sizes_the_header_then_apply_fills_it(self):
        with mock.patch.object(rgb, "ARGB_LEDS", 60):
            rgb.prepare(self.client)
        self.assertEqual(self.apply(color="00FFFF"), 0)
        self.assertEqual(colours(self.srv.devices[4]), [(0, 255, 255)] * 60)

    def test_whole_device_colour_uses_the_current_led_count(self):
        aura = [d for d in self.client.devices if d.name == "Aura"][0]
        self.assertEqual(aura.led_count, 0)
        self.backend.resize_zone(4, 0, 30)  # resized behind this client's back (the daemon after a restart)
        self.assertEqual(aura.set_color(rc.RGBColor(1, 2, 3)), 0)
        self.assertEqual(colours(self.srv.devices[4]), [(1, 2, 3)] * 30)

    def test_original_lighting_is_saved_once_and_restored(self):
        path = os.path.join(self.tmp.name, "original.json")
        with mock.patch.object(rgb, "ORIGINAL", path):
            with self.srv.guard:  # what the devices showed before RGB Anywhere
                self.srv.devices[0].modes[1].colors = [u.RGBColor(255, 0, 0)]
                self.srv.devices[1].colors = [u.RGBColor(0, 255, 0)] * 4
            self.assertEqual(rgb.remember_originals(self.backend), 5)
            self.assertEqual(self.apply(color="0000FF"), 0)
            self.assertEqual(colours(self.srv.devices[1]), [(0, 0, 255)] * 4)
            self.assertEqual(rgb.remember_originals(self.backend), 0)  # never overwritten with our own colours
            self.assertEqual(self.apply(mode="original"), 0)
        d = self.srv.devices
        self.assertEqual((d[0].active_mode, mode_colours(d[0].modes[1])), (1, [(255, 0, 0)]))
        self.assertEqual(colours(d[1]), [(0, 255, 0)] * 4)
        self.assertEqual(d[2].active_mode, 0)  # the effects-only fan was never touched

    def test_a_device_plugged_in_later_is_saved_before_its_first_colour(self):
        path = os.path.join(self.tmp.name, "original.json")
        with mock.patch.object(rgb, "ORIGINAL", path):
            rgb.remember_originals(self.backend)
            with self.srv.guard:
                self.srv.devices.append(st.device("Late Mouse", u.DeviceType.MOUSE, [st.mode(0, "Direct",
                                        u.ModeFlags.HAS_PER_LED_COLOR, u.ModeColors.PER_LED)], 0, 2, location="usb9"))
            self.assertEqual(rgb.remember_originals(self.backend), 1)
            self.assertEqual([s["name"] for s in rgb.load_originals()][-1], "Late Mouse")

    def test_a_blank_reading_is_never_restored(self):
        """Most controllers can't be read back: OpenRGB reports Direct or Off with every colour black. Restoring that
        switched the lights off on a real PC, so such devices are left as they are."""
        path = os.path.join(self.tmp.name, "original.json")
        with mock.patch.object(rgb, "ORIGINAL", path):
            rgb.remember_originals(self.backend)  # the stub's devices start black, as OpenRGB reports unreadable ones
            ok, unknown = rgb.restorable()
            self.assertIn("Keyboard", [s["name"] for s in unknown])
            self.assertIn("Fan", [s["name"] for s in ok])  # Rainbow takes no colour: a real reading
            self.assertEqual(self.apply(color="00FF00"), 0)
            self.assertEqual(self.apply(mode="original"), 0)
        self.assertEqual(colours(self.srv.devices[1]), [(0, 255, 0)] * 4)  # kept, not blacked out

    def test_unreadable_devices_get_their_exit_choice_or_are_handed_back(self):
        path = os.path.join(self.tmp.name, "original.json")
        with mock.patch.object(rgb, "ORIGINAL", path):
            rgb.remember_originals(self.backend)  # Board, Keyboard and Aura read back black: unreadable
            self.assertEqual(self.apply(color="00FF00"), 0)
            exit_ = {"fans": "FF0000", "keyboard": "leave"}  # Board and Aura are fans; Keyboard is handed back
            self.assertEqual(self.apply(mode="original", exit=exit_), 0)
        d = self.srv.devices
        self.assertEqual(mode_colours(d[0].modes[1]), [(255, 0, 0)])  # Board: Static, its exit colour
        self.assertEqual(colours(d[1]), [(0, 255, 0)] * 4)  # Keyboard: left as it was
        done = rgb.LAST_RESTORE
        self.assertIn("Board", done["colored"])
        self.assertEqual([h["name"] for h in done["handed_back"]], ["Keyboard"])
        self.assertIn("Fan", done["restored"])

    def test_effect_on_exit_and_the_hand_back_message(self):
        path = os.path.join(self.tmp.name, "original.json")
        with self.srv.guard:
            self.srv.devices[1].metadata.vendor = "Corsair"  # the keyboard
            self.srv.devices[1].modes.append(st.rainbow(1))
        self.client.update()
        with mock.patch.object(rgb, "ORIGINAL", path):
            rgb.remember_originals(self.backend)
            self.assertEqual(self.apply(mode="original", exit={"keyboard": "effect"}), 0)
            self.assertEqual(self.srv.devices[1].active_mode, 1)  # Rainbow
            self.assertEqual(self.apply(mode="original", exit={"all": "leave"}), 0)
        msg = rgb.handback_message()
        self.assertIn("couldn't be read", msg)
        self.assertIn("Corsair iCUE", msg)

    def test_restore_without_a_saved_copy_changes_nothing(self):
        with mock.patch.object(rgb, "ORIGINAL", os.path.join(self.tmp.name, "none.json")):
            self.assertEqual(self.apply(mode="original"), 0)
        self.assertEqual(colours(self.srv.devices[1]), [(0, 0, 0)] * 4)

    def test_a_failing_device_does_not_stop_the_others(self):
        with self.srv.guard:
            self.srv.devices[0].active_mode = 9  # no such mode: set_colors refuses this device
        self.client.update()
        with mock.patch.object(rc.Device, "set_mode", lambda dev, name: None):
            self.assertEqual(rgb.apply(self.client, {"color": "FF0000", "mode": "static", "groups": {}}, log=lambda m: None), 1)
        self.assertEqual(colours(self.srv.devices[1]), [(255, 0, 0)] * 4)


class Setup(unittest.TestCase):
    def setUp(self):
        self.srv = st.Server(devices())
        self.addCleanup(self.srv.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with mock.patch.object(rc.SdkBackend, "SETTLE", 0):
            self.backend = rc.SdkBackend(config_dir=self.tmp.name, port=self.srv.port, spawn=False)
        self.addCleanup(self.backend.shutdown)


class DirectTest(Through, Setup):
    """rgb.apply in the daemon itself (what it does on start and after a rescan)."""

    def setUp(self):
        super().setUp()
        self.client = rc.Client(self.backend)


class DaemonTest(Through, Setup):
    """rgb.apply in the app: every call goes through the daemon's socket."""

    def setUp(self):
        super().setUp()
        token = os.path.join(self.tmp.name, "rgbd.token")
        with open(token, "w") as f:
            f.write("secret-token")
        for obj, name, value in ((rgbd, "_core", self.backend), (rgbd, "_client", rc.Client(self.backend)),
                                 (rgbd, "_token", "secret-token"), (rgbd, "_start_error", None),
                                 (rgbd, "_ready", threading.Event()), (rc, "TOKEN_FILE", token)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        rgbd._ready.set()
        self.daemon = rgbd.Server(("127.0.0.1", 0), rgbd.Handler)
        threading.Thread(target=self.daemon.serve_forever, daemon=True).start()
        self.addCleanup(self.daemon.server_close)
        self.addCleanup(self.daemon.shutdown)
        self.remote = rc.RemoteBackend(port=self.daemon.server_address[1], timeout=20)
        self.client = rc.Client(self.remote)

    def test_silent_or_oversized_requests_are_dropped(self):
        import socket
        port = self.daemon.server_address[1]
        with mock.patch.object(rgbd.Handler, "timeout", 0.5), socket.create_connection(("127.0.0.1", port)) as s:
            s.settimeout(5)
            self.assertIn(b"timed out", s.recv(100))  # answered and closed once the timeout passed
        with mock.patch.object(rgbd.Handler, "MAX_LINE", 100), socket.create_connection(("127.0.0.1", port)) as s:
            s.sendall(b"x" * 500 + b"\n")
            s.settimeout(5)
            self.assertIn(b"request too long", s.recv(1000))

    def test_busy(self):
        self.assertIs(self.remote.request("busy"), False)
        with rgbd._busy:
            self.assertIs(self.remote.request("busy"), True)

    def test_bad_token_and_unknown_command(self):
        self.remote._token = "wrong"
        with self.assertRaisesRegex(RuntimeError, "bad token"):
            self.remote.request("devices")
        self.remote._token = "secret-token"
        with self.assertRaisesRegex(RuntimeError, "unknown command"):
            self.remote.request("explode")

    def test_bad_requests_are_refused_with_the_reason(self):
        with self.assertRaisesRegex(RuntimeError, "ValueError: no colours given"):
            self.remote.set_colors(1, [])
        with self.assertRaisesRegex(RuntimeError, "ValueError: no device 99"):
            self.remote.set_mode(99, "Direct")
        with self.assertRaisesRegex(RuntimeError, "LookupError"):
            self.remote.set_colors(0, [1], key=["Gone", "loc"])

    def test_openrgb_that_did_not_start(self):
        with mock.patch.object(rgbd, "_core", None), mock.patch.object(rgbd, "_client", None), \
                mock.patch.object(rgbd, "_start_error", "TimeoutError: no port"), \
                mock.patch.object(rgb, "service_state", lambda name: None):  # runs sc.exe on Windows
            self.assertEqual(self.remote.request("ping"), "OpenRGB not started")
            with self.assertRaisesRegex(RuntimeError, "OpenRGB did not start \\(TimeoutError: no port\\)"):
                self.remote.devices()
            d = self.remote.debug()
        self.assertEqual(d["core"]["start_error"], "TimeoutError: no port")
        self.assertEqual(d["devices"], [])


class DaemonPortTest(unittest.TestCase):
    """serve() when its port is taken. Server is replaced by a failing bind, so serve() can never get further."""

    def serve(self, daemon_answers):
        def taken(*a, **k):
            raise OSError(10048, "address in use")
        with mock.patch.object(rgbd, "Server", taken), mock.patch.object(rgbd, "reachable", lambda: daemon_answers), \
                mock.patch.object(rc, "SdkBackend", side_effect=AssertionError("must not start OpenRGB")), \
                self.assertLogs("rgb.rgbd", "INFO") as logs:
            rgbd.serve()
        return logs.output[0]

    def test_port_taken_by_another_program_is_reported(self):
        self.assertIn("taken by another program", self.serve(False))

    def test_running_daemon_is_left_alone(self):
        self.assertIn("already running", self.serve(True))


class RescanWaitTest(unittest.TestCase):
    def wait(self, answers, **kw):
        clock = [0.0]
        backend = mock.Mock()
        backend.request.side_effect = lambda cmd: answers.pop(0) if answers else False

        def sleep(s):
            clock[0] += s
        return rgb.wait_for_rescan(backend, sleep=sleep, clock=lambda: clock[0], **kw), clock[0]

    def test_waits_for_the_rescan_to_start_and_end(self):
        self.assertEqual(self.wait([False, False, True, True, True, False]), (True, 2.5))

    def test_gives_up_waiting_for_a_start(self):
        self.assertEqual(self.wait([False] * 100, start=3), (False, 3.5))

    def test_timeout(self):
        self.assertEqual(self.wait([True] * 1000, timeout=5)[0], False)

    def test_daemon_reports_busy(self):
        self.assertFalse(rgbd._busy.locked())
        with rgbd._busy:
            self.assertTrue(rgbd._busy.locked())


class CommandLineTest(unittest.TestCase):
    def run_cli(self, *argv):
        state = {"color": "0000FF", "mode": "static", "groups": {}, "brightness": 100}
        saved = []
        with mock.patch.object(rgb.sys, "argv", ["rgb.py", *argv]), mock.patch.object(rgb, "load_state", lambda: state), \
                mock.patch.object(rgb, "connect", lambda: "client"), mock.patch.object(rgb, "prepare", lambda c: None), \
                mock.patch.object(rgb, "apply", lambda c, s: 0), mock.patch.object(rgb, "save_state", saved.append):
            rgb._main()
        return saved[0]

    def test_colours_groups_and_brightness(self):
        s = self.run_cli("red", "breathing", "ram=00ff00", "brightness=150")
        self.assertEqual((s["color"], s["mode"], s["groups"], s["brightness"]), ("FF0000", "breathing", {"ram": "00FF00"}, 100))

    def test_bad_input_is_refused(self):
        for argv in (("fan=FF0000",), ("nocolour",), ("ram=12",), ("--bogus",)):
            with self.assertRaises((SystemExit, ValueError), msg=argv):
                self.run_cli(*argv)


class ServiceStateTest(unittest.TestCase):
    ENGLISH = """
SERVICE_NAME: PawnIO
        TYPE               : 1  KERNEL_DRIVER
        STATE              : 4  RUNNING
        WIN32_EXIT_CODE    : 0  (0x0)
"""

    def test_parse(self):
        self.assertEqual(rgb.parse_service_state(self.ENGLISH), "RUNNING")
        translated = self.ENGLISH.replace("STATE   ", "ZUSTAND ").replace("4  RUNNING", "1  STOPPED")
        self.assertEqual(rgb.parse_service_state(translated), "STOPPED")
        self.assertIsNone(rgb.parse_service_state("[SC] EnumQueryServicesStatus:OpenService FAILED 1060:\n\n"
                                                  "The specified service does not exist as an installed service.\n"))
        self.assertIsNone(rgb.parse_service_state(""))


class StartPawnioTest(unittest.TestCase):
    def run_start(self, states, rc=0):
        """start_pawnio() with sc replaced: service_state() answers from states (the last one repeats); returns
        (result, the sc commands it ran)."""
        states, ran = list(states), []

        def state(name):
            return states.pop(0) if len(states) > 1 else states[0]

        with mock.patch.object(rgb, "service_state", state), mock.patch.object(rgb.time, "sleep", lambda s: None), \
                mock.patch.object(rgb, "_quiet", lambda *cmd: ran.append(cmd) or mock.Mock(returncode=rc, stdout="", stderr="")), \
                mock.patch.object(rgb, "_log") as log:
            return rgb.start_pawnio(wait=0.05), ran, log

    def test_stopped_service_is_started(self):
        ok, ran, log = self.run_start(["STOPPED", "START_PENDING", "RUNNING"])
        self.assertTrue(ok)
        self.assertEqual(ran, [("sc", "start", "PawnIO")])
        self.assertIn("PawnIO was stopped", log.info.call_args.args[0])

    def test_running_or_missing_service_is_left_alone(self):
        for state in ("RUNNING", None):
            ok, ran, _ = self.run_start([state])
            self.assertFalse(ok)
            self.assertEqual(ran, [])

    def test_failed_start_is_reported_not_raised(self):
        ok, ran, log = self.run_start(["STOPPED"], rc=5)
        self.assertFalse(ok)
        self.assertEqual(len(ran), 1)
        log.warning.assert_called_once()


class ReviveRamTest(unittest.TestCase):
    STUCK = ("Starting DRAM enumeration (timeout=10000ms)\n"
             "Detected module [0:2]: PN=CMH16GX5M2B5200C40; proto=Bootloader; assigned slot index=2\n"
             "DRAM enumeration finished. Restored timeouts to normal\n"
             "Switched dram module [0:2] to mode: Application\n")

    def run_revive(self, icue_running=False, log_text=STUCK, wait=5):
        """switch_ram_to_application() with sc, iCUE and the Corsair log replaced. Starting iCUE makes the service
        write log_text to a new log, as it does once iCUE connects. Returns (result, sc commands, fake icue, Popen)."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        old, new = os.path.join(tmp, "cdcs-old.log"), os.path.join(tmp, "cdcs-new.log")
        with open(old, "w") as f:
            f.write("x" * 50)  # its length must not carry over: reading the new log from byte 50 misses the stuck stick
        logs = [old]

        def started(*a, **kw):
            with open(new, "w") as f:
                f.write(log_text)
            logs.append(new)
        ran = []
        icue = mock.Mock(**{"running.return_value": icue_running})
        popen = mock.Mock(side_effect=started)
        with mock.patch.dict(sys.modules, {"icue": icue}), \
                mock.patch.object(rgb, "service_state", lambda name: "STOPPED"), \
                mock.patch.object(rgb, "_icue_exe", lambda: os.path.join(tmp, "iCUE.exe")), \
                mock.patch.object(rgb, "_corsair_log", lambda: (logs[-1], os.path.getsize(logs[-1]))), \
                mock.patch.object(rgb.subprocess, "Popen", popen), mock.patch.object(rgb.time, "sleep", lambda s: None), \
                mock.patch.object(rgb, "_quiet", lambda *cmd: ran.append(cmd) or mock.Mock(returncode=0, stdout="", stderr="")):
            return rgb.switch_ram_to_application(wait=wait), ran, icue, popen

    def test_icue_is_started_for_the_service_and_ended_after_the_switch(self):
        with mock.patch.object(rgb.applog, "event") as event:
            ok, ran, icue, popen = self.run_revive()
        self.assertTrue(ok)
        self.assertEqual(ran, [("sc", "start", rgb.CORSAIR_SERVICE)])
        self.assertIn("--autorun", popen.call_args.args[0])
        icue.quit_icue.assert_called_once()
        self.assertEqual(event.call_args.kwargs["stuck"], 1)
        self.assertEqual(event.call_args.kwargs["switched"], 1)
        self.assertTrue(event.call_args.kwargs["ok"])

    def test_icue_in_use_is_left_alone(self):
        ok, ran, icue, popen = self.run_revive(icue_running=True)
        self.assertFalse(ok)
        self.assertEqual(ran, [])
        popen.assert_not_called()
        icue.quit_icue.assert_not_called()

    def test_icue_is_ended_even_when_the_sticks_never_switch(self):
        with mock.patch.object(rgb.applog, "event") as event:
            ok, _, icue, _ = self.run_revive(log_text=self.STUCK.rsplit("Switched", 1)[0], wait=0.2)
        self.assertTrue(ok)
        icue.quit_icue.assert_called_once()
        self.assertFalse(event.call_args.kwargs["ok"])


class RulesTest(unittest.TestCase):
    def dev(self, *names):
        return rc.Device(None, {"index": 0, "name": "D", "type": 0, "led_count": 1, "modes": list(names), "zones": []})

    def test_dev_mode_name(self):
        self.assertEqual(rgb.dev_mode_name(self.dev("Direct", "Static"), "static"), "static")
        self.assertEqual(rgb.dev_mode_name(self.dev("Direct", "Rainbow"), "static"), "direct")
        self.assertEqual(rgb.dev_mode_name(self.dev("Custom", "Rainbow"), "static"), "custom")
        self.assertEqual(rgb.dev_mode_name(self.dev("Rainbow", "Wave"), "static"), None)
        self.assertEqual(rgb.dev_mode_name(self.dev("Static", "Direct"), "off"), "direct")
        self.assertEqual(rgb.dev_mode_name(self.dev("Static", "Off"), "off"), "off")
        self.assertEqual(rgb.dev_mode_name(self.dev("Rainbow"), "off"), None)
        self.assertEqual(rgb.dev_mode_name(self.dev("Rainbow", "Breathing"), "breathing"), "breathing")

    def test_clean_lighting(self):
        p = rgb.clean_lighting({"name": "x" * 40, "color": "#ff0000", "groups": {"ram": "blue"}, "brightness": 300,
                                "id": "abc"})
        self.assertEqual(p, {"name": "x" * 24, "color": "FF0000", "groups": {"ram": "0000FF"}, "brightness": 100, "id": "abc"})
        self.assertNotIn("groups", rgb.clean_lighting({"color": "00FF00"}))
        for bad in (None, [], {"color": "zz"}, {}, {"color": "FF0000", "groups": {"fan": "FF0000"}},
                    {"color": "FF0000", "groups": ["ram"]}, {"color": "FF0000", "groups": {"ram": 12}},
                    {"color": "FF0000", "brightness": "lots"}):
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                rgb.clean_lighting(bad)

    def test_set_group_color(self):
        s = {"color": "0000FF", "groups": {"ram": "FF0000"}, "mode": "off"}
        self.assertEqual(rgb.set_group_color(s, "keyboard", "#00ff00"), "00FF00")
        self.assertEqual(s, {"color": "0000FF", "groups": {"ram": "FF0000", "keyboard": "00FF00"}, "mode": "static"})
        rgb.set_group_color(s, "all", "red")
        self.assertEqual((s["color"], s["groups"]), ("FF0000", {}))
        for target, colour in (("fan", "FF0000"), ("RAM", "FF0000"), (None, "FF0000"), ("all", "12345"),
                               ("all", "GGGGGG"), ("all", None), ("ram", "")):
            with self.assertRaises(ValueError, msg=repr((target, colour))):
                rgb.set_group_color(s, target, colour)
        self.assertEqual((s["color"], s["groups"]), ("FF0000", {}))


if __name__ == "__main__":
    unittest.main()
