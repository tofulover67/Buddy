#!/usr/bin/env python3
"""
Audit's Resolve calls - made in the Resolve reader's process
(core/resolve_reader.py, jobs "audit.scan", "audit.goto" and "audit.fix"),
so a long timeline read never freezes Buddy, even while Resolve plays.

scan() reads the open timeline into plain data - its settings, every track
and clip, the markers - and checks.py finds the problems in that. Reading
changes nothing; goto() moves the playhead; fix() makes the fixes checks.py
offers, and only when the person presses one: turning tracks and clips on,
deleting a flash frame, closing a gap, removing a to-do marker.

Measured on Studio 21.1 (2026-10-06): GetClipEnabled and GetIsTrackEnabled
read as they should; a clip's media pool properties carry its own "Online
Status"; a still says 24 fps whatever the timeline is (so its rate means
nothing); a Text+ has no media pool item, and its font and text are on the
TextPlus tool in its Fusion comp. 17 clips read in 0.02 s.
"""

from core.marker_colors import numeric_markers
from core.resolve_bridge import ResolveConnectionError
from pages.audio_assistant.render import channels_from_mapping
from pages.media_relink.relink_engine import media_exists
from pages.stills_exporter.resolve_ext import frames_to_timecode

KINDS = ("video", "audio", "subtitle")
# Media pool "Type"s that are pictures with a rate of their own.
MOVING = ("Video", "Video + Audio")


