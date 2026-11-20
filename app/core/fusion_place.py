"""Putting a generated Fusion comp (core/fusion_comp.py) on Resolve's timeline as a
new clip - the route Animation's titles proved on Studio 21.1 and Data Graphics uses
too:

  1. A Text+ from Buddy's template bin placed with AppendToTimeline - the call that
     lands on an exact frame and track.
  2. The comp imported over the clip's own (ImportFusionComp replaces a title clip's
     comp, and the Edit page renders an imported comp at once).
  3. The timeline read back: the clip is where it should be, carrying the comp's tag.

Safety: it only adds. A clip of someone else's in the span is never covered - the
topmost track free of them is used, or a new one - and only Buddy's own earlier
graphic of the same kind on that spot (its comp carries the tag) is replaced.

No Qt: it loads in the Resolve reader's process (core/resolve_reader_child.py).
"""

import math
import os
import tempfile

TEMPLATE_BIN = "Buddy Text+"
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "pages", "text_animator", "text_plus_template.drb")
_TEMPLATE_TYPES = ("Fusion Title", "Generator", "Title")


class PlaceError(RuntimeError):
    """Why a graphic couldn't be placed, in words for the person."""


def tagged(tag):
    """is_ours(item) for comps carrying `tag` in their CustomData."""
    def is_ours(item):
        try:
            for i in range(1, int(item.GetFusionCompCount() or 0) + 1):
                comp = item.GetFusionCompByIndex(i)
                if comp is not None and comp.GetData(tag):
                    return True
        except Exception:
            pass
        return False
    return is_ours


def in_the_way(items, start, end, is_ours):
    """The items overlapping [start, end): (Buddy's own, everyone else's)."""
    ours, theirs = [], []
    for item in items or []:
        try:
            s, e = int(item.GetStart()), int(item.GetEnd())
        except Exception:
            continue
        if s < end and start < e:
            (ours if is_ours(item) else theirs).append(item)
    return ours, theirs


def clock(frame, fps, start_frame=0):
    """A timeline frame as h:mm:ss.f from the timeline's start - for messages."""
    seconds = max(0.0, (frame - start_frame) / float(fps or 24))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def pick_track(timeline, start, end, is_ours):
    """The track a new graphic goes on: the topmost video track with nothing of anyone
    else's in the span, else a new track above them all."""
    count = int(timeline.GetTrackCount("video") or 0)
    for track in range(count, 0, -1):
        _ours, theirs = in_the_way(timeline.GetItemListInTrack("video", track), start, end, is_ours)
        if not theirs:
            return track
    return count + 1


def source_frames_for(length, source_fps, timeline_fps):
    """How many of the template's own frames to place so it lasts `length` timeline
    frames (AppendToTimeline counts the SOURCE clip's frames and Resolve rounds the
    timeline length down - subtitle_engine.source_frames_for has the measurements)."""
    if not source_fps or not timeline_fps or abs(source_fps - timeline_fps) < 1e-6:
        return max(1, int(length))
    ratio = timeline_fps / source_fps
    n = math.ceil(length / ratio - 1e-9)
    if math.floor(n * ratio + 1e-9) > length:
        n -= 1
    return max(1, n)


