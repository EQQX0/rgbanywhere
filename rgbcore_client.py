"""Python side of the lighting daemon: the object API rgb.py uses, and the backends behind it.

Two backends give the same small object API (client.devices, dev.modes, dev.zones, dev.set_mode,
dev.set_color, zone.resize), so rgb.py works with either:

  SdkBackend     runs the bundled OpenRGB (openrgb\\OpenRGB.exe, unmodified) and talks to its SDK server
                 (needs admin for SMBus / RAM lights)
  RemoteBackend  talks to the elevated daemon (rgbd.py) over a localhost socket

Copyright (C) 2026 EQQX2
SPDX-License-Identifier: GPL-3.0-or-later
OpenRGB (GPL-2.0-or-later) is a separate program that ships alongside; see NOTICE.
"""
import enum
import json
import os
import socket
import subprocess
import threading
import time
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 47823
SDK_PORT = 47826  # private port for our own OpenRGB process, so a copy you run yourself (6742) is left alone
TASK_LIGHTING = "RGB Anywhere Lighting"  # elevated scheduled tasks (own names, so they never replace another install's tasks)
TASK_RESCAN = "RGB Anywhere Rescan"
TOKEN_FILE = os.path.join(HERE, "rgbd.token")


class RGBColor(namedtuple("RGBColor", "red green blue")):
    pass


# Same order as DEVICE_TYPE_* / ZONE_TYPE_* in OpenRGB's RGBController.h
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


# How a mode takes colours (ModeColors in OpenRGB's SDK)
COLOR_KINDS = {0: "none", 1: "per-LED", 2: "mode colour", 3: "random"}
# What SdkBackend.set_colors did
SET_RESULTS = {0: "per-LED colours", 1: "mode colour", 2: "mode takes no colour"}


class Mode:
    def __init__(self, name, info=None):
        self.name = name
        info = info or {}
        self.color_kind = COLOR_KINDS.get(info.get("color_mode"), "?")
        self.colors = info.get("colors")


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
        info = {m["name"]: m for m in j.get("mode_info", [])}
        self.modes = [Mode(m, info.get(m)) for m in j["modes"]]
        self.active_mode = j.get("active_mode", -1)
        self.vendor = j.get("vendor", "")
        self.location = j.get("location", "")
        self.zones = [Zone(self, i, z) for i, z in enumerate(j["zones"])]

    def set_mode(self, name):
        for m in self.modes:
            if m.name.lower() == name.lower():
                self._client._b.set_mode(self._idx, m.name)
                return
        raise ValueError("Mode %s not found on %s" % (name, self.name))

    def set_color(self, color):
        """Returns what the backend did: a key of SET_RESULTS."""
        rgb = (color.red << 16) | (color.green << 8) | color.blue
        return self._client._b.set_colors(self._idx, [rgb] * max(self.led_count, 1))


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


LOG_LEVELS = {"fatal": 0, "error": 1, "warning": 2, "info": 3, "verbose": 4, "debug": 5, "trace": 6}


def set_core_log_level(config_dir, level):
    """Set OpenRGB's own log level (OpenRGB.json > LogManager > loglevel) before it starts.

    OpenRGB writes its log to <config_dir>/logs/OpenRGB_<time>.log. "debug" shows every detector it tries.
    """
    n = LOG_LEVELS.get(str(level).lower(), level)
    if not isinstance(n, int):
        raise ValueError("unknown log level %r (use one of %s)" % (level, ", ".join(LOG_LEVELS)))
    path = os.path.join(config_dir, "OpenRGB.json")
    try:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
    except (FileNotFoundError, ValueError):
        settings = {}
    settings.setdefault("LogManager", {})["loglevel"] = n
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4)


