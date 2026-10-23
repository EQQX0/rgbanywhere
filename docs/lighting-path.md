# How a colour change and monitors-off travel, and where they can fail

Use this with `python rgb.py --diagnose` and the logs to find which step broke. File and function names are as of branch openrgb-bundle.

## Colour change

1. **Request.** The window (`ui/index.html`, `api.set_color`), a phone (`PhoneHandler`, `/api/set_color`) or `rgbctl color` (`ControlHandler`, `/call/set_color`) calls `Api.set_color(target, hex)` in `rgb_app.pyw`.
   Fails if the lights are off (answer: "Turn the lights on"), or the target or colour is invalid (`rgb.set_group_color` refuses it). rgbctl prints these as `{"ok": false}` with exit code 1.
2. **State.** `App.commit()` saves `state.json` and queues an "apply" job. A burst of changes (slider drags) collapses into one job.
3. **Connect.** `App.rgb_worker` connects with `rgb.connect()`: a `RemoteBackend` to the daemon on 127.0.0.1:47823, using the token in `rgbd.token`. If the daemon does not answer, the app runs the "RGB Anywhere Lighting" task once and keeps trying for 45 s.
   Log: `logs\rgb.log` "lighting daemon not reachable"; the status reads "Lighting service not running" or "OpenRGB did not start".
4. **Prepare.** `rgb.prepare()` sizes empty ARGB headers (`argb_size`, within leds_min..leds_max) and logs the device list ("devices (N): ...").
5. **Apply.** `rgb.apply()` goes through every device:
   - `dev_mode_name` picks Static, then Direct, then Custom (for "off": Direct, then Static, or the device's own "Off"). A device with none of these is skipped ("left as it is").
   - `Device.set_mode` then `Device.set_color` (fill=True) are sent to the daemon with the device's key (name and location).
   - Per-device failures are logged as "FAILED on <device>"; the app then reconnects once and applies again ("lights_retry" in `logs\events.jsonl`).
6. **Daemon.** `rgbd.Handler` checks the token, waits for the first detection to finish, and calls `SdkBackend`:
   - `_call` reconnects or restarts OpenRGB when needed (log "connecting to OpenRGB again", "starting OpenRGB again", "not started again for now").
   - `set_colors` answers 0 (per-LED colours sent), 1 (mode colour sent), 2 (the mode takes no colour) or 3 (no LEDs). 2 and 3 are logged by `rgb.apply` with the reason.
7. **OpenRGB.** `openrgb\OpenRGB.exe --server` applies the mode or LED packet to the controller. Its log is `cache\core\logs\OpenRGB_<time>.log`. Errors there (PawnIO, SMBus, a controller refusing a mode) end up in the diagnosis's "Last errors and warnings".

Colours that "do not change" with no error anywhere usually mean one of: the device's active mode takes no colour (result 2, see the diagnosis hint), the mode's brightness is at its minimum (hint "brightness at the minimum"), another RGB program overrides it (hint "Other RGB programs are running"), or an ARGB header has 0 LEDs (result 3, hint "has 0 LEDs").

## Monitors off

1. The Monitors button, `rgbctl monitors-off` or switching the lights off calls `App.monitors_off()`.
2. A thread waits 1.5 s (so the click or a nudge of the mouse does not wake the screens at once), then broadcasts `WM_SYSCOMMAND / SC_MONITORPOWER = 2` with `SendNotifyMessageW(HWND_BROADCAST, ...)`.
3. Outcome: `logs\events.jsonl` "monitors_off" with ok true or false and the Windows error code. rgbctl waits for this and reports a failure with exit code 1; the window and phones show "Couldn't turn the monitors off".
4. Windows wakes the monitors at the next keyboard or mouse input. A mouse that reports tiny movements, a game controller or a remote-desktop session can wake them at once; that is not an error and is not logged.
5. When the lights went off together with the monitors, `App.wake_watch` turns the lights back on at the first input after 3 s ("input after lights+monitors off; lights on").

Unverified on hardware: whether SC_MONITORPOWER behaves the same on PCs with Modern Standby, where Windows may treat it differently.
