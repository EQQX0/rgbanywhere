# Third-party licenses

RGB Control as a whole is released under the GNU General Public License, version 3 or (at your option) any later version (see `LICENSE`). It contains and links the components below. Their notices and licenses stay with them.

## OpenRGB (embedded, source included)

- Project: OpenRGB, https://openrgb.org, https://gitlab.com/CalcProgrammer1/OpenRGB
- Version: release_1.0, commit `81bbe18a84c2e507006f19dd252e397e40a56bfe`
  https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/81bbe18a84c2e507006f19dd252e397e40a56bfe
- Copyright: Adam Honse and the OpenRGB contributors. The author of each file is named in that file's header.
- License: GNU GPL version 2 or later (`SPDX-License-Identifier: GPL-2.0-or-later` in the headers). The upstream `LICENSE` file is kept unchanged at `third_party/openrgb/LICENSE`.
- Where: the complete upstream tree is in `third_party/openrgb/`. `build.ps1` compiles the device core, 95 of its source files, plus controllers for the hardware named in `core/CMakeLists.txt`, into `rgbcore.dll`. The list of compiled files is the `CONTROLLER_DIRS` and source list in that file.
- Changes made to OpenRGB files (each one carries a "Modified by EQQX2" comment in the file; headers and copyright lines are untouched):
  - `third_party/openrgb/i2c_smbus/Windows/i2c_smbus_pawnio.cpp`: finds the PawnIO `.bin` modules next to `rgbcore.dll` instead of next to the host executable.
  - `third_party/openrgb/wmi/wmi.cpp`: no COM initialisation in a static initialiser (it deadlocks inside a DLL); COM is initialised as multithreaded and started from `rgb_init()`.
- Combining GPL-2.0-or-later code with GPL-3.0-or-later code is allowed; the combined work is distributed under GPL-3.0-or-later.

## Binaries that ship inside the OpenRGB tree

`build.ps1` copies these prebuilt files from `third_party/openrgb/dependencies/` next to the app. They were not built from source here.

| Component | Used for | License | Source |
|-----------|----------|---------|--------|
| PawnIOLib.dll (Copyright (C) 2023 namazso) | talking to the PawnIO driver | LGPL-2.1-or-later | https://github.com/namazso/PawnIOLib |
| PawnIO modules `*.bin` | SMBus / LPC access modules | LGPL-2.1-or-later (see the PawnIO.Modules repository) | https://github.com/namazso/PawnIO.Modules |
| hidapi-hotplug.dll | USB HID access | hidapi licenses (BSD-3-Clause / GPL-3.0 / original hidapi license) | https://gitlab.com/CalcProgrammer1/hidapi (OpenRGB's hotplug fork) |
| libusb-1.0.dll | USB access | LGPL-2.1-or-later | https://libusb.info |
| libwinpthread-1.dll | MinGW-w64 threading runtime, copied from your toolchain | MIT / ZPL-2.1 | https://www.mingw-w64.org |

The PawnIO **driver** is not distributed here. Install it from https://pawnio.eu and read its license there.

Header-only code compiled into `rgbcore.dll` from `third_party/openrgb/dependencies/`: nlohmann/json (MIT) and CRCpp (`CRC.h`, BSD-3-Clause, Copyright Daniel Bahr; license text in the header). The hidapi and libusb headers describe the DLLs above. Other folders in that directory (hueplusplus, libe131, mdns, stb, mbedtls, and so on) are part of the upstream tree but are not compiled into `rgbcore.dll`.

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
