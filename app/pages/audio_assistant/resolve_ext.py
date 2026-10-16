#!/usr/bin/env python3
"""
Audio Assistant's own Resolve calls - free functions taking a controller,
like every tool's resolve_ext.py. The reads the Timeline tab draws, and
the Mixer's writes (apply, crossfade) at the bottom. seek() is only ever run
from resolve_child.py, in a process of its own (see there).

Not used, on purpose: Timeline.NormalizeAudioLevel. On 21.1 it ignores
targetLoudness (EBU R128 at -14 and -23, and YouTube mode, all set the same
volume - measured on a duplicate timeline; targetLevel does work, for the
peak modes) and does nothing on a timeline that isn't the active one. Match
is Buddy's own measurement instead - see levels.py.

What the Timeline tab mirrors, measured on Resolve 21.1.0.17:
  - an audio track's name, sub type ("mono", "stereo", "adaptive16", ...),
    enabled and locked state
  - each clip's record range (GetStart/GetEnd, absolute frames), enabled
    state, clip colour, AudioVolume/AudioPan (GetProperties), fades in
    frames (GetFades), and its media's file path and format
  - where in the file a clip starts: GetSourceStartFrame() in the media's
    own frames (its "FPS" clip property). GetSourceStartTime() is NOT that -
    it's the file's source timecode in seconds (4875.5 for a camera MP4 whose
    clip starts at frame 0). Speed changes aren't read: a retimed clip's
    waveform is drawn at 100%.
  - the playhead (GetCurrentTimecode) and Resolve's own selection
    (GetSelectedClips, matched to the drawn clips by GetUniqueId)
Voice Isolation reads None on clips that don't support it, and only works
on the active timeline - which is the only one read here.

A full read costs ~3 ms per clip (94 clips: 0.3 s); the live read (playhead,
selection, the selected clips' levels) under 1 ms. The page does the live
one twice a second and the full one every few seconds.
"""

import hashlib
import os
import re
import tempfile
import uuid
import xml.etree.ElementTree as ET
from fractions import Fraction

from core import marker_colors
from core.resolve_bridge import ResolveConnectionError
from pages.stills_exporter.resolve_ext import timecode_to_frames

from . import keys as K
from . import render

# Resolve's 16 clip colours, for display (close to its swatches). "" = none set.
CLIP_COLORS = {
    "Orange": "#E8742B", "Apricot": "#F0A052", "Yellow": "#E2C33D", "Lime": "#9FC43A",
    "Olive": "#76902F", "Green": "#3F9E4D", "Teal": "#2FA39A", "Navy": "#2B4F8F",
    "Blue": "#4A83D8", "Purple": "#8C5BC9", "Violet": "#B066C9", "Pink": "#E0709F",
    "Tan": "#C9A77C", "Beige": "#D8C9A8", "Brown": "#8A5A3A", "Chocolate": "#6B4331",
}


# Resolve's audio transitions (AddTransition category "audio"), as named on the timeline.
CROSSFADES = ["Cross Fade +3 dB", "Cross Fade 0 dB", "Cross Fade -3 dB"]
TRANSITION_PREFIX = "Cross Fade"
# Clip property ranges Resolve accepts (a value outside makes SetProperties refuse everything).
VOLUME_RANGE = (-100.0, 30.0)
PAN_RANGE = (-100.0, 100.0)
LEVELER_GAIN_RANGE = (0.0, 6.0)
MOST_CUTS = 3000          # the picture's cuts read at the most (a long, busy edit has a few hundred)


def file_key(path):
    """A short id for a media file, shared by every clip that uses it."""
    return hashlib.sha1(os.path.normcase(os.path.normpath(path)).encode("utf-8")).hexdigest()[:16]


