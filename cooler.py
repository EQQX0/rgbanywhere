# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Change the image on the DeepCool LT360 VISION screen.

The DeepCool app plays a pre-converted 854x480 H.264 mp4 from its media folder and reloads
the last-selected one on startup. We swap that file's contents and restart the app.

Usage:
  python cooler.py <image|gif|video>   put this on the cooler screen
  python cooler.py --restore            put back the original DeepCool file
  python cooler.py --off / --on         black out the screen / bring its picture back
"""
import glob
import os
import re
import json
import shutil
import subprocess
import sys
import time

import imageio_ffmpeg

import applog
import media

log = applog.get("cooler")

APPDATA = os.environ["APPDATA"]
MEDIA = os.path.join(APPDATA, "DeepCool", "media")
LOG = os.path.join(APPDATA, "DeepCool", "logs", "main.log")


def _config_path():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"), encoding="utf-8-sig") as f:
            return json.load(f).get("deepcool_path")
    except Exception:
        return None


DEEPCOOL = _config_path() or r"C:\DeepCool\DeepCool.exe"
W, H, FPS = 854, 480, 24


def installed():
    """Whether a DeepCool screen cooler's app is on this PC (config.json `deepcool_path` overrides the default)."""
    return os.path.isfile(DEEPCOOL)


STILL_SECONDS = 10
NO_WINDOW = 0x08000000
LIT = ".lit"  # suffix of the picture parked beside the black clip while the lights are off


_last_active = None


def active_media():
    """Path of the mp4 the DeepCool app is currently set to play (from its log)."""
    try:
        with open(LOG, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError as e:
        log.error("Can't read DeepCool's log %s: %s", LOG, e)
        raise
    hits = re.findall(r"(?:add video|add media to player) (.+?\.mp4)\s*$", text, re.M)
    for path in reversed(hits):
        if os.path.exists(path.strip()):
            global _last_active
            if path.strip() != _last_active:  # asked every few seconds: only note when it changes
                _last_active = path.strip()
                log.info("the cooler is now set to play %s", _last_active)
            return path.strip()
    files = glob.glob(os.path.join(MEDIA, "**", "cropped-*.mp4"), recursive=True)
    if not files:
        log.error("No DeepCool media file found under %s and none named in its log (%d hits)", MEDIA, len(hits))
        raise FileNotFoundError("Pick any GIF once in the DeepCool app first, then try again.")
    log.warning("DeepCool log named no existing file; using the newest cropped-*.mp4: %s", max(files, key=os.path.getmtime))
    return max(files, key=os.path.getmtime)


def thumbnail(src_mp4, folder):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    for jpg in glob.glob(os.path.join(folder, "first_*.jpg")):
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", src_mp4, "-frames:v", "1", jpg],
                           capture_output=True, text=True, errors="ignore", creationflags=NO_WINDOW)
        if r.returncode:
            log.warning("Couldn't refresh DeepCool's thumbnail %s: %s", jpg, r.stderr.strip()[-300:])
        else:
            log.debug("thumbnail refreshed: %s", jpg)


def stop_app():
    r = subprocess.run(["taskkill", "/IM", "DeepCool.exe", "/F"], capture_output=True, text=True, errors="ignore",
                       creationflags=NO_WINDOW)
    log.info("stopped DeepCool (exit %s): %s", r.returncode, (r.stdout or r.stderr).strip()[:200])
    time.sleep(2)


def start_app():
    try:
        subprocess.Popen([DEEPCOOL, "-autoRun"], creationflags=NO_WINDOW)
        log.info("started DeepCool again")
    except OSError:
        log.exception("Couldn't start DeepCool (%s)", DEEPCOOL)
        raise


