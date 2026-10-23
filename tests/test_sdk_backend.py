# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""SdkBackend against a stub OpenRGB SDK server. Starts no OpenRGB and touches no hardware.

  python -m unittest discover -s tests -v
"""
import tempfile
import unittest
from unittest import mock

import sdk_stub as st
from sdk_stub import u
import rgbcore_client as rc

RED, GREY = u.RGBColor(255, 0, 0), u.RGBColor(9, 9, 9)
PER_LED, SPECIFIC = u.ModeFlags.HAS_PER_LED_COLOR, u.ModeFlags.HAS_MODE_SPECIFIC_COLOR


def rgb(c):
    return (c.red, c.green, c.blue)


def edge_devices():
    """make_devices() plus the awkward cases set_colors has to survive."""
    kb = st.device("Keyboard", u.DeviceType.KEYBOARD, [st.mode(0, "Direct", PER_LED, u.ModeColors.PER_LED)], 0, 4)
    kb.colors = [GREY] * 4
    devs = st.make_devices()
    devs[1] = kb
    return devs + [
        # 3: a mode colour list shorter than the LEDs (colors_max 2, 5 LEDs)
        st.device("Strip", u.DeviceType.LEDSTRIP,
                  [st.mode(0, "Static", SPECIFIC, u.ModeColors.MODE_SPECIFIC, [st.BLACK] * 2, 1, 2)], 0, 5),
        # 4: no zones and no LEDs at all
        st.device("Empty", u.DeviceType.LIGHT, [st.mode(0, "Direct", PER_LED, u.ModeColors.PER_LED)], 0, 0, zones=[]),
        # 5: an addressable header nobody sized yet (0 LEDs, takes 0 to 120)
        st.device("Aura", u.DeviceType.MOTHERBOARD, [st.mode(0, "Direct", PER_LED, u.ModeColors.PER_LED)], 0, 0,
                  zones=[st.zone("ARGB Header", 0, 0, 120)]),
        # 6: modes as some controllers describe them: per-LED capable but set to no colour, and a colour list without
        #    the colour flag (openrgb-python's own check refuses to send that one)
        st.device("Odd", u.DeviceType.LIGHT,
                  [st.mode(0, "Glow", PER_LED, u.ModeColors.NONE),
                   st.mode(1, "Weird", u.ModeFlags(0), u.ModeColors.NONE, [RED], 1, 1)], 0, 2),
        # 7: a mode-colour mode that currently holds no colours (openrgb-python then drops colors_min/max)
        st.device("Bare", u.DeviceType.LIGHT, [st.mode(0, "Static", SPECIFIC, u.ModeColors.MODE_SPECIFIC)], 0, 2),
        # 8: a mode that needs more colours than it holds now
        st.device("Needy", u.DeviceType.LIGHT,
                  [st.mode(0, "Static", SPECIFIC, u.ModeColors.MODE_SPECIFIC, [st.BLACK], 2, 4)], 0, 2),
    ]


class SdkBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = st.Server(edge_devices())
        cls.tmp = tempfile.TemporaryDirectory()
        with mock.patch.object(rc.SdkBackend, "SETTLE", 0):
            cls.b = rc.SdkBackend(config_dir=cls.tmp.name, log_level="debug", port=cls.srv.port, spawn=False)

    @classmethod
    def tearDownClass(cls):
        cls.b.shutdown()
        cls.srv.stop()
        cls.tmp.cleanup()

    def setUp(self):
        with self.srv.guard:
            self.srv.devices, self.srv.log[:] = edge_devices(), []
        self.b.devices()

    def sent(self, kind):
        return [x for x in self.srv.log if x[0] == kind]

    def test_devices_json_matches_the_old_core(self):
        d = self.b.devices()
        self.assertEqual([x["name"] for x in d[:3]], ["Board", "Keyboard", "Fan"])
        self.assertEqual(d[0]["type"], 0)
        self.assertEqual(d[0]["led_count"], 3)
        self.assertEqual(d[0]["active_mode"], 1)
        self.assertEqual(d[0]["modes"], ["Direct", "Static"])
        self.assertEqual(d[0]["mode_info"][1]["color_mode"], 2)
        self.assertEqual(d[0]["zones"][0]["leds"], 3)
        self.assertEqual(d[0]["vendor"], "Vend")
        self.assertEqual(d[5]["zones"][0], {"name": "ARGB Header", "type": 1, "leds": 0, "leds_min": 0, "leds_max": 120})
        self.assertEqual(d[4]["zones"], [])

    def test_mode_specific_colour_is_pushed_as_a_mode(self):
        self.assertEqual(self.b.set_colors(0, [0x112233] * 3), 1)
        self.assertEqual([rgb(c) for c in self.srv.devices[0].modes[1].colors], [(0x11, 0x22, 0x33)])

    def test_mode_colours_stay_within_colors_max(self):
        self.assertEqual(self.b.set_colors(3, [0xABCDEF, 0x010101, 0x020202, 0x030303, 0x040404]), 1)
        m = self.srv.devices[3].modes[0]
        self.assertEqual([rgb(c) for c in m.colors], [(0xAB, 0xCD, 0xEF)] * 2)
        self.assertEqual((m.colors_min, m.colors_max), (1, 2))
        self.assertEqual(self.sent("leds"), [])

    def test_mode_colours_reach_colors_min(self):
        self.assertEqual(self.b.set_colors(8, [0x00FF00]), 1)
        self.assertEqual([rgb(c) for c in self.srv.devices[8].modes[0].colors], [(0, 255, 0)] * 2)

    def test_mode_colour_on_a_mode_holding_none(self):
        self.assertEqual(self.b.set_colors(7, [0x0000FF] * 2), 1)
        m = self.srv.devices[7].modes[0]
        self.assertEqual([rgb(c) for c in m.colors], [(0, 0, 255)])
        self.assertEqual((m.colors_min, m.colors_max, m.color_mode), (1, 1, u.ModeColors.MODE_SPECIFIC))

    def test_per_led_colours(self):
        self.assertEqual(self.b.set_colors(1, [0xFF0000, 0x00FF00, 0x0000FF, 0x010203]), 0)
        self.assertEqual([rgb(x) for x in self.srv.devices[1].colors], [(255, 0, 0), (0, 255, 0), (0, 0, 255), (1, 2, 3)])

    def test_fewer_colours_than_leds_overlay_the_first(self):
        self.assertEqual(self.b.set_colors(1, [0xFF0000]), 0)
        self.assertEqual([rgb(x) for x in self.srv.devices[1].colors], [(255, 0, 0)] + [(9, 9, 9)] * 3)

    def test_extra_colours_are_ignored(self):
        self.assertEqual(self.b.set_colors(1, [0x0000FF] * 9), 0)
        self.assertEqual(self.sent("leds"), [("leds", 1, 4)])

    def test_mode_without_colour(self):
        self.assertEqual(self.b.set_colors(2, [0xFFFFFF] * 2), 2)
        self.assertEqual(self.srv.log, [])

    def test_device_without_leds(self):
        self.assertEqual(self.b.set_colors(4, [0xFFFFFF]), 3)
        self.assertEqual(self.b.set_colors(5, [0xFFFFFF]), 3)
        self.assertEqual(self.sent("leds"), [])

    def test_per_led_capable_mode_is_switched_to_per_led(self):
        self.assertEqual(self.b.set_colors(6, [0x00FF00] * 2), 0)
        self.assertEqual(self.srv.devices[6].modes[0].color_mode, u.ModeColors.PER_LED)
        self.assertEqual([rgb(x) for x in self.srv.devices[6].colors], [(0, 255, 0)] * 2)

    def test_mode_openrgb_python_would_refuse(self):
        self.b.set_mode(6, "weird")
        self.assertEqual(self.srv.devices[6].active_mode, 1)
        self.assertEqual(self.b.set_colors(6, [0x00FF00]), 2)

    def test_bad_colours(self):
        for colours in ([], None, "FF0000", [-1], [0x1000000], ["FF0000"], [1.5], [True]):
            with self.assertRaises(ValueError, msg=repr(colours)):
                self.b.set_colors(1, colours)
        self.assertEqual(self.srv.log, [])

    def test_bad_device_index(self):
        for dev in (-1, 9, 99, "1", None, True, 1.0):
            with self.assertRaises(ValueError, msg=repr(dev)):
                self.b.set_colors(dev, [0])
            with self.assertRaises(ValueError, msg=repr(dev)):
                self.b.set_mode(dev, "Direct")
        self.assertEqual(self.srv.log, [])

    def test_no_active_mode(self):
        with self.srv.guard:
            self.srv.devices[1].active_mode = 5
        self.b.devices()
        with self.assertRaises(ValueError):
            self.b.set_colors(1, [0])

    def test_set_mode_and_client_api(self):
        client = rc.Client(self.b)
        board = client.devices[0]
        board.set_mode("direct")
        self.assertEqual(self.srv.devices[0].active_mode, 0)
        self.assertEqual(board.set_color(rc.RGBColor(1, 2, 3)), 0)
        with self.assertRaises(ValueError):
            board.set_mode("nope")
        with self.assertRaises(ValueError):
            self.b.set_mode(0, "nope")
        self.assertIn("SDK protocol", self.b.version())
        self.assertEqual(board.key, ["Board", "loc"])

    def test_zero_led_device_through_the_client(self):
        aura = rc.Client(self.b).devices[5]
        self.assertEqual(aura.led_count, 0)
        self.assertEqual(aura.set_color(rc.RGBColor(1, 2, 3)), 3)
        self.assertEqual((aura.zones[0].leds_min, aura.zones[0].leds_max), (0, 120))

    def test_resize_then_colour(self):
        self.b.resize_zone(5, 0, 60)
        self.assertEqual(self.b.devices()[5]["led_count"], 60)
        self.assertEqual(self.b.set_colors(5, [0x00FF00] * 60), 0)
        self.assertEqual(self.sent("leds"), [("leds", 5, 60)])

    def test_bad_resize(self):
        for zone, n in ((1, 10), (-1, 10), ("0", 10), (0, 121), (0, -1), (0, "60"), (0, None)):
            with self.assertRaises(ValueError, msg=repr((zone, n))):
                self.b.resize_zone(5, zone, n)
        with self.assertRaises(ValueError):
            self.b.resize_zone(4, 0, 10)  # no zones at all
        self.assertEqual(self.sent("resize"), [])

    def test_debug_and_bad_device(self):
        d = self.b.debug()
        self.assertIsNone(d["i2c_buses"])
        self.assertEqual(d["protocol"], 3)
        self.assertTrue(d["log_dir"].endswith("logs"))
        with self.assertRaises(ValueError):
            self.b.set_colors(99, [0])


class ModeColourCountTest(unittest.TestCase):
    def count(self, colors, lo, hi):
        return rc.mode_colour_count(u.ModeData(0, "M", 0, SPECIFIC, None, None, None, None, lo, hi, None, None, None,
                                               u.ModeColors.MODE_SPECIFIC, colors))

    def test_counts(self):
        self.assertEqual(self.count([RED], 1, 1), 1)
        self.assertEqual(self.count([RED] * 3, 1, 2), 2)
        self.assertEqual(self.count([RED], 2, 4), 2)
        self.assertEqual(self.count(None, None, None), 1)
        self.assertEqual(self.count([], 0, 0), 1)
        self.assertEqual(self.count([RED] * 7, 1, 0), 7)


if __name__ == "__main__":
    unittest.main()
