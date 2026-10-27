# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""rgbctl: control RGB Anywhere from a terminal, a script or an AI agent.

Every command prints one JSON object on stdout. Exit code 0 = done, 1 = the app refused or the arguments were wrong,
2 = the app is not running (start "RGB Anywhere" and retry).

  rgbctl status                     current state: mode, color, per-group colors, brightness, presets, profiles, features
  rgbctl color <color> [--target T] set a color. <color> is RRGGBB, #RRGGBB or a name (red, blue, warm...).
                                    T is all (default), ram, fans, keyboard, mouse, gpu, audio or other
  rgbctl brightness <5-100>
  rgbctl on | off                   off also turns the monitors off, like the Off button
  rgbctl original                   put every device back the way it was before RGB Anywhere (any color takes over again)
  rgbctl exit <group> <choice>      what devices that can't be read show on original and uninstall. group: all or a
                                    group; choice: leave (hand back to the vendor app), effect (built-in) or a color
  rgbctl presets                    list saved presets
  rgbctl preset <number>            apply preset (numbers as listed by "presets", starting at 0)
  rgbctl profiles                   list profiles
  rgbctl profile <id>               apply a profile
  rgbctl monitors-off               turn the monitors off (any key or mouse move wakes them)
  rgbctl rescan                     re-detect devices
  rgbctl describe                   machine-readable list of these commands

It talks to the running app on 127.0.0.1:47824 using the token in control.token, which the app keeps in
%LOCALAPPDATA%\\RGB Anywhere (or next to itself in a source checkout). No other dependencies.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 47824
TIMEOUT = 20  # seconds to wait for the app
GROUPS = ("all", "ram", "fans", "keyboard", "mouse", "gpu", "audio", "other")
NAMES = {"red": "FF0000", "green": "00FF00", "blue": "0000FF", "white": "FFFFFF", "yellow": "FFFF00", "cyan": "00FFFF",
         "magenta": "FF00FF", "purple": "8000FF", "orange": "FF6000", "pink": "FF3090", "warm": "FFB070", "teal": "00C0A0"}

COMMANDS = [
    {"name": "status", "args": [], "does": "Return the current lighting state, presets, profiles and features."},
    {"name": "color", "args": ["color", "--target all|ram|fans|keyboard|mouse|gpu|audio|other"], "does": "Set a static color. Accepts RRGGBB, #RRGGBB or a name."},
    {"name": "brightness", "args": ["percent 5-100"], "does": "Set brightness."},
    {"name": "on", "args": [], "does": "Switch the lights on (static, last color)."},
    {"name": "off", "args": [], "does": "Switch the lights off; the monitors turn off too."},
    {"name": "original", "args": [], "does": "Restore each device's lighting from before RGB Anywhere; a color takes over again."},
    {"name": "exit", "args": ["all|ram|fans|keyboard|mouse|gpu|audio|other", "leave|effect|color"],
     "does": "Set what devices whose original lighting can't be read show on original and uninstall."},
    {"name": "presets", "args": [], "does": "List saved presets."},
    {"name": "preset", "args": ["number"], "does": "Apply a preset by its list number."},
    {"name": "profiles", "args": [], "does": "List profiles."},
    {"name": "profile", "args": ["id"], "does": "Apply a profile by id."},
    {"name": "monitors-off", "args": [], "does": "Turn the monitors off."},
    {"name": "rescan", "args": [], "does": "Re-detect lighting devices."},
]


def out(obj, code=0):
    print(json.dumps(obj, indent=2))
    sys.exit(code)


def fail(msg, code=1):
    out({"ok": False, "error": msg}, code)


def parse_color(text):
    t = text.strip().lower()
    t = NAMES.get(t, t).lstrip("#").upper()
    if len(t) != 6 or any(c not in "0123456789ABCDEF" for c in t):
        fail("%r is not a color. Use RRGGBB, #RRGGBB or one of: %s" % (text, ", ".join(sorted(NAMES))))
    return t


def token():
    # The app writes it to the user's data folder when installed in Program Files, else next to itself (see applog)
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "RGB Anywhere", "control.token")
    path = os.environ.get("RGBCTL_TOKEN_FILE") or (local if os.path.isfile(local) else os.path.join(HERE, "control.token"))
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        fail("no control.token at %s. Start RGB Anywhere once so it can create it." % path, 2)


def request(method, path, body=None):
    port = int(os.environ.get("RGBCTL_PORT", PORT))
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", "")
        except ValueError:
            msg = ""
        fail("the app answered %s %s" % (e.code, msg))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):  # up, but stuck
            fail("RGB Anywhere did not answer within %d seconds. It may be busy or hung." % TIMEOUT)
        fail("RGB Anywhere is not running (nothing listening on 127.0.0.1:%d). Start it and retry." % port, 2)


def call(name, *args):
    r = request("POST", "/call/" + name, {"args": list(args)})
    res = r.get("result")
    if isinstance(res, dict) and res.get("error"):
        fail(res["error"])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rgbctl", description="Control RGB Anywhere. Prints JSON.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("color")
    c.add_argument("color")
    c.add_argument("--target", default="all", choices=GROUPS)
    b = sub.add_parser("brightness")
    b.add_argument("percent", type=int)
    p = sub.add_parser("preset")
    p.add_argument("number", type=int)
    pr = sub.add_parser("profile")
    pr.add_argument("id")
    ex = sub.add_parser("exit")
    ex.add_argument("group", choices=GROUPS)
    ex.add_argument("choice")
    for n in ("status", "on", "off", "original", "presets", "profiles", "monitors-off", "rescan", "describe"):
        sub.add_parser(n)
    a = ap.parse_args(argv)

    if a.cmd == "describe":
        out({"ok": True, "commands": COMMANDS, "output": "JSON", "exit_codes": {"0": "done", "1": "refused or bad arguments", "2": "app not running"}})
    if a.cmd in ("status", "presets", "profiles"):
        st = request("GET", "/status")
        if a.cmd == "presets":
            out({"ok": True, "presets": [{"number": i, **p} for i, p in enumerate(st.get("presets", []))]})
        if a.cmd == "profiles":
            out({"ok": True, "profiles": st.get("profiles", [])})
        out({"ok": True, **st})
    if a.cmd == "color":
        call("set_color", a.target, parse_color(a.color))
    elif a.cmd == "brightness":
        if not 5 <= a.percent <= 100:
            fail("brightness must be 5 to 100")
        call("set_brightness", a.percent)
    elif a.cmd == "on":
        call("set_mode", "static")
    elif a.cmd == "off":
        call("set_mode", "off")
    elif a.cmd == "original":
        call("set_mode", "original")
    elif a.cmd == "exit":
        out({"ok": True, "exit": call("set_exit", a.group, a.choice if a.choice in ("leave", "effect") else parse_color(a.choice))})
    elif a.cmd == "preset":
        if a.number < 0:
            fail("preset numbers start at 0 (see: rgbctl presets)")
        call("apply_preset", a.number)
    elif a.cmd == "profile":
        call("apply_profile", a.id)
    elif a.cmd == "monitors-off":
        call("monitors_off")
    elif a.cmd == "rescan":
        call("rescan")
    st = request("GET", "/status").get("state", {})
    out({"ok": True, "state": {k: st.get(k) for k in ("mode", "color", "groups", "brightness")}})


if __name__ == "__main__":
    main()
