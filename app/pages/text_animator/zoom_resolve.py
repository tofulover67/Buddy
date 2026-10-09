#!/usr/bin/env python3
"""
The Zoom tab's zooms (zoom.py) on a clip in Resolve: reading the one clip
selected - its file and the stretch of it the timeline uses, for the player
in Buddy - and putting zooms on it or taking them off. Runs on the Animation
page's ResolveWorker.

A clip's zooms are one Transform in its Fusion comp, straight after the
clip's own picture comes in:

    MediaIn1 -> BuddyZoom (Transform: Size, Angle, and Center through an XYPath, keyed) -> what MediaIn1 fed

A Transform at the clip's own size zooms inside its own frame - nothing
outside the picture ever shows (zoom.view keeps the framing inside it) -
and anything after it is left alone: a Previews motion preset's Merge, the
Inspector. Its keys are the framing's every frame while a zoom moves
(zoom.keys), in comp frames, so a clip trimmed later keeps each zoom on the
same picture; the zooms themselves are kept on the Transform as tool data
("BuddyZoom") in comp frames too, and come back for editing from there.
A zoom that asks for motion blur turns on the Transform's own (it blurs
only what moves, so one zoom's blur never smears another's hold).
Every change ends with motion_resolve's export / import round trip
(_reload) - the Edit page renders nothing else (see motion_resolve).

An adjustment clip takes zooms too - its comp's MediaIn is everything
under it, at the timeline's size and rate (measured on Studio 21.1: the
same Transform renders exactly as predicted) - so a zoom there moves every
clip below at once. It has no picture of its own: the player plays the
clips under it in turn (under()), each as it is.

The Graded view: the clip's grade as a 3D LUT (export_grade), which
Resolve exports only from its Color page - so it goes there and back.

Where the player finds the picture: the clip's file (Media Pool "File
Path"), from GetSourceStartFrame at the clip's own frame rate (measured on
Studio 21.1: a 29.97 fps camera clip on a 24 fps timeline reads source
frames 964-1118 for 124 timeline frames - 32.17 s into the file). A still
is its file. A compound clip, nested timeline or generator has no one file:
not playable here.
"""

import json
import os

from . import motion_resolve as mr
from . import zoom as zm

NAME = "BuddyZoom"
MOTION_BLUR_QUALITY = 8         # the Transform's motion blur samples
ADJUST = "adjust"               # an adjustment clip (or another clip with only a picture comp)
KINDS = (mr.IMAGE, mr.VIDEO, ADJUST)
LOOK_AT = 20                    # selected titles checked for being adjustment clips, at most
MAX_PARTS = 60                  # clips under an adjustment clip the player goes through, at most


def _fps(timeline):
    try:
        fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    except (TypeError, ValueError):
        fps = 24.0
    return fps if fps > 0 else 24.0


def is_adjustment(item):
    """Whether a clip with no Media Pool item (mr.TITLE) is an adjustment
    clip - told by what's in it, since Resolve names it in its own language:
    no comp yet, or comps that take in a picture (MediaIn) rather than make
    their own (a Text+, a Fusion title or generator). A plain generator such
    as Solid Color passes too, harmlessly: its zoom zooms the colour."""
    try:
        count = int(item.GetFusionCompCount() or 0)
        for index in range(1, count + 1):
            comp = item.GetFusionCompByIndex(index)
            if comp is None or not comp.GetToolList(False, "MediaIn"):
                return False
        return True
    except Exception:
        return False


def kind_of(item, track_type):
    """mr.classify, with adjustment clips told apart from titles: ADJUST."""
    kind = mr.classify(item, track_type)
    return ADJUST if kind == mr.TITLE and is_adjustment(item) else kind


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _zoom_tool(comp):
    for tool in (comp.GetToolList(False, "Transform") or {}).values():
        try:
            if (tool.GetAttrs("TOOLS_Name") or "").startswith(NAME):
                return tool
        except Exception:
            continue
    return None


def _zoom_comp(item):
    """(index, comp, Transform) for the comp a clip's zooms are in, or None."""
    for index in range(int(item.GetFusionCompCount() or 0), 0, -1):
        comp = item.GetFusionCompByIndex(index)
        tool = _zoom_tool(comp) if comp is not None else None
        if tool is not None:
            return index, comp, tool
    return None


def _rate(comp, item, fps):
    """(first comp frame, comp frames, comp frames a second of the clip)."""
    clip_frames = int(item.GetDuration() or 0)
    base, frames = mr.comp_frames(comp, clip_frames)
    return base, frames, frames / max(1, clip_frames) * fps


