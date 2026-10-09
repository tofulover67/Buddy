#!/usr/bin/env python3
"""
Putting the Presets tab's word highlight (highlight.py) on the Text+ clips
selected in Resolve's timeline, and taking it off again. Runs on the
Animation page's ResolveWorker.

What goes into a Text+ clip's comp, right after the Text+ - so a motion
preset from Previews still moves it all, highlight and words together:

    Text+ -> BuddyHighlightColor (ColorGain, masked by BuddyHighlightColorMask)
          -> BuddyHighlight (Merge, foreground) -> whatever the Text+ fed before
    BuddyHighlightBox (Background, masked by BuddyHighlightBoxMask) -> BuddyHighlight (background)

The two masks are Rectangle masks keyed to move from word to word as each
is said (highlight.plan): the box's padded and rounded, behind the words;
the colour's tight round the word, so the gain only touches the word being
said. A gain, not a fill: the Text+'s fill colour times it is the colour
chosen, and a black outline or shadow stays black. With Pop on, a text
Follower on the Text+ pops each word at its own start (Manual Curve delays,
as Animate by's units.py) - not with Animate by's Follower already there.

Where each word is: Text+'s Write On (Start / End, shares of its characters,
line breaks counted: character i shows when Start x n <= i < End x n -
measured 21.1) shows one word at a time, and the Text+'s DoD is then that word's ink
(measured: DoD = ink + 2 px) - read with only the fill on, so a shadow
doesn't pull the box off the word. Every change ends with motion_resolve's
export / import round trip (_reload), which also lays the nodes out.

The Text+ carries tool data BuddyHighlight: what went on, so Remove takes
exactly that out and the page can count highlighted clips - and the word
starts (seconds from the clip's start) with the text they're for. Starts set
by hand on the Presets tab's timing strip ("timing": "manual") are kept by
every later Highlight while the text stays the same.
"""

import json
import time
from pathlib import Path

from . import highlight as hl
from . import motion_resolve as mr

TAG = "BuddyHighlight"                  # tool data on the Text+: json of what went on
PREFIX = "BuddyHighlight"               # every tool Highlight adds is named with it
MERGE_NAME = "BuddyHighlight"
BOX_NAME, BOX_MASK = "BuddyHighlightBox", "BuddyHighlightBoxMask"
COLOR_NAME, COLOR_MASK = "BuddyHighlightColor", "BuddyHighlightColorMask"
KEYED = ("Center", "Width", "Height", "Level")      # a mask's inputs Highlight keys
POP_KEYED = ("DelayByCharacterPosition", "WordSizeX", "WordSizeY")
DOD_MARGIN = 2.0                        # px a Text+'s DoD reaches past its ink
LOOK_AT = 40                            # summary(): comps opened per read, at most


# --------------------------------------------------------------- reading --

# The Text+ inputs the words' ink depends on: with the words, what the
# record says the boxes were measured for (measured / stale).
MEASURED = ("Font", "Style", "Size", "Center", "CharacterSpacing", "LineSpacing", "LayoutType",
            "HorizontalJustificationNew", "VerticalJustificationNew", "Width", "Height")


