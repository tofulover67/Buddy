#!/usr/bin/env python3
"""
Text Edit's Resolve calls. Qt-free: the reads, the playhead and the In/Out
marks run in the Resolve reader's process (core/resolve_reader_child.py
JOBS "podcast_text.*") - Resolve holds every call while the timeline plays,
and one made inside Buddy froze it. The cuts (keys.py) run on Buddy's main
thread, behind the busy overlay.

Subtitles already on the timeline are read too (subtitle_cues), to be used
as the transcript without transcribing.

Writes: SetCurrentTimecode (a click on a word), SetMarkInOut / ClearMarkInOut
(a selection), and the person's own Ripple Delete key, pressed for them
when they delete words.
"""

import hashlib
import json

from core.resolve_bridge import ResolveConnectionError
from pages.stills_exporter.resolve_ext import frames_to_timecode, timecode_to_frames


def _call(obj, name, *args, default=None):
    """obj.name(*args), or `default` if Resolve refuses - its objects answer
    hasattr() True for any name, so a missing method only shows as a failure."""
    try:
        result = getattr(obj, name)(*args)
    except Exception:  # noqa: BLE001
        return default
    return default if result is None else result


def _float(value, default=None):
    try:
        return float(str(value).split()[0])
    except (TypeError, ValueError, IndexError):
        return default


