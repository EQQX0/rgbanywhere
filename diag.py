"""What OpenRGB found, and plain-language reasons for anything missing.

collect() runs inside the lighting daemon (it needs OpenRGB and the daemon's own elevation); text() turns the
result into the report that `python rgb.py --diagnose`, the daemon log and the debug report show. Reading only:
nothing here changes a device.

Copyright (C) 2026 EQQX2
SPDX-License-Identifier: GPL-3.0-or-later
"""
import csv
import glob
import os
import platform
import subprocess
import sys

import rgb
import rgbcore_client as rc

HERE = os.path.dirname(os.path.abspath(__file__))
PAWNIO_SERVICE = "PawnIO"
# Programs that talk to the same hardware. Two programs driving one device fight, and the loser usually wins.
CONFLICTS = {
    "openrgb.exe": "OpenRGB", "icue.exe": "Corsair iCUE", "lghub.exe": "Logitech G HUB",
    "lghub_agent.exe": "Logitech G HUB", "lghub_system_tray.exe": "Logitech G HUB",
    "armourycrate.service.exe": "ASUS Armoury Crate", "armourycrate.usersessionhelper.exe": "ASUS Armoury Crate",
    "lightingservice.exe": "ASUS Aura lighting service", "signalrgb.exe": "SignalRGB",
    "msi.centralserver.exe": "MSI Center", "rgbfusion.exe": "Gigabyte RGB Fusion",
}


def _running_conflicts(own_pids=()):
    """Other RGB programs that are running. Our own bundled OpenRGB (own_pids) does not count."""
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="ignore",
                           timeout=20, creationflags=0x08000000)
    except Exception:
        return []
    names = set()
    for row in csv.reader(r.stdout.splitlines()):
        if len(row) > 1 and not (row[0].lower() == "openrgb.exe" and row[1] in {str(p) for p in own_pids}):
            names.add(row[0].lower())
    return sorted({label for exe, label in CONFLICTS.items() if exe in names})


def _log_problems(path, n=12):
    """The last error and warning lines of OpenRGB's own log (OpenRGB_*.log), without its long HTML dialogs."""
    return [l[:300] for l in _log_lines(path, ("][Error", "][Warning"))[-n:]]


def _log_lines(path, needles):
    if not path:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [l.strip() for l in f if any(x in l for x in needles)]
    except OSError:
        return []


def _newest_log(log_dir):
    logs = glob.glob(os.path.join(log_dir, "OpenRGB_*.log"))
    return max(logs, key=os.path.getmtime) if logs else None


def _build(core_dir):
    """Which OpenRGB build fetch-openrgb.ps1 put there (first line of fetched-from.txt), or None."""
    try:
        with open(os.path.join(core_dir, "fetched-from.txt"), encoding="ascii", errors="replace") as f:
            return f.readline().strip() or None
    except OSError:
        return None


def _device(d):
    modes = [{"name": m.name, "colors": m.color_kind} for m in d.modes]
    active = d.modes[d.active_mode] if 0 <= d.active_mode < len(d.modes) else None
    dim = (active is not None and active.brightness_max is not None and active.brightness_min != active.brightness_max
           and active.brightness == active.brightness_min)
    return {"name": d.name, "vendor": d.vendor, "type": d.type.name, "group": rgb.group_of(d), "location": d.location,
            "leds": d.led_count,
            "zones": ["%s (%s, %d LEDs%s)" % (z.name, z.type.name, len(z.leds),
                                              ", takes %s-%s" % (z.leds_min, z.leds_max) if z.leds_max else "")
                      for z in d.zones],
            "empty_headers": [z.name for z in d.zones if not z.leds and z.leds_max],
            "active_mode": active.name if active is not None else "?", "dim": dim, "modes": modes}


