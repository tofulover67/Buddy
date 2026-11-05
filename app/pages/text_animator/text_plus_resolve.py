"""
The Text+ tabs' long edits - text_plus.py's Apply buttons, Space words over the
whole timeline, Stack on their own tracks - with no Qt, so the Resolve
reader's process (core/resolve_reader_child.py) can make them while Buddy keeps
drawing (core/busy_job.py). Resolve's scripting library holds Python's lock for
as long as each call takes, and an Apply over a timeline is thousands of calls:
made on Buddy's UI thread, the busy overlay stood still and Windows called Buddy
"Not Responding". With no reader, Buddy makes them itself, just as before.

Each edit is a function of the clips themselves - (timeline item, video track)
pairs, as text_plus.py finds them - and a *_here twin, the reader's job, given
the clips as [GetUniqueId, track] (a Resolve object can't cross processes):
it finds them on the open timeline first, and a clip that isn't there now is
skipped, as a missing clip always was. The answers are plain data - what each
clip had before, for Undo, by its place in the list asked for ("i"), so the
page keeps its own clip objects for Undo; and the log lines, in order: written
to `log` as they come here, handed back in "log" by a *_here.

What needs Qt - measuring text (canvas_math) - stays in text_plus.py: Space
words and Bounding read here ("textplus.read"), measure there, then write here.
"""

import os
import shutil
import tempfile

from pages.transcribe import drt

from .animation_engine import FusionAnimationEngine as E
from .layout_presets import compute_vertical_stack_centers, group_overlapping_clips
from .live_preview import _read_text_tool_state, parse_point


class TextPlusJobError(RuntimeError):
    """The edit can't be made on what's open in Resolve now - the message says why."""


def _quiet(_line):
    pass


# ------------------------------------------------------------ fusion lookups --

def fusion_comp(clip, log=_quiet):
    """A timeline item's Fusion comp, or None."""
    if clip is None:
        return None
    if callable(getattr(clip, "GetFusionCompByIndex", None)):
        try:
            comp = clip.GetFusionCompByIndex(1)
            if comp is not None:
                return comp
        except Exception as err:
            log(f"  - [GetFusionCompByIndex Error]: {err}")
    if callable(getattr(clip, "GetFusionCompByName", None)):
        try:
            comp = clip.GetFusionCompByName()
            if comp is not None:
                return comp
        except Exception as err:
            log(f"  - [GetFusionCompByName Error]: {err}")
    if callable(getattr(clip, "GetFusionCompList", None)):
        try:
            comps = clip.GetFusionCompList()
            if comps and len(comps) > 0:
                return comps[0]
        except Exception as err:
            log(f"  - [GetFusionCompList Error]: {err}")
    return None


def text_tool(comp, log=_quiet):
    """A comp's (first) Text+ tool, or None."""
    if comp is None:
        return None
    if callable(getattr(comp, "GetToolList", None)):
        try:
            tools = comp.GetToolList(False, "TextPlus")
            if tools:
                return list(tools.values())[0] if isinstance(tools, dict) else tools[0]
        except Exception as err:
            log(f"  - [GetToolList TextPlus Error]: {err}")
    if callable(getattr(comp, "GetToolList", None)):
        try:
            tools = comp.GetToolList(False)
            if tools:
                tool_list = list(tools.values()) if isinstance(tools, dict) else tools
                for t in tool_list:
                    if getattr(t, "ID", None) in ["TextPlus", "Text1", "Text"]:
                        return t
        except Exception as err:
            log(f"  - [GetToolList All Error]: {err}")
    for tool_name in ["TextPlus_1", "TextPlus1", "Text1", "Template"]:
        if callable(getattr(comp, "FindTool", None)):
            try:
                tool = comp.FindTool(tool_name)
                if tool is not None:
                    return tool
            except Exception as err:
                log(f"  - [FindTool '{tool_name}' Error]: {err}")
    return None


