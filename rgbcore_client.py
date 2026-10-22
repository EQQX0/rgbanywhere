"""Python side of the embedded OpenRGB device core (rgbcore.dll, built from third_party/openrgb).

Two backends give the same small object API (client.devices, dev.modes, dev.zones, dev.set_mode,
dev.set_color, zone.resize), so rgb.py works with either:

  CoreBackend    loads rgbcore.dll in this process (needs admin for SMBus / RAM lights)
  RemoteBackend  talks to the elevated daemon (rgbd.py) over a localhost socket

Copyright (C) 2026 EQQX2
SPDX-License-Identifier: GPL-3.0-or-later
This program loads rgbcore.dll, which contains code derived from OpenRGB (GPL-2.0-or-later);
see NOTICE and third_party/openrgb/LICENSE.
"""
import ctypes
import enum
import json
import os
import socket
import threading
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 47823
TASK_LIGHTING = "RGB Anywhere Lighting"  # elevated scheduled tasks (own names, so they never replace another install's tasks)
TASK_RESCAN = "RGB Anywhere Rescan"
TOKEN_FILE = os.path.join(HERE, "rgbd.token")


class RGBColor(namedtuple("RGBColor", "red green blue")):
    pass


# Same order as DEVICE_TYPE_* / ZONE_TYPE_* in OpenRGB 1.0's RGBController.h
class DeviceType(enum.IntEnum):
    MOTHERBOARD = 0; DRAM = 1; GPU = 2; COOLER = 3; LEDSTRIP = 4; KEYBOARD = 5; MOUSE = 6; MOUSEMAT = 7
    HEADSET = 8; HEADSET_STAND = 9; GAMEPAD = 10; LIGHT = 11; SPEAKER = 12; VIRTUAL = 13; STORAGE = 14
    CASE = 15; MICROPHONE = 16; ACCESSORY = 17; KEYPAD = 18; LAPTOP = 19; MONITOR = 20; UNKNOWN = 21


class ZoneType(enum.IntEnum):
    SINGLE = 0; LINEAR = 1; MATRIX = 2; LINEAR_LOOP = 3; MATRIX_LOOP = 4; SEGMENTED = 5


def _enum(cls, v):
    try:
        return cls(v)
    except ValueError:
        return cls(0) if cls is ZoneType else DeviceType.UNKNOWN


class Mode:
    def __init__(self, name):
        self.name = name


class Zone:
    def __init__(self, dev, idx, j):
        self._dev, self._idx = dev, idx
        self.name = j["name"]
        self.type = _enum(ZoneType, j["type"])
        self.leds = [None] * j["leds"]  # callers only use len()

    def resize(self, n):
        self._dev._client._b.resize_zone(self._dev._idx, self._idx, n)


class Location:
    def __init__(self, s):
        self.location = s

    def split(self, *a):
        return self.location.split(*a)


class Metadata:
    def __init__(self, loc):
        self.location = loc


class Device:
    def __init__(self, client, j):
        self._client, self._idx = client, j["index"]
        self.name = j["name"]
        self.type = _enum(DeviceType, j["type"])
        self.metadata = Metadata(j.get("location", ""))
        self.led_count = j["led_count"]
        self.modes = [Mode(m) for m in j["modes"]]
        self.zones = [Zone(self, i, z) for i, z in enumerate(j["zones"])]

    def set_mode(self, name):
        for m in self.modes:
            if m.name.lower() == name.lower():
                self._client._b.set_mode(self._idx, m.name)
                return
        raise ValueError("Mode %s not found on %s" % (name, self.name))

    def set_color(self, color):
        rgb = (color.red << 16) | (color.green << 8) | color.blue
        self._client._b.set_colors(self._idx, [rgb] * max(self.led_count, 1))


class Client:
    def __init__(self, backend):
        self._b = backend
        self.devices = []
        self.update()

    def update(self):
        self.devices = [Device(self, j) for j in self._b.devices()]

    def rescan(self):
        self._b.rescan()
        self.update()


# ---------------------------------------------------------------- in-process backend
class CoreBackend:
    def __init__(self, dll=None, config_dir=None):
        dll = dll or os.path.join(HERE, "rgbcore.dll")
        os.add_dll_directory(os.path.dirname(dll))
        self._dll = ctypes.CDLL(dll)
        d = self._dll
        d.rgb_init.argtypes = [ctypes.c_char_p]
        d.rgb_devices_json.restype = ctypes.c_char_p
        d.rgb_set_mode.argtypes = [ctypes.c_int, ctypes.c_char_p]
        d.rgb_set_colors.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.c_int]
        d.rgb_resize_zone.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        d.rgb_core_version.restype = ctypes.c_char_p
        self._lock = threading.Lock()
        cfg = config_dir or os.path.join(HERE, "cache", "core")
        os.makedirs(cfg, exist_ok=True)
        self._call(d.rgb_init, cfg.encode())

    def _call(self, fn, *a):
        with self._lock:
            return fn(*a)

    def version(self):
        return self._dll.rgb_core_version().decode()

    def devices(self):
        return json.loads(self._call(self._dll.rgb_devices_json).decode("utf-8"))

    def set_mode(self, dev, name):
        r = self._call(self._dll.rgb_set_mode, dev, name.encode())
        if r:
            raise ValueError("set_mode failed (%d)" % r)

    def set_colors(self, dev, colors):
        arr = (ctypes.c_uint * len(colors))(*colors)
        self._call(self._dll.rgb_set_colors, dev, arr, len(colors))

    def resize_zone(self, dev, zone, n):
        self._call(self._dll.rgb_resize_zone, dev, zone, n)

    def rescan(self):
        self._call(self._dll.rgb_rescan)

    def shutdown(self):
        self._call(self._dll.rgb_shutdown)


# ---------------------------------------------------------------- daemon backend
class RemoteBackend:
    """JSON lines over 127.0.0.1; the token (rgbd.token, written by the daemon) is sent with every request."""

    def __init__(self, port=PORT, timeout=120):
        self.port, self.timeout = port, timeout
        with open(TOKEN_FILE, encoding="ascii") as f:
            self._token = f.read().strip()
        self.request("ping")

    def request(self, cmd, **args):
        with socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout) as s:
            s.sendall((json.dumps({"token": self._token, "cmd": cmd, **args}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        resp = json.loads(buf.decode("utf-8"))
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "daemon error"))
        return resp.get("result")

    def devices(self):
        return self.request("devices")

    def set_mode(self, dev, name):
        self.request("set_mode", dev=dev, mode=name)

    def set_colors(self, dev, colors):
        self.request("set_colors", dev=dev, colors=colors)

    def resize_zone(self, dev, zone, n):
        self.request("resize_zone", dev=dev, zone=zone, leds=n)

    def rescan(self):
        self.request("rescan")
