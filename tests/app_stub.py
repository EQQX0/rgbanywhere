# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Loads rgb_app.pyw on any OS with the Windows-only and GUI modules replaced by stand-ins, so App and Api logic
can be tested without a window. Nothing is started: main() is not run."""
import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_mod = None


def load():
    global _mod
    if _mod:
        return _mod
    os.environ.setdefault("RGB_LOG_DIR", tempfile.mkdtemp(prefix="rgbapp_logs_"))
    os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="rgbapp_appdata_"))
    if not hasattr(ctypes, "windll"):
        ctypes.windll = mock.MagicMock(**{"kernel32.GetConsoleWindow.return_value": 0})
    for name in ("imageio_ffmpeg", "pystray", "qrcode", "webview", "webview.dom", "winaccent", "icue", "winreg"):
        sys.modules.setdefault(name, mock.MagicMock())
    sys.path.insert(0, ROOT)
    loader = importlib.machinery.SourceFileLoader("rgb_app", os.path.join(ROOT, "rgb_app.pyw"))
    spec = importlib.util.spec_from_loader("rgb_app", loader)
    _mod = importlib.util.module_from_spec(spec)
    loader.exec_module(_mod)
    return _mod
