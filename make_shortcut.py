# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Create the app shortcut with the app's taskbar ID so pinning opens RGB Anywhere, not Python.

  python make_shortcut.py             Start menu shortcut
  python make_shortcut.py --desktop   Start menu and desktop shortcuts
"""
import os
import sys

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell, shellcon

HERE = os.path.dirname(os.path.abspath(__file__))
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
NAME = "RGB Anywhere.lnk"
START = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs", NAME)
DESKTOP = os.path.join(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, 0, 0), NAME)

targets = [START] + ([DESKTOP] if "--desktop" in sys.argv else [])

for lnk in targets:
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
    link.SetPath(PYW)
    link.SetArguments('"%s"' % os.path.join(HERE, "rgb_app.pyw"))
    link.SetWorkingDirectory(HERE)
    link.SetIconLocation(os.path.join(HERE, "rgb.ico"), 0)
    link.SetDescription("Control RGB lighting and the cooler screen")
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType("Admin.RGBControl"))
    store.Commit()
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(lnk, 0)
    print(lnk)
if "--desktop" not in sys.argv and os.path.exists(DESKTOP):
    os.remove(DESKTOP)
