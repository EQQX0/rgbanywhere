"""RGB Control: lighting (RAM, fans, keyboard, mouse via the embedded OpenRGB core) and the DeepCool cooler screen.

UI is ui/index.html in a WebView2 window (pywebview). A tiny localhost server serves the page and a
cached copy of whatever the cooler is playing, so the preview never locks DeepCool's file.

Modified by EQQX2, 2026-10-22: lighting goes through the embedded OpenRGB core (rgbd.py) instead of an OpenRGB server.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import base64
import ctypes
import ctypes.wintypes
import functools
import glob
import io
import json
import logging
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Hide the console if we were started with python.exe instead of pythonw.exe.
_con = ctypes.windll.kernel32.GetConsoleWindow()
if _con:
    ctypes.windll.user32.ShowWindow(_con, 0)
    ctypes.windll.kernel32.FreeConsole()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import applog  # noqa: E402

applog.setup("app")  # before anything else, so even a failure while starting up is recorded
log = applog.get("app")
plog = applog.get("phone")

import cooler  # noqa: E402
import icue  # noqa: E402
import media  # noqa: E402
import pystray  # noqa: E402
import qrcode  # noqa: E402
import rgb  # noqa: E402
import webview  # noqa: E402
import winaccent  # noqa: E402
from PIL import Image  # noqa: E402
from webview.dom import DOMEventHandler  # noqa: E402

APP_ID = "Admin.RGBControl"  # taskbar identity; matches the Start menu shortcut
TITLE = "RGB Control"
SINGLE_PORT = 47321  # a second launch connects here to show the running window
UI = os.path.join(HERE, "ui", "index.html")
ICON = os.path.join(HERE, "rgb.ico")
CACHE = os.path.join(HERE, "cache")
ACCENT_BACKUP = os.path.join(HERE, "accent_backup.json")  # the user's Windows accent before we synced it
NIRCMD = os.path.join(HERE, "nircmd.exe")  # NirSoft's NirCmd: turns the monitors off
PROFILES = os.path.join(HERE, "profiles")  # <id>.mp4 (the cooler clip, already converted) and <id>.jpg per profile
NO_WINDOW = 0x08000000
PREFERRED_MONITOR = rgb.CFG.get("preferred_monitor")  # config.json: open on the monitor whose hardware ID contains this (e.g. "MSI"); None = leave the window where Windows puts it
UPLOADS = os.path.join(HERE, "cache", "uploads")
STAGED = os.path.join(HERE, "cache", "staged")  # previews of pictures waiting in the editor
PHONE_PORT = 47822
FW_RULE = "RGB Control Phone"
# What a phone may call. Deliberately excludes desktop-only things (file dialog, calibration test mode).
PHONE_METHODS = {"init", "set_color", "set_brightness", "set_mode", "apply_preset", "save_preset",
                 "update_preset", "delete_preset", "restore_preset", "restore_screen", "rescan", "set_sync_accent",
                 "monitors_off", "save_profile", "update_profile", "delete_profile", "restore_profile",
                 "apply_profile", "calibrate", "set_calibration", "reset_calibration", "apply_screen", "discard_staged", "log_client"}
APK_PATH = os.path.join(HERE, "android", "RGB-Control.apk")
CALIBRATION_TIMEOUT = 600  # seconds: a phone that wanders off mid-calibration can't leave the lights on a test color
RESCAN_GAP = 30  # seconds between rescans: restarting OpenRGB again sooner tends to cost a RAM stick
RESCAN_RETRIES = 2  # extra rescans when one brings the RAM up wrong (see rgb.ram_problem)
# Logitech G HUB puts the keyboard and mouse lights to sleep while the PC is idle and wakes them with its own
# colors. OpenRGB isn't told, so nothing looks wrong from here; rescan when the user comes back.
WAKE_GRACE = 3  # seconds after lights+monitors go off before input counts as "wake" (the click itself, mouse nudges)
IDLE_AFTER = 300  # seconds without keyboard/mouse input; a rescan blinks every light, so short pauses don't count
# The price: roughly one rescan in five brings a RAM stick up missing, as the wrong model or without Direct
# mode (something garbles OpenRGB's reads while it detects them). Set to False to stop rescanning unasked.
IDLE_RESCAN = bool(rgb.CFG.get("idle_rescan", False))  # config.json; only useful with Logitech G HUB or similar
UPLOAD_EXTS = {".gif", ".png", ".apng", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif",
               ".avif", ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}
PICK_TYPES = ("Pictures and videos (*.jpg;*.jpeg;*.png;*.gif;*.webp;*.heic;*.heif;*.avif;*.bmp;*.tif;*.tiff;*.jfif;"
              "*.mp4;*.mov;*.m4v;*.webm;*.mkv;*.avi;*.3gp)", "All files (*.*)")
MAX_UPLOAD = 300 * 1024 * 1024

DEFAULT_PRESETS = [
    {"name": "Autumn", "color": "FF3C00", "groups": {"ram": "A62700", "fans": "A62700"}},
    {"name": "Blue", "color": "0000FF"},
    {"name": "Red", "color": "FF0000"},
    {"name": "Purple", "color": "8000FF"},
    {"name": "Cyan", "color": "00FFFF"},
    {"name": "Pink", "color": "FF0080"},
    {"name": "White", "color": "FFFFFF"},
]


# ---------- cooler preview ----------
def deepcool_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DeepCool.exe", "/NH"], capture_output=True,
                         text=True, creationflags=NO_WINDOW).stdout
    return "DeepCool.exe" in out


def cooler_preview():
    """(key, cached_copy, source) for what the cooler is set to play."""
    src = cooler.active_media()
    st = os.stat(src)
    key = "%d-%d" % (st.st_mtime_ns, st.st_size)
    dst = os.path.join(CACHE, "preview-%s.mp4" % key)
    if not os.path.exists(dst):
        os.makedirs(CACHE, exist_ok=True)
        for old in glob.glob(os.path.join(CACHE, "preview-*.mp4")):
            try:
                os.remove(old)
            except OSError as e:
                log.debug("couldn't remove old preview %s: %s", old, e)
        shutil.copy2(src, dst + ".part")
        os.replace(dst + ".part", dst)
        log.info("cooler preview refreshed from %s (%d KB)", src, os.path.getsize(dst) // 1024)
    return key, dst, src


def default_name(src):
    base = os.path.splitext(os.path.basename(src))[0]
    return re.sub(r"^cropped-", "", base)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self.send_file(UI, "text/html; charset=utf-8")
            elif path.startswith("/preview/"):
                name = os.path.basename(path)
                f = os.path.join(CACHE, name)
                if re.fullmatch(r"preview-[\d-]+\.mp4", name) and os.path.exists(f):
                    self.send_file(f, "video/mp4")
                else:
                    self.send_error(404)
            elif path.startswith("/staged/"):  # editor preview of a picture the user just chose
                name = os.path.basename(path)
                f = os.path.join(STAGED, name)
                if re.fullmatch(r"[0-9a-f]{12}\.jpg", name) and os.path.exists(f):
                    self.send_file(f, "image/jpeg")
                else:
                    self.send_error(404)
            elif path.startswith("/profiles/"):  # a profile's cooler thumbnail
                name = os.path.basename(path)
                f = os.path.join(PROFILES, name)
                if re.fullmatch(r"[0-9a-f]{10}\.jpg", name) and os.path.exists(f):
                    self.send_file(f, "image/jpeg")
                else:
                    self.send_error(404)
            else:
                self.send_error(404)
        except (ConnectionError, OSError):
            pass

    def send_file(self, path, ctype):
        size = os.path.getsize(path)
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m:
            start = int(m.group(1) or 0)
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


# ---------- phone access ----------
def lan_ip():
    """This PC's address on the local network (no packets are sent; connect() on UDP just picks the route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def firewall_ok():
    r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=" + FW_RULE],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode == 0 and FW_RULE in r.stdout


def add_firewall_rule():
    """Allow inbound TCP on the phone port from the local subnet on Private networks. Shows one UAC prompt."""
    args = ('advfirewall firewall add rule name="%s" dir=in action=allow protocol=TCP localport=%d '
            'profile=private remoteip=localsubnet' % (FW_RULE, PHONE_PORT))
    ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", args, None, 0)


