"""Footage Search's Resolve calls: which Media Pool clips there are to search,
Find in Media Pool, and putting a found stretch of a clip on the timeline.

Asked for through core/resolve_reader.py's process when there is one (JSON in
and out), since while the timeline plays Resolve holds every call. The Media
Pool items a scan found stay with the process that scanned (_SCANNED), for
reveal and place to work on.

place() is the only write, made when the person presses Place: one clip, video
only, on a track above V1 - an existing one that's free there and unlocked,
else a new one - checked to have landed where it was put, and taken off again
if it didn't. Nothing already on the timeline is moved or replaced."""

import math
import os
import re

from core.media_search import extract
from core.resolve_bridge import ResolveConnectionError

STILL_SECONDS = 5.0
VIDEO, IMAGE = "Video", "Image"

# The latest scan's Media Pool items in this process, by clip id.
_SCANNED = {"project": None, "objects": {}, "folders": {}, "rows": {}}

# Pages with no Media Pool to show a selection in.
_NO_POOL_PAGES = ("color", "deliver", "photo")


def _list(value):
    return list(value.values()) if isinstance(value, dict) else list(value or [])


def _number(value, default=0.0):
    try:
        return float(re.match(r"\s*(-?[\d.]+)", str(value)).group(1))
    except (AttributeError, TypeError, ValueError):
        return default


def _kind(path):
    """VIDEO or IMAGE for a clip whose file media search can look at
    (core/media_search/extract.py), else None - camera raw, sound, and a
    numbered sequence, whose path names a range of files and none of them."""
    kind = extract.kind_of(path)
    if "%" in path or "[" in os.path.basename(path):
        return None
    return VIDEO if kind == extract.VIDEO else IMAGE if kind == extract.IMAGE else None


def _pool(controller):
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("Open a project in Resolve first.")
    pool = project.GetMediaPool()
    if pool is None:
        raise ResolveConnectionError("Could not access the Media Pool.")
    return project, pool