def kept_zooms(item, fps, duration):
    """The zooms a clip already has, in seconds from its first frame."""
    found = _zoom_comp(item)
    if found is None:
        return []
    _index, comp, tool = found
    try:
        raw = tool.GetData(zm.TAG)
        kept = json.loads(raw) if raw else {}
    except (TypeError, ValueError, AttributeError):
        kept = {}
    base, _frames, rate = _rate(comp, item, fps)
    return zm.from_comp((kept or {}).get("zooms"), rate, base, duration)


def _resolution(timeline):
    try:
        return (int(timeline.GetSetting("timelineResolutionWidth") or 1920),
                int(timeline.GetSetting("timelineResolutionHeight") or 1080))
    except (TypeError, ValueError):
        return 1920, 1080


def under(timeline, item, fps):
    """What shows under an adjustment clip, for the player: [{"from", "to"
    (s into the adjustment clip), "path", "kind", "start" (s into the file
    at "from"), "rate"}] - at each moment the top video clip or still on a
    lower track, enabled. Titles, compound clips and the like count as
    nothing to show (left as gaps, drawn black); so do missing files."""
    first, last = item.GetStart(), item.GetEnd()
    try:
        _type, track = item.GetTrackTypeAndIndex()
    except Exception:
        return []
    covered, parts = [], []                 # covered: [a, b) timeline frames already shown from above
    for t in range(int(track) - 1, 0, -1):
        try:
            if not timeline.GetIsTrackEnabled("video", t):
                continue
        except Exception:
            pass
        for below in timeline.GetItemListInTrack("video", t) or []:
            a, b = max(first, below.GetStart()), min(last, below.GetEnd())
            if a >= b or not below.GetClipEnabled():
                continue
            kind = mr.classify(below, "video")
            if kind not in (mr.VIDEO, mr.IMAGE):
                continue
            pieces = [(a, b)]
            for c, d in covered:            # what's still uncovered of [a, b)
                pieces = [q for x, y in pieces for q in ((x, min(y, c)), (max(x, d), y)) if q[0] < q[1]]
            if not pieces:
                continue
            info = clip_info(below, kind, fps, zooms=False)
            if not info["exists"]:
                continue
            for x, y in pieces:
                parts.append({"from": (x - first) / fps, "to": (y - first) / fps, "path": info["path"],
                              "kind": info["kind"], "name": info["name"],
                              "start": info["start"] + (x - below.GetStart()) / fps * info["rate"],
                              "rate": info["rate"]})
            covered.append((a, b))
        if len(parts) > MAX_PARTS:
            break
    parts.sort(key=lambda p: p["from"])
    return parts[:MAX_PARTS]


def adjust_info(item, timeline, fps):
    """clip_info for an adjustment clip: the timeline's size, and "parts"
    (under()) for the player in place of a file."""
    width, height = _resolution(timeline)
    duration = int(item.GetDuration() or 0) / fps
    return {"id": item.GetUniqueId(), "name": item.GetName(), "kind": ADJUST, "path": "", "exists": True,
            "start": 0.0, "rate": 1.0, "duration": duration, "fps": fps, "width": width, "height": height,
            "zooms": kept_zooms(item, fps, duration), "parts": under(timeline, item, fps)}


def clip_info(item, kind, fps, zooms=True):
    """What the Zoom tab shows for one clip: {"id", "name", "kind" ("video" /
    "image"), "path", "exists", "start" (s into the file), "rate" (file
    seconds per clip second: its speed), "duration" (s), "fps", "width",
    "height", "zooms"}."""
    pool = item.GetMediaPoolItem()
    props = (pool.GetClipProperty() if pool else {}) or {}
    path = str(props.get("File Path") or "")
    width, height = 1920, 1080
    try:
        width, height = (int(v) for v in str(props.get("Resolution") or "").lower().split("x")[:2])
    except ValueError:
        pass
    duration = int(item.GetDuration() or 0) / fps
    start, rate = 0.0, 1.0
    if kind == mr.VIDEO:
        source_fps = _num(props.get("FPS"), fps) or fps
        start = _num(item.GetSourceStartFrame()) / source_fps
        try:
            speed = (item.GetSpeed() or {}).get("Percentage")
        except Exception:
            speed = None
        rate = _num(speed, 100.0) / 100.0 if speed not in (None, 0, 0.0) else 1.0
    return {"id": item.GetUniqueId(), "name": item.GetName(), "kind": "video" if kind == mr.VIDEO else "image",
            "path": path, "exists": bool(path) and os.path.isfile(path), "start": start, "rate": rate,
            "duration": duration, "fps": fps, "width": width, "height": height,
            "zooms": kept_zooms(item, fps, duration) if zooms else []}


