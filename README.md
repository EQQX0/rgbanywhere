# RGB Anywhere

RGB Anywhere is a Windows app that sets the color, mode and brightness of your RGB lights from one window. It groups RAM, fans and case, keyboard, mouse, graphics card, audio and other devices so you can color each group, it can put your lighting back the way it was before you installed it, and it has a tray icon, presets, a phone and Android remote, and optional control of a DeepCool cooler screen.

## It bundles OpenRGB

RGB Anywhere talks to your hardware through [OpenRGB](https://openrgb.org) 1.0 ([commit 81bbe18a](https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/81bbe18a84c2e507006f19dd252e397e40a56bfe)). You do not need to install it. The official Windows build sits unmodified in the `openrgb` folder. An elevated background process (`rgbd.py`) starts it without a window and controls it over the OpenRGB protocol on 127.0.0.1:47826, because RAM and some boards are reached over SMBus and that needs admin rights. The app itself runs as a normal user and talks to `rgbd.py` on localhost. A copy of OpenRGB you run yourself on its default port is left alone.

## License

GPL-3.0-or-later (see `LICENSE`). OpenRGB is GPL-2.0-or-later and runs as a separate program, so it is not part of this work. Third-party components and their licenses are in `THIRD_PARTY_LICENSES.md`. `fetch-openrgb.ps1` downloads the official OpenRGB build and checks its SHA-256.

## Tested hardware

Tested on one PC only (Windows 11):

- 4x Corsair Vengeance RGB DDR5 (SMBus, needs the PawnIO driver)
- ASUS B650E MAX GAMING WIFI (Aura USB, three ARGB fan headers)
- Logitech G515 RAPID TKL keyboard and G502 HERO mouse (via Lightspeed receiver)
- DeepCool LT360 VISION cooler screen

OpenRGB 1.0 supports keyboards, mice, motherboards, GPUs, RAM, fans, coolers and strips from many brands. Only the hardware above has been tried here.

OpenRGB probes many buses, including SMBus on the motherboard. Close OpenRGB and iCUE before you start this app.

**Install on a second PC is untested.** The bundled-OpenRGB build has not been run against real hardware yet. Expect problems and read `logs\`.

## Download

Download `RGB-Anywhere-Setup.exe` (about 87 MB) from the [latest release](https://github.com/EQQX0/rgbanywhere/releases/latest) and run it. The app is inside the file, so Setup needs no further download. The setup window offers three choices, all on by default: a desktop shortcut, start with Windows, and open the app when setup finishes. Press Install. Setup checks the files against their SHA-256 and asks for admin once. The admin step copies them into `C:\Program Files\RGB Anywhere`, checks them again there, unpacks them and runs the installer. Your settings, the app's logs and its tokens live in `%LOCALAPPDATA%\RGB Anywhere`. A Start menu shortcut is always created. If something fails, the window offers an "Open log" button for `install.log` in the install folder.

To update, run Setup again. It stops the running app, the lighting daemon and OpenRGB before it replaces the files, so Windows asks for permission once. Updating from 0.2 or older moves the install from `%LOCALAPPDATA%\RGB Anywhere` to Program Files: the settings stay where they are, `config.json` and `original.json` move with the program, and the old program files are removed. (This update path has not been tried on a real PC yet.)

To remove it, open Settings > Apps > RGB Anywhere > Uninstall (or run `Uninstall.exe` in the install folder). This removes the scheduled tasks, startup entry, shortcuts, the install folder and your settings folder. The PawnIO driver stays, because other programs may use it.

The app has its own Python, so nothing else needs installing. RAM and some boards need the [PawnIO](https://pawnio.eu) driver. When the driver is missing, Setup downloads its installer from the author's release page (github.com/namazso/PawnIO.Setup), checks the SHA-256, installs it silently and starts it. If that fails, or the driver is installed but Windows will not start it, Setup prints why and USB devices keep working. The release does not contain the driver.

Prefer to do it by hand? Download `RGBAnywhere-win64.zip` from the same page, extract it to a folder you will keep and run `Install.cmd`. The `.sha256` file next to it holds the checksum.

Nothing is signed, so Windows SmartScreen may warn you. The checksum is downloaded from the same release as the zip, so it catches a damaged download but not a tampered release. The zip is built on one PC by `package.ps1`; `package.ps1` also builds the Setup exe from `installer/setup.cpp` and appends the zip to it (`installer/bundle.ps1`). A plain `installer/build.ps1` build has no zip inside and downloads the latest release instead.

## Build and install from source

You need Windows 10 or 11 and Python 3.10+ (tick "Add to PATH"). In PowerShell, in this folder:

```
powershell -ExecutionPolicy Bypass -File install.ps1    # asks for admin once
```

`install.ps1` installs the Python packages, runs `fetch-openrgb.ps1` if `openrgb\OpenRGB.exe` is missing (it records the build it fetched in `openrgb\fetched-from.txt`), registers the scheduled tasks "RGB Anywhere Lighting" (the elevated daemon, at logon) and "RGB Anywhere Rescan", adds the startup entry, the Start menu shortcut and, if Uninstall.exe is present, the Settings > Apps entry. `-Desktop` also adds a desktop shortcut and `-NoStartup` skips the startup entry. `uninstall.ps1 -Dir <folder>` removes an install (it only accepts a folder named "RGB Anywhere" that holds the app). RAM and some boards also need the [PawnIO](https://pawnio.eu) driver, which the script downloads from the author's release page (hash-checked) and installs when it is missing. USB devices work without it.

The unit tests need only Python and `openrgb-python`. They run against a stub OpenRGB server and touch no hardware:

```
python -m unittest discover -s tests -v
```

To build the Android app, run `android/build.ps1`. It needs a JDK and the Android SDK, and it signs with a key you create (`android/rgb-control.keystore`, never committed).

## How devices are grouped

Every device type OpenRGB reports has a group. The app only shows the groups your PC has.

| Group | Devices |
|-------|---------|
| ram | memory sticks |
| fans | motherboard, LED strips, ARGB fan hubs, coolers, case lighting |
| keyboard | keyboards, keypads |
| mouse | mice, mouse mats, mouse docks |
| gpu | graphics cards |
| audio | headsets, headset stands, speakers, microphones |
| other | gamepads, lamps, monitors, storage, laptops and anything OpenRGB can't classify |

OpenRGB files some fan hubs, docks and mats as "accessory", "light" or "unknown". For those the name decides, by whole words: "Mouse Dock" goes to mouse, "Commander Core" or "Uni Fan" to fans. Add a device name to `group_overrides` in `config.json` to move it to another group.

## Your original lighting

The first time RGB Anywhere finds a device, before it changes anything, it saves that device's mode, colors, brightness and speed to `original.json` in the install folder. A device you plug in later is saved the first time it is found. A device saved once is never saved again, so the file keeps what you had before RGB Anywhere.

To go back, press **Original** in the app and then **Restore now**, or run `rgbctl original` (or `python rgb.py original`). Pick any color to take over again. Uninstalling restores it too, before the app is removed.

Most devices can't report the colors they show. OpenRGB then reads back Direct, Static or Off with every color black, and restoring that would switch the lights off, so RGB Anywhere treats such a device as unreadable. On the PC this was tested on (Corsair DDR5, ASUS Aura board, Logitech keyboard and mouse) no device could be read. For unreadable devices, the Original window lets you choose per group what they show on Original and on uninstall:

| Choice | What happens |
|--------|--------------|
| Leave as is (default) | RGB Anywhere hands the device back and stops changing it, then restarts (or starts) the device's own app so it sends its lighting profile again, effects included: Logitech G HUB, Corsair iCUE, Razer Synapse, SteelSeries GG and NZXT CAM. Armoury Crate, MSI Center and others it can't restart are named for you to open. A device whose app isn't installed keeps its current color. |
| Built-in effect | The device's own rainbow-like effect (Spectrum Cycle, Rainbow Wave, ...), close to how most devices look out of the box. |
| Color | A static color you pick. |

From a script: `rgbctl exit all leave`, `rgbctl exit ram effect`, `rgbctl exit fans FF6000`. Devices you used with RGB Anywhere before this feature existed were saved with RGB Anywhere's colors, because those were already on them.

## config.json

`config.json` sits in the install folder (`C:\Program Files\RGB Anywhere` after Setup). The lighting service acts on it as admin, so only admins can change it: open Notepad as administrator to edit it. Copy `config.example.json` to `config.json` if it is missing. The file and every key are optional.

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
rgbctl color FF3C00 --target ram   # all (default), ram, fans, keyboard, mouse, gpu, audio, other
rgbctl brightness 60
rgbctl off                         # lights off, monitors off
rgbctl on
rgbctl original                    # back to the lighting from before RGB Anywhere
rgbctl exit fans effect            # what unreadable devices show on original/uninstall: leave, effect or a color
rgbctl presets / rgbctl preset 2
rgbctl profiles / rgbctl profile <id>
rgbctl monitors-off
rgbctl rescan
rgbctl describe                    # the command list as JSON, for an agent to read
```

`rgbctl.cmd` sits in the install folder (`C:\Program Files\RGB Anywhere`). Tell an agent to run `rgbctl describe` first. From a source checkout use `python rgbctl.py`.

The app serves this on 127.0.0.1:47824 and needs the token in `control.token` next to it. Any program running as your user can read that file, so any such program can control your lights.

## Troubleshooting

If devices are missing or colors don't change, run this in PowerShell from the install folder (`C:\Program Files\RGB Anywhere` after Setup). It only reads, so no light changes:

```
.\python\python.exe rgb.py --diagnose
```

It lists what the lighting daemon found: whether it runs as admin, whether OpenRGB started (and why not), the PawnIO driver state, the SMBus buses OpenRGB's log says it opened, every device with its modes, zones and how each mode takes colors, other RGB programs that are running, the newest OpenRGB log, and a numbered list of likely problems. If the daemon does not answer, it reports what it can see without it and exits with code 1. It saves the same text to `logs\diagnose-<time>.txt`. The daemon also writes this diagnosis to `logs\rgb.log` each time it detects devices.

`docs/lighting-path.md` follows a colour change and monitors-off from the button to the hardware, with the log line for each step. What the common messages mean:

| Message | Cause and fix |
|---------|---------------|
| Status "OpenRGB did not start" | The daemon runs but OpenRGB did not. `--diagnose` says why: OpenRGB missing (reinstall), port 47826 taken by another program, or OpenRGB exiting at start (see its log). Press Rescan to try again. |
| "OpenRGB stopped 3 times in 5 minutes" | OpenRGB keeps crashing, so the daemon stopped restarting it. Read the newest `cache\core\logs\OpenRGB_*.log`, then press Rescan. |
| Status "Lighting service not running" | The daemon does not answer. Run `schtasks /run /tn "RGB Anywhere Lighting"`; if it stops again, `logs\errors.log` says why (for example "port 47823 ... is taken by another program"). |
| "<device> has 0 LEDs" | An addressable header with no size. Set `argb_leds` in `config.json` and press Rescan. |

The app's logs live in `%LOCALAPPDATA%\RGB Anywhere\logs`, the lighting service's in `logs\` in the install folder (both in one `logs\` folder for a source checkout). Read `events.jsonl` and `errors.log` first. OpenRGB's own detection log is in `cache\core\logs\OpenRGB_<time>.log`; set `core_log_level` to `debug` in `config.json` for more detail. "Save debug report" in the app's Diagnostics dialog (the "Logs and diagnostics" button) includes both, plus the diagnosis. If the keyboard or mouse stop changing, press Rescan in the app or run `schtasks /run /tn "RGB Anywhere Rescan"`.

Razer devices that keep their own colors are usually being driven by Razer Synapse. In Synapse, set each Razer device's lighting to Static, color black, or quit Synapse and turn it off under Task Manager > Startup apps. `--diagnose` names the RGB programs it finds running (Razer Synapse, iCUE, G HUB, Armoury Crate, SignalRGB, SteelSeries GG, NZXT CAM, L-Connect and others) and lists devices that only have effect modes, which RGB Anywhere leaves alone.

Close iCUE and OpenRGB before using this app. Two programs talking to the RAM at once can push Corsair sticks into bootloader mode, where they ignore colors until iCUE's service resets them.

The cooler screen feature needs a DeepCool LT-series cooler with the DeepCool app installed.

## Security notes

The lighting service runs as admin, so its code must not be changeable without admin rights. Setup puts the program in Program Files, owned by Administrators, where normal programs can only read it; the service only reads your settings folder and never writes there. Before 0.3 the program lived in `%LOCALAPPDATA%\RGB Anywhere`, which any program running as you could change, and through the service gain admin rights without a prompt: update with Setup to move it. A source checkout installed with `install.ps1` has the same weakness, and the installer warns about it.

The phone remote is off by default. When you turn it on, it listens on your LAN with a random token in `state.json`. Anyone with the link controls your lights. The daemon listens on 127.0.0.1 only and checks a token stored in `rgbd.token`. The `rgbctl` API listens on 127.0.0.1:47824 and checks `control.token`.