def _timeline_info(project):
    timeline = project.GetCurrentTimeline()
    if not timeline:
        return None
    return {"name": str(timeline.GetName() or ""), "fps": _number(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0}


def scan(controller):
    """The Media Pool's footage and stills: {"project_id", "project_name",
    "timeline": {"name", "fps"} or None, "clips": [{"id", "name", "bin",
    "path", "type", "seconds", "fps", "frames"}], "other": how many more
    clips have a picture but no file media search can read}."""
    project, pool = _pool(controller)
    root = pool.GetRootFolder()
    if root is None:
        raise ResolveConnectionError("Could not access the Media Pool's root bin.")
    rows, objects, folders = {}, {}, {}
    other = [0]

    def walk(folder, names):
        for clip in _list(folder.GetClipList()):
            try:
                props = clip.GetClipProperty() or {}
                if props.get("Type") == "Timeline":
                    continue
                clip_id = str(clip.GetUniqueId())
                path = str(props.get("File Path") or "")
                kind = _kind(path)
                if not clip_id or clip_id in rows:
                    continue
                if not path or kind is None:
                    other[0] += str(props.get("Type") or "").lower().startswith(("video", "still"))
                    continue
                fps, frames = _number(props.get("FPS")), _number(props.get("Frames"))
                seconds = frames / fps if kind == VIDEO and fps > 0 and frames > 0 else STILL_SECONDS
                rows[clip_id] = {"id": clip_id, "name": str(props.get("Clip Name") or clip.GetName() or "Untitled"),
                                 "bin": "/".join(names), "path": path, "type": kind, "seconds": seconds,
                                 "fps": fps, "frames": int(frames)}
                objects[clip_id], folders[clip_id] = clip, folder
            except Exception:  # noqa: BLE001 - one malformed Media Pool item must not hide the rest
                continue
        for child in _list(folder.GetSubFolderList()):
            walk(child, [*names, str(child.GetName() or "Bin")])

    walk(root, [str(root.GetName() or "Master")])
    _SCANNED.update(project=str(project.GetUniqueId()), objects=objects, folders=folders, rows=rows)
    return {"project_id": str(project.GetUniqueId()), "project_name": str(project.GetName() or "Project"),
            "timeline": _timeline_info(project), "clips": list(rows.values()), "other": other[0]}


def _scanned(controller, expected_project_id, clip_id):
    """(project, pool, the clip, its bin, its row) from the latest scan here -
    or a scan now, in a process that hasn't looked at this project."""
    project, pool = _pool(controller)
    if str(project.GetUniqueId()) != expected_project_id:
        raise ResolveConnectionError("The open Resolve project changed. Refresh Footage Search first.")
    if _SCANNED["project"] != expected_project_id or clip_id not in _SCANNED["objects"]:
        scan(controller)
    clip = _SCANNED["objects"].get(clip_id)
    if clip is None:
        raise ResolveConnectionError("That clip isn't in the Media Pool any more.")
    return project, pool, clip, _SCANNED["folders"].get(clip_id), _SCANNED["rows"][clip_id]


def reveal(controller, expected_project_id, clip_id):
    """Find in Media Pool: opens the clip's bin and selects the clip there -
    from the Edit page when Resolve is on one with no Media Pool. Returns the
    bin's name."""
    _project, pool, clip, folder, _row = _scanned(controller, expected_project_id, clip_id)
    resolve = getattr(controller, "resolve", None)
    try:
        if resolve is not None and str(resolve.GetCurrentPage() or "") in _NO_POOL_PAGES:
            resolve.OpenPage("edit")
    except Exception:  # noqa: BLE001 - the selection is still made; the person switches page
        pass
    if folder is not None and not pool.SetCurrentFolder(folder):
        raise ResolveConnectionError("Resolve wouldn't open the clip's bin.")
    if not pool.SetSelectedClip(clip):
        raise ResolveConnectionError("Resolve wouldn't select the clip.")
    return str(folder.GetName() or "Master") if folder is not None else ""


def source_frames_for(length, source_fps, timeline_fps):
    """How many of a clip's own frames to place so it lasts `length` timeline
    frames: AppendToTimeline counts the source's frames, and Resolve rounds
    the timeline length down - one frame short rather than long (measured for
    Subtitles' Text+ clips, pages/text_animator/subtitle_engine.py)."""
    if not source_fps or not timeline_fps or abs(source_fps - timeline_fps) < 1e-6:
        return max(1, int(length))
    ratio = timeline_fps / source_fps
    n = math.ceil(length / ratio - 1e-9)
    if math.floor(n * ratio + 1e-9) > length:
        n -= 1
    return max(1, n)


def _timecode_frames(timecode, fps):
    """'HH:MM:SS:FF' (or ;FF, drop frame) as a frame number."""
    drop = ";" in timecode
    hours, minutes, seconds, frames = (int(part) for part in timecode.replace(";", ":").split(":"))
    total = (hours * 3600 + minutes * 60 + seconds) * int(round(fps)) + frames
    if drop:
        dropped = int(round(fps * 0.066666))
        total_minutes = hours * 60 + minutes
        total -= dropped * (total_minutes - total_minutes // 10)
    return total


def _free_track(timeline, start, end):
    """The lowest video track above V1 with nothing in [start, end), on and
    unlocked - or a new one, added on top. None if Resolve adds none."""
    count = int(timeline.GetTrackCount("video") or 0)
    for index in range(2, count + 1):
        if timeline.GetIsTrackLocked("video", index) or not timeline.GetIsTrackEnabled("video", index):
            continue
        if not any(int(i.GetStart()) < end and int(i.GetEnd()) > start
                   for i in _list(timeline.GetItemListInTrack("video", index))):
            return index
    if not timeline.AddTrack("video") or int(timeline.GetTrackCount("video") or 0) != count + 1:
        return None
    return count + 1


def place(controller, expected_project_id, clip_id, source_start, seconds, record_seconds=None):
    """Puts `seconds` of a clip, from `source_start` seconds into it, on the
    open timeline: at `record_seconds` from the timeline's start, or at the
    playhead when that's None. Picture only, on a track above V1 (_free_track).
    Returns {"track", "timeline"}."""
    project, pool, clip, _folder, row = _scanned(controller, expected_project_id, clip_id)
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise ResolveConnectionError("Open a timeline in Resolve first.")
    fps = _number(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    origin = int(timeline.GetStartFrame() or 0)
    if record_seconds is None:
        timecode = timeline.GetCurrentTimecode()
        if not timecode:
            raise ResolveConnectionError("Resolve didn't say where the playhead is.")
        record = _timecode_frames(str(timecode), fps)
    else:
        record = origin + int(round(float(record_seconds) * fps))
    length = max(1, int(round(float(seconds) * fps)))
    if row["type"] == IMAGE:
        first, count = 0, length                    # a still lasts as long as it's asked to
    else:
        clip_fps = row["fps"] or fps
        first = max(0, int(round(float(source_start) * clip_fps)))
        count = source_frames_for(length, clip_fps, fps)
        if row["frames"]:
            first = min(first, max(0, row["frames"] - 1))
            count = max(1, min(count, row["frames"] - first))
    track = _free_track(timeline, record, record + length)
    if track is None:
        raise ResolveConnectionError("Resolve wouldn't add a video track for it.")
    placed = pool.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": first, "endFrame": first + count,
                                     "mediaType": 1, "trackIndex": track, "recordFrame": record}]) or []
    item = placed[0] if placed else None
    if item is None:
        raise ResolveConnectionError("Resolve didn't place the clip.")
    if int(item.GetStart()) != record:
        timeline.DeleteClips([item], False)         # it landed somewhere else: not left there
        raise ResolveConnectionError("Resolve put the clip somewhere else, so it was taken off again.")
    return {"track": track, "timeline": str(timeline.GetName() or "")}
