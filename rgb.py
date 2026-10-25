"""Set all RGB (RAM, fans, keyboard, mouse) through the bundled OpenRGB (started and controlled by rgbd.py).

Modified by EQQX2, 2026-10-22: lighting goes through rgbd.py, which runs the bundled OpenRGB.
SPDX-License-Identifier: GPL-3.0-or-later

Usage:
  python rgb.py                 reapply the last saved color/mode
  python rgb.py blue            named color or hex (0000FF / #0000FF)
  python rgb.py red breathing   optional mode (static, breathing, rainbow, spectrum cycle, off, ...)
  python rgb.py ram=B32A00 fans=B32A00   per-group color (ram, fans, keyboard, mouse, gpu, audio, other); keeps the rest
  python rgb.py original        put every device back the way it was before RGB Anywhere first changed it
  python rgb.py brightness=60   0-100, scales every color
  python rgb.py --restore       the same as "original" once, without saving it, then restarts the vendor apps of the
                                devices it handed back (Uninstall uses it)
  python rgb.py --diagnose      what was found and why devices or colors may be missing (changes nothing);
                                also saved to logs\\diagnose-<time>.txt
"""
import json
import os
import re
import subprocess
import sys
import time

import applog
import rgbcore_client
from rgbcore_client import RGBColor

_log = applog.get("lights")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(applog.DATA_DIR, "state.json")  # written by the app (no admin), only read by the daemon
CONFIG = os.path.join(HERE, "config.json")  # settings the elevated daemon acts on: only admins can change them
ORIGINAL = os.path.join(HERE, "original.json")  # each device's lighting before RGB Anywhere first changed it (daemon)
GROUPS = ("ram", "fans", "keyboard", "mouse", "gpu", "audio", "other")  # what the UI can target (only those a PC has)


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


def _argb_leds(value, default=60):
    """config.json argb_leds as a whole number of 1 or more. A bad value must not stop the daemon from starting."""
    try:
        n = int(str(value).strip())  # via str: 60.5 and true are refused, not rounded
    except ValueError:
        n = 0
    if n >= 1:
        return n
    _log.warning("config.json argb_leds %r is not a whole number of 1 or more; using %d", value, default)
    return default


ARGB_LEDS = _argb_leds(CFG.get("argb_leds", 60))  # LEDs to give each empty ARGB header (prepare() checks after every start)
HELPER_SERVICE = CFG.get("helper_service")  # a sensor service that disturbs OpenRGB's RAM detection; stopped during rescans
RAM_EXPECTED = CFG.get("ram_expected")      # sticks that should be found; None = don't check
RAM_SAME_MODEL = bool(CFG.get("ram_same_model", False))  # flag sticks detected as different models (a misread on matched kits)


NAMES = {
    "blue": "0000FF", "red": "FF0000", "green": "00FF00", "white": "FFFFFF",
    "purple": "8000FF", "pink": "FF0080", "cyan": "00FFFF", "yellow": "FFFF00",
    "orange": "FF6000", "autumn": "FF3C00", "black": "000000",
}


def to_hex(s):
    """Color name or hex (0000FF / #0000FF) -> "0000FF". Raises ValueError for anything else."""
    h = NAMES.get(s.lower(), s.lstrip("#")).upper()
    if len(h) != 6 or any(c not in "0123456789ABCDEF" for c in h):
        raise ValueError("not a color: %r (use a name like blue or a hex code like 0000FF)" % s)
    return h


def valid_hex(h):
    try:
        return to_hex(str(h)) == str(h).upper()
    except ValueError:
        return False


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


# Every DeviceType OpenRGB 1.0 reports (rgbcore_client.DeviceType). Types with no group of their own go to "other".
_TYPE_GROUP = {"DRAM": "ram", "MOTHERBOARD": "fans", "LEDSTRIP": "fans", "COOLER": "fans", "CASE": "fans",
               "KEYBOARD": "keyboard", "KEYPAD": "keyboard", "MOUSE": "mouse", "MOUSEMAT": "mouse", "GPU": "gpu",
               "HEADSET": "audio", "HEADSET_STAND": "audio", "SPEAKER": "audio", "MICROPHONE": "audio"}
