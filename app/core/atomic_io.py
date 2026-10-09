"""Crash-safe JSON files for the pages' data (time entries, palettes, assets).

write_json() writes to a temp file beside the target, flushes it to disk,
then swaps it in with os.replace(), so a crash or power cut mid-save leaves
either the old file or the new one - never a truncated one. The previous
version is kept as "<name>.bak".

read_json() tells "no file yet" (returns the default) apart from "file is
there but unreadable" (raises CorruptFileError), so callers never mistake a
damaged file for an empty library and save an empty one over it. Before
raising, it tries the .bak copy and returns that if it reads cleanly - the
damaged file is then set aside (so the next save can't copy it over the good
.bak) and, given a `warnings` list, a line is added to it for the user.
"""

import json
import os
import shutil
import tempfile


class CorruptFileError(Exception):
    """The file exists but isn't valid JSON (and neither is its .bak)."""


def backup_path(path):
    return path + ".bak"


def write_json(path, data, indent=2):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        # Only a file that still parses is worth keeping as the backup - never
        # replace a good .bak with a damaged copy.
        if os.path.exists(path):
            try:
                _load(path)
                shutil.copy2(path, backup_path(path))
            except (OSError, ValueError):
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _load(path, expected_type=None):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if expected_type is not None and not isinstance(data, expected_type):
        raise ValueError("JSON has an unexpected root type")
    return data


def read_json(path, default=None, warnings=None, expected_type=None):
    """The parsed file, `default` if neither copy exists, the .bak copy if
    the primary is missing or damaged, else CorruptFileError. expected_type
    also treats a valid JSON document with the wrong root type as damaged."""
    # Recovery sets a damaged primary aside. Until the caller saves again,
    # its backup is the only usable copy, including on the next app launch.
    bak = backup_path(path)
    if not os.path.exists(path) and not os.path.exists(bak):
        return default
    try:
        return _load(path, expected_type)
    except (OSError, ValueError) as err:
        if os.path.exists(bak):
            try:
                data = _load(bak, expected_type)
            except (OSError, ValueError):
                pass
            else:
                name = os.path.basename(path)
                try:
                    moved = set_aside(path)
                    kept = f" The damaged file has been kept as {os.path.basename(moved)}." if moved else ""
                except OSError:
                    kept = ""
                if warnings is not None:
                    warnings.append(
                        f"{name} was missing or damaged, so the backup copy from the save before "
                        f"last was loaded instead – the most recent change may be missing.{kept}"
                    )
                return data
        raise CorruptFileError(f"{path} could not be read: {err}") from err


def set_aside(path):
    """Renames a damaged file to "<name>.corrupt-N" so a fresh save can't
    overwrite it and the user (or support) can still recover it. Returns the
    new path, or None if there was nothing to move."""
    if not os.path.exists(path):
        return None
    n = 1
    while os.path.exists(f"{path}.corrupt-{n}"):
        n += 1
    target = f"{path}.corrupt-{n}"
    os.replace(path, target)
    return target
