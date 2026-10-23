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

import applog

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 47823
SDK_PORT = 47826  # private port for our own OpenRGB process, so a copy you run yourself (6742) is left alone
TASK_LIGHTING = "RGB Anywhere Lighting"  # elevated scheduled tasks (own names, so they never replace another install's tasks)
TASK_RESCAN = "RGB Anywhere Rescan"
TOKEN_FILE = os.path.join(HERE, "rgbd.token")
_log = applog.get("core")


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
SET_RESULTS = {0: "per-LED colours", 1: "mode colour", 2: "mode takes no colour", 3: "device has no LEDs"}


class Mode:
    def __init__(self, name, info=None):
        self.name = name
        info = info or {}
        self.color_kind = COLOR_KINDS.get(info.get("color_mode"), "?")
        self.colors = info.get("colors")
        self.brightness = info.get("brightness")
        self.brightness_min, self.brightness_max = info.get("brightness_min"), info.get("brightness_max")


class Zone:
    def __init__(self, dev, idx, j):
        self._dev, self._idx = dev, idx
        self.name = j["name"]
        self.type = _enum(ZoneType, j["type"])
        self.leds = [None] * j["leds"]  # callers only use len()
        self.leds_min, self.leds_max = j.get("leds_min"), j.get("leds_max")  # None from a daemon that does not say

    def resize(self, n):
        self._dev._client._b.resize_zone(self._dev._idx, self._idx, n, key=self._dev.key)


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
        self.key = [self.name, self.location]  # finds the device again if OpenRGB restarted and renumbered them

    def set_mode(self, name):
        for m in self.modes:
            if m.name.lower() == name.lower():
                self._client._b.set_mode(self._idx, m.name, key=self.key)
                return
        raise ValueError("Mode %s not found on %s" % (name, self.name))

    def set_color(self, color):
        """The whole device in one colour. Returns what the backend did: a key of SET_RESULTS."""
        rgb = (color.red << 16) | (color.green << 8) | color.blue
        # fill: the daemon sizes the list itself, so a zone resized since our last update() is covered too
        return self._client._b.set_colors(self._idx, [rgb], key=self.key, fill=True)


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
PROTOCOL = 3  # SDK protocol we speak: OpenRGB 1.0 server mode never answers the plugin request of protocol 4


def _sdk_errors():
    """What a dropped, timed-out or confused SDK connection raises inside openrgb-python."""
    import struct
    from openrgb.utils import ControllerParsingError
    return OSError, ControllerParsingError, struct.error


def _unchecked(m):
    """A copy of ModeData m that packs without openrgb-python's validate(). That check rejects modes some controllers
    describe (a colour list on a mode without the colour flag, a speed outside its range); sending back what OpenRGB
    sent us, plus our own change, is safer than refusing the device."""
    import dataclasses
    from openrgb.utils import ModeData

    class Unchecked(ModeData):
        def validate(self, version):
            pass
    return Unchecked(**{f.name: getattr(m, f.name) for f in dataclasses.fields(m)})


def mode_colour_count(m):
    """How many colours mode m takes: as many as it holds now (at least one), within colors_min..colors_max.
    Either limit may be missing: openrgb-python drops both when OpenRGB sends a mode with no colours."""
    n = len(m.colors or []) or 1
    lo = m.colors_min if m.colors_min is not None else 1
    hi = m.colors_max or max(n, lo)
    return max(1, min(max(n, lo), hi))


def _port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _own_openrgb_pids(exe):
    """PIDs of running OpenRGB processes started from `exe` (Windows only; elsewhere none)."""
    if os.name != "nt":
        return []
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='OpenRGB.exe'\" | "
          "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.ExecutablePath }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           errors="ignore", timeout=30, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired):
        return []
    want = os.path.normcase(os.path.abspath(exe))
    pids = []
    for line in r.stdout.splitlines():
        pid, _, path = line.strip().partition("|")
        if pid.isdigit() and path and os.path.normcase(os.path.abspath(path)) == want:
            pids.append(int(pid))
    return pids


def _kill_pid(pid):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, creationflags=0x08000000)