def swap_in(new_file, target):
    """Replace target while the app is closed (it locks the file it's playing)."""
    stop_app()
    try:
        for attempt in range(1, 11):
            try:
                os.replace(new_file, target) if new_file.endswith(".new.mp4") else shutil.copy2(new_file, target)
                log.info("replaced %s (attempt %d, %d KB)", target, attempt, os.path.getsize(target) // 1024)
                break
            except PermissionError as e:
                log.warning("file still locked (attempt %d/10): %s", attempt, e)
                time.sleep(1)
        else:
            log.error("DeepCool kept %s locked for 10 s", target)
            raise PermissionError("DeepCool is still holding " + target)
        thumbnail(target, os.path.dirname(target))
    finally:
        start_app()


def set_screen(src, spec=None):
    """Put `src` (picture / GIF / video) on the cooler, with optional edits (see media.py). Raises media.MediaError."""
    target = active_media()
    backup = target + ".orig"
    if not os.path.exists(backup):
        shutil.copy2(target, backup)
    tmp = target + ".new.mp4"
    log.info("set_screen: %s -> %s", src, target)
    try:
        media.render(src, spec, tmp)  # convert first: DeepCool is only stopped once we know the file is good
    except Exception:
        log.exception("conversion failed, DeepCool left untouched")
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    swap_in(tmp, target)
    _forget_lit(target)  # a picture chosen with the lights off is what comes back, not the one parked before
    return target


def restore():
    target = active_media()
    backup = target + ".orig"
    if not os.path.exists(backup):
        log.warning("restore requested but there is no backup next to %s", target)
        return False
    log.info("restoring original %s", backup)
    swap_in(backup, target)
    _forget_lit(target)  # the original is what plays now, whatever was set before the lights went off
    return True


def shown_file():
    """The file holding the picture the cooler shows with the lights on: the parked one while the screen is blacked out."""
    target = active_media()
    return target + LIT if os.path.exists(target + LIT) else target


def set_rendered(src):
    """Put an mp4 already in the cooler's format (a saved profile) on the screen, with no conversion."""
    target = active_media()
    backup = target + ".orig"
    if not os.path.exists(backup):
        shutil.copy2(target, backup)
    log.info("set_rendered: %s -> %s", src, target)
    swap_in(src, target)  # copies, keeping src's mtime, so the app can tell this picture is already showing
    _forget_lit(target)
    return target


def frame(src, jpg):
    """Save a still from src as a small jpg (profile thumbnails). Raises RuntimeError."""
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", "0.5", "-i", src,
                        "-frames:v", "1", "-vf", "scale=427:-2", jpg],
                       capture_output=True, text=True, errors="ignore", creationflags=NO_WINDOW)
    if r.returncode or not os.path.exists(jpg):
        log.error("couldn't take a still from %s: %s", src, r.stderr.strip()[-300:])
        raise RuntimeError("Couldn't make a thumbnail of the cooler picture")


def _forget_lit(target):
    try:
        os.remove(target + LIT)
    except FileNotFoundError:
        pass


def is_blank():
    """True while the screen is showing the black clip, i.e. the real picture is parked next to it."""
    return os.path.exists(active_media() + LIT)


def blank():
    """Lights off: park the current picture beside it and play a black clip, so the screen goes dark."""
    target = active_media()
    lit = target + LIT
    if os.path.exists(lit):
        log.info("blank: the screen is already dark")
        return False
    backup = target + ".orig"
    if not os.path.exists(backup):  # set_screen makes this on first use; do it here too so .orig is never the black clip
        shutil.copy2(target, backup)
    tmp = target + ".new.mp4"
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=%dx%d:r=%d" % (W, H, FPS), "-t", str(STILL_SECONDS),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", tmp],
                       capture_output=True, text=True, errors="ignore", creationflags=NO_WINDOW)
    if r.returncode:
        log.error("couldn't make the black clip: %s", r.stderr.strip()[-300:])
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError("Couldn't make the black clip for the cooler screen")
    shutil.copy2(target, lit)  # keeps the mtime, so the app still recognises the picture when it comes back
    try:
        swap_in(tmp, target)
    except Exception:
        _forget_lit(target)  # the picture never left; don't pretend the screen is dark
        raise
    log.info("screen off: %s parked as %s", target, lit)
    return True


def unblank():
    """Lights on: put the parked picture back."""
    target = active_media()
    lit = target + LIT
    if not os.path.exists(lit):
        log.info("unblank: nothing parked, the screen is already showing its picture")
        return False
    swap_in(lit, target)
    _forget_lit(target)
    log.info("screen on: %s restored", target)
    return True


if __name__ == "__main__":
    applog.setup("cooler-cli")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--restore":
        print("restored" if restore() else "nothing to restore")
    elif sys.argv[1] == "--off":
        print("screen off" if blank() else "already off")
    elif sys.argv[1] == "--on":
        print("screen on" if unblank() else "already on")
    else:
        print("now playing on cooler:", set_screen(sys.argv[1]))