def text_tools(comp, log=_quiet, tool_of=None):
    """Every Text+ in a comp - a Buddy title (Animation > Titles) is one per word,
    and a restyle is for all of them. [] when there's none."""
    if comp is None:
        return []
    found = []
    if callable(getattr(comp, "GetToolList", None)):
        try:
            tools = comp.GetToolList(False, "TextPlus")
            found = (list(tools.values()) if isinstance(tools, dict) else list(tools)) if tools else []
        except Exception as err:
            log(f"  - [GetToolList TextPlus Error]: {err}")
    if not found:
        one = (tool_of or (lambda c: text_tool(c, log)))(comp)
        found = [one] if one is not None else []
    return found


def lock_comp(comp, lock, log=_quiet):
    """comp.Lock() / comp.Unlock(), as the animation features do around their edits:
    Fusion renders nothing while locked, and once on Unlock. Never raises."""
    method = getattr(comp, "Lock" if lock else "Unlock", None)
    if callable(method):
        try:
            method()
        except Exception as err:
            log(f"  - [{'Lock' if lock else 'Unlock'} Error]: {err}")


def _lookups(log, comp_of, tool_of):
    """log, comp_of(clip) and tool_of(comp): the page's own (Buddy's process), or these."""
    log = log or _quiet
    return (log, comp_of or (lambda clip: fusion_comp(clip, log)),
            tool_of or (lambda comp: text_tool(comp, log)))


# ------------------------------------------------------------ the reader's side --

def _open_timeline(controller, timeline_id=""):
    """The timeline open in Resolve - the one the page found the clips on."""
    pm = controller.resolve.GetProjectManager()
    project = pm.GetCurrentProject() if pm is not None else None
    timeline = project.GetCurrentTimeline() if project is not None else None
    if timeline is None:
        raise TextPlusJobError("No active timeline found.")
    if timeline_id:
        try:
            now = str(timeline.GetUniqueId() or "")
        except Exception:
            now = ""
        if now and now != str(timeline_id):
            raise TextPlusJobError("A different timeline is open in Resolve now – nothing was changed.")
    return timeline


def find_clips(timeline, refs):
    """[(timeline item or None, track)] for [[GetUniqueId, video track]] - None for one
    not on that track now. Only the tracks named are looked through."""
    wanted = {str(uid) for uid, _track in refs if uid}
    found = {}
    for track in sorted({int(track) for _uid, track in refs}):
        try:
            items = timeline.GetItemListInTrack("video", track) or []
        except Exception:
            continue
        for item in items:
            try:
                uid = str(item.GetUniqueId() or "")
            except Exception:
                continue
            if uid in wanted:
                found[uid] = item
    return [(found.get(str(uid)) if uid else None, int(track)) for uid, track in refs]


def _here(controller, timeline_id, clips, edit, **kwargs):
    """edit() on the clips found again, its log lines in the answer."""
    lines = []
    result = edit(find_clips(_open_timeline(controller, timeline_id), clips), log=lines.append, **kwargs)
    result["log"] = lines
    return result


# ------------------------------------------------------------ reads --

def read_words(clips, log=None, comp_of=None, tool_of=None):
    """{"clips": [{"state", "span"}]}: each clip's Text+ as the canvas reads it
    (live_preview._read_text_tool_state, None without one) and, where it has a
    position, its [start, end] - what Space words sets out over the timeline."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    out = []
    for clip, _track in clips:
        comp = comp_of(clip)
        tool = tool_of(comp) if comp is not None else None
        state = _read_text_tool_state(tool)
        span = None
        if state and state.get("center") is not None:
            try:
                span = [int(clip.GetStart()), int(clip.GetEnd())]
            except Exception:
                span = [0, 0]
        out.append({"state": state, "span": span})
    return {"clips": out}


def read_bounding(clips, log=None, comp_of=None, tool_of=None):
    """{"clips": [record or None]}: what Bounding fits each clip by - its "name",
    whether it has a Text+ ("tool"), its "words", "font", "size" and "center" (or
    the "error" reading them gave), and "chained" for words on a chain, which aren't
    read. None for a clip that isn't there."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    out = []
    for clip, track_index in clips:
        if clip is None:
            out.append(None)
            continue
        record = {"name": clip.GetName() if hasattr(clip, "GetName") else f"Clip on Track {track_index}",
                  "tool": False}
        out.append(record)
        comp = comp_of(clip)
        if comp is None:
            continue
        tool = tool_of(comp)
        if tool is None:
            continue
        record["tool"] = True
        try:
            record.update(words=E.words_of(tool), font=tool.GetInput("Font"), size=tool.GetInput("Size"),
                          center=parse_point(tool.GetInput("Center")))
        except Exception as err:
            record["error"] = str(err)
            continue
        if (record["center"] is not None and isinstance(record["size"], (int, float))
                and record["words"] is None):
            record["chained"] = E.chained(tool)
    return {"clips": out}


