#!/usr/bin/env python3
"""
Pure-Python file-matching logic for Media Relink - deliberately free of any
PySide6 or DaVinci Resolve import so it can be exercised with plain unit
tests.

Resolve does have a built-in MediaPool.RelinkClips(items, folder) call that
searches a folder for matching media on Resolve's own terms, but its
matching/recursion behavior isn't documented precisely enough to build a
predictable "here's exactly what will happen" preview around - callers
would have to trust it and check afterward. Building the filename index
ourselves means every proposed match can be shown to the user (and
overridden) before anything is actually relinked.
"""

import os
import re

# Case-insensitive filename comparison everywhere below - Resolve projects
# routinely move between Windows and macOS/network-share workflows where a
# clip's recorded path and the file's real path may disagree only in case.


# Resolve records an image sequence as one path with its frame range in
# brackets: "R[5207310-5207311].jpg" is R5207310.jpg and R5207311.jpg on
# disk. No file has the bracketed name, so a sequence is checked - and
# found in a search - by its first frame.
_SEQUENCE = re.compile(r"^(.*)\[(\d+)-(\d+)\]([^\[\]]*)$")


def sequence_first_frame(path):
    """The first frame's own path for an image sequence's recorded path,
    or None when the path isn't a sequence's."""
    folder, name = os.path.split(path or "")
    match = _SEQUENCE.match(name)
    if not match:
        return None
    return os.path.join(folder, match.group(1) + match.group(2) + match.group(4))


def media_exists(path):
    """Whether a clip's recorded path is on disk - a sequence by its first
    frame (a file really named with brackets counts as itself too)."""
    if os.path.exists(path):
        return True
    first = sequence_first_frame(path)
    return bool(first) and os.path.exists(first)


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class SearchCancelled(Exception):
    """build_file_index was asked to stop."""


# Never searched, wherever they are: the trash, and the file system's own
# bookkeeping. Windows won't let anyone open most of these, so a search of
# a whole drive used to stop at the first one.
_SKIP_ANYWHERE = {name.casefold() for name in (
    "$RECYCLE.BIN", "System Volume Information",
    ".Trashes", ".Trash", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100",
    ".TemporaryItems", ".MobileBackups",
)}

# Only at the top of a Windows drive: the operating system itself. Choosing
# C:\ means the files people keep on it, not Windows' own.
_SKIP_AT_DRIVE_ROOT = {name.casefold() for name in (
    "Windows", "Program Files", "Program Files (x86)", "ProgramData", "$WinREAgent",
    "Config.Msi", "Recovery", "PerfLogs",
)}

# Only at the top of a Mac's own disk ("/"): macOS itself, and Volumes -
# the other drives, which choosing the Mac's disk doesn't search, the same
# as choosing C:\ doesn't search D:\.
_SKIP_AT_MAC_ROOT = {name.casefold() for name in (
    "System", "Library", "private", "dev", "cores", "bin", "sbin", "usr", "etc", "var", "tmp",
    "opt", "Applications", "Volumes",
)}

_DRIVE_ROOT = re.compile(r"^[A-Za-z]:[\\/]*$")

# IO_REPARSE_TAG_MOUNT_POINT: a Windows directory junction (or a drive
# mounted in a folder).
_JUNCTION = 0xA0000003


def skip_folder(search_root, folder):
    """Whether a search of search_root leaves out the folder at `folder`,
    by its name: trash and system folders anywhere, and the operating
    system's own folders when search_root is a whole Windows drive (D:\\)
    or a Mac's own disk (/). Told apart by the paths' shape alone, so it
    reads the same on either platform."""
    folder = folder.rstrip("\\/")
    name = re.split(r"[\\/]", folder)[-1]
    if name.casefold() in _SKIP_ANYWHERE:
        return True
    if _DRIVE_ROOT.match(search_root):
        at_top = _SKIP_AT_DRIVE_ROOT
    elif search_root == "/":
        at_top = _SKIP_AT_MAC_ROOT
    else:
        return False
    parent = folder[:len(folder) - len(name)].rstrip("\\/")
    return name.casefold() in at_top and parent.casefold() == search_root.rstrip("\\/").casefold()


