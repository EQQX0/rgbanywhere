# Third-party licenses

RGB Control as a whole is released under the GNU General Public License, version 3 or (at your option) any later version (see `LICENSE`). It contains and links the components below. Their notices and licenses stay with them.

## OpenRGB (embedded, source included)

- Project: OpenRGB, https://openrgb.org, https://gitlab.com/CalcProgrammer1/OpenRGB
- Version: release_1.0, commit `81bbe18a84c2e507006f19dd252e397e40a56bfe`
  https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/81bbe18a84c2e507006f19dd252e397e40a56bfe
- Copyright: Adam Honse and the OpenRGB contributors. The author of each file is named in that file's header.
- License: GNU GPL version 2 or later (`SPDX-License-Identifier: GPL-2.0-or-later` in the headers). The upstream `LICENSE` file is kept unchanged at `third_party/openrgb/LICENSE`.
- Where: the complete upstream tree is in `third_party/openrgb/`. `build.ps1` compiles the device core plus 187 of the 196 controller folders into `rgbcore.dll`. The compiled files are the source list in `core/CMakeLists.txt` and the folders not listed in `core/exclude.cmake`. A scripted check of the header in all 894 compiled files (2026-10-22) found 890 marked GPL-2.0-or-later, `libe131` (Apache-2.0), `qt/hsv.cpp` (MIT) and one file with no header (`dependencies/NVFC/nvapi.cpp`, see open points). Folders whose files are marked GPL-2.0-only (HIDLampArray, Mountain, GigabyteRGBFusion2USB, LianLi) are left out because GPL-2.0-only code cannot be combined into a GPL-3.0-or-later work. Folders with files that have no license header (Sinowealth, AsusAuraCore) are left out too. The list is in `core/exclude.cmake`. This check reads headers only; it is not a legal review.
- Changes made to OpenRGB files (each one carries a "Modified by EQQX2" comment in the file; headers and copyright lines are untouched):
  - `third_party/openrgb/i2c_smbus/Windows/i2c_smbus_pawnio.cpp`: finds the PawnIO `.bin` modules next to `rgbcore.dll` instead of next to the host executable.
  - `third_party/openrgb/wmi/wmi.cpp`: no COM initialisation in a static initialiser (it deadlocks inside a DLL); COM is initialised as multithreaded and started from `rgb_init()`.
- Combining GPL-2.0-or-later code with GPL-3.0-or-later code is allowed; the combined work is distributed under GPL-3.0-or-later.

## Binaries that ship inside the OpenRGB tree

`build.ps1` copies these prebuilt files from `third_party/openrgb/dependencies/` next to the app. They were not built from source here.

| Component | Used for | License | Source |
|-----------|----------|---------|--------|
| PawnIOLib.dll (Copyright (C) 2023 namazso) | talking to the PawnIO driver | LGPL-2.1-or-later (per the header in PawnIOLib.h) | https://github.com/namazso/PawnIOLib (archived, moved to https://github.com/namazso/PawnIO) |
| PawnIO modules `*.bin` | SMBus / LPC access modules | LGPL-2.1 (its COPYING file; "or later" not checked) | https://github.com/namazso/PawnIO.Modules |
| hidapi-hotplug.dll | USB HID access | hidapi licenses (BSD-3-Clause / GPL-3.0 / original hidapi license) | https://gitlab.com/CalcProgrammer1/hidapi (OpenRGB's hotplug fork) |
| libusb-1.0.dll | USB access | LGPL-2.1-or-later | https://libusb.info |
| libwinpthread-1.dll | MinGW-w64 threading runtime, copied from your toolchain | MIT / ZPL-2.1 | https://www.mingw-w64.org |

The PawnIO **driver** is not distributed here. Install it from https://pawnio.eu and read its license there. Upstream describes the driver as GPL-2.0 or later with a linking exception. The LGPL-2.1 text and notes for the PawnIO files are in `third_party/openrgb/dependencies/PawnIO/`.

Code compiled into `rgbcore.dll` from `third_party/openrgb/`: nlohmann/json (MIT), CRCpp (`CRC.h`, BSD-3-Clause, Copyright Daniel Bahr), libe131 (Apache-2.0, Copyright 2016 Hugo Hromic; Apache-2.0 is compatible with GPL-3.0), `qt/hsv.cpp` (MIT, Copyright 2013 Martin Mitas), `dependencies/NVFC` (NVIDIA API wrapper, license as in OpenRGB) and the AMD Display Library headers (MIT, Copyright AMD). License text is in each file's header. The hidapi and libusb headers describe the DLLs above. Other folders in that directory (hueplusplus, libe131, mdns, stb, mbedtls, and so on) are part of the upstream tree but are not compiled into `rgbcore.dll`.

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
| proxy_tools, bottle, pythonnet, clr_loader, cffi, six, typing_extensions | MIT, MIT, MIT, MIT, MIT, MIT, PSF-2.0 | pulled in by pywebview |

## Android app

`android/` is original code. `android/build.ps1` downloads Google's R8 (BSD-3-Clause / Apache-2.0) and uses the Android SDK and a JDK at build time. None of these are committed. The signing key is yours and is never committed.

## Not redistributed

- `nircmd.exe` (freeware, NirSoft) is optional and is not in this repository.
- DeepCool and Corsair software are separate products that the app can work alongside. Nothing from them is included.

## Open points

These need a person to confirm. See the compliance checklist in `README.md`.

- Exact license wording of the prebuilt PawnIO and hidapi binaries should be checked against their upstream repositories before a public release.
- The FFmpeg build inside imageio-ffmpeg and libheif inside pillow-heif come from pip; this project does not redistribute them.

## Release zip

The release zip adds the unmodified embeddable Python from python.org (PSF-2.0, `python\` folder) and the pip packages listed above installed under `python\Lib\site-packages`, each with its `*.dist-info` license files. `SOURCE.txt` in the zip points to this repository, tag `v<version>`, as the corresponding source.
