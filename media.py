# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Turn any picture, GIF or video into the 854x480 H.264 mp4 the DeepCool screen plays, applying the user's edits.

Pictures (JPG, PNG, WebP, HEIC, AVIF, TIFF, BMP, single-frame GIF, ...) are composed with Pillow and then looped
into a short video. Animated formats and videos go through ffmpeg filters. Animated WebP/APNG, which ffmpeg
can't read reliably, are unpacked with Pillow first.

Edits (all optional), as a dict `spec`:
    rot      0 / 90 / 180 / 270   clockwise degrees
    flip     bool                 mirror left-right (after rotating)
    fit      "fill" | "fit" | "blur"   fill = cover the screen, fit = whole picture, blur = whole picture on a blurred copy
    zoom     0.2 .. 6             1 = the size `fit` gives
    px, py   -1.5 .. 1.5          picture centre offset, as a fraction of the screen width / height
    bright, contrast, sat   -0.9 .. 0.9   0 = unchanged
"""
import math
import os
import re
import shutil
import subprocess
import tempfile

import time

import imageio_ffmpeg
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import applog

log = applog.get("media")

try:  # HEIC / HEIF / AVIF (phone photos)
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception as _e:  # pragma: no cover - optional
    pillow_heif = None
    log.warning("HEIC/AVIF support unavailable: %s", _e)

W, H, FPS = 854, 480, 24
STILL_SECONDS = 10      # how long a picture is held (the cooler loops the file)
MIN_LOOP_SECONDS = 4    # very short clips are repeated up to this length
MAX_SECONDS = 120       # longer videos are cut here (keeps the file small)
TIMEOUT = 600           # seconds before a conversion is given up on
MAX_SIDE = 6000         # larger pictures are shrunk before editing
NO_WINDOW = 0x08000000

DEFAULT_SPEC = {"rot": 0, "flip": False, "fit": "fill", "zoom": 1.0, "px": 0.0, "py": 0.0,
                "bright": 0.0, "contrast": 0.0, "sat": 0.0}


class MediaError(Exception):
    """An error whose message is fit to show the user. `detail` keeps the technical text (ffmpeg's stderr, ...) for the logs."""

    def __init__(self, message, detail=""):
        super().__init__(message)
        self.detail = detail


def _ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def normalize_spec(spec):
    s = dict(DEFAULT_SPEC)
    s.update({k: v for k, v in (spec or {}).items() if k in DEFAULT_SPEC})

    def num(v, lo, hi, d):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return d
        return d if math.isnan(v) else max(lo, min(hi, v))
    s["rot"] = int(round(num(s["rot"], 0, 360, 0) / 90.0)) % 4 * 90
    s["flip"] = bool(s["flip"])
    s["fit"] = s["fit"] if s["fit"] in ("fill", "fit", "blur") else "fill"
    s["zoom"] = num(s["zoom"], 0.2, 6.0, 1.0)
    s["px"], s["py"] = num(s["px"], -1.5, 1.5, 0.0), num(s["py"], -1.5, 1.5, 0.0)
    for k in ("bright", "contrast", "sat"):
        s[k] = num(s[k], -0.9, 0.9, 0.0)
    return s


# ---------------------------------------------------------------------------------------- reading
def _open_image(src):
    """Open with Pillow, or None if Pillow doesn't know the format (videos)."""
    try:
        im = Image.open(src)
        im.load() if getattr(im, "n_frames", 1) == 1 else im.seek(0)
        return im
    except Exception as e:
        log.debug("Pillow couldn't open %s: %s: %s", os.path.basename(src), type(e).__name__, e)
        return None


def _to_rgb(im):
    """Any Pillow mode -> RGB, with transparency flattened onto black."""
    if im.mode in ("I;16", "I;16L", "I;16B", "I"):
        im = im.point(lambda i: i * (1 / 256.0)).convert("L")
    if im.mode == "CMYK":
        im = im.convert("RGB")
    if im.mode in ("RGBA", "LA", "PA", "P") or "transparency" in im.info:
        rgba = im.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return im.convert("RGB")


def load_still(src):
    """Upright RGB picture, shrunk if enormous. Raises MediaError."""
    try:
        im = _open_image(src)
        if im is None:
            raise MediaError("This file isn't a picture I can read.")
        im = ImageOps.exif_transpose(im)
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        return _to_rgb(im)
    except MediaError:
        raise
    except Exception as e:
        log.exception("Couldn't open picture %s", src)
        raise MediaError("Couldn't open this picture (%s)." % type(e).__name__, detail=applog.format_exc())


def _ffmpeg_info(src):
    """(width, height, duration_seconds) of the first video stream, via `ffmpeg -i`. Raises MediaError."""
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", src], capture_output=True, text=True,
                       errors="ignore", creationflags=NO_WINDOW, timeout=60)
    text = r.stderr
    m = re.search(r"Stream #\d+:\d+.*?Video:.*?,\s*(\d{2,5})x(\d{2,5})", text)
    if not m:
        log.warning("No readable picture/video in %s. ffmpeg said: %s", src, " | ".join(text.strip().splitlines()[-4:]))
        raise MediaError("This file has no picture or video I can read.", detail=text[-3000:])
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0
    w, h = int(m.group(1)), int(m.group(2))
    rot = re.search(r"rotat\w*(?: of)?\s*(-?\d+(?:\.\d+)?)", text)  # phone videos: sideways frames + a rotation tag
    if rot and int(round(abs(float(rot.group(1))) / 90)) % 2 == 1:
        w, h = h, w  # ffmpeg turns such videos upright while decoding, so the picture we edit is rotated
    return w, h, dur