def read_here(controller, timeline_id="", clips=(), kind="words"):
    """read_words or read_bounding ("kind") in the reader's process ("textplus.read")."""
    return _here(controller, timeline_id, clips, read_bounding if kind == "bounding" else read_words)


# ------------------------------------------------------------ positions --

def space_words(clips, centers, log=None, comp_of=None, tool_of=None):
    """Space words' writes: each clip's Center to its place in `centers` (Fusion's,
    bottom-up), unless it's there already. {"moved", "steps": [{"i", "old", "new"}]}."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    steps, moved = [], 0
    for i, ((clip, _track), center) in enumerate(zip(clips, centers)):
        new_center = [center[0], center[1]]
        comp = comp_of(clip)
        tool = tool_of(comp) if comp is not None else None
        if tool is None:
            log("[Word-by-word] Could not re-locate the Text+ tool for one word – skipped.")
            continue
        try:
            old = parse_point(tool.GetInput("Center"))
        except Exception:
            old = None
        if old is not None and abs(old[0] - new_center[0]) < 1e-6 and abs(old[1] - new_center[1]) < 1e-6:
            continue
        try:
            tool.SetInput("Center", new_center)
            moved += 1
            if old is not None:
                steps.append({"i": i, "old": list(old), "new": new_center})
        except Exception as err:
            log(f"[Word-by-word] Center SetInput Exception: {err}")
    return {"moved": moved, "steps": steps}


def space_here(controller, timeline_id="", clips=(), centers=()):
    """space_words in the reader's process ("textplus.space")."""
    return _here(controller, timeline_id, clips, space_words, centers=centers)


def apply_position(clips, references, log=None, comp_of=None, tool_of=None):
    """Apply position: each clip's Center to its track's reference ({track: (x, y)},
    the clip under the playhead there); a track without one is left alone.
    {"updated", "scanned", "steps": [{"i", "old", "new"}]}."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    steps, updated, scanned = [], 0, 0
    for i, (clip, track_index) in enumerate(clips):
        reference_center = references.get(track_index)
        if clip is None or reference_center is None:
            continue                # a track with nothing under the playhead: left alone
        scanned += 1
        clip_name = clip.GetName() if callable(getattr(clip, "GetName", None)) else f"Clip on Track {track_index}"
        comp = comp_of(clip)
        tool = tool_of(comp) if comp is not None else None
        if tool is None:
            continue
        try:
            old_center = parse_point(tool.GetInput("Center"))
        except Exception:
            old_center = None
        new_center = [reference_center[0], reference_center[1]]
        if (old_center is not None and abs(old_center[0] - new_center[0]) < 1e-9
                and abs(old_center[1] - new_center[1]) < 1e-9):
            continue
        try:
            tool.SetInput("Center", new_center)
            updated += 1
            if old_center is not None:
                steps.append({"i": i, "old": list(old_center), "new": new_center})
        except Exception as err:
            log(f"  - Warning: Could not set Center for clip '{clip_name}': {err}")
    return {"updated": updated, "scanned": scanned, "steps": steps}


def position_here(controller, timeline_id="", clips=(), references=()):
    """apply_position in the reader's process ("textplus.position"); references as
    [[track, x, y]]."""
    by_track = {int(track): (x, y) for track, x, y in references}
    return _here(controller, timeline_id, clips, apply_position, references=by_track)


def bounding(clips, plan, scanned, scope_desc, width, height, log=None, comp_of=None, tool_of=None):
    """Bounding's writes, from the plan text_plus.py measured (_bounding_plan): per
    clip in order, a "line" to log and, for one it fits, its new "size" and "center";
    "processed" ones are then snapped together vertically where they're on screen at
    the same time, each by its "cap" - (px per size, cap height in ems), what
    canvas_math.cap_height_fraction measures with - as the timeline's width x
    height has it. {"updated"}."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    updated = 0
    processed = []
    for step in plan:
        clip, _track = clips[step["i"]]
        tool = None
        if step.get("processed"):
            comp = comp_of(clip)
            tool = tool_of(comp) if comp is not None else None
            if tool is None:
                continue            # gone since it was read: as a clip without a Text+
        if step.get("line"):
            log(step["line"])
        if tool is None:
            continue
        processed.append((clip, tool, step["name"], step["cap"]))
        if "size" not in step:
            continue
        try:
            tool.SetInput("Size", step["size"])
            tool.SetInput("Center", [step["center"][0], step["center"][1]])
            updated += 1
        except Exception as err:
            log(f"  - Warning: Could not set Size/Center for clip '{step['name']}': {err}")
    log(f"Applied Bounding resize to {updated}/{scanned} clip(s) on {scope_desc}.")
    _snap_stacked_groups(processed, width, height, log)
    return {"updated": updated}