_net_cache = {"t": 0, "v": True}


def network_is_private():
    """False when the connected network is 'Public' (the firewall rule is Private-only). Cached 30 s."""
    if time.time() - _net_cache["t"] < 30:
        return _net_cache["v"]
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-NetConnectionProfile | Where-Object IPv4Connectivity -ne 'Disconnected' "
                        "| Select-Object -First 1).NetworkCategory"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    cat = r.stdout.strip()
    _net_cache.update(t=time.time(), v=(cat != "Public"))
    return _net_cache["v"]


def qr_data_url(text):
    img = qrcode.make(text, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class PhoneHandler(Handler):
    """Same UI as the desktop window, served on the LAN. Every request needs the token (cookie)."""
    app = None

    _denied = {}  # ip -> last time we logged a refusal (a scanner can't fill the log)

    def token_ok(self, value):
        return bool(value) and secrets.compare_digest(value, self.app.state["phone_token"])

    def authed(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "rgbt" in c and self.token_ok(c["rgbt"].value)

    # --- logging hooks (the base class would print to a console that doesn't exist)
    def log_request(self, code="-", size="-"):
        if self.path.startswith(("/api/poll", "/favicon", "/api/hello", "/preview/", "/staged/", "/profiles/")):
            return  # polled constantly / harmless: not worth a line each
        plog.debug("%s %s %s -> %s", self.client_address[0], self.command, applog.redact(self.path), code)

    def log_error(self, fmt, *args):
        plog.warning("%s %s", self.client_address[0], applog.redact(fmt % args))

    def log_message(self, fmt, *args):
        plog.debug("%s %s", self.client_address[0], applog.redact(fmt % args))

    def fail(self, where):
        """Unexpected exception while serving: log everything, answer 500."""
        plog.exception("error while handling %s %s from %s", self.command, applog.redact(self.path), self.client_address[0])
        applog.event("phone_error", ok=False, where=where, path=applog.redact(self.path),
                     error=applog.format_exc().strip().splitlines()[-1])
        try:
            self.send_json({"error": "Something went wrong on the PC (see the logs)."}, 500)
        except Exception:
            pass

    def deny(self, why="no valid sign-in"):
        ip = self.client_address[0]
        if time.time() - self._denied.get(ip, 0) > 60:
            self._denied[ip] = time.time()
            plog.warning("refused %s %s from %s: %s (user-agent: %s)", self.command, applog.redact(self.path), ip, why,
                         (self.headers.get("User-Agent") or "")[:100])
            applog.event("phone_denied", ok=False, ip=ip, path=applog.redact(self.path), error=why)
        body = (b"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                b"<body style='font:16px system-ui;background:#0d0d11;color:#ddd;padding:32px'>"
                b"<h2>RGB Control</h2><p>Open the link from the QR code in RGB Control on your PC "
                b"(Phone button).</p>")
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/hello":  # unauthenticated on purpose: lets the Android app find this PC. Reveals nothing.
                return self.send_json({"app": "rgb-control", "v": 1})
            if u.path == "/":
                t = parse_qs(u.query).get("t", [""])[0]
                if self.token_ok(t):  # first visit from the QR code: remember it, then drop the token from the URL
                    applog.event("phone_paired", ok=True, ip=self.client_address[0],
                                 agent=(self.headers.get("User-Agent") or "")[:140])
                    self.send_response(302)
                    self.send_header("Set-Cookie", "rgbt=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000" % t)
                    self.send_header("Location", "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if not self.authed():
                    return self.deny("wrong or missing token" if t else "no sign-in cookie")
                host = self.headers.get("Host", "")
                pair = "rgbcontrol://pair?host=%s&t=%s" % (host, self.app.state["phone_token"])
                flags = "window.RGB_REMOTE=true;window.RGB_PAIR=%s;window.RGB_APK=%s" % (
                    json.dumps(pair), "true" if os.path.exists(APK_PATH) else "false")
                with open(UI, "rb") as f:
                    page = f.read().replace(b"<head>", ("<head><script>%s</script>" % flags).encode(), 1)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            elif not self.authed():
                self.deny()
            elif u.path == "/app.apk":
                if not os.path.exists(APK_PATH):
                    return self.send_error(404)
                size = os.path.getsize(APK_PATH)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="RGB-Control.apk"')
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(APK_PATH, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            elif u.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif u.path == "/api/poll":
                a = self.app
                self.send_json({"state": a.public_state(), "presets": a.presets, "profiles": a.profiles_public(),
                                "status": a.status, "cooler": a.cooler_info(), "features": a.features(), "busy": a.busy_text, "msg": a.last_msg})
            elif u.path.startswith(("/preview/", "/staged/", "/profiles/")):
                Handler.do_GET(self)
            else:
                self.send_error(404)
        except (ConnectionError, OSError) as e:
            plog.debug("client went away during GET %s: %s", applog.redact(self.path), e)
        except Exception:
            self.fail("GET")

    def do_POST(self):
        if not self.authed():
            return self.deny("POST without a valid sign-in")
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if u.path.startswith("/api/"):
                name = u.path[5:]
                if name not in PHONE_METHODS:
                    plog.warning("phone asked for a method that isn't allowed: %s", name)
                    return self.send_error(404)
                if n > 1_000_000:
                    return self.send_error(413)
                args = json.loads(self.rfile.read(n) or b"{}").get("args", [])
                try:
                    result = getattr(Api(self.app, source="phone"), name)(*args)
                except Exception as e:
                    plog.exception("phone API %s failed", name)
                    applog.event("phone_api", ok=False, method=name, error="%s: %s" % (type(e).__name__, e))
                    return self.send_json({"error": str(e)}, 400)
                self.app.after_remote()
                self.send_json(result)
            elif u.path == "/upload":
                name = os.path.basename(parse_qs(u.query).get("name", ["upload"])[0])
                ext = os.path.splitext(name)[1].lower()
                ip = self.client_address[0]
                if ext not in UPLOAD_EXTS:
                    applog.event("phone_upload", ok=False, name=name, ip=ip, error="unsupported type " + ext)
                    return self.send_json({"error": "Unsupported file type"}, 415)
                if n <= 0 or n > MAX_UPLOAD:
                    applog.event("phone_upload", ok=False, name=name, ip=ip, size=n, error="size not allowed")
                    return self.send_json({"error": "File too large (max 300 MB)"}, 413)
                if self.app.screen_busy:
                    applog.event("phone_upload", ok=False, name=name, ip=ip, error="cooler screen busy")
                    return self.send_json({"error": "The cooler screen is busy, try again in a moment"}, 409)
                os.makedirs(UPLOADS, exist_ok=True)
                for old in glob.glob(os.path.join(UPLOADS, "phone-*")):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
                dst = os.path.join(UPLOADS, "phone-%d%s" % (time.time_ns(), ext))
                with open(dst, "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(256 * 1024, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                if left:
                    applog.event("phone_upload", ok=False, name=name, ip=ip, size=n, error="interrupted, %d bytes missing" % left)
                    return self.send_json({"error": "Upload interrupted"}, 400)
                applog.event("phone_upload", ok=True, name=name, ip=ip, size=n)
                info = self.app.stage_safe(dst, os.path.splitext(name)[0], source="phone")  # the phone then opens the editor
                if "error" in info:
                    return self.send_json(info, 422)
                self.send_json({"ok": True, "staged": info})
            else:
                self.send_error(404)
        except (ConnectionError, OSError) as e:
            plog.warning("connection problem during POST %s: %s", applog.redact(self.path), e)
        except ValueError as e:
            plog.warning("bad request body for %s: %s", applog.redact(self.path), e)
        except Exception:
            self.fail("POST")


# ---------- app ----------
class Api:
    """Methods callable from JS (window.pywebview.api.*). Private attrs start with _ so pywebview skips them."""

    def __init__(self, app, source="desktop"):
        self._app = app
        self._source = source  # "desktop" window or "phone"; recorded with the cooler attempts

    def init(self):
        a = self._app
        return {"state": a.public_state(), "presets": a.presets, "profiles": a.profiles_public(), "status": a.status,
                "cooler": a.cooler_info(), "features": a.features()}

    def set_color(self, target, hexc):
        s = self._app.state
        if s.get("mode") == "off":  # lights are off: colours are locked until they're switched back on
            return {"error": "Turn the lights on to change colors."}
        if target == "all":
            s["color"], s["groups"] = hexc, {}
        else:
            s.setdefault("groups", {})[target] = hexc
        s["mode"] = "static"
        self._app.commit()

    def set_brightness(self, v):
        self._app.state["brightness"] = max(5, min(100, int(v)))
        self._app.commit()

    def set_mode(self, mode):
        was_off = self._app.state.get("mode") == "off"
        self._app.state["mode"] = "off" if mode == "off" else "static"
        self._app.commit()
        if mode == "off" and not was_off:  # lights off takes the monitors with it
            self._app.monitors_off(wake_lights=True)

    def apply_preset(self, i):
        return self._app.apply_preset(self._app.presets[int(i)])

    def set_calibration(self, group, gains):
        if group in rgb.GROUPS:
            self._app.state.setdefault("calibration", {})[group] = [max(0, min(200, int(g))) for g in gains]
            self._app.commit()

    def reset_calibration(self, group):
        self._app.state.setdefault("calibration", {}).pop(group, None)
        self._app.commit()
        return self._app.state["calibration"]

    def rescan(self):
        self._app.rescan()

    # ----- diagnostics -----
    def log_client(self, entry):
        """Errors raised inside the page itself (desktop window or a phone). Rate limited so a loop can't flood the log."""
        a = self._app
        now = time.time()
        a._client_log = [t for t in a._client_log if now - t < 60]
        if len(a._client_log) >= 30:
            return
        a._client_log.append(now)
        entry = entry if isinstance(entry, dict) else {"msg": str(entry)}
        level = {"error": logging.ERROR, "warn": logging.WARNING}.get(str(entry.get("level")), logging.INFO)
        msg, where = str(entry.get("msg", ""))[:500], "%s:%s" % (str(entry.get("src", ""))[:160], entry.get("line", "?"))
        applog.get("page").log(level, "[%s] %s (%s) %s", self._source, msg, where, str(entry.get("stack") or "")[:1500])
        if level >= logging.ERROR:
            applog.event("page_error", ok=False, source=self._source, error=msg, where=where,
                         agent=str(entry.get("ua", ""))[:120])

    def diag_info(self):
        failed = applog.FAILED_DIR
        return {"dir": applog.LOG_DIR, "session": applog.SESSION, "summary": applog.summary(24),
                "recent": applog.recent_events(12),
                "failed_files": len([f for f in os.listdir(failed) if not f.endswith(".txt")]) if os.path.isdir(failed) else 0}

    def open_logs(self):
        os.makedirs(applog.LOG_DIR, exist_ok=True)
        subprocess.Popen(["explorer", applog.LOG_DIR])

    def save_report(self):
        path = applog.build_report()
        subprocess.Popen(["explorer", "/select,", path])
        applog.event("debug_report", ok=True, path=path)
        return {"path": path}

    # ----- phone access (desktop window only) -----
    def phone_info(self):
        a = self._app
        url = "http://%s:%d/?t=%s" % (lan_ip(), PHONE_PORT, a.state["phone_token"])
        return {"enabled": a.phone_server is not None, "url": url, "host": "%s:%d" % (lan_ip(), PHONE_PORT),
                "qr": qr_data_url(url), "apk": os.path.exists(APK_PATH), "firewall": firewall_ok() if a.phone_server else True,
                "private": network_is_private() if a.phone_server else True}

    def set_phone(self, on):
        a = self._app
        a.state["phone_enabled"] = bool(on)
        rgb.save_state(a.state)
        log.info("phone access %s", "ON" if on else "OFF")
        if on:
            if not a.phone_start():
                a.state["phone_enabled"] = False
                rgb.save_state(a.state)
                applog.event("phone_enable", ok=False, error="port %d already in use" % PHONE_PORT)
                return {**self.phone_info(), "error": "Port %d is already in use" % PHONE_PORT}
            if not firewall_ok():
                log.info("firewall rule '%s' missing; asking Windows for permission to add it", FW_RULE)
                add_firewall_rule()  # one UAC prompt
            applog.event("phone_enable", ok=True, firewall_rule=firewall_ok())
        else:
            a.phone_stop()
            applog.event("phone_disable", ok=True)
        return self.phone_info()

    def show_apk(self):
        if os.path.exists(APK_PATH):
            subprocess.Popen(["explorer", "/select,", APK_PATH])

    def new_phone_token(self):
        """Invalidates every phone that was signed in; the QR code changes."""
        a = self._app
        a.state["phone_token"] = secrets.token_urlsafe(16)
        applog.add_secret(a.state["phone_token"])
        rgb.save_state(a.state)
        applog.event("phone_token_rotated", ok=True)
        return self.phone_info()

    def set_sync_accent(self, on):
        a = self._app
        a.state["sync_accent"] = bool(on)
        rgb.save_state(a.state)
        log.info("Windows accent sync %s", "ON" if on else "OFF")
        if on:
            a.sync_accent()
        elif os.path.exists(ACCENT_BACKUP):
            with open(ACCENT_BACKUP) as f:
                winaccent.restore(json.load(f))
        else:
            log.warning("accent sync turned off but accent_backup.json is missing; can't restore the original accent")

    def monitors_off(self):
        self._app.monitors_off()

    def calibrate(self, hexc):
        """Show hexc on every device while calibrating; None ends calibration mode."""
        if hexc and self._app.state.get("mode") == "off":  # a test colour would light the devices
            return {"error": "Turn the lights on to calibrate."}
        log.info("calibration test color: %s (%s)", hexc or "ended", self._source)
        self._app.test_color = hexc or None
        self._app.test_until = time.time() + CALIBRATION_TIMEOUT
        self._app.jobs.put("apply")

    def _current_preset(self, name):
        s = self._app.state
        groups = {g: c for g, c in s.get("groups", {}).items() if c != s["color"]}
        p = {"name": str(name).strip()[:24] or "Preset", "color": s["color"]}
        if groups:
            p["groups"] = groups
        return p

    def save_preset(self, name):
        self._app.presets.append(self._current_preset(name))
        log.info("preset saved: %r", name)
        return self._app.presets_changed()

    def update_preset(self, i, name, use_current):
        i = int(i)
        if 0 <= i < len(self._app.presets):
            p = self._app.presets[i]
            if use_current:
                p = self._current_preset(name)
            else:
                p["name"] = str(name).strip()[:24] or p["name"]
            self._app.presets[i] = p
        return self._app.presets_changed()

    def delete_preset(self, i):
        i = int(i)
        if 0 <= i < len(self._app.presets):
            log.info("preset deleted: %r", self._app.presets[i].get("name"))
            self._app.presets.pop(i)
        return self._app.presets_changed()

    def restore_preset(self, i, p):
        self._app.presets.insert(min(int(i), len(self._app.presets)), p)
        return self._app.presets_changed()

    # Profiles: the lighting and the cooler picture together. Each call returns the new list (or {"error": ...}).
    def save_profile(self, name):
        return self._app.save_profile(name)

    def update_profile(self, pid, name, use_current):
        return self._app.update_profile(str(pid), name, bool(use_current))

    def delete_profile(self, pid):
        return self._app.delete_profile(str(pid))

    def restore_profile(self, i, p):
        return self._app.restore_profile(int(i), p)

    def apply_profile(self, pid):
        return self._app.apply_profile(str(pid), source=self._source)

    def pick_screen(self):
        """File dialog -> stage the picture; the page then opens the editor with the returned info (or shows its error)."""
        res = self._app.window.create_file_dialog(webview.OPEN_DIALOG, file_types=PICK_TYPES)
        if not res:
            log.debug("file dialog closed without a choice")
            return None
        return self._app.stage_safe(res[0], source="desktop")

    def apply_screen(self, staged_id, spec):
        return self._app.apply_staged(staged_id, spec, source=self._source)

    def discard_staged(self, staged_id):
        self._app.staged.pop(str(staged_id), None)

    def restore_screen(self):
        self._app.restore_screen()


def _logged_api(cls):
    """Wrap every public Api method so an exception is written to the log with its stack trace before it propagates.
    (Under pythonw there is no console, so without this such errors would vanish.)"""
    def make(fn, name):
        @functools.wraps(fn)
        def wrapper(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except Exception:
                log.exception("API %s(%s) failed [%s]", name, ", ".join(repr(x)[:60] for x in a), getattr(self, "_source", "?"))
                applog.event("api_error", ok=False, method=name, source=getattr(self, "_source", "?"),
                             error=applog.format_exc().strip().splitlines()[-1])
                raise
        return wrapper
    for name, fn in list(vars(cls).items()):
        if not name.startswith("_") and callable(fn) and name != "log_client":
            setattr(cls, name, make(fn, name))
    return cls


Api = _logged_api(Api)


class App:
    def __init__(self):
        self.state = rgb.load_state()
        if self.state["mode"] not in ("static", "off"):
            self.state["mode"] = "static"
        self.presets = self.state.setdefault("presets", json.loads(json.dumps(DEFAULT_PRESETS)))
        self.profiles = self.state.setdefault("profiles", [])
        self._drop_orphan_profile_files()
        self.status = ["Connecting…", "warn"]
        self.wake_armed_at = 0  # time lights+monitors went off together; 0 = not waiting for input (see wake_watch)
        self.test_color = None
        self.test_until = 0
        self.staged = {}  # id -> {"src": path, "name": shown name} for pictures open in the editor
        if "phone_token" not in self.state:  # created once and persisted, so phones stay signed in across restarts
            self.state["phone_token"] = secrets.token_urlsafe(16)
            rgb.save_state(self.state)
        self.state.setdefault("phone_enabled", False)
        applog.add_secret(self.state["phone_token"])  # never let the sign-in token reach a log line
        self._client_log = []  # timestamps, to rate-limit errors reported by the page
        self._cooler_err = None
        self.phone_server = None
        self.busy_text = ""
        self.last_msg = ["", ""]
        self.state.setdefault("sync_accent", True)
        self.accent_timer = None
        if not os.path.exists(ACCENT_BACKUP):
            with open(ACCENT_BACKUP, "w") as f:
                json.dump(winaccent.snapshot(), f, indent=1)
        self.client = None
        self.rescan_lock = threading.Lock()
        self.rescan_timer = None  # a rescan waiting for RESCAN_GAP to pass
        self.last_rescan = 0
        self.rescan_check = False  # the next connection is the first after a rescan: look at the RAM
        self.rescan_tries = 0      # rescans repeated so far because the RAM came up wrong
        self.jobs = queue.Queue()
        self.quitting = False
        self.screen_busy = False
        self._screen_dark = None  # does the cooler show the black clip? None until first checked (see sync_screen)
        self._screen_gave_up = None  # the direction (dark or not) whose last attempt failed
        self.last_cooler = None
        self.window = None
        self.tray = None

    # ----- js bridge -----
    def js(self, fn, *args):
        if self.window:
            try:
                self.window.evaluate_js("window.%s && window.%s(%s)" % (fn, fn, ",".join(json.dumps(a) for a in args)))
            except Exception as e:  # e.g. the window is hidden or still loading; the page catches up on its next poll
                log.debug("evaluate_js(%s) failed: %s: %s", fn, type(e).__name__, e)

    def set_status(self, text, kind):
        if text != self.status[0]:
            log.info("status: %s (%s)", text, kind)
        self.status = [text, kind]
        self.js("rgbStatus", text, kind)

    # ----- lighting -----
    def commit(self):
        rgb.save_state(self.state)
        self.jobs.put("apply")
        self.tint()
        self.sync_accent()
        self.sync_screen()

    def sync_screen(self, after_job=False):
        """Lights off blacks out the cooler screen; any other mode brings its picture back.

        after_job: called when a screen job ends. A job that failed isn't repeated that way (each try restarts
        DeepCool); the next lights change tries again.
        """
        want_dark = self.state["mode"] == "off"
        if not after_job:
            self._screen_gave_up = None
        elif self._screen_gave_up == want_dark:
            return
        if self._screen_dark is None:  # first look: what the cooler is showing now
            try:
                self._screen_dark = cooler.is_blank()
            except Exception as e:  # no DeepCool media yet: nothing to black out
                log.debug("screen sync skipped: %s: %s", type(e).__name__, e)
                return
        if want_dark == self._screen_dark or self.screen_busy:
            return  # busy: run_screen looks again when it finishes

        def go():
            try:
                if want_dark:
                    cooler.blank()
                else:
                    cooler.unblank()
            except Exception:
                self._screen_gave_up = want_dark
                raise
            self._screen_dark = want_dark
        log.info("lights %s: %s the cooler screen", "off" if want_dark else "on", "blacking out" if want_dark else "restoring")
        self.run_screen(go, "Turning the screen off…" if want_dark else "Turning the screen on…",
                        "Screen off" if want_dark else "Screen on", kind="cooler_blank" if want_dark else "cooler_unblank")

    def sync_accent(self):
        """Debounced: Windows redraws a lot on accent changes, so wait until edits settle."""
        if not self.state.get("sync_accent") or self.state["mode"] == "off":
            return
        if self.accent_timer:
            self.accent_timer.cancel()

        def go():
            try:
                winaccent.set_accent(self.state["color"])
            except Exception:
                log.exception("Couldn't set the Windows accent color")
                applog.event("accent_sync", ok=False, error=applog.format_exc().strip().splitlines()[-1])
        self.accent_timer = threading.Timer(0.7, go)
        self.accent_timer.daemon = True
        self.accent_timer.start()

    def tint(self):
        try:
            tint_window(self.state["color"] if self.state["mode"] != "off" else "2A2A33")
        except Exception as e:
            log.debug("couldn't tint the window frame: %s: %s", type(e).__name__, e)

    def presets_changed(self):
        rgb.save_state(self.state)
        if self.tray:
            self.tray.update_menu()
        return self.presets

    def apply_preset(self, p):
        if self.state.get("mode") == "off":  # also reached from the tray menu
            self.js("rgbToast", "Turn the lights on to change colors", "info")
            return {"error": "Turn the lights on to change colors."}
        self.state["color"] = p["color"]
        self.state["groups"] = dict(p.get("groups", {}))
        self.state["mode"] = "static"
        self.commit()
        self.js("rgbState", self.public_state())

    def set_mode(self, mode):
        was_off = self.state.get("mode") == "off"
        self.state["mode"] = mode
        self.commit()
        self.js("rgbState", self.public_state())
        self.js("rgbToast", "Lights and monitors off" if mode == "off" else "Lights on", "ok")  # tray menu
        if mode == "off" and not was_off:
            self.monitors_off(wake_lights=True)

    def rescan(self, retry=False):
        """Re-detect devices via the elevated "RGB Rescan" task (rgbd.py --rescan), then reconnect.

        Asked again within RESCAN_GAP of the last one, it waits out the gap first; asking more often than
        that changes nothing. `retry` marks a rescan the app repeats by itself (see after_rescan).
        """
        if not retry:
            self.rescan_tries = 0
        with self.rescan_lock:
            if self.rescan_timer:
                return
            wait = self.last_rescan + RESCAN_GAP - time.time()
            if wait > 0:
                log.info("rescan asked %ds after the last one; doing it in %ds", RESCAN_GAP - wait, wait)
                self.rescan_timer = threading.Timer(wait, self._rescan)
                self.rescan_timer.daemon = True
                self.rescan_timer.start()
            else:
                self.last_rescan = time.time()
        if wait > 0:
            self.set_status("Waiting to rescan…", "warn")
        else:
            self._rescan()

    def _rescan(self):
        with self.rescan_lock:
            self.rescan_timer = None
            self.last_rescan = time.time()
        self.set_status("Rescanning…", "warn")
        self.rescan_check = True
        self.client = None
        r = subprocess.run(["schtasks", "/run", "/tn", "RGB Rescan"], capture_output=True, text=True, errors="ignore",
                           creationflags=NO_WINDOW)
        ok = r.returncode == 0
        log.info("rescan: schtasks /run 'RGB Rescan' -> exit %s %s", r.returncode, (r.stdout + r.stderr).strip()[:200])
        applog.event("openrgb_rescan", ok=ok, error=None if ok else (r.stdout + r.stderr).strip()[:200])

        def later():
            # The task closes OpenRGB, starts it again and waits for detection. Connect once it is done:
            # connecting while OpenRGB is still down would start a second copy of it.
            time.sleep(3)
            for _ in range(90):  # up to 45 s: the task also stops and starts DeepCool's helper around the restart
                q = subprocess.run(["schtasks", "/query", "/tn", "RGB Rescan", "/fo", "csv", "/nh"], capture_output=True,
                                   text=True, errors="ignore", creationflags=NO_WINDOW)
                if "Running" not in q.stdout:
                    break
                time.sleep(0.5)
            self.jobs.put("connect")
        threading.Thread(target=later, daemon=True, name="rescan-wait").start()

    def after_rescan(self):
        """Rescan again, up to RESCAN_RETRIES times, when a rescan brought the RAM up wrong."""
        if not self.rescan_check:
            return
        self.rescan_check = False
        wrong = rgb.ram_problem(self.client)
        if not wrong:
            log.info("RAM is right after the rescan%s", " (%d repeated)" % self.rescan_tries if self.rescan_tries else "")
            return
        # Not under iCUE: it holds the sticks while it is open, so detecting them again can't go better.
        stop = ("iCUE is open" if icue.running() else
                "still wrong after %d more rescans" % RESCAN_RETRIES if self.rescan_tries >= RESCAN_RETRIES else None)
        if stop:
            log.warning("RAM came up wrong after the rescan (%s); not rescanning again: %s", wrong, stop)
            applog.event("rescan_retry", ok=False, problem=wrong, error=stop)
            self.set_status("RAM came up wrong", "warn")
            return
        self.rescan_tries += 1
        log.warning("RAM came up wrong after the rescan (%s); rescanning again (%d of %d)", wrong, self.rescan_tries,
                    RESCAN_RETRIES)
        applog.event("rescan_retry", ok=True, problem=wrong, attempt=self.rescan_tries)
        self.rescan(retry=True)

    def rgb_worker(self):
        while True:
            job = self.jobs.get()
            while not self.jobs.empty():  # collapse bursts (slider drags) into one apply
                nxt = self.jobs.get()
                job = "connect" if "connect" in (job, nxt) else nxt
            try:
                if self.client is None or job == "connect":
                    self.set_status("Connecting…", "warn")
                    self.client = rgb.connect(wait=45)
                    rgb.prepare(self.client)
                    self.note_devices()
                    self.set_status("%d devices" % len(self.client.devices), "ok")
                    if job == "connect":  # the one queued once the rescan task is done, so detection has finished
                        self.after_rescan()
                    if self.client is None:
                        continue  # the repeat rescan started straight away
                st = dict(self.state)
                if self.test_color:
                    st.update(test=self.test_color, mode="static")
                if rgb.apply(self.client, st, log=self._rgb_log):
                    # some/all devices failed: OpenRGB probably restarted (Rescan, sleep). Reconnect once and retry.
                    log.warning("some devices failed to take the colors; reconnecting to the lighting daemon and retrying once")
                    self.set_status("Reconnecting…", "warn")
                    self.client = rgb.connect(wait=20)
                    rgb.prepare(self.client)
                    self.note_devices()
                    self.set_status("%d devices" % len(self.client.devices), "ok")
                    again = rgb.apply(self.client, st, log=self._rgb_log)
                    applog.event("lights_retry", ok=not again, failed_after_retry=again)
            except Exception as e:
                log.exception("lights worker failed (job=%s)", job)
                applog.event("lights_worker", ok=False, job=job, error="%s: %s" % (type(e).__name__, e))
                self.client = None
                self.set_status("Lighting service not running", "bad")

    def idle_watch(self):
        """Rescan when the user comes back to an idle PC, to take the keyboard and mouse back (see IDLE_AFTER)."""
        away = 0
        while True:
            idle = idle_seconds()
            if idle >= IDLE_AFTER:
                away = idle
            elif away:
                # Not while a rescan is already under way, and not under iCUE: detecting the RAM while iCUE
                # is talking to it is what knocks sticks out (see icue.py).
                skip = "not connected" if self.client is None else "iCUE is open" if icue.running() else None
                log.info("back after %ds idle; %s", away, "no rescan: " + skip if skip else "rescanning")
                applog.event("idle_return", ok=True, idle_s=away, skipped=skip)
                away = 0
                if not skip:
                    self.rescan()
            time.sleep(2)

    def wake_watch(self):
        """Lights switched off together with the monitors come back on at the first keyboard/mouse input, the same
        moment Windows wakes the monitors. Armed by monitors_off(wake_lights=True) once the monitors really went off."""
        while True:
            armed = self.wake_armed_at
            if armed:
                if self.state.get("mode") != "off":
                    self.wake_armed_at = 0  # someone switched the lights on another way
                elif time.time() - armed > WAKE_GRACE and idle_ms() < 1000:
                    self.wake_armed_at = 0
                    log.info("input after lights+monitors off; lights on")
                    applog.event("wake_lights", ok=True)
                    self.set_mode("static")
            time.sleep(0.25)

    @staticmethod
    def _rgb_log(msg):
        """rgb.apply reports per-device results; failures are already logged in detail by rgb.py, successes aren't needed."""
        pass

    # ----- what this PC has -----
    def note_devices(self):
        """Remember which of the four UI groups have a device, so the UI only offers those (kept for the next start)."""
        found = {rgb.group_of(d) for d in self.client.devices}
        groups = [g for g in rgb.GROUPS if g in found]
        if groups and groups != self.state.get("device_groups"):
            self.state["device_groups"] = groups
            rgb.save_state(self.state)

    def features(self):
        return {"cooler": cooler.installed(), "groups": self.state.get("device_groups") or list(rgb.GROUPS)}

    # ----- cooler -----
    def cooler_info(self):
        if not cooler.installed():
            return {"live": False, "name": "", "url": ""}
        try:
            key, dst, src = cooler_preview()
        except Exception as e:
            msg = "%s: %s" % (type(e).__name__, e)
            if msg != self._cooler_err:  # polled every few seconds: log a given problem once, not every time
                self._cooler_err = msg
                log.warning("can't read the cooler's current picture: %s", msg)
            return {"live": False, "name": "Nothing set in DeepCool yet", "url": ""}
        self._cooler_err = None
        name = self.state.get("cooler_name") if self.state.get("cooler_key") == key else None
        return {"live": deepcool_running(), "name": name or default_name(src),
                "url": "/preview/" + os.path.basename(dst)}

    def cooler_watch(self):
        """Keep the preview in sync, including changes made in the DeepCool app itself."""
        while True:
            try:
                if self.test_color and time.time() > self.test_until:  # calibration left open: restore the real colors
                    log.info("calibration left open for %ds; restoring the real colors", CALIBRATION_TIMEOUT)
                    self.test_color = None
                    self.jobs.put("apply")
                    self.js("rgbCalClosed")
                if not self.screen_busy:
                    info = self.cooler_info()
                    if info != self.last_cooler:
                        self.last_cooler = info
                        self.js("rgbCooler", info)
            except Exception:
                log.exception("cooler watcher hit an error (it keeps running)")
            time.sleep(4)

    def run_screen(self, fn, busy, done, kind="cooler_apply", meta=None, src=None):
        """Run a slow cooler job in a thread. Every attempt, successful or not, is written to events.jsonl."""
        if self.screen_busy:
            log.warning("%s ignored: the cooler screen is busy", kind)
            return
        self.screen_busy = True
        self.busy_text, self.last_msg = busy, ["", ""]  # mirrored to phones via /api/poll
        self.js("rgbMsg", "", "")
        self.js("rgbBusy", busy)
        meta = meta or {}

        def work():
            t0 = time.time()
            try:
                fn()
                time.sleep(3)  # let DeepCool come back up before we report "live"
                self.last_msg = [done, "ok"]
                applog.event(kind, ok=True, ms=int((time.time() - t0) * 1000), **meta)
            except Exception as e:
                log.exception("%s failed", kind)
                msg = str(e) or type(e).__name__
                kept = applog.keep_failed_input(src, "%s: %s" % (kind, msg)) if src else None
                applog.event(kind, ok=False, ms=int((time.time() - t0) * 1000), error=msg[:300], exception=type(e).__name__,
                             detail=str(getattr(e, "detail", ""))[-1500:], kept_copy=kept, **meta)
                self.last_msg = [msg[:110], "bad"]
            finally:
                self.screen_busy = False
                self.busy_text = ""
                self.last_cooler = self.cooler_info()
                self.js("rgbMsg", *self.last_msg)
                self.js("rgbCooler", self.last_cooler)
                self.sync_screen(after_job=True)  # the lights may have been flipped again while this ran
                self.js("rgbBusy", "")
        threading.Thread(target=work, daemon=True).start()

    # ----- phone -----
    def phone_start(self):
        if self.phone_server:
            return True
        try:
            PhoneHandler.app = self
            srv = ThreadingHTTPServer(("0.0.0.0", PHONE_PORT), PhoneHandler)
        except OSError as e:
            log.exception("can't start the phone server on port %d", PHONE_PORT)
            applog.event("phone_server", ok=False, error="bind failed: %s" % e)
            return False
        self.phone_server = srv
        threading.Thread(target=srv.serve_forever, daemon=True, name="phone-http").start()
        log.info("phone server listening on 0.0.0.0:%d (LAN address %s)", PHONE_PORT, lan_ip())
        applog.event("phone_server", ok=True, port=PHONE_PORT, lan_ip=lan_ip(), firewall_rule=firewall_ok())
        return True

    def phone_stop(self):
        if self.phone_server:
            self.phone_server.shutdown()
            self.phone_server.server_close()
            self.phone_server = None
            log.info("phone server stopped")

    def public_state(self):
        """State as sent to any UI: never includes the phone sign-in token."""
        return {k: v for k, v in self.state.items() if k not in ("phone_token", "profiles")}

    # ----- profiles: the lighting and the cooler picture, saved together -----
    def profiles_public(self):
        """Profiles as the page shows them, each with its thumbnail's address (the ?v= changes when it's re-saved)."""
        out = []
        for p in self.profiles:
            jpg = os.path.join(PROFILES, p["id"] + ".jpg")
            v = int(os.path.getmtime(jpg)) if os.path.exists(jpg) else 0
            out.append(dict(p, thumb="/profiles/%s.jpg?v=%d" % (p["id"], v) if v else ""))
        return out

    def _profiles_changed(self):
        rgb.save_state(self.state)
        return self.profiles_public()

    def _find_profile(self, pid):
        return next((p for p in self.profiles if p.get("id") == pid), None)

    def _capture_profile(self, pid, name):
        """Copy what the cooler shows (the real picture, even while the lights have it blacked out) and the lighting."""
        if not cooler.installed():  # no cooler on this PC: a profile is just the lighting
            s = self.state
            return {"id": pid, "name": str(name).strip()[:24] or "Profile", "color": s["color"],
                    "groups": {g: c for g, c in s.get("groups", {}).items() if c != s["color"]},
                    "brightness": s.get("brightness", 100), "cooler_name": ""}
        if self.screen_busy:
            raise RuntimeError("The cooler screen is busy, try again in a moment.")
        try:
            src = cooler.shown_file()
        except Exception as e:
            raise RuntimeError("Set a picture on the cooler first.") from e
        os.makedirs(PROFILES, exist_ok=True)
        mp4, jpg = os.path.join(PROFILES, pid + ".mp4"), os.path.join(PROFILES, pid + ".jpg")
        shutil.copy2(src, mp4 + ".part")  # copy2 keeps the mtime: applying it later can tell it's already showing
        os.replace(mp4 + ".part", mp4)
        cooler.frame(mp4, jpg)
        s = self.state
        st = os.stat(mp4)  # same mtime and size as src, so the same key cooler_preview gives it
        key = "%d-%d" % (st.st_mtime_ns, st.st_size)
        pic = s.get("cooler_name") if s.get("cooler_key") == key else default_name(cooler.active_media())
        groups = {g: c for g, c in s.get("groups", {}).items() if c != s["color"]}
        return {"id": pid, "name": str(name).strip()[:24] or "Profile", "color": s["color"], "groups": groups,
                "brightness": s.get("brightness", 100), "cooler_name": pic or ""}

    def save_profile(self, name):
        try:
            p = self._capture_profile(secrets.token_hex(5), name)
        except Exception as e:
            log.exception("couldn't save profile %r", name)
            applog.event("profile_save", ok=False, error=str(e)[:200])
            return {"error": str(e) or "Couldn't save the profile"}
        self.profiles.append(p)
        log.info("profile saved: %r (%s, cooler: %s)", p["name"], p["color"], p["cooler_name"])
        applog.event("profile_save", ok=True, name=p["name"])
        return self._profiles_changed()

    def update_profile(self, pid, name, use_current):
        p = self._find_profile(pid)
        if not p:
            return self.profiles_public()
        if use_current:
            try:
                p.update(self._capture_profile(pid, name))
            except Exception as e:
                log.exception("couldn't re-save profile %r", p["name"])
                return {"error": str(e) or "Couldn't update the profile"}
        else:
            p["name"] = str(name).strip()[:24] or p["name"]
        log.info("profile updated: %r (recaptured=%s)", p["name"], use_current)
        return self._profiles_changed()

    def delete_profile(self, pid):
        """Its files stay until the next start, so Undo can bring it back whole (see _drop_orphan_profile_files)."""
        p = self._find_profile(pid)
        if p:
            self.profiles.remove(p)
            log.info("profile deleted: %r", p["name"])
        return self._profiles_changed()

    def restore_profile(self, i, p):
        if isinstance(p, dict) and re.fullmatch(r"[0-9a-f]{10}", str(p.get("id", ""))) and not self._find_profile(p["id"]):
            keep = {k: p[k] for k in ("id", "name", "color", "groups", "brightness", "cooler_name") if k in p}
            self.profiles.insert(max(0, min(i, len(self.profiles))), keep)
        return self._profiles_changed()

    def _drop_orphan_profile_files(self):
        keep = {p.get("id") for p in self.profiles}
        for f in glob.glob(os.path.join(PROFILES, "*")):
            if os.path.splitext(os.path.basename(f))[0].split(".")[0] not in keep:
                try:
                    os.remove(f)
                except OSError as e:
                    log.debug("couldn't remove old profile file %s: %s", f, e)

    def apply_profile(self, pid, source="desktop"):
        """Lights at once; the cooler picture swapped in as a background job (skipped when it's already showing)."""
        p = self._find_profile(pid)
        if not p:
            return {"error": "That profile is gone."}
        if self.state.get("mode") == "off":
            self.js("rgbToast", "Turn the lights on to apply a profile", "info")
            return {"error": "Turn the lights on to apply a profile."}
        mp4 = os.path.join(PROFILES, pid + ".mp4")
        s = self.state
        s["color"], s["groups"], s["mode"] = p["color"], dict(p.get("groups", {})), "static"
        s["brightness"] = max(5, min(100, int(p.get("brightness", s.get("brightness", 100)))))
        err = None
        if not cooler.installed():
            pass
        elif not os.path.exists(mp4):
            err = "This profile's cooler picture is missing; only the lights changed."
        elif self.screen_busy:
            err = "The cooler screen is busy; only the lights changed."
        else:
            try:
                target = cooler.active_media()
                a, b = os.stat(target), os.stat(mp4)
                same = (a.st_mtime_ns, a.st_size) == (b.st_mtime_ns, b.st_size) and not os.path.exists(target + cooler.LIT)
            except Exception:
                same = False
            if not same:
                def go():
                    cooler.set_rendered(mp4)
                    self._screen_dark = False
                    key, _, _ = cooler_preview()
                    self.state["cooler_name"], self.state["cooler_key"] = p.get("cooler_name") or p["name"], key
                    rgb.save_state(self.state)
                # Started before commit(): with the screen busy, commit's sync_screen leaves the cooler to this job.
                self.run_screen(go, "Switching to %s…" % p["name"], "Profile %s is on" % p["name"], kind="profile_apply",
                                meta={"name": p["name"], "source": source})
        log.info("profile applied: %r (from %s)%s", p["name"], source, "; " + err if err else "")
        self.commit()
        self.js("rgbState", self.public_state())
        return {"error": err} if err else {"ok": True}

    # ----- monitors -----
    def monitors_off(self, wake_lights=False):
        """Turn every monitor off with NirCmd. Windows wakes them at the next keyboard or mouse input.
        wake_lights: the lights went off with them, so that same input switches the lights back on (see wake_watch)."""
        def go():
            err = None
            if not os.path.exists(NIRCMD):
                err = "nircmd.exe is missing from the RGB folder"
            else:
                try:
                    # Wait a moment first: the click that asked for it, or a nudge of the mouse right after, would wake them again.
                    r = subprocess.run([NIRCMD, "cmdwait", "1500", "monitor", "off"], capture_output=True, text=True,
                                       errors="ignore", creationflags=NO_WINDOW, timeout=15)
                    if r.returncode:
                        err = "nircmd exited with %s %s" % (r.returncode, (r.stdout + r.stderr).strip()[:150])
                except (OSError, subprocess.SubprocessError) as e:
                    err = "%s: %s" % (type(e).__name__, e)
            applog.event("monitors_off", ok=err is None, error=err)
            if err:
                log.error("couldn't turn the monitors off: %s", err)
                self.last_msg = ["Couldn't turn the monitors off", "bad"]
                self.js("rgbMsg", *self.last_msg)
            else:
                log.info("monitors off")
                if wake_lights:
                    self.wake_armed_at = time.time()
        threading.Thread(target=go, daemon=True, name="monitors").start()

    def after_remote(self):
        """A phone changed something: bring the desktop window (and tray menu) in line."""
        self.js("rgbState", self.public_state())
        self.js("rgbPresets", self.presets)
        self.js("rgbProfiles", self.profiles_public())
        if self.tray:
            self.tray.update_menu()

    # ----- picture editor staging -----
    @staticmethod
    def _file_facts(path, source):
        try:
            size_kb = os.path.getsize(path) // 1024
        except OSError:
            size_kb = None
        return {"file": os.path.basename(path), "ext": os.path.splitext(path)[1].lower(), "size_kb": size_kb, "source": source}

    def stage_safe(self, path, display_name=None, source="desktop"):
        """Read the picture and make an editor preview. Returns info for the editor, or {"error": readable message}.
        Every attempt is recorded; a copy of a file that can't be read is kept for later debugging."""
        facts = self._file_facts(path, source)
        t0 = time.time()
        log.info("staging %s (%s KB, %s)", facts["file"], facts["size_kb"], source)
        try:
            os.makedirs(STAGED, exist_ok=True)
            sid = secrets.token_hex(6)
            info = media.make_preview(path, os.path.join(STAGED, sid + ".jpg"))
        except media.MediaError as e:
            log.warning("can't use %s: %s", path, e)
            kept = applog.keep_failed_input(path, "stage: %s" % e)
            applog.event("cooler_stage", ok=False, ms=int((time.time() - t0) * 1000), error=str(e), detail=e.detail[-1500:],
                         kept_copy=kept, **facts)
            return {"error": str(e)}
        except Exception as e:
            log.exception("unexpected error reading %s", path)
            kept = applog.keep_failed_input(path, "stage: %s: %s" % (type(e).__name__, e))
            applog.event("cooler_stage", ok=False, ms=int((time.time() - t0) * 1000), exception=type(e).__name__,
                         error=str(e)[:300], detail=applog.format_exc()[-1500:], kept_copy=kept, **facts)
            return {"error": "Couldn't read this file (%s)." % type(e).__name__}
        applog.event("cooler_stage", ok=True, ms=int((time.time() - t0) * 1000), media_kind=info["kind"], w=info["w"], h=info["h"],
                     duration=round(info["duration"], 1), **facts)
        name = display_name or os.path.splitext(os.path.basename(path))[0]
        self.staged[sid] = {"src": path, "name": name}
        for old in list(self.staged)[:-8]:  # keep the last few; forget (and delete previews of) older ones
            self.staged.pop(old, None)
            try:
                os.remove(os.path.join(STAGED, old + ".jpg"))
            except OSError as e:
                log.debug("couldn't remove old editor preview %s: %s", old, e)
        return {"id": sid, "name": name, "kind": info["kind"], "w": info["w"], "h": info["h"],
                "duration": round(info["duration"], 1), "preview": "/staged/%s.jpg" % sid}

    def apply_staged(self, sid, spec, source="desktop"):
        entry = self.staged.get(str(sid))
        if not entry or not os.path.exists(entry["src"]):
            log.warning("apply requested for staged picture %s but it's gone (%s)", sid, "entry missing" if not entry else "file missing")
            applog.event("cooler_apply", ok=False, error="staged picture no longer available", source=source)
            return {"error": "That picture isn't available any more. Choose it again."}
        if self.screen_busy:
            log.warning("apply for %s refused: the cooler screen is busy", entry["name"])
            return {"error": "The cooler screen is busy, try again in a moment."}
        self.set_screen(entry["src"], entry["name"], spec, source=source)
        return {"ok": True}

    def set_screen(self, path, shown, spec=None, source="desktop"):
        def go():
            cooler.set_screen(path, spec)
            key, _, _ = cooler_preview()
            self.state["cooler_name"], self.state["cooler_key"] = shown, key
            rgb.save_state(self.state)
        meta = dict(self._file_facts(path, source), name=shown, spec=media.normalize_spec(spec))
        log.info("put on cooler: %s spec=%s", shown, meta["spec"])
        self.run_screen(go, "Converting %s…" % shown, "Now on your cooler", kind="cooler_apply", meta=meta, src=path)

    def restore_screen(self):
        def go():
            if not cooler.restore():
                raise RuntimeError("Nothing to restore")
            self.state.pop("cooler_name", None)
            self.state.pop("cooler_key", None)
            rgb.save_state(self.state)
        self.run_screen(go, "Restoring original…", "Original restored", kind="cooler_restore")

    def on_drop(self, e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        path = files[0].get("pywebviewFullPath") if files else None
        if not path:
            log.warning("a drop reached the window but carried no file path (%d items)", len(files))
        if path:
            info = self.stage_safe(path, source="drop")
            if "error" in info:
                self.last_msg = [info["error"], "bad"]
                self.js("rgbMsg", info["error"], "bad")
            else:
                self.js("rgbEdit", info)

    # ----- window / tray -----
    def on_loaded(self):
        self.window.dom.document.events.drop += DOMEventHandler(self.on_drop, True, True)

    def on_shown(self):
        log.info("window shown for the first time")
        move_to_preferred_monitor()
        style_window(color=self.state["color"])
        self.tint()

    def on_closing(self):
        if self.quitting:
            log.info("window closing for real (quit)")
            return True
        log.info("window hidden to the tray")
        self.window.hide()
        if self.test_color:  # leaving mid-calibration: go back to the real colors
            log.info("closed during calibration; restoring the real colors")
            self.test_color = None
            self.jobs.put("apply")
            self.js("rgbCalClosed")
        return False  # cancel close; keep running in the tray

    def show(self):
        log.info("showing the window")
        try:
            self.window.show()
            self.window.restore()
            move_to_preferred_monitor()
            style_window(front=True, color=self.state["color"])
            self.tint()
        except Exception:
            log.exception("couldn't show the window")
            raise

    def quit(self):
        log.info("quit requested from the tray")
        self.quitting = True
        if self.tray:
            self.tray.stop()
        self.window.destroy()

    def start_tray(self):
        def preset(p):
            return lambda icon, item: self.apply_preset(p)

        def items():  # rebuilt on update_menu(), so it follows preset edits
            yield pystray.MenuItem("Open RGB Control", lambda i, it: self.show(), default=True)
            yield pystray.Menu.SEPARATOR
            for p in self.presets:
                yield pystray.MenuItem(p["name"], preset(p))
            yield pystray.Menu.SEPARATOR
            yield pystray.MenuItem("Lights on", lambda i, it: self.set_mode("static"))
            yield pystray.MenuItem("Lights off", lambda i, it: self.set_mode("off"))
            yield pystray.MenuItem("Rescan devices", lambda i, it: self.rescan())
            yield pystray.Menu.SEPARATOR
            yield pystray.MenuItem("Open logs folder", lambda i, it: Api(self).open_logs())
            yield pystray.MenuItem("Save debug report", lambda i, it: Api(self).save_report())
            yield pystray.Menu.SEPARATOR
            yield pystray.MenuItem("Quit", lambda i, it: self.quit())

        menu = pystray.Menu(items)
        self.tray = pystray.Icon(TITLE, Image.open(ICON), TITLE, menu)
        self.tray.run_detached()


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("DeviceName", ctypes.c_wchar * 32), ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", ctypes.c_ulong), ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


class _MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.wintypes.RECT), ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.c_ulong), ("szDevice", ctypes.c_wchar * 32)]


def find_monitor(match=PREFERRED_MONITOR):
    """(HMONITOR, work-area RECT) of the monitor whose hardware ID contains `match` (e.g. 'MSI'), else None."""
    u = ctypes.windll.user32
    ids, i = {}, 0
    while True:  # \\.\DISPLAYn  ->  hardware ID of the monitor plugged into it (MONITOR\MSI3FA6\...)
        a = _DISPLAY_DEVICE()
        a.cb = ctypes.sizeof(a)
        if not u.EnumDisplayDevicesW(None, i, ctypes.byref(a), 0):
            break
        j = 0
        while True:
            m = _DISPLAY_DEVICE()
            m.cb = ctypes.sizeof(m)
            if not u.EnumDisplayDevicesW(a.DeviceName, j, ctypes.byref(m), 0):
                break
            ids[a.DeviceName] = ids.get(a.DeviceName, "") + m.DeviceID.upper()
            j += 1
        i += 1
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_ssize_t)

    def cb(hmon, dc, rect, lp):
        mi = _MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(mi)
        u.GetMonitorInfoW(hmon, ctypes.byref(mi))
        if match.upper() in ids.get(mi.szDevice, ""):
            found.append((hmon, mi.rcWork))
        return True
    u.EnumDisplayMonitors(None, None, proto(cb), 0)
    return found[0] if found else None


def move_to_preferred_monitor():
    """Center the window on the preferred monitor (unless it's already there). No-op if that monitor is absent."""
    if not PREFERRED_MONITOR:
        return
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    try:
        target = find_monitor() if hwnd else None
    except Exception:
        log.exception("monitor lookup failed; leaving the window where Windows put it")
        return
    if not hwnd:
        log.debug("move_to_preferred_monitor: window not found yet")
        return
    if not target:
        log.warning("no monitor matching %r is connected; the window stays on the current screen", PREFERRED_MONITOR)
        return
    hmon, work = target
    if u.MonitorFromWindow(hwnd, 2) == hmon:  # MONITOR_DEFAULTTONEAREST
        return
    log.info("moving the window to the %s monitor", PREFERRED_MONITOR)
    if u.IsZoomed(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE: a maximized window would just re-maximize on the old screen
    r = ctypes.wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    x = max(work.left, work.left + (work.right - work.left - w) // 2)
    y = max(work.top, work.top + (work.bottom - work.top - h) // 2)
    u.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)  # NOSIZE | NOZORDER | NOACTIVATE


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_ms():
    """Milliseconds since the last keyboard or mouse input in this session."""
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    # both are 32-bit millisecond tick counts, so the subtraction has to wrap the same way
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF


def idle_seconds():
    """Seconds since the last keyboard or mouse input in this session."""
    return idle_ms() // 1000


def colorref(rgb_tuple):
    r, g, b = rgb_tuple
    return ctypes.c_int(r | (g << 8) | (b << 16))  # COLORREF is 0x00BBGGRR


def tint_window(hexc):
    """Title bar = current lighting color mixed into the page's near-black; border = the color itself."""
    hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
    if not hwnd:
        return
    c = [int(hexc[i:i + 2], 16) for i in (0, 2, 4)]
    base = (8, 8, 12)
    caption = tuple(round(b + (x - b) * 0.28) for x, b in zip(c, base))
    border = tuple(round(b + (x - b) * 0.75) for x, b in zip(c, base))
    dwm = ctypes.windll.dwmapi
    dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref(caption)), 4)  # DWMWA_CAPTION_COLOR
    dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(colorref(border)), 4)   # DWMWA_BORDER_COLOR
    dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(colorref((236, 236, 241))), 4)  # DWMWA_TEXT_COLOR


def style_window(front=False, color="FF3C00"):
    """Dark title bar tinted with the lighting color, plus our icon on the native window."""
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    if not hwnd:
        return
    dark = ctypes.c_int(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
    tint_window(color)
    for size, which in ((16, 0), (32, 1)):  # ICON_SMALL, ICON_BIG
        h = u.LoadImageW(None, ICON, 1, size, size, 0x10)
        if h:
            u.SendMessageW(hwnd, 0x80, which, h)
    if front:
        u.SetForegroundWindow(hwnd)


def single_instance():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", SINGLE_PORT))
        srv.listen(1)
        return srv
    except OSError:
        try:
            socket.create_connection(("127.0.0.1", SINGLE_PORT), timeout=2).close()
            log.info("another RGB Control is already running; asked it to show its window")
        except OSError as e:
            log.warning("port %d is busy but nothing answered there (%s); not starting a second copy", SINGLE_PORT, e)
        return None


def _guarded(fn, name):
    """Run a background loop; if it ever dies, say why instead of disappearing silently."""
    def run():
        try:
            fn()
        except Exception:
            log.critical("background thread %s died", name, exc_info=True)
            applog.event("thread_died", ok=False, thread=name, error=applog.format_exc().strip().splitlines()[-1])
    return run


def main():
    srv = single_instance()
    if srv is None:
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    log.info("packages: %s", "; ".join(applog._versions()))

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True, name="local-http").start()
    url = "http://127.0.0.1:%d/" % http.server_address[1]
    log.info("window page served at %s", url)

    app = App()
    log.info("state loaded: color=%s mode=%s brightness=%s presets=%d phone_enabled=%s sync_accent=%s", app.state.get("color"),
             app.state.get("mode"), app.state.get("brightness"), len(app.presets), app.state.get("phone_enabled"),
             app.state.get("sync_accent"))
    app.window = webview.create_window(TITLE, url, js_api=Api(app), width=1060, height=640,
                                       min_size=(900, 600), background_color="#0d0d11",
                                       hidden="--tray" in sys.argv)
    app.window.events.loaded += app.on_loaded
    app.window.events.shown += app.on_shown
    app.window.events.closing += app.on_closing

    def listen():
        while True:
            conn, _ = srv.accept()
            conn.close()
            log.info("second launch asked this window to show itself")
            app.show()

    def started():
        log.info("window system is up; starting tray and background workers")
        app.start_tray()
        for name, target in (("lights", app.rgb_worker), ("cooler-watch", app.cooler_watch), ("single-instance", listen),
                             ("icue-watch", functools.partial(icue.watch, app.rescan)), ("idle-watch", app.idle_watch),
                             ("wake-watch", app.wake_watch)):
            if name == "idle-watch" and not IDLE_RESCAN:
                continue
            if name == "icue-watch" and not rgb.CFG.get("icue_quit_on_close", False):
                continue
            threading.Thread(target=_guarded(target, name), daemon=True, name=name).start()
        app.jobs.put("connect")
        app.sync_accent()
        if app.state.get("phone_enabled"):
            app.phone_start()

    try:
        webview.start(started)
    except Exception:
        log.critical("the window system failed", exc_info=True)
        applog.event("webview", ok=False, error=applog.format_exc().strip().splitlines()[-1])
        raise
    finally:
        log.info("=== RGB Control exiting (session %s) ===", applog.SESSION)


if __name__ == "__main__":
    main()