# ---------------------------------------------------------------- OpenRGB backend
class SdkBackend:
    """Runs the bundled, unmodified OpenRGB (openrgb\\OpenRGB.exe) as a headless SDK server on 127.0.0.1 and talks to it
    with the openrgb-python client. SMBus / RAM lights need admin, so the elevated daemon owns the process.

    spawn=False connects to a server somebody else started (used by the tests)."""

    SETTLE = 6  # seconds without a change in the device list before detection counts as finished
    START_TIMEOUT = 120

    def __init__(self, exe=None, config_dir=None, log_level=None, port=SDK_PORT, spawn=True):
        self.exe = exe or os.path.join(HERE, "openrgb", "OpenRGB.exe")
        self.core_dir = os.path.dirname(self.exe)
        self.port, self.spawn = port, spawn
        self.config_dir = config_dir or os.path.join(HERE, "cache", "core")
        os.makedirs(self.config_dir, exist_ok=True)
        if log_level is not None:
            set_core_log_level(self.config_dir, log_level)
        self._lock = threading.RLock()
        self._proc = None
        self._cli = None
        self._start()

    # -- process and connection
    def _start(self):
        from openrgb import OpenRGBClient
        if self.spawn:
            if not os.path.exists(self.exe):
                raise FileNotFoundError("OpenRGB is missing: %s. Reinstall RGB Anywhere." % self.exe)
            self._proc = subprocess.Popen(
                [self.exe, "--server", "--server-host", "127.0.0.1", "--server-port", str(self.port),
                 "--config", self.config_dir, "--noautoconnect"],
                cwd=self.core_dir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000)  # CREATE_NO_WINDOW
        deadline = time.time() + self.START_TIMEOUT
        while True:
            try:
                self._cli = OpenRGBClient("127.0.0.1", self.port, "RGB Anywhere", protocol_version=3)  # 3: no plugin requests
                break
            except (OSError, ConnectionError):
                if self._proc is not None and self._proc.poll() is not None:
                    raise RuntimeError("OpenRGB exited with code %s while starting" % self._proc.returncode)
                if time.time() > deadline:
                    raise TimeoutError("OpenRGB did not open its SDK server on port %d" % self.port)
                time.sleep(0.5)
        self._settle()

    def _settle(self):
        """OpenRGB lists devices as the detectors find them; wait until the list stops growing."""
        last, since = -1, time.time()
        deadline = time.time() + self.START_TIMEOUT
        while time.time() < deadline:
            self._cli.update()
            n = len(self._cli.devices)
            if n != last:
                last, since = n, time.time()
            elif time.time() - since >= self.SETTLE:
                return
            time.sleep(1)

    def _stop(self):
        if self._cli is not None:
            try:
                self._cli.disconnect()
            except Exception:
                pass
            self._cli = None
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def _ensure(self):
        if self.spawn and (self._proc is None or self._proc.poll() is not None):
            self._stop()
            self._start()
        return self._cli

    # -- backend interface (same as RemoteBackend)
    def version(self):
        cli = self._cli
        return "OpenRGB 1.0 (bundled), SDK protocol %s" % (cli.protocol_version if cli else "?")

    @staticmethod
    def _device_json(i, d):
        modes = [{"name": m.name, "color_mode": int(m.color_mode), "flags": int(m.flags), "colors": len(m.colors or [])}
                 for m in d.modes]
        meta = d.metadata
        return {"index": i, "name": d.name, "vendor": meta.vendor or "", "description": meta.description,
                "location": meta.location, "serial": meta.serial, "type": int(d.type), "led_count": len(d.leds),
                "active_mode": d.active_mode, "modes": [m["name"] for m in modes], "mode_info": modes,
                "zones": [{"name": z.name, "type": int(z.type), "leds": len(z.leds), "leds_min": zd.leds_min,
                           "leds_max": zd.leds_max} for z, zd in zip(d.zones, d.data.zones)]}

    def devices(self):
        with self._lock:
            cli = self._ensure()
            cli.update()
            return [self._device_json(i, d) for i, d in enumerate(cli.devices)]

    def _dev(self, i):
        devs = self._ensure().devices
        if not 0 <= i < len(devs):
            raise ValueError("no device %s" % i)
        return devs[i]

    def set_mode(self, dev, name):
        with self._lock:
            self._dev(dev).set_mode(name)

    def set_colors(self, dev, colors):
        """0 = per-LED colours applied, 1 = applied as the mode colour, 2 = the active mode takes no colour."""
        from openrgb.utils import ModeColors, ModeFlags, RGBColor as ORGBColor

        def conv(v):
            return ORGBColor((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

        with self._lock:
            d = self._dev(dev)
            if not 0 <= d.active_mode < len(d.modes):
                raise ValueError("device %s has no active mode" % dev)
            m = d.modes[d.active_mode]
            # Modes such as Static on ASUS Aura boards keep their colour in the mode itself (MODE_SPECIFIC); per-LED
            # colours written to them change nothing on the hardware, so set the mode's colour and push the mode.
            if m.color_mode != ModeColors.PER_LED and ModeFlags.HAS_MODE_SPECIFIC_COLOR in m.flags and colors:
                count = len(m.colors or []) or max(m.colors_min or 1, 1)
                if m.colors_max:
                    count = min(count, m.colors_max)
                m.color_mode = ModeColors.MODE_SPECIFIC
                m.colors = [conv(colors[0])] * count
                d.set_mode(m)
                return 1
            if m.color_mode != ModeColors.PER_LED and ModeFlags.HAS_PER_LED_COLOR not in m.flags:
                return 2
            cur = list(d.colors)
            for i in range(min(len(cur), len(colors))):
                cur[i] = conv(colors[i])
            d._set_device_colors(cur)
            return 0

    def debug(self):
        running = (self._proc is not None and self._proc.poll() is None) if self.spawn else None
        # The SDK does not list SMBus buses, so i2c_buses is None; diag reads OpenRGB's log instead.
        return {"started": self._cli is not None, "config_dir": self.config_dir, "exe": self.exe, "running": running,
                "pid": self._proc.pid if self._proc is not None else None, "i2c_buses": None}

    def resize_zone(self, dev, zone, n):
        with self._lock:
            d = self._dev(dev)
            if not 0 <= zone < len(d.zones):
                raise ValueError("no zone %s" % zone)
            d.zones[zone].resize(n)

    def rescan(self):
        """The SDK has no rescan request, so restart OpenRGB: it detects everything again on start."""
        with self._lock:
            if not self.spawn:
                self._cli.update()
                return
            self._stop()
            time.sleep(1)
            self._start()

    def shutdown(self):
        with self._lock:
            self._stop()


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
        return self.request("set_colors", dev=dev, colors=colors)

    def debug(self):
        return self.request("debug")

    def resize_zone(self, dev, zone, n):
        self.request("resize_zone", dev=dev, zone=zone, leds=n)

    def rescan(self):
        self.request("rescan")
