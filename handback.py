# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Hand devices back to their vendor app: restart G HUB, iCUE, Synapse... so it sends its own lighting profile again.

Used when RGB Anywhere lets go of devices whose original lighting it could not read (Original, and on uninstall).
The vendor app keeps the user's real profile, effects included, which RGB Anywhere cannot copy. Apps it cannot restart
from here (Armoury Crate and MSI Center are Store apps with services) are only named, for the user to open.
"""
import ntpath  # Windows paths, also when the tests run elsewhere
import os
import re
import subprocess
import time

import applog

_log = applog.get("handback")
NO_WINDOW = 0x08000000

# Exe names and install folders come from vendor support pages, forums and process lists, not from a test PC with each
# app; an exe that isn't there is skipped, and the registry's uninstall entries are checked first.
APPS = {
    "Logitech G HUB": {"procs": ("lghub.exe", "lghub_agent.exe", "lghub_system_tray.exe"), "name": r"\blogitech g hub\b",
                       "paths": (r"%ProgramFiles%\LGHUB\lghub.exe",)},
    "Corsair iCUE": {"procs": ("icue.exe",), "name": r"\bicue\b",
                     "paths": (r"%ProgramFiles%\Corsair\Corsair iCUE5 Software\iCUE.exe",
                               r"%ProgramFiles%\Corsair\CORSAIR iCUE 5 Software\iCUE.exe",
                               r"%ProgramFiles%\Corsair\CORSAIR iCUE 4 Software\iCUE.exe")},
    "Razer Synapse": {"procs": ("razerappengine.exe", "razer synapse 3.exe"), "name": r"\brazer (synapse|app ?engine)\b",
                      "paths": (r"%ProgramFiles%\Razer\RazerAppEngine\RazerAppEngine.exe",
                                r"%ProgramFiles(x86)%\Razer\Synapse3\WPFUI\Framework\Razer Synapse 3 Host\Razer Synapse 3.exe")},
    "SteelSeries GG": {"procs": ("steelseriesgg.exe", "steelseriesengine.exe"), "name": r"\bsteelseries gg\b",
                       "paths": (r"%ProgramFiles%\SteelSeries\GG\SteelSeriesGG.exe",)},
    "NZXT CAM": {"procs": ("nzxt cam.exe",), "name": r"\bnzxt cam\b", "paths": (r"%ProgramFiles%\NZXT CAM\NZXT CAM.exe",)},
}
UNINSTALL_KEYS = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                  r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")


def _uninstall_entries():
    """(DisplayName, DisplayIcon, InstallLocation) of every installed program, from the registry."""
    import winreg
    out = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in UNINSTALL_KEYS:
            try:
                key = winreg.OpenKey(root, path)
            except OSError:
                continue
            with key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            vals = []
                            for v in ("DisplayName", "DisplayIcon", "InstallLocation"):
                                try:
                                    vals.append(str(winreg.QueryValueEx(sub, v)[0]))
                                except OSError:
                                    vals.append("")
                            out.append(tuple(vals))
                    except OSError:
                        continue
    return out


def find_exe(app, entries=None, exists=os.path.isfile):
    """The program to start for `app` (a key of APPS), or None if it isn't installed."""
    spec = APPS[app]
    try:
        entries = _uninstall_entries() if entries is None else entries
    except Exception:  # not Windows, or the registry can't be read: fall back to the usual folders
        entries = []
    for name, icon, folder in entries:
        if not re.search(spec["name"], name, re.I):
            continue
        icon = icon.split(",")[0].strip().strip('"')
        if icon.lower().endswith(".exe") and ntpath.basename(icon).lower() in spec["procs"] and exists(icon):
            return icon
        for proc in spec["procs"]:
            if folder and exists(ntpath.join(folder, proc)):
                return ntpath.join(folder, proc)
    return next((p for p in (os.path.expandvars(p) for p in spec["paths"]) if exists(p)), None)


def running_names():
    """Lower-case exe names of the running processes."""
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="ignore",
                       timeout=20, creationflags=NO_WINDOW)
    return {line.split('","')[0].strip('"').lower() for line in r.stdout.splitlines() if line.startswith('"')}


def _kill(proc):
    r = subprocess.run(["taskkill", "/F", "/IM", proc], capture_output=True, text=True, errors="ignore",
                       creationflags=NO_WINDOW)
    _log.info("taskkill %s -> exit %s", proc, r.returncode)


def _launch(exe):
    """Start `exe` through Explorer, so it runs as the signed-in user even when this process is elevated (Uninstall)."""
    subprocess.Popen(["explorer.exe", exe], creationflags=NO_WINDOW)
    _log.info("started %s", exe)


def restart(apps, running=running_names, kill=_kill, launch=_launch, find=find_exe, sleep=time.sleep):
    """Restart (or start) each vendor app in `apps` so it sends its lighting profile again. Returns what happened:
    {"restarted": [...], "started": [...], "not_installed": [...], "manual": [...], "failed": [...]}."""
    done = {"restarted": [], "started": [], "not_installed": [], "manual": [], "failed": []}
    todo = sorted(set(a for a in apps if a))
    if not todo:
        return done
    try:
        live = running()
    except Exception:
        live = set()
    for app in todo:
        if app not in APPS:
            done["manual"].append(app)
            continue
        try:
            exe = find(app)
            if not exe:
                done["not_installed"].append(app)
                continue
            was = [p for p in APPS[app]["procs"] if p in live]
            for p in was:
                kill(p)
            if was:
                sleep(2)  # let it let go of the devices before it starts again
            launch(exe)
            done["restarted" if was else "started"].append(app)
        except Exception as e:
            done["failed"].append(app)
            _log.warning("could not restart %s: %s: %s", app, type(e).__name__, e)
    _log.info("hand back: %s", done)
    applog.event("handback", ok=not done["failed"], **done)
    return done


def summary(done):
    """One line for the user, or "" if there was nothing to hand back."""
    parts = []
    back = done.get("restarted", []) + done.get("started", [])
    if back:
        parts.append("Restarted %s so it puts its own lighting back." % " and ".join(back))
    if done.get("manual"):
        parts.append("Open %s to put its lighting back." % ", ".join(done["manual"]))
    if done.get("not_installed"):
        parts.append("%s isn't installed; pick an exit color in Original for those devices." % ", ".join(done["not_installed"]))
    if done.get("failed"):
        parts.append("Couldn't restart %s; open it yourself." % ", ".join(done["failed"]))
    return " ".join(parts)
