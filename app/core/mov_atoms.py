#!/usr/bin/env python3
"""
What in an MP4 or MOV file Qt's FFmpeg can't read past - and where.

Sony's cameras (FX3, FX30, a7 IV... firmware from 2023 on) record XAVC S
with 24-bit PCM sound as ISO/IEC 23003-5 PCM: an 'ipcm' sample entry
carrying a 'chnl' channel-layout box. The FFmpeg inside Qt 6.11 (libavformat
61, FFmpeg 7.1) reads the file's moov up to that 'chnl' and stops with
"error reading header", so QMediaPlayer says "Could not open file" - for
every clip the camera shot (2026-10-09, 24 of 24 in a folder; the FFmpeg 9
on the user's PATH reads them all). Nothing in the file is wrong: FFmpeg
7.1's reader of that box is.

A file read with those four bytes as 'free' (an atom every reader skips)
plays whole, sound included: FFmpeg takes the PCM's channel count from the
sample entry itself. core/media_source.py reads a file that way for the
players; this module finds the spots - reading only the file's atom
headers and its moov, a few hundred KB at the end of a 14 GB camera file.

No Qt. Any file that isn't MP4/MOV, or has nothing of the kind, gives [].
"""

from __future__ import annotations

import os
import struct

EXTENSIONS = {".mp4", ".mov", ".m4v", ".m4a", ".3gp"}
# Containers walked into, the sample-description table (a full box: version
# and flags, then an entry count), and audio sample entries, whose own boxes
# come after 28 bytes of fixed fields.
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
AUDIO_ENTRIES = {b"ipcm", b"fpcm", b"lpcm", b"mp4a", b"sowt", b"twos", b"in24", b"in32"}
AUDIO_ENTRY_FIELDS = 28
BAD = b"chnl"
MOOV_MAX = 64 * 1024 * 1024
_cache: dict[tuple, list[int]] = {}


def _top_level(handle, size):
    """(offset, size, type) of each top-level atom, by seeking."""
    pos = 0
    while pos + 8 <= size:
        handle.seek(pos)
        head = handle.read(16)
        if len(head) < 8:
            return
        atom_size, kind = struct.unpack(">I4s", head[:8])
        if atom_size == 1 and len(head) >= 16:
            atom_size = struct.unpack(">Q", head[8:16])[0]
        elif atom_size == 0:
            atom_size = size - pos
        if atom_size < 8:
            return
        yield pos, atom_size, kind
        pos += atom_size


def _walk(data, base, start, end, depth=0):
    """(absolute offset, size, type) of each atom under data[start:end]."""
    pos = start
    while pos + 8 <= end:
        atom_size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if atom_size == 1:
            if pos + 16 > end:
                return
            atom_size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif atom_size == 0:
            atom_size = end - pos
        if atom_size < header:
            return
        yield base + pos, atom_size, kind
        inner = min(pos + atom_size, end)
        if kind in CONTAINERS:
            yield from _walk(data, base, pos + header, inner, depth + 1)
        elif kind == b"stsd":
            yield from _walk(data, base, pos + header + 8, inner, depth + 1)
        elif kind in AUDIO_ENTRIES and depth:
            yield from _walk(data, base, pos + header + AUDIO_ENTRY_FIELDS, inner, depth + 1)
        pos += atom_size


def unreadable_spots(path) -> list[int]:
    """The offsets of the type fields Qt's FFmpeg stops at (the 'chnl' box
    of an audio sample entry) - [] for a file it reads whole. Remembered
    per file (path, size, modified time)."""
    if os.path.splitext(str(path))[1].lower() not in EXTENSIONS:
        return []
    try:
        stat = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns)
        found = _cache.get(key)
        if found is not None:
            return list(found)
        spots = []
        with open(path, "rb") as handle:
            for offset, atom_size, kind in _top_level(handle, stat.st_size):
                if kind != b"moov" or atom_size > MOOV_MAX:
                    continue
                handle.seek(offset)
                data = handle.read(atom_size)
                spots.extend(at + 4 for at, _size, name in _walk(data, offset, 8, len(data), 1) if name == BAD)
        if len(_cache) > 512:
            _cache.clear()
        _cache[key] = spots
        return list(spots)
    except (OSError, struct.error):
        return []


def patched(data: bytes, start: int, spots) -> bytes:
    """data, as read from `start`, with each spot's four bytes read as 'free'
    - whichever of them this read covers."""
    end = start + len(data)
    for spot in spots:
        low, high = max(spot, start), min(spot + 4, end)
        if low < high:
            data = data[:low - start] + b"free"[low - spot:high - spot] + data[high - start:]
    return data