def _plain(value):
    """A Fusion input value as JSON keeps it, the same way every time: numbers
    rounded, a Point (or any table) a list, so two readings compare."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    if isinstance(value, dict):
        return [_plain(value[k]) for k in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    x, y = getattr(value, "X", None), getattr(value, "Y", None)
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return [round(float(x), 4), round(float(y), 4)]
    return str(value)


def measured(tp, text):
    """What the ink is measured for: the words, and the inputs that put them
    where they are (MEASURED). Kept in the record; stale() compares."""
    out = {"text": text}
    for name in MEASURED:
        try:
            out[name] = _plain(tp.GetInput(name))
        except Exception:
            out[name] = None
    return out


def stale(tp, rec):
    """True when the words, size or position have changed since the highlight
    was measured - its boxes may not line up any more. False for a record
    without a measurement (an earlier Buddy's)."""
    kept = (rec or {}).get("measured")
    if not isinstance(kept, dict):
        return False
    try:
        return measured(tp, mr._text_of(tp)) != kept
    except Exception:
        return False


def record(tp):
    """What Highlight put on a Text+ ({"options", "pop", "timing", "text",
    "starts", "measured"}), or None."""
    try:
        raw = tp.GetData(TAG)
        found = json.loads(raw) if raw else None
    except (TypeError, ValueError, AttributeError):
        return None
    return found if isinstance(found, dict) else None


def _found(item):
    """(index, comp, Text+) for a lone Text+ clip, or None."""
    try:
        return mr._lone_text_plus(item)
    except Exception:
        return None


_cache = {"key": None, "transcripts": []}


def transcripts(folder):
    """highlight.load_transcripts for `folder`, read again only when its files
    change - the selection poll asks every couple of seconds."""
    try:
        key = tuple(sorted((p.name, p.stat().st_mtime) for p in Path(folder).glob("*.json")))
    except OSError:
        key = ()
    if key != _cache["key"]:
        _cache.update(key=key, transcripts=hl.load_transcripts(folder))
    return _cache["transcripts"]


def _fps(timeline):
    try:
        fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    except (TypeError, ValueError):
        fps = 24.0
    return fps if fps > 0 else 24.0


def _span(item, timeline, fps):
    """(start, end) of a clip in seconds from the timeline's first frame - as
    the transcripts count."""
    zero = int(timeline.GetStartFrame() or 0)
    return (item.GetStart() - zero) / fps, (item.GetEnd() - zero) / fps


def _playhead(timeline, item, fps):
    """Where Resolve's playhead is, in seconds from the clip's start - None
    when it's not on the clip."""
    try:
        at = mr._timecode_frames(timeline.GetCurrentTimecode(), fps)
    except Exception:
        return None
    return (at - item.GetStart()) / fps if item.GetStart() <= at < item.GetEnd() else None


def kept_starts(rec, text):
    """The word starts set by hand (seconds from the clip's start) a highlight
    record keeps, while the Text+ still has the text they were set for -
    else None."""
    if not rec or rec.get("timing") != "manual" or rec.get("text") != text:
        return None
    starts = rec.get("starts")
    if (not isinstance(starts, list) or len(starts) != len(hl.words_of(text))
            or not all(isinstance(t, (int, float)) for t in starts)):
        return None
    return [float(t) for t in starts]


def timing_view(item, tp, timeline, fps, transcripts_dir):
    """The Presets tab's timing strip for one caption: {"id", "name", "text",
    "words", "duration", "fps", "starts" (seconds from the clip's start: the
    ones set by hand, else as Highlight would time them), "auto" (as
    Highlight would), "timing" ("manual", "transcript" or "even"),
    "playhead"} - or None for a caption with no words."""
    text = mr._text_of(tp)
    words = hl.words_of(text)
    if not words:
        return None
    start, end = _span(item, timeline, fps)
    found = hl.best_transcript(transcripts(transcripts_dir), [(text, start, end)]) if transcripts_dir else (None, None)
    times, how = hl.word_times(text, found[1], start, end)
    auto = [round(t - start, 4) for t in times]
    kept = kept_starts(record(tp), text)
    return {"id": item.GetUniqueId(), "name": item.GetName(), "text": text, "words": [w[2] for w in words],
            "duration": end - start, "fps": fps, "starts": kept or auto, "auto": auto,
            "timing": "manual" if kept else how, "playhead": _playhead(timeline, item, fps)}


def summary(controller, transcripts_dir=None):
    """{"timeline", "fps", "titles", "highlighted", "stale", "other", "total",
    "clip"} for the selection: how many are lone Text+ clips, how many of
    those carry a highlight (and how many of those were measured for other
    words, size or position - stale), and how many are something else -
    and with just one Text+ selected, its timing_view ("clip"), else None."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        return {"timeline": "", "fps": 24.0, "titles": 0, "highlighted": 0, "stale": 0, "other": 0, "total": 0,
                "clip": None}
    titles = highlighted = changed = other = looked = 0
    lone = []
    for item, track_type in mr.selected_items(timeline):
        if mr.classify(item, track_type) != mr.TITLE:
            other += 1
            continue
        looked += 1
        if looked > LOOK_AT:
            titles += 1                 # past LOOK_AT: counted, not opened (as motion_resolve.summary)
            continue
        found = _found(item)
        if found is None:
            other += 1
            continue
        titles += 1
        rec = record(found[2])
        highlighted += rec is not None
        changed += rec is not None and stale(found[2], rec)
        lone.append((item, found[2]))
    fps = _fps(timeline)
    clip = None
    if titles == 1 and len(lone) == 1:
        try:
            clip = timing_view(*lone[0], timeline, fps, transcripts_dir)
        except Exception:
            clip = None
    return {"timeline": timeline.GetName(), "fps": fps, "titles": titles, "highlighted": highlighted,
            "stale": changed, "other": other, "total": titles + other, "clip": clip}


def _frame(comp):
    width = float(comp.GetPrefs("Comp.FrameFormat.Width") or 1920)
    height = float(comp.GetPrefs("Comp.FrameFormat.Height") or 1080)
    return width, height


def _dod(tool, when):
    """(left, bottom, right, top) px of a tool's picture at `when`, or None."""
    try:
        box = tool.Output.GetDoD(when)
        left, bottom, right, top = (float(box[k]) for k in (1, 2, 3, 4))
    except Exception:
        return None
    return None if right - left < 1 or top - bottom < 1 else (left, bottom, right, top)


def measure(comp, tp, text, when):
    """Each word's ink (cx, cy, w, h) as shares of the frame, y up, at comp
    frame `when` - each read with Write On showing that word alone, and only
    the fill on. Everything is put back as it was. Raises when a word can't
    be read."""
    words = hl.words_of(text)
    n = len(text)
    width, height = _frame(comp)
    keep = {name: tp.GetInput(name) for name in ("Start", "End")}
    shown = {f"Enabled{e}": tp.GetInput(f"Enabled{e}") for e in mr.ELEMENTS if e > 1}
    out = []
    try:
        for name in shown:
            if mr._on(shown[name]):
                tp.SetInput(name, 0)
        for first, last, _word in words:
            # Half a character in from each edge: a share landing exactly on
            # one (27/47) can come back a hair past it and drop the word's
            # first letter (measured on a second line).
            tp.SetInput("Start", (first - 0.5) / n)
            tp.SetInput("End", (last - 0.5) / n)
            box = _dod(tp, when)
            if box is None:
                raise RuntimeError("Resolve wouldn't say where its words are")
            left, bottom, right, top = box
            left, right = left + DOD_MARGIN, max(left + DOD_MARGIN + 1, right - DOD_MARGIN)
            bottom, top = bottom + DOD_MARGIN, max(bottom + DOD_MARGIN + 1, top - DOD_MARGIN)
            out.append(((left + right) / 2 / width, (bottom + top) / 2 / height,
                        (right - left) / width, (top - bottom) / height))
    finally:
        for name, value in keep.items():
            if value is not None:
                tp.SetInput(name, value)
        for name, value in shown.items():
            if mr._on(value):
                tp.SetInput(name, value)
    return out


# -------------------------------------------------------------- building --

def _downstream(tool):
    """The inputs a tool's picture goes into."""
    try:
        found = tool.Output.GetConnectedInputs() or {}
    except Exception:
        return []
    return list(found.values()) if isinstance(found, dict) else list(found)


def _feed(ours, tp):
    """The output that comes into Buddy's tools from before them: the Text+'s,
    unless one other tool was put between since (its output, so it stays
    in the chain when Buddy's tools come off)."""
    sources = []
    for tool in ours:
        for name in ("Input", "Background", "Foreground"):
            try:
                out = getattr(tool, name).GetConnectedOutput()
                source = out.GetTool() if out is not None else None
            except Exception:
                continue
            if source is None or source is tp or _ours(source) or any(source is s for s in sources):
                continue
            try:
                if source.GetAttrs("TOOLS_RegID") in ("TextPlus", "RectangleMask", "BezierSpline", "XYPath"):
                    continue
            except Exception:
                continue
            sources.append(source)
    return sources[0].Output if len(sources) == 1 else tp.Output


def _ours(tool):
    try:
        return (tool.GetAttrs("TOOLS_Name") or "").startswith(PREFIX)
    except Exception:
        return False


def _mask(comp, name, keys, base, corner):
    """A Rectangle mask moving as `keys` say (highlight.plan's)."""
    mask = comp.AddTool("RectangleMask", mr.FAR, mr.FAR)
    mask.SetAttrs({"TOOLS_Name": name})
    mask.SetInput("CornerRadius", corner)
    mask.SetInput("SoftEdge", 0.0)
    mask.AddModifier("Center", "XYPath")
    path = mr._source_tool(mask.Center)
    if path is None:
        raise RuntimeError("couldn't attach a motion path")
    mr._key(comp, path.X, keys["x"], base)
    mr._key(comp, path.Y, keys["y"], base)
    mr._key(comp, mask.Width, keys["w"], base)
    mr._key(comp, mask.Height, keys["h"], base)
    mr._key(comp, mask.Level, keys["level"], base)
    return mask


def _fill(tp):
    """The Text+'s fill colour (r, g, b), 0-1."""
    out = []
    for channel in ("Red1", "Green1", "Blue1"):
        try:
            out.append(float(tp.GetInput(channel)))
        except (TypeError, ValueError):
            out.append(1.0)
    return tuple(out)


def _build(comp, tp, text, starts, ink, options, base, frames, fps, timing, seconds):
    """apply's change to the comp. Returns whether the words pop. `seconds`
    are the word starts as kept in the record."""
    width, height = _frame(comp)
    aspect = width / max(1.0, height)
    look = options["look"]
    after = _downstream(tp)
    last = tp
    if look in ("both", "color"):
        tight = hl.plan(starts, hl.word_boxes(ink, aspect, hl.COLOR_PAD), frames, fps, options)
        gain = comp.AddTool("ColorGain", mr.FAR, mr.FAR)
        gain.SetAttrs({"TOOLS_Name": COLOR_NAME})
        gain.ConnectInput("Input", tp)
        # On the box, the word's own colour; with no box, the highlight's.
        target = options["word_color"] if look == "both" else options["box_color"]
        for channel, value in zip(("GainRed", "GainGreen", "GainBlue"),
                                  hl.color_gain(hl.rgb(target), _fill(tp))):
            gain.SetInput(channel, value)
        gain.SetInput("PreDividePostMultiply", 1)
        gain.ConnectInput("EffectMask", _mask(comp, COLOR_MASK, tight, base, 0.0))
        last = gain
    if look in ("both", "box"):
        keys = hl.plan(starts, hl.word_boxes(ink, aspect, hl.BOX_PAD), frames, fps, options)
        box = comp.AddTool("Background", mr.FAR, mr.FAR)
        box.SetAttrs({"TOOLS_Name": BOX_NAME})
        r, g, b = hl.rgb(options["box_color"])
        for channel, value in (("TopLeftRed", r), ("TopLeftGreen", g), ("TopLeftBlue", b), ("TopLeftAlpha", 1.0)):
            box.SetInput(channel, value)
        box.ConnectInput("EffectMask", _mask(comp, BOX_MASK, keys, base, hl.SHAPES[options["shape"]]))
        merge = comp.AddTool("Merge", mr.FAR, mr.FAR)
        merge.SetAttrs({"TOOLS_Name": MERGE_NAME})
        merge.ConnectInput("Background", box)
        merge.ConnectInput("Foreground", last)
        last = merge
    for inp in after:
        inp.ConnectTo(last.Output)
    if not after:
        media_out = mr._media_out(comp)
        if media_out is not None:
            media_out.ConnectInput("Input", last)
    pops = False
    keys = hl.pop_keys(fps, options)
    # The pop is a text Follower, and a Text+'s text takes one modifier: over
    # Animate by's it has no place, and over Character Level Styling it would
    # replace the styling (mr.text_source) - those words just don't pop.
    if keys is not None and mr.text_source(tp) is None:
        tp.AddModifier("StyledText", mr.FOLLOWER_ID)
        source = mr.follower(tp)
        if source is None:
            raise RuntimeError("Resolve wouldn't put a text Follower on its Text+")
        source.SetInput("Text", text)
        source.SetInput("Delay", 1.0)
        mr._key(comp, source.DelayByCharacterPosition,
                {float(i): {"value": d} for i, d in enumerate(hl.delays(text, starts))})
        source.SetInput("Order", mr.MANUAL_CURVE)
        source.SetInput("TransformSize", 1)
        mr._key(comp, source.WordSizeX, keys, base)
        mr._key(comp, source.WordSizeY, keys, base)
        pops = True
    tp.SetData(TAG, json.dumps({"options": options, "pop": pops, "timing": timing, "text": text,
                                "starts": [round(t, 4) for t in seconds], "measured": measured(tp, text)}))
    return pops


def _take_out(comp, tp):
    """Takes Highlight's tools and pop off a Text+'s comp; what they fed gets
    the Text+ again. Raises, with nothing changed, when something is chained
    behind the pop's Follower (mr.chained)."""
    kept = record(tp) or {}
    if kept.get("pop") and mr._units_record(tp) is None and mr.chained(tp):
        raise RuntimeError(mr.CHAINED)
    # The pop's Follower is the one built as the pop builds it (mr.timed): the
    # record can outlast it, with a letter preset's or the person's on the text now.
    ours = [t for t in (comp.GetToolList(False) or {}).values() if _ours(t)]
    feed = _feed(ours, tp)          # what came into Buddy's tools: the Text+, or a node put between since
    for tool in ours:
        for inp in _downstream(tool):
            try:
                if not _ours(inp.GetTool()):
                    inp.ConnectTo(feed)
            except Exception:
                continue
    mr._delete_named(comp, (PREFIX,), inputs=KEYED)
    source = mr.follower(tp)
    if kept.get("pop") and source is not None and mr._units_record(tp) is None and mr.timed(source):
        try:
            words = source.GetInput("Text")
        except Exception:
            words = None
        for name in POP_KEYED:
            try:
                mr._delete_feeding(getattr(source, name))
            except Exception:
                pass
        tp.StyledText.ConnectTo(None)
        if isinstance(words, str):
            tp.SetInput("StyledText", words)
        source.Delete()
    try:
        tp.SetData(TAG, None)
    except Exception:
        tp.SetData(TAG, "")


def apply(item, starts_for, options, fps):
    """Puts the highlight on one lone Text+ clip. `starts_for(text, kept)`
    gives its words' starts in timeline frames from the clip's first frame,
    and how they were timed ("manual", "transcript" or "even") - `kept` being
    the starts set by hand the clip already has (kept_starts), or None.
    Returns {"pops", "timing"}, or None when the clip isn't a lone Text+.
    Raises with a reason."""
    found = _found(item)
    if found is None:
        return None
    index, comp, tp = found
    if mr.chained(tp):
        raise RuntimeError(mr.CHAINED)      # its words can't be read (mr.chained), so nothing is begun
    text = mr._text_of(tp)
    if not text.strip():
        raise RuntimeError("its Text+ has no text")
    clip_frames = int(item.GetDuration() or 0)
    base, frames = mr.comp_frames(comp, clip_frames)
    ratio = frames / max(1, clip_frames)
    starts, timing = starts_for(text, kept_starts(record(tp), text))
    seconds = [s / fps for s in starts]
    starts = [s * ratio for s in starts]
    backup = mr._export(item, index)
    if backup is None:
        raise RuntimeError("Resolve wouldn't save a copy of its Fusion composition first")
    try:
        comp.Lock()
        comp.StartUndo("Buddy highlight")
        try:
            if record(tp) is not None:
                _take_out(comp, tp)
            # Mid-clip: Animate by's words have come in by then (at the
            # playhead they may not have).
            ink = measure(comp, tp, text, base + frames // 2)
            pops = _build(comp, tp, text, starts, ink, options, base, frames, fps * ratio, timing, seconds)
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
    return {"pops": pops, "timing": timing}


def remove(item):
    """Takes the highlight off a clip. False when it had none."""
    found = _found(item)
    if found is None or record(found[2]) is None:
        return False
    index, comp, tp = found
    if (record(tp) or {}).get("pop") and mr._units_record(tp) is None and mr.chained(tp):
        raise RuntimeError(mr.CHAINED)      # as _take_out would, before anything is exported
    backup = mr._export(item, index)
    if backup is None:
        raise RuntimeError("Resolve wouldn't save a copy of its Fusion composition first")
    try:
        comp.Lock()
        try:
            _take_out(comp, tp)
        finally:
            comp.Unlock()
        if not mr._reload(item, index):
            raise RuntimeError("Resolve didn't take the updated Fusion composition")
    except Exception:
        mr._restore(item, index, backup)
        raise
    finally:
        mr._forget(backup)
    return True


# ---------------------------------------------------------------- the jobs --

def _clips(timeline):
    """[(item, text)] for the selected lone Text+ clips, and how many other
    clips are selected."""
    clips, other = [], 0
    for item, track_type in mr.selected_items(timeline):
        found = _found(item) if mr.classify(item, track_type) == mr.TITLE else None
        if found is None:
            other += 1
            continue
        clips.append((item, mr._text_of(found[2])))
    return clips, other


def run_apply(controller, options, transcripts_dir, name_of=lambda item: item.GetName()):
    """The highlight on every selected Text+ clip, each word timed by the
    transcript that matches the clips best (or evenly, where none does).
    A clip whose words were timed by hand keeps that timing. `options` None
    is Refresh: each clip that has a highlight gets it again with its own
    options, measured for its words as they are now; the rest are left.
    Returns {"applied", "hand", "spoken", "even", "popless", "other",
    "failed": [(clip, reason)], "transcript": its file name or "", "seconds"}."""
    started = time.monotonic()
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    zero = int(timeline.GetStartFrame() or 0)
    refresh = options is None
    options = None if refresh else hl.clean_options(options)
    clips, other = _clips(timeline)
    spans = {id(item): ((item.GetStart() - zero) / fps, (item.GetEnd() - zero) / fps) for item, _t in clips}
    path, words = hl.best_transcript(transcripts(transcripts_dir),
                                     [(text, *spans[id(item)]) for item, text in clips])
    applied = hand = spoken = even = popless = 0
    failed = []
    for item, _text in clips:
        start, end = spans[id(item)]

        def starts_for(text, kept, start=start, end=end):
            if kept is not None:
                return [t * fps for t in kept], "manual"
            times, how = hl.word_times(text, words, start, end)
            return [(t - start) * fps for t in times], how

        own = options
        if refresh:
            found = _found(item)
            rec = record(found[2]) if found is not None else None
            if rec is None:
                other += 1              # no highlight to measure again
                continue
            own = hl.clean_options(rec.get("options"))
        try:
            done = apply(item, starts_for, own, fps)
        except Exception as exc:  # noqa: BLE001 - one clip's trouble is reported, the rest go on
            failed.append((name_of(item), str(exc)))
            continue
        if done is None:
            other += 1
            continue
        applied += 1
        if done["timing"] == "manual":
            hand += 1
        elif done["timing"] == "transcript":
            spoken += 1
        else:
            even += 1
        popless += own["pop"] != "off" and not done["pops"]
    return {"applied": applied, "hand": hand, "spoken": spoken, "even": even, "popless": popless, "other": other,
            "failed": failed, "transcript": path.name if path else "",
            "seconds": round(time.monotonic() - started, 2)}


def run_remove(controller, name_of=lambda item: item.GetName()):
    """Takes the highlight off every selected clip that has one."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    removed, failed = 0, []
    for item, track_type in mr.selected_items(timeline):
        if mr.classify(item, track_type) != mr.TITLE:
            continue
        try:
            removed += bool(remove(item))
        except Exception as exc:  # noqa: BLE001
            failed.append((name_of(item), str(exc)))
    return {"removed": removed, "failed": failed}


def _selected(timeline, clip_id):
    """The selected lone Text+ clip with that unique id, or None."""
    for item, track_type in mr.selected_items(timeline):
        if mr.classify(item, track_type) == mr.TITLE and item.GetUniqueId() == clip_id:
            return item
    return None


def clean_starts(starts, count, duration, fps):
    """Word starts from the timing strip, made safe: `count` numbers, each on
    a frame, inside the clip and a frame after the one before. Raises
    ValueError for the wrong count or something that isn't a number."""
    if not isinstance(starts, list) or len(starts) != count:
        raise ValueError("the timing is for other words")
    frame = 1.0 / fps
    last = max(0.0, duration - frame)
    out = []
    for i, t in enumerate(starts):
        t = round(float(t) * fps) / fps
        t = min(max(t, out[-1] + frame if out else 0.0), last)
        out.append(t)
    return out


def apply_timing(controller, clip_id, starts, options):
    """The highlight on the selected caption `clip_id`, its words starting at
    `starts` (seconds from the clip's start, set by hand) - kept with it.
    Returns {"name", "pops"}. Raises with a reason."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    item = _selected(timeline, clip_id)
    if item is None:
        raise RuntimeError("That caption isn't selected in Resolve's timeline any more – select it again.")
    fps = _fps(timeline)
    start, end = _span(item, timeline, fps)
    picked = {}

    def starts_for(text, _kept):
        try:
            picked["starts"] = clean_starts(starts, len(hl.words_of(text)), end - start, fps)
        except (TypeError, ValueError):
            raise RuntimeError("Its words have changed since the timing was set – check it again.") from None
        return [t * fps for t in picked["starts"]], "manual"

    done = apply(item, starts_for, hl.clean_options(options), fps)
    if done is None:
        raise RuntimeError("It isn't a Text+ caption.")
    return {"name": item.GetName(), "pops": done["pops"]}


def read_playhead(controller, clip_id):
    """Where Resolve's playhead is on the selected caption `clip_id`, in
    seconds from its start - None when it's somewhere else."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        return None
    item = _selected(timeline, clip_id)
    return None if item is None else _playhead(timeline, item, _fps(timeline))