def probe(src):
    """dict(kind='image'|'anim'|'video', w, h, duration). Anim = animated picture; video = anything ffmpeg plays."""
    im = _open_image(src)
    if im is None:
        log.debug("Pillow can't open %s; trying ffmpeg", os.path.basename(src))
    if im is not None:
        n = getattr(im, "n_frames", 1)
        if n <= 1:
            im = ImageOps.exif_transpose(im)
            return {"kind": "image", "w": im.width, "h": im.height, "duration": 0.0}
        if im.format == "GIF":  # ffmpeg reads GIFs well and keeps timing
            w, h = im.size
            try:
                _, _, dur = _ffmpeg_info(src)
            except MediaError:
                dur = 0.0
            return {"kind": "video", "w": w, "h": h, "duration": dur}
        total = 0
        for i in range(n):
            im.seek(i)
            total += im.info.get("duration", 100)
        return {"kind": "anim", "w": im.width, "h": im.height, "duration": total / 1000.0}
    w, h, dur = _ffmpeg_info(src)
    return {"kind": "video", "w": w, "h": h, "duration": dur}


def make_preview(src, dst_jpg, max_side=1280):
    """Save an upright JPEG preview of the first frame (even for videos). Returns probe() info."""
    info = probe(src)
    if info["kind"] == "video":
        png = dst_jpg + ".png"
        t = "0.5" if info["duration"] > 1 else "0"
        r = subprocess.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", t, "-i", src,
                            "-frames:v", "1", "-vf", "setsar=1", png],
                           capture_output=True, creationflags=NO_WINDOW, timeout=120)
        if r.returncode or not os.path.exists(png):  # clip shorter than the seek: take the very first frame
            subprocess.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-frames:v", "1",
                            "-vf", "setsar=1", png], capture_output=True, creationflags=NO_WINDOW, timeout=120)
        if not os.path.exists(png):
            raise MediaError("Couldn't read a picture from this video.")
        im = Image.open(png).convert("RGB")
        os.remove(png)
    else:
        im = load_still(src) if info["kind"] == "image" else _to_rgb(ImageOps.exif_transpose(_first_frame(src)))
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    im.save(dst_jpg, "JPEG", quality=88)
    return info


