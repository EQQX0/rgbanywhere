# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Logging and diagnostics for RGB Anywhere. The app writes to logs\\ in the user's data folder, the elevated lighting
daemon to logs\\ in the install folder (the same folder for a source checkout):

  rgb.log          everything (debug and up), rotating, 3 MB x 5
  errors.log       warnings and errors only, with stack traces, rotating
  events.jsonl     one JSON line per *attempt* (cooler pictures, lights, phone pairing, uploads, ...), failures included
  crash.log        hard crashes (faulthandler) and unhandled exceptions
  failed\\          copies of files that couldn't be converted (newest few), with a note on why
  debug-report-*.zip  made on demand: logs + settings (secrets removed) + system and tool versions

The sign-in token for phones is never written: it's masked in every message.
"""
import datetime
import faulthandler
import json
import logging
import logging.handlers
import os
import platform
import re
import secrets
import shutil
import sys
import threading
import traceback
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _under(path, roots):
    path = os.path.normcase(os.path.abspath(path))
    return any(r and path.startswith(os.path.normcase(os.path.join(os.path.abspath(r), ""))) for r in roots)


def folders(here, env, is_admin):
    """Where each kind of file goes, for code in `here` and environment `env`.

    Installed by Setup, the code sits in Program Files, which only admins can change, because the lighting daemon
    runs it as admin. Whatever a normal process writes (settings, the app's logs, its tokens and caches) then lives
    in the user's own folder, and the daemon only reads from there. A source checkout keeps everything next to the
    code. RGB_DATA_DIR and RGB_LOG_DIR override (the tests use them)."""
    protected = _under(here, [env.get(v) for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")])
    data = env.get("RGB_DATA_DIR") or (
        os.path.join(env["LOCALAPPDATA"], "RGB Anywhere") if protected and env.get("LOCALAPPDATA") else here)
    service_logs = os.path.join(here, "logs")  # the elevated daemon's: never in a folder a normal process controls
    user_logs = os.path.join(data, "logs")
    logs = env.get("RGB_LOG_DIR") or (service_logs if is_admin or not protected else user_logs)
    events = [os.path.join(logs, "events.jsonl")] if env.get("RGB_LOG_DIR") else sorted(
        {os.path.join(d, "events.jsonl") for d in (user_logs, service_logs)})
    return {"protected": protected, "data": data, "service_logs": service_logs, "user_logs": user_logs, "logs": logs,
            "events": events}


IS_ADMIN = _is_admin()
_F = folders(HERE, os.environ, IS_ADMIN)
PROTECTED, DATA_DIR, LOG_DIR = _F["protected"], _F["data"], _F["logs"]
SERVICE_LOG_DIR, USER_LOG_DIR = _F["service_logs"], _F["user_logs"]
EVENT_FILES = _F["events"]  # both processes' event files, for the app's summary and the debug report
FAILED_DIR = os.path.join(LOG_DIR, "failed")
EVENTS = os.path.join(LOG_DIR, "events.jsonl")
SESSION = secrets.token_hex(3)          # tells one run of the app apart from the next in the logs
MAX_FAILED_FILES, MAX_FAILED_BYTES = 5, 30 * 1024 * 1024
EVENTS_MAX = 5 * 1024 * 1024

_started = False
_lock = threading.Lock()
_secrets = set()
_crash_file = None

_TOKEN_RE = re.compile(r"(?i)((?:[?&]t=)|(?:rgbt=)|(?:phone_token\W{1,4}))[A-Za-z0-9_\-]{8,}")


def add_secret(value):
    if value and len(value) >= 8:
        _secrets.add(value)


def redact(text):
    """Mask phone sign-in tokens (and any registered secret) in a string."""
    text = _TOKEN_RE.sub(lambda m: m.group(1) + "***", str(text))
    for s in _secrets:
        text = text.replace(s, "***")
    return text


class _Redact(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
            red = redact(msg)
            if red != msg:
                record.msg, record.args = red, ()
            if record.exc_info and not record.exc_text:  # an exception's text can carry a URL or token too
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:
            pass
        return True


class _SafeRotating(logging.handlers.RotatingFileHandler):
    """Several processes (the app, the startup task, the CLI) share the files; if another one holds the file during
    a rollover, keep appending instead of failing."""

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                self.stream = self._open()


def get(name):
    return logging.getLogger("rgb." + name)


def setup(role="app"):
    """Start logging once per process. Safe to call again."""
    global _started, _crash_file
    if _started:
        return
    _started = True
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger("rgb")
    root.setLevel(logging.DEBUG)
    root.propagate = False
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s [%(processName)s:%(threadName)s] %(name)s: %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    for fname, level, size, count in (("rgb.log", logging.DEBUG, 3 * 1024 * 1024, 5),
                                      ("errors.log", logging.WARNING, 1024 * 1024, 3)):
        h = _SafeRotating(os.path.join(LOG_DIR, fname), maxBytes=size, backupCount=count, encoding="utf-8", delay=True)
        h.setLevel(level)
        h.setFormatter(fmt)
        h.addFilter(_Redact())
        root.addHandler(h)
    try:
        _crash_file = open(os.path.join(LOG_DIR, "crash.log"), "a", buffering=1, encoding="utf-8")
        _crash_file.write("\n--- %s session %s (%s) ---\n" % (datetime.datetime.now().isoformat(timespec="seconds"), SESSION, role))
        faulthandler.enable(_crash_file, all_threads=True)
    except Exception:
        pass

    log = get("main")

    def excepthook(et, ev, tb):
        log.critical("Unhandled exception", exc_info=(et, ev, tb))
        event("crash", ok=False, error="%s: %s" % (et.__name__, ev), where="main thread")
    sys.excepthook = excepthook

    def thread_hook(args):
        log.critical("Unhandled exception in thread %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        event("crash", ok=False, error="%s: %s" % (args.exc_type.__name__, args.exc_value),
              where="thread " + (args.thread.name if args.thread else "?"))
    threading.excepthook = thread_hook

    log.info("=== RGB Anywhere %s started: pid=%d session=%s python=%s %s", role, os.getpid(), SESSION,
             platform.python_version(), platform.platform())
    log.info("argv=%s", sys.argv)


def event(kind, /, ok=True, **fields):
    """Append one attempt/outcome to events.jsonl. Failures are also written to errors.log.
    Never raises: a logging mistake must not break the feature being logged."""
    for k in ("t", "session", "kind", "ok"):        # reserved names: keep both values instead of clashing
        if k in fields:
            fields["field_" + k] = fields.pop(k)
    rec = {"t": datetime.datetime.now().isoformat(timespec="milliseconds"), "session": SESSION, "kind": kind, "ok": bool(ok)}
    rec.update(fields)
    try:
        line = redact(json.dumps(rec, ensure_ascii=False, default=str))
        with _lock:
            os.makedirs(LOG_DIR, exist_ok=True)
            try:
                if os.path.getsize(EVENTS) > EVENTS_MAX:
                    for i in (2, 1):
                        a, b = "%s.%d" % (EVENTS, i), "%s.%d" % (EVENTS, i + 1)
                        if os.path.exists(a):
                            os.replace(a, b)
                    os.replace(EVENTS, EVENTS + ".1")
            except OSError:
                pass
            with open(EVENTS, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    short = ", ".join("%s=%s" % (k, str(v)[:120]) for k, v in fields.items() if k not in ("detail", "stack"))
    if ok:
        get("events").info("%s ok %s", kind, short)
    else:
        get("events").warning("%s FAILED %s", kind, short)


def keep_failed_input(path, reason):
    """Keep a copy of a file we couldn't handle (newest few only) so the problem can be reproduced later."""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) > MAX_FAILED_BYTES:
            return None
        os.makedirs(FAILED_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = re.sub(r"[^\w.\- ]+", "_", os.path.basename(path))[:80]
        dst = os.path.join(FAILED_DIR, "%s_%s" % (stamp, safe))
        shutil.copy2(path, dst)
        with open(dst + ".txt", "w", encoding="utf-8") as f:
            f.write("original: %s\nwhen: %s\nreason: %s\n" % (path, stamp, reason))
        files = sorted((p for p in os.listdir(FAILED_DIR) if not p.endswith(".txt")), reverse=True)
        for old in files[MAX_FAILED_FILES:]:
            for p in (old, old + ".txt"):
                try:
                    os.remove(os.path.join(FAILED_DIR, p))
                except OSError:
                    pass
        return dst
    except Exception:
        get("main").exception("Couldn't keep a copy of the failed file")
        return None


def _read_tail(path, max_bytes=256 * 1024):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="ignore")
        return data.splitlines()[1:] if size > max_bytes else data.splitlines()
    except OSError:
        return []


def recent_events(n=15):
    """The newest `n` events of the app and the lighting daemon together, oldest first."""
    out = []
    for path in EVENT_FILES:
        for line in _read_tail(path)[-n:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return sorted(out, key=lambda r: str(r.get("t", "")))[-n:]


def summary(hours=24):
    """{'failed': n, 'total': n, 'last_error': 'text', 'when': 'iso'} for the last `hours`."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    failed = total = 0
    last = None
    for rec in recent_events(400):
        if rec.get("t", "") < cutoff:
            continue
        total += 1
        if not rec.get("ok", True):
            failed += 1
            last = rec
    return {"failed": failed, "total": total, "when": last["t"] if last else None,
            "last_error": ("%s: %s" % (last["kind"], last.get("error", "failed"))) if last else None}


def _versions():
    from importlib import metadata
    lines = []
    for pkg in ("Pillow", "pillow-heif", "pywebview", "pystray", "openrgb-python", "qrcode", "imageio-ffmpeg", "playwright"):
        try:
            lines.append("%s %s" % (pkg, metadata.version(pkg)))
        except Exception:
            lines.append("%s (not installed)" % pkg)
    try:
        import imageio_ffmpeg, subprocess
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-version"], capture_output=True, text=True, timeout=15,
                           creationflags=0x08000000)
        lines.append("ffmpeg: " + (r.stdout.splitlines() or ["?"])[0])
    except Exception as e:
        lines.append("ffmpeg: unavailable (%s)" % e)
    return lines


def build_report(diagnosis=None):
    """Zip the logs, OpenRGB's own logs, settings (secrets removed), system info and, when given, the
    lighting daemon's diagnosis text (diag.text). Returns the zip path."""
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(LOG_DIR, "debug-report-%s.zip" % stamp)
    state_path = os.path.join(DATA_DIR, "state.json")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(LOG_DIR)):
            p = os.path.join(LOG_DIR, name)
            if os.path.isfile(p) and not name.startswith("debug-report-"):
                z.write(p, "logs/" + name)
        if os.path.normcase(SERVICE_LOG_DIR) != os.path.normcase(LOG_DIR) and os.path.isdir(SERVICE_LOG_DIR):
            for name in sorted(os.listdir(SERVICE_LOG_DIR)):  # the lighting daemon's, in the install folder
                p = os.path.join(SERVICE_LOG_DIR, name)
                if os.path.isfile(p):
                    try:
                        z.write(p, "service-logs/" + name)
                    except OSError as e:
                        z.writestr("service-logs/%s.unreadable.txt" % name, str(e))
        if os.path.isdir(FAILED_DIR):
            for name in os.listdir(FAILED_DIR):
                z.write(os.path.join(FAILED_DIR, name), "logs/failed/" + name)
        core_logs = os.path.join(HERE, "cache", "core", "logs")  # OpenRGB's detection log, written by the daemon
        if os.path.isdir(core_logs):
            for name in sorted(n for n in os.listdir(core_logs) if n.startswith("OpenRGB_"))[-3:]:
                try:
                    z.write(os.path.join(core_logs, name), "core-logs/" + name)
                except OSError as e:
                    z.writestr("core-logs/%s.unreadable.txt" % name, str(e))
        for name in ("OpenRGB.json", "Configuration.json"):  # OpenRGB's settings and the zone sizes it saved
            p = os.path.join(HERE, "cache", "core", name)
            if os.path.isfile(p):
                z.write(p, "core-config/" + name)
        if diagnosis:
            z.writestr("diagnosis.txt", redact(diagnosis))
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
            state.pop("phone_token", None)
            z.writestr("settings.json", json.dumps(state, indent=2, ensure_ascii=False))
        except Exception as e:
            z.writestr("settings.json", "unreadable: %s" % e)
        info = ["RGB Anywhere debug report", "made: " + datetime.datetime.now().isoformat(timespec="seconds"),
                "session: " + SESSION, "os: " + platform.platform(), "machine: " + platform.machine(),
                "python: " + sys.version, ""] + _versions()
        try:
            import subprocess
            r = subprocess.run(["tasklist", "/FI", "STATUS eq running", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                               timeout=20, creationflags=0x08000000)
            procs = sorted({l.split(",")[0].strip('"') for l in r.stdout.splitlines() if l})
            info += ["", "relevant processes: " + ", ".join(p for p in procs if re.search(
                r"openrgb|deepcool|armoury|lghub|icue|python|chrome|msedgewebview", p, re.I))]
        except Exception:
            pass
        z.writestr("system.txt", "\n".join(info))
    for old in sorted(p for p in os.listdir(LOG_DIR) if p.startswith("debug-report-"))[:-3]:
        try:
            os.remove(os.path.join(LOG_DIR, old))
        except OSError:
            pass
    get("main").info("Debug report written: %s", out)
    return out


def format_exc():
    return traceback.format_exc()