def _timeline(controller):
    project = controller.current_project()
    if not project:
        raise ResolveConnectionError("No project is open in Resolve.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise ResolveConnectionError("No timeline is open in Resolve.")
    return timeline


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolution(text):
    """'3840x2160' -> (3840, 2160), or None."""
    try:
        w, h = str(text or "").lower().split("x", 1)
        return int(w), int(h)
    except ValueError:
        return None


def _call(obj, name, *args, default=None):
    """obj.name(*args), or default when Resolve hasn't got it or it fails."""
    try:
        return getattr(obj, name)(*args)
    except Exception:  # noqa: BLE001 - an older Resolve, or a clip it won't answer for
        return default


def _chained(tool):
    """Whether a Text+'s words come through one text modifier with another behind
    it (Character Level Styling behind a Follower). Reading that Text+'s words
    froze Resolve for good (Studio 21.1, docs/RESOLVE_API_GUIDE.md), so they
    aren't read; walking the connections is safe."""
    try:
        feeding = tool.StyledText.GetConnectedOutput()
        source = feeding.GetTool() if feeding is not None else None
        return source is not None and source.Text.GetConnectedOutput() is not None
    except Exception:  # noqa: BLE001 - no modifier, or one with no text of its own
        return False


def _titles(item):
    """[{"font", "style", "text"}] for each Text+ in a clip's Fusion comps."""
    out = []
    for index in range(1, int(_call(item, "GetFusionCompCount", default=0) or 0) + 1):
        comp = _call(item, "GetFusionCompByIndex", index)
        tools = _call(comp, "GetToolList", False, "TextPlus", default={}) if comp else {}
        for tool in (tools or {}).values():
            text = "" if _chained(tool) else _call(tool, "GetInput", "StyledText", default="")
            out.append({"font": str(_call(tool, "GetInput", "Font", default="") or ""),
                        "style": str(_call(tool, "GetInput", "Style", default="") or ""),
                        "text": str(text or "")})
    return out


def _media(mpi, cache):
    """What Audit needs from a media pool item's properties - read once per
    item (GetClipProperty() returns ~200 of them)."""
    key = _call(mpi, "GetUniqueId") or id(mpi)
    if key not in cache:
        props = _call(mpi, "GetClipProperty", default={}) or {}
        path = str(props.get("File Path") or "")
        status = str(props.get("Online Status") or "")
        cache[key] = {
            "type": str(props.get("Type") or ""),
            "path": path,
            # Resolve's word first; a path that isn't on disk is offline too.
            "online": status != "Offline" and (not path or media_exists(path)),
            "fps": _number(props.get("FPS")),
            "res": _resolution(props.get("Resolution")),
            "audio_ch": int(_number(props.get("Audio Ch"))),
        }
    return cache[key]


def _sound_of(clip, item):
    """Where an audio clip starts in its file (seconds) and the file's
    channels it plays (0-based; None: all) - for the recording checks
    (sources.py). GetSourceStartFrame is in the file's own frames."""
    fps = clip.get("fps") or 24.0
    clip["source_s"] = _number(_call(item, "GetSourceStartFrame", default=0)) / fps
    channels = channels_from_mapping(_call(item, "GetSourceAudioChannelMapping", default=""))
    clip["channels"] = None if channels == list(range(clip.get("audio_ch") or 0)) else channels


def _markers(raw):
    return [{"frame": int(frame), "color": str(info.get("color") or ""), "name": str(info.get("name") or ""),
             "note": str(info.get("note") or "")} for frame, info in numeric_markers(raw)]


def scan(controller):
    """The open timeline as plain data, for checks.run()."""
    timeline = _timeline(controller)
    fps = _number(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    media_cache = {}
    tracks = []
    for kind in KINDS:
        for index in range(1, int(_call(timeline, "GetTrackCount", kind, default=0) or 0) + 1):
            items = []
            for item in _call(timeline, "GetItemListInTrack", kind, index, default=[]) or []:
                if item is None:
                    continue
                clip = {"name": str(_call(item, "GetName", default="") or ""),
                        "start": int(_call(item, "GetStart", default=0) or 0),
                        "end": int(_call(item, "GetEnd", default=0) or 0),
                        "enabled": _call(item, "GetClipEnabled", default=True) is not False,
                        "markers": _markers(_call(item, "GetMarkers", default={}))}
                mpi = _call(item, "GetMediaPoolItem") if kind != "subtitle" else None
                if mpi is not None:
                    clip.update(_media(mpi, media_cache))
                    if kind == "audio" and clip.get("path"):
                        _sound_of(clip, item)
                if kind == "video":
                    titles = _titles(item)
                    if titles:
                        clip["titles"] = titles
                items.append(clip)
            tracks.append({"kind": kind, "index": index,
                           "name": str(_call(timeline, "GetTrackName", kind, index, default="") or ""),
                           "enabled": _call(timeline, "GetIsTrackEnabled", kind, index, default=True) is not False,
                           "locked": bool(_call(timeline, "GetIsTrackLocked", kind, index, default=False)),
                           "subtype": (str(_call(timeline, "GetTrackSubType", kind, index, default="") or "")
                                       if kind == "audio" else ""),
                           "items": items})
    return {
        "name": str(_call(timeline, "GetName", default="") or ""),
        "id": str(_call(timeline, "GetUniqueId", default="") or ""),
        "fps": fps,
        "drop": str(timeline.GetSetting("timelineDropFrameTimecode")) == "1",
        "width": int(_number(timeline.GetSetting("timelineResolutionWidth"))),
        "height": int(_number(timeline.GetSetting("timelineResolutionHeight"))),
        "start": int(_call(timeline, "GetStartFrame", default=0) or 0),
        "end": int(_call(timeline, "GetEndFrame", default=0) or 0),
        "markers": _markers(_call(timeline, "GetMarkers", default={})),
        "tracks": tracks,
    }


def goto(controller, frame, timeline_id=""):
    """Moves the playhead to `frame` (the timeline's own frame number) on the
    timeline that was checked - not on another one opened since."""
    timeline = _timeline(controller)
    if timeline_id and str(_call(timeline, "GetUniqueId", default="") or "") != timeline_id:
        raise ResolveConnectionError("A different timeline is open now – check again to go to this one.")
    fps = _number(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    drop = str(timeline.GetSetting("timelineDropFrameTimecode")) == "1"
    timecode = frames_to_timecode(int(frame), fps, drop)
    if not timeline.SetCurrentTimecode(timecode):
        raise ResolveConnectionError("Resolve didn't move the playhead.")
    return timecode


# ---------------------------------------------------------------- fixes --

class FixRefused(Exception):
    """A fix that wasn't made, with the sentence that says why."""


def _where(kind, index):
    return "VAS"[KINDS.index(kind)] + str(index)


def _find(timeline, ref):
    """The clip a check found (checks._ref), if it's still where it was."""
    for item in _call(timeline, "GetItemListInTrack", ref["kind"], ref["index"], default=[]) or []:
        if item is None:
            continue
        if (int(_call(item, "GetStart", default=-1)), int(_call(item, "GetEnd", default=-1))) != (ref["start"], ref["end"]):
            continue
        if ref.get("name") and str(_call(item, "GetName", default="") or "") != ref["name"]:
            continue
        return item
    raise FixRefused("It's changed since the check – check again.")


def _open(timeline, kind, index):
    if _call(timeline, "GetIsTrackLocked", kind, index, default=False):
        raise FixRefused(f"{_where(kind, index)} is locked in Resolve – unlock it first.")


def _nothing_else(timeline, start, end, leave=()):
    """Raises unless start..end is empty on every track, but for `leave`.
    By id: Resolve hands out a new object for the same clip on every read."""
    from pages.command_center.actions import _track_items
    gone = {_call(other, "GetUniqueId") or id(other) for other in leave}
    for _kind, _index, item in _track_items(timeline):
        if (_call(item, "GetUniqueId") or id(item)) in gone:
            continue
        a, b = int(_call(item, "GetStart", default=0) or 0), int(_call(item, "GetEnd", default=0) or 0)
        if a < end and b > start:
            raise FixRefused("Something's there now – check again.")


def _ripple_ready(timeline):
    from pages.command_center.actions import ActionError, unlocked
    try:
        unlocked(timeline)
    except ActionError as exc:
        raise FixRefused(str(exc)) from None


def _fix_one(project, timeline, fix):
    do = fix.get("do")
    if do == "track_on":
        if not timeline.SetTrackEnable(fix["kind"], int(fix["index"]), True):
            raise FixRefused("Resolve didn't turn the track on.")
    elif do == "clip_on":
        _open(timeline, fix["kind"], fix["index"])
        if not _find(timeline, fix).SetClipEnabled(True):
            raise FixRefused("Resolve didn't turn the clip on.")
    elif do == "delete_clip":
        refs = [fix, *(fix.get("sound") or ())]
        for ref in refs:
            _open(timeline, ref["kind"], ref["index"])
        items = [_find(timeline, ref) for ref in refs]
        ripple = fix.get("then") == "close"
        if ripple:
            _ripple_ready(timeline)
            _nothing_else(timeline, fix["start"], fix["end"], items)
        if not timeline.DeleteClips(items, ripple):
            raise FixRefused("Resolve didn't delete the clip.")
    elif do == "close_gap":
        from pages.command_center.actions import ActionError, close_gaps
        _ripple_ready(timeline)
        start, end = int(fix["start"]), int(fix["end"])
        _nothing_else(timeline, start, end)
        try:
            close_gaps(project, timeline, [(start, end)])
        except ActionError as exc:
            raise FixRefused(str(exc)) from None
    elif do == "delete_marker":
        frame = int(fix["frame"])
        owner = _find(timeline, fix["clip"]) if fix.get("clip") else timeline
        if fix.get("clip"):
            _open(timeline, fix["clip"]["kind"], fix["clip"]["index"])
        here = dict(numeric_markers(_call(owner, "GetMarkers", default={}) or {})).get(frame)
        seen = {"name": fix.get("marker", ""), "note": fix.get("note", ""), "color": fix.get("color", "")}
        if here is None or any(str(here.get(k) or "") != v for k, v in seen.items()):
            raise FixRefused("The marker's changed since the check – check again.")
        if not owner.DeleteMarkerAtFrame(frame):
            raise FixRefused("Resolve didn't remove the marker.")
    else:
        raise FixRefused(f"Audit can't fix {do!r}.")


def fix(controller, fixes, timeline_id=""):
    """Makes checks.py's fixes on the timeline that was checked, one after
    another: {"done": how many were made, "failed": [why each other one
    wasn't]}. WRITES to Resolve - only when the person presses a fix (and
    has said yes to one that takes something off the timeline)."""
    timeline = _timeline(controller)
    if timeline_id and str(_call(timeline, "GetUniqueId", default="") or "") != timeline_id:
        raise ResolveConnectionError("A different timeline is open now – check again to fix this one.")
    project = controller.current_project()
    done, failed = 0, []
    for one in fixes or ():
        try:
            _fix_one(project, timeline, one)
            done += 1
        except FixRefused as exc:
            failed.append(str(exc))
    return {"done": done, "failed": failed}