def _kill_on_close_job():
    """A Windows job object that ends its processes when this process ends (its last handle closes), so a daemon that
    is killed never leaves OpenRGB holding the SMBus and the SDK port. None if it cannot be made."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    job = k.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
        raise ctypes.WinError(ctypes.get_last_error())
    return job


def _join_job(job, proc):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    if not k.AssignProcessToJobObject(job, int(proc._handle)):
        raise ctypes.WinError(ctypes.get_last_error())


class SdkBackend:
    """Runs the bundled, unmodified OpenRGB (openrgb\\OpenRGB.exe) as a headless SDK server on 127.0.0.1 and talks to it
    with the openrgb-python client. SMBus / RAM lights need admin, so the elevated daemon owns the process.

    A dropped connection is made again; if OpenRGB exited it is started again (at most MAX_RESTARTS times in
    RESTART_WINDOW, then requests fail until a rescan) and on_restart() is called, because a fresh OpenRGB has forgotten
    the zone sizes and colours. Requests take an optional device key ([name, location]) so a request made with an index
    from before a restart still reaches the same device.

    spawn=False connects to a server somebody else started (used by the tests)."""

    SETTLE = 6  # seconds without a change in the device list before detection counts as finished
    START_TIMEOUT = 120
    CONNECT_TIMEOUT = 20  # a running OpenRGB that does not take a connection this long is restarted
    CALL_TIMEOUT = 30     # a request still running after this has its socket closed, so a lost reply cannot hang the daemon
    MAX_RESTARTS = 3
    RESTART_WINDOW = 300
    PORT_WAIT = 10        # seconds to wait for our port to come free before reporting who holds it

    def __init__(self, exe=None, config_dir=None, log_level=None, port=SDK_PORT, spawn=True, on_restart=None):
        self.exe = exe or os.path.join(HERE, "openrgb", "OpenRGB.exe")
        self.core_dir = os.path.dirname(self.exe)
        self.port, self.spawn, self.on_restart = port, spawn, on_restart
        self.config_dir = config_dir or os.path.join(HERE, "cache", "core")
        self.log_dir = os.path.join(self.config_dir, "logs")
        os.makedirs(self.config_dir, exist_ok=True)
        if log_level is not None:
            set_core_log_level(self.config_dir, log_level)
        self._lock = threading.RLock()
        self._proc = None
        self._cli = None
        self._job = None
        self._restarts = []  # times of the restarts nobody asked for, for MAX_RESTARTS
        self.last_error = None
        self._start()

    # -- process and connection
    def _start(self):
        if self.spawn:
            if not os.path.exists(self.exe):
                raise FileNotFoundError("OpenRGB is missing: %s. Reinstall RGB Anywhere." % self.exe)
            self._free_port()
            self._proc = subprocess.Popen(
                [self.exe, "--server", "--server-host", "127.0.0.1", "--server-port", str(self.port),
                 "--config", self.config_dir, "--noautoconnect"],
                cwd=self.core_dir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000)  # CREATE_NO_WINDOW
            self._adopt(self._proc)
        try:
            self._connect(time.time() + self.START_TIMEOUT)
            self._settle()
        except BaseException:
            self._stop()  # never leave a half-started OpenRGB behind
            raise

    def _adopt(self, proc):
        """Tie OpenRGB's lifetime to ours (see _kill_on_close_job). Best effort: without it a killed daemon can leave
        OpenRGB running, which the next start then ends (see _free_port)."""
        if os.name != "nt":
            return
        try:
            if self._job is None:
                self._job = _kill_on_close_job()
            _join_job(self._job, proc)
        except Exception as e:
            _log.warning("could not tie OpenRGB to the daemon's lifetime: %s: %s", type(e).__name__, e)

    def _free_port(self):
        """OpenRGB left running by a daemon that died holds our port and the SMBus: end it. Another program on the
        port is reported, not touched."""
        for pid in _own_openrgb_pids(self.exe):
            _log.warning("ending OpenRGB (pid %s), left running by an earlier daemon", pid)
            _kill_pid(pid)
        deadline = time.time() + self.PORT_WAIT
        while _port_in_use(self.port):
            if time.time() > deadline:
                raise RuntimeError("Another program is using port %d on 127.0.0.1, so OpenRGB cannot start its SDK server. "
                                   "Find it with: netstat -ano | findstr :%d" % (self.port, self.port))
            time.sleep(0.5)

    def _connect(self, deadline):
        from openrgb import OpenRGBClient
        last = "nothing listening yet"
        while True:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("OpenRGB exited with code %s while starting. Its log is in %s"
                                   % (self._proc.returncode, self.log_dir))
            try:
                cli = OpenRGBClient("127.0.0.1", self.port, "RGB Anywhere", protocol_version=PROTOCOL)
                if cli.protocol_version == PROTOCOL:
                    # openrgb-python sends each request's header and body separately; without this every request
                    # waits for the server's delayed ACK (40 ms on Linux; Windows may delay ACKs up to 200 ms)
                    cli.comms.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self._cli = cli
                    return
                # openrgb-python waits 1 s for the version answer and otherwise falls back to protocol 0, which
                # misreads every later reply. A busy OpenRGB answers late; connect again.
                last = "OpenRGB answered with SDK protocol %s, expected %d" % (cli.protocol_version, PROTOCOL)
                self._close(cli)
            except _sdk_errors() as e:
                last = "%s: %s" % (type(e).__name__, e)
            if time.time() > deadline:
                raise TimeoutError("OpenRGB did not open its SDK server on 127.0.0.1:%d (%s). Its log is in %s"
                                   % (self.port, last, self.log_dir))
            time.sleep(0.5)

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

    @staticmethod
    def _close(cli):
        try:
            cli.disconnect()
        except Exception:
            pass

    def _disconnect(self):
        if self._cli is not None:
            self._close(self._cli)
            self._cli = None

    def _stop(self):
        self._disconnect()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def _restart(self, why):
        now = time.time()
        self._restarts = [t for t in self._restarts if now - t < self.RESTART_WINDOW]
        if len(self._restarts) >= self.MAX_RESTARTS:
            self._stop()
            raise RuntimeError("OpenRGB stopped %d times in %d minutes (last: %s), so it is not started again for now. "
                               "Press Rescan to try again; OpenRGB's log is in %s"
                               % (len(self._restarts), self.RESTART_WINDOW // 60, why, self.log_dir))
        self._restarts.append(now)
        _log.warning("%s; starting OpenRGB again", why)
        self._stop()
        self._start()
        if self.on_restart:
            try:
                self.on_restart()
            except Exception:
                _log.exception("on_restart failed")

    def _reconnect(self, why):
        _log.warning("%s; connecting to OpenRGB again", why)
        self._disconnect()
        try:
            self._connect(time.time() + self.CONNECT_TIMEOUT)
        except Exception as e:
            if not self.spawn:
                raise
            self._restart("OpenRGB does not answer (%s)" % e)

    @staticmethod
    def _peer_closed(cli):
        """True when OpenRGB closed the connection. openrgb-python itself only notices after waiting 10 s for a reply."""
        s = cli.comms.sock
        if s is None:
            return True
        try:
            s.setblocking(False)
            try:
                return s.recv(1, socket.MSG_PEEK) == b""
            finally:
                s.settimeout(10.0)  # what openrgb-python sets after connecting
        except BlockingIOError:
            return False  # open, nothing waiting
        except OSError:
            return True

    def _ensure(self):
        """The client, reconnecting (or starting OpenRGB again if it exited) when the connection is gone."""
        if self.spawn and (self._proc is None or self._proc.poll() is not None):
            self._restart("OpenRGB exited (code %s)" % (self._proc.returncode if self._proc is not None else "?"))
        elif self._cli is None or self._peer_closed(self._cli):
            self._reconnect("the SDK connection was closed")
        return self._cli

    def _watchdog(self, cli):
        """openrgb-python can wait forever on a reply cut off half way. Closing the socket makes it fail instead."""
        def fire():
            s = cli.comms.sock
            _log.warning("an OpenRGB request took over %ds; closing its connection", self.CALL_TIMEOUT)
            if s is not None:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                s.close()
        t = threading.Timer(self.CALL_TIMEOUT, fire)
        t.daemon = True
        t.start()
        return t

    def _call(self, fn):
        """Run fn(client) under the lock. When the connection breaks, connect again (starting OpenRGB again if it
        exited) and run fn once more. fn looks its device up itself, so the second run finds it in the new list."""
        with self._lock:
            for attempt in (1, 2):
                cli = self._ensure()
                dog = self._watchdog(cli)
                try:
                    return fn(cli)
                except _sdk_errors() as e:
                    self.last_error = "%s: %s" % (type(e).__name__, e)
                    if attempt == 2:
                        raise
                    _log.warning("OpenRGB request failed (%s); trying once more", self.last_error)
                    self._disconnect()
                finally:
                    dog.cancel()

    # -- backend interface (same as RemoteBackend)
    def version(self):
        cli = self._cli
        return "OpenRGB 1.0 (bundled), SDK protocol %s" % (cli.protocol_version if cli else "?")

    @staticmethod
    def _key(d):
        return [d.name, d.metadata.location]

    @staticmethod
    def _device_json(i, d):
        modes = [{"name": m.name, "color_mode": int(m.color_mode), "flags": int(m.flags), "colors": len(m.colors or []),
                  "colors_min": m.colors_min, "colors_max": m.colors_max, "brightness": m.brightness,
                  "brightness_min": m.brightness_min, "brightness_max": m.brightness_max}
                 for m in d.modes]
        meta = d.metadata
        return {"index": i, "name": d.name, "vendor": meta.vendor or "", "description": meta.description,
                "location": meta.location, "serial": meta.serial, "type": int(d.type), "led_count": len(d.leds),
                "active_mode": d.active_mode, "modes": [m["name"] for m in modes], "mode_info": modes,
                "zones": [{"name": z.name, "type": int(z.type), "leds": len(z.leds), "leds_min": zd.leds_min,
                           "leds_max": zd.leds_max} for z, zd in zip(d.zones, d.data.zones)]}

    def devices(self):
        def go(cli):
            cli.update()
            return [self._device_json(i, d) for i, d in enumerate(cli.devices)]
        return self._call(go)

    def _dev(self, cli, i, key=None):
        """Device i, or with a key the device that has it (it moves when OpenRGB restarts and lists devices anew)."""
        devs = cli.devices
        if not isinstance(i, int) or isinstance(i, bool):
            raise ValueError("device index must be a whole number, not %r" % (i,))
        if key is not None:
            key = list(key)
            if 0 <= i < len(devs) and devs[i] is not None and self._key(devs[i]) == key:
                return devs[i]
            for d in devs:
                if d is not None and self._key(d) == key:
                    return d
            raise LookupError("%s is no longer listed by OpenRGB (it restarted or the device was unplugged)" % key[0])
        if not 0 <= i < len(devs) or devs[i] is None:
            raise ValueError("no device %s (OpenRGB lists %d)" % (i, len(devs)))
        return devs[i]

    def _push_mode(self, d, m):
        """Send mode m to device d and wait for OpenRGB to report it active. A mode it does not confirm is logged."""
        import warnings
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            d.set_mode(_unchecked(m))
        for w in seen:
            _log.warning("%s", w.message)

    def set_mode(self, dev, name, key=None):
        def go(cli):
            d = self._dev(cli, dev, key)
            m = next((m for m in d.modes if m.name.lower() == str(name).lower()), None)
            if m is None:
                raise ValueError("Mode %s not found on %s" % (name, d.name))
            self._push_mode(d, m)
        self._call(go)

    def set_colors(self, dev, colors, key=None, fill=False):
        """0 = per-LED colours applied, 1 = applied as the mode colour, 2 = the active mode takes no colour,
        3 = the device has no LEDs (an addressable header nobody sized yet). Fewer colours than LEDs leave the rest
        as they are; more are ignored; fill=True gives every LED the first colour. A mode colour is the first colour."""
        from openrgb.utils import ModeColors, ModeFlags, RGBColor as ORGBColor
        if not isinstance(colors, (list, tuple)) or not colors:
            raise ValueError("no colours given")
        if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 0xFFFFFF for v in colors):
            raise ValueError("colours must be whole numbers from 0 to 0xFFFFFF")

        def conv(v):
            return ORGBColor((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

        def go(cli):
            d = self._dev(cli, dev, key)
            if not 0 <= d.active_mode < len(d.modes):
                raise ValueError("%s has no active mode" % d.name)
            m = d.modes[d.active_mode]
            # Modes such as Static on ASUS Aura boards keep their colour in the mode itself (MODE_SPECIFIC); per-LED
            # colours written to them change nothing on the hardware, so set the mode's colour and push the mode.
            if m.color_mode != ModeColors.PER_LED and ModeFlags.HAS_MODE_SPECIFIC_COLOR in m.flags:
                n = mode_colour_count(m)
                m.color_mode = ModeColors.MODE_SPECIFIC
                m.colors = [conv(colors[0])] * n
                m.colors_min = n if m.colors_min is None else m.colors_min
                m.colors_max = n if m.colors_max is None else m.colors_max
                self._push_mode(d, m)
                return 1
            if m.color_mode != ModeColors.PER_LED and ModeFlags.HAS_PER_LED_COLOR not in m.flags:
                return 2
            if not d.leds:
                return 3
            if m.color_mode != ModeColors.PER_LED:  # the mode can take per-LED colours but is set to another kind
                m.color_mode = ModeColors.PER_LED
                self._push_mode(d, m)
            if fill:
                cur = [conv(colors[0])] * len(d.leds)
            else:
                cur = list(d.colors)
                for i in range(min(len(cur), len(colors))):
                    cur[i] = conv(colors[i])
            d._set_device_colors(cur)
            return 0
        return self._call(go)

    def debug(self):
        running = (self._proc is not None and self._proc.poll() is None) if self.spawn else None
        cli = self._cli
        # The SDK does not list SMBus buses, so i2c_buses is None; diag reads OpenRGB's log instead.
        return {"started": cli is not None, "config_dir": self.config_dir, "log_dir": self.log_dir, "exe": self.exe,
                "running": running, "pid": self._proc.pid if self._proc is not None else None, "port": self.port,
                "protocol": cli.protocol_version if cli is not None else None, "restarts": len(self._restarts),
                "last_error": self.last_error, "i2c_buses": None}

    def resize_zone(self, dev, zone, n, key=None):
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError("LED count must be a whole number of 0 or more, not %r" % (n,))

        def go(cli):
            d = self._dev(cli, dev, key)
            if not isinstance(zone, int) or isinstance(zone, bool) or not 0 <= zone < len(d.zones):
                raise ValueError("no zone %r on %s (it has %d)" % (zone, d.name, len(d.zones)))
            zd = d.data.zones[zone]
            # OpenRGB ignores a size outside the zone's range without saying so
            if not zd.leds_min <= n <= zd.leds_max:
                raise ValueError("%s / %s takes %d to %d LEDs, not %d" % (d.name, zd.name, zd.leds_min, zd.leds_max, n))
            d.zones[zone].resize(n)
        self._call(go)

    def rescan(self):
        """The SDK has no rescan request, so restart OpenRGB: it detects everything again on start."""
        with self._lock:
            self._restarts = []
            if not self.spawn:
                self._ensure().update()
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
        if not buf.endswith(b"\n"):  # the daemon closed the connection without an answer (it stopped or crashed)
            raise ConnectionError("the lighting daemon closed the connection without answering %r" % cmd)
        resp = json.loads(buf.decode("utf-8"))
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "daemon error"))
        return resp.get("result")

    def devices(self):
        return self.request("devices")

    def set_mode(self, dev, name, key=None):
        self.request("set_mode", dev=dev, mode=name, key=key)

    def set_colors(self, dev, colors, key=None, fill=False):
        return self.request("set_colors", dev=dev, colors=colors, key=key, fill=fill)

    def debug(self):
        return self.request("debug")

    def resize_zone(self, dev, zone, n, key=None):
        self.request("resize_zone", dev=dev, zone=zone, leds=n, key=key)

    def rescan(self):
        self.request("rescan")