def timecode_frames(timecode, fps, drop=False):
    """An SMPTE timecode (HH:MM:SS:FF, or ;FF) as a frame count, or None."""
    try:
        hours, minutes, seconds, frames = (int(p) for p in str(timecode).replace(";", ":").split(":"))
    except (ValueError, TypeError):
        return None
    rate = int(round(fps))
    total = (hours * 3600 + minutes * 60 + seconds) * rate + frames
    if drop and rate in (30, 60):
        per_minute = 2 if rate == 30 else 4
        total_minutes = hours * 60 + minutes
        total -= per_minute * (total_minutes - total_minutes // 10)
    return total


def timeline_info(timeline):
    """((width, height), fps, drop frame) of a timeline."""
    def setting(name, fallback):
        try:
            return timeline.GetSetting(name) or fallback
        except Exception:
            return fallback
    width, height = int(setting("timelineResolutionWidth", 1920)), int(setting("timelineResolutionHeight", 1080))
    fps = float(setting("timelineFrameRate", 24))
    drop = str(setting("timelineDropFrameTimecode", "0")) in ("1", "True", "true")
    return (width, height), fps, drop


def playhead(timeline):
    """The playhead as a timeline frame number (timelines start at 01:00:00:00, not 0)."""
    _frame, fps, drop = timeline_info(timeline)
    return timecode_frames(timeline.GetCurrentTimecode(), fps, drop)


def _in_bin(root):
    for folder in root.GetSubFolderList() or []:
        if folder.GetName() == TEMPLATE_BIN:
            for clip in folder.GetClipList() or []:
                if (clip.GetClipProperty("Type") or "") in _TEMPLATE_TYPES:
                    return clip
    return None


def template(pool):
    """The Text+ in Buddy's template bin - imported there first if it's missing, with
    the selected bin put back after. None if it couldn't be found or added."""
    try:
        root = pool.GetRootFolder()
        found = _in_bin(root)
        if found is not None or not os.path.isfile(TEMPLATE_FILE):
            return found
        selected = pool.GetCurrentFolder()
        try:
            pool.SetCurrentFolder(root)
            imported = pool.ImportFolderFromFile(TEMPLATE_FILE)
        finally:
            if selected is not None:
                pool.SetCurrentFolder(selected)
        return _in_bin(root) if imported else None
    except Exception:
        return None


def _at(timeline, track, start):
    return next((it for it in timeline.GetItemListInTrack("video", track) or [] if int(it.GetStart()) == start), None)


def place_comp(project, timeline, write, tag, length, at=None, track=None, what="graphic", replace=True):
    """A new clip of `length` frames carrying a generated comp. write(comp_frames) ->
    (the comp's text, its id): called once the clip is on the timeline, with the
    frames it really got. at: the timeline frame it starts on (the playhead if None);
    track: the video track (the topmost free one if None); tag: the CustomData key
    the comp carries its id under; replace: Buddy's own earlier graphic of that tag on
    the spot is taken off first (False: it's left, and the new one goes on a track
    above it). The playhead is put back where it was - AppendToTimeline leaves it at
    the new clip's end (measured on Studio 21.1.2), and from its start the graphic can
    be seen, and placed again to change it. Returns {"id", "track", "start", "end",
    "replaced"}; raises PlaceError."""
    _frame, fps, _drop = timeline_info(timeline)
    is_ours = tagged(tag) if replace else (lambda _item: False)
    was_at = timeline.GetCurrentTimecode()
    start = at if at is not None else playhead(timeline)
    if start is None:
        raise PlaceError("Resolve didn't say where the playhead is.")
    start, length = int(start), max(1, int(length))
    end = start + length
    if track is None:
        track = pick_track(timeline, start, end, is_ours)
    while int(timeline.GetTrackCount("video") or 0) < track:
        if timeline.AddTrack("video") is False:
            raise PlaceError(f"Resolve wouldn't add video track {track}.")
    ours, theirs = in_the_way(timeline.GetItemListInTrack("video", track), start, end, is_ours)
    if theirs:
        clip = theirs[0]
        raise PlaceError(f"V{track} already has \"{clip.GetName()}\" at "
                         f"{clock(clip.GetStart(), fps, timeline.GetStartFrame())} - pick an empty track.")
    if ours and not timeline.DeleteClips(ours, False):
        raise PlaceError(f"Couldn't take Buddy's earlier {what} off V{track}.")
    gone = f" Buddy's earlier {what} there has been taken off." if ours else ""

    source = template(project.GetMediaPool())
    if source is None:
        raise PlaceError("Couldn't find or add Buddy's Text+ template in the Media Pool." + gone)
    source_fps = float(source.GetClipProperty("FPS") or fps)
    # endFrame is exclusive here: 0..95 placed 95 frames (21.1).
    project.GetMediaPool().AppendToTimeline([{
        "mediaPoolItem": source, "startFrame": 0, "endFrame": source_frames_for(length, source_fps, fps),
        "trackIndex": track, "recordFrame": start, "mediaType": 1}])
    item = _at(timeline, track, start)
    if was_at:
        try:
            timeline.SetCurrentTimecode(was_at)
        except Exception:
            pass
    if item is None:
        raise PlaceError(f"Resolve didn't put the {what} on V{track} at "
                         f"{clock(start, fps, timeline.GetStartFrame())}." + gone)
    text, gid = write(int(item.GetEnd()) - int(item.GetStart()))
    handle, path = tempfile.mkstemp(prefix="buddy_graphic_", suffix=".comp")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        taken = item.ImportFusionComp(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    # Read it back rather than trusting the calls.
    item = _at(timeline, track, start)
    comp = item.GetFusionCompByIndex(int(item.GetFusionCompCount() or 1)) if item is not None else None
    if not taken or comp is None or comp.GetData(tag) != gid:
        if item is not None:
            timeline.DeleteClips([item], False)        # a bare Text+ isn't left behind
        raise PlaceError(f"Resolve didn't take the {what}'s Fusion comp, so nothing was added." + gone)
    return {"id": gid, "track": track, "start": start, "end": int(item.GetEnd()), "replaced": bool(ours)}
