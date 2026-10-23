# RGB Control

RGB Control is a Windows app that sets the color, mode and brightness of your RGB lights from one window. It groups RAM, fans and motherboard, keyboard and mouse so you can color each group, and it has a tray icon, presets, a phone and Android remote, and optional control of a DeepCool cooler screen.

## It bundles OpenRGB

RGB Control talks to your hardware through [OpenRGB](https://openrgb.org) 1.0 ([commit 81bbe18a](https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/81bbe18a84c2e507006f19dd252e397e40a56bfe)). You do not need to install it. The official Windows build sits unmodified in the `openrgb` folder. An elevated background process (`rgbd.py`) starts it without a window and controls it over the OpenRGB protocol on 127.0.0.1:47826, because RAM and some boards are reached over SMBus and that needs admin rights. The app itself runs as a normal user and talks to `rgbd.py` on localhost. A copy of OpenRGB you run yourself on its default port is left alone.

## License

GPL-3.0-or-later (see `LICENSE`). OpenRGB is GPL-2.0-or-later and runs as a separate program, so it is not part of this work. Third-party components and their licenses are in `THIRD_PARTY_LICENSES.md`. `fetch-openrgb.ps1` downloads the official OpenRGB build and checks its SHA-256.

## Tested hardware

Tested on one PC only (Windows 11):

- 4x Corsair Vengeance RGB DDR5 (SMBus, needs the PawnIO driver)
- ASUS B650E MAX GAMING WIFI (Aura USB, three ARGB fan headers)
- Logitech G515 RAPID TKL keyboard and G502 HERO mouse (via Lightspeed receiver)
- DeepCool LT360 VISION cooler screen

OpenRGB 1.0 supports keyboards, mice, motherboards, GPUs, RAM, fans, coolers and strips from many brands. Only the hardware above has been tried here.

OpenRGB probes many buses, including SMBus on the motherboard, including SMBus on the motherboard. Close OpenRGB and iCUE before you start this app.

**Install on a second PC is untested.** The bundled-OpenRGB build has not been run against real hardware yet. Expect problems and read `logs\`.

## Download

Download `RGB-Anywhere-Setup.exe` from the [latest release](https://github.com/EQQX0/rgbanywhere/releases/latest) and run it. The setup window offers three choices, all on by default: a desktop shortcut, start with Windows, and open the app when setup finishes. Press Install. Setup downloads the app (about 75 MB), checks its SHA-256, unpacks it to `%LOCALAPPDATA%\RGB Anywhere` and runs the installer, which asks for admin once. A Start menu shortcut is always created. If something fails, the window offers an "Open log" button for `install.log` in the install folder.

To update, quit the app from the tray icon and run Setup again.

To remove it, open Settings > Apps > RGB Anywhere > Uninstall (or run `Uninstall.exe` in the install folder). This removes the scheduled tasks, startup entry, shortcuts and the install folder, including your settings. The PawnIO driver stays, because other programs may use it.

The app has its own Python, so nothing else needs installing. RAM and some boards need the [PawnIO](https://pawnio.eu) driver. The installer runs `winget install namazso.PawnIO` when the driver is missing, and prints a warning with the link if that fails. The release does not contain the driver.

Prefer to do it by hand? Download `RGBAnywhere-win64.zip` from the same page, extract it to a folder you will keep and run `Install.cmd`. The `.sha256` file next to it holds the checksum.

Nothing is signed, so Windows SmartScreen may warn you. The checksum is downloaded from the same release as the zip, so it catches a damaged download but not a tampered release. The zip is built on one PC by `package.ps1`; the Setup exe is built from `installer/setup.cpp` by `installer/build.ps1`.

## Build and install from source

You need Windows 10 or 11 and Python 3.10+ (tick "Add to PATH"). In PowerShell, in this folder:

```
powershell -ExecutionPolicy Bypass -File install.ps1    # asks for admin once
```

`install.ps1` installs the Python packages, runs `fetch-openrgb.ps1` if `openrgb\OpenRGB.exe` is missing, registers the scheduled tasks "RGB Anywhere Lighting" (the elevated daemon, at logon) and "RGB Anywhere Rescan", adds the startup entry, the Start menu shortcut and, if Uninstall.exe is present, the Settings > Apps entry. `-Desktop` also adds a desktop shortcut and `-NoStartup` skips the startup entry. `uninstall.ps1 -Dir <folder>` removes an install (it only accepts a folder named "RGB Anywhere" that holds the app). RAM and some boards also need the [PawnIO](https://pawnio.eu) driver, which the script installs with winget when it is missing. USB devices work without it.

To build the Android app, run `android/build.ps1`. It needs a JDK and the Android SDK, and it signs with a key you create (`android/rgb-control.keystore`, never committed).

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
| `argb_leds` | LEDs to give each empty addressable (ARGB) header or strip, kept within what the header allows (default 60) |
| `group_overrides` | `{"name fragment": "ram"}` forces matching devices into a group |
| `ram_expected` | Number of RAM sticks you have. The app rescans when fewer show up |
| `ram_same_model` | `true` flags RAM detected as different models, a known misread on matched kits |
| `helper_service` | A Windows service that disturbs RAM detection. It stops during rescans |
| `preferred_monitor` | Part of a monitor's hardware ID, such as `MSI`. The window opens on that screen |
| `idle_rescan` | `true` rescans when you return to an idle PC (for Logitech G HUB, which sleeps keyboard and mouse lights) |
| `icue_quit_on_close` | `true` makes iCUE's X button quit iCUE. Running iCUE together with this app can knock Corsair RAM into a state where it ignores colors |
| `deepcool_path` | Path to DeepCool.exe if it is not in `C:\DeepCool` |
| `core_log_level` | How much OpenRGB logs: `info` (default), `verbose` or `debug`. `debug` lists every detector it tries. Takes effect when the lighting daemon starts |

## Command line

```
python rgb.py blue
python rgb.py FF3C00 breathing
python rgb.py ram=A62700 fans=A62700
python rgb.py brightness=60
python rgb.py --restart          # re-detect devices
python rgb.py --diagnose         # what was found and why things may be missing; changes nothing
```

## Control from scripts and AI agents

`rgbctl` drives the running app. Every command prints one JSON object, and the exit code is 0 for done, 1 for refused or bad arguments, 2 when the app is not running.

```
rgbctl status                      # mode, color, brightness, presets, profiles, features
rgbctl color red                   # RRGGBB, #RRGGBB or a name
rgbctl color FF3C00 --target ram   # all (default), ram, fans, keyboard, mouse
rgbctl brightness 60
rgbctl off                         # lights off, monitors off
rgbctl on
rgbctl presets / rgbctl preset 2
rgbctl profiles / rgbctl profile <id>
rgbctl monitors-off
rgbctl rescan
rgbctl describe                    # the command list as JSON, for an agent to read
```

`rgbctl.cmd` sits in the install folder (`%LOCALAPPDATA%\RGB Anywhere`). Tell an agent to run `rgbctl describe` first. From a source checkout use `python rgbctl.py`.

The app serves this on 127.0.0.1:47824 and needs the token in `control.token` next to it. Any program running as your user can read that file, so any such program can control your lights.

## Troubleshooting

If devices are missing or colors don't change, run this in PowerShell from the install folder (`%LOCALAPPDATA%\RGB Anywhere` after Setup). It only reads, so no light changes:

```
.\python\python.exe rgb.py --diagnose
```

It lists what the lighting daemon found: whether it runs as admin, the PawnIO driver state, the SMBus buses it opened, every device with its modes and how each mode takes colors, other RGB programs that are running, and a numbered list of likely problems. It saves the same text to `logs\diagnose-<time>.txt`. The daemon also writes this diagnosis to `logs\rgb.log` each time it detects devices.

Logs live in `logs\`. Read `events.jsonl` and `errors.log` first. OpenRGB's own detection log is in `cache\core\logs\OpenRGB_<time>.log`; set `core_log_level` to `debug` in `config.json` for more detail. "Save debug report" in the app's Diagnostics dialog (the "Logs and diagnostics" button) includes both, plus the diagnosis. If the keyboard or mouse stop changing, press Rescan in the app or run `schtasks /run /tn "RGB Anywhere Rescan"`.

Close iCUE and OpenRGB before using this app. Two programs talking to the RAM at once can push Corsair sticks into bootloader mode, where they ignore colors until iCUE's service resets them.

The cooler screen feature needs a DeepCool LT-series cooler with the DeepCool app installed.

## Security notes

The phone remote is off by default. When you turn it on, it listens on your LAN with a random token in `state.json`. Anyone with the link controls your lights. The daemon listens on 127.0.0.1 only and checks a token stored in `rgbd.token`. The `rgbctl` API listens on 127.0.0.1:47824 and checks `control.token`.