def _local(core_dir, log_dir, own_pid=None):
    """What any process can see: files, services, other programs, OpenRGB's log. Needs no admin and no daemon."""
    log = _newest_log(log_dir)
    return {
        "python": sys.executable,
        "os": platform.platform(),
        "install_dir": HERE,
        "core_exe": os.path.join(core_dir, "OpenRGB.exe"),
        "core_exe_present": os.path.exists(os.path.join(core_dir, "OpenRGB.exe")),
        "core_build": _build(core_dir),
        "core_log_dir": log_dir,
        "core_log": log,
        "core_log_problems": _log_problems(log),
        # OpenRGB logs each SMBus / I2C bus it opens; the SDK does not list them
        "core_log_buses": [l[:200] for l in _log_lines(log, ("Registering I2C interface",))][-12:],
        "pawnio": {
            "service": rgb.service_state(PAWNIO_SERVICE),
            "lib": os.path.exists(os.path.join(core_dir, "PawnIOLib.dll")),
            "modules": sorted(os.path.basename(p) for p in glob.glob(os.path.join(core_dir, "*.bin"))),
        },
        "helper_service": {"name": rgb.HELPER_SERVICE, "state": rgb.service_state(rgb.HELPER_SERVICE) if rgb.HELPER_SERVICE else None},
        "conflicts": _running_conflicts([own_pid] if own_pid else []),
        "config": {k: rgb.CFG[k] for k in ("ram_expected", "argb_leds", "helper_service", "core_log_level", "group_overrides")
                   if k in rgb.CFG},
    }


def collect(core, client, start_error=None):
    """Everything the daemon knows about detection, as plain data (sent to the app as JSON). core and client are None
    when OpenRGB did not start; start_error then says why."""
    info = core.debug() if core is not None else {"started": False, "running": False, "pid": None}
    info["start_error"] = start_error
    if client is not None:
        try:
            client.update()
        except Exception as e:  # report what is known instead of failing the whole diagnosis
            info["update_error"] = "%s: %s" % (type(e).__name__, e)
    core_dir = getattr(core, "core_dir", os.path.join(HERE, "openrgb"))
    log_dir = getattr(core, "log_dir", os.path.join(HERE, "cache", "core", "logs"))
    d = _local(core_dir, log_dir, info.get("pid"))
    d.update({
        "daemon": True,
        "daemon_error": None,
        "elevated": rgb.is_elevated(),
        "pid": os.getpid(),
        "core_version": core.version() if core is not None else "not started",
        "core": info,
        "devices": [_device(x) for x in client.devices] if client is not None else [],
    })
    d["hints"] = hints(d)
    return d


def collect_local(error):
    """The diagnosis when the daemon does not answer: what this (normal user) process can see on its own."""
    d = _local(os.path.join(HERE, "openrgb"), os.path.join(HERE, "cache", "core", "logs"))
    d.update({"daemon": False, "daemon_error": error, "elevated": None, "pid": None, "core_version": "unknown",
              "core": {"started": None, "running": None, "pid": None, "start_error": None}, "devices": None})
    d["hints"] = hints(d)
    return d


def _start_hint(err, d):
    """Plain advice for SdkBackend's start errors."""
    log = "OpenRGB's log is in %s." % d["core_log_dir"]
    if err.startswith("FileNotFoundError") or not d["core_exe_present"]:
        return ("OpenRGB is missing (%s). Reinstall RGB Anywhere, or in a source checkout run "
                "'powershell -ExecutionPolicy Bypass -File fetch-openrgb.ps1'." % d["core_exe"])
    if "Another program is using port" in err:
        return ("OpenRGB could not start: %s Close that program, then press Rescan." % err.split(": ", 1)[-1])
    if "stopped" in err and "times in" in err:
        return "OpenRGB keeps exiting, so it is no longer started automatically. Press Rescan to try again. " + log
    if "exited with code" in err:
        return "OpenRGB exited while starting (%s). Its last log lines below may say why. %s" % (err.split(": ", 1)[-1], log)
    if err.startswith("TimeoutError"):
        return ("OpenRGB started but did not open its SDK server on 127.0.0.1:%d within 2 minutes. "
                "Another copy of OpenRGB or a security program may be blocking it. %s" % (rc.SDK_PORT, log))
    return "OpenRGB did not start: %s. %s" % (err, log)