def get_timeline(controller):
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("No project is open in Resolve.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveConnectionError("No timeline is open in Resolve.")
    return timeline


def timeline_fps(timeline):
    raw = str(_call(timeline, "GetSetting", "timelineFrameRate", default="") or "")
    return _float(raw.replace("DF", "").replace("df", ""), 24.0) or 24.0


def channels_from_mapping(mapping):
    """The file's channels a clip plays, 0-based (Resolve's
    GetSourceAudioChannelMapping JSON) - None for all of them."""
    try:
        data = json.loads(mapping) if isinstance(mapping, str) else mapping
        tracks = data["track_mapping"]
        picked = [int(i) - 1 for key in sorted(tracks, key=lambda k: int(k))
                  for i in tracks[key].get("channel_idx") or []]
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return picked if picked and min(picked) >= 0 else None


def _media(item, cache):
    """{path, fps, channels (the file's count)} or None (a title, a
    generator, a compound clip). GetClipProperty is the slowest call here:
    `cache`, by media pool item id, keeps it."""
    mpi = _call(item, "GetMediaPoolItem")
    if mpi is None:
        return None
    mid = str(_call(mpi, "GetUniqueId", default="") or "")
    if mid and mid in cache:
        return cache[mid]
    props = _call(mpi, "GetClipProperty", default={}) or {}
    path = str(props.get("File Path") or "") if isinstance(props, dict) else ""
    media = {"path": path, "id": mid, "fps": _float(props.get("FPS")) if isinstance(props, dict) else None,
             "count": int(_float(props.get("Audio Ch"), 0) or 0) if isinstance(props, dict) else 0} if path else None
    if mid:
        cache[mid] = media
    return media


def read_timeline(controller, cache):
    """The open timeline as model.py's `timeline`, with "sig": what changes
    when anything on it moves."""
    timeline = get_timeline(controller)
    fps = timeline_fps(timeline)
    start = int(_call(timeline, "GetStartFrame", default=0) or 0)
    data = {"id": str(_call(timeline, "GetUniqueId", default="") or ""), "name": str(_call(timeline, "GetName", default="") or ""),
            "fps": fps, "start": start, "end": int(_call(timeline, "GetEndFrame", default=start) or start),
            "drop": str(_call(timeline, "GetSetting", "timelineDropFrameTimecode", default="0")) == "1",
            "tracks": {"audio": [], "video": []}, "items": []}
    for kind in ("audio", "video"):
        for index in range(1, int(_call(timeline, "GetTrackCount", kind, default=0) or 0) + 1):
            name = str(_call(timeline, "GetTrackName", kind, index, default="") or "")
            data["tracks"][kind].append({
                "index": index, "name": name,
                "locked": bool(_call(timeline, "GetIsTrackLocked", kind, index, default=False)),
            })
            for item in _call(timeline, "GetItemListInTrack", kind, index, default=[]) or []:
                first, end = _call(item, "GetStart"), _call(item, "GetEnd")
                if first is None or end is None:
                    continue
                media = _media(item, cache)
                src_fps = (media or {}).get("fps") or fps
                offset = _float(_call(item, "GetSourceStartFrame"), None)
                if offset is None:
                    offset = _float(_call(item, "GetLeftOffset"), 0.0) or 0.0
                channels = None
                if kind == "audio" and media is not None and media.get("count", 0) > 1:
                    channels = channels_from_mapping(_call(item, "GetSourceAudioChannelMapping", default=""))
                    if channels is not None and sorted(channels) == list(range(media["count"])):
                        channels = None         # the whole file
                data["items"].append({
                    "kind": kind, "track": index, "uid": str(_call(item, "GetUniqueId", default="") or ""),
                    "name": str(_call(item, "GetName", default="") or ""),
                    "path": (media or {}).get("path", ""), "channels": channels,
                    "start": int(first), "end": int(end), "offset": float(offset) / float(src_fps),
                    "locked": media is None,
                })
    data["subtitles"] = subtitle_tracks(timeline)
    data["sig"] = signature(data)
    return data


def subtitle_tracks(timeline):
    """[{index, name, count}] - the subtitle tracks with subtitles on them."""
    out = []
    for index in range(1, int(_call(timeline, "GetTrackCount", "subtitle", default=0) or 0) + 1):
        count = len(_call(timeline, "GetItemListInTrack", "subtitle", index, default=[]) or [])
        if count:
            out.append({"index": index, "count": count,
                        "name": str(_call(timeline, "GetTrackName", "subtitle", index, default="") or "")})
    return out


def subtitle_cues(controller, cache, index, timeline_id=""):
    """Subtitle track `index` read as text: {"timeline": read_timeline()'s,
    "cues": [[first, end, text]] in frames from the timeline's start, in
    time order}. A subtitle's text is its clip's name. Read-only."""
    timeline = get_timeline(controller)
    if timeline_id and str(_call(timeline, "GetUniqueId", default="")) != timeline_id:
        raise ResolveConnectionError("Another timeline is open now.")
    start = int(_call(timeline, "GetStartFrame", default=0) or 0)
    cues = []
    for item in _call(timeline, "GetItemListInTrack", "subtitle", int(index), default=[]) or []:
        first, end = _call(item, "GetStart"), _call(item, "GetEnd")
        text = " ".join(str(_call(item, "GetName", default="") or "").split())
        if first is not None and end is not None and text:
            cues.append([int(first) - start, int(end) - start, text])
    return {"timeline": read_timeline(controller, cache), "cues": sorted(cues)}


def signature(data):
    rows = sorted((i["kind"], i["track"], i["start"], i["end"], round(i["offset"], 4), i["path"])
                  for i in data["items"])
    digest = hashlib.sha1(repr(rows).encode("utf-8")).hexdigest()[:16]
    return f'{data["id"]}|{digest}|{len(rows)}'


def playhead(controller):
    """{"id", "frame": playhead from the timeline's start (None if unknown)}."""
    timeline = get_timeline(controller)
    fps = timeline_fps(timeline)
    timecode = _call(timeline, "GetCurrentTimecode", default="")
    start = int(_call(timeline, "GetStartFrame", default=0) or 0)
    frame = timecode_to_frames(timecode, fps) - start if timecode else None
    return {"id": str(_call(timeline, "GetUniqueId", default="") or ""), "frame": frame}


def seek(controller, frame, timeline_id=""):
    """The playhead to `frame` (from the timeline's start), on that timeline only."""
    timeline = get_timeline(controller)
    if timeline_id and str(_call(timeline, "GetUniqueId", default="")) != timeline_id:
        raise ResolveConnectionError("Another timeline is open now.")
    fps = timeline_fps(timeline)
    drop = str(_call(timeline, "GetSetting", "timelineDropFrameTimecode", default="0")) == "1"
    start = int(_call(timeline, "GetStartFrame", default=0) or 0)
    if not timeline.SetCurrentTimecode(frames_to_timecode(start + max(0, int(frame)), fps, drop)):
        raise ResolveConnectionError("Resolve didn't move the playhead – is the timeline playing?")
    return True


# SetMarkInOut's frames: from the timeline's start ("rel") or absolute ("abs")
# - the documentation doesn't say; found by reading the marks back.
_BASIS = {"value": None}


def _marks(timeline):
    marks = _call(timeline, "GetMarkInOut", default={}) or {}
    for kind in ("video", "audio"):
        got = marks.get(kind) if isinstance(marks, dict) else None
        if isinstance(got, dict) and "in" in got and "out" in got:
            return int(got["in"]), int(got["out"])
    return None


def set_marks(controller, first, end, timeline_id=""):
    """Resolve's In and Out on [first, end) frames from the timeline's start
    (Out is the last frame kept in). Needs Resolve 19.1 or later. True if set."""
    timeline = get_timeline(controller)
    if timeline_id and str(_call(timeline, "GetUniqueId", default="")) != timeline_id:
        raise ResolveConnectionError("Another timeline is open now.")
    start = int(_call(timeline, "GetStartFrame", default=0) or 0)
    first, last = int(first), max(int(first), int(end) - 1)
    order = [_BASIS["value"]] if _BASIS["value"] else ["rel", "abs"]
    for basis in order:
        offset = 0 if basis == "rel" else start
        ok = _call(timeline, "SetMarkInOut", first + offset, last + offset, "all", default=False)
        if not ok:
            ok = _call(timeline, "SetMarkInOut", first + offset, last + offset, default=False)
        got = _marks(timeline)
        if ok and (got is None or got == (first + offset, last + offset)):
            _BASIS["value"] = basis
            return True
    return False


def clear_marks(controller):
    timeline = get_timeline(controller)
    return bool(_call(timeline, "ClearMarkInOut", "all", default=False) or _call(timeline, "ClearMarkInOut", default=False))


# ----------------------------------------------- in the reader's process --

_HERE = {"media": {}}


def read_here(controller, fresh=False):
    if fresh:
        _HERE["media"].clear()
    return read_timeline(controller, _HERE["media"])


def subtitles_here(controller, index, timeline_id=""):
    return subtitle_cues(controller, _HERE["media"], index, timeline_id)


def live_here(controller):
    return playhead(controller)


def seek_here(controller, frame, timeline_id=""):
    return seek(controller, frame, timeline_id)


def marks_here(controller, first, end, timeline_id=""):
    return set_marks(controller, first, end, timeline_id)


def clear_marks_here(controller):
    return clear_marks(controller)
