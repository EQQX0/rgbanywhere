# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Set the Windows accent color (Start, taskbar, title bars, buttons) and broadcast the change."""
import ctypes
import winreg

import applog

log = applog.get("accent")

ACCENT_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"
DWM_KEY = r"Software\Microsoft\Windows\DWM"


def _abgr(rgb):
    r, g, b = rgb
    return 0xFF000000 | (b << 16) | (g << 8) | r


def _mix(rgb, other, t):
    return tuple(round(c + (o - c) * t) for c, o in zip(rgb, other))


def _get(key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, name)
    except OSError:
        return None


def _set(key, name, value, kind):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, name, 0, kind, value)


def snapshot():
    """Current accent settings, so they can be restored later."""
    snap = {}
    for key, name in ((ACCENT_KEY, "AccentPalette"), (ACCENT_KEY, "AccentColorMenu"), (ACCENT_KEY, "StartColorMenu"),
                      (DWM_KEY, "AccentColor"), (DWM_KEY, "ColorizationColor"), (DWM_KEY, "ColorizationAfterglow")):
        v = _get(key, name)
        if v is not None:
            val, kind = v
            snap["%s|%s" % (key, name)] = [val.hex() if isinstance(val, bytes) else val, kind]
    return snap


def restore(snap):
    for full, (val, kind) in snap.items():
        key, name = full.split("|", 1)
        _set(key, name, bytes.fromhex(val) if kind == winreg.REG_BINARY else val, kind)
    _broadcast()
    log.info("restored the original Windows accent (%d values)", len(snap))


def set_accent(hexc):
    rgb = tuple(int(hexc[i:i + 2], 16) for i in (0, 2, 4))
    if max(rgb) < 40:  # pure black/near-black makes Windows unreadable; keep a dim version instead
        rgb = _mix(rgb, (64, 64, 72), 0.6)
    white, black = (255, 255, 255), (0, 0, 0)
    # Windows palette order: 3 lighter shades, the accent, 3 darker shades, then one spare
    shades = [_mix(rgb, white, .6), _mix(rgb, white, .4), _mix(rgb, white, .2), rgb,
              _mix(rgb, black, .2), _mix(rgb, black, .4), _mix(rgb, black, .6), _mix(rgb, black, .75)]
    palette = b"".join(bytes([r, g, b, 0]) for r, g, b in shades)
    _set(ACCENT_KEY, "AccentPalette", palette, winreg.REG_BINARY)
    _set(ACCENT_KEY, "AccentColorMenu", _abgr(rgb), winreg.REG_DWORD)
    _set(ACCENT_KEY, "StartColorMenu", _abgr(shades[4]), winreg.REG_DWORD)
    _set(DWM_KEY, "AccentColor", _abgr(rgb), winreg.REG_DWORD)
    argb = 0xC4000000 | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    _set(DWM_KEY, "ColorizationColor", argb, winreg.REG_DWORD)
    _set(DWM_KEY, "ColorizationAfterglow", argb, winreg.REG_DWORD)
    _broadcast()
    log.info("Windows accent set to %s", hexc)


def _broadcast():
    res = ctypes.c_ulong()
    send = ctypes.windll.user32.SendMessageTimeoutW
    for msg in ("ImmersiveColorSet", "WindowsThemeElement"):
        send(0xFFFF, 0x001A, 0, ctypes.c_wchar_p(msg), 0x0002, 1000, ctypes.byref(res))  # WM_SETTINGCHANGE
    send(0xFFFF, 0x0320, 0, 0, 0x0002, 1000, ctypes.byref(res))  # WM_DWMCOLORIZATIONCOLORCHANGED
