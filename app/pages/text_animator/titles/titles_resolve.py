"""Putting a composed title on Resolve's timeline: one Text+ clip at the playhead
whose comp is the title (textplus_comp.py) - one Text+ per word, editable in Fusion
afterwards.

The way it goes in (measured on Studio 21.1, 2026-10-05):
  1. The title's fonts installed for the user and handed to the running Fusion
     (fonts.py) - Text+ can only draw installed fonts.
  2. A Text+ from Buddy's template bin placed with AppendToTimeline - the call that
     lands on an exact frame and track (subtitle_engine.py).
  3. Each face's baseline measured in that clip's comp (text_measure.py), so the
     words sit where the composer put them.
  4. The title's comp imported over the clip's (ImportFusionComp replaces a title
     clip's comp, and the Edit page renders an imported comp at once).
  5. The timeline read back: the clip is where it should be, with its words.

Safety: a title is Buddy's when its comp carries the BuddyTitle tag. Placing never
covers anything else - a clip of someone else's in the span stops it, naming the clip
and where - and Buddy's own earlier title in that spot is replaced.
"""

import hashlib
import json
import os
import re
import tempfile

from .. import canvas_math as cm
from .. import subtitle_engine as se
from .. import text_measure
from . import fonts, looks, measure, textplus_comp, timing

TAG = textplus_comp.TAG
DEFAULT_SECONDS = 3.0


class PlaceError(RuntimeError):
    """Why a title couldn't be placed, in words for the person."""


def title_id(doc, frame, fps):
    """The title's id: a hash of exactly what was placed - the document, the frame size
    and the rate - so the same title is recognised again."""
    raw = json.dumps({"doc": doc, "frame": list(frame), "fps": float(fps)}, sort_keys=True, default=str)
    return "bt-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def is_buddy_title(item):
    """Whether a timeline item is a title Buddy placed (its comp carries the tag)."""
    try:
        for i in range(1, int(item.GetFusionCompCount() or 0) + 1):
            comp = item.GetFusionCompByIndex(i)
            if comp is not None and comp.GetData(TAG):
                return True
    except Exception:
        pass
    return False


def in_the_way(items, start, end, is_ours=is_buddy_title):
    """The items overlapping [start, end): (Buddy's own titles, everyone else's)."""
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


def pick_track(timeline, start, end, is_ours=is_buddy_title):
    """The track a new title goes on: the topmost video track with nothing of anyone
    else's in the span, else a new track above them all."""
    count = int(timeline.GetTrackCount("video") or 0)
    for track in range(count, 0, -1):
        _ours, theirs = in_the_way(timeline.GetItemListInTrack("video", track), start, end, is_ours)
        if not theirs:
            return track
    return count + 1


def _template(pool):
    """The Text+ in Buddy's template bin - imported there first if it's missing."""
    root = pool.GetRootFolder()
    for folder in root.GetSubFolderList() or []:
        if folder.GetName() == se.TEMPLATE_BIN:
            for clip in folder.GetClipList() or []:
                if (clip.GetClipProperty("Type") or "") in ("Fusion Title", "Generator", "Title"):
                    return clip
    return se.TextPlusGenerator()._add_text_plus_template(pool)


def _timeline_info(timeline):
    width, height = se.get_timeline_resolution(timeline)
    fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    drop = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") in ("1", "True", "true")
    return (width, height), fps, drop


def playhead(timeline):
    """The playhead as a timeline frame number (timelines start at 01:00:00:00, not 0)."""
    _frame, fps, drop = _timeline_info(timeline)
    return se.timecode_to_frames(timeline.GetCurrentTimecode(), fps, drop)


