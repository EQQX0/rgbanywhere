# Third-party licenses

RGB Control as a whole is released under the GNU General Public License, version 3 or (at your option) any later version (see `LICENSE`). It ships and uses the components below. Their notices and licenses stay with them.

## OpenRGB (bundled, unmodified)

- Project: OpenRGB, https://openrgb.org, https://gitlab.com/CalcProgrammer1/OpenRGB
- Version: release 1.0, commit `81bbe18a84c2e507006f19dd252e397e40a56bfe`
  https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/81bbe18a84c2e507006f19dd252e397e40a56bfe
- Copyright: Adam Honse and the OpenRGB contributors.
- License: GNU GPL version 2 or later overall. Some folders of the upstream tree carry GPL-2.0-only headers.
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

## Python packages (installed with pip, not included)

| Package | License | Notes |
|---------|---------|-------|
| pywebview | BSD-3-Clause | window and WebView2 host |
| pystray | LGPL-3.0 | tray icon. LGPL-3.0 is compatible with GPL-3.0 |
| Pillow | MIT-CMU (HPND-style) | images |
| pillow-heif | BSD-3-Clause | HEIC/AVIF support. It bundles libheif and libde265 (LGPL-3.0) |
| imageio-ffmpeg | BSD-2-Clause | ships an FFmpeg binary (LGPL-2.1+ or GPL, depending on the build; see https://github.com/imageio/imageio-ffmpeg) |
| qrcode | BSD | QR code for pairing |
| pywin32 | PSF-2.0 | Windows APIs |
| openrgb-python | GPL-3.0 | client for the OpenRGB network protocol |
| proxy_tools, bottle, pythonnet, clr_loader, cffi, six, typing_extensions | MIT, MIT, MIT, MIT, MIT, MIT, PSF-2.0 | pulled in by pywebview |

## Android app

`android/` is original code. `android/build.ps1` downloads Google's R8 (BSD-3-Clause / Apache-2.0) and uses the Android SDK and a JDK at build time. None of these are committed. The signing key is yours and is never committed.

## Not redistributed

- DeepCool and Corsair software are separate products that the app can work alongside. Nothing from them is included.

## Open points

These need a person to confirm. See the compliance checklist in `README.md`.

- License wording of the PawnIO, hidapi and Qt files inside the OpenRGB zip should be checked against upstream before a public release.
- The FFmpeg build inside imageio-ffmpeg and libheif inside pillow-heif come from pip; this project does not redistribute them.

## Release zip

The release zip adds the unmodified OpenRGB build (`openrgb\`), the unmodified embeddable Python from python.org (PSF-2.0, `python\` folder) and the pip packages listed above installed under `python\Lib\site-packages`, each with its `*.dist-info` license files. `SOURCE.txt` in the zip points to this repository, tag `v<version>`, as the corresponding source.
