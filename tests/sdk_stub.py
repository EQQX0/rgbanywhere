# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""A stub OpenRGB SDK server for the tests: answers the packets SdkBackend sends from a list of devices in memory.
Starts no OpenRGB and touches no hardware.

Failure switches on the server (each fires once and then resets):
  slow_version  answer the protocol version request 1.5 s late (openrgb-python falls back to protocol 0 after 1 s)
  cut_data      send half of the next controller data reply, then close the connection
  hang_data     never answer the next controller data request
"""
import logging
import os
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import time

# Keep the app's logs (events.jsonl, rgb.log) out of the checkout. applog reads this when it is first imported.
os.environ.setdefault("RGB_LOG_DIR", tempfile.mkdtemp(prefix="rgb-test-logs-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openrgb import utils as u  # noqa: E402

logging.getLogger("rgb").addHandler(logging.NullHandler())  # the backend's warnings are expected here, keep them quiet
V = 3
BLACK = u.RGBColor(0, 0, 0)


def mode(i, name, flags, color_mode, colors=None, cmin=None, cmax=None):
    if colors:
        cmin = 1 if cmin is None else cmin
        cmax = 1 if cmax is None else cmax
    return u.ModeData(i, name, i, flags, None, None, None, None, cmin, cmax, None, None, None, color_mode, colors)


def rainbow(i=0):
    return u.ModeData(i, "Rainbow", i, u.ModeFlags.HAS_SPEED, 1, 5, None, None, None, None, 3, None, None,
                      u.ModeColors.NONE, None)


def zone(name, leds, lo=None, hi=None, ztype=u.ZoneType.LINEAR):
    lo = leds if lo is None else lo
    hi = leds if hi is None else hi
    return u.ZoneData(name, ztype, lo, hi, leds, 0, 0, leds=[u.LEDData("L%d" % i, i) for i in range(leds)],
                      colors=[BLACK] * leds)


def device(name, dtype, modes, active, leds, zones=None, location="loc"):
    zones = [zone("Zone", leds)] if zones is None else zones
    return u.ControllerData(name, u.MetaData("Vend", "desc", "1", "SN", location), dtype,
                            [u.LEDData("L%d" % i, i) for i in range(leds)], zones, modes, [BLACK] * leds, active)


def make_devices():
    per_led, specific = u.ModeFlags.HAS_PER_LED_COLOR, u.ModeFlags.HAS_MODE_SPECIFIC_COLOR
    return [
        device("Board", u.DeviceType.MOTHERBOARD,
               [mode(0, "Direct", per_led, u.ModeColors.PER_LED),
                mode(1, "Static", specific, u.ModeColors.MODE_SPECIFIC, [BLACK])], 1, 3),
        device("Keyboard", u.DeviceType.KEYBOARD, [mode(0, "Direct", per_led, u.ModeColors.PER_LED)], 0, 4),
        device("Fan", u.DeviceType.COOLER, [rainbow()], 0, 2),
    ]


class Loose(u.ModeData):
    """Packs without openrgb-python's check, so the stub can describe modes the way some OpenRGB controllers do."""
    def validate(self, version):
        pass


def pack_device(d):
    real = d.modes
    d.modes = [Loose(*(getattr(m, f) for f in m.__dataclass_fields__)) for m in real]
    try:
        return d.pack(V)
    finally:
        d.modes = real


def resize(dev, zi, n):
    """What OpenRGB 1.0 does on a resize: the zone, and so the device, gets n LEDs. It does not check the range."""
    z = dev.zones[zi]
    z.num_leds = n
    z.leds = [u.LEDData("%s %d" % (z.name, i), i) for i in range(n)]
    z.colors = [BLACK] * n
    start = 0
    for x in dev.zones:
        x.start_idx = start
        start += len(x.leds)
    dev.leds = [led for x in dev.zones for led in x.leds]
    dev.colors = [BLACK] * len(dev.leds)


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
        srv = self.server
        with srv.guard:
            srv.conns.append(self.request)
            srv.connects += 1
        try:
            while True:
                h = struct.unpack("ccccIII", self.recv(16))
                dev, ptype, size = h[4:]
                body = self.recv(size) if size else b""
                with srv.guard:
                    devs = srv.devices
                    if ptype == 40:
                        if srv.take("slow_version"):
                            time.sleep(1.5)
                        self.reply(0, 40, struct.pack("I", V))
                    elif ptype == 0:
                        self.reply(0, 0, struct.pack("I", len(devs)))
                    elif ptype == 1:
                        data = pack_device(devs[dev])
                        if srv.take("hang_data"):
                            continue
                        if srv.take("cut_data"):
                            self.request.sendall(struct.pack("ccccIII", b"O", b"R", b"G", b"B", dev, 1, len(data))
                                                 + data[:len(data) // 2])
                            self.request.shutdown(socket.SHUT_RDWR)
                            return
                        self.reply(dev, 1, data)
                    elif ptype == 150:
                        self.reply(0, 150, struct.pack("IH", 6, 0))
                    elif ptype == 1000:
                        zi, n = struct.unpack("ii", body)
                        resize(devs[dev], zi, n)
                        srv.log.append(("resize", dev, zi, n))
                    elif ptype == 1050:
                        n = struct.unpack("H", body[4:6])[0]
                        devs[dev].colors = [u.RGBColor(*body[6 + 4 * i:9 + 4 * i]) for i in range(n)]
                        srv.log.append(("leds", dev, n))
                    elif ptype == 1101:
                        m = u.ModeData.unpack(iter(body[8:]), V, struct.unpack("i", body[4:8])[0])
                        devs[dev].modes[m.id] = m
                        devs[dev].active_mode = m.id
                        srv.log.append(("mode", dev, m.name))
                    srv.packets.append(ptype)
        except (ConnectionError, OSError):
            pass
        finally:
            with srv.guard:
                if self.request in srv.conns:
                    srv.conns.remove(self.request)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, devices=None, port=0):
        super().__init__(("127.0.0.1", port), Stub)
        self.guard = threading.RLock()
        self.devices = make_devices() if devices is None else devices
        self.log, self.packets, self.conns, self.fail = [], [], [], set()
        self.connects = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server_address[1]

    def take(self, what):
        if what in self.fail:
            self.fail.discard(what)
            return True
        return False

    def notify(self, ptype, payload=b""):
        """Send every client a packet it did not ask for, as OpenRGB does for list changes."""
        with self.guard:
            for c in list(self.conns):
                c.sendall(struct.pack("ccccIII", b"O", b"R", b"G", b"B", 0, ptype, len(payload)) + payload)

    def drop_all(self):
        """Close every client connection, as OpenRGB does when it exits."""
        with self.guard:
            for c in list(self.conns):
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.conns.clear()

    def stop(self):
        self.drop_all()
        self.shutdown()
        self.server_close()


class FakeProc:
    """Stands in for the OpenRGB process: SdkBackend only polls, terminates and waits on it."""
    pid = 4242

    def __init__(self, code=None):
        self.returncode = code
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self.returncode is None:
            self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.terminate()