def place(resolve, project, timeline, layout, doc, seconds=DEFAULT_SECONDS, at=None, track=None, log=None,
          animate=True):
    """Put a composed title on the timeline. at: the timeline frame it starts on (the
    playhead if None); track: the video track (the topmost free one if None); animate:
    key its entrance (and exit) from the clock (timing.py), lengthening a title too
    short to play them. Returns {"id", "track", "start", "end", "words"}; raises
    PlaceError."""
    log = log or (lambda _msg: None)
    frame, fps, _drop = _timeline_info(timeline)
    if tuple(layout["frame"]) != tuple(frame):
        raise PlaceError(f"The title was laid out for {layout['frame'][0]}x{layout['frame'][1]}, "
                         f"but the timeline is {frame[0]}x{frame[1]}.")
    if not layout["boxes"]:
        raise PlaceError("There's no text to place.")
    start = int(at if at is not None else playhead(timeline))
    styles = looks.box_styles(layout, doc)      # with the person's colours, outlines and faces
    # Not "clock": that's the function the messages below say a time with.
    timed = timing.timing(doc, layout, seconds, styles) if animate else None
    if timed is not None and seconds < timing.min_duration(timed):
        seconds = timing.min_duration(timed)
        timed = timing.timing(doc, layout, seconds, styles)
    length = max(1, int(round(seconds * fps)))
    end = start + length
    tid = title_id(doc, frame, fps)

    # 1. Fonts Text+ can draw.
    fusion = resolve.Fusion()
    report = fonts.install(faces={tuple(b["font"]) for b in layout["boxes"]} & set(measure.bundled_files()))
    for name, why in report["failed"]:
        log(f"Couldn't install the font {name}: {why}")
    still = set(fonts.add_to_fusion(fusion))
    font_list = fusion.FontManager.GetFontList() or {}
    absent = sorted({tuple(b["font"]) for b in layout["boxes"]
                     if tuple(b["font"]) in still or b["font"][1] not in (font_list.get(b["font"][0]) or {})})
    if absent:
        raise PlaceError("Resolve doesn't have the font " + ", ".join(f"{f} {s}" for f, s in absent)
                         + " - restart Resolve and place the title again.")
    cm.set_font_files(font_list)

    # 2. The spot: nobody else's clip in the way; Buddy's own title there is replaced.
    if track is None:
        track = pick_track(timeline, start, end)
    while int(timeline.GetTrackCount("video") or 0) < track:
        if timeline.AddTrack("video") is False:
            raise PlaceError(f"Resolve wouldn't add video track {track}.")
    ours, theirs = in_the_way(timeline.GetItemListInTrack("video", track), start, end)
    if theirs:
        clip = theirs[0]
        raise PlaceError(f"V{track} already has \"{clip.GetName()}\" at "
                         f"{clock(clip.GetStart(), fps, timeline.GetStartFrame())} - pick an empty track.")
    if ours and not timeline.DeleteClips(ours, False):
        raise PlaceError(f"Couldn't take Buddy's earlier title off V{track}.")
    # The new clip goes on the spot the old one had, so the old one is off
    # first; from here a failure says so.
    gone = " Buddy's earlier title there has been taken off." if ours else ""

    # 3. A Text+ clip on that spot.
    pool = project.GetMediaPool()
    template = _template(pool)
    if template is None:
        raise PlaceError("Couldn't find or add Buddy's Text+ template in the Media Pool." + gone)
    source_fps = float(template.GetClipProperty("FPS") or fps)
    frames = se.source_frames_for(length, source_fps, fps)
    # endFrame is exclusive here: 0..95 placed 95 frames (21.1).
    pool.AppendToTimeline([{"mediaPoolItem": template, "startFrame": 0, "endFrame": frames,
                            "trackIndex": track, "recordFrame": start, "mediaType": 1}])
    item = next((it for it in timeline.GetItemListInTrack("video", track) or [] if int(it.GetStart()) == start), None)
    if item is None:
        raise PlaceError(f"Resolve didn't put the title on V{track} at {clock(start, fps, timeline.GetStartFrame())}."
                         + gone)
    comp = item.GetFusionCompByIndex(1)
    comp_frames = int(item.GetEnd()) - int(item.GetStart())

    # 4. Where each face's baseline sits, then the title's comp in place of the clip's.
    faces = sorted({tuple(b["font"]) for b in layout["boxes"]})
    cm.set_baselines(text_measure.measure_baselines(comp, faces, frame))
    handle, path = tempfile.mkstemp(prefix="buddy_title_", suffix=".comp")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textplus_comp.comp_text(layout, tid, comp_frames, styles=styles, clock=timed, fps=fps))
        if not item.ImportFusionComp(path):
            raise PlaceError("Resolve didn't take the title's Fusion comp." + gone)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    # 5. Read it back rather than trusting the calls.
    item = next((it for it in timeline.GetItemListInTrack("video", track) or [] if int(it.GetStart()) == start), None)
    comp = item.GetFusionCompByIndex(int(item.GetFusionCompCount() or 1)) if item is not None else None
    if comp is None or comp.GetData(TAG) != tid:
        raise PlaceError("The title went on, but its comp didn't come back as placed.")
    # The words' own Text+ (Word1..n) - not their copies (Word1Copy0: echo, extrude).
    words = sum(1 for tool in (comp.GetToolList(False, "TextPlus") or {}).values()
                if re.fullmatch(r"Word\d+", str((tool.GetAttrs() or {}).get("TOOLS_Name", ""))))
    if words != len(layout["boxes"]):
        log(f"The title has {words} words in Resolve, not {len(layout['boxes'])}.")
    if int(item.GetEnd()) != end:
        log(f"The title lasts {int(item.GetEnd()) - start} frames, not {length}.")
    return {"id": tid, "track": track, "start": start, "end": int(item.GetEnd()), "words": words}