# OpenRGB files some fan hubs, docks and mats under ACCESSORY, LIGHT or UNKNOWN; their names say what they are.
# Whole words, checked in order, so "Mouse Dock" is a mouse and "Addressable" is not DDR memory.
_NAME_GROUP = [(re.compile(p, re.I), g) for p, g in (
    (r"\bddr\d*\b|\bdram\b", "ram"), (r"\bmouse", "mouse"), (r"\bkeyboard|\bkeypad", "keyboard"),
    (r"\bheadset|\bspeaker|\bmicrophone", "audio"),
    (r"\bfans?\b|\bargb\b|\b(led|light) strip|\bcommander\b|\blighting node\b|\bhub\b|\bcooler\b", "fans"))]
_NAME_GUESS_TYPES = {None, "ACCESSORY", "LIGHT", "UNKNOWN", "VIRTUAL"}
GROUP_LABELS = {"ram": "RAM", "fans": "Fans & case", "keyboard": "Keyboard", "mouse": "Mouse", "gpu": "Graphics card",
                "audio": "Audio", "other": "Other"}


def group_of(dev):
    """Which UI group a device belongs to (one of GROUPS), from its OpenRGB type, or its name when the type says little.

    `dev` is an OpenRGB device or a name string. config.json `group_overrides` ({"substring of device name": "group"})
    wins over both.
    """
    name = dev if isinstance(dev, str) else dev.name
    for needle, group in CFG.get("group_overrides", {}).items():
        if needle.lower() in name.lower() and group in GROUPS:
            return group
    t = getattr(getattr(dev, "type", None), "name", None)
    if t in _TYPE_GROUP:
        return _TYPE_GROUP[t]
    if t in _NAME_GUESS_TYPES:
        for pattern, group in _NAME_GROUP:
            if pattern.search(name):
                return group
    return "other"


def load_state():
    state = {"color": "0000FF", "mode": "static", "groups": {}, "brightness": 100}
    try:
        with open(STATE) as f:
            state.update(json.load(f))
    except FileNotFoundError:
        _log.info("no state.json yet, using defaults")
        return state
    except Exception:
        # Keep the damaged file for inspection instead of silently overwriting it with defaults.
        keep = "%s.corrupt-%s" % (STATE, time.strftime("%Y%m%d-%H%M%S"))
        _log.exception("state.json is unreadable; using defaults. A copy was kept as %s", keep)
        applog.event("state_corrupt", ok=False, error="state.json unreadable", kept=keep)
        try:
            if not (applog.PROTECTED and applog.IS_ADMIN):  # the daemon never changes files in the user's folder
                os.replace(STATE, keep)
        except OSError:
            pass
    # A bad color (an older rgb.py saved any argument as one) would make every device fail: fall back and say so.
    if not valid_hex(state.get("color")):
        _log.warning("state.json has an invalid color %r; using 0000FF", state.get("color"))
        state["color"] = "0000FF"
    groups = state.get("groups") or {}
    for g in [g for g, h in groups.items() if not valid_hex(h)]:
        _log.warning("state.json has an invalid %s color %r; dropping it", g, groups[g])
        del groups[g]
    return state


def save_state(state):
    """Write atomically (temp file + replace) so a crash or a second process can't leave a half-written file."""
    tmp = "%s.%d.tmp" % (STATE, os.getpid())
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
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
    """Connect to the lighting daemon (rgbd.py), starting it through its elevated "RGB Anywhere Lighting" task if needed.

    The daemon is elevated because RAM and some boards are reached over SMBus; this process need not be.
    """
    return _retry(lambda: rgbcore_client.Client(rgbcore_client.RemoteBackend()), wait)


def daemon(wait=30):
    """The daemon's RemoteBackend, without asking for devices: it answers even when OpenRGB did not start."""
    return _retry(lambda: rgbcore_client.RemoteBackend(timeout=60), wait)


def wait_for_rescan(backend, start=10, timeout=180, sleep=time.sleep, clock=time.time):
    """Wait until the daemon has started and finished a rescan. The rescan task takes a few seconds to reach the
    daemon, so wait up to `start` seconds to see it busy. Returns True if it was seen finishing."""
    t0, seen = clock(), False
    while clock() - t0 < timeout:
        busy = backend.request("busy")
        if busy:
            seen = True
        elif seen or clock() - t0 > start:
            return seen
        sleep(0.5)
    return False


