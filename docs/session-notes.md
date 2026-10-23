# Session notes: openrgb-bundle

## Report (kept current; read this first)

State: tasks 1 to 8 of the brief are done and pushed; a fresh-eyes pass is under way. Nothing here has run on real hardware or against the real OpenRGB.exe. CI (`.github/workflows/tests.yml`) runs the unit tests on Ubuntu and Windows (Python 3.10 and 3.13), pyflakes, a PowerShell parse check and a MinGW build of the Setup exe. CI green at 07bb19e (all 106 tests ran on Windows, none skipped).

Fixed:
1. OpenRGB crash, hang, slow start, busy port, SDK timeouts, cut-off or reset replies: reconnect, restart (max 3 in 5 min), retry once.
2. A colour push racing a restart reaches the right device (requests carry name and location).
3. Daemon stays up when OpenRGB fails to start, says why, retries on Rescan; waits for first detection.
4. Effects-only devices no longer make every apply "fail" and reconnect. Whole-device colours fill every LED the daemon knows about.
5. Phone and rgbctl colours are validated; rgbctl monitors-off reports a failure.
6. Empty ARGB headers sized within their range on any device; bad argb_leds no longer stops the daemon.
7. openrgb-python 0.3.8 quirks: protocol-0 fallback, Nagle delay, unknown packet 160 from OpenRGB 1.0, double lock release.
8. Setup can update a running install (one elevated step stops the daemon, OpenRGB and the app first).
9. fetch-openrgb.ps1: TLS 1.2, either zip layout, fetched-from.txt; openrgb-python pinned to 0.3.8.
10. --diagnose works without OpenRGB or the daemon and gives one hint per failure.
11. THIRD_PARTY_LICENSES.md checked against upstream where possible.
12. After a rescan the app follows the daemon, not schtasks' English "Running"; a renumbering notice is read before any write; app worker race fixed; presets, profiles and service states checked.

Tests: 106 (1 skipped here: no PowerShell on PATH), all passing on Linux (Python 3.10 and 3.13) and in CI on Windows (`python -m unittest discover -s tests -v`). Files: test_sdk_backend, test_sdk_failures, test_apply, test_argb, test_diag, test_repo, test_rgbctl, test_applog, test_rgbd, test_scripts.

Unverified on hardware: everything touching OpenRGB.exe, PawnIO, RAM, ARGB sizing, colours, monitors-off, the job object, leftover-process cleanup, Setup's update path, package.ps1 output.

When something fails on the PC, `docs/lighting-path.md` says which log line each step leaves.
Commands for the Windows PC, read-only first (in the checkout, branch openrgb-bundle):
1. `python -m pip install openrgb-python==0.3.8` then `python -m unittest discover -s tests -v`
2. `powershell -ExecutionPolicy Bypass -File fetch-openrgb.ps1` then `type openrgb\fetched-from.txt` and `dir openrgb\PawnIOLib.dll openrgb\*.bin`
3. `sc query PawnIO` and `schtasks /query /tn "RGB Anywhere Lighting" /v /fo list`
4. `netstat -ano | findstr :47826` (should be empty before the daemon starts)
These change lights or processes:
5. `python rgb.py --diagnose` (starts the daemon if needed, which applies the saved colours)
6. `python rgbctl.py color red`, then `python rgbctl.py color 0000FF --target fans`, then `python rgbctl.py monitors-off`
7. `schtasks /run /tn "RGB Anywhere Rescan"`, then `python rgb.py --diagnose` again; read `logs\rgb.log` and `cache\core\logs\OpenRGB_*.log`
8. End the "RGB Anywhere Lighting" task in Task Manager and check that OpenRGB.exe ends with it (job object)
9. Build the Setup exe (`installer\build.ps1`) and a zip (`package.ps1`), install, then run Setup again over it (update path)

## Needs the owner

1. Hardware runs above. Please paste `logs\diagnose-*.txt` and the newest `cache\core\logs\OpenRGB_*.log` into the next session.
2. The OpenRGB zip could not be downloaded here (codeberg.org blocked), so its layout, hash and the licenses of PawnIOLib, hidapi, libusb and Qt inside it are unchecked.
3. License decision: pillow-heif's Windows wheels are GPLv2 by their own notice (x265). The release zip ships them next to a GPL-3.0-or-later app. Drop HEIC support, find another build, or accept.
4. Design decision: OpenRGB 1.0 accepts an SDK rescan request (packet 140, `NetworkServer.cpp` at 81bbe18) from any client. Rescan could re-detect without restarting OpenRGB. The restart was kept because it also clears a stuck OpenRGB; say if you want the packet tried first.
5. Naming: the app calls itself "RGB Control" (window, tray, Android app, firewall rule, README title) while Setup, tasks and folder say "RGB Anywhere". Left alone.
6. Check on a non-English Windows that `sc query PawnIO` still shows the state as a number and an English word (for example "4  RUNNING"); commit 8b446d9 relies on that and it was only checked against the English layout.
7. openrgb-python 0.3.8 supports SDK protocol 4 at most; OpenRGB 1.0 speaks 6. A newer client library may remove the workarounds; not evaluated.

