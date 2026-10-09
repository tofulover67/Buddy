"""Crash-safe, local storage for Ask Buddy conversations.

Pictures sent with a question are kept beside them, in pictures/, at the
size they open at (pictures.VIEW_SIDE) - the conversations hold only each
one's file name and a small preview. A file no conversation names any
more goes when a conversation is deleted."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from core.atomic_io import backup_path, read_json, write_json

from .conversation import ChatSessions


FILE_NAME = "conversations.json"
PICTURES_DIR = "pictures"
PICTURE_NAME = re.compile(r"^[0-9a-f]{20}\.jpg$")


def load(folder):
    path = Path(folder) / FILE_NAME
    warnings = []
    data = read_json(str(path), default=None, warnings=warnings)
    return (ChatSessions.from_data(data) if data is not None else ChatSessions(), warnings)


def save(folder, chats: ChatSessions, forget_backup=False):
    """forget_backup after a delete: write_json keeps the previous save as
    the .bak, and a deleted conversation must not live on in there."""
    path = str(Path(folder) / FILE_NAME)
    write_json(path, chats.to_data())
    if forget_backup:
        _remove(backup_path(path))
        prune_pictures(folder, chats)


def picture_name(data: bytes) -> str:
    """A sent picture's file name - from its content, so sending one twice keeps one file."""
    return hashlib.sha1(data).hexdigest()[:20] + ".jpg"


def save_picture(folder, data: bytes) -> str:
    """A sent picture's file, for picture_path."""
    name = picture_name(data)
    path = Path(folder) / PICTURES_DIR / name
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_bytes(data)
        os.replace(part, path)
    return name


def picture_path(folder, name) -> Path | None:
    if not isinstance(name, str) or not PICTURE_NAME.match(name):
        return None
    path = Path(folder) / PICTURES_DIR / name
    return path if path.is_file() else None


def prune_pictures(folder, chats: ChatSessions):
    """Pictures only deleted conversations named."""
    named = {name for chat in chats.chats for block in chat.blocks for name in block.get("views") or []}
    directory = Path(folder) / PICTURES_DIR
    if directory.is_dir():
        for entry in directory.iterdir():
            if entry.name not in named:
                _remove(str(entry))


def erase(folder):
    """Delete all: the file, its .bak, and any damaged copies read_json set
    aside (.corrupt-N) - those hold conversations too."""
    folder = Path(folder)
    if not folder.is_dir():
        return
    for entry in folder.iterdir():
        name = entry.name
        if name == FILE_NAME or name.startswith(FILE_NAME + ".bak") or name.startswith(FILE_NAME + ".corrupt-"):
            _remove(str(entry))
    pictures = folder / PICTURES_DIR
    if pictures.is_dir():
        for entry in pictures.iterdir():
            _remove(str(entry))
        try:
            pictures.rmdir()
        except OSError:
            pass


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