def _retry(make, wait):
    """make() until it works, starting the daemon's task after the first failure."""
    last = None
    for attempt in range(wait):
        try:
            got = make()
            _log.info("connected to the lighting daemon%s", " after %d attempts" % (attempt + 1) if attempt else "")
            return got
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            if attempt == 0:
                _log.warning("lighting daemon not reachable (%s); starting it", last)
                r = _quiet("schtasks", "/run", "/tn", rgbcore_client.TASK_LIGHTING)
                _log.info("schtasks /run 'RGB Anywhere Lighting' -> exit %s %s", r.returncode, (r.stdout + r.stderr).strip()[:200])
            time.sleep(1)
    _log.error("Could not connect to the lighting daemon within %ds (last error: %s)", wait, last)
    applog.event("core_connect", ok=False, error="could not connect within %ds" % wait, last=last)
    raise ConnectionError("Could not connect to the lighting daemon (%s)" % last)


def is_elevated():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def argb_size(zone):
    """LEDs to give `zone` if it is an empty addressable header, else None. ARGB_LEDS is kept within the zone's range:
    OpenRGB 1.0 does not check the range itself, and a controller may not cope with more LEDs than leds_max."""
    if zone.leds_max is None:  # a daemon that does not report the range: the old rule
        return ARGB_LEDS if zone.type.name == "LINEAR" and len(zone.leds) < 2 else None
    if zone.leds_min >= zone.leds_max or len(zone.leds) >= 2:  # fixed LEDs, or already sized
        return None
    return max(zone.leds_min, min(ARGB_LEDS, zone.leds_max))


def prepare(client):
    client.update()
    for dev in client.devices:
        for zone in dev.zones:
            n = argb_size(zone)
            if n is None or n == len(zone.leds):
                continue
            if n != ARGB_LEDS:
                _log.warning("%s / %s takes %d to %d LEDs; giving it %d instead of argb_leds %d", dev.name, zone.name,
                             zone.leds_min, zone.leds_max, n, ARGB_LEDS)
            _log.info("resizing %s / %s from %d to %d LEDs", dev.name, zone.name, len(zone.leds), n)
            try:
                zone.resize(n)
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


