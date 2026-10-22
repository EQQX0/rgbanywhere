# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Make iCUE's X button really quit it.

iCUE has no such setting: X only hides the window to the tray, and iCUE then keeps talking to the RAM,
which clashes with OpenRGB (sticks drop into bootloader mode and stop taking colors). So once an iCUE
window has been shown and then none is visible any more, end iCUE and stop the services it started.
Minimizing is left alone (a minimized window still counts as visible).
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import time

import applog

_log = applog.get("icue")

EXE = "icue.exe"
# Started on demand by iCUE; signed-in users are allowed to stop them. The first one is what drives the RAM.
SERVICES = ("CorsairDeviceControlService", "CorsairCpuIdService", "iCUEDevicePluginHost",
            "CorsairDeviceListerService", "iCUEUpdateService")
POLL = 1.5
HIDDEN_POLLS = 3  # the window has to stay gone this long, so the gap between splash and main window doesn't count
NO_WINDOW = 0x08000000

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
_kernel32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
_kernel32.CloseHandle.argtypes = (wt.HANDLE,)
_ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_user32.GetWindowLongW.argtypes = (wt.HWND, ctypes.c_int)
_user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
_user32.IsWindowVisible.argtypes = (wt.HWND,)
_user32.GetWindowTextLengthW.argtypes = (wt.HWND,)
_names = {}  # pid -> exe name; a pid can be reused, so entries are dropped when the pid has no windows left


def _exe_of(pid):
    h = _kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wt.DWORD(520)
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return ""
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(h)


def windows():
    """(running, visible): whether iCUE owns any top-level window, and whether one of them is on screen."""
    found = {"running": False, "visible": False}
    seen = set()

    def each(hwnd, _):
        pid = wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        seen.add(pid.value)
        if pid.value not in _names:
            _names[pid.value] = _exe_of(pid.value)
        if _names[pid.value] == EXE:
            found["running"] = True
            tool = _user32.GetWindowLongW(hwnd, -20) & 0x80  # WS_EX_TOOLWINDOW: tooltips, tray helpers
            if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextLengthW(hwnd) and not tool:
                found["visible"] = True
        return True

    _user32.EnumWindows(_ENUM(each), 0)
    for pid in set(_names) - seen:
        del _names[pid]
    return found["running"], found["visible"]


def running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + EXE, "/NH"], capture_output=True, text=True,
                         errors="ignore", creationflags=NO_WINDOW).stdout
    return EXE in out.lower()


def quit_icue():
    r = subprocess.run(["taskkill", "/IM", "iCUE.exe", "/F"], capture_output=True, text=True, errors="ignore",
                       creationflags=NO_WINDOW)
    _log.info("taskkill iCUE.exe -> exit %s %s", r.returncode, (r.stdout + r.stderr).strip()[:200])
    left = []
    for s in SERVICES:
        r = subprocess.run(["sc", "stop", s], capture_output=True, text=True, errors="ignore", creationflags=NO_WINDOW)
        if r.returncode not in (0, 1062, 109, 1061):  # wasn't running / already on its way out after iCUE ended
            left.append("%s (%s)" % (s, r.returncode))
    if left:
        _log.warning("couldn't stop: %s", ", ".join(left))
    applog.event("icue_quit", ok=not left, error=", ".join(left) or None)


def watch(on_quit=None):
    """Loop forever. on_quit() runs after iCUE was ended, e.g. to take the lights back."""
    shown = False
    hidden = 0
    while True:
        running, visible = windows()
        if not running:
            shown, hidden = False, 0
        elif visible:
            shown, hidden = True, 0
        elif shown:
            hidden += 1
            if hidden >= HIDDEN_POLLS:
                _log.info("iCUE's window was closed; ending iCUE and its services")
                quit_icue()
                shown, hidden = False, 0
                if on_quit:
                    # Let the Corsair service let go of the RAM first: a rescan while it was still
                    # shutting down found only 3 of the 4 sticks.
                    for _ in range(15):
                        r = subprocess.run(["sc", "query", SERVICES[0]], capture_output=True, text=True,
                                           errors="ignore", creationflags=NO_WINDOW)
                        if "STOPPED" in r.stdout:
                            break
                        time.sleep(1)
                    time.sleep(5)
                    on_quit()
        time.sleep(POLL)
