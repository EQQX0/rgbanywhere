# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Read and change the DeepCool app's settings database (%APPDATA%\\DeepCool\\db, a LevelDB).

Only what the app needs is here: read the newest value of a key, and write a new value by appending one record to
the database's write-ahead log, which LevelDB replays the next time DeepCool opens it. DeepCool must be closed while
writing (it locks the folder), and a copy of the folder is made before the first write.
"""
import glob
import json
import os
import shutil
import struct
import time

import applog

log = applog.get("deepcool_db")

DB = os.path.join(os.environ.get("APPDATA", ""), "DeepCool", "db")
BLOCK = 32768
HEADER = 7
FULL, FIRST, MIDDLE, LAST = 1, 2, 3, 4


# ---------------------------------------------------------------------------------------- low-level format
def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 127) << s
        s += 7
        if c < 128:
            return r, i


def _put_varint(n):
    out = bytearray()
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)


def _snappy(b):
    n, i = _varint(b, 0)
    o = bytearray()
    while i < len(b):
        t = b[i]
        i += 1
        k = t & 3
        if k == 0:
            n_ = t >> 2
            if n_ >= 60:
                nb = n_ - 59
                n_ = int.from_bytes(b[i:i + nb], "little")
                i += nb
            n_ += 1
            o += b[i:i + n_]
            i += n_
            continue
        if k == 1:
            n_ = ((t >> 2) & 7) + 4
            off = ((t >> 5) << 8) | b[i]
            i += 1
        elif k == 2:
            n_ = (t >> 2) + 1
            off = int.from_bytes(b[i:i + 2], "little")
            i += 2
        else:
            n_ = (t >> 2) + 1
            off = int.from_bytes(b[i:i + 4], "little")
            i += 4
        for _ in range(n_):
            o.append(o[-off])
    return bytes(o)


def _crc_table():
    t = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        t.append(c)
    return t


_CRC = _crc_table()


def _crc32c(data):
    c = 0xFFFFFFFF
    for x in data:
        c = _CRC[(c ^ x) & 255] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


def _masked_crc(data):
    c = _crc32c(data)
    return ((((c >> 15) | (c << 17)) & 0xFFFFFFFF) + 0xA282EAD8) & 0xFFFFFFFF


def _block(d, off, size):
    raw = d[off:off + size]
    return _snappy(raw) if d[off + size] == 1 else raw


def _block_entries(blk):
    nr = struct.unpack("<I", blk[-4:])[0]
    end = len(blk) - 4 - 4 * nr
    i, key = 0, b""
    while i < end:
        shared, i = _varint(blk, i)
        unshared, i = _varint(blk, i)
        vlen, i = _varint(blk, i)
        key = key[:shared] + blk[i:i + unshared]
        i += unshared
        yield key, blk[i:i + vlen]
        i += vlen


def _log_records(d):
    """Whole records of a LevelDB log (a write-ahead log or a MANIFEST)."""
    pos, frag = 0, b""
    while pos + HEADER <= len(d):
        left = BLOCK - pos % BLOCK
        if left < HEADER:
            pos += left
            continue
        length, kind = struct.unpack("<HB", d[pos + 4:pos + 7])
        if kind == 0 and length == 0:
            pos += left
            continue
        data = d[pos + HEADER:pos + HEADER + length]
        pos += HEADER + length
        if kind == FULL:
            yield data
        elif kind == FIRST:
            frag = data
        elif kind == MIDDLE:
            frag += data
        elif kind == LAST:
            yield frag + data
            frag = b""


# ---------------------------------------------------------------------------------------- reading
def _tables(folder):
    """{user key: (sequence, deleted, value)} from the .ldb tables."""
    out = {}
    for f in glob.glob(os.path.join(folder, "*.ldb")) + glob.glob(os.path.join(folder, "*.sst")):
        with open(f, "rb") as fh:
            d = fh.read()
        ft = d[-48:]
        j = 0
        _, j = _varint(ft, j)
        _, j = _varint(ft, j)
        idx_off, j = _varint(ft, j)
        idx_size, j = _varint(ft, j)
        for _, handle in _block_entries(_block(d, idx_off, idx_size)):
            off, x = _varint(handle, 0)
            size, _ = _varint(handle, x)
            for ik, v in _block_entries(_block(d, off, size)):
                tag = struct.unpack("<Q", ik[-8:])[0]
                seq, kind = tag >> 8, tag & 255
                _keep(out, ik[:-8], seq, kind == 0, v)
    return out


def _keep(out, key, seq, deleted, value):
    if key not in out or out[key][0] < seq:
        out[key] = (seq, deleted, value)


def _batches(folder):
    """Every (key, sequence, deleted, value) written in the .log files, plus the highest sequence they reach."""
    for f in sorted(glob.glob(os.path.join(folder, "*.log"))):
        if os.path.basename(f) == "LOG":
            continue
        with open(f, "rb") as fh:
            d = fh.read()
        for rec in _log_records(d):
            if len(rec) < 12:
                continue
            seq, count = struct.unpack("<QI", rec[:12])
            i = 12
            for n in range(count):
                kind = rec[i]
                i += 1
                klen, i = _varint(rec, i)
                key = rec[i:i + klen]
                i += klen
                value = b""
                if kind == 1:
                    vlen, i = _varint(rec, i)
                    value = rec[i:i + vlen]
                    i += vlen
                yield key, seq + n, kind == 0, value


def _manifest_last_sequence(folder):
    try:
        with open(os.path.join(folder, "CURRENT"), encoding="ascii") as f:
            name = f.read().strip()
        with open(os.path.join(folder, name), "rb") as f:
            d = f.read()
    except OSError:
        return 0
    last = 0
    for rec in _log_records(d):
        i = 0
        try:
            while i < len(rec):
                tag, i = _varint(rec, i)
                if tag == 1:
                    n, i = _varint(rec, i)
                    i += n
                elif tag in (2, 3, 9):
                    _, i = _varint(rec, i)
                elif tag == 4:
                    last, i = _varint(rec, i)
                elif tag == 5:
                    _, i = _varint(rec, i)
                    n, i = _varint(rec, i)
                    i += n
                elif tag == 6:
                    _, i = _varint(rec, i)
                    _, i = _varint(rec, i)
                elif tag == 7:
                    for _ in range(4):
                        _, i = _varint(rec, i)
                    for _ in range(2):
                        n, i = _varint(rec, i)
                        i += n
                else:
                    break
        except IndexError:
            break
    return last


def read_all(folder=None):
    """{key (str): value (str)} of the newest live entries."""
    folder = folder or DB
    state = _tables(folder)
    for key, seq, deleted, value in _batches(folder):
        _keep(state, key, seq, deleted, value)
    return {k.decode("utf-8", "replace"): v.decode("utf-8", "replace") for k, (_, dead, v) in state.items() if not dead}


def read_json(key, folder=None):
    raw = read_all(folder).get(key)
    return None if raw is None else json.loads(raw)


# ---------------------------------------------------------------------------------------- writing
def backup(folder=None):
    """Copy the database folder beside itself (once per run). Returns the copy's path."""
    folder = folder or DB
    dst = folder + "_backup_" + time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(folder):
        if name != "LOCK":
            shutil.copy2(os.path.join(folder, name), dst)
    log.info("backed up %s to %s", folder, dst)
    return dst


