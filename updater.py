# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Updates: is a newer release out on GitHub, and fetching its Setup. The app shows an Update button when one is.
Setup then installs it the way it does a first install: it stops the app and the lighting service, asks Windows for
permission once and starts the app again at the end. Settings stay in the user's folder, so nothing is lost."""
import glob
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request

import applog

REPO = "EQQX0/rgbanywhere"
API = "https://api.github.com/repos/%s/releases/latest" % REPO
PAGES = "https://github.com/%s/releases/" % REPO  # the only pages "What's new" opens
SETUP = "RGB-Anywhere-Setup.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "rgbanywhere-update-"  # the %TEMP% folders a download goes to
log = applog.get("update")


def current(here=HERE):
    """The installed version (version.txt, written by package.ps1), or None for a source checkout."""
    try:
        with open(os.path.join(here, "version.txt"), encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


def key(v):
    """'v0.10.1' -> (0, 10, 1), so versions compare as numbers, not text."""
    return tuple(int(n) for n in re.findall(r"\d+", v or ""))


def latest(timeout=15):
    """The newest release: its version and page, and Setup's URL, size and SHA-256 as GitHub publishes them."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "RGBAnywhere"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.load(r)
    asset = next((a for a in rel.get("assets", []) if a.get("name") == SETUP), {})
    digest = asset.get("digest") or ""
    return {"version": rel["tag_name"].lstrip("v"), "page": rel.get("html_url", ""),
            "url": asset.get("browser_download_url"), "size": asset.get("size") or 0,
            "sha256": digest[len("sha256:"):].lower() if digest.startswith("sha256:") else None}


def check(here=HERE):
    """Whether a newer release than this install is out. A source checkout has no version and is updated with git."""
    have = current(here)
    if have is None:
        return {"current": None, "available": False, "source": True}
    rel = latest()
    newer = key(rel["version"]) > key(have)
    if newer and not (rel["url"] and rel["sha256"]):
        log.warning("release %s has no %s with a published checksum; not offering it", rel["version"], SETUP)
        newer = False
    return {"current": have, "latest": rel["version"], "available": newer, "release": rel}


def cleanup():
    """Remove earlier downloads. One whose Setup is still running is locked and stays until the next time."""
    for d in glob.glob(os.path.join(tempfile.gettempdir(), PREFIX + "*")):
        shutil.rmtree(d, ignore_errors=True)


def download(rel, progress=None, timeout=30):
    """Fetch Setup into a new folder in %TEMP% and check it against the published SHA-256. Returns its path.
    progress(percent) is called each time the whole percent changes."""
    if not rel.get("url") or not rel.get("sha256"):
        raise ValueError("the release has no %s with a published checksum" % SETUP)
    cleanup()
    path = os.path.join(tempfile.mkdtemp(prefix=PREFIX), SETUP)
    h, done, shown = hashlib.sha256(), 0, -1
    req = urllib.request.Request(rel["url"], headers={"User-Agent": "RGBAnywhere"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or rel.get("size") or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            pct = min(99, done * 100 // total) if total else 0
            if progress and pct != shown:
                shown = pct
                progress(pct)
    if h.hexdigest() != rel["sha256"]:
        os.remove(path)
        raise ValueError("the download did not match its published checksum")
    return path
