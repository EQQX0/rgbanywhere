# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""SdkBackend against a stub OpenRGB SDK server. Starts no OpenRGB and touches no hardware.

  python -m unittest discover -s tests -v
"""
import os
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openrgb import utils as u  # noqa: E402
import rgbcore_client as rc  # noqa: E402

V = 3
BLACK = u.RGBColor(0, 0, 0)


def mode(i, name, flags, color_mode, colors=None):
    return u.ModeData(i, name, i, flags, None, None, None, None, 1 if colors else None, 1 if colors else None,
                      None, None, None, color_mode, colors)


def rainbow():
    return u.ModeData(0, "Rainbow", 0, u.ModeFlags.HAS_SPEED, 1, 5, None, None, None, None, 3, None, None, u.ModeColors.NONE, None)


def device(name, dtype, modes, active, leds):
    zone = u.ZoneData("Zone", u.ZoneType.LINEAR, 0, leds, leds, 0, 0, leds=[u.LEDData("L%d" % i, i) for i in range(leds)],
                      colors=[BLACK] * leds)
    return u.ControllerData(name, u.MetaData("Vend", "desc", "1", "SN", "loc"), dtype,
                            [u.LEDData("L%d" % i, i) for i in range(leds)], [zone], modes, [BLACK] * leds, active)


def make_devices():
    per_led, specific = u.ModeFlags.HAS_PER_LED_COLOR, u.ModeFlags.HAS_MODE_SPECIFIC_COLOR
    return [
        device("Board", u.DeviceType.MOTHERBOARD,
               [mode(0, "Direct", per_led, u.ModeColors.PER_LED), mode(1, "Static", specific, u.ModeColors.MODE_SPECIFIC, [BLACK])], 1, 3),
        device("Keyboard", u.DeviceType.KEYBOARD, [mode(0, "Direct", per_led, u.ModeColors.PER_LED)], 0, 4),
        device("Fan", u.DeviceType.COOLER, [rainbow()], 0, 2),
    ]


class Stub(socketserver.BaseRequestHandler):
    def recv(self, n):
        buf = b""
        while len(buf) < n:
            c = self.request.recv(n - len(buf))
            if not c:
                raise ConnectionError
            buf += c
        return buf

    def reply(self, dev, ptype, data):
        self.request.sendall(struct.pack("ccccIII", b"O", b"R", b"G", b"B", dev, ptype, len(data)) + data)

    def handle(self):
        devs = self.server.devices
        try:
            while True:
                h = struct.unpack("ccccIII", self.recv(16))
                dev, ptype, size = h[4:]
                body = self.recv(size) if size else b""
                if ptype == 40:
                    self.reply(0, 40, struct.pack("I", V))
                elif ptype == 0:
                    self.reply(0, 0, struct.pack("I", len(devs)))
                elif ptype == 1:
                    self.reply(dev, 1, devs[dev].pack(V))
                elif ptype == 150:
                    self.reply(0, 150, struct.pack("IH", 6, 0))
                elif ptype == 1050:
                    n = struct.unpack("H", body[4:6])[0]
                    devs[dev].colors = [u.RGBColor(*body[6 + 4 * i:9 + 4 * i]) for i in range(n)]
                    self.server.log.append(("leds", dev))
                elif ptype == 1101:
                    m = u.ModeData.unpack(iter(body[8:]), V, struct.unpack("i", body[4:8])[0])
                    devs[dev].modes[m.id] = m
                    devs[dev].active_mode = m.id
                    self.server.log.append(("mode", dev, m.name))
        except (ConnectionError, OSError):
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class SdkBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Server(("127.0.0.1", 0), Stub)
        cls.srv.devices, cls.srv.log = make_devices(), []
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        rc.SdkBackend.SETTLE = 1
        cls.tmp = tempfile.TemporaryDirectory()
        cls.b = rc.SdkBackend(config_dir=cls.tmp.name, log_level="debug", port=cls.srv.server_address[1], spawn=False)

    @classmethod
    def tearDownClass(cls):
        cls.b.shutdown()
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def test_devices_json_matches_the_old_core(self):
        d = self.b.devices()
        self.assertEqual([x["name"] for x in d], ["Board", "Keyboard", "Fan"])
        self.assertEqual(d[0]["type"], 0)
        self.assertEqual(d[0]["led_count"], 3)
        self.assertEqual(d[0]["active_mode"], 1)
        self.assertEqual(d[0]["modes"], ["Direct", "Static"])
        self.assertEqual(d[0]["mode_info"][1]["color_mode"], 2)
        self.assertEqual(d[0]["zones"][0]["leds"], 3)
        self.assertEqual(d[0]["vendor"], "Vend")

    def test_mode_specific_colour_is_pushed_as_a_mode(self):
        self.assertEqual(self.b.set_colors(0, [0x112233] * 3), 1)
        m = self.srv.devices[0].modes[1]
        self.assertEqual([tuple(c) if hasattr(c, "__iter__") else (c.red, c.green, c.blue) for c in m.colors], [(0x11, 0x22, 0x33)])

    def test_per_led_colours(self):
        self.assertEqual(self.b.set_colors(1, [0xFF0000, 0x00FF00, 0x0000FF, 0x010203]), 0)
        c = self.srv.devices[1].colors
        self.assertEqual([(x.red, x.green, x.blue) for x in c], [(255, 0, 0), (0, 255, 0), (0, 0, 255), (1, 2, 3)])

    def test_mode_without_colour(self):
        self.assertEqual(self.b.set_colors(2, [0xFFFFFF] * 2), 2)

    def test_set_mode_and_client_api(self):
        client = rc.Client(self.b)
        board = client.devices[0]
        board.set_mode("direct")
        self.assertEqual(self.srv.devices[0].active_mode, 0)
        self.assertEqual(board.set_color(rc.RGBColor(1, 2, 3)), 0)
        with self.assertRaises(ValueError):
            board.set_mode("nope")
        self.assertIn("SDK protocol", self.b.version())

    def test_debug_and_bad_device(self):
        self.assertIsNone(self.b.debug()["i2c_buses"])
        with self.assertRaises(ValueError):
            self.b.set_colors(9, [0])


if __name__ == "__main__":
    unittest.main()