def _newest_log(folder):
    logs = [f for f in glob.glob(os.path.join(folder, "*.log")) if os.path.basename(f) != "LOG"]
    return max(logs, key=lambda f: int(os.path.splitext(os.path.basename(f))[0])) if logs else None


def _append_record(path, payload):
    with open(path, "ab") as f:
        pos = f.tell()
        first = True
        while True:
            left = BLOCK - pos % BLOCK
            if left < HEADER:
                f.write(b"\0" * left)
                pos += left
                left = BLOCK
            room = left - HEADER
            piece, payload = payload[:room], payload[room:]
            last = not payload
            kind = (FULL if last else FIRST) if first else (LAST if last else MIDDLE)
            body = bytes([kind]) + piece
            f.write(struct.pack("<IH", _masked_crc(body), len(piece)) + body)
            pos += HEADER + len(piece)
            first = False
            if last:
                return


def put(key, value, folder=None):
    """Write `value` (str) under `key` (str). DeepCool must not be running."""
    folder = folder or DB
    path = _newest_log(folder)
    if path is None:
        raise RuntimeError("DeepCool's database has no log file to add to")
    top = _manifest_last_sequence(folder)
    for seq, _, _ in _tables(folder).values():
        top = max(top, seq)
    for _, seq, _, _ in _batches(folder):
        top = max(top, seq)
    kb, vb = key.encode("utf-8"), value.encode("utf-8")
    batch = struct.pack("<QI", top + 1, 1) + b"\x01" + _put_varint(len(kb)) + kb + _put_varint(len(vb)) + vb
    _append_record(path, batch)
    log.info("wrote %s (%d bytes) at sequence %d", key, len(vb), top + 1)


def set_json_field(key, path_, value, folder=None):
    """Set one nested field (path_ is a list of names) of the JSON stored under `key`, keeping the rest."""
    doc = read_json(key, folder)
    if doc is None:
        raise KeyError(key)
    node = doc
    for name in path_[:-1]:
        node = node[name]
    node[path_[-1]] = value
    put(key, json.dumps(doc, separators=(",", ":"), ensure_ascii=False), folder)
