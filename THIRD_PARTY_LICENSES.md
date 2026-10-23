# Third-party licenses

RGB Control as a whole is released under the GNU General Public License, version 3 or (at your option) any later version (see `LICENSE`). It ships and uses the components below. Their notices and licenses stay with them.

## OpenRGB (bundled, unmodified)

- Project: OpenRGB, https://openrgb.org, https://gitlab.com/CalcProgrammer1/OpenRGB
- Version: release 1.0, commit `81bbe18a84c2e507006f19dd252e397e40a56bfe`
  https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/81bbe18a84c2e507006f19dd252e397e40a56bfe
- Copyright: Adam Honse and the OpenRGB contributors.
- License: GNU GPL version 2 or later. Checked on 2026-10-23 at the commit above: `LICENSE` is the GPL version 2 text and the source files carry `SPDX-License-Identifier: GPL-2.0-or-later` (for example `NetworkServer.cpp`). Some folders of the upstream tree may carry GPL-2.0-only headers; that was not re-checked.
- How it ships: the official Windows build (`OpenRGB_1.0_Windows_64_81bbe18.zip` from https://codeberg.org/OpenRGB/OpenRGB/releases, SHA-256 `182A52A3C97C4C4AE52C80286B4260C9666C51C3447DBC416EE8945F66192E90`) is unpacked unmodified into `openrgb\`. `fetch-openrgb.ps1` downloads it, checks the hash and unpacks it. Its source is at the commit above.
- How it is used: `rgbd.py` starts `openrgb\OpenRGB.exe` as a separate process with `--server` on 127.0.0.1:47826 and talks to it over the OpenRGB network protocol. Nothing from OpenRGB is compiled or linked into RGB Control, so the GPL-2.0-or-later program and the GPL-3.0-or-later app are separate works.

## Files inside the OpenRGB zip

These come from the official OpenRGB build and are not modified here. Licenses are as stated upstream; I have not checked each one against its source.

| Component | Used for | License | Source |
|-----------|----------|---------|--------|
| PawnIOLib.dll and `*.bin` modules | talking to the PawnIO driver (SMBus, LPC) | PawnIOLib header: LGPL-2.1-or-later. Modules: LGPL-2.1 | https://github.com/namazso/PawnIO, https://github.com/namazso/PawnIO.Modules |
| hidapi-hotplug.dll | USB HID access | hidapi licenses | https://gitlab.com/CalcProgrammer1/hidapi |
| libusb-1.0.dll | USB access | LGPL-2.1-or-later | https://libusb.info |
| Qt 6 DLLs and plugins | OpenRGB's own GUI code, loaded even when headless | LGPL-3.0 and others | https://www.qt.io |

The PawnIO **driver** is not distributed here. Setup installs it with `winget install namazso.PawnIO`. Its repository (https://github.com/namazso/PawnIO) lists GPL-2.0 (version 2 or later) with an exception for combining it with LGPL programs and with modules that talk to it only through its device IO-control interface. pawnio.eu states no redistribution terms, so this project does not bundle the driver or its installer.

## Python packages

A source install gets these from pip. The release zip includes them, installed under `python\Lib\site-packages`, each with its `*.dist-info` license files. Licenses below were checked against PyPI metadata (and, where noted, the files inside the Windows wheel) on 2026-10-23, for the version named. `requirements.txt` allows newer versions of most of them, so a later build can pick up different terms.

| Package | Version checked | License | Notes |
|---------|-----------------|---------|-------|
| pywebview | 6.2.1 | BSD-3-Clause | window and WebView2 host |
| pystray | 0.19.5 | LGPL-3.0 | tray icon. LGPL-3.0 is compatible with GPL-3.0 |
| Pillow | 13.0.0 | MIT-CMU | images |
| pillow-heif | 1.8.1 | source BSD-3-Clause; Windows wheel GPLv2 | HEIC/AVIF support. Its `LICENSES_bundled.txt` says the binary wheels are GPLv2 "due to base library licenses" and bundle libheif (LGPLv3), libde265 (LGPLv3), x265 (GPLv2) and the MinGW-w64 runtime (GPL-3.0 with GCC exception, MIT, BSD). See Open points |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause | the Windows wheel also ships `ffmpeg-win-x86_64-v7.1.exe` with no license file for it. See Open points |
| qrcode | 8.2 | BSD | QR code for pairing. PyPI also lists an "Other/Proprietary License" classifier. See Open points |
| colorama | 0.4.6 | BSD | pulled in by qrcode on Windows |
| pywin32 | 312 | PSF | Windows APIs |
| openrgb-python | 0.3.8 | GPL-3.0 | client for the OpenRGB network protocol |
| proxy_tools, bottle, typing_extensions | 0.1.0, 0.13.4, 4.16.0 | MIT, MIT, PSF-2.0 | pulled in by pywebview |
| pythonnet, clr_loader, cffi | 3.2.1, 0.3.1, 2.1.1 | MIT, MIT (wheel `LICENSE`), MIT-0 | pulled in by pywebview on Windows |
| six | 1.17.0 | MIT | pulled in by pystray |

## Android app

`android/` is original code. `android/build.ps1` downloads Google's R8 (BSD-3-Clause / Apache-2.0) and uses the Android SDK and a JDK at build time. None of these are committed. The signing key is yours and is never committed.

## Not redistributed

- DeepCool and Corsair software are separate products that the app can work alongside. Nothing from them is included.

## Open points

These need a person to confirm before a public release.

- Files inside the OpenRGB zip: PawnIOLib.dll and the `*.bin` modules, hidapi-hotplug.dll, libusb-1.0.dll and the Qt 6 DLLs. Their licenses in the table above are as stated upstream and were not checked against the files actually in the zip (codeberg.org, which hosts the zip, could not be reached from the session that wrote this).
- pillow-heif's Windows wheels are GPLv2 by their own notice (x265 is GPLv2). Whether shipping them in the release zip next to this GPL-3.0-or-later app is fine is a licensing decision for the owner.
- The FFmpeg binary inside imageio-ffmpeg comes without a license file in the wheel; its build (LGPL or GPL) is not stated there.
- qrcode's PyPI metadata lists both BSD and "Other/Proprietary License".

## Release zip

The release zip adds the unmodified OpenRGB build (`openrgb\`), the unmodified embeddable Python from python.org (PSF-2.0, `python\` folder) and the pip packages listed above installed under `python\Lib\site-packages`, each with its `*.dist-info` license files. `SOURCE.txt` in the zip points to this repository, tag `v<version>`, as the corresponding source.
