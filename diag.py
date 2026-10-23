"""What the lighting core found, and plain-language reasons for anything missing.

collect() runs inside the lighting daemon (it needs the core and the daemon's own elevation); text() turns the
result into the report that `python rgb.py --diagnose`, the daemon log and the debug report show. Reading only:
nothing here changes a device.

Copyright (C) 2026 EQQX2
SPDX-License-Identifier: GPL-3.0-or-later
"""
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


def _running_conflicts():
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="ignore",
                           timeout=20, creationflags=0x08000000)
    except Exception:
        return []
    names = {line.split(",")[0].strip('"').lower() for line in r.stdout.splitlines() if line}
    return sorted({label for exe, label in CONFLICTS.items() if exe in names})


def _log_problems(path, n=12):
    """The last error and warning lines of the core's own log (OpenRGB_*.log), without its long HTML dialogs."""
    if not path:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if "][Error" in l or "][Warning" in l]
    except OSError:
        return []
    return [l[:300] for l in lines[-n:]]


def _device(d):
    modes = [{"name": m.name, "colors": m.color_kind} for m in d.modes]
    active = d.modes[d.active_mode].name if 0 <= d.active_mode < len(d.modes) else "?"
    return {"name": d.name, "vendor": d.vendor, "type": d.type.name, "group": rgb.group_of(d), "location": d.location,
            "leds": d.led_count, "zones": ["%s (%s, %d LEDs)" % (z.name, z.type.name, len(z.leds)) for z in d.zones],
            "active_mode": active, "modes": modes}


def collect(core, client):
    """Everything the daemon knows about detection, as plain data (sent to the app as JSON)."""
    client.update()
    core_dir = os.path.dirname(os.path.join(HERE, "rgbcore.dll"))
    log_dir = os.path.join(getattr(core, "config_dir", os.path.join(HERE, "cache", "core")), "logs")
    logs = sorted(glob.glob(os.path.join(log_dir, "OpenRGB_*.log")))
    d = {
        "elevated": rgb.is_elevated(),
        "pid": os.getpid(),
        "python": sys.executable,
        "os": platform.platform(),
        "install_dir": HERE,
        "core_version": core.version(),
        "core": core.debug(),
        "core_log": logs[-1] if logs else None,
        "core_log_problems": _log_problems(logs[-1] if logs else None),
        "pawnio": {
            "service": rgb.service_state(PAWNIO_SERVICE),
            "lib": os.path.exists(os.path.join(core_dir, "PawnIOLib.dll")),
            "modules": sorted(os.path.basename(p) for p in glob.glob(os.path.join(core_dir, "*.bin"))),
        },
        "helper_service": {"name": rgb.HELPER_SERVICE, "state": rgb.service_state(rgb.HELPER_SERVICE) if rgb.HELPER_SERVICE else None},
        "conflicts": _running_conflicts(),
        "config": {k: rgb.CFG[k] for k in ("ram_expected", "argb_leds", "helper_service", "core_log_level", "group_overrides")
                   if k in rgb.CFG},
        "devices": [_device(x) for x in client.devices],
    }
    d["hints"] = hints(d)
    return d


