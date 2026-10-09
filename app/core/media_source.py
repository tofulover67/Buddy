#!/usr/bin/env python3
"""
Opening a media file in a Qt player, so that every file Buddy can read
plays - including the ones Qt's own FFmpeg can't open as they are.

open_source(player, path) is setSource(QUrl.fromLocalFile(path)) - except
for a file with something in it that Qt's FFmpeg stops at (core/mov_atoms.py:
Sony's 'chnl' box), which is handed to the player as a QIODevice that reads
the file with those bytes replaced (PatchedFile). The player reads through
Python then - ~120 reads a second of 32 KB at a 4K camera clip's bit rate,
nothing against the decoding - and seeks as it would in the file. The
device is the player's child: gone, file closed, with the player.

Works for QMediaPlayer and QAudioDecoder alike (both have setSourceDevice).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QIODevice, QUrl

from core import mov_atoms


class PatchedFile(QIODevice):
    """A file, read-only, with a few four-byte spots read as 'free'."""

    def __init__(self, path, spots, parent=None):
        super().__init__(parent)
        self._path = path
        self._file = open(path, "rb", buffering=0)
        self._size = os.path.getsize(path)
        self._spots = list(spots)

    def isSequential(self):
        return False

    def size(self):
        return self._size

    def bytesAvailable(self):
        return max(0, self._size - self.pos()) + super().bytesAvailable()

    def seek(self, pos):
        if not super().seek(pos):
            return False
        try:
            self._file.seek(pos)
        except (OSError, ValueError):
            return False
        return True

    def readData(self, maxlen):
        try:
            start = self._file.tell()
            data = self._file.read(maxlen)
        except (OSError, ValueError):
            return b""
        return mov_atoms.patched(data, start, self._spots)

    def writeData(self, _data, _length=0):
        return -1

    def close(self):
        super().close()
        try:
            self._file.close()
        except OSError:
            pass

    def __del__(self):
        try:
            self._file.close()
        except Exception:  # noqa: BLE001 - going away anyway
            pass


def open_source(player, path) -> bool:
    """Gives the player the file to play. True when it had to be read
    through PatchedFile (for the crash trail, a tooltip)."""
    path = str(path or "")
    url = QUrl.fromLocalFile(path)
    spots = mov_atoms.unreadable_spots(path) if path else []
    if not spots:
        player.setSource(url)
        return False
    try:
        device = PatchedFile(path, spots, player)
    except OSError:
        player.setSource(url)            # the player reports what's wrong with it
        return False
    device.open(QIODevice.ReadOnly)
    player.setSourceDevice(device, url)
    return True
