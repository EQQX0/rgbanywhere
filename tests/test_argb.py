# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Sizing empty addressable (ARGB) headers: rgb.prepare against the stub SDK server. No OpenRGB, no hardware.

  python -m unittest discover -s tests -v
"""
import tempfile
import unittest
from unittest import mock

import sdk_stub as st
from sdk_stub import u
import rgb
import rgbcore_client as rc

PER_LED = u.ModeFlags.HAS_PER_LED_COLOR


def board(*zones):
    leds = sum(z.num_leds for z in zones)
    start = 0
    for z in zones:
        z.start_idx, start = start, start + z.num_leds
    d = st.device("Board", u.DeviceType.MOTHERBOARD, [st.mode(0, "Direct", PER_LED, u.ModeColors.PER_LED)], 0, leds,
                  zones=list(zones))
    d.leds = [led for z in zones for led in z.leds]
    return d


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.srv = st.Server([
            board(st.zone("Onboard", 3), st.zone("ARGB 1", 0, 0, 120), st.zone("ARGB 2", 0, 0, 40),
                  st.zone("ARGB 3", 30, 0, 120), st.zone("ARGB 4", 1, 1, 90)),
            # a resizable strip on a device grouped elsewhere still gets a size
            st.device("Strip", u.DeviceType.KEYBOARD, [st.mode(0, "Direct", PER_LED, u.ModeColors.PER_LED)], 0, 0,
                      zones=[st.zone("Strip", 0, 0, 300)]),
        ])
        self.addCleanup(self.srv.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with mock.patch.object(rc.SdkBackend, "SETTLE", 0):
            self.b = rc.SdkBackend(config_dir=self.tmp.name, port=self.srv.port, spawn=False)
        self.addCleanup(self.b.shutdown)
        self.client = rc.Client(self.b)

    def sizes(self):
        return [[len(z.leds) for z in d.zones] for d in self.srv.devices]

    def test_empty_headers_are_sized_within_their_range(self):
        with mock.patch.object(rgb, "ARGB_LEDS", 60):
            rgb.prepare(self.client)
        # onboard fixed; empty 0..120 -> 60; empty 0..40 -> 40; already sized stays; 1 LED with min 1 -> 60
        self.assertEqual(self.sizes(), [[3, 60, 40, 30, 60], [60]])
        self.assertEqual(self.client.devices[0].led_count, 3 + 60 + 40 + 30 + 60)

    def test_second_prepare_changes_nothing(self):
        rgb.prepare(self.client)
        before = len(self.srv.log)
        rgb.prepare(self.client)
        self.assertEqual(len(self.srv.log), before)

    def test_size_below_the_minimum_is_raised(self):
        with self.srv.guard:
            self.srv.devices[0].zones[1].leds_min = 8
        with mock.patch.object(rgb, "ARGB_LEDS", 1):
            rgb.prepare(self.client)
        self.assertEqual(self.sizes()[0][1], 8)

    def test_resize_failure_does_not_stop_prepare(self):
        real = rc.Zone.resize

        def flaky(zone, n):
            if zone.name == "ARGB 1":
                raise RuntimeError("boom")
            real(zone, n)
        with mock.patch.object(rc.Zone, "resize", flaky), mock.patch.object(rgb, "ARGB_LEDS", 60):
            rgb.prepare(self.client)
        self.assertEqual(self.sizes(), [[3, 0, 40, 30, 60], [60]])


class ArgbSizeTest(unittest.TestCase):
    def zone(self, leds, lo, hi, ztype=1):
        return rc.Zone(None, 0, {"name": "Z", "type": ztype, "leds": leds, "leds_min": lo, "leds_max": hi})

    def test_rules(self):
        with mock.patch.object(rgb, "ARGB_LEDS", 60):
            self.assertEqual(rgb.argb_size(self.zone(0, 0, 120)), 60)
            self.assertEqual(rgb.argb_size(self.zone(0, 0, 20)), 20)
            self.assertEqual(rgb.argb_size(self.zone(0, 0, 0)), None)  # cannot be resized
            self.assertEqual(rgb.argb_size(self.zone(5, 0, 120)), None)
            self.assertEqual(rgb.argb_size(self.zone(0, 4, 4)), None)
            # a daemon that does not send the range: the old rule (LINEAR and under 2 LEDs)
            self.assertEqual(rgb.argb_size(self.zone(0, None, None)), 60)
            self.assertEqual(rgb.argb_size(self.zone(0, None, None, ztype=0)), None)

    def test_config_value(self):
        self.assertEqual(rgb._argb_leds(30), 30)
        self.assertEqual(rgb._argb_leds("45"), 45)
        for bad in (0, -5, "abc", None, 60.5, True, [60]):
            self.assertEqual(rgb._argb_leds(bad), 60, bad)


if __name__ == "__main__":
    unittest.main()
