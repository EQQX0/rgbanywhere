"""Set all RGB (RAM, fans, keyboard, mouse) through the embedded OpenRGB device core (rgbcore.dll, via rgbd.py).

Modified by EQQX2, 2026-10-22: the OpenRGB server / openrgb-python is replaced by the embedded core.
SPDX-License-Identifier: GPL-3.0-or-later

Usage:
  python rgb.py                 reapply the last saved color/mode
  python rgb.py blue            named color or hex (0000FF / #0000FF)
  python rgb.py red breathing   optional mode (static, breathing, rainbow, spectrum cycle, off, ...)
  python rgb.py ram=B32A00 fans=B32A00   per-device color (ram, fans, keyboard, mouse); keeps the rest
  python rgb.py brightness=60   0-100, scales every color
"""
import json
import os
import subprocess
import sys
import time

import applog
import rgbcore_client
from rgbcore_client import RGBColor

_log = applog.get("lights")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
CONFIG = os.path.join(HERE, "config.json")
GROUPS = ("ram", "fans", "keyboard", "mouse")  # what the UI can target; other devices follow the overall color


def load_config():
    """Per-PC settings from config.json (all optional; see README). Missing file = generic defaults."""
    try:
        with open(CONFIG, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        _log.exception("config.json is unreadable; using defaults")
        return {}


CFG = load_config()
ARGB_LEDS = int(CFG.get("argb_leds", 60))  # LEDs to give each empty motherboard ARGB header; OpenRGB forgets sizes on restart
HELPER_SERVICE = CFG.get("helper_service")  # a sensor service that disturbs OpenRGB's RAM detection; stopped during rescans
RAM_EXPECTED = CFG.get("ram_expected")      # sticks that should be found; None = don't check
RAM_SAME_MODEL = bool(CFG.get("ram_same_model", False))  # flag sticks detected as different models (a misread on matched kits)


NAMES = {
    "blue": "0000FF", "red": "FF0000", "green": "00FF00", "white": "FFFFFF",
    "purple": "8000FF", "pink": "FF0080", "cyan": "00FFFF", "yellow": "FFFF00",
    "orange": "FF6000", "autumn": "FF3C00", "black": "000000",
}


def to_hex(s):
    return NAMES.get(s.lower(), s.lstrip("#")).upper()


def to_color(h, brightness=100, gains=(100, 100, 100)):
    """Hex -> RGBColor, scaled by brightness and by per-channel calibration gains (percent, 0-200).

    Gains are a balance, not absolute boosts: if any gain is above 100, all three are scaled down so the
    strongest is 100. That way "G 200%" visibly shifts toward green even when G is already at 255.
    """
    k = max(0, min(100, brightness)) / 100
    gains = [max(0, min(200, g)) for g in gains]
    top = max(gains)
    if top > 100:
        gains = [g * 100 / top for g in gains]
    return RGBColor(*(min(255, round(int(h[i:i + 2], 16) * k * g / 100))
                      for i, g in zip((0, 2, 4), gains)))


_TYPE_GROUP = {"DRAM": "ram", "MOTHERBOARD": "fans", "LEDSTRIP": "fans", "COOLER": "fans", "CASE": "fans",
               "KEYBOARD": "keyboard", "KEYPAD": "keyboard", "MOUSE": "mouse", "MOUSEMAT": "mouse"}


def group_of(dev):
    """Which UI group a device belongs to: ram, fans (board/strips/coolers/case), keyboard, mouse, or other.

    `dev` is an OpenRGB device (classified by its type) or a name string. config.json `group_overrides`
    ({"substring of device name": "group"}) wins over the type.
    """
    name = dev if isinstance(dev, str) else dev.name
    for needle, group in CFG.get("group_overrides", {}).items():
        if needle.lower() in name.lower():
            return group
    t = getattr(getattr(dev, "type", None), "name", None)
    if t in _TYPE_GROUP:
        return _TYPE_GROUP[t]
    if t:
        return "other"
    # name only (no device object): best guess
    if "DDR" in name or "DRAM" in name:
        return "ram"
    if "Mouse" in name:
        return "mouse"
    return "other"


def load_state():
    state = {"color": "0000FF", "mode": "static", "groups": {}, "brightness": 100}
    try:
        with open(STATE) as f:
            state.update(json.load(f))
    except FileNotFoundError:
        _log.info("no state.json yet, using defaults")
    except Exception:
        # Keep the damaged file for inspection instead of silently overwriting it with defaults.
        keep = "%s.corrupt-%s" % (STATE, time.strftime("%Y%m%d-%H%M%S"))
        _log.exception("state.json is unreadable; using defaults. A copy was kept as %s", keep)
        applog.event("state_corrupt", ok=False, error="state.json unreadable", kept=keep)
        try:
            os.replace(STATE, keep)
        except OSError:
            pass
    return state


def save_state(state):
    """Write atomically (temp file + replace) so a crash or a second process can't leave a half-written file."""
    tmp = "%s.%d.tmp" % (STATE, os.getpid())
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE)
    except Exception:
        _log.exception("Couldn't save state.json")
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def color_for(state, group):
    return state.get("groups", {}).get(group, state["color"])


