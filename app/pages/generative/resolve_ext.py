#!/usr/bin/env python3
"""
Clean Up's Resolve calls, made in the Resolve reader's process
(core/resolve_reader_child.py: "generative.find", "generative.swap",
"generative.add") - or on a worker thread when there's none.

find   the picture or footage to work on: the clip under the playhead on
       the open timeline - else the one selected in the Media Pool. For
       footage under the playhead, also the part of the file the timeline
       uses and the file's frame at the playhead (GetSourceStartFrame is in
       the media's own frames, from its first - see Audio Assistant's
       resolve_ext). With selected=True: the clip selected on the timeline
       instead - one dragged from the timeline onto the page (the drag says
       nothing of which clip, but pressing on a clip to drag it selects it).
       Reads only.
swap   points that Media Pool clip at the cleaned file (ReplaceClip): every
       use of it in the project shows the cleaned picture. The original
       file stays where it was.
add    imports the cleaned file (or a footage job's video file) into the
       same bin as the original - and leaves that bin showing, the new
       clip selected.
add_sequence   imports an image sequence a footage job made (name_%06d.png,
       first .. last) into that bin, as one clip, the same way.

A clip is found again by its Media Pool id, so a swap goes to the clip
that was opened even if the playhead or selection moved since.
"""

import os

from core.resolve_bridge import ResolveConnectionError
from pages.stills_exporter.resolve_ext import timecode_to_frames

STILL_EXTS = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".bmp", ".dib", ".webp", ".exr", ".jp2",
              ".hdr", ".pbm", ".pgm", ".ppm", ".pnm"}


class CleanupError(Exception):
    """Why it can't be done, worded for the person."""


def _project(controller):
    project = controller.current_project()
    if not project:
        raise ResolveConnectionError("No project is open in Resolve.")
    return project


def _is_still(item):
    try:
        kind = str(item.GetClipProperty("Type") or "")
        path = str(item.GetClipProperty("File Path") or "")
    except Exception:  # noqa: BLE001 - an item that won't say isn't one to open
        return False, "", ""
    return ("still" in kind.lower() or os.path.splitext(path)[1].lower() in STILL_EXTS) and \
        "%" not in path and "[" not in os.path.basename(path), kind, path


def _describe(item, where):
    ok, kind, path = _is_still(item)
    return {"name": str(item.GetName() or os.path.basename(path)), "path": path, "id": str(item.GetUniqueId()),
            "where": where, "still": ok, "kind": kind}


def _call(obj, name, *args, default=None):
    """obj.name(*args), or `default` if Resolve refuses (its objects answer
    hasattr() True for any name: a missing method shows as a failed call)."""
    try:
        result = getattr(obj, name)(*args)
    except Exception:  # noqa: BLE001
        return default
    return default if result is None else result


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _footage(got, item, pool_item, timeline, outside="nearest"):
    """For footage on the timeline: {"range": [first, end) in the file's
    own frames, "frame": the file's frame at the playhead} - left out
    where Resolve won't say (the whole clip is used then). A playhead
    that's not over the clip gives its nearest frame - or its first, with
    outside="first" (a dragged clip, nowhere near the playhead)."""
    if got["still"] or "%" in got["path"]:
        return got
    tl_fps = _float(_call(timeline, "GetSetting", "timelineFrameRate"), 0.0)
    media_fps = _float(_call(pool_item, "GetClipProperty", "FPS"), tl_fps) or tl_fps
    first = _call(item, "GetSourceStartFrame")
    length = _call(item, "GetDuration")
    if first is None or not length or not tl_fps:
        return got
    k = media_fps / tl_fps                       # file frames per timeline frame (100% speed)
    first = int(round(_float(first)))
    end = first + max(1, int(round(_float(length) * k)))
    got = {**got, "range": [first, end]}
    tc = _call(timeline, "GetCurrentTimecode", default="")
    start = _call(item, "GetStart")
    if tc and start is not None:
        try:
            at = timecode_to_frames(str(tc), tl_fps) - int(_float(start))
        except (ValueError, TypeError):
            return got
        if outside == "first" and not 0 <= at < _float(length):
            at = 0
        got["frame"] = max(first, min(end - 1, first + int(round(at * k))))
    return got


def _selected_on(timeline):
    """[(timeline item, its Media Pool clip)] for the video clips selected
    on the timeline - a clip's linked audio is selected with it."""
    selected = _call(timeline, "GetSelectedClips", default=[]) or []
    if isinstance(selected, dict):
        selected = list(selected.values())
    out = []
    for item in selected:
        track = _call(item, "GetTrackTypeAndIndex", default=None)
        if track and str(track[0]).lower() != "video":
            continue
        pool_item = _call(item, "GetMediaPoolItem")
        if pool_item is not None:
            out.append((item, pool_item))
    return out