def _is_link(entry):
    """A symbolic link or a Windows directory junction: a way into a folder
    that's somewhere else - or above, a loop - so never walked into. Other
    reparse points are real folders (OneDrive's are) and are walked.
    os.DirEntry.is_junction() is Python 3.12's; before it, the junction's
    tag is read off the entry (st_reparse_tag is Windows-only)."""
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    if is_junction is not None:
        return is_junction()
    return getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0) == _JUNCTION


def wanted_names(paths):
    """The lowercased filenames find_candidates looks up for these
    recorded paths - an image sequence's first frame too - for
    build_file_index's wanted."""
    names = set()
    for path in paths:
        if not path:
            continue
        names.add(os.path.basename(path).lower())
        first = sequence_first_frame(path)
        if first:
            names.add(os.path.basename(first).lower())
    return names


def build_file_index(search_root, progress=None, should_stop=None, wanted=None, unreadable=None):
    """Recursively walks search_root and returns a dict mapping a
    lowercased filename to a list of every full path found with that name
    (a list, not a single path, because the same filename can legitimately
    exist more than once under a search folder - e.g. per-camera-card
    exports that reuse the same numbering).

    wanted, a set of lowercased filenames (wanted_names), keeps only those
    in the index - a whole drive can hold millions of files. None keeps
    every one.

    A folder below search_root that can't be opened (no permission, or
    gone mid-search) is left out, and its path added to the unreadable
    list when one is given. search_root itself missing or unreadable is
    still an error. Trash and system folders aren't searched (skip_folder),
    and links and junctions aren't followed, so no folder is walked twice.

    progress(files_seen) is called after each folder, counting every file
    looked at; should_stop() is checked there too, raising SearchCancelled
    when it returns True - a walk over a big network share can take
    minutes.

    Folders and files are walked in name order, so candidates always list
    the same way: the file system's own order is alphabetical on NTFS, but
    not on macOS's APFS."""
    if not os.path.isdir(search_root):
        raise NotADirectoryError(search_root)

    index = {}
    seen = 0
    folders = [search_root]
    while folders:
        folder = folders.pop()
        try:
            with os.scandir(folder) as scan:
                entries = sorted(scan, key=lambda entry: entry.name.casefold())
        except OSError:
            if folder == search_root:
                raise
            if unreadable is not None:
                unreadable.append(folder)
            entries = []
        subfolders = []
        for entry in entries:
            try:
                is_folder = entry.is_dir()
                if is_folder and (_is_link(entry) or skip_folder(search_root, entry.path)):
                    continue
            except OSError:   # gone since the folder was listed
                continue
            if is_folder:
                subfolders.append(entry.path)
                continue
            seen += 1
            key = entry.name.lower()
            if wanted is None or key in wanted:
                index.setdefault(key, []).append(entry.path)
        # Depth first, each folder's own files before its subfolders' -
        # the first subfolder in name order next.
        folders.extend(reversed(subfolders))
        if should_stop is not None and should_stop():
            raise SearchCancelled()
        if progress is not None:
            progress(seen)
    return index


def find_candidates(old_path, file_index):
    """Given a clip's old (offline) recorded path, returns the list of
    candidate replacement paths found in file_index by exact (case
    -insensitive) filename match. Empty list means no match found. A
    candidate list of length 1 is an unambiguous match; length > 1 means
    the caller should surface a choice rather than silently picking one.

    An image sequence is found by its first frame, and proposed as the
    same bracketed name in the folder that frame is in."""
    if not old_path:
        return []
    name = os.path.basename(old_path)
    candidates = list(file_index.get(name.lower(), []))
    first = sequence_first_frame(old_path)
    if first:
        for frame in file_index.get(os.path.basename(first).lower(), []):
            path = os.path.join(os.path.dirname(frame), name)
            if not any(same_path(path, c) for c in candidates):
                candidates.append(path)
    return candidates


class MatchStatus:
    ONLINE = "online"
    MATCH_FOUND = "match_found"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


def classify_match(is_online, candidates):
    """Turns an online/offline flag plus a candidate list into one of the
    MatchStatus values used to drive both the tree's status column and
    which rows a "Relink All Matches" pass should touch."""
    if is_online:
        return MatchStatus.ONLINE
    if len(candidates) == 1:
        return MatchStatus.MATCH_FOUND
    if len(candidates) > 1:
        return MatchStatus.AMBIGUOUS
    return MatchStatus.NOT_FOUND