def connect(wait=60):
    """Connect to the lighting daemon (rgbd.py), starting it through its elevated "RGB Lighting" task if needed.

    The daemon is elevated because RAM and some boards are reached over SMBus; this process need not be.
    """
    for attempt in range(wait):
        try:
            client = rgbcore_client.Client(rgbcore_client.RemoteBackend())
            _log.info("connected to the lighting daemon%s", " after %d attempts" % (attempt + 1) if attempt else "")
            return client
        except Exception as e:
            if attempt == 0:
                _log.warning("lighting daemon not reachable (%s: %s); starting it", type(e).__name__, e)
                r = _quiet("schtasks", "/run", "/tn", "RGB Lighting")
                _log.info("schtasks /run 'RGB Lighting' -> exit %s %s", r.returncode, (r.stdout + r.stderr).strip()[:200])
            time.sleep(1)
    _log.error("Could not connect to the lighting daemon within %ds", wait)
    applog.event("core_connect", ok=False, error="could not connect within %ds" % wait)
    raise ConnectionError("Could not connect to the lighting daemon")


def is_elevated():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def prepare(client):
    client.update()
    for dev in client.devices:
        if group_of(dev) == "fans":
            for zone in dev.zones:
                # Only empty addressable headers: fixed zones (onboard LEDs) can't be resized.
                if zone.type.name == "LINEAR" and len(zone.leds) < 2:
                    _log.info("resizing %s / %s from %d to %d LEDs", dev.name, zone.name, len(zone.leds), ARGB_LEDS)
                    try:
                        zone.resize(ARGB_LEDS)
                    except Exception:
                        _log.exception("Couldn't resize %s / %s", dev.name, zone.name)
    client.update()
    names = [d.name for d in client.devices]
    _log.info("devices (%d): %s", len(names), "; ".join("%s [%s]" % (n, group_of(d)) for n, d in zip(names, client.devices)))
    ram = sum(1 for d in client.devices if group_of(d) == "ram")
    if RAM_EXPECTED and ram < RAM_EXPECTED:
        _log.warning("only %d RAM stick(s) detected (expected %d)", ram, RAM_EXPECTED)
    # RAM sticks that came up without Direct (see apply), and how long after boot, to correlate with power-ons.
    no_direct = ram_without_direct(client)
    if no_direct:
        _log.warning("RAM without Direct mode at %s (uptime %ds)", ", ".join(no_direct), uptime_s())
    applog.event("openrgb_devices", ok=True, count=len(names), ram=ram, devices=names,
                 ram_no_direct=no_direct, uptime_s=uptime_s())


def ram_without_direct(client):
    """Bus addresses of the RAM sticks that have no Direct mode."""
    return [d.metadata.location.split("address")[-1].strip() for d in client.devices
            if group_of(d) == "ram" and not any(m.name.lower() == "direct" for m in d.modes)]


def ram_problem(client):
    """What is wrong with the RAM as OpenRGB detected it, or None.

    Something garbles OpenRGB's reads now and then while it detects the sticks, and one then comes up
    missing, without Direct mode or as a different model. Detecting again usually gets it right.
    """
    names = [d.name for d in client.devices if group_of(d) == "ram"]
    if RAM_EXPECTED and len(names) < RAM_EXPECTED:
        return "only %d of %d sticks detected" % (len(names), RAM_EXPECTED)
    no_direct = ram_without_direct(client)
    if no_direct:
        return "no Direct mode at %s" % ", ".join(no_direct)
    if RAM_SAME_MODEL and len(set(names)) > 1:
        return "sticks detected as different models: %s" % ", ".join(sorted(set(names)))
    return None


def uptime_s():
    try:
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_uint64
        return ctypes.windll.kernel32.GetTickCount64() // 1000
    except Exception:
        return -1