def bounding_here(controller, timeline_id="", clips=(), plan=(), scanned=0, scope_desc="", width=1920, height=1080):
    """bounding in the reader's process ("textplus.bounding")."""
    return _here(controller, timeline_id, clips, bounding, plan=plan, scanned=scanned, scope_desc=scope_desc,
                 width=width, height=height)


def _snap_stacked_groups(records, timeline_width, timeline_height, log):
    """The clips Bounding fitted that are on screen together, stacked vertically
    touching - their Center and Size read again, after the fit."""
    clips_for_grouping = []
    by_key = {}
    for idx, (clip, tool, clip_name, cap) in enumerate(records):
        try:
            start = clip.GetStart() if callable(getattr(clip, "GetStart", None)) else None
            end = clip.GetEnd() if callable(getattr(clip, "GetEnd", None)) else None
        except Exception as err:
            log(f"  - [Bounding] Could not read start/end for clip '{clip_name}' – excluded from vertical stacking: {err}")
            continue
        if start is None or end is None:
            log(f"  - [Bounding] Clip '{clip_name}' has no GetStart()/GetEnd() – excluded from vertical stacking.")
            continue
        clips_for_grouping.append((idx, start, end))
        by_key[idx] = (clip, tool, clip_name, cap)

    groups = group_overlapping_clips(clips_for_grouping)
    aspect_ratio = (timeline_width / timeline_height) if timeline_height else 1.0
    solo_groups = sum(1 for group_keys in groups if len(group_keys) < 2)
    if solo_groups:
        log(
            f"[Bounding] {solo_groups} clip(s) never temporally overlap any other Text+ "
            "clip – their vertical position is left untouched by design (vertical "
            "snapping only applies to clips that are actually stacked together at some "
            "point on the timeline)."
        )

    groups_snapped = 0
    clips_moved = 0
    for group_keys in groups:
        if len(group_keys) < 2:
            continue
        members = []
        for key in group_keys:
            clip, tool, clip_name, cap = by_key[key]
            try:
                center = parse_point(tool.GetInput("Center"))
                font_size = tool.GetInput("Size")
            except Exception as err:
                log(f"  - [Bounding] Could not read Center/Size for clip '{clip_name}' – excluded from vertical stacking: {err}")
                continue
            if center is None or not isinstance(font_size, (int, float)):
                log(f"  - [Bounding] Clip '{clip_name}' has an unreadable Center/Size – excluded from vertical stacking.")
                continue
            # Cap height, as a fraction of composition height (it's measured in widths):
            # canvas_math.cap_height_fraction, from its two measures of the font.
            px_per_size, cap_em = cap
            height_fraction = max(float(font_size), 0.0) * px_per_size * cap_em * aspect_ratio
            try:
                background_enabled = bool(tool.GetInput("Enabled4"))
                extend_vertical = tool.GetInput("ExtendVertical4")
            except Exception:
                background_enabled = False
                extend_vertical = None
            if background_enabled and isinstance(extend_vertical, (int, float)):
                height_fraction += 2.0 * float(extend_vertical)
            members.append((key, center[0], -center[1], height_fraction, clip, tool, clip_name))
        if len(members) < 2:
            continue
        new_centers = compute_vertical_stack_centers([(m[0], m[2], m[3]) for m in members])
        for key, center_x, _neg_center_y, _height_fraction, clip, tool, clip_name in members:
            new_neg_center_y = new_centers.get(key)
            if new_neg_center_y is None:
                continue
            try:
                tool.SetInput("Center", [center_x, -new_neg_center_y])
                clips_moved += 1
            except Exception as err:
                log(f"  - Warning: Could not vertically snap clip '{clip_name}': {err}")
        groups_snapped += 1
    if groups_snapped:
        log(f"[Bounding] Vertically snapped {clips_moved} Text+ clip(s) across {groups_snapped} stacked group(s) on the timeline.")


