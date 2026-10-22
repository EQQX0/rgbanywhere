# RGB Control

RGB Control is a Windows app that sets the color, mode and brightness of your RGB lights from one window. It groups RAM, fans and motherboard, keyboard and mouse so you can color each group, and it has a tray icon, presets, a phone and Android remote, and optional control of a DeepCool cooler screen.

## It embeds OpenRGB

RGB Control talks to your hardware through code from [OpenRGB](https://openrgb.org) (release 1.0, [commit 81bbe18a](https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/81bbe18a84c2e507006f19dd252e397e40a56bfe)). You do not need to install OpenRGB. The OpenRGB device core is in `third_party/openrgb/` with its license and copyright notices unchanged, and `build.ps1` compiles it into `rgbcore.dll`. An elevated background process (`rgbd.py`) loads that DLL, because RAM and some boards are reached over SMBus and that needs admin rights. The app itself runs as a normal user and talks to the process on localhost.

## License

GPL-3.0-or-later (see `LICENSE`). OpenRGB is GPL-2.0-or-later, so the combined work is GPL-3.0-or-later. Third-party components and their licenses are in `THIRD_PARTY_LICENSES.md`. The source of everything you run, including the DLL, is in this repository. Files taken from OpenRGB that I changed say "Modified by EQQX2" in a comment.

## Tested hardware

Tested on one PC only (Windows 11):

- 4x Corsair Vengeance RGB DDR5 (SMBus, needs the PawnIO driver)
- ASUS B650E MAX GAMING WIFI (Aura USB, three ARGB fan headers)
- Logitech G515 RAPID TKL keyboard and G502 HERO mouse (via Lightspeed receiver)
- DeepCool LT360 VISION cooler screen

The core compiles in 187 of OpenRGB's 196 controller folders: keyboards, mice, motherboards, GPUs, RAM, fans, coolers and strips from many brands. Only the hardware above has been tried here. The nine folders left out are in `core/exclude.cmake`. Philips Hue needs extra libraries and two are Linux-only. Four contain GPL-2.0-only code (HID lamp arrays, Mountain keyboards, Gigabyte RGB Fusion2 USB, Lian Li hubs) and two contain files with no license header (Sinowealth keyboards, ASUS Aura Core laptops), so they cannot go into a GPL-3.0 build. Pass `-Controllers "CorsairDRAMController;AsusAuraUSBController"` to `build.ps1` for a smaller build.

The detectors probe the same buses OpenRGB does, including SMBus on the motherboard. Close OpenRGB and iCUE before you start this app.

**Install on a second PC is untested.** The embedded core has also not been run against real hardware from this repository's final tree. Expect problems and read `logs\`.

## Download

Download `RGB-Anywhere-Setup.exe` from the [latest release](https://github.com/EQQX0/rgbanywhere/releases/latest) and run it. It downloads the app (about 75 MB), checks the SHA-256, unpacks it to `%LOCALAPPDATA%\RGB Anywhere` and starts the installer, which asks for admin once. Afterwards, open "RGB Anywhere" from the Start menu. Run the Setup exe again to update; quit the app from the tray icon first.

The app has its own Python, so nothing else needs installing. RAM and some boards need the [PawnIO](https://pawnio.eu) driver. The installer runs `winget install namazso.PawnIO` when the driver is missing, and prints a warning with the link if that fails. The release does not contain the driver.

Prefer to do it by hand? Download `RGBAnywhere-win64.zip` from the same page, extract it to a folder you will keep and run `Install.cmd`. The `.sha256` file next to it holds the checksum.

Nothing is signed, so Windows SmartScreen may warn you. The zip is built on one PC by `package.ps1`; the Setup exe is built from `installer/setup.cpp` by `installer/build.ps1`.

## Build and install from source

You need Windows 10 or 11, Python 3.10+ (tick "Add to PATH") and a C++ toolchain:

```
winget install BrechtSanders.WinLibs.POSIX.UCRT
python -m pip install cmake ninja
```

Then in PowerShell, in this folder:

```
powershell -ExecutionPolicy Bypass -File build.ps1      # builds rgbcore.dll and copies its runtime files here
powershell -ExecutionPolicy Bypass -File install.ps1    # asks for admin once
```

`install.ps1` installs the Python packages, runs `build.ps1` if the DLL is missing, registers the scheduled tasks "RGB Anywhere Lighting" (the elevated daemon, at logon) and "RGB Anywhere Rescan", adds the startup entry and the Start menu shortcut. RAM and some boards also need the [PawnIO](https://pawnio.eu) driver, which the script installs with winget when it is missing. USB devices work without it.

Add `-Android` to `build.ps1` to build the Android app as well. It needs a JDK and the Android SDK, and it signs with a key you create (`android/rgb-control.keystore`, never committed).

## How devices are grouped

| Group | Devices |
|-------|---------|
| ram | memory sticks |
| fans | motherboard, LED strips, coolers, case lighting |
| keyboard | keyboards, keypads |
| mouse | mice, mouse mats |

Anything else follows the overall color. Add a device name to `group_overrides` in `config.json` to move it.

## config.json

Copy `config.example.json` to `config.json`. The file and every key are optional.

| Key | Meaning |
|-----|---------|
| `argb_leds` | LEDs to give each empty motherboard ARGB header (default 60) |
| `group_overrides` | `{"name fragment": "ram"}` forces matching devices into a group |
| `ram_expected` | Number of RAM sticks you have. The app rescans when fewer show up |
| `ram_same_model` | `true` flags RAM detected as different models, a known misread on matched kits |
| `helper_service` | A Windows service that disturbs RAM detection. It stops during rescans |
| `preferred_monitor` | Part of a monitor's hardware ID, such as `MSI`. The window opens on that screen |
| `idle_rescan` | `true` rescans when you return to an idle PC (for Logitech G HUB, which sleeps keyboard and mouse lights) |
| `icue_quit_on_close` | `true` makes iCUE's X button quit iCUE. Running iCUE together with this app can knock Corsair RAM into a state where it ignores colors |
| `deepcool_path` | Path to DeepCool.exe if it is not in `C:\DeepCool` |

## Command line

```
python rgb.py blue
python rgb.py FF3C00 breathing
python rgb.py ram=A62700 fans=A62700
python rgb.py brightness=60
python rgb.py --restart          # re-detect devices
```

## Troubleshooting

Logs live in `logs\`. Read `events.jsonl` and `errors.log` first. If the keyboard or mouse stop changing, press Rescan in the app or run `schtasks /run /tn "RGB Anywhere Rescan"`.

Close iCUE and OpenRGB before using this app. Two programs talking to the RAM at once can push Corsair sticks into bootloader mode, where they ignore colors until iCUE's service resets them.

The cooler screen feature needs a DeepCool LT-series cooler with the DeepCool app installed. `nircmd.exe` is optional and not included.

## Security notes

The phone remote is off by default. When you turn it on, it listens on your LAN with a random token in `state.json`. Anyone with the link controls your lights. The daemon listens on 127.0.0.1 only and checks a token stored in `rgbd.token`.
