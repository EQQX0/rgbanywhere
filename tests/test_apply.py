# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""A colour change end to end: rgb.apply -> Client -> RemoteBackend -> the daemon's socket handler -> SdkBackend ->
the stub SDK server. Starts no OpenRGB and touches no hardware.

  python -m unittest discover -s tests -v
"""
import os
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