# ------------------------------------------------------------ animations --

def clip_key(clip, track_index):
    """(name, start, end, track) of a clip, or None when one can't be read."""
    try:
        name = clip.GetName() if callable(getattr(clip, "GetName", None)) else None
        start = clip.GetStart() if callable(getattr(clip, "GetStart", None)) else None
        end = clip.GetEnd() if callable(getattr(clip, "GetEnd", None)) else None
    except Exception:
        return None
    if name is None or start is None or end is None:
        return None
    return (name, start, end, track_index)


def clip_fps(clip, comp, fps):
    """The clip's comp frames per second (FusionAnimationEngine.comp_fps)."""
    try:
        clip_frames = int(clip.GetDuration() or 0)
    except Exception:
        clip_frames = 0
    return E.comp_fps(comp, clip_frames, fps)


def preset_before(tool, comp, fps):
    """The animation this tab has on a clip, for Undo to put back: what the Text+ itself
    records (so it's right after the clip is moved, trimmed or Buddy restarted) - or,
    on a clip an earlier Buddy animated, the preset its keys are, at the speed and
    direction chosen now (those weren't kept). Not the session's memory of clips
    (name, start, end, track): another clip can come to sit at the same spot."""
    recorded = E.preset_on(tool)
    if recorded is not None:
        return recorded
    earlier = E.legacy_preset(tool, comp)
    return (earlier, None, None, fps) if earlier else None


