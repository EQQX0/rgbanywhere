"""Lighting daemon: hosts the embedded OpenRGB device core (rgbcore.dll) in an elevated process.

Reaching RAM and some boards over SMBus needs admin, the app does not, so the app talks to this daemon over
a localhost socket (token in rgbd.token). Run by the "RGB Anywhere Lighting" scheduled task (highest privileges).

  python rgbd.py            serve (and apply the saved colours once the devices are found)
  python rgbd.py --rescan   re-detect devices in the running daemon (the "RGB Anywhere Rescan" task); starts it if absent

Copyright (C) 2026 EQQX2
SPDX-License-Identifier: GPL-3.0-or-later
Uses rgbcore.dll, which contains code derived from OpenRGB (GPL-2.0-or-later); see NOTICE.
"""
import hmac
import json
import os
import secrets
import socketserver
import subprocess
import sys
import threading
import time

import applog
import diag
import rgb
import rgbcore_client as rc

_log = applog.get("rgbd")
_core = None
_client = None
_token = ""
_busy = threading.Lock()  # one rescan at a time


def _refresh_and_apply():
    _client.update()
    rgb.prepare(_client)
    rgb.apply(_client, rgb.load_state())


def _log_diagnosis():
    """Write what was found and why things may be missing to the daemon log, once per detection."""
    try:
        d = diag.collect(_core, _client)
        for line in diag.text(d).splitlines():
            _log.info("diag: %s", line)
        applog.event("core_diagnosis", ok=not d["hints"], devices=len(d["devices"]), i2c_buses=len(d["core"].get("i2c_buses", [])),
                     elevated=d["elevated"], pawnio=d["pawnio"]["service"], hints=d["hints"])
    except Exception:
        _log.exception("could not collect the diagnosis")


def _rescan():
    with _busy:
        helper = rgb.stop_helper()  # the cooler's sensor service disturbs RAM detection
        try:
            _core.rescan()
            time.sleep(1)
            _client.update()
            _log_diagnosis()
            _refresh_and_apply()
        finally:
            if helper:
                rgb.start_helper()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            req = json.loads(self.rfile.readline().decode("utf-8"))
            if not hmac.compare_digest(str(req.get("token", "")), _token):
                raise PermissionError("bad token")
            cmd = req["cmd"]
            if cmd == "ping":
                res = _core.version()
            elif cmd == "devices":
                res = _core.devices()
            elif cmd == "set_mode":
                _core.set_mode(req["dev"], req["mode"]); res = None
            elif cmd == "set_colors":
                res = _core.set_colors(req["dev"], req["colors"])
            elif cmd == "debug":
                res = diag.collect(_core, _client)
            elif cmd == "resize_zone":
                _core.resize_zone(req["dev"], req["zone"], req["leds"]); res = None
            elif cmd == "rescan":
                _rescan(); res = None
            else:
                raise ValueError("unknown command %r" % cmd)
            out = {"ok": True, "result": res}
        except Exception as e:
            _log.warning("request failed: %s: %s", type(e).__name__, e)
            out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.wfile.write((json.dumps(out) + "\n").encode("utf-8"))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False  # a second daemon must fail to bind rather than share the port
    daemon_threads = True


def reachable():
    try:
        rc.RemoteBackend(timeout=5)
        return True
    except Exception:
        return False


def serve():
    global _core, _client, _token
    try:
        server = Server(("127.0.0.1", rc.PORT), Handler)
    except OSError:
        _log.info("daemon already running; exiting")
        return
    if not rgb.is_elevated():
        _log.warning("not elevated: RAM and some boards will not be found. Run it from the 'RGB Anywhere Lighting' task.")
    _token = secrets.token_hex(24)
    with open(rc.TOKEN_FILE, "w", encoding="ascii") as f:
        f.write(_token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    helper = rgb.stop_helper()
    try:
        _core = rc.CoreBackend(log_level=rgb.CFG.get("core_log_level", "info"))
        _client = rc.Client(_core)
        _log.info("core: %s, %d devices", _core.version(), len(_client.devices))
        _log_diagnosis()
        _refresh_and_apply()
    except Exception:
        _log.exception("could not start the device core")
        applog.event("core_start", ok=False, error=applog.format_exc().strip().splitlines()[-1])
        raise
    finally:
        if helper:
            rgb.start_helper()
    try:
        threading.Event().wait()  # until the task is stopped
    except KeyboardInterrupt:
        pass
    try:
        _core.shutdown()
    finally:
        os._exit(0)  # the core's background threads would keep the process alive


def main():
    applog.setup("rgbd")
    if "--rescan" in sys.argv:
        if reachable():
            rc.RemoteBackend(timeout=300).request("rescan")
        else:  # nothing running: start the daemon through its task (it detects on start-up anyway)
            subprocess.run(["schtasks", "/run", "/tn", rc.TASK_LIGHTING], creationflags=0x08000000)
        return
    serve()


if __name__ == "__main__":
    main()
