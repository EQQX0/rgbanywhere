"""Lighting daemon: runs the bundled OpenRGB in an elevated process.

Reaching RAM and some boards over SMBus needs admin, the app does not, so the app talks to this daemon over
a localhost socket (token in rgbd.token). Run by the "RGB Anywhere Lighting" scheduled task (highest privileges).

  python rgbd.py            serve (and apply the saved colours once the devices are found)
  python rgbd.py --rescan   re-detect devices in the running daemon (the "RGB Anywhere Rescan" task); starts it if absent

Copyright (C) 2026 EQQX2
SPDX-License-Identifier: GPL-3.0-or-later
Starts the unmodified OpenRGB (GPL-2.0-or-later) from openrgb\\ as a separate program; see NOTICE.
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
_start_error = None  # why OpenRGB did not start; requests report it until a rescan starts it
_ready = threading.Event()  # set once the first start finished, well or not


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
        applog.event("core_diagnosis", ok=not d["hints"], devices=len(d["devices"]), 
                     elevated=d["elevated"], pawnio=d["pawnio"]["service"], hints=d["hints"])
    except Exception:
        _log.exception("could not collect the diagnosis")


def _start_core():
    """Start OpenRGB and apply the saved colours. A failure is kept in _start_error: the daemon keeps serving, so the
    app and --diagnose can show why, and a rescan tries again."""
    global _core, _client, _start_error
    core = None
    try:
        core = rc.SdkBackend(log_level=rgb.CFG.get("core_log_level", "info"), on_restart=_after_restart)
        _client, _core, _start_error = rc.Client(core), core, None
    except Exception as e:
        if core is not None:
            core.shutdown()
        _core = _client = None
        _start_error = "%s: %s" % (type(e).__name__, e)
        _log.exception("could not start OpenRGB")
        applog.event("core_start", ok=False, error=_start_error)
        return
    _log.info("OpenRGB: %s, %d devices", _core.version(), len(_client.devices))
    _log_diagnosis()
    _refresh_and_apply()


def _after_restart():
    """OpenRGB exited and SdkBackend started it again: it has forgotten the zone sizes and colours, so apply them again
    (in a thread: the request that noticed still holds the backend's lock)."""
    applog.event("core_restart", ok=True, restarts=_core.debug()["restarts"] if _core else None)

    def go():
        with _busy:
            try:
                _refresh_and_apply()
            except Exception:
                _log.exception("could not apply the colours after OpenRGB restarted")
    threading.Thread(target=go, daemon=True, name="reapply").start()


def _rescan():
    with _busy:
        helper = rgb.stop_helper()  # the cooler's sensor service disturbs RAM detection
        try:
            if _core is None:  # it did not start before: try again from scratch
                _start_core()
                if _core is None:
                    raise RuntimeError("OpenRGB did not start: %s" % _start_error)
                return
            _core.rescan()
            time.sleep(1)
            _client.update()
            _log_diagnosis()
            _refresh_and_apply()
        finally:
            if helper:
                rgb.start_helper()


def _need_core():
    """The backend, once OpenRGB is up. While it is still detecting devices, wait (the app's request has 120 s)."""
    _ready.wait(100)
    if _core is None:
        if not _ready.is_set():
            raise RuntimeError("OpenRGB is still detecting devices; try again in a moment")
        raise RuntimeError("OpenRGB did not start (%s). Run 'python rgb.py --diagnose' or press Rescan." % _start_error)
    return _core


class Handler(socketserver.StreamRequestHandler):
    timeout = 30  # seconds to send the request line; a client that connects and says nothing must not keep a thread
    MAX_LINE = 1 << 20

    def handle(self):
        try:
            line = self.rfile.readline(self.MAX_LINE + 1)
            if len(line) > self.MAX_LINE:
                raise ValueError("request too long")
            req = json.loads(line.decode("utf-8"))
            if not hmac.compare_digest(str(req.get("token", "")), _token):
                raise PermissionError("bad token")
            cmd = req["cmd"]
            if cmd == "ping":
                res = _core.version() if _core else "OpenRGB not started"
            elif cmd == "devices":
                res = _need_core().devices()
            elif cmd == "set_mode":
                _need_core().set_mode(req["dev"], req["mode"], key=req.get("key")); res = None
            elif cmd == "set_colors":
                res = _need_core().set_colors(req["dev"], req["colors"], key=req.get("key"), fill=bool(req.get("fill")))
            elif cmd == "debug":
                res = diag.collect(_core, _client, start_error=_start_error)
            elif cmd == "resize_zone":
                _need_core().resize_zone(req["dev"], req["zone"], req["leds"], key=req.get("key")); res = None
            elif cmd == "rescan":
                _rescan(); res = None
            elif cmd == "busy":  # a start, rescan or re-apply is running (the app waits for a rescan to end)
                res = _busy.locked()
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
    global _token
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
        with _busy:
            _start_core()
    except Exception:
        _log.exception("could not apply the saved colours")
    finally:
        _ready.set()
        if helper:
            rgb.start_helper()
    try:
        threading.Event().wait()  # until the task is stopped
    except KeyboardInterrupt:
        pass
    try:
        if _core is not None:
            _core.shutdown()
    finally:
        os._exit(0)  # the server threads would keep the process alive


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