def apply_preset(tool, comp, clip_name, preset_choice, speed, direction, fps=24.0,
                 fallback_center=(0.5, 0.5), log=_quiet):
    """One Text+ animated with a Timeline Animation preset - this tab's own earlier
    one taken off first. False, and nothing changed, where something else already
    animates what it would. A Slide goes to the clip's own Center (fallback_center
    when that can't be read)."""
    try:
        start_frame = float((comp.GetAttrs() or {}).get("COMPN_RenderStart", 0))
    except (AttributeError, TypeError, ValueError):
        start_frame = 0
    if E.conflict(tool, comp, preset_choice):
        # Something that isn't this tab's already animates what the preset would
        # (the callers count these): nothing of the clip is changed.
        log(f"  - Left clip '{clip_name}' as it is: something else already animates it there.")
        return False
    # Only this tab's own earlier animation comes off - see remove_animations_from_clip.
    E.remove_animations_from_clip(tool, comp)
    if preset_choice in E.LETTER_PRESETS:   # before "Pop": "Letter Pop" has it too
        success, preset_logs = E.apply_letter_preset_to_clip(
            tool, comp, preset_choice, speed=speed, start_frame=start_frame, fps=fps)
    elif "Bounce" in preset_choice:
        duration = E.scaled_duration(20, speed, fps)
        success, preset_logs = E.apply_bounce_preset_to_clip(
            tool, comp, start_frame=start_frame, duration=duration)
    elif "Pop" in preset_choice:
        duration = E.scaled_duration(15, speed, fps)
        success, preset_logs = E.apply_pop_preset_to_clip(
            tool, comp, start_frame=start_frame, duration=duration)
    elif "Slide" in preset_choice:
        target_center = fallback_center
        if hasattr(tool, "GetInput"):
            try:
                existing_center = parse_point(tool.GetInput("Center"))
            except Exception:
                existing_center = None
            if existing_center is not None:
                target_center = existing_center
        duration = E.scaled_duration(15, speed, fps)
        success, preset_logs = E.apply_slide_preset_to_clip(
            tool, comp, target_center=target_center, direction=direction,
            start_frame=start_frame, duration=duration)
    else:
        duration = E.scaled_duration(15, speed, fps)
        success, preset_logs = E.apply_fade_preset_to_clip(
            tool, comp, start_frame=start_frame, duration=duration)
    for line in preset_logs:
        log(line)
    if success:
        E.mark_preset(tool, preset_choice, speed, direction, fps)
    else:
        log(f"  - Warning: Could not apply '{preset_choice}' animation to clip '{clip_name}'.")
    return success


def apply_animation(clips, preset, speed, direction, fps, fallback_center=(0.5, 0.5),
                    log=None, comp_of=None, tool_of=None):
    """Apply animation: the preset on each clip's Text+ - not one where something else
    already has the text, size, opacity or pivot ("left"). {"count", "left", "total",
    "steps": [{"i", "old", "new", "key"}]}: what each animated clip had before (for
    Undo, preset_before) and has now, (preset, speed, direction, fps)."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    steps = []
    count = left = 0
    total = len(clips)
    for idx, (clip, track_index) in enumerate(clips, start=1):
        if clip is None:
            continue
        clip_name = clip.GetName() if callable(getattr(clip, "GetName", None)) else f"Clip_{idx}"
        comp = comp_of(clip)
        if comp is None:
            log(f"  - [{idx}/{total}] Warning: Could not access Fusion comp for clip '{clip_name}'.")
            continue
        tool = tool_of(comp)
        if tool is None:
            log(f"  - [{idx}/{total}] Warning: Could not find TextPlus tool inside comp for clip '{clip_name}'.")
            continue
        held = E.conflict(tool, comp, preset)
        if held:
            # Previews' Animate by, the highlight's pop, Character Level Styling or
            # the person's own keys: theirs stays, and this clip is left as it is.
            what = ("its text is already on another Follower or modifier" if held == "text"
                    else "its size, opacity or pivot is already animated")
            log(f"  - [{idx}/{total}] Left '{clip_name}' as it is: {what}.")
            left += 1
            continue
        key = clip_key(clip, track_index)
        comp_fps = clip_fps(clip, comp, fps)
        old_preset = preset_before(tool, comp, comp_fps)
        new_preset = (preset, speed, direction, comp_fps)
        if apply_preset(tool, comp, clip_name, preset, speed, direction, comp_fps, fallback_center, log):
            count += 1
            if key is not None:
                steps.append({"i": idx - 1, "old": old_preset, "new": new_preset, "key": key})
    return {"count": count, "left": left, "total": total, "steps": steps}


def animate_here(controller, timeline_id="", clips=(), preset="", speed="Medium", direction="", fps=24.0,
                 fallback_center=(0.5, 0.5)):
    """apply_animation in the reader's process ("textplus.animate")."""
    return _here(controller, timeline_id, clips, apply_animation, preset=preset, speed=speed,
                 direction=direction, fps=float(fps), fallback_center=tuple(fallback_center))