def hints(d):
    """Likely reasons for missing devices or colours that don't change, most important first."""
    out = []
    devs = d["devices"] or []
    core = d["core"]
    ram = [x for x in devs if x["group"] == "ram"]
    if not d["daemon"]:
        out.append("The lighting daemon does not answer (%s). Start it with: schtasks /run /tn \"%s\". If it stops again, "
                   "read logs\\errors.log; check the task with: schtasks /query /tn \"%s\" /v /fo list"
                   % (d["daemon_error"], rc.TASK_LIGHTING, rc.TASK_LIGHTING))
    elif core.get("start_error"):
        out.append(_start_hint(core["start_error"], d))
    elif core.get("running") is False:
        out.append("OpenRGB is not running. Press Rescan to start it again; %s holds its log." % d["core_log_dir"])
    if d["daemon"] and core.get("update_error"):
        out.append("OpenRGB did not answer the device list request (%s). Press Rescan." % core["update_error"])
    if core.get("restarts"):
        out.append("OpenRGB stopped and was started again %d time(s) in the last 5 minutes (last problem: %s). If it keeps "
                   "happening, its log in %s may say why." % (core["restarts"], core.get("last_error") or "unknown",
                                                              d["core_log_dir"]))
    if d["elevated"] is False:
        out.append("The lighting daemon is not running as admin, so RAM and other SMBus devices cannot be found. "
                   "Start it through its task: schtasks /run /tn \"%s\"" % rc.TASK_LIGHTING)
    svc = d["pawnio"]["service"]
    problems = " ".join(d.get("core_log_problems", []))
    if "Could not open PawnIO" in problems and svc == "RUNNING":
        out.append("A service named PawnIO runs, but OpenRGB cannot open the PawnIO device, so the RAM's SMBus stays closed. "
                   "This is usually an old or leftover driver (for example after deleting its folder). Reinstall it: "
                   "'winget uninstall namazso.PawnIO', then 'sc.exe delete PawnIO' if the service is still listed, restart the PC, "
                   "'winget install namazso.PawnIO', restart again.")
    elif "Permission Denied, PawnIO" in problems:
        out.append("PawnIO refused access: OpenRGB was not running as admin when it detected devices.")
    elif "PawnIO initialization aborted (code=" in problems:
        out.append("PawnIO opened but rejected a module. The installed PawnIO may be too old for these modules: update it with "
                   "'winget upgrade namazso.PawnIO'.")
    if svc is None:
        out.append("The PawnIO driver is not installed, so RAM lights cannot be reached. Install it with "
                   "'winget install namazso.PawnIO', restart the PC, then press Rescan.")
    elif svc != "RUNNING":
        out.append("The PawnIO driver service is %s. Restart the PC, or run 'sc start PawnIO' as admin, then press Rescan." % svc)
    if d["core_exe_present"] and (not d["pawnio"]["lib"] or not d["pawnio"]["modules"]):
        out.append("PawnIOLib.dll or the PawnIO .bin modules are missing from the openrgb folder. Reinstall RGB Anywhere.")
    # RAM sits on the motherboard's SMBus, which only PawnIO opens; the NVIDIA/AMD GPU buses don't reach it.
    if d["daemon"] and not core.get("start_error") and svc == "RUNNING" and d["elevated"] and not ram and "PawnIO" not in problems:
        out.append("PawnIO runs but no RAM was found. Close other RGB programs, then press Rescan; if it stays missing, "
                   "search OpenRGB's log for 'PawnIO' or 'SMBus'.")
    expected = d["config"].get("ram_expected")
    if expected and 0 < len(ram) < expected:
        out.append("Only %d of %d RAM sticks were found. Press Rescan; if a stick keeps missing, see Troubleshooting in README.md."
                   % (len(ram), expected))
    if d["conflicts"]:
        out.append("Other RGB programs are running: %s. They fight over the same devices, so colours may not stick "
                   "and devices may be missing. Quit them (also from the tray) and press Rescan." % ", ".join(d["conflicts"]))
    if d["devices"] is not None and not devs and not core.get("start_error"):
        out.append("No device was found at all. OpenRGB's log lists each detector it tried.")
    for x in devs:
        kinds = {m["name"]: m["colors"] for m in x["modes"]}
        if kinds.get(x["active_mode"]) in ("none", "random"):
            out.append("%s is in mode '%s', which takes no colour. Pick Static to set a colour." % (x["name"], x["active_mode"]))
        if x.get("dim"):
            out.append("%s's mode '%s' has its brightness at the minimum, so colours may not show." % (x["name"], x["active_mode"]))
        for z in x.get("empty_headers", []):
            out.append("%s / %s has 0 LEDs, so it takes no colour. It is an addressable header that was not given a size; "
                       "set argb_leds in config.json (see README.md) and press Rescan." % (x["name"], z))
    return out