def summary(controller):
    """{"timeline", "total", "usable", "clip"} for the selection - "usable"
    how many are video clips, stills or adjustment clips, "clip" (clip_info
    / adjust_info) when that's
    exactly one, else None, and "reason" saying why not."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        return {"timeline": "", "total": 0, "usable": 0, "clip": None, "reason": "no_timeline"}
    picked = mr.selected_items(timeline)
    # Telling an adjustment clip from a title opens its comps: for the first
    # LOOK_AT titles only (a few hundred subtitles selected would be a lot
    # of work for Resolve every poll) - past those, titles count as titles.
    usable, looked = [], 0
    for item, track_type in picked:
        kind = mr.classify(item, track_type)
        if kind == mr.TITLE and looked < LOOK_AT:
            looked += 1
            kind = ADJUST if is_adjustment(item) else kind
        if kind in KINDS:
            usable.append((item, kind))
    out = {"timeline": timeline.GetName(), "total": len(picked), "usable": len(usable), "clip": None, "reason": ""}
    if not picked:
        out["reason"] = "none"
    elif len(usable) != 1:
        out["reason"] = "many" if len(usable) > 1 else "other"
    else:
        try:
            item, kind = usable[0]
            out["clip"] = (adjust_info(item, timeline, _fps(timeline)) if kind == ADJUST
                           else clip_info(item, kind, _fps(timeline)))
        except Exception as exc:  # noqa: BLE001 - said on the page
            out["reason"], out["error"] = "error", str(exc)
    return out


# --------------------------------------------------------------- changing --

def _take_out(comp, tool):
    """The zoom Transform out, and what it fed fed from its input again."""
    source = mr._source_tool(tool.Input)
    try:
        after = list((tool.Output.GetConnectedInputs() or {}).values())
    except Exception:
        after = []
    for name in ("Size", "Center", "Angle"):
        try:
            mr._delete_feeding(getattr(tool, name))
        except Exception:
            pass
    for inp in after:
        if source is not None:
            inp.ConnectTo(source.Output)
    tool.Delete()


def _media_in(comp):
    found = list((comp.GetToolList(False, "MediaIn") or {}).values())
    return found[0] if found else None


def _build(comp, zooms, item, fps, duration):
    base, frames, rate = _rate(comp, item, fps)
    media_in = _media_in(comp)
    if media_in is None:
        raise RuntimeError("its Fusion composition has no MediaIn")
    try:
        after = list((media_in.Output.GetConnectedInputs() or {}).values())
    except Exception:
        after = []
    tool = comp.AddTool("Transform", mr.FAR, mr.FAR)
    tool.SetAttrs({"TOOLS_Name": NAME})
    tool.ConnectInput("Input", media_in)
    for inp in after:
        inp.ConnectTo(tool.Output)
    width = float(comp.GetPrefs("Comp.FrameFormat.Width") or 1920)
    height = float(comp.GetPrefs("Comp.FrameFormat.Height") or 1080)
    keys = zm.keys(zooms, duration, frames, rate, base, width / max(1.0, height))
    mr._key(comp, tool.Size, keys["Size"])
    if len(keys["Angle"]) > 1 or any(k["value"] for k in keys["Angle"].values()):
        mr._key(comp, tool.Angle, keys["Angle"])
    if zm.blurred(zooms):
        tool.SetInput("MotionBlur", 1)
        tool.SetInput("Quality", MOTION_BLUR_QUALITY)
        tool.SetInput("ShutterAngle", 180)
    tool.AddModifier("Center", "XYPath")
    path = mr._source_tool(tool.Center)
    if path is None:
        raise RuntimeError("couldn't attach a motion path")
    mr._key(comp, path.X, keys["Center.X"])
    mr._key(comp, path.Y, keys["Center.Y"])
    tool.SetData(zm.TAG, json.dumps({"zooms": zm.to_comp(zooms, rate, base)}))


def apply(item, zooms, fps):
    """Puts `zooms` (seconds from the clip's start) on a clip, in place of any
    it had; none takes them off. Returns how many went on. Raises with a
    reason."""
    duration = int(item.GetDuration() or 0) / fps
    zooms = zm.clean(zooms, duration)
    found = _zoom_comp(item)
    if found is not None:
        index, comp = found[0], found[1]
    else:
        if not zooms:
            return 0
        index, comp, _merge = mr._working_comp(item)
    backup = mr._export(item, index)
    if backup is None:
        raise RuntimeError("Resolve wouldn't save a copy of its Fusion composition first")
    try:
        comp.Lock()
        comp.StartUndo("Buddy zoom")
        try:
            old = _zoom_tool(comp)
            if old is not None:
                _take_out(comp, old)
            if zooms:
                _build(comp, zooms, item, fps, duration)
        finally:
            comp.EndUndo(True)
            comp.Unlock()
        if not mr._reload(item, index):
            raise RuntimeError("Resolve didn't take the updated Fusion composition")
    except Exception:
        mr._restore(item, index, backup)
        raise
    finally:
        mr._forget(backup)
    return len(zooms)


def _selected(timeline, clip_id):
    for item, track_type in mr.selected_items(timeline):
        if item.GetUniqueId() == clip_id and kind_of(item, track_type) in KINDS:
            return item
    return None


def run_apply(controller, clip_id, zooms):
    """The Zoom tab's Apply: `zooms` on the selected clip `clip_id`.
    Returns {"name", "count"}. Raises with a reason."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    item = _selected(timeline, clip_id)
    if item is None:
        raise RuntimeError("That clip isn't selected in Resolve's timeline any more – select it again.")
    return {"name": item.GetName(), "count": apply(item, zooms, _fps(timeline))}


def playhead(controller, clip_id):
    """Where Resolve's playhead is on the selected clip, in seconds from its
    start - None when it's not on it."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        return None
    item = _selected(timeline, clip_id)
    if item is None:
        return None
    fps = _fps(timeline)
    try:
        at = mr._timecode_frames(timeline.GetCurrentTimecode(), fps)
    except Exception:
        return None
    return (at - item.GetStart()) / fps if item.GetStart() <= at < item.GetEnd() else None


def read_cube(path):
    """A .cube 3D LUT as (size, bytes): size^3 RGB triples, 0-255, red
    changing fastest (the file's order). Raises ValueError for anything else."""
    size, rows = 0, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            if parts[0] == "LUT_3D_SIZE":
                size = int(parts[1])
            elif len(parts) == 3 and parts[0][0] in "0123456789.-":
                rows.append(parts)
    if not 2 <= size <= 128 or len(rows) != size ** 3:
        raise ValueError(f"not a 3D LUT ({len(rows)} entries for size {size})")
    out = bytearray(3 * len(rows))
    for i, row in enumerate(rows):
        for k in range(3):
            out[3 * i + k] = max(0, min(255, round(float(row[k]) * 255)))
    return size, bytes(out)


def is_identity(size, data):
    """Whether a LUT (read_cube) changes nothing - an ungraded clip's."""
    step = 255 / (size - 1)
    for i in range(0, size ** 3, 7):                    # a sample is plenty
        r, g, b = i % size, (i // size) % size, i // (size * size)
        if max(abs(data[3 * i] - r * step), abs(data[3 * i + 1] - g * step), abs(data[3 * i + 2] - b * step)) > 1.5:
            return False
    return True


def export_grade(controller, clip_id, path):
    """The selected clip `clip_id`'s grade as a LUT (read_cube) - colour only:
    Resolve bakes its colour corrections into it, not blurs, keys' shapes or
    effects. Resolve exports a LUT only on the Color page (Studio 21.1), so
    this goes there and straight back (~0.3 s - it shows). Any clip works,
    wherever the playhead is. Raises with a reason."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    item = _selected(timeline, clip_id)
    if item is None:
        raise RuntimeError("That clip isn't selected in Resolve's timeline any more – select it again.")
    resolve = controller.resolve
    page = resolve.GetCurrentPage()
    if os.path.exists(path):
        os.remove(path)
    try:
        if page != "color":
            resolve.OpenPage("color")
        ok = item.ExportLUT(int(resolve.EXPORT_LUT_33PTCUBE), path)
    finally:
        if page and page != "color":
            resolve.OpenPage(page)
    if not ok or not os.path.isfile(path):
        raise RuntimeError("Resolve wouldn't export the clip's grade.")
    try:
        return read_cube(path)
    finally:
        os.remove(path)


def run_paste(controller, zooms, from_duration, mode):
    """Copied zooms (from a clip `from_duration` long) onto every selected
    video clip, still and adjustment clip, in place of theirs - at the same times, or
    stretched to each one's length (zoom.adapt). Returns {"applied",
    "empty": clips none of them reached, "failed": [(clip, reason)]}."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    fps = _fps(timeline)
    applied, empty, failed = 0, 0, []
    for item, track_type in mr.selected_items(timeline):
        if kind_of(item, track_type) not in KINDS:
            continue
        duration = int(item.GetDuration() or 0) / fps
        fitted = zm.adapt(zooms, from_duration, duration, mode)
        if not fitted:
            empty += 1
            continue
        try:
            apply(item, fitted, fps)
            applied += 1
        except Exception as exc:  # noqa: BLE001 - one clip's trouble is reported, the rest go on
            failed.append((item.GetName(), str(exc)))
    return {"applied": applied, "empty": empty, "failed": failed}