## Done, step by step (newest last)

1. a96520a Failure handling in SdkBackend and rgbd (tests/test_sdk_failures.py).
2. 4f62eb2 set_colors and resize_zone edge cases (tests/test_sdk_backend.py, shared stub tests/sdk_stub.py).
3. d16cd28 TCP_NODELAY on the SDK socket.
4. 6a9900f End-to-end colour fixes, monitors-off result for rgbctl (tests/test_apply.py).
5. ef17e2f ARGB sizing (tests/test_argb.py).
6. fdb56d3 Setup update path (cross-compiled with MinGW, PowerShell parsed with pwsh).
7. f2b11d0 fetch-openrgb.ps1, pin, .gitignore, README, repo checks (tests/test_repo.py).
8. dac9f3b Diagnosis rework (tests/test_diag.py).
9. 2f864d8 GitHub Actions workflow.
10. 413b86f Skip packet 160; resize comments corrected from the 1.0 source.
11. 1968a48 Retry on openrgb-python's double lock release (found by CI on Windows).
12. 0321743 THIRD_PARTY_LICENSES.md.
13. 9db4cf8 App follows a rescan through the daemon's "busy" answer.
14. fea3862 rgb.py command line refuses unknown groups; fixed 3 s wait removed.
15. f187504 Daemon request timeout and size cap.
16. 03d4319 App lights worker keeps one client reference per job (not run: needs pywebview).
17. 4e438f8 Device-list notice read before resolving a device.
18. a3c7b94 rgbctl tests.
19. 9218eea Daemon reports another program on its port.
20. 8b446d9 sc query state read by value (see Needs the owner 6).
21. d323fd3 README message table.
22. eb672cd Presets and profiles validated on restore and apply.
23. 9d38984 Debug report adds OpenRGB.json and Configuration.json.
24. 03e4db8 Daemon start, retry and rescan tests.
25. cad3d03 pyflakes job in CI.
26. d63fd17 install.ps1 checks for Python 3.10+ (Store alias).
27. b4ff8fd PowerShell parse test.
28. 9995c03 MinGW build of the Setup exe in CI.
29. 961c095 docs/lighting-path.md: colour and monitors-off traced step by step with log lines.

## Next

- Watch CI on the latest commit and fix anything red.
- Fresh-eyes pass over the whole repo (rgb_app.pyw, cooler.py, media.py, rgbctl.py, applog.py) for bugs, dead code and races.

## Notes for a later session

- The `stop-slop` skill named in the brief is not installed here. Prose was checked by hand (plain words, no em dashes, no filler).
- OpenRGB 1.0 source facts used (tag release_1.0 = 81bbe18, gitlab.com/CalcProgrammer1/OpenRGB): CLI flags --server, --server-host, --server-port, --config, --noautoconnect exist (cli.cpp); logs go to `<config>/logs/OpenRGB_<yyyymmdd_hhmmss>.log` with `[time][Level  ]` prefixes (LogManager.cpp); "Registering I2C interface" is logged at info (DetectionManager.cpp); ACKs, server name and flags only go to protocol 6+ clients, packet 160 goes to all; ResizeZone has no range check and saves Configuration.json; SetModeDescription copies every field unchecked; plugin list is unanswered without a plugin manager.
- openrgb-python 0.3.8 quirks worked around in SdkBackend: 1 s protocol-version wait that falls back to protocol 0; a zero-byte header after the server closes keeps its lock for 10 s; a reply cut off half way loops forever; a reset mid-reply releases its lock twice; unknown packet types are left in the socket; ModeData.pack() refuses some modes OpenRGB reports.
- Tools that helped here: `pwsh` 7.4.6 from the GitHub release tarball (parse check: `[System.Management.Automation.Language.Parser]::ParseFile`), `g++-mingw-w64-x86-64` to compile installer/setup.cpp, `uv venv -p 3.10` for the 3.10 run.