def apply(client, state, log=print):
    """Send the state to every device. Returns how many devices failed (a stale connection fails them all)."""
    mode = state["mode"]
    failed = 0
    _log.info("apply: mode=%s color=%s groups=%s brightness=%s calibration=%s%s", mode, state.get("color"),
              state.get("groups"), state.get("brightness"), state.get("calibration"),
              " TEST=%s" % state["test"] if state.get("test") else "")
    for dev in client.devices:
        names = [m.name.lower() for m in dev.modes]
        try:
            if mode in names:
                dev.set_mode(mode)
            elif mode == "off":
                dev.set_mode("direct" if "direct" in names else "static")
            elif "static" in names:
                dev.set_mode("static")
            elif "direct" in names or "custom" not in names:
                dev.set_mode("direct")
            else:
                # Corsair RAM sometimes comes up without Direct (OpenRGB misreads its protocol version);
                # Custom is its per-LED static mode and is always there.
                _log.warning("%s has no Direct mode; using Custom instead", dev.name)
                dev.set_mode("custom")
            if mode == "off":
                dev.set_color(RGBColor(0, 0, 0))
            else:
                group = group_of(dev)
                hexc = state.get("test") or color_for(state, group)  # "test" = calibration preview
                gains = state.get("calibration", {}).get(group, (100, 100, 100))
                dev.set_color(to_color(hexc, state.get("brightness", 100), gains))
            _log.debug("ok   %s", dev.name)
            log("ok   " + dev.name)
        except Exception as e:
            failed += 1
            _log.warning("FAILED on %s: %s: %s", dev.name, type(e).__name__, e, exc_info=True)
            log("fail %s %s" % (dev.name, e))
    if failed:
        applog.event("lights_apply", ok=False, error="%d of %d devices failed" % (failed, len(client.devices)),
                     mode=mode, color=state.get("color"))
    return failed


def _quiet(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="ignore", creationflags=0x08000000)


def service_state(name):
    """"RUNNING", "STOPPED", "STOP_PENDING"... or None if there is no such service."""
    for line in _quiet("sc", "query", name).stdout.splitlines():
        if "STATE" in line:
            return line.split()[-1]
    return None


def stop_helper(wait=10):
    """Stop DeepCool's sensor helper so it can't read the RAM while OpenRGB detects it: with it running,
    about one detection in five came up with a stick missing, without Direct mode or as another model.
    Returns True if it was running (and so should be started again afterwards). Needs admin."""
    if not HELPER_SERVICE or service_state(HELPER_SERVICE) != "RUNNING":
        return False
    r = _quiet("sc", "stop", HELPER_SERVICE)
    if r.returncode != 0:
        _log.warning("couldn't stop %s (exit %s): %s", HELPER_SERVICE, r.returncode, (r.stdout + r.stderr).strip()[:150])
        applog.event("helper_stop", ok=False, error="sc stop exit %s" % r.returncode)
        return False
    t0 = time.time()
    while service_state(HELPER_SERVICE) not in ("STOPPED", None):
        if time.time() - t0 > wait:
            _log.warning("%s still running after %ds; going on without it stopped", HELPER_SERVICE, wait)
            applog.event("helper_stop", ok=False, error="still running after %ds" % wait)
            return True
        time.sleep(0.25)
    _log.info("stopped %s for the rescan (%.1fs)", HELPER_SERVICE, time.time() - t0)
    return True


def start_helper():
    if not HELPER_SERVICE:
        return
    r = _quiet("sc", "start", HELPER_SERVICE)
    ok = r.returncode in (0, 1056)  # 1056: it was already running
    _log.info("started %s again -> exit %s", HELPER_SERVICE, r.returncode)
    if not ok:
        _log.warning("couldn't start %s: %s", HELPER_SERVICE, (r.stdout + r.stderr).strip()[:150])
        applog.event("helper_start", ok=False, error="sc start exit %s" % r.returncode)


def main():
    applog.setup("lights-cli")
    try:
        _main()
    except SystemExit:
        raise
    except Exception:
        _log.exception("rgb.py failed")
        applog.event("lights_cli", ok=False, error=applog.format_exc().strip().splitlines()[-1], argv=sys.argv[1:])
        raise


def _main():
    state = load_state()
    args = sys.argv[1:]
    try:
        if "--restart" in args:  # re-detect devices (the daemon stops/starts the cooler helper around it)
            args.remove("--restart")
            applog.event("core_rescan", ok=True, source="cli")
            connect().rescan()
        groups = [a for a in args if "=" in a]
        rest = [a for a in args if "=" not in a]
        if rest:
            state["color"] = to_hex(rest[0])
            state["mode"] = " ".join(rest[1:]).lower() or "static"
            state["groups"] = {}  # new overall color resets per-device colors
        for g in groups:
            name, val = g.split("=", 1)
            if name.lower() == "brightness":
                state["brightness"] = int(val)
            else:
                state.setdefault("groups", {})[name.lower()] = to_hex(val)

        try:
            client = connect()
        except ConnectionError as e:
            sys.exit(str(e))
        time.sleep(3)  # let device detection finish
        prepare(client)
        apply(client, state)
        save_state(state)
    finally:
        pass


if __name__ == "__main__":
    main()
