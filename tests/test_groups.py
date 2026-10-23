# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Which group each kind of device lands in. Touches no hardware.

  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("RGB_LOG_DIR", tempfile.mkdtemp(prefix="rgb-test-logs-"))  # keep logs out of the checkout
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rgb  # noqa: E402
import rgbcore_client as rc  # noqa: E402


def dev(name, kind):
    return SimpleNamespace(name=name, type=rc.DeviceType[kind])


class GroupTest(unittest.TestCase):
    def test_every_openrgb_device_type_has_a_group(self):
        for t in rc.DeviceType:
            self.assertIn(rgb.group_of(dev("x", t.name)), rgb.GROUPS, t.name)
        self.assertEqual(set(rgb.GROUP_LABELS), set(rgb.GROUPS))

    def test_types(self):
        cases = {"DRAM": "ram", "MOTHERBOARD": "fans", "LEDSTRIP": "fans", "COOLER": "fans", "CASE": "fans",
                 "KEYBOARD": "keyboard", "KEYPAD": "keyboard", "MOUSE": "mouse", "MOUSEMAT": "mouse", "GPU": "gpu",
                 "HEADSET": "audio", "HEADSET_STAND": "audio", "SPEAKER": "audio", "MICROPHONE": "audio",
                 "GAMEPAD": "other", "MONITOR": "other", "STORAGE": "other", "LAPTOP": "other"}
        for kind, group in cases.items():
            self.assertEqual(rgb.group_of(dev("x", kind)), group, kind)

    def test_vague_types_are_grouped_by_name(self):
        self.assertEqual(rgb.group_of(dev("Razer Mouse Dock Pro", "ACCESSORY")), "mouse")
        self.assertEqual(rgb.group_of(dev("Razer Chroma Addressable RGB Controller", "UNKNOWN")), "other")
        self.assertEqual(rgb.group_of(dev("Corsair Commander Core", "UNKNOWN")), "fans")
        self.assertEqual(rgb.group_of(dev("Lian Li Uni Fan SL", "LIGHT")), "fans")
        self.assertEqual(rgb.group_of(dev("Nanoleaf Shapes", "LIGHT")), "other")
        # a clear type wins over the name: a keyboard called "...Mouse..." stays a keyboard
        self.assertEqual(rgb.group_of(dev("Mouse-shaped Keyboard", "KEYBOARD")), "keyboard")

    def test_names_alone(self):
        self.assertEqual(rgb.group_of("Corsair Vengeance RGB DDR5"), "ram")
        self.assertEqual(rgb.group_of("Logitech G502 HERO Mouse"), "mouse")
        self.assertEqual(rgb.group_of("Something"), "other")

    def test_overrides_win_but_only_with_a_real_group(self):
        with mock.patch.dict(rgb.CFG, {"group_overrides": {"Chroma Addressable": "fans", "Dock": "nonsense"}}):
            self.assertEqual(rgb.group_of(dev("Razer Chroma Addressable RGB Controller", "UNKNOWN")), "fans")
            self.assertEqual(rgb.group_of(dev("Razer Mouse Dock", "ACCESSORY")), "mouse")

    def test_vendor_apps(self):
        def d(name, vendor=""):
            return SimpleNamespace(name=name, vendor=vendor)
        self.assertEqual(rgb.vendor_app(d("Razer BlackWidow V4")), "Razer Synapse")
        self.assertEqual(rgb.vendor_app(d("Vengeance RGB DDR5", "Corsair")), "Corsair iCUE")
        self.assertEqual(rgb.vendor_app(d("ASUS B650E MAX GAMING WIFI")), "ASUS Armoury Crate")
        self.assertEqual(rgb.vendor_app(d("G502 HERO", "Logitech")), "Logitech G HUB")
        self.assertIsNone(rgb.vendor_app(d("Generic ARGB Strip")))

    def test_exit_choice(self):
        self.assertEqual(rgb.exit_choice({}, "ram"), "leave")
        self.assertEqual(rgb.exit_choice({"exit": {"all": "effect"}}, "ram"), "effect")
        self.assertEqual(rgb.exit_choice({"exit": {"all": "effect", "ram": "FF0000"}}, "ram"), "FF0000")
        self.assertEqual(rgb.exit_choice({"exit": {"ram": "nonsense"}}, "ram"), "leave")

    def test_rgbctl_offers_the_same_groups(self):
        import rgbctl
        self.assertEqual(rgbctl.GROUPS, ("all",) + rgb.GROUPS)


if __name__ == "__main__":
    unittest.main()