def find(controller, selected=False):
    """The picture or footage to work on: {name, path, id, where, still}
    (footage under the playhead also: range, frame; with selected=True the
    clip selected on the timeline instead, and "others": how many more
    were selected). Raises CleanupError saying what's there instead."""
    project = _project(controller)
    timeline = project.GetCurrentTimeline()
    if selected:
        if timeline is None:
            raise CleanupError("Resolve has no timeline open.")
        found = []
        for item, pool_item in _selected_on(timeline):
            got = _describe(pool_item, "timeline")
            if got["path"] and "%" not in got["path"]:
                found.append(_footage(got, item, pool_item, timeline, outside="first"))
        if not found:
            raise CleanupError("Resolve didn't say which clip that was – click it on the timeline, then use From Resolve.")
        return {**found[0], "others": len(found) - 1}
    if timeline is not None:
        try:
            item = timeline.GetCurrentVideoItem()
            pool_item = item.GetMediaPoolItem() if item else None
        except Exception:  # noqa: BLE001 - a generator, a title, a compound clip
            item = pool_item = None
        if pool_item is not None:
            got = _describe(pool_item, "timeline")
            if got["path"] and "%" not in got["path"]:
                return _footage(got, item, pool_item, timeline)
    try:
        selected = project.GetMediaPool().GetSelectedClips() or []
    except Exception:  # noqa: BLE001 - an older Resolve
        selected = []
    for item in selected:
        got = _describe(item, "pool")
        if got["path"] and "%" not in got["path"]:
            return got
    raise CleanupError("Put the playhead over a clip on the timeline, or select one in the Media Pool.")


def _walk(folder):
    yield folder
    for sub in folder.GetSubFolderList() or []:
        yield from _walk(sub)


def _locate(project, clip_id):
    """(the Media Pool clip with this id, its bin)."""
    pool = project.GetMediaPool()
    for folder in _walk(pool.GetRootFolder()):
        for clip in folder.GetClipList() or []:
            try:
                if str(clip.GetUniqueId()) == clip_id:
                    return clip, folder
            except Exception:  # noqa: BLE001 - a clip that won't say
                continue
    raise CleanupError("That clip isn't in the Media Pool any more.")


def swap(controller, clip_id, path):
    """Points the clip at `path`. {"name"}."""
    project = _project(controller)
    clip, _folder = _locate(project, clip_id)
    if not os.path.isfile(path):
        raise CleanupError("The cleaned file isn't there any more.")
    if not clip.ReplaceClip(path):
        raise CleanupError("Resolve wouldn't swap the file in.")
    # Resolve can say yes and leave the clip as it was: check.
    now = str(clip.GetClipProperty("File Path") or "")
    if os.path.normcase(os.path.abspath(now)) != os.path.normcase(os.path.abspath(path)):
        raise CleanupError("Resolve said yes, but the clip still uses the old file.")
    return {"name": str(clip.GetName() or "")}


def _import_shown(project, clip_id, what, failed):
    """ImportMedia([what]) into the bin that holds clip_id (the current bin
    if that clip's gone) - and that bin is left showing in the Media Pool
    with the new clip selected: put away in a bin that isn't the one being
    looked at, it was easy to miss (seen with a sequence put beside its
    original in "FX6" while "assets" was showing). {"name", "bin"}."""
    pool = project.GetMediaPool()
    try:
        _clip, folder = _locate(project, clip_id) if clip_id else (None, None)
    except CleanupError:
        folder = None
    if folder is not None:
        pool.SetCurrentFolder(folder)
    imported = pool.ImportMedia([what]) or []
    if not imported:
        raise CleanupError(failed)
    item = imported[0]
    _call(pool, "SetSelectedClip", item)
    shown = folder or _call(pool, "GetCurrentFolder")
    name = _call(item, "GetName", default="") or (what if isinstance(what, str) else what["FilePath"])
    return {"name": os.path.basename(str(name)), "bin": str(_call(shown, "GetName", default="") if shown else "")}


def add(controller, clip_id, path):
    """Imports `path` into the bin that holds clip_id (the current bin if
    that clip's gone), and shows it there. {"name", "bin"}."""
    project = _project(controller)
    if not os.path.isfile(path):
        raise CleanupError(f"{os.path.basename(path)} isn't there any more.")
    return _import_shown(project, clip_id, path, "Resolve didn't import the file.")


def add_sequence(controller, clip_id, pattern, first, last):
    """Imports the sequence `pattern` (name_%06d.png), frames first .. last,
    as one clip into the bin that holds clip_id (else the current bin), and
    shows it there. {"name", "bin"}."""
    project = _project(controller)
    if not os.path.isfile(pattern.replace("%06d", f"{int(first):06d}")):
        raise CleanupError("The frames aren't there any more.")
    return _import_shown(project, clip_id, {"FilePath": pattern, "StartIndex": int(first), "EndIndex": int(last)},
                         "Resolve didn't import the frames.")