def _first_frame(src):
    im = Image.open(src)
    im.seek(0)
    return im.copy()


# ---------------------------------------------------------------------------------------- editing math
def _scale_for(sw, sh, spec):
    s0 = max(W / sw, H / sh) if spec["fit"] == "fill" else min(W / sw, H / sh)
    return s0 * spec["zoom"]


def _rotated_size(w, h, rot):
    return (h, w) if rot in (90, 270) else (w, h)


def _enhance(im, spec):
    if spec["bright"]:
        im = ImageEnhance.Brightness(im).enhance(1 + spec["bright"])
    if spec["contrast"]:
        im = ImageEnhance.Contrast(im).enhance(1 + spec["contrast"])
    if spec["sat"]:
        im = ImageEnhance.Color(im).enhance(1 + spec["sat"])
    return im


def compose_still(im, spec):
    """The 854x480 frame for a still picture."""
    if spec["rot"]:
        im = im.rotate(-spec["rot"], expand=True)  # Pillow rotates counter-clockwise
    if spec["flip"]:
        im = ImageOps.mirror(im)
    im = _enhance(im, spec)
    sw, sh = im.size
    s = _scale_for(sw, sh, spec)
    nw, nh = max(1, round(sw * s)), max(1, round(sh * s))
    if nw * nh > 60_000_000:  # absurd zoom on a huge picture: refuse rather than exhaust memory
        raise MediaError("That zoom is too large for this picture.")
    if spec["fit"] == "blur":
        k = max(W / sw, H / sh)
        bg = im.resize((max(W, round(sw * k)), max(H, round(sh * k))), Image.LANCZOS)
        left, top = (bg.width - W) // 2, (bg.height - H) // 2
        canvas = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(24))
        canvas = ImageEnhance.Brightness(canvas).enhance(0.7)
    else:
        canvas = Image.new("RGB", (W, H), (0, 0, 0))
    big = im.resize((nw, nh), Image.LANCZOS)
    x = round(W / 2 + spec["px"] * W - nw / 2)
    y = round(H / 2 + spec["py"] * H - nh / 2)
    canvas.paste(big, (x, y))
    return canvas


def _video_filter(w, h, spec):
    """ffmpeg -filter_complex for a moving picture of source size w x h."""
    rw, rh = _rotated_size(w, h, spec["rot"])
    s = _scale_for(rw, rh, spec)
    nw, nh = max(2, round(rw * s / 2) * 2), max(2, round(rh * s / 2) * 2)
    pre = ["setsar=1", "fps=%d" % FPS]
    pre += {0: [], 90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}[spec["rot"]]
    if spec["flip"]:
        pre.append("hflip")
    eq = []
    if spec["bright"]:
        eq.append("brightness=%.3f" % (spec["bright"] * 0.5))
    if spec["contrast"]:
        eq.append("contrast=%.3f" % (1 + spec["contrast"]))
    if spec["sat"]:
        eq.append("saturation=%.3f" % (1 + spec["sat"]))
    if eq:
        pre.append("eq=" + ":".join(eq))
    x = round(W / 2 + spec["px"] * W - nw / 2)
    y = round(H / 2 + spec["py"] * H - nh / 2)
    chain = ",".join(pre)
    if spec["fit"] == "blur":
        return ("[0:v]%s,split[a][b];"
                "[a]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,gblur=sigma=24,eq=brightness=-0.15[bg];"
                "[b]scale=%d:%d:flags=lanczos[fg];[bg][fg]overlay=x=%d:y=%d:shortest=1,format=yuv420p[v]"
                % (chain, W, H, W, H, nw, nh, x, y))
    return ("color=c=black:s=%dx%d:r=%d[bg];[0:v]%s,scale=%d:%d:flags=lanczos[fg];"
            "[bg][fg]overlay=x=%d:y=%d:shortest=1,format=yuv420p[v]" % (W, H, FPS, chain, nw, nh, x, y))