def load_originals():
    """The devices' lighting saved before RGB Anywhere first changed it (see remember_originals), or []."""
    try:
        with open(ORIGINAL, encoding="utf-8") as f:
            saved = json.load(f)
        return saved if isinstance(saved, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        _log.exception("original.json is unreadable; nothing to restore from it")
        return []


def remember_originals(backend):
    """Save the lighting of every device not saved yet, before anything here changes it. Call it before apply().

    Run on each detection, so a device plugged in later is saved too, before its first colour. A device saved once is
    never saved again, so the file keeps what it had before RGB Anywhere, not what RGB Anywhere set. Returns how many
    devices were added. Never raises: failing to save must not stop the lights."""
    try:
        saved = load_originals()
        known = {tuple(s.get("key") or ()) for s in saved}
        new = [s for s in backend.snapshot() if tuple(s["key"]) not in known]
        if not new:
            return 0
        tmp = "%s.%d.tmp" % (ORIGINAL, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(saved + new, f, indent=1)
        os.replace(tmp, ORIGINAL)
        _log.info("saved the original lighting of %d device(s): %s", len(new),
                  "; ".join("%s (%s)" % (s["name"], s["mode"]) for s in new))
        applog.event("originals_saved", ok=True, devices=[s["name"] for s in new])
        return len(new)
    except Exception as e:
        _log.exception("could not save the devices' original lighting")
        applog.event("originals_saved", ok=False, error="%s: %s" % (type(e).__name__, e))
        return 0


def readable(saved):
    """Whether a saved device holds lighting OpenRGB really read. Most controllers can't be read back: OpenRGB then
    reports a colour mode (Direct, Static) or Off with every colour black, and restoring that would switch the lights
    off. Effect modes that take no colour (Rainbow) are real readings."""
    takes_colour = saved.get("color_mode") in (1, 2)  # ModeColors PER_LED, MODE_SPECIFIC
    black = not any(saved.get("leds") or []) and not any(saved.get("mode_colors") or [])
    return not (black and (takes_colour or str(saved.get("mode", "")).lower() == "off"))


def restorable():
    """The saved devices worth restoring, and those whose original lighting could not be read (both as saved)."""
    saved = load_originals()
    return [s for s in saved if readable(s)], [s for s in saved if not readable(s)]


# The vendor app that keeps a profile for a device, by vendor or name. Opening it puts that profile back.
VENDOR_APPS = [(re.compile(p, re.I), app) for p, app in (
    (r"\brazer\b", "Razer Synapse"), (r"\bcorsair\b", "Corsair iCUE"), (r"\basus\b|\baura\b|\brog\b", "ASUS Armoury Crate"),
    (r"\blogitech\b", "Logitech G HUB"), (r"\bmsi\b|\bmystic light\b", "MSI Center"),
    (r"\bgigabyte\b|\baorus\b", "Gigabyte Control Center"), (r"\bsteelseries\b", "SteelSeries GG"), (r"\bnzxt\b", "NZXT CAM"),
    (r"\blian li\b", "Lian Li L-Connect"), (r"\bcooler master\b", "Cooler Master MasterPlus"), (r"\bhyperx\b", "HyperX NGENUITY"),
    (r"\bthermaltake\b", "Thermaltake TT RGB Plus"), (r"\basrock\b", "ASRock Polychrome RGB"))]
# Built-in effects most devices have; the first one a device has is its "effect" on exit.
EFFECT_MODES = ("spectrum cycle", "rainbow wave", "rainbow", "color cycle", "colour cycle", "spectrum", "color wave", "wave")
LAST_RESTORE = {}  # what the latest restore did, for the app to tell the user


def vendor_app(dev):
    """The vendor app that keeps this device's lighting, or None."""
    text = "%s %s" % (getattr(dev, "vendor", "") or "", dev.name)
    return next((app for pattern, app in VENDOR_APPS if pattern.search(text)), None)


def exit_choice(state, group):
    """What a device of `group` shows when its original lighting can't be read: "leave" (hand it back: it keeps its
    colour until its vendor app sets it again), "effect" (its built-in rainbow-like effect) or a hex colour. Set per
    group in state["exit"], or for every group under "all"."""
    ex = state.get("exit") or {}
    choice = ex.get(group, ex.get("all", "leave"))
    return choice if choice in ("leave", "effect") or valid_hex(choice) else "leave"


def restore(client, log=print, state=None):
    """Put every saved device back the way it was before RGB Anywhere (state mode "original"), where OpenRGB could read
    it. A device it could not read gets its group's exit choice (see exit_choice); left as it is, its vendor app is
    named so the user can open it. Returns failures; LAST_RESTORE says what happened."""
    state = state or {}
    ok, unknown = restorable()
    res = client.restore(ok)
    done = {"restored": res["restored"], "missing": res["missing"], "failed": list(res["failed"]), "colored": [],
            "effect": [], "handed_back": []}
    by_key = {tuple(d.key): d for d in client.devices}
    for s in unknown:
        dev = by_key.get(tuple(s.get("key") or ()))
        if dev is None:
            done["missing"].append(s.get("name", "?"))
            continue
        choice = exit_choice(state, group_of(dev))
        try:
            effect = next((m.name for m in dev.modes if m.name.lower() in EFFECT_MODES), None) if choice == "effect" else None
            if valid_hex(choice) and dev_mode_name(dev, "static"):
                dev.set_mode(dev_mode_name(dev, "static"))
                dev.set_color(to_color(choice.upper()))
                done["colored"].append(dev.name)
            elif effect:
                dev.set_mode(effect)
                done["effect"].append(dev.name)
            else:  # "leave", or a device with no static mode or no built-in effect
                done["handed_back"].append({"name": dev.name, "app": vendor_app(dev)})
        except Exception as e:
            done["failed"].append(dev.name)
            _log.warning("restore: %s exit choice %r failed: %s: %s", dev.name, choice, type(e).__name__, e)
    _log.info("restore: %d restored, %d set to their exit colour, %d to their effect, handed back %s, missing %s, "
              "failed %s", len(done["restored"]), len(done["colored"]), len(done["effect"]),
              ["%s (%s)" % (h["name"], h["app"] or "no vendor app known") for h in done["handed_back"]] or "none",
              done["missing"] or "none", done["failed"] or "none")
    for name in done["restored"]:
        log("ok   " + name)
    for h in done["handed_back"]:
        log("keep %s (its original lighting could not be read%s)" % (h["name"], "; open %s" % h["app"] if h["app"] else ""))
    for name in done["failed"]:
        log("fail %s (could not restore)" % name)
    LAST_RESTORE.clear()
    LAST_RESTORE.update(done)
    applog.event("lights_restore", ok=not done["failed"], restored=len(done["restored"]), colored=done["colored"],
                 effect=done["effect"], handed_back=[h["name"] for h in done["handed_back"]], missing=done["missing"],
                 failed=done["failed"])
    return len(done["failed"])


def handback_message(done=None):
    """One line for the user about devices left as they are, naming the apps that can put their lighting back."""
    done = LAST_RESTORE if done is None else done
    left = done.get("handed_back") or []
    if not left:
        return ""
    apps = sorted({h["app"] for h in left if h["app"]})
    return "%d device(s) couldn't be read and keep their current colours.%s" % (
        len(left), " Open %s to put their own lighting back, or pick an exit colour." % ", ".join(apps) if apps else
        " Pick an exit colour for them.")


def handback_apps(done=None):
    """The vendor apps of the devices the latest restore handed back."""
    done = LAST_RESTORE if done is None else done
    return sorted({h["app"] for h in done.get("handed_back") or [] if h["app"]})


def apply(client, state, log=print):
    """Send the state to every device. Returns how many devices failed (a stale connection fails them all)."""
    mode = state["mode"]
    if mode == "original":
        return restore(client, log, state)
    failed = 0
    _log.info("apply: mode=%s color=%s groups=%s brightness=%s calibration=%s%s", mode, state.get("color"),
              state.get("groups"), state.get("brightness"), state.get("calibration"),
              " TEST=%s" % state["test"] if state.get("test") else "")
    for dev in client.devices:
        try:
            want = dev_mode_name(dev, mode)
            if want is None:  # only effect modes (a fan that only cycles colours): nothing to set, and nothing broke
                _log.info("%s has no static, direct or custom mode; left as it is (modes: %s)", dev.name,
                          ", ".join(m.name for m in dev.modes) or "none")
                log("skip " + dev.name)
                continue
            if want == "custom":
                # Corsair RAM sometimes comes up without Direct (OpenRGB misreads its protocol version);
                # Custom is its per-LED static mode and is always there.
                _log.warning("%s has no Direct mode; using Custom instead", dev.name)
            dev.set_mode(want)
            if mode == "off":
                color = RGBColor(0, 0, 0)
            else:
                group = group_of(dev)
                hexc = state.get("test") or color_for(state, group)  # "test" = calibration preview
                gains = state.get("calibration", {}).get(group, (100, 100, 100))
                color = to_color(hexc, state.get("brightness", 100), gains)
            how = dev.set_color(color)
            active = next((m for m in dev.modes if m.name.lower() == want), None)
            _log.debug("ok   %s: mode %s (%s) <- #%02X%02X%02X as %s", dev.name, active.name if active else "?",
                       active.color_kind if active else "?", color.red, color.green, color.blue,
                       rgbcore_client.SET_RESULTS.get(how, "?"))
            if how == 2 and mode != "off":
                _log.info("%s: mode %s takes no colour, so the colour was not applied", dev.name, active.name if active else "?")
            elif how == 3:
                _log.warning("%s has no LEDs, so no colour was applied. An addressable header needs a size: see argb_leds "
                             "in README.md", dev.name)
            log("ok   " + dev.name)
        except Exception as e:
            failed += 1
            _log.warning("FAILED on %s: %s: %s", dev.name, type(e).__name__, e, exc_info=True)
            log("fail %s %s" % (dev.name, e))
    if failed:
        applog.event("lights_apply", ok=False, error="%d of %d devices failed" % (failed, len(client.devices)),
                     mode=mode, color=state.get("color"))
    return failed


def dev_mode_name(dev, mode):
    """The mode apply() picks for `dev`, or None if it has none that takes a colour we set."""
    names = [m.name.lower() for m in dev.modes]
    if mode in names:
        return mode
    order = ("direct", "static") if mode == "off" else ("static", "direct", "custom")
    return next((n for n in order if n in names), None)


def clean_lighting(p):
    """A preset or profile that comes back from outside (Undo on the page or a phone), with its lighting checked:
    colours valid, groups known, brightness 5-100, name short. Other keys are kept. Raises ValueError."""
    if not isinstance(p, dict):
        raise ValueError("not a preset or profile: %r" % (p,))
    out = dict(p)
    out["color"] = to_hex(str(p.get("color", "")))
    if "groups" in p:
        groups = p["groups"] or {}
        if not isinstance(groups, dict) or any(g not in GROUPS for g in groups):
            raise ValueError("bad groups: %r" % (groups,))
        out["groups"] = {g: to_hex(str(c)) for g, c in groups.items()}
    if "brightness" in p:
        out["brightness"] = max(5, min(100, int(p["brightness"])))
    if "name" in p:
        out["name"] = str(p["name"])[:24]
    return out


def set_group_color(state, target, color):
    """Set `target` ("all" or one of GROUPS) to `color` (a name or hex) in state. Raises ValueError for anything else,
    so a bad request from the phone or rgbctl never reaches state.json or the devices."""
    if target != "all" and target not in GROUPS:
        raise ValueError("unknown target %r (use all, %s)" % (target, ", ".join(GROUPS)))
    h = to_hex(str(color))
    if target == "all":
        state["color"], state["groups"] = h, {}
    else:
        state.setdefault("groups", {})[target] = h
    state["mode"] = "static"
    return h


def diagnose():
    """Ask the daemon what it found, print the report with likely causes, and save it under logs\\. Changes nothing."""
    import datetime
    import diag
    try:
        d = daemon(wait=30).debug()
    except (ConnectionError, RuntimeError, OSError, ValueError) as e:  # what this process can see on its own
        d = diag.collect_local("%s: %s" % (type(e).__name__, e))
    report = diag.text(d)
    print(report)
    os.makedirs(applog.LOG_DIR, exist_ok=True)
    path = os.path.join(applog.LOG_DIR, "diagnose-%s.txt" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(report + "\n\n" + json.dumps(d, indent=2, ensure_ascii=False))
    print("\nSaved to " + path)
    applog.event("diagnose", ok=not d["hints"], hints=d["hints"], devices=len(d["devices"] or []), daemon=d["daemon"])
    return 0 if d["daemon"] else 1


def _quiet(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="ignore", creationflags=0x08000000)


SERVICE_STATES = ("STOPPED", "START_PENDING", "STOP_PENDING", "RUNNING", "CONTINUE_PENDING", "PAUSE_PENDING", "PAUSED")


def service_state(name):
    """"RUNNING", "STOPPED", "STOP_PENDING"... or None if there is no such service."""
    return parse_service_state(_quiet("sc", "query", name).stdout)


def parse_service_state(text):
    """The state from `sc query` output. Matched by its value ("4  RUNNING"), not its label, which some Windows
    languages translate."""
    for line in text.splitlines():
        words = line.replace(":", " ").split()
        if len(words) >= 2 and words[-1] in SERVICE_STATES and words[-2].isdigit():
            return words[-1]
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


CORSAIR_SERVICE = "CorsairDeviceControlService"


def _corsair_log():
    """The newest log of the Corsair service, (path, size) or (None, 0)."""
    import glob
    logs = glob.glob(os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Corsair", "Logs",
                                  CORSAIR_SERVICE, "cdcs-*.log"))
    if not logs:
        return None, 0
    newest = max(logs, key=os.path.getmtime)
    return newest, os.path.getsize(newest)


def switch_ram_to_application(wait=30):
    """Get RAM sticks out of bootloader mode. A stick that SMBus traffic clashed with falls into it and then ignores
    Direct writes (OpenRGB lists it without Direct mode, or under the wrong name); only Corsair's own service
    switches it back. So run that service just long enough to enumerate the sticks, then stop it again.
    OpenRGB must not be running meanwhile. Returns True if the service ran. Needs admin."""
    state = service_state(CORSAIR_SERVICE)
    if state is None:
        return False
    if state != "STOPPED":  # iCUE (or something else) has it running: it handles the sticks itself
        _log.info("%s is %s; not starting it to revive the RAM", CORSAIR_SERVICE, state)
        return False
    _, offset = _corsair_log()
    r = _quiet("sc", "start", CORSAIR_SERVICE)
    if r.returncode not in (0, 1056):
        _log.warning("couldn't start %s (exit %s): %s", CORSAIR_SERVICE, r.returncode, (r.stdout + r.stderr).strip()[:150])
        applog.event("ram_revive", ok=False, error="sc start exit %s" % r.returncode)
        return False
    t0 = time.time()
    switched = 0
    try:
        while time.time() - t0 < wait:
            time.sleep(1)
            path, size = _corsair_log()
            if path and size > offset:
                try:
                    with open(path, "rb") as f:
                        f.seek(offset if offset <= size else 0)
                        text = f.read().decode("utf-8", "ignore")
                    switched = text.count("to mode: Application")
                    if "DRAM enumeration" in text and time.time() - t0 > 6 and (
                            "Detected module" in text and ("proto=Bootloader" not in text or switched)):
                        time.sleep(2)  # let the last switch finish
                        break
                except OSError:
                    pass
    finally:
        _quiet("sc", "stop", CORSAIR_SERVICE)
        t1 = time.time()
        while service_state(CORSAIR_SERVICE) not in ("STOPPED", None) and time.time() - t1 < 15:
            time.sleep(0.5)
        time.sleep(3)  # let it let go of the SMBus
    _log.info("%s ran %.0fs to revive the RAM; switched %d stick(s) to Application mode", CORSAIR_SERVICE,
              time.time() - t0, switched)
    applog.event("ram_revive", ok=True, switched=switched, seconds=round(time.time() - t0))
    return True


def start_pawnio(name="PawnIO", wait=10):
    """Start the PawnIO driver if it is installed but stopped. It is a demand-start service, so after a restart nothing
    else starts it, and OpenRGB then finds no SMBus (no RAM). Returns True if it was started here. Needs admin."""
    if service_state(name) != "STOPPED":
        return False
    r = _quiet("sc", "start", name)
    t0 = time.time()
    while service_state(name) not in ("RUNNING", None):
        if time.time() - t0 > wait:
            break
        time.sleep(0.25)
    state = service_state(name)
    _log.info("PawnIO was stopped; sc start %s -> exit %s, now %s", name, r.returncode, state)
    if state != "RUNNING":
        _log.warning("couldn't start %s (exit %s): %s", name, r.returncode, (r.stdout + r.stderr).strip()[:150])
        applog.event("pawnio_start", ok=False, error="sc start exit %s, state %s" % (r.returncode, state))
        return False
    applog.event("pawnio_start", ok=True)
    return True


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
    if "--diagnose" in args:
        sys.exit(diagnose())
    if "--restore" in args:  # Uninstall.exe, before it removes the daemon: hand the devices back, change nothing else
        failed = restore(connect(wait=20), state=state)
        import handback
        print(handback.summary(handback.restart(handback_apps())) or "nothing to hand back")
        sys.exit(1 if failed else 0)
    unknown = [a for a in args if a.startswith("--") and a != "--restart"]
    if unknown:
        sys.exit("unknown option %s. See the usage at the top of rgb.py." % " ".join(unknown))
    if "--restart" in args:  # re-detect devices (the daemon stops/starts the cooler helper around it)
        args.remove("--restart")
        applog.event("core_rescan", ok=True, source="cli")
        connect().rescan()
    groups = [a for a in args if "=" in a]
    rest = [a for a in args if "=" not in a]
    if rest == ["original"]:
        state["mode"] = "original"
    elif rest:
        state["color"] = to_hex(rest[0])
        state["mode"] = " ".join(rest[1:]).lower() or "static"
        state["groups"] = {}  # new overall color resets per-device colors
    for g in groups:
        name, val = g.split("=", 1)
        name = name.lower()
        if name == "brightness":
            state["brightness"] = max(0, min(100, int(val)))
        elif name in GROUPS:
            state.setdefault("groups", {})[name] = to_hex(val)
        else:
            sys.exit("unknown group %r (use %s or brightness)" % (name, ", ".join(GROUPS)))

    try:
        client = connect()  # the daemon answers once its device detection has finished
    except ConnectionError as e:
        sys.exit(str(e))
    prepare(client)
    apply(client, state)
    save_state(state)

if __name__ == "__main__":
    main()
