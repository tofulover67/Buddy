"""Designs kept by name, to open again and use again: one file each in the tool's own
folder, ~/.buddy/tools/design/designs/<id>.json (core/tool_dirs.py), written whole
(core/atomic_io.py). An image layer keeps where its file is, not the picture. No Qt.
"""

import os
import re
import time
import uuid

from core import atomic_io, tool_dirs

from . import model

MAX_DESIGNS = 500
ID = re.compile(r"d-[0-9a-f]{10}\Z")


def folder():
    return os.path.join(tool_dirs.path("design"), "designs")


def _file(design_id):
    if not ID.match(str(design_id or "")):
        return None
    return os.path.join(folder(), f"{design_id}.json")


def _read(design_id):
    path = _file(design_id)
    if path is None:
        return None
    try:
        return atomic_io.read_json(path, default=None, expected_type=dict)
    except (OSError, atomic_io.CorruptFileError):
        return None


def listed():
    """[{"id", "name", "layers", "saved"}] of the designs kept, the latest first."""
    try:
        names = os.listdir(folder())
    except OSError:
        return []
    out = []
    for name in names:
        design_id, ext = os.path.splitext(name)
        kept = _read(design_id) if ext == ".json" else None
        if kept is None:
            continue
        design = model.clean(kept.get("design"))
        out.append({"id": design_id, "name": design["name"], "layers": model.count(design),
                    "saved": float(kept.get("saved") or 0.0)})
    return sorted(out, key=lambda d: -d["saved"])[:MAX_DESIGNS]


def load(design_id):
    """A kept design, clean - or None when it isn't there or can't be read."""
    kept = _read(design_id)
    return None if kept is None else model.clean(kept.get("design"))


def save(design, design_id=None):
    """The design kept: over the one with that id, or as a new one. Returns its id;
    raises OSError when it can't be written."""
    if _file(design_id) is None:
        design_id = "d-" + uuid.uuid4().hex[:10]
    atomic_io.write_json(_file(design_id), {"design": model.clean(design), "saved": time.time()}, indent=None)
    return design_id


def remove(design_id):
    """A kept design gone (and the copy atomic_io keeps of it). True when it was there."""
    path = _file(design_id)
    if path is None or not os.path.isfile(path):
        return False
    os.remove(path)
    try:
        os.remove(atomic_io.backup_path(path))
    except OSError:
        pass
    return True