def get_timeline(controller):
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("No project is open in Resolve.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveConnectionError("No timeline is open in Resolve.")
    return timeline


def _float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _call(obj, name, *args, default=None):
    """obj.name(*args), or `default` if Resolve refuses. Resolve's objects
    answer hasattr() True for any name, so a missing method only shows up
    as a failed call."""
    try:
        result = getattr(obj, name)(*args)
    except Exception:  # noqa: BLE001 - a clip or file Resolve can't describe
        return default
    return default if result is None else result


def _media(item, cache):
    """The file under a clip: {path, key, rate, channels, codec, fps} or
    None (a compound clip, a generator). `cache` is kept by the page, keyed
    by media pool item id: GetClipProperty() is the slowest call here."""
    mpi = _call(item, "GetMediaPoolItem")
    if mpi is None:
        return None
    mid = _call(mpi, "GetUniqueId", default="")
    if mid and mid in cache:
        return cache[mid]
    props = _call(mpi, "GetClipProperty", default={}) or {}
    path = str(props.get("File Path") or "")
    media = None
    if path:
        media = {
            "path": path,
            "key": file_key(path),
            "rate": _float(str(props.get("Sample Rate") or "").split()[0] if props.get("Sample Rate") else None),
            "channels": int(_float(props.get("Audio Ch"), 0) or 0),
            "codec": str(props.get("Audio Codec") or ""),
            "fps": _float(props.get("FPS")),
        }
        media["fx"] = _fx_of(media)
    if mid:
        cache[mid] = media
    return media


def peaks_key(key, channels):
    """The waveform a clip is drawn and measured with: its file's (`key`) for the
    channels it plays - peaks.fold()'s, one per file and channel set."""
    return key if not channels else f"{key}:{'.'.join(str(c) for c in channels)}"


def _play(clip, item, media):
    """The file's channels the clip plays (Resolve's mapping, 0-based; None: all
    of them) and the waveform that goes with them, onto the clip. A clip mapped to
    every channel of its file in order is 'all' - the same waveform."""
    channels = render.channels_from_mapping(_call(item, "GetSourceAudioChannelMapping", default=""))
    if channels and channels == list(range(int(media.get("channels") or 0))):
        channels = None
    clip["channels"] = channels
    clip["peaks"] = peaks_key(media["key"], channels)


def _levels(item):
    """A clip's own audio settings - what the live read refreshes."""
    props = _call(item, "GetProperties", default={}) or {}
    fades = _call(item, "GetFades", default={}) or {}
    volume = _float(props.get("AudioVolume"))
    isolation = props.get("AudioVoiceIsolationEnabled")
    leveler = props.get("AudioDialogueLevelerEnabled")
    return {
        "enabled": bool(_call(item, "GetClipEnabled", default=True)),
        "volume": volume if volume is not None and props.get("AudioVolumeEnabled", True) is not False else 0.0,
        "pan": _float(props.get("AudioPan"), 0.0),
        "fade_in": _float(fades.get("FadeIn"), 0.0),
        "fade_out": _float(fades.get("FadeOut"), 0.0),
        # None: this clip can't have it (Resolve reads None and refuses writes).
        "isolation": None if isolation is None else {
            "on": bool(isolation), "amount": _float(props.get("AudioVoiceIsolationAmount"), 0.0)},
        "leveler": None if leveler is None else {
            "on": bool(leveler),
            "mode": int(_float(props.get("AudioDialogueLevelerMode"), 0.0) or 0),
            "reduce_loud": bool(props.get("AudioDialogueLevelerReduceLoudDialogue")),
            "lift_soft": bool(props.get("AudioDialogueLevelerLiftSoftDialogue")),
            "background": bool(props.get("AudioDialogueLevelerBackgroundReduction")),
            "gain": _float(props.get("AudioDialogueLevelerOutputGain"), 0.0),
        },
    }


def _clip(item, fps, cache):
    uid = _call(item, "GetUniqueId", default="")
    start, end = _call(item, "GetStart"), _call(item, "GetEnd")
    if not uid or start is None or end is None:
        return None
    media = _media(item, cache)
    name = str(_call(item, "GetName", default="") or "")
    if media is None and name.startswith(TRANSITION_PREFIX):
        # An audio crossfade sits in the track's item list like a clip.
        return {"id": uid, "name": name, "start": int(start), "end": int(end), "transition": True}
    src_fps = (media or {}).get("fps") or fps
    clip = {
        "id": uid,
        "name": name,
        "start": int(start),
        "end": int(end),
        "color": CLIP_COLORS.get(_call(item, "GetClipColor", default=""), ""),
        # Seconds into the file where the clip starts.
        "offset": (_float(_call(item, "GetSourceStartFrame"), 0.0) or 0.0) / src_fps if src_fps else 0.0,
        "media": media,
        "channels": None,
        "peaks": None,
    }
    if media is not None:
        _play(clip, item, media)
    clip.update(_levels(item))
    return clip


def read_timeline(controller, media_cache):
    """The open timeline's audio: (data for the view, {clip id: item}) -
    the items so the live read can refresh the selected ones."""
    timeline = get_timeline(controller)
    fps = _float(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    start = int(timeline.GetStartFrame())
    tracks, items = [], {}
    for index in range(1, int(timeline.GetTrackCount("audio") or 0) + 1):
        clips = []
        for item in timeline.GetItemListInTrack("audio", index) or []:
            clip = _clip(item, fps, media_cache)
            if clip is not None:
                clips.append(clip)
                items[clip["id"]] = item
        tracks.append({
            "index": index,
            "name": str(_call(timeline, "GetTrackName", "audio", index, default="") or f"Audio {index}"),
            "kind": str(_call(timeline, "GetTrackSubType", "audio", index, default="") or ""),
            "enabled": bool(_call(timeline, "GetIsTrackEnabled", "audio", index, default=True)),
            "locked": bool(_call(timeline, "GetIsTrackLocked", "audio", index, default=False)),
            "clips": clips,
        })
    _read_keys(controller, timeline, fps, start, tracks, items, media_cache)
    link_effects(tracks)
    markers = [{"frame": start + int(frame), "color": marker_colors.HEX.get(info.get("color", ""), "#888888"),
                "name": str(info.get("name") or ""), "beat": info.get("customData") == BEAT_DATA,
                "duration": max(1, int(_float(info.get("duration"), 1.0) or 1))}
               for frame, info in marker_colors.numeric_markers(_call(timeline, "GetMarkers", default={}))]
    # Where the picture ends - what music is most often fitted to - and where its
    # cuts are: each video clip's first frame, on any track (the DAW scores to them).
    video_end, cuts = 0, set()
    for index in range(1, int(_call(timeline, "GetTrackCount", "video", default=0) or 0) + 1):
        for item in _call(timeline, "GetItemListInTrack", "video", index, default=[]) or []:
            video_end = max(video_end, int(_call(item, "GetEnd", default=0) or 0))
            first = _call(item, "GetStart", default=None)
            if first is not None and len(cuts) < MOST_CUTS:
                cuts.add(int(first))
    cuts.discard(start)                               # the timeline's start isn't a cut
    resolve = getattr(controller, "resolve", None)
    project = controller.current_project()
    data = {
        "id": str(_call(timeline, "GetUniqueId", default="") or ""),
        # A processed WAV's sidecar lists the projects that use it (render.py).
        "project": str(_call(project, "GetUniqueId", default="") or "") if project is not None else "",
        "name": str(timeline.GetName() or ""),
        # Voice Isolation and the Dialogue Leveler are probably Studio's only.
        "studio": "Studio" in str(_call(resolve, "GetProductName", default="Studio") if resolve else "Studio"),
        "fps": fps,
        "drop_frame": str(_call(timeline, "GetSetting", "timelineDropFrameTimecode", default="0")) == "1",
        "start": start,
        "end": int(timeline.GetEndFrame()),
        "video_end": video_end or None,
        "cuts": sorted(cuts),
        "tracks": tracks,
        "markers": markers,
    }
    return data, items


# ----------------------------------------------------------- volume keys --
#
# See keys.py for what Resolve does with them. A curve applied in Buddy is a
# timeline of its own (named "<clip> (Buddy curve)", in the "Buddy Audio" bin,
# with Buddy's note in its Comments) holding the clip with the keys, placed as a
# nested clip on a free track at the clip's place; the clip itself stays, turned
# off. The nested clip's own volume is heard on top of the keys, so the Levels
# controls (and Match) work on it as on any clip.

def _timelines(project):
    """{name: Timeline} for the project's timelines."""
    out = {}
    for index in range(1, int(_call(project, "GetTimelineCount", default=0) or 0) + 1):
        tl = _call(project, "GetTimelineByIndex", index)
        if tl is not None:
            out[str(_call(tl, "GetName", default="") or "")] = tl
    return out


def _curve_of(item, timelines):
    """A Buddy curve clip's (its timeline, note, the clip in it, its media pool
    item) - or None for any other clip. timelines() gives _timelines()."""
    mpi = _call(item, "GetMediaPoolItem")
    name = str(_call(mpi, "GetName", default="") or "") if mpi is not None else ""
    if not K.is_curve_name(name):
        return None
    note = K.read_note(_call(mpi, "GetMetadata", "Comments", default=""))
    tl = timelines().get(name) if note else None
    inner = next(iter(_call(tl, "GetItemListInTrack", "audio", 1, default=[]) or []), None) if tl else None
    return None if inner is None else (tl, note, inner, mpi)


def _exported(controller, timeline):
    """parse_fcp7() of an FCP 7 XML export of the timeline ([] if Resolve won't).
    A few ms, and nothing in the project changes."""
    resolve = getattr(controller, "resolve", None)
    path = os.path.join(tempfile.gettempdir(), f"buddy_keys_{uuid.uuid4().hex[:10]}.xml")
    try:
        kind, none = getattr(resolve, "EXPORT_FCP_7_XML"), getattr(resolve, "EXPORT_NONE")
        if not _call(timeline, "Export", path, kind, none, default=False):
            return []
        with open(path, encoding="utf-8") as f:
            return K.parse_fcp7(f.read())
    except (AttributeError, OSError, ET.ParseError, ValueError):
        return []
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _read_keys(controller, timeline, fps, start, tracks, items, cache):
    """Each clip's "keys" (file-time keys, or None) and "curve" (Buddy's curve
    clips: {"original": the clip it stands in for, "edited": its keys changed in
    Resolve since}, else None). A curve clip also takes the media and offset of
    the clip in it, so its waveform draws."""
    project = controller.current_project()
    found = {}

    def timelines():
        if "all" not in found:
            found["all"] = _timelines(project) if project is not None else {}
        return found["all"]

    plain = []
    for track in tracks:
        for clip in track["clips"]:
            if clip.get("transition"):
                continue
            clip["keys"], clip["curve"] = None, None
            if clip["media"] is None:
                _read_curve(controller, clip, items[clip["id"]], timelines, fps, cache)
            else:
                plain.append(clip)
    if plain:
        keyed = K.match_keys(_exported(controller, timeline), plain, start)
        for clip in plain:
            clip["keys"] = keyed.get(clip["id"]) or None


def _read_curve(controller, clip, item, timelines, fps, cache):
    curve = _curve_of(item, timelines)
    if curve is None:
        return
    tl, note, inner, _mpi = curve
    media = _media(inner, cache)
    if media is None:
        return
    src_fps = media.get("fps") or fps
    inner_offset = (_float(_call(inner, "GetSourceStartFrame"), 0.0) or 0.0) / src_fps
    clip["media"] = media
    _play(clip, inner, media)            # the channels the clip in it plays, not the nested clip's mix
    # A nested clip's source start counts frames into its timeline: a trim.
    clip["offset"] = inner_offset + (_float(_call(item, "GetSourceStartFrame"), 0.0) or 0.0) / fps
    got = K.match_keys(_exported(controller, tl), [{
        "id": "inner", "name": str(_call(inner, "GetName", default="") or ""), "offset": inner_offset,
        "start": int(_call(inner, "GetStart", default=0)), "end": int(_call(inner, "GetEnd", default=0))}],
        int(_call(tl, "GetStartFrame", default=0))).get("inner", [])
    edited = not K.same_keys(K.expand(note["keys"], fps, inner_offset), got)
    clip["keys"] = (got if edited else note["keys"]) or None
    clip["curve"] = {"original": note["original"], "edited": edited}


def read_live(controller, fps, selected_items):
    """What changes by the second: {timeline id, playhead frame, the clip ids
    Resolve has selected, {id: levels} for the clips Buddy has selected}."""
    timeline = get_timeline(controller)
    timecode = _call(timeline, "GetCurrentTimecode", default="")
    selected = []
    for item in _call(timeline, "GetSelectedClips", default=[]) or []:
        uid = _call(item, "GetUniqueId", default="")
        if uid:
            selected.append(uid)
    return {
        "id": str(_call(timeline, "GetUniqueId", default="") or ""),
        "playhead": timecode_to_frames(timecode, fps) if timecode else None,
        "resolve_selection": selected,
        "levels": {uid: _levels(item) for uid, item in selected_items.items()},
    }


# ------------------------------------------------------------- writes --
#
# Each finds the clips afresh by id and reads its result back through fresh
# handles again, so what the page shows is what Resolve has - not what was
# asked for. Measured on a duplicate timeline: SetProperties and SetFades
# take 0-2 ms and don't hold the GIL, so they run on the page's worker.

def audio_items(controller):
    """(timeline, {clip id: item}) for the open timeline's audio - fresh handles."""
    timeline = get_timeline(controller)
    items = {}
    for index in range(1, int(timeline.GetTrackCount("audio") or 0) + 1):
        for item in timeline.GetItemListInTrack("audio", index) or []:
            uid = _call(item, "GetUniqueId", default="")
            if uid:
                items[uid] = item
    return timeline, items


def _fresh_levels(controller, ids):
    _timeline, items = audio_items(controller)
    return {uid: _levels(items[uid]) for uid in ids if uid in items}


def apply(controller, changes):
    """Sets clips' properties and fades. changes: {clip id: {"props": {Resolve
    key: value}, "fades": {"FadeIn": frames, "FadeOut": frames}}} (either part
    may be missing). Returns {"before": the same shape, holding what each clip
    had - apply() it to undo; "after": {id: levels}, read back; "failed": [ids]}.
    SetProperties is all or nothing per clip, so a clip either takes every
    property asked of it or none."""
    _timeline, items = audio_items(controller)
    before, failed = {}, []
    for uid, change in changes.items():
        item = items.get(uid)
        if item is None:
            failed.append(uid)
            continue
        old = {}
        props = change.get("props") or {}
        if props:
            now = _call(item, "GetProperties", default={}) or {}
            old["props"] = {key: now[key] for key in props if now.get(key) is not None}
            if not _call(item, "SetProperties", props, default=False):
                failed.append(uid)
                continue
        fades = change.get("fades")
        if fades:
            now = _call(item, "GetFades", default={}) or {}
            old["fades"] = {key: int(round(_float(now.get(key), 0.0))) for key in ("FadeIn", "FadeOut")}
            wanted = dict(old["fades"], **{key: int(round(value)) for key, value in fades.items()})
            if not _call(item, "SetFades", wanted, default=False):
                if old.get("props"):
                    _call(item, "SetProperties", old["props"])      # all of it, or none of it
                failed.append(uid)
                continue
        before[uid] = old
    return {"before": before, "after": _fresh_levels(controller, changes), "failed": failed}


def crossfade(controller, left_ids, kind, frames):
    """An audio crossfade over the cut at the end of each clip in left_ids,
    centred on it. Buddy can't take one off again - DeleteClips refuses a
    transition (measured) - so the page asks first. Returns (added, failed)."""
    _timeline, items = audio_items(controller)
    added, failed = 0, []
    for uid in left_ids:
        item = items.get(uid)
        done = item is not None and _call(item, "AddTransition", {
            "type": kind, "category": "audio", "position": "end", "alignment": "center", "duration": int(frames)})
        if done:
            added += 1
        else:
            failed.append(uid)
    return added, failed


# What a curve clip takes over from the clip it stands in for (and a new curve
# clip from the one it replaces): everything the Levels and Clean-up controls set.
_CARRIED = ("AudioPan", "AudioVoiceIsolationEnabled", "AudioVoiceIsolationAmount", "AudioDialogueLevelerEnabled",
            "AudioDialogueLevelerMode", "AudioDialogueLevelerReduceLoudDialogue",
            "AudioDialogueLevelerLiftSoftDialogue", "AudioDialogueLevelerBackgroundReduction",
            "AudioDialogueLevelerOutputGain")


def _outer(item):
    """A clip's settings that a curve clip carries on: {"props", "fades", "color"}."""
    props = _call(item, "GetProperties", default={}) or {}
    fades = _call(item, "GetFades", default={}) or {}
    return {
        "props": dict({key: props[key] for key in _CARRIED if props.get(key) is not None},
                      AudioVolume=_float(props.get("AudioVolume"), 0.0)),
        "fades": {key: int(round(_float(fades.get(key), 0.0))) for key in ("FadeIn", "FadeOut")},
        "color": str(_call(item, "GetClipColor", default="") or ""),
    }


def _set_outer(item, outer):
    """_outer()'s settings onto a clip: the volume and pan first (all a clip
    has for certain), then Voice Isolation and the Leveler as one - Resolve
    refuses a whole SetProperties with one key it doesn't take."""
    props = dict(outer["props"])
    base = {key: props.pop(key) for key in ("AudioVolume", "AudioPan") if key in props}
    _call(item, "SetProperties", base)
    if props:
        _call(item, "SetProperties", props)
    if any(outer.get("fades", {}).values()):
        _call(item, "SetFades", outer["fades"])
    if outer.get("color"):
        _call(item, "SetClipColor", outer["color"])


def _walk(folder):
    for clip in _call(folder, "GetClipList", default=[]) or []:
        yield folder, clip
    for sub in _call(folder, "GetSubFolderList", default=[]) or []:
        yield from _walk(sub)


def _folder_of(root, mpi):
    uid = _call(mpi, "GetUniqueId", default="")
    return next((folder for folder, clip in _walk(root) if _call(clip, "GetUniqueId", default="") == uid), root)


def _bin(media_pool):
    """Buddy's bin for curve timelines, made at the top of the media pool if it isn't there."""
    root = media_pool.GetRootFolder()
    for sub in _call(root, "GetSubFolderList", default=[]) or []:
        if _call(sub, "GetName", default="") == K.BIN_NAME:
            return sub
    made = _call(media_pool, "AddSubFolder", root, K.BIN_NAME)
    if made is None:
        raise ResolveConnectionError("Resolve didn't make Buddy's bin for curves.")
    return made


BUDDY_TRACK = "Buddy"


def _track_ids(timeline, index):
    return [_call(i, "GetUniqueId", default="") for i in _call(timeline, "GetItemListInTrack", "audio", index,
                                                                    default=[]) or []]


_NUMBERED = re.compile(r"(.*\S) (\d+)")


def _track_sig(timeline, index):
    """What tells a track apart from the others: its name and its clips. A name
    Resolve made ("Audio 3" - numbered by position, renumbered when a track goes
    in above it) counts only as a default, not by its number."""
    name = str(_call(timeline, "GetTrackName", "audio", index, default="") or "")
    numbered = _NUMBERED.fullmatch(name)
    label = ("default", numbered.group(1)) if numbered and int(numbered.group(2)) == index else ("named", name)
    return label, tuple(_track_ids(timeline, index))


def _new_track(before, after, wanted):
    """Which of `after` (the tracks' _track_sig()s once one was added) is the
    new one: a default-named, empty track whose removal gives `before` back.
    `wanted` if it can be (the insert asked for, as Resolve 21.1 does it), else
    the last that can (appended); None if none can. When several can - the
    tracks from `wanted` down are all empty and default-named - they can't be
    told apart, and `wanted` is taken: no clip or name of anyone's is changed."""
    fresh = [p for p in range(1, len(after) + 1)
             if after[p - 1][0][0] == "default" and not after[p - 1][1] and after[:p - 1] + after[p:] == before]
    if wanted in fresh:
        return wanted
    return fresh[-1] if fresh else None


def _buddy_track(timeline, below, start, end):
    """(index, added) - the audio track a clip of track `below` gets its curve
    clip on. Buddy's own tracks, all named BUDDY_TRACK, sit in a run directly
    under the track whose clips they stand in for (the user's choice: not
    whatever track below has a gap there, which could be the music). The
    first of that run with nothing in [start, end), on and unlocked, is
    reused; if there's none, a new one goes in at the end of the run.

    Resolve 21.1 inserts a track where asked with AddTrack("audio",
    {"audioType", "index"}) - undocumented; the tracks under it move down
    (measured). A Resolve that appends it instead gets it at the bottom,
    which is checked by the tracks' names and clips (_new_track), so no track
    of the user's is ever the one named Buddy.
    (index, added) with added True when a track was made; None if Resolve
    made none."""
    count = int(_call(timeline, "GetTrackCount", "audio", default=0) or 0)
    index = below + 1
    while index <= count and _call(timeline, "GetTrackName", "audio", index, default="") == BUDDY_TRACK:
        usable = _call(timeline, "GetIsTrackEnabled", "audio", index, default=True) \
            and not _call(timeline, "GetIsTrackLocked", "audio", index, default=False)
        if usable and not any(_call(i, "GetStart", default=0) < end and _call(i, "GetEnd", default=0) > start
                              for i in _call(timeline, "GetItemListInTrack", "audio", index, default=[]) or []):
            return index, False
        index += 1
    subtype = _call(timeline, "GetTrackSubType", "audio", below, default="mono") or "mono"
    before = [_track_sig(timeline, i) for i in range(1, count + 1)]
    if not _call(timeline, "AddTrack", "audio", {"audioType": subtype, "index": index}, default=False):
        return None
    if int(_call(timeline, "GetTrackCount", "audio", default=0) or 0) != count + 1:
        return None
    # Where it went: asked for `index`, a Resolve that appends puts it at the bottom.
    index = _new_track(before, [_track_sig(timeline, i) for i in range(1, count + 2)], index)
    if index is None:
        return None
    _call(timeline, "SetTrackName", "audio", index, BUDDY_TRACK)
    return index, True


def _drop_empty_buddy_track(timeline, index):
    """A Buddy track left with nothing on it goes (after a failed Apply, or a
    curve taken off) - never a track that isn't Buddy's, or has clips."""
    if _call(timeline, "GetTrackName", "audio", index, default="") == BUDDY_TRACK and not _track_ids(timeline, index):
        _call(timeline, "DeleteTrack", "audio", index)


def _place(media_pool, mpi, track, start, frames):
    """A timeline's media pool item placed as a nested clip, audio only, at
    record frame `start` of `track` of the current timeline - the placed item,
    or None. (On a spot that isn't empty Resolve places nothing.)"""
    placed = _call(media_pool, "AppendToTimeline", [{
        "mediaPoolItem": mpi, "startFrame": 0, "endFrame": frames, "mediaType": 2,
        "trackIndex": track, "recordFrame": start}], default=[]) or []
    item = placed[0] if placed else None
    if item is None or _call(item, "GetStart") != start or _call(item, "GetEnd") != start + frames:
        return None
    return item


def apply_curve(controller, clip_id, keys, fps, volume, outer=None):
    """Puts a volume curve on a clip - Resolve keyframes, in a one-clip timeline
    placed as a nested clip where the clip is (see keys.py).

    keys: the curve's own keys (file time), heard with the nested clip's
    `volume` on top. On a clip that's already a Buddy curve, the new one
    replaces it, on its track, taking over its settings; on any other clip it
    goes on a "Buddy" track directly under the clip's track (_buddy_track:
    one there with room, or a new one), the clip's settings come with it, and
    the clip is turned off. outer: _outer()'s settings to
    give it instead (an undone Remove).

    Everything is checked - the clip in the new timeline is the same file,
    from the same frame, as long - and taken back if anything fails. Returns
    {"id": the curve clip, "original": the clip it stands in for, "replaced":
    the curve clip it replaced or None, "track": the audio track it's on}."""
    project = controller.current_project()
    timeline, items = audio_items(controller)
    media_pool = project.GetMediaPool()
    item = items.get(clip_id)
    if item is None:
        raise ResolveConnectionError("That clip isn't on the open timeline any more.")
    kind, track = (_call(item, "GetTrackTypeAndIndex", default=["audio", 1]) or ["audio", 1])[:2]
    start, end = int(item.GetStart()), int(item.GetEnd())
    frames = end - start
    found = {}

    def timelines():
        if "all" not in found:
            found["all"] = _timelines(project)
        return found["all"]

    curve = _curve_of(item, timelines)
    source = curve[2] if curve else item
    original = curve[1]["original"] if curve else clip_id
    mpi = _call(source, "GetMediaPoolItem")
    props = (_call(mpi, "GetClipProperty", default={}) or {}) if mpi is not None else {}
    path = str(props.get("File Path") or "")
    if not path:
        raise ResolveConnectionError("That clip has no audio file Buddy can put a curve on.")
    media_fps = _float(props.get("FPS"), fps) or fps
    media_frame = K.frame_duration(media_fps)
    first = int(round(_float(_call(source, "GetSourceStartFrame"), 0.0) or 0.0))
    source_in = first * media_frame
    if curve:
        # A trimmed curve clip starts that many of its timeline's frames in.
        source_in += int(round(_float(_call(item, "GetSourceStartFrame"), 0.0) or 0.0)) * K.frame_duration(fps)
        first = int(round(source_in / media_frame))
    media_start = timecode_to_frames(str(props.get("Start TC") or "00:00:00:00"), media_fps) * media_frame
    origin = float(source_in)
    before = _outer(item)
    carried = outer or before
    carried = dict(carried, props=dict(carried["props"], AudioVolume=float(volume)))
    name = K.curve_name(str(_call(source, "GetName", default="") or "Clip"), timelines())
    fcpxml = K.nested_fcpxml(name, path, fps, int(timeline.GetStartFrame()), frames, media_start, source_in,
                             int(_float(props.get("Audio Ch"), 1) or 1), K.expand(keys, fps, origin),
                             media_frame=media_frame)
    xml_path = os.path.join(tempfile.gettempdir(), f"buddy_curve_{uuid.uuid4().hex[:10]}.fcpxml")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(fcpxml)
    bin_ = _bin(media_pool)
    folder_before = _call(media_pool, "GetCurrentFolder")
    try:
        media_pool.SetCurrentFolder(bin_)
        new_tl = _call(media_pool, "ImportTimelineFromFile", xml_path, {
            "timelineName": name, "importSourceClips": False,
            "sourceClipsFolders": [_folder_of(media_pool.GetRootFolder(), mpi)]})
    finally:
        # The import makes the new timeline the current one, and it goes in the current bin.
        project.SetCurrentTimeline(timeline)
        if folder_before is not None:
            media_pool.SetCurrentFolder(folder_before)
        try:
            os.remove(xml_path)
        except OSError:
            pass
    if new_tl is None:
        raise ResolveConnectionError("Resolve didn't take the curve.")
    placed, disabled, removed, added, spot = None, False, False, False, None
    try:
        inner = next(iter(_call(new_tl, "GetItemListInTrack", "audio", 1, default=[]) or []), None)
        if inner is None or _call(_call(inner, "GetMediaPoolItem"), "GetUniqueId") != _call(mpi, "GetUniqueId") \
                or int(_call(inner, "GetSourceStartFrame", default=-1)) != first \
                or int(inner.GetEnd()) - int(inner.GetStart()) != frames:
            raise ResolveConnectionError("Resolve didn't line the curve up with the clip, so Buddy took it back.")
        got = K.match_keys(_exported(controller, new_tl), [{
            "id": "inner", "name": str(_call(inner, "GetName", default="") or ""), "offset": origin,
            "start": int(inner.GetStart()), "end": int(inner.GetEnd())}], int(_call(new_tl, "GetStartFrame", default=0)))
        if not K.same_keys(K.expand(keys, fps, origin), got.get("inner", [])):
            raise ResolveConnectionError("Resolve didn't keep the curve's keys where Buddy put them, so Buddy took it back.")
        new_mpi = next((c for c in _call(bin_, "GetClipList", default=[]) or []
                        if _call(c, "GetName", default="") == name), None)
        if new_mpi is None or not _call(new_mpi, "SetMetadata", {"Comments": K.note(keys, original)}, default=False):
            raise ResolveConnectionError("Resolve didn't keep Buddy's note on the curve.")
        if curve:
            if not _call(timeline, "DeleteClips", [item], default=False):
                raise ResolveConnectionError("Resolve didn't make room for the new curve.")
            removed = True
            spot = int(track)
        else:
            got_track = _buddy_track(timeline, int(track), start, end)
            if got_track is None:
                raise ResolveConnectionError("Resolve didn't add a track for the curve.")
            spot, added = got_track
        placed = _place(media_pool, new_mpi, spot, start, frames)
        if placed is None:
            raise ResolveConnectionError("Resolve didn't place the curve on the timeline.")
        _set_outer(placed, carried)
        if not curve:
            if not _call(item, "SetClipEnabled", False, default=False):
                raise ResolveConnectionError("Resolve didn't turn the clip off under its curve.")
            disabled = True
    except Exception:
        if placed is not None:
            _call(timeline, "DeleteClips", [placed])
        if removed:                       # the curve clip it was to replace goes back
            back = _place(media_pool, curve[3], int(track), start, frames)
            if back is not None:
                _set_outer(back, before)
        if disabled:
            _call(item, "SetClipEnabled", True)
        if added:                         # the Buddy track made for it goes too
            _drop_empty_buddy_track(timeline, spot)
        _call(media_pool, "DeleteTimelines", [new_tl])
        raise
    if curve:
        _call(media_pool, "DeleteTimelines", [curve[0]])
    return {"id": str(_call(placed, "GetUniqueId", default="") or ""), "original": original,
            "replaced": clip_id if curve else None, "track": spot}


def remove_curve(controller, clip_id):
    """Takes a Buddy curve clip off: the clip it stood in for is turned back on,
    the curve clip and its timeline go. Returns what apply_curve() needs to put
    it back: {"original", "keys", "volume", "outer"}."""
    project = controller.current_project()
    timeline, items = audio_items(controller)
    media_pool = project.GetMediaPool()
    item = items.get(clip_id)
    curve = _curve_of(item, lambda: _timelines(project)) if item is not None else None
    if curve is None:
        raise ResolveConnectionError("That clip isn't a Buddy curve any more.")
    tl, note, _inner, _mpi = curve
    outer = _outer(item)
    original = items.get(note["original"])
    track = (_call(item, "GetTrackTypeAndIndex", default=["audio", 0]) or ["audio", 0])[1]
    # The clip it stood in for is heard again before the curve clip goes -
    # never neither of them.
    if original is not None and not _call(original, "SetClipEnabled", True, default=False):
        raise ResolveConnectionError("Resolve didn't turn the original clip back on, so the curve clip stays.")
    if not _call(timeline, "DeleteClips", [item], default=False):
        if original is not None:
            _call(original, "SetClipEnabled", False)
        raise ResolveConnectionError("Resolve didn't take the curve clip off.")
    _call(media_pool, "DeleteTimelines", [tl])
    if track:
        _drop_empty_buddy_track(timeline, int(track))
    return {"original": note["original"] if original is not None else "", "keys": note["keys"],
            "volume": outer["props"]["AudioVolume"], "outer": outer}


# ---------------------------------------------------------------- effects --
#
# Effects are Buddy's own processing (dsp.py), baked into a WAV beside the
# media (render.py). The WAV goes in the "Buddy Audio" bin and on a Buddy track
# under the clip - at the clip's place, from the same moment of the recording
# - and the clip itself stays, turned off: as with a curve. A processed clip is
# known by its WAV's sidecar; the clip it stands in for is the turned-off clip
# of the source file over the same frames on a track above it (link_effects).

def _fx_of(media):
    """What a processed WAV's sidecar says, for the page: {"chain", "source",
    "file_start", "channels_used", "fit"} - or None for any other file.
    "fit": fit.py's plan, for fitted music (None for effects) - a WAV that
    isn't one stretch of its source, so Effects take it as a file of its own."""
    path = (media or {}).get("path") or ""
    if not path.lower().endswith(".wav"):
        return None
    side = render.read_sidecar(path)
    if not side or not isinstance(side.get("source"), dict):
        return None
    fit = side.get("fit") if isinstance(side.get("fit"), dict) else None
    return {"chain": side.get("chain") or [], "source": str(side["source"].get("path") or ""),
            "file_start": float(side.get("file_start") or 0.0), "channels_used": side.get("channels_used"),
            "fit": fit}


def link_effects(tracks):
    """Each processed clip's "fx": {"chain", "original": the clip it stands in
    for, or None} - and None on every other clip; each fitted one's "fit"
    likewise ({"original", "length_s", "song_s", "removed_s", "bars"}). The
    original: turned off, the WAV's source file, on a track above, the
    nearest one - over the same frames (a fitted one: from the same frame;
    it's as long as the song was)."""
    for index, track in enumerate(tracks):
        for clip in track["clips"]:
            if clip.get("transition"):
                continue
            fx = (clip.get("media") or {}).get("fx") if not clip.get("curve") else None
            clip["fx"] = clip["fit"] = None
            if not fx:
                continue
            fitted = fx.get("fit")
            source = os.path.normcase(os.path.abspath(fx["source"])) if fx["source"] else ""
            original = None
            for above in reversed(tracks[:index]):
                original = next((c["id"] for c in above["clips"] if not c.get("transition") and not c.get("enabled", True)
                                 and c["start"] == clip["start"] and (fitted or c["end"] == clip["end"])
                                 and c.get("media")
                                 and os.path.normcase(os.path.abspath(c["media"]["path"])) == source), None)
                if original:
                    break
            if fitted:
                clip["fit"] = {"original": original, "length_s": fitted.get("length_s"), "song_s": fitted.get("song_s"),
                               "bars": fitted.get("bars"), "times": fitted.get("times"),
                               "longer": (fitted.get("a") or 0) > (fitted.get("b") or 0)}
            else:
                clip["fx"] = {"chain": fx["chain"], "original": original}


def _first_second(item, props):
    """Seconds into its file where a clip starts (GetSourceStartFrame is in the
    file's own frames, its "FPS")."""
    fps = _float(props.get("FPS")) or 24.0
    return (_float(_call(item, "GetSourceStartFrame"), 0.0) or 0.0) / fps


def _wav_item(media_pool, bin_, path):
    """The WAV's media pool item: the one in Buddy's bin, or imported there."""
    norm = os.path.normcase(os.path.abspath(path))
    for _folder, clip in _walk(bin_):
        found = str(_call(clip, "GetClipProperty", "File Path", default="") or "")
        if found and os.path.normcase(os.path.abspath(found)) == norm:
            return clip
    before = _call(media_pool, "GetCurrentFolder")
    try:
        media_pool.SetCurrentFolder(bin_)
        got = _call(media_pool, "ImportMedia", [path], default=[]) or []
    finally:
        if before is not None:
            media_pool.SetCurrentFolder(before)
    return got[0] if got else None


def effects_source(controller, clip_id):
    """What a clip's effects are made from: {"path": the recording, "start_s",
    "end_s" (seconds into it), "channels": the ones it plays, or None for all,
    "chain": the effects it has now ([] on a clip with none)} - for an already
    processed clip, its WAV's source, not the WAV."""
    timeline, items = audio_items(controller)
    item = items.get(clip_id)
    if item is None:
        raise ResolveConnectionError("That clip isn't on the open timeline any more.")
    fps = _float(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    mpi = _call(item, "GetMediaPoolItem")
    props = (_call(mpi, "GetClipProperty", default={}) or {}) if mpi is not None else {}
    path = str(props.get("File Path") or "")
    if not path:
        raise ResolveConnectionError("That clip has no audio file Buddy can process.")
    length = (int(item.GetEnd()) - int(item.GetStart())) / fps
    start = _first_second(item, props)
    fx = _fx_of({"path": path})
    if fx and not fx["fit"]:
        start += fx["file_start"]
        return {"path": fx["source"], "start_s": start, "end_s": start + length,
                "channels": fx["channels_used"], "chain": fx["chain"]}
    channels = render.channels_from_mapping(_call(item, "GetSourceAudioChannelMapping", default=""))
    if channels and channels == list(range(int(_float(props.get("Audio Ch"), 0) or 0))):
        channels = None
    return {"path": path, "start_s": start, "end_s": start + length, "channels": channels, "chain": []}


def apply_effects(controller, clip_id, wav):
    """Puts render.render()'s WAV (`wav`: its result, plus "source": the
    recording it was made from) where the clip is. On a clip that's already
    processed the new WAV replaces it, on its track, taking over its settings;
    on any other clip it goes on a Buddy track directly under the clip's
    (_buddy_track), the clip's volume, pan, fades and clean-up come with it,
    and the clip is turned off. Checked - same place, same length - and taken
    back if anything fails. Returns {"id": the processed clip, "replaced": the
    clip it replaced or None, "track"}."""
    project = controller.current_project()
    timeline, items = audio_items(controller)
    media_pool = project.GetMediaPool()
    item = items.get(clip_id)
    if item is None:
        raise ResolveConnectionError("That clip isn't on the open timeline any more.")
    fps = _float(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    source = effects_source(controller, clip_id)
    if os.path.normcase(os.path.abspath(source["path"])) != os.path.normcase(os.path.abspath(wav["source"])):
        raise ResolveConnectionError("That clip has changed since its audio was processed – try again.")
    old_mpi = _call(item, "GetMediaPoolItem")
    had = _fx_of({"path": str(_call(old_mpi, "GetClipProperty", "File Path", default="") or "")})
    replacing = had is not None and not had["fit"]
    kind, track = (_call(item, "GetTrackTypeAndIndex", default=["audio", 1]) or ["audio", 1])[:2]
    start, end = int(item.GetStart()), int(item.GetEnd())
    old_first = int(round(_float(_call(item, "GetSourceStartFrame"), 0.0) or 0.0))
    new_mpi = _wav_item(media_pool, _bin(media_pool), wav["path"])
    if new_mpi is None:
        raise ResolveConnectionError("Resolve didn't import the processed audio.")
    wav_fps = _float(_call(new_mpi, "GetClipProperty", "FPS", default=None)) or fps
    first = int(round((source["start_s"] - wav["file_start"]) * wav_fps))
    length = int(round((end - start) * wav_fps / fps))
    before = _outer(item)
    placed, disabled, removed, added, spot = None, False, False, False, None
    try:
        if replacing:
            if not _call(timeline, "DeleteClips", [item], default=False):
                raise ResolveConnectionError("Resolve didn't make room for the processed audio.")
            removed, spot = True, int(track)
        else:
            got_track = _buddy_track(timeline, int(track), start, end)
            if got_track is None:
                raise ResolveConnectionError("Resolve didn't add a track for the processed audio.")
            spot, added = got_track
        got = _call(media_pool, "AppendToTimeline", [{
            "mediaPoolItem": new_mpi, "startFrame": first, "endFrame": first + length, "mediaType": 2,
            "trackIndex": spot, "recordFrame": start}], default=[]) or []
        placed = got[0] if got else None
        if placed is None or _call(placed, "GetStart") != start or _call(placed, "GetEnd") != end:
            raise ResolveConnectionError("Resolve didn't place the processed audio where the clip is.")
        _set_outer(placed, before)
        if not replacing:
            if not _call(item, "SetClipEnabled", False, default=False):
                raise ResolveConnectionError("Resolve didn't turn the clip off under its processed audio.")
            disabled = True
    except Exception:
        if placed is not None:
            _call(timeline, "DeleteClips", [placed])
        if removed:                       # the processed clip it was to replace goes back
            back = _call(media_pool, "AppendToTimeline", [{
                "mediaPoolItem": old_mpi, "startFrame": old_first, "endFrame": old_first + (end - start),
                "mediaType": 2, "trackIndex": int(track), "recordFrame": start}], default=[]) or []
            if back:
                _set_outer(back[0], before)
        if disabled:
            _call(item, "SetClipEnabled", True)
        if added:
            _drop_empty_buddy_track(timeline, spot)
        raise
    return {"id": str(_call(placed, "GetUniqueId", default="") or ""), "replaced": clip_id if replacing else None,
            "track": spot}


def apply_fit(controller, clip_id, wav, frames):
    """Puts fit.save()'s WAV (`wav`: its result) on a Buddy track under the
    clip (_buddy_track: one with room for it, or a new one), from the clip's
    first frame for `frames` timeline frames - the length it was fitted to -
    and turns the clip off. The clip's volume, pan, clean-up, colour and fade
    in come with it; not its fade out - the song ends on its own now. Checked
    and taken back if anything fails. Returns {"id", "track"}."""
    project = controller.current_project()
    timeline, items = audio_items(controller)
    media_pool = project.GetMediaPool()
    item = items.get(clip_id)
    if item is None:
        raise ResolveConnectionError("That clip isn't on the open timeline any more.")
    path = str(_call(_call(item, "GetMediaPoolItem"), "GetClipProperty", "File Path", default="") or "")
    if os.path.normcase(os.path.abspath(path or ".")) != os.path.normcase(os.path.abspath(wav["source"])):
        raise ResolveConnectionError("That clip has changed since its music was fitted – try again.")
    fps = _float(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    track = (_call(item, "GetTrackTypeAndIndex", default=["audio", 1]) or ["audio", 1])[1]
    start = int(item.GetStart())
    new_mpi = _wav_item(media_pool, _bin(media_pool), wav["path"])
    if new_mpi is None:
        raise ResolveConnectionError("Resolve didn't import the fitted music.")
    wav_fps = _float(_call(new_mpi, "GetClipProperty", "FPS", default=None)) or fps
    length = int(round(frames * wav_fps / fps))
    before = _outer(item)
    before["fades"] = {"FadeIn": before["fades"].get("FadeIn", 0), "FadeOut": 0}
    placed, disabled, added, spot = None, False, False, None
    try:
        got_track = _buddy_track(timeline, int(track), start, start + frames)
        if got_track is None:
            raise ResolveConnectionError("Resolve didn't add a track for the fitted music.")
        spot, added = got_track
        got = _call(media_pool, "AppendToTimeline", [{
            "mediaPoolItem": new_mpi, "startFrame": 0, "endFrame": length, "mediaType": 2,
            "trackIndex": spot, "recordFrame": start}], default=[]) or []
        placed = got[0] if got else None
        if placed is None or _call(placed, "GetStart") != start or _call(placed, "GetEnd") != start + frames:
            raise ResolveConnectionError("Resolve didn't place the fitted music where the clip starts.")
        _set_outer(placed, before)
        if not _call(item, "SetClipEnabled", False, default=False):
            raise ResolveConnectionError("Resolve didn't turn the clip off under its fitted music.")
        disabled = True
    except Exception:
        if placed is not None:
            _call(timeline, "DeleteClips", [placed])
        if disabled:
            _call(item, "SetClipEnabled", True)
        if added:
            _drop_empty_buddy_track(timeline, spot)
        raise
    return {"id": str(_call(placed, "GetUniqueId", default="") or ""), "track": spot}


def remove_effects(controller, clip_id, original_id):
    """Takes a processed clip off: the clip it stood in for (`original_id`,
    link_effects') is turned back on, the processed one goes, and its Buddy
    track if that's left empty. The WAV stays in the bin - Tidy up lets it
    go. Returns {"original", "chain"} for an Undo to put it back."""
    timeline, items = audio_items(controller)
    item, original = items.get(clip_id), items.get(original_id or "")
    if item is None:
        raise ResolveConnectionError("That clip isn't on the open timeline any more.")
    fx = _fx_of({"path": str(_call(_call(item, "GetMediaPoolItem"), "GetClipProperty", "File Path", default="") or "")})
    if fx is None:
        raise ResolveConnectionError("That clip isn't Buddy's processed audio.")
    if original is None:
        raise ResolveConnectionError("The clip it stands in for isn't on the timeline any more, so Buddy left it.")
    track = (_call(item, "GetTrackTypeAndIndex", default=["audio", 0]) or ["audio", 0])[1]
    if not _call(original, "SetClipEnabled", True, default=False):     # never neither of them heard
        raise ResolveConnectionError("Resolve didn't turn the original clip back on, so the processed audio stays.")
    if not _call(timeline, "DeleteClips", [item], default=False):
        _call(original, "SetClipEnabled", False)
        raise ResolveConnectionError("Resolve didn't take the processed audio off.")
    if track:
        _drop_empty_buddy_track(timeline, int(track))
    return {"original": original_id, "chain": fx["chain"]}


# ---------------------------------------------------------- sound design --
#
# A sound Sound Design made (a riser, an impact, a whoosh: a WAV with a
# sidecar that says so - no "source", so it's never taken for processed
# audio) goes on a Buddy track under the music it was made for, as any other
# clip: nothing is turned off under it.

def place_sound(controller, path, frame, seconds, below):
    """Puts the WAV at `path` (`seconds` long) on a Buddy track under audio
    track `below` (_buddy_track), its first sample at timeline frame `frame`.
    A sound that would start before the timeline does has its head cut off
    there - its moment still lands where it should. Checked, and taken back
    if Resolve didn't put it there. Returns {"id", "track"}."""
    project = controller.current_project()
    timeline = get_timeline(controller)
    media_pool = project.GetMediaPool()
    fps = _float(timeline.GetSetting("timelineFrameRate"), 24.0) or 24.0
    first_frame = int(_call(timeline, "GetStartFrame", default=0) or 0)
    head = max(0, first_frame - int(frame))
    start, end = int(frame) + head, int(frame) + int(round(seconds * fps))
    if end - start < 1:
        raise ResolveConnectionError("The sound would end before the timeline starts.")
    new_mpi = _wav_item(media_pool, _bin(media_pool), path)
    if new_mpi is None:
        raise ResolveConnectionError("Resolve didn't import the sound.")
    wav_fps = _float(_call(new_mpi, "GetClipProperty", "FPS", default=None)) or fps
    got_track = _buddy_track(timeline, max(1, int(below)), start, end)
    if got_track is None:
        raise ResolveConnectionError("Resolve didn't add a track for the sound.")
    spot, added = got_track
    placed = None
    try:
        got = _call(media_pool, "AppendToTimeline", [{
            "mediaPoolItem": new_mpi, "startFrame": int(round(head * wav_fps / fps)),
            "endFrame": int(round((end - int(frame)) * wav_fps / fps)), "mediaType": 2,
            "trackIndex": spot, "recordFrame": start}], default=[]) or []
        placed = got[0] if got else None
        if placed is None or _call(placed, "GetStart") != start or abs((_call(placed, "GetEnd") or 0) - end) > 1:
            raise ResolveConnectionError("Resolve didn't place the sound where it belongs.")
    except Exception:
        if placed is not None:
            _call(timeline, "DeleteClips", [placed])
        if added:
            _drop_empty_buddy_track(timeline, spot)
        raise
    return {"id": str(_call(placed, "GetUniqueId", default="") or ""), "track": spot}


def remove_sound(controller, clip_id):
    """Takes a placed sound off (Undo), and its Buddy track if that's left
    empty. The WAV stays in the bin - Tidy up lets it go."""
    timeline, items = audio_items(controller)
    item = items.get(clip_id)
    if item is None:
        raise ResolveConnectionError("The sound isn't on the open timeline any more.")
    track = (_call(item, "GetTrackTypeAndIndex", default=["audio", 0]) or ["audio", 0])[1]
    if not _call(timeline, "DeleteClips", [item], default=False):
        raise ResolveConnectionError("Resolve didn't take the sound off.")
    if track:
        _drop_empty_buddy_track(timeline, int(track))
    return {"id": clip_id}


# ---------------------------------------------------------- beat markers --
# Timeline markers on a music clip's beats (beats.py finds them). Buddy's own
# carry BEAT_DATA as their custom data, so Clear takes off only those - never
# a marker of anyone's.

BEAT_DATA = "buddy-beat"


def add_beat_markers(controller, marks):
    """marks: [{"frame" (from the timeline's start), "color", "name", "note"}].
    A frame that has a marker already is passed over (Resolve refuses it).
    Returns the frames added."""
    timeline = get_timeline(controller)
    added = []
    for mark in marks:
        frame = int(mark["frame"])
        if _call(timeline, "AddMarker", frame, mark["color"], mark["name"] or "Beat", mark.get("note", ""), 1,
                 BEAT_DATA, default=False):
            added.append(frame)
    return added


def remove_beat_markers(controller, frames=None):
    """Buddy's beat markers off the timeline: those at `frames`, or all of
    them. Returns what was removed, as add_beat_markers() takes it back."""
    timeline = get_timeline(controller)
    wanted = None if frames is None else {int(f) for f in frames}
    removed = []
    for frame, info in marker_colors.numeric_markers(_call(timeline, "GetMarkers", default={})):
        frame = int(frame)
        if info.get("customData") != BEAT_DATA or (wanted is not None and frame not in wanted):
            continue
        if _call(timeline, "DeleteMarkerAtFrame", frame, default=False):
            removed.append({"frame": frame, "color": str(info.get("color") or "Blue"),
                            "name": str(info.get("name") or ""), "note": str(info.get("note") or "")})
    return removed


# --------------------------------------------------------------- Tidy up --
# What Buddy made that nothing uses any more: curve timelines in the "Buddy
# Audio" bin no timeline has on it (a curve clip deleted in Resolve leaves its
# timeline behind), processed WAVs likewise, and this project's WAVs on disk
# that are gone from its media pool. Resolve's "Usage" clip property says how
# often a clip is on a timeline - a nested one too - and it's live (measured
# on 21.1: 1 while a curve clip is placed, 0 the moment it's deleted).

def _usage(mpi):
    try:
        return int(_call(mpi, "GetClipProperty", "Usage", default=0) or 0)
    except (TypeError, ValueError):
        return 0


def _find_bin(media_pool):
    root = media_pool.GetRootFolder()
    return next((s for s in _call(root, "GetSubFolderList", default=[]) or []
                 if _call(s, "GetName", default="") == K.BIN_NAME), None)


def _scan(project):
    """(curves, wavs, files, timelines): the unused curve timelines' and WAVs'
    media pool items and on-disk orphans, as the Tidy up list's entries."""
    media_pool = project.GetMediaPool()
    pid = str(_call(project, "GetUniqueId", default="") or "")
    bin_ = _find_bin(media_pool)
    timelines = _timelines(project)
    curves, wavs = [], []
    for _folder, clip in _walk(bin_) if bin_ is not None else []:
        props = _call(clip, "GetClipProperty", default={}) or {}
        name = str(props.get("Clip Name") or _call(clip, "GetName", default="") or "")
        path = str(props.get("File Path") or "")
        # Buddy's own only: its note in the Comments, as _curve_of() asks - a
        # timeline someone named "... (Buddy curve)" and filed here isn't Buddy's to delete.
        if props.get("Type") == "Timeline" and K.is_curve_name(name) and name in timelines \
                and K.read_note(_call(clip, "GetMetadata", "Comments", default="")) is not None:
            curves.append({"mpi": clip, "name": name, "usage": _usage(clip), "timeline": timelines[name]})
        elif path.lower().endswith(".wav") and render.read_sidecar(path) is not None:
            wavs.append({"mpi": clip, "name": name, "path": path, "usage": _usage(clip)})
    unused_curves = [c for c in curves if c["usage"] <= 0]
    # A WAV used only inside an unused curve timeline goes with it.
    inside = {}
    for curve in unused_curves:
        tl = curve["timeline"]
        for index in range(1, int(_call(tl, "GetTrackCount", "audio", default=0) or 0) + 1):
            for item in _call(tl, "GetItemListInTrack", "audio", index, default=[]) or []:
                uid = _call(_call(item, "GetMediaPoolItem"), "GetUniqueId", default="")
                inside[uid] = inside.get(uid, 0) + 1
    for wav in wavs:
        wav["usage"] -= inside.get(_call(wav["mpi"], "GetUniqueId", default=""), 0)
    # On disk: this project's WAVs beside its media (or in the fallback) that
    # aren't in its media pool any more.
    in_pool, folders = {os.path.normcase(w["path"]) for w in wavs}, {render.FALLBACK_DIR}
    for _folder, clip in _walk(media_pool.GetRootFolder()):
        path = str(_call(clip, "GetClipProperty", "File Path", default="") or "")
        if path:
            folders.add(os.path.join(os.path.dirname(path), render.FOLDER_NAME))
            in_pool.add(os.path.normcase(path))
    files = [{"path": path, "name": os.path.basename(path)}
             for folder in sorted(folders) for path, side in render.buddy_wavs_in(folder).items()
             if pid and pid in (side.get("projects") or []) and os.path.normcase(path) not in in_pool]
    return unused_curves, [w for w in wavs if w["usage"] <= 0], files, pid


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def tidy_scan(controller):
    """What Tidy up would remove: {"items": [{"id", "kind" ("curve" | "wav" |
    "file"), "name", "size" (bytes, 0 for a timeline), "path"}]}. Reads only."""
    curves, wavs, files, _pid = _scan(controller.current_project())
    items = [{"id": "curve:" + str(_call(c["mpi"], "GetUniqueId", default="")), "kind": "curve",
              "name": c["name"], "size": 0, "path": ""} for c in curves]
    items += [{"id": "wav:" + str(_call(w["mpi"], "GetUniqueId", default="")), "kind": "wav",
               "name": w["name"], "size": _size(w["path"]), "path": w["path"]} for w in wavs]
    items += [{"id": "file:" + f["path"], "kind": "file", "name": f["name"], "size": _size(f["path"]),
               "path": f["path"]} for f in files]
    return {"items": items}


def tidy_remove(controller, ids):
    """Removes what's asked for of what tidy_scan() lists - scanned again first,
    so nothing that got used meanwhile goes. Curve timelines are deleted from
    the project, then WAVs leave the media pool (a WAV that was only in those
    timelines is free now). A WAV file no other project has is handed back to
    go to the Recycle Bin - that's the page's to do, on the GUI thread
    (core/recycle.py). Returns {"removed": ids, "failed": ids, "recycle":
    [paths]}."""
    wanted = set(ids or [])
    project = controller.current_project()
    media_pool = project.GetMediaPool()
    removed, failed, recycle = [], [], []
    curves, _wavs, _files, pid = _scan(project)
    for curve in curves:
        cid = "curve:" + str(_call(curve["mpi"], "GetUniqueId", default=""))
        if cid in wanted:
            (removed if _call(media_pool, "DeleteTimelines", [curve["timeline"]], default=False) else failed).append(cid)
    _curves, wavs, files, pid = _scan(project)          # again: the curves' WAVs may be free now
    for wav in wavs:
        wid = "wav:" + str(_call(wav["mpi"], "GetUniqueId", default=""))
        if wid not in wanted:
            continue
        if not _call(media_pool, "DeleteClips", [wav["mpi"]], default=False):
            failed.append(wid)
            continue
        removed.append(wid)
        if render.release(wav["path"], pid):
            recycle += render.with_its_sidecar(wav["path"])
    for f in files:
        fid = "file:" + f["path"]
        if fid in wanted:
            removed.append(fid)
            if render.release(f["path"], pid):
                recycle += render.with_its_sidecar(f["path"])
    failed += [i for i in wanted if i not in removed and i not in failed]      # used again, or gone
    return {"removed": removed, "failed": failed, "recycle": recycle}


def seek(controller, timecode):
    """Moves Resolve's playhead. ~500 ms, holding the GIL: run it from
    resolve_child.py, never in Buddy itself."""
    if not get_timeline(controller).SetCurrentTimecode(timecode):
        raise ResolveConnectionError("Resolve didn't move the playhead - is the timeline playing?")


# ------------------------------------------------ in the reader's process --
#
# The page's reads go through the Resolve reader's process
# (core/resolve_reader.py): while the timeline plays Resolve holds every call,
# and one made inside Buddy froze it. These keep, in that process, what the
# page kept for them: the media cache and the last full read's items.
_HERE = {"media": {}, "items": {}}


def read_timeline_here(controller, fresh=False):
    """read_timeline()'s data for the view; its items stay here for
    read_live_here. fresh: forget the media read before (the page's Refresh)."""
    if fresh:
        _HERE["media"].clear()
    data, items = read_timeline(controller, _HERE["media"])
    _HERE["items"] = items
    return data


def read_live_here(controller, fps, ids):
    """read_live() for the clips `ids` the last full read here found."""
    items = _HERE["items"]
    return read_live(controller, fps, {uid: items[uid] for uid in ids if uid in items})