# ---------------------------------------------------------------------------------------- encoding
ENC = ["-an", "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "22",
       "-maxrate", "4M", "-bufsize", "8M", "-r", str(FPS), "-movflags", "+faststart"]


def _run_ffmpeg(args):
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"] + args
    log.debug("ffmpeg: %s", " ".join('"%s"' % a if " " in a else a for a in cmd[1:]))
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", creationflags=NO_WINDOW, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        log.error("ffmpeg timed out after %ds: %s", TIMEOUT, " ".join(cmd[1:]))
        raise MediaError("Converting took too long, so I gave up. Try a shorter or smaller file.",
                         detail="timeout after %ds\n%s" % (TIMEOUT, " ".join(cmd[1:])))
    if r.returncode:
        lines = [l.strip() for l in r.stderr.splitlines() if l.strip()]
        log.error("ffmpeg failed (exit %s) after %.1fs: %s\n%s", r.returncode, time.time() - t0, " ".join(cmd[1:]), r.stderr[-3000:])
        raise MediaError("Couldn't convert this file: " + (lines[-1][:80] if lines else "unknown error"),
                         detail="exit %s\n%s\n%s" % (r.returncode, " ".join(cmd[1:]), r.stderr[-4000:]))
    log.debug("ffmpeg ok in %.1fs", time.time() - t0)


def render(src, spec, out_mp4):
    """Convert `src` into the cooler's mp4 at `out_mp4`. Raises MediaError with a readable message."""
    spec = normalize_spec(spec)
    info = probe(src)
    t0 = time.time()
    log.info("render start: %s (%s) kind=%s %sx%s dur=%.1fs spec=%s", os.path.basename(src),
             "%.1f MB" % (os.path.getsize(src) / 1048576) if os.path.exists(src) else "?", info["kind"], info["w"], info["h"],
             info["duration"], spec)
    try:
        return _render(src, spec, info, out_mp4)
    finally:
        log.info("render finished in %.1fs -> %s", time.time() - t0,
                 "%d KB" % (os.path.getsize(out_mp4) / 1024) if os.path.exists(out_mp4) else "no output")


def _render(src, spec, info, out_mp4):
    tmp = tempfile.mkdtemp(prefix="rgbmedia_")
    try:
        if info["kind"] == "image":
            png = os.path.join(tmp, "frame.png")
            compose_still(load_still(src), spec).save(png)
            _run_ffmpeg(["-loop", "1", "-framerate", str(FPS), "-t", str(STILL_SECONDS), "-i", png,
                         "-vf", "format=yuv420p"] + ENC + [out_mp4])
            return info

        if info["kind"] == "anim":  # animated WebP/APNG/...: unpack to numbered PNGs ffmpeg can read
            im = Image.open(src)
            n = min(getattr(im, "n_frames", 1), 900)
            ms = []
            for i in range(n):
                im.seek(i)
                ms.append(max(20, im.info.get("duration", 100)))
                _to_rgb(ImageOps.exif_transpose(im.copy())).save(os.path.join(tmp, "f%05d.png" % i))
            rate = max(1.0, min(60.0, 1000.0 / (sum(ms) / len(ms))))
            inputs = ["-framerate", "%.3f" % rate, "-i", os.path.join(tmp, "f%05d.png")]
            w, h = im.size
            dur = n / rate
        else:
            inputs = []
            w, h, dur = info["w"], info["h"], info["duration"]
            if not w or not h:
                w, h, dur = _ffmpeg_info(src)

        loop = ["-stream_loop", "-1"] if 0 < dur < MIN_LOOP_SECONDS else []
        length = MIN_LOOP_SECONDS if 0 < dur < MIN_LOOP_SECONDS else min(dur or MAX_SECONDS, MAX_SECONDS)
        src_args = inputs if inputs else ["-i", src]
        _run_ffmpeg(loop + src_args + ["-t", "%.2f" % length, "-filter_complex", _video_filter(w, h, spec),
                                       "-map", "[v]"] + ENC + [out_mp4])
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