def remove_animations(clips, fps, log=None, comp_of=None, tool_of=None):
    """Remove animations: this tab's own off each clip's Text+. {"count" (clips it took
    one off), "stuck" (a letter animation that had to stay: a chain behind its
    Follower), "total", "steps": [{"i", "old", "key"}]} - "old" the preset Undo can
    put back."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    steps = []
    count = stuck = 0
    total = len(clips)
    for idx, (clip, track_index) in enumerate(clips, start=1):
        if clip is None:
            continue
        clip_name = clip.GetName() if callable(getattr(clip, "GetName", None)) else f"Clip_{idx}"
        comp = comp_of(clip)
        if comp is None:
            log(f"  - [{idx}/{total}] Warning: Could not access Fusion comp for clip '{clip_name}'.")
            continue
        tool = tool_of(comp)
        if tool is None:
            log(f"  - [{idx}/{total}] Warning: Could not find TextPlus tool inside comp for clip '{clip_name}'.")
            continue
        key = clip_key(clip, track_index)
        old_preset = preset_before(tool, comp, clip_fps(clip, comp, fps))
        # True only where there was an animation of this tab's to take off.
        success, remove_logs = E.remove_animations_from_clip(tool, comp)
        for line in remove_logs:
            log(line)
        if success:
            count += 1
            # Undo can put back a preset it can name (the record, or an earlier Buddy's
            # by its keys); what it can't is no step to undo.
            if key is not None and old_preset is not None:
                steps.append({"i": idx - 1, "old": old_preset, "key": key})
        elif (E.animation_on(tool) or {}).get("follower"):
            stuck += 1      # its letter animation is still on: a chain behind the Follower
    return {"count": count, "stuck": stuck, "total": total, "steps": steps}


def unanimate_here(controller, timeline_id="", clips=(), fps=24.0):
    """remove_animations in the reader's process ("textplus.unanimate")."""
    return _here(controller, timeline_id, clips, remove_animations, fps=float(fps))


# ------------------------------------------------------------ font style --

def style_tool(tool, clip_name, settings, force_font_only=False, log=_quiet):
    """Font Styling on one Text+, by `settings` (text_plus.py's _style_settings).
    (whether anything went on, whether it keeps an outline, shadow or box Font
    Styling has no note of switching on)."""
    s = settings
    success = False
    if s["font_on"] or force_font_only:
        family, style = s["font_face"]
        success, style_logs = E.apply_text_style(
            tool, font_name=family, font_size=s["font_size"], color=s["font_color"], font_style=style)
        for line in style_logs:
            log(line)
    if force_font_only:
        return success, False

    # Outline, Shadow, Box: a ticked one is set (and noted on the Text+ as Font
    # Styling's); an unticked one is switched off only where Font Styling switched it
    # on - an outline the person set in the Inspector is theirs and stays.
    groups = (
        ("outline_on", E.OUTLINE_ELEMENT, "Outline", lambda: E.apply_outline_style(
            tool, color=s["outline_color"], thickness=s["outline_thickness"], opacity=s["outline_opacity"])),
        ("shadow_on", E.SHADOW_ELEMENT, "Shadow", lambda: E.apply_shadow_style(
            tool, color=s["shadow_color"], offset=(s["shadow_offset_x"], s["shadow_offset_y"]),
            blur=s["shadow_blur"], opacity=s["shadow_opacity"])),
        ("background_on", E.BACKGROUND_ELEMENT, "Background", lambda: E.apply_background_style(
            tool, color=s["background_color"], opacity=s["background_opacity"],
            corner_radius=s["background_corner_radius"],
            extend_horizontal=s["background_extend_horizontal"],
            extend_vertical=s["background_extend_vertical"])),
    )
    ours = E.styled_elements(tool)
    tried = [success] if s["font_on"] else []
    kept = False
    for toggle, element, label, apply in groups:
        if s[toggle]:
            done, logs = apply()
            if done:
                E.note_element(tool, element, True)
        elif element in ours:
            done, logs = E.disable_shading_element(tool, element, label)
            if done:
                E.note_element(tool, element, False)
        else:
            kept = kept or E.element_is_on(tool, element)
            continue
        tried.append(done)
        for line in logs:
            log(line)
    if tried and not any(tried):
        log(f"  - Warning: Could not apply font style to clip {clip_name}.")
    return any(tried), kept