def hints(d):
    """Likely reasons for missing devices or colours that don't change, most important first."""
    out = []
    devs = d["devices"]
    buses = d["core"].get("i2c_buses")
    ram = [x for x in devs if x["group"] == "ram"]
    if not d["elevated"]:
        out.append("The lighting daemon is not running as admin, so RAM and other SMBus devices cannot be found. "
                   "Start it through its task: schtasks /run /tn \"%s\"" % rc.TASK_LIGHTING)
    svc = d["pawnio"]["service"]
    problems = " ".join(d.get("core_log_problems", []))
    if "Could not open PawnIO" in problems and svc == "RUNNING":
        out.append("A service named PawnIO runs, but the core cannot open the PawnIO device, so the RAM's SMBus stays closed. "
                   "This is usually an old or leftover driver (for example after deleting its folder). Reinstall it: "
                   "'winget uninstall namazso.PawnIO', then 'sc.exe delete PawnIO' if the service is still listed, restart the PC, "
                   "'winget install namazso.PawnIO', restart again.")
    elif "Permission Denied, PawnIO" in problems:
        out.append("PawnIO refused access: the core was not running as admin when it detected devices.")
    elif "PawnIO initialization aborted (code=" in problems:
        out.append("PawnIO opened but rejected a module. The installed PawnIO may be too old for these modules: update it with "
                   "'winget upgrade namazso.PawnIO'.")
    if svc is None:
        out.append("The PawnIO driver is not installed, so RAM lights cannot be reached. Install it with "
                   "'winget install namazso.PawnIO', restart the PC, then press Rescan.")
    elif svc != "RUNNING":
        out.append("The PawnIO driver service is %s. Restart the PC, or run 'sc start PawnIO' as admin, then press Rescan." % svc)
    if not d["pawnio"]["lib"] or not d["pawnio"]["modules"]:
        out.append("PawnIOLib.dll or the PawnIO .bin modules are missing next to rgbcore.dll. Reinstall RGB Anywhere.")
    # RAM sits on the motherboard's SMBus, which only PawnIO opens; the NVIDIA/AMD GPU buses don't reach it.
    board = [b for b in buses or [] if b["name"].startswith("PawnIO")]
    if buses is None:
        out.append("rgbcore.dll is older than this app and reports no SMBus details. Rebuild it with build.ps1 or reinstall.")
    elif not board and d["elevated"] and svc == "RUNNING" and "PawnIO" not in problems:
        out.append("PawnIO runs but no motherboard SMBus was opened. Search the core log for 'PawnIO' to see why.")
    elif board and not ram:
        out.append("The motherboard SMBus is open but no RAM was found. Close other RGB programs, then press Rescan.")
    expected = d["config"].get("ram_expected")
    if expected and 0 < len(ram) < expected:
        out.append("Only %d of %d RAM sticks were found. Press Rescan; if a stick keeps missing, see Troubleshooting in README.md."
                   % (len(ram), expected))
    if d["conflicts"]:
        out.append("Other RGB programs are running: %s. They fight over the same devices, so colours may not stick "
                   "and devices may be missing. Quit them (also from the tray) and press Rescan." % ", ".join(d["conflicts"]))
    if not devs:
        out.append("No device was found at all. The core log lists each detector it tried.")
    for x in devs:
        kinds = {m["name"]: m["colors"] for m in x["modes"]}
        if kinds.get(x["active_mode"]) in ("none", "random"):
            out.append("%s is in mode '%s', which takes no colour. Pick Static to set a colour." % (x["name"], x["active_mode"]))
    return out


def text(d):
    """Plain-text report of collect()'s result."""
    lines = ["RGB Anywhere diagnosis", ""]
    lines.append("Daemon: pid %s, %s" % (d["pid"], "running as admin" if d["elevated"] else "NOT running as admin"))
    lines.append("Core: %s" % d["core_version"])
    core = d["core"]
    if "note" in core:
        lines.append("  " + core["note"])
    else:
        lines.append("  log level %s, detection: %s" % (core.get("log_level"), core.get("detection") or "-"))
    p = d["pawnio"]
    lines.append("PawnIO: service %s, PawnIOLib.dll %s, %d module(s) %s" % (
        p["service"] or "NOT INSTALLED", "present" if p["lib"] else "MISSING", len(p["modules"]), ", ".join(p["modules"])))
    buses = core.get("i2c_buses")
    if buses is not None:
        lines.append("SMBus buses: %d" % len(buses))
        lines += ["  %s (bus %s, PCI %s)%s" % (b["name"], b["bus_id"], b["pci"], "" if b["name"].startswith("PawnIO") else ", GPU: does not reach RAM")
                  for b in buses]
    if d["helper_service"]["name"]:
        lines.append("Helper service %s: %s" % (d["helper_service"]["name"], d["helper_service"]["state"]))
    lines.append("Other RGB programs running: %s" % (", ".join(d["conflicts"]) or "none"))
    lines.append("Config: %s" % (d["config"] or "defaults"))
    lines.append("")
    lines.append("Devices: %d" % len(d["devices"]))
    for i, x in enumerate(d["devices"]):
        lines.append("  %d. %s [%s, %s] %d LEDs at %s" % (i, x["name"], x["type"], x["group"], x["leds"], x["location"] or "?"))
        lines.append("     mode now: %s; modes: %s" % (x["active_mode"], ", ".join("%s (%s)" % (m["name"], m["colors"]) for m in x["modes"])))
        if x["zones"]:
            lines.append("     zones: %s" % "; ".join(x["zones"]))
    lines.append("")
    lines.append("Core log: %s" % (d["core_log"] or "none yet"))
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
