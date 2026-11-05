#!/usr/bin/env python3
"""
Auto Switch's calls to Resolve. No Qt.

    read_here(controller, fresh)   the open timeline: Audio Assistant's read of
                                   its audio (clips, files, offsets, channels)
                                   and its video tracks (each clip's file, a
                                   still of each track's first) - a reader job
                                   ("podswitch.read", core/resolve_reader_child.py)
    build_here(controller, shots, cameras, timeline_id, muted)
                                   a reader job ("podswitch.build") - and
    build(...)                     on the UI thread only where there's no
                                   reader (core/busy_job.py): a NEW timeline, "<name>
                                   (Auto Switch)", the open one's export with
                                   each camera razored at every switch
                                   (otio_split) and the pieces not on screen
                                   turned off - and the muted cameras' own
                                   sound, cut the same, turned off. The open
                                   timeline isn't touched.

The round trip is Project Setup's (otio_engine.read_current_timeline,
import_rebuilt): an OTIO export, worked on as JSON, imported as a new
timeline bound to the Media Pool's clips - not hundreds of timeline API
edits, which have crashed Resolve on big timelines.
"""

import os
from types import SimpleNamespace

from pages.audio_assistant import resolve_ext as audio_ext
from pages.project_setup import otio_engine as oe
from pages.project_setup.resolve_ext import ProjectSetupController

from . import otio_split

SUFFIX = "(Auto Switch)"
STILL_SIDE = 240        # a camera's still in step 1: its longer side, in pixels
VIDEO_EXTS = (".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".braw", ".r3d", ".ari", ".crm", ".mts", ".m2ts",
              ".insv", ".lrv")


class BuildError(RuntimeError):
    """The new timeline couldn't be made; the open one is as it was."""


def _file_name(item):
    """The file a timeline item plays ("A001_C003.MOV") - "" for a title or generator."""
    media = audio_ext._call(item, "GetMediaPoolItem", default=None)
    if media is None:
        return ""
    for key in ("File Name", "File Path"):
        value = audio_ext._call(media, "GetClipProperty", key, default="")
        if isinstance(value, str) and value:
            return os.path.basename(value.replace("\\", "/"))
    return ""


def _still(item):
    """A small JPEG of the clip's middle frame, as a data URL - "" when
    Resolve can't (before 21.1, or a clip with no picture). ExportFrame
    moves neither the page nor the playhead."""
    duration = audio_ext._call(item, "GetDuration", default=0) or 0
    result = audio_ext._call(item, "ExportFrame", int(duration) // 2,
                             {"outputType": "encodedString", "encodedImageFormat": "jpg",
                              "maxDimension": STILL_SIDE}, default=None)
    if isinstance(result, dict) and result.get("success") and isinstance(result.get("data"), str):
        return "data:image/jpeg;base64," + result["data"]
    return ""


def _video_tracks(timeline, start):
    tracks = []
    for index in range(1, int(audio_ext._call(timeline, "GetTrackCount", "video", default=0) or 0) + 1):
        clips, first = [], None
        for item in audio_ext._call(timeline, "GetItemListInTrack", "video", index, default=[]) or []:
            begin = audio_ext._call(item, "GetStart", default=None)
            end = audio_ext._call(item, "GetEnd", default=None)
            if begin is None or end is None:
                continue
            clips.append({"start": int(begin), "end": int(end),
                          "name": str(audio_ext._call(item, "GetName", default="") or ""),
                          "file": _file_name(item)})
            if first is None and clips[-1]["file"]:
                first = item
        tracks.append({
            "index": index,
            "name": str(audio_ext._call(timeline, "GetTrackName", "video", index, default="") or f"Video {index}"),
            "enabled": bool(audio_ext._call(timeline, "GetIsTrackEnabled", "video", index, default=True)),
            "clips": clips,
            "still": _still(first) if first is not None else "",
        })
    return tracks


def read(controller, media_cache):
    """{audio data (Audio Assistant's), "video": [tracks]}."""
    data, _items = audio_ext.read_timeline(controller, media_cache)
    data["video"] = _video_tracks(audio_ext.get_timeline(controller), data["start"])
    return data


def read_here(controller, fresh=False):
    """read() in the reader's process (its media cache kept there)."""
    data = audio_ext.read_timeline_here(controller, fresh)
    data["video"] = _video_tracks(audio_ext.get_timeline(controller), data["start"])
    return data


def is_camera_sound(path):
    """A mic track whose files are camera files: the camera's own mic, which
    hears everyone - left out of who's talking unless it's asked for."""
    return str(path or "").lower().endswith(VIDEO_EXTS)


def _turn_off(timeline, kind, wanted, origin):
    """Turns off the items of `kind` starting where `wanted` {(track,
    start)} says (a frame either way: Resolve rounds) -> (turned, failed)."""
    turned, failed = 0, 0
    for track in sorted({t for t, _b in wanted}):
        for item in audio_ext._call(timeline, "GetItemListInTrack", kind, track, default=[]) or []:
            begin = int(item.GetStart()) - origin
            if not {(track, begin), (track, begin - 1), (track, begin + 1)} & wanted:
                continue
            ok = audio_ext._call(item, "SetClipEnabled", False, default=False)
            if ok and audio_ext._call(item, "GetClipEnabled", default=False) is False:
                turned += 1
            else:
                failed += 1
    return turned, failed


def build_here(controller, shots, cameras, timeline_id="", muted=()):
    """build() from plain data - shots as {"start", "end", "camera"} - as the
    page sends it to the Resolve reader's process ("podswitch.build")."""
    shots = [SimpleNamespace(start=int(s["start"]), end=int(s["end"]), camera=int(s["camera"])) for s in shots]
    return build(controller, shots, [int(c) for c in cameras], str(timeline_id or ""), [int(m) for m in muted])


def build(controller, shots, cameras, timeline_id="", muted=()):
    """{"name", "off", "failed", "wanted", "sound_off", "sound_failed",
    "warnings"} - see the module docstring."""
    wrapped = ProjectSetupController(controller)
    timeline = wrapped.get_current_timeline()
    if timeline is None:
        raise BuildError("No timeline is open in Resolve.")
    if timeline_id and str(audio_ext._call(timeline, "GetUniqueId", default="") or "") != timeline_id:
        raise BuildError("A different timeline is open now – press Analyse again for this one.")
    name = str(timeline.GetName() or "Timeline")
    start = int(timeline.GetStartFrame())
    try:
        document, _clips, workdir = oe.read_current_timeline(wrapped)
        result, pieces, sound, warnings = otio_split.split(document, shots, cameras, start, muted)
        new = oe.import_rebuilt(wrapped, result, workdir, name, SUFFIX, "auto_switch.otio")
    except oe.OtioError as exc:
        raise BuildError(str(exc)) from exc
    origin = int(new.GetStartFrame())
    off = {(track, begin) for track, begin, _end, on in pieces if not on}
    turned, failed = _turn_off(new, "video", off, origin)
    quiet = {(track, begin) for track, begin, _end in sound}
    sound_off, sound_failed = _turn_off(new, "audio", quiet, origin)
    try:
        project = wrapped.get_project()
        project.SetCurrentTimeline(new)
    except Exception:  # noqa: BLE001 - the timeline is made either way
        pass
    return {"name": str(new.GetName() or ""), "off": turned, "failed": failed, "wanted": len(off),
            "sound_off": sound_off, "sound_failed": sound_failed, "warnings": warnings}