def apply_style(clips, settings, force_font_only=False, log=None, comp_of=None, tool_of=None, progress=None):
    """Font Styling on every Text+ of each clip, its comp locked meanwhile: unlocked,
    Resolve redraws after every one of the ~30 inputs set per Text+ and holds the next
    call until it has (~0.3 s each, ~10 s a clip). progress(n, total) before each
    clip, when given. {"updated", "scanned", "kept"} - "kept": the Text+ left with an
    outline, shadow or box Font Styling has no note of."""
    log, comp_of, tool_of = _lookups(log, comp_of, tool_of)
    updated = scanned = kept = 0
    for n, (clip, track_index) in enumerate(clips, 1):
        if progress is not None:
            progress(n, len(clips))
        if clip is None:
            continue
        scanned += 1
        comp = comp_of(clip)
        tools = text_tools(comp, log, tool_of)
        if not tools:
            continue
        clip_name = clip.GetName() if hasattr(clip, "GetName") else "Clip"
        # Every Text+ of the clip: a Buddy title is one per word.
        lock_comp(comp, True, log)
        try:
            styled = [style_tool(tool, f"'{clip_name}' on Track {track_index}", settings, force_font_only, log)
                      for tool in tools]
        finally:
            lock_comp(comp, False, log)
        kept += sum(1 for _ok, keeps in styled if keeps)
        if any(ok for ok, _keeps in styled):
            updated += 1
    return {"updated": updated, "scanned": scanned, "kept": kept}


def style_here(controller, timeline_id="", clips=(), settings=None, force_font_only=False):
    """apply_style in the reader's process ("textplus.style")."""
    return _here(controller, timeline_id, clips, apply_style, settings=dict(settings or {}),
                 force_font_only=bool(force_font_only))


# ------------------------------------------------------------ stack --

def _all_folders(folder):
    found = [folder]
    for sub in folder.GetSubFolderList() or []:
        found.extend(_all_folders(sub))
    return found


def stack_into_new_timeline(resolve, timeline, plan):
    """The timeline exported as .drt, its clips restacked by `plan` ({(track, start):
    (new track, frames)}, word_by_word.stack_plan), imported as a new timeline - bound
    to the Media Pool clips already there - and opened. Its name."""
    project = resolve.GetProjectManager().GetCurrentProject()
    mp = project.GetMediaPool()
    taken = {project.GetTimelineByIndex(i).GetName() for i in range(1, (project.GetTimelineCount() or 0) + 1)}
    name, n = f"{timeline.GetName()} (Word-by-word)", 2
    while name in taken:
        name, n = f"{timeline.GetName()} (Word-by-word {n})", n + 1
    folder = tempfile.mkdtemp(prefix="buddy word-by-word ")
    exported, built = os.path.join(folder, "timeline.drt"), os.path.join(folder, "stacked.drt")
    try:
        if not timeline.Export(exported, resolve.EXPORT_DRT, resolve.EXPORT_NONE) or not os.path.isfile(exported):
            raise drt.DrtError("Resolve couldn't export the timeline.")
        drt.with_video_clips_restacked(exported, built, plan)
        made = mp.ImportTimelineFromFile(built, {
            "timelineName": name, "importSourceClips": False,
            "sourceClipsFolders": _all_folders(mp.GetRootFolder())})
        if not made:
            raise drt.DrtError("Resolve didn't import the new timeline.")
        made.SetName(name)          # a .drt comes in named after the file, whatever timelineName says
        project.SetCurrentTimeline(made)
        return made.GetName()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def stack_here(controller, timeline_id="", plan=()):
    """stack_into_new_timeline in the reader's process ("textplus.stack"); the plan as
    [[track, start, new track, frames]]. {"name"}."""
    timeline = _open_timeline(controller, timeline_id)
    moves = {(int(track), int(start)): (int(new), int(frames)) for track, start, new, frames in plan}
    return {"name": stack_into_new_timeline(controller.resolve, timeline, moves)}