def text(d):
    """Plain-text report of collect()'s or collect_local()'s result."""
    lines = ["RGB Anywhere diagnosis", ""]
    core = d["core"]
    if d["daemon"]:
        lines.append("Lighting daemon: pid %s, %s" % (d["pid"], "running as admin" if d["elevated"] else "NOT running as admin"))
    else:
        lines.append("Lighting daemon: NOT REACHABLE (%s)" % d["daemon_error"])
    lines.append("OpenRGB: %s" % d["core_version"])
    lines.append("  program: %s (%s), build: %s" % (d["core_exe"], "present" if d["core_exe_present"] else "MISSING",
                                                    d["core_build"] or "not recorded"))
    if core.get("start_error"):
        lines.append("  did not start: %s" % core["start_error"])
    elif d["daemon"]:
        lines.append("  process: %s, pid %s, SDK on 127.0.0.1:%s, protocol %s, restarted %s time(s)" % (
            "running" if core.get("running") else "NOT running" if core.get("running") is False else "started elsewhere",
            core.get("pid"), core.get("port"), core.get("protocol"), core.get("restarts", 0)))
        if core.get("last_error"):
            lines.append("  last connection problem: %s" % core["last_error"])
    p = d["pawnio"]
    lines.append("PawnIO driver: service %s; in the openrgb folder: PawnIOLib.dll %s, %d module(s) %s" % (
        p["service"] or "NOT INSTALLED", "present" if p["lib"] else "MISSING", len(p["modules"]), ", ".join(p["modules"])))
    if d["helper_service"]["name"]:
        lines.append("Helper service %s: %s" % (d["helper_service"]["name"], d["helper_service"]["state"]))
    lines.append("Other RGB programs running: %s" % (", ".join(d["conflicts"]) or "none"))
    lines.append("Config: %s" % (d["config"] or "defaults"))
    lines.append("")
    if d["devices"] is None:
        lines.append("Devices: unknown (only the daemon can list them)")
    else:
        lines.append("Devices: %d" % len(d["devices"]))
    for i, x in enumerate(d["devices"] or []):
        lines.append("  %d. %s [%s, %s] %d LEDs at %s" % (i, x["name"], x["type"], x["group"], x["leds"], x["location"] or "?"))
        lines.append("     mode now: %s; modes: %s" % (x["active_mode"], ", ".join("%s (%s)" % (m["name"], m["colors"]) for m in x["modes"])))
        if x["zones"]:
            lines.append("     zones: %s" % "; ".join(x["zones"]))
    lines.append("")
    lines.append("OpenRGB log folder: %s" % d["core_log_dir"])
    lines.append("Newest OpenRGB log: %s" % (d["core_log"] or "none yet"))
    if d["core_log_buses"]:
        lines.append("SMBus / I2C buses it registered:")
        lines += ["  " + l for l in d["core_log_buses"]]
    if d.get("core_log_problems"):
        lines.append("Last errors and warnings in it:")
        lines += ["  " + l for l in d["core_log_problems"]]
    lines.append("")
    if d["hints"]:
        lines.append("Likely problems:")
        lines += ["  %d. %s" % (i + 1, h) for i, h in enumerate(d["hints"])]
    else:
        lines.append("No problems spotted.")
    return "\n".join(lines)
