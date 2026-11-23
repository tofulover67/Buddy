#!/usr/bin/env python3
"""
The only place Ask Buddy is allowed to CHANGE a Resolve project.

resolve_ext.py in this package is strictly read-only and stays that way.
Everything that writes lives here, behind two independent gates:

  1. core/write_consent.py  - the user typed a sentence to enable writing
                              at all. Checked in page.py before the tool is
                              even offered to the model.
  2. the preview card       - the model PROPOSES; this module builds an
                              exact list of what would change; the user
                              presses Apply. Nothing here runs from the
                              agent loop.

That split is the whole design. A model being wrong in prose is a bad
answer; a model being wrong about which clip it meant is lost work, and
the two failures need different defences. Consent covers the capability,
the preview covers the specific change.

SCOPE: full. Actions fall into two groups and the split is the thing to
preserve:

  additive     markers, bins, timelines, renames, clip colours, appending
               to the END of a timeline. Nothing already in place moves.
  destructive  inserts that ripple, deletion of clips/timelines/bins, and
               project or timeline settings. These change or destroy work
               the user never named, so they set ProposedAction.destructive
               and carry specific warnings the card leads with.

A new action belongs in the second group unless it demonstrably cannot
move or remove anything that already exists. Default to destructive when
unsure - an unnecessary warning costs nothing, a missing one costs a
project.

Previews are built by READING the project first, so the card shows what
will actually happen rather than echoing what the model asked for. When
the model says "rename the three 29.97 clips" and only one exists, the
card says one - which is exactly the discrepancy worth seeing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .resolve_ext import _is_real_media_clip, _safe

# Resolve rejects a colour it does not know, and the API's failure mode is
# a bare False with no reason, so the list is checked up front.
MARKER_COLORS = [
    "Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia",
    "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream",
]
CLIP_COLORS = [
    "Orange", "Apricot", "Yellow", "Lime", "Olive", "Green", "Teal", "Navy",
    "Blue", "Purple", "Violet", "Pink", "Tan", "Beige", "Brown", "Chocolate",
]

# One proposal cannot touch more than this. A model that has misunderstood
# the question tends to misunderstand it across the whole timeline, and a
# capped blast radius turns that into an obvious refusal rather than a
# hundred silent changes.
MAX_ITEMS_PER_ACTION = 50


class ActionError(ValueError):
    """Bad proposal. The message goes back to the model AND to the user, so
    it says what was wrong rather than just that something was."""


@dataclass
class ProposedAction:
    """What the card renders and what Apply later executes.

    `details` is the point of the whole mechanism: one line per item that
    will actually change, built from the live project. A summary alone
    ("add 12 markers") hides the case where the model picked the wrong 12.
    """

    action_id: str
    summary: str
    details: list[str] = field(default_factory=list)
    args: dict = field(default_factory=dict)
    # One line from the model on why, shown on the card above the items.
    reason: str = ""
    # Resolved during preview so Apply does not have to look anything up a
    # second time and risk resolving to different objects than were shown.
    plan: list = field(default_factory=list)
    # True for anything that moves or destroys work the user did not
    # name. The card leads with this rather than burying it in the item
    # list, because "add 3 markers" and "delete 3 timelines" must not
    # look alike to someone skimming.
    destructive: bool = False
    # Specific consequences in plain language. Not decoration -
    # "everything after the playhead shifts later" is the part a user
    # needs BEFORE pressing Apply, not after.
    warnings: list = field(default_factory=list)
    # Which project (and, for timeline actions, which timeline) the preview
    # was built from. Filled in by preview() and checked by execute(): the
    # plan above is only meaningful against the objects it was read from,
    # and Apply can be pressed long after the user has switched timelines.
    target: dict = field(default_factory=dict)
    # Filled in by the executor of a REVERTIBLE_ACTIONS change: per item
    # Resolve accepted, what was there before - what revert() puts back.
    undo: list = field(default_factory=list)

    def as_text(self) -> str:
        lines = [self.summary]
        lines.extend(f"  - {d}" for d in self.details)
        lines.extend(f"  ! {w}" for w in self.warnings)
        return "\n".join(lines)


# --------------------------------------------------------------- helpers


def _require_project(controller):
    project = _safe(controller.current_project)
    if not project:
        raise ActionError("No project is open in Resolve.")
    return project


def _require_timeline(controller):
    timeline = _safe(_require_project(controller).GetCurrentTimeline)
    if not timeline:
        raise ActionError("No timeline is open in the current project.")
    return timeline


def _require_pool(controller):
    pool = _safe(_require_project(controller).GetMediaPool)
    if not pool:
        raise ActionError("Could not access the Media Pool.")
    return pool


def _identity(obj) -> dict:
    """{"id", "name"} for a project or timeline. GetUniqueId() is preferred
    because names repeat across projects and the API hands back a fresh
    wrapper object on every call, so object identity means nothing."""
    return {
        "id": str(_safe(lambda: obj.GetUniqueId(), "") or ""),
        "name": str(_safe(lambda: obj.GetName(), "") or ""),
    }


def _target_identity(controller, action_id) -> dict:
    target = {"project": _identity(_require_project(controller))}
    if action_id in TIMELINE_ACTIONS:
        target["timeline"] = _identity(_require_timeline(controller))
    return target


def _check_target(controller, action_id, recorded):
    """Refuse when the project or timeline is no longer the one the preview
    was built from. Every execute_* re-resolves "the current timeline", so
    without this an Apply pressed after switching timelines would write the
    previewed plan into whatever is open now."""
    if not recorded:
        return
    current = _target_identity(controller, action_id)
    for kind in ("project", "timeline"):
        was, now = recorded.get(kind), current.get(kind)
        if was is None or now is None:
            continue
        same = (
            was["id"] == now["id"] if was["id"] and now["id"]
            else was["name"] == now["name"]
        )
        if not same:
            change = f"'{was['name']}' -> '{now['name']}'"
            if was["name"] == now["name"]:
                change = f"'{was['name']}' is now a different {kind} of the same name"
            raise ActionError(
                f"The {kind} changed since this was proposed ({change}); "
                "nothing was changed. Ask again."
            )


def _check_color(value, allowed, label):
    if value not in allowed:
        raise ActionError(
            f"'{value}' is not a {label} colour Resolve accepts. "
            f"Valid: {', '.join(allowed)}."
        )
    return value


def _check_count(items, what):
    if not items:
        raise ActionError(f"No {what} were given, so there is nothing to do.")
    if len(items) > MAX_ITEMS_PER_ACTION:
        raise ActionError(
            f"{len(items)} {what} is more than one action may change "
            f"({MAX_ITEMS_PER_ACTION} max). Propose a smaller change."
        )


def _pool_clips(controller):
    """Every real clip in the Media Pool, walked depth-first.

    Timelines appear in bins as pseudo-clips; the same guard used by
    batch_clip_renamer keeps them out, so "rename every clip" can never
    rename a timeline.
    """
    pool = _require_pool(controller)
    root = _safe(pool.GetRootFolder)
    if not root:
        raise ActionError("Could not access the Media Pool's root folder.")

    found, stack = [], [root]
    while stack:
        folder = stack.pop()
        for clip in _safe(folder.GetClipList, []) or []:
            if _is_real_media_clip(clip):
                found.append(clip)
        stack.extend(_safe(folder.GetSubFolderList, []) or [])
    return found


# ---------------------------------------------------------- add_markers


def _timeline_fps(controller, timeline) -> float:
    """The timeline's frame rate (its own setting, else the project's), or 0."""
    for source in (timeline, _safe(controller.current_project)):
        value = _safe(lambda s=source: s.GetSetting("timelineFrameRate"), "")
        try:
            fps = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        if fps > 0:
            return fps
    return 0.0


def _marker_frame(marker, start, fps) -> int:
    """Where one marker goes, as an offset from the timeline's start.

    Three ways to say it, so a chapter list or a transcript can be turned
    into markers without the model doing frame arithmetic it would get
    wrong: 'frame' (the offset itself), 'time' (how far in - "2:15",
    "1:02:15" or seconds) or 'timecode' (the timeline's own, "01:02:15:00").
    """
    if marker.get("frame") is not None:
        try:
            return int(marker.get("frame"))
        except (TypeError, ValueError):
            raise ActionError(
                f"Marker {marker!r} has no usable 'frame'. Frames are "
                "offsets from the timeline start, as project_state reports "
                "them under 'frame_offset'."
            )
    if marker.get("time") is None and marker.get("timecode") is None:
        raise ActionError(
            f"Marker {marker!r} has no usable 'frame'. Frames are "
            "offsets from the timeline start, as project_state reports "
            "them under 'frame_offset'. A 'time' into the timeline (\"2:15\") or its "
            "'timecode' (\"01:02:15:00\") works too."
        )
    if not fps:
        raise ActionError("The timeline's frame rate couldn't be read, so a time can't be turned into a "
                          "frame. Give the marker's 'frame' instead.")
    if marker.get("timecode") is not None:
        from pages.stills_exporter.resolve_ext import timecode_to_frames
        try:
            return timecode_to_frames(str(marker["timecode"]).strip(), fps) - int(start)
        except (TypeError, ValueError):
            raise ActionError(f"'{marker['timecode']}' isn't a timecode like 01:02:15:00.")
    text = str(marker["time"]).strip()
    try:
        parts = [float(p) for p in text.split(":")]
        if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
            raise ValueError(text)
    except ValueError:
        raise ActionError(f"'{text}' isn't a time like 2:15 or 1:02:15.")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return int(round(seconds * fps))


def preview_add_markers(controller, args) -> ProposedAction:
    timeline = _require_timeline(controller)
    markers = args.get("markers") or []
    _check_count(markers, "markers")

    existing = _safe(timeline.GetMarkers, {}) or {}
    start = _safe(timeline.GetStartFrame, 0) or 0
    name = _safe(timeline.GetName, "the timeline")

    fps = _timeline_fps(controller, timeline)
    plan, details, taken = [], [], set()
    for marker in markers:
        if not isinstance(marker, dict):
            raise ActionError(f"Marker {marker!r} should be {{frame, name, color, note}}.")
        frame = _marker_frame(marker, start, fps)
        if frame < 0:
            raise ActionError(f"Frame {frame} is before the timeline starts.")
        if frame in taken:
            raise ActionError(
                f"Two of these markers land on frame {frame}. Resolve allows only one marker per frame."
            )
        taken.add(frame)
        if frame in existing:
            raise ActionError(
                f"There is already a marker at frame {frame} "
                f"({existing[frame].get('name', 'unnamed')}). Resolve allows "
                "only one marker per frame, and this action never replaces "
                "an existing one."
            )
        color = _check_color(marker.get("color") or "Blue", MARKER_COLORS, "marker")
        label = (marker.get("name") or "").strip()
        note = (marker.get("note") or "").strip()
        plan.append((frame, color, label, note))
        details.append(
            f"frame {frame} (timeline frame {frame + start}) – "
            f"{color} – {label or 'unnamed'}"
            + (f" – note: {note}" if note else "")
        )

    return ProposedAction(
        action_id="add_markers",
        summary=f"Add {len(plan)} marker(s) to '{name}'.",
        details=details,
        args=args,
        plan=plan,
    )


def execute_add_markers(controller, proposal, log=lambda m: None) -> str:
    timeline = _require_timeline(controller)
    done = 0
    for frame, color, label, note in proposal.plan:
        ok = timeline.AddMarker(frame, color, label, note, 1)
        if ok:
            done += 1
            proposal.undo.append((frame, color, label))
            log(f"Added {color} marker at frame {frame}.")
        else:
            # Keep going: a refused marker is not a reason to abandon the
            # ones that would succeed, and the count reported back is real.
            log(f"Resolve refused a marker at frame {frame}.")
    return f"Added {done} of {len(proposal.plan)} marker(s)."


# ------------------------------------------------------------ create_bin


def preview_create_bin(controller, args) -> ProposedAction:
    name = (args.get("name") or "").strip()
    if not name:
        raise ActionError("A bin needs a name.")

    pool = _require_pool(controller)
    root = _safe(pool.GetRootFolder)
    if not root:
        raise ActionError("Could not access the Media Pool's root folder.")

    existing = [f.GetName() for f in (_safe(root.GetSubFolderList, []) or [])]
    if name in existing:
        raise ActionError(
            f"A root-level bin named '{name}' already exists, so there is "
            "nothing to create."
        )

    return ProposedAction(
        action_id="create_bin",
        summary=f"Create a new Media Pool bin named '{name}'.",
        details=[f"at the root level, alongside: {', '.join(existing) or 'nothing'}"],
        args=args,
        plan=[name],
    )


def execute_create_bin(controller, proposal, log=lambda m: None) -> str:
    pool = _require_pool(controller)
    root = _safe(pool.GetRootFolder)
    name = proposal.plan[0]
    if pool.AddSubFolder(root, name) is None:
        raise ActionError(f"Resolve refused to create a bin named '{name}'.")
    log(f"Created bin '{name}'.")
    return f"Created the bin '{name}'."


# ---------------------------------------------------------- rename_clips


def preview_rename_clips(controller, args) -> ProposedAction:
    renames = args.get("renames") or []
    _check_count(renames, "renames")

    clips = _pool_clips(controller)
    by_name = {}
    for clip in clips:
        current = _safe(lambda c=clip: c.GetClipProperty("Clip Name"), "") or ""
        by_name.setdefault(current, []).append(clip)

    plan, details, seen = [], [], set()
    for entry in renames:
        old = (entry.get("from") or "").strip()
        new = (entry.get("to") or "").strip()
        if not old or not new:
            raise ActionError(
                f"Each rename needs a 'from' and a 'to'; got {entry!r}."
            )
        matches = by_name.get(old) or []
        if not matches:
            raise ActionError(
                f"No Media Pool clip is named '{old}'. Nothing was changed. "
                "Read the project first rather than guessing clip names."
            )
        if len(matches) > 1:
            raise ActionError(
                f"{len(matches)} clips are named '{old}'. Renaming would be "
                "ambiguous, so nothing was changed."
            )
        if old in seen:
            raise ActionError(f"'{old}' is listed twice in the same proposal.")
        seen.add(old)
        plan.append((matches[0], old, new))
        details.append(f"'{old}'  ->  '{new}'")

    return ProposedAction(
        action_id="rename_clips",
        summary=f"Rename {len(plan)} Media Pool clip(s).",
        details=details,
        args=args,
        plan=plan,
    )


def execute_rename_clips(controller, proposal, log=lambda m: None) -> str:
    done = 0
    for clip, old, new in proposal.plan:
        if clip.SetClipProperty("Clip Name", new):
            done += 1
            proposal.undo.append((clip, old, new))
            log(f"Renamed '{old}' to '{new}'.")
        else:
            log(f"Resolve refused to rename '{old}'.")
    return f"Renamed {done} of {len(proposal.plan)} clip(s)."


# ------------------------------------------------------- set_clip_colors


def preview_set_clip_colors(controller, args) -> ProposedAction:
    color = _check_color(args.get("color") or "", CLIP_COLORS, "clip")
    targets = args.get("clips") or []
    _check_count(targets, "clips")

    # Built once: GetItemListInTrack is an IPC round trip per track.
    items = _timeline_items(controller)

    plan, details = [], []
    for track, index, item in _find_timeline_clips(items, targets):
        plan.append(item)
        details.append(f"{track} clip {index}: '{_safe(item.GetName, '')}'  ->  {color}")

    return ProposedAction(
        action_id="set_clip_colors",
        summary=f"Set {len(plan)} timeline clip(s) to the colour {color}.",
        details=details,
        args=args,
        plan=[(item, color) for item in plan],
    )


def execute_set_clip_colors(controller, proposal, log=lambda m: None) -> str:
    """Read back, not trusted: on a title or generator SetClipColor says True and
    nothing changes."""
    done = unstuck = 0
    for item, color in proposal.plan:
        name = _safe(item.GetName, '')
        before = _safe(item.GetClipColor, "") or ""
        if not item.SetClipColor(color):
            log(f"Resolve refused to colour '{name}'.")
        elif _safe(item.GetClipColor, color) != color:
            unstuck += 1
            log(f"Resolve said it coloured '{name}', but the colour didn't stay - titles and "
                "generators can't take a clip colour. A marker on it would work instead.")
        else:
            done += 1
            proposal.undo.append((item, before, color))
            log(f"Coloured '{name}' {color}.")
    tail = f" {unstuck} didn't keep the colour (titles and generators can't have one)." if unstuck else ""
    return f"Coloured {done} of {len(proposal.plan)} clip(s).{tail}"


# -------------------------------------------------------- add_clip_flags


def preview_add_clip_flags(controller, args) -> ProposedAction:
    """A flag on each clip. Flags stack - a clip can carry several - so one
    more takes nothing away; a clip that already has this colour is left
    out, and the card says so."""
    color = _check_color(args.get("color") or "", MARKER_COLORS, "flag")
    targets = args.get("clips") or []
    _check_count(targets, "clips")
    items = _timeline_items(controller)

    plan, details = [], []
    for track, index, item in _find_timeline_clips(items, targets):
        name = _safe(item.GetName, "")
        if color in (_safe(lambda i=item: i.GetFlagList(), []) or []):
            details.append(f"{track} clip {index}: '{name}' already has a {color} flag – left as it is")
            continue
        plan.append(item)
        details.append(f"{track} clip {index}: '{name}'  +  {color} flag")
    if not plan:
        raise ActionError(f"Every one of those clips already has a {color} flag, so there is nothing to do.")

    return ProposedAction(
        action_id="add_clip_flags",
        summary=f"Add a {color} flag to {len(plan)} timeline clip(s).",
        details=details,
        args=args,
        plan=[(item, color) for item in plan],
    )


def execute_add_clip_flags(controller, proposal, log=lambda m: None) -> str:
    done = 0
    for item, color in proposal.plan:
        name = _safe(item.GetName, "")
        if item.AddFlag(color):
            done += 1
            log(f"Flagged '{name}' {color}.")
        else:
            log(f"Resolve refused to flag '{name}'.")
    return f"Flagged {done} of {len(proposal.plan)} clip(s)."


# --------------------------------------------------- fill_clip_metadata


def preview_fill_clip_metadata(controller, args) -> ProposedAction:
    """Metadata for Media Pool clips - EMPTY fields only. A field that
    already says something is somebody's work, and overwriting it is not
    additive; the proposal is refused rather than quietly skipping it, so
    the model has to say what's already there."""
    entries = args.get("clips") or []
    _check_count(entries, "clips")

    by_name = {}
    for clip in _pool_clips(controller):
        current = _safe(lambda c=clip: c.GetClipProperty("Clip Name"), "") or ""
        by_name.setdefault(current, []).append(clip)

    plan, details, seen = [], [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ActionError(f"Clip {entry!r} should be {{name, metadata: {{field: value}}}}.")
        name = str(entry.get("name") or "").strip()
        fields = entry.get("metadata")
        if not name or not isinstance(fields, dict) or not fields:
            raise ActionError(f"Each clip needs a 'name' and a 'metadata' object of field: value; got {entry!r}.")
        matches = by_name.get(name) or []
        if not matches:
            raise ActionError(
                f"No Media Pool clip is named '{name}'. Nothing was changed. "
                "Read the project first rather than guessing clip names."
            )
        if len(matches) > 1:
            raise ActionError(f"{len(matches)} clips are named '{name}', so the reference is ambiguous.")
        if name in seen:
            raise ActionError(f"'{name}' is listed twice in the same proposal.")
        seen.add(name)
        clip = matches[0]
        existing = _safe(lambda c=clip: c.GetMetadata(), {}) or {}
        for field_name, value in fields.items():
            field_name, value = str(field_name).strip(), str(value).strip()
            if not field_name or not value:
                raise ActionError(f"'{name}': a metadata field and its value can't be empty.")
            already = existing.get(field_name) if isinstance(existing, dict) else None
            if already not in ("", None):
                raise ActionError(
                    f"'{name}' already has {field_name} set to '{already}', and this action only fills "
                    "empty fields. Nothing was changed; leave that field out, or tell the user it is "
                    "already filled in."
                )
            plan.append((clip, name, field_name, value))
            details.append(f"'{name}': {field_name} = '{value}'")
    if len(plan) > MAX_ITEMS_PER_ACTION:
        raise ActionError(f"{len(plan)} fields is more than one action may change ({MAX_ITEMS_PER_ACTION} max).")

    return ProposedAction(
        action_id="fill_clip_metadata",
        summary=f"Fill in {len(plan)} empty metadata field(s) on {len(seen)} Media Pool clip(s).",
        details=details,
        args=args,
        plan=plan,
    )


def execute_fill_clip_metadata(controller, proposal, log=lambda m: None) -> str:
    done = 0
    for clip, name, field_name, value in proposal.plan:
        # Looked at again: the field may have been filled in since the card appeared.
        now = _safe(lambda c=clip: c.GetMetadata(), {}) or {}
        if isinstance(now, dict) and now.get(field_name) not in ("", None):
            log(f"'{name}': {field_name} has been filled in since – left as it is.")
        elif clip.SetMetadata({field_name: value}):
            done += 1
            log(f"'{name}': {field_name} = '{value}'.")
        else:
            log(f"Resolve refused to set {field_name} on '{name}' – it may not be a field it knows.")
    return f"Filled in {done} of {len(proposal.plan)} metadata field(s)."


# ==========================================================================
# TIMELINE EDITS
#
# Everything below this line can move or destroy work the user did not
# mention. The additive four above cannot. That distinction is carried on
# ProposedAction.destructive and shown on the card, because "add 3 markers"
# and "delete 3 timelines" must not look alike when someone is skimming.
# ==========================================================================


INSERT_KINDS = {
    # kind -> (Timeline method, does it need a name?)
    "fusion_composition": ("InsertFusionCompositionIntoTimeline", False),
    "title": ("InsertTitleIntoTimeline", True),
    "fusion_title": ("InsertFusionTitleIntoTimeline", True),
    "generator": ("InsertGeneratorIntoTimeline", True),
    "fusion_generator": ("InsertFusionGeneratorIntoTimeline", True),
}


def _timelines(controller):
    project = _require_project(controller)
    count = int(_safe(project.GetTimelineCount, 0) or 0)
    out = []
    for i in range(1, count + 1):
        timeline = _safe(lambda n=i: project.GetTimelineByIndex(n))
        if timeline is not None:
            out.append(timeline)
    return out


def _folders(controller):
    """Every bin below the root, depth-first. The root itself is excluded -
    it cannot be deleted and offering it would only invite trying."""
    pool = _require_pool(controller)
    root = _safe(pool.GetRootFolder)
    if not root:
        raise ActionError("Could not access the Media Pool's root folder.")
    found, stack = [], list(_safe(root.GetSubFolderList, []) or [])
    while stack:
        folder = stack.pop()
        found.append(folder)
        stack.extend(_safe(folder.GetSubFolderList, []) or [])
    return found


def _bin_contents(folder):
    """(clip count, [timeline names], sub-bin count) for everything below
    one bin, however deep. Timelines are listed in bins as pseudo-clips, so
    they are told apart from real clips with the same guard _pool_clips
    uses - a card saying "12 clips" when four of them are edits would hide
    exactly the part that cannot be recreated."""
    clips, timelines, subbins = 0, [], 0
    stack = [folder]
    while stack:
        current = stack.pop()
        for clip in _safe(current.GetClipList, []) or []:
            if _is_real_media_clip(clip):
                clips += 1
            else:
                timelines.append(
                    _safe(lambda c=clip: c.GetClipProperty("Clip Name"), "")
                    or _safe(lambda c=clip: c.GetName(), "") or "unnamed"
                )
        children = _safe(current.GetSubFolderList, []) or []
        subbins += len(children)
        stack.extend(children)
    return clips, timelines, subbins


def _by_name(objects, name, what):
    matches = [o for o in objects if (_safe(o.GetName, "") or "") == name]
    if not matches:
        raise ActionError(
            f"No {what} named '{name}' exists. Nothing was changed - read "
            "the project before proposing rather than guessing names."
        )
    if len(matches) > 1:
        raise ActionError(
            f"{len(matches)} {what}s are named '{name}', so the reference is "
            "ambiguous. Nothing was changed."
        )
    return matches[0]


def _timeline_items(controller):
    """{(track, index): item} for every video track."""
    timeline = _require_timeline(controller)
    items = {}
    count = int(_safe(lambda: timeline.GetTrackCount("video"), 0) or 0)
    for track in range(1, count + 1):
        listed = _safe(lambda t=track: timeline.GetItemListInTrack("video", t), []) or []
        for index, item in enumerate(listed, start=1):
            items[(f"V{track}", index)] = item
    return items


def _find_timeline_clips(items, targets):
    """[(track, index, item)] for the model's [{track, index}] references.

    'index' is the clip's 1-based position within its track, exactly as
    project_state's timeline listing reports it. A reference may also carry
    the 'name' and/or 'start' it was read with; when it does they must still
    match, because a position that now holds a different clip is the one
    mistake an index cannot reveal on its own.
    """
    found, seen = [], set()
    for target in targets:
        if not isinstance(target, dict):
            raise ActionError(
                f"Clip {target!r} should be {{track, index}}, as project_state's "
                "timeline listing reports them."
            )
        track = str(target.get("track") or "").strip().upper()
        try:
            index = int(target.get("index"))
        except (TypeError, ValueError):
            raise ActionError(
                f"Clip {target!r} needs a numeric 'index'. Both 'track' and "
                "'index' are reported by project_state's timeline listing."
            )
        item = items.get((track, index))
        if item is None:
            raise ActionError(
                f"There is no clip at {track} index {index}. Nothing was "
                "changed; re-read the timeline before proposing again."
            )
        if (track, index) in seen:
            raise ActionError(f"{track} index {index} is listed twice.")
        seen.add((track, index))

        actual_name = _safe(item.GetName, "") or ""
        name = target.get("name")
        if name is not None and str(name).strip() != actual_name:
            raise ActionError(
                f"{track} index {index} is '{actual_name}', not '{name}'. "
                "Nothing was changed; re-read the timeline before proposing "
                "again."
            )
        start = target.get("start")
        if start is not None:
            actual_start = _safe(item.GetStart, None)
            try:
                matches = float(start) == float(actual_start)
            except (TypeError, ValueError):
                matches = False
            if not matches:
                raise ActionError(
                    f"{track} index {index} ('{actual_name}') starts at "
                    f"{actual_start}, not {start}. Nothing was changed; "
                    "re-read the timeline before proposing again."
                )
        found.append((track, index, item))
    return found


# ------------------------------------------------------ append_to_timeline


def preview_append_to_timeline(controller, args) -> ProposedAction:
    names = args.get("clips") or []
    _check_count(names, "clips")

    pool_clips = _pool_clips(controller)
    by_name = {}
    for clip in pool_clips:
        key = _safe(lambda c=clip: c.GetClipProperty("Clip Name"), "") or ""
        by_name.setdefault(key, []).append(clip)

    timeline = _require_timeline(controller)
    plan, details = [], []
    for name in names:
        name = (name or "").strip()
        matches = by_name.get(name) or []
        if not matches:
            raise ActionError(f"No Media Pool clip is named '{name}'.")
        if len(matches) > 1:
            raise ActionError(f"{len(matches)} clips are named '{name}'.")
        plan.append(matches[0])
        details.append(f"'{name}' appended to the end")

    return ProposedAction(
        action_id="append_to_timeline",
        summary=(
            f"Append {len(plan)} clip(s) to the END of "
            f"'{_safe(timeline.GetName, '')}'."
        ),
        details=details,
        args=args,
        plan=plan,
        # Appending adds after everything else; nothing already on the
        # timeline moves. This is the one timeline edit that is purely
        # additive, which is why it carries no warning.
    )


def execute_append_to_timeline(controller, proposal, log=lambda m: None) -> str:
    pool = _require_pool(controller)
    appended = pool.AppendToTimeline(proposal.plan)
    count = len(appended or [])
    log(f"Appended {count} clip(s).")
    return f"Appended {count} clip(s) to the timeline."


# ----------------------------------------------------- insert_into_timeline


def preview_insert_into_timeline(controller, args) -> ProposedAction:
    kind = (args.get("kind") or "").strip()
    if kind not in INSERT_KINDS:
        raise ActionError(
            f"'{kind}' is not something that can be inserted. "
            f"Valid: {', '.join(sorted(INSERT_KINDS))}."
        )
    method, needs_name = INSERT_KINDS[kind]
    name = (args.get("name") or "").strip()
    if needs_name and not name:
        raise ActionError(
            f"Inserting a {kind.replace('_', ' ')} needs its name, exactly as "
            "it appears in the Effects Library."
        )

    timeline = _require_timeline(controller)
    if not hasattr(timeline, method):
        raise ActionError(
            f"This version of Resolve does not expose {method}()."
        )

    label = kind.replace("_", " ")
    return ProposedAction(
        action_id="insert_into_timeline",
        summary=(
            f"Insert a {label}" + (f" ('{name}')" if name else "")
            + f" at the playhead on '{_safe(timeline.GetName, '')}'."
        ),
        details=[
            f"inserted at the current playhead position",
            "everything to the right of the playhead SHIFTS LATER "
            "(Resolve's insert edit ripples the timeline)",
        ],
        args=args,
        plan=[method, name],
        destructive=True,
        warnings=[
            "This moves clips you did not mention. Check the playhead is "
            "where you think it is before applying."
        ],
    )


def execute_insert_into_timeline(controller, proposal, log=lambda m: None) -> str:
    timeline = _require_timeline(controller)
    method, name = proposal.plan
    call = getattr(timeline, method)
    item = call(name) if name else call()
    if not item:
        raise ActionError(f"Resolve refused the insert ({method}).")
    log(f"Inserted via {method}.")
    return "Inserted at the playhead."


# --------------------------------------------------------- create_timeline


def preview_create_timeline(controller, args) -> ProposedAction:
    name = (args.get("name") or "").strip()
    if not name:
        raise ActionError("A timeline needs a name.")
    existing = [_safe(t.GetName, "") for t in _timelines(controller)]
    if name in existing:
        raise ActionError(f"A timeline named '{name}' already exists.")
    return ProposedAction(
        action_id="create_timeline",
        summary=f"Create a new empty timeline named '{name}'.",
        details=[f"alongside: {', '.join(existing) or 'no other timelines'}"],
        args=args,
        plan=[name],
    )


def execute_create_timeline(controller, proposal, log=lambda m: None) -> str:
    pool = _require_pool(controller)
    name = proposal.plan[0]
    if pool.CreateEmptyTimeline(name) is None:
        raise ActionError(f"Resolve refused to create a timeline named '{name}'.")
    log(f"Created timeline '{name}'.")
    return f"Created the timeline '{name}'."


# ============================== DESTRUCTIVE ===============================


def preview_delete_markers(controller, args) -> ProposedAction:
    timeline = _require_timeline(controller)
    existing = _safe(timeline.GetMarkers, {}) or {}
    if not existing:
        raise ActionError("This timeline has no markers, so there is nothing to delete.")

    color = (args.get("color") or "").strip()
    frames = args.get("frames") or []
    if color and frames:
        raise ActionError("Give either a colour or a list of frames, not both.")

    details = []
    if color:
        if color != "All" and color not in MARKER_COLORS:
            raise ActionError(
                f"'{color}' is not a marker colour. Use one of "
                f"{', '.join(MARKER_COLORS)}, or 'All'."
            )
        doomed = [
            (f, info) for f, info in existing.items()
            if color == "All" or info.get("color") == color
        ]
        if not doomed:
            raise ActionError(f"No {color} markers exist on this timeline.")
        _check_count(doomed, "markers")
    else:
        _check_count(frames, "frames")
        doomed = []
        for frame in frames:
            try:
                key = float(frame)
            except (TypeError, ValueError):
                raise ActionError(f"'{frame}' is not a frame number.")
            match = next((f for f in existing if float(f) == key), None)
            if match is None:
                raise ActionError(f"There is no marker at frame {frame}.")
            doomed.append((match, existing[match]))
    # Exactly the markers listed in the preview, as they are now: Apply
    # deletes these and no others (a colour or a frame could name different
    # ones by then), and only those still as shown.
    plan = [("frames", [(f, info.get("color", ""), info.get("name", "")) for f, info in doomed])]

    for frame, info in sorted(doomed, key=lambda p: float(p[0])):
        details.append(
            f"frame {frame} – {info.get('color', '?')} – "
            f"{info.get('name') or 'unnamed'}"
        )

    return ProposedAction(
        action_id="delete_markers",
        summary=f"DELETE {len(doomed)} marker(s) from "
                f"'{_safe(timeline.GetName, '')}'.",
        details=details,
        args=args,
        plan=plan,
        destructive=True,
        warnings=["Deleted markers and their notes cannot be recovered by Buddy."],
    )


def execute_delete_markers(controller, proposal, log=lambda m: None) -> str:
    timeline = _require_timeline(controller)
    _mode, value = proposal.plan[0]
    now = {float(f): info for f, info in (_safe(timeline.GetMarkers, {}) or {}).items()}
    done = 0
    for frame, color, name in value:
        info = now.get(float(frame))
        if info is None or info.get("color", "") != color or info.get("name", "") != name:
            log(f"Left the marker at frame {frame}: it isn't the one that was shown.")
        elif timeline.DeleteMarkerAtFrame(frame):
            done += 1
            log(f"Deleted the marker at frame {frame}.")
        else:
            log(f"Resolve refused to delete the marker at frame {frame}.")
    left = len(value) - done
    return f"Deleted {done} of {len(value)} marker(s)." + (
        " The rest changed since the preview or were refused - ask again to see what is there now." if left else "")


def preview_delete_timeline_clips(controller, args) -> ProposedAction:
    targets = args.get("clips") or []
    _check_count(targets, "clips")
    ripple = bool(args.get("ripple"))

    items = _timeline_items(controller)
    plan, details = [], []
    for track, index, item in _find_timeline_clips(items, targets):
        plan.append(item)
        details.append(f"{track} clip {index}: '{_safe(item.GetName, '')}'")

    warnings = ["These clips are removed from the timeline.",
                "Only these clips: any audio linked to them stays on its track."]
    if ripple:
        warnings.append(
            "Ripple delete is ON: everything after each removed clip SHIFTS "
            "EARLIER to close the gap."
        )
    else:
        warnings.append("A gap is left where each clip was.")

    return ProposedAction(
        action_id="delete_timeline_clips",
        summary=f"DELETE {len(plan)} clip(s) from the timeline"
                + (" (ripple)" if ripple else ""),
        details=details,
        args=args,
        plan=[plan, ripple],
        destructive=True,
        warnings=warnings,
    )


def execute_delete_timeline_clips(controller, proposal, log=lambda m: None) -> str:
    """On Resolve 21.0, DeleteClips says False on the Fairlight page however often
    it's asked; 21.1
    deletes from every page (checked here on all seven). So a refusal elsewhere
    opens the Edit page, tries once more, and goes back to the page the user was on."""
    timeline = _require_timeline(controller)
    items, ripple = proposal.plan
    if not timeline.DeleteClips(items, ripple):
        resolve = getattr(controller, "resolve", None)
        page = _safe(resolve.GetCurrentPage) if resolve is not None else None
        if not page or page == "edit" or not _safe(lambda: resolve.OpenPage("edit"), False):
            raise ActionError("Resolve refused to delete those timeline clips.")
        try:
            # Switching pages can leave the old timeline handle stale.
            deleted = _require_timeline(controller).DeleteClips(items, ripple)
        finally:
            _safe(lambda: resolve.OpenPage(page))
        if not deleted:
            raise ActionError("Resolve refused to delete those timeline clips.")
        log(f"Resolve only deletes clips on the Edit page, so Buddy opened it for a moment ({page} before).")
    log(f"Deleted {len(items)} timeline clip(s).")
    return f"Deleted {len(items)} clip(s) from the timeline."


def preview_delete_media_pool_items(controller, args) -> ProposedAction:
    clips = args.get("clips") or []
    timelines = args.get("timelines") or []
    bins = args.get("bins") or []
    total = len(clips) + len(timelines) + len(bins)
    if not total:
        raise ActionError("Nothing was listed for deletion.")
    if total > MAX_ITEMS_PER_ACTION:
        raise ActionError(
            f"{total} items is more than one action may delete "
            f"({MAX_ITEMS_PER_ACTION} max)."
        )

    details, warnings = [], []
    clip_objs, timeline_objs, bin_objs = [], [], []

    if clips:
        pool_clips = _pool_clips(controller)
        by_name = {}
        for clip in pool_clips:
            key = _safe(lambda c=clip: c.GetClipProperty("Clip Name"), "") or ""
            by_name.setdefault(key, []).append(clip)
        for name in clips:
            matches = by_name.get((name or "").strip()) or []
            if not matches:
                raise ActionError(f"No Media Pool clip is named '{name}'.")
            if len(matches) > 1:
                raise ActionError(f"{len(matches)} clips are named '{name}'.")
            clip_objs.append(matches[0])
            details.append(f"clip: '{name}'")

    if timelines:
        available = _timelines(controller)
        current = _safe(_require_project(controller).GetCurrentTimeline)
        current_name = _safe(current.GetName, "") if current else ""
        for name in timelines:
            timeline = _by_name(available, (name or "").strip(), "timeline")
            timeline_objs.append(timeline)
            details.append(f"TIMELINE: '{name}'")
            if name == current_name:
                warnings.append(
                    f"'{name}' is the timeline currently open."
                )

    # Deleting a bin takes everything below it, so the cap and the card both
    # count the whole subtree - not just the bin, and not just its top level.
    inside = 0
    timelines_in_bins = 0
    if bins:
        available = _folders(controller)
        current = _safe(_require_project(controller).GetCurrentTimeline)
        current_name = _safe(current.GetName, "") if current else ""
        for name in bins:
            folder = _by_name(available, (name or "").strip(), "bin")
            bin_objs.append(folder)
            n_clips, bin_timelines, n_subbins = _bin_contents(folder)
            inside += n_clips + len(bin_timelines) + n_subbins
            timelines_in_bins += len(bin_timelines)
            details.append(
                f"BIN: '{name}' and everything in it – {n_clips} clip(s), "
                f"{len(bin_timelines)} timeline(s), {n_subbins} sub-bin(s)"
            )
            if bin_timelines:
                warnings.append(
                    f"Bin '{name}' holds {len(bin_timelines)} timeline(s) "
                    f"({', '.join(repr(t) for t in bin_timelines)}); they are "
                    "deleted with it."
                )
            if current_name and current_name in bin_timelines:
                warnings.append(
                    f"'{current_name}', the timeline currently open, is inside "
                    f"bin '{name}'."
                )
        if total + inside > MAX_ITEMS_PER_ACTION:
            raise ActionError(
                f"Deleting those bins would delete {total + inside} items in "
                f"total, counting everything inside them, which is more than "
                f"one action may delete ({MAX_ITEMS_PER_ACTION} max)."
            )

    warnings.insert(
        0,
        "Deleting a timeline destroys the edit it contains. Buddy cannot "
        "bring it back, and Resolve's undo does not always cover media pool "
        "deletions."
        if timelines or timelines_in_bins
        else "This removes items from the Media Pool.",
    )

    return ProposedAction(
        action_id="delete_media_pool_items",
        summary=f"DELETE {total} item(s) from the Media Pool"
                + (f", plus {inside} item(s) inside the bins." if inside else "."),
        details=details,
        args=args,
        plan=[clip_objs, timeline_objs, bin_objs],
        destructive=True,
        warnings=warnings,
    )


def execute_delete_media_pool_items(controller, proposal, log=lambda m: None) -> str:
    pool = _require_pool(controller)
    clip_objs, timeline_objs, bin_objs = proposal.plan
    done = []
    # Timelines first: deleting a bin that holds one would take it anyway,
    # and doing it in this order keeps the log honest about what went.
    if timeline_objs and not pool.DeleteTimelines(timeline_objs):
        raise ActionError("Resolve refused to delete those timelines.")
    if timeline_objs:
        done.append(f"{len(timeline_objs)} timeline(s)")
        log(f"Deleted {len(timeline_objs)} timeline(s).")
    if clip_objs and not pool.DeleteClips(clip_objs):
        raise ActionError("Resolve refused to delete those clips.")
    if clip_objs:
        done.append(f"{len(clip_objs)} clip(s)")
        log(f"Deleted {len(clip_objs)} clip(s).")
    if bin_objs and not pool.DeleteFolders(bin_objs):
        raise ActionError("Resolve refused to delete those bins.")
    if bin_objs:
        done.append(f"{len(bin_objs)} bin(s)")
        log(f"Deleted {len(bin_objs)} bin(s).")
    return "Deleted " + ", ".join(done) + "."


# =========================== PROJECT SETTINGS =============================


def _preview_settings(controller, args, target, label, action_id):
    settings = args.get("settings") or {}
    if not isinstance(settings, dict) or not settings:
        raise ActionError("No settings were given.")
    if len(settings) > MAX_ITEMS_PER_ACTION:
        raise ActionError(f"Too many settings at once ({len(settings)}).")

    details, plan, warnings = [], [], []
    for key, value in settings.items():
        key = str(key).strip()
        value = str(value)
        current = _safe(lambda k=key: target.GetSetting(k), "")
        if current in ("", None):
            # GetSetting returns "" for a key this Resolve version does not
            # have, so a blank reading means the key is probably wrong - and
            # SetSetting would silently do nothing.
            warnings.append(
                f"'{key}' reads back empty, so it may not be a valid setting "
                "key for this version of Resolve."
            )
            details.append(f"{key}: (unset or unknown)  ->  {value}")
        elif str(current) == value:
            raise ActionError(
                f"'{key}' is already '{value}', so there is nothing to change."
            )
        else:
            details.append(f"{key}: {current}  ->  {value}")
        plan.append((key, value))

    warnings.insert(
        0,
        "Changing these alters how every clip in the project is interpreted "
        "and rendered. A wrong value here is not obvious on screen.",
    )
    return ProposedAction(
        action_id=action_id,
        summary=f"Change {len(plan)} {label} setting(s).",
        details=details,
        args=args,
        plan=plan,
        destructive=True,
        warnings=warnings,
    )


def _note_setting(proposal, key, before, value):
    """For revert: only a setting that had a value can be put back."""
    if before not in ("", None):
        proposal.undo.append((key, str(before), value))


def preview_set_project_settings(controller, args) -> ProposedAction:
    return _preview_settings(
        controller, args, _require_project(controller), "project",
        "set_project_settings",
    )


def execute_set_project_settings(controller, proposal, log=lambda m: None) -> str:
    project = _require_project(controller)
    done = 0
    for key, value in proposal.plan:
        before = _safe(lambda k=key: project.GetSetting(k), "")
        if project.SetSetting(key, value):
            done += 1
            _note_setting(proposal, key, before, value)
            log(f"Set {key} = {value}.")
        else:
            log(f"Resolve refused to set {key}.")
    return f"Changed {done} of {len(proposal.plan)} project setting(s)."


def preview_set_timeline_settings(controller, args) -> ProposedAction:
    return _preview_settings(
        controller, args, _require_timeline(controller), "timeline",
        "set_timeline_settings",
    )


def execute_set_timeline_settings(controller, proposal, log=lambda m: None) -> str:
    timeline = _require_timeline(controller)
    done = 0
    for key, value in proposal.plan:
        before = _safe(lambda k=key: timeline.GetSetting(k), "")
        if timeline.SetSetting(key, value):
            done += 1
            _note_setting(proposal, key, before, value)
            log(f"Set {key} = {value}.")
        else:
            log(f"Resolve refused to set {key}.")
    return f"Changed {done} of {len(proposal.plan)} timeline setting(s)."


# ---------------------------------------------------------------- registry
#
# Three tables, deliberately separate, and kept in step by the test suite
# rather than by remembering:
#
#   ACTIONS              id -> (preview, execute)
#   ACTION_SUMMARIES     id -> one line for the user, used by "help"
#   DESTRUCTIVE_ACTIONS  which ones can move or destroy unnamed work
#
# The alternative was describing the actions again in the help text, which
# would have gone stale the first time one was added. test_full_actions.py
# asserts all three have identical keys.

ACTION_SUMMARIES = {
    "add_markers": "add markers to the timeline, at frames, times or timecodes",
    "add_clip_flags": "add a flag to clips on the timeline",
    "fill_clip_metadata": "fill in empty metadata fields on Media Pool clips",
    "create_bin": "create a Media Pool bin",
    "create_timeline": "create a new empty timeline",
    "rename_clips": "rename Media Pool clips",
    "set_clip_colors": "set the colour of clips on the timeline",
    "append_to_timeline": "append clips to the END of the timeline",
    "insert_into_timeline": (
        "insert a Fusion composition, title or generator at the playhead "
        "(this ripples – everything after it shifts later)"
    ),
    "delete_markers": "delete markers, by colour or by frame",
    "delete_timeline_clips": "delete clips from the timeline",
    "delete_media_pool_items": "delete clips, timelines or bins from the Media Pool",
    "set_project_settings": "change project settings (frame rate, colour management…)",
    "set_timeline_settings": "change the current timeline's settings",
}

DESTRUCTIVE_ACTIONS = frozenset({
    "insert_into_timeline",
    "delete_markers",
    "delete_timeline_clips",
    "delete_media_pool_items",
    "set_project_settings",
    "set_timeline_settings",
})

# Actions whose plan belongs to the timeline open at preview time - Apply is
# refused if a different one is open by then. The rest act on the project as
# a whole (bins, Media Pool clips, the timeline list, project settings), so
# only the project has to be the same.
TIMELINE_ACTIONS = frozenset({
    "add_markers",
    "add_clip_flags",
    "set_clip_colors",
    "append_to_timeline",
    "insert_into_timeline",
    "delete_markers",
    "delete_timeline_clips",
    "set_timeline_settings",
})


ACTIONS = {
    # Additive: nothing here moves or removes anything already in place.
    "add_markers": (preview_add_markers, execute_add_markers),
    "add_clip_flags": (preview_add_clip_flags, execute_add_clip_flags),
    "fill_clip_metadata": (preview_fill_clip_metadata, execute_fill_clip_metadata),
    "create_bin": (preview_create_bin, execute_create_bin),
    "create_timeline": (preview_create_timeline, execute_create_timeline),
    "rename_clips": (preview_rename_clips, execute_rename_clips),
    "set_clip_colors": (preview_set_clip_colors, execute_set_clip_colors),
    "append_to_timeline": (preview_append_to_timeline, execute_append_to_timeline),
    # Edits and deletions: these move or destroy work the user may not have
    # mentioned, and every one sets ProposedAction.destructive.
    "insert_into_timeline": (preview_insert_into_timeline, execute_insert_into_timeline),
    "delete_markers": (preview_delete_markers, execute_delete_markers),
    "delete_timeline_clips": (
        preview_delete_timeline_clips, execute_delete_timeline_clips),
    "delete_media_pool_items": (
        preview_delete_media_pool_items, execute_delete_media_pool_items),
    "set_project_settings": (
        preview_set_project_settings, execute_set_project_settings),
    "set_timeline_settings": (
        preview_set_timeline_settings, execute_set_timeline_settings),
}


def preview(controller, action_id, args) -> ProposedAction:
    if action_id not in ACTIONS:
        raise ActionError(
            f"Unknown action '{action_id}'. Available: "
            f"{', '.join(sorted(ACTIONS))}."
        )
    # Read before AND checked after the preview's own reads, so the recorded
    # target is guaranteed to be the one the item list was built from.
    target = _target_identity(controller, action_id)
    proposal = ACTIONS[action_id][0](controller, args or {})
    _check_target(controller, action_id, target)
    proposal.target = target
    # DESTRUCTIVE_ACTIONS is authoritative. An individual preview may also
    # set the flag, but it can never clear it by forgetting to - which is
    # exactly the mistake that would ship a delete looking like an add.
    proposal.destructive = (
        proposal.destructive or action_id in DESTRUCTIVE_ACTIONS
    )
    return proposal


# ------------------------------------------------------------------ plans
#
# Several ADDITIVE actions on one card, applied in order by one Apply:
# "make a bin, flag the mismatched clips, mark where each starts". Never a
# destructive one - those move or remove things, so a later step's item
# list (read before anything ran) could be pointing somewhere else by the
# time it runs. Each step is previewed against the project as it is now,
# which also means a step can't depend on an earlier one's result.

MAX_PLAN_STEPS = 5


@dataclass
class ProposedPlan:
    """What the card shows for a plan - the same face as ProposedAction."""

    steps: list = field(default_factory=list)
    reason: str = ""
    action_id: str = "plan"
    destructive: bool = False       # by construction: only additive steps get in
    warnings: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        return f"{len(self.steps)} changes, applied in this order."

    @property
    def details(self) -> list[str]:
        lines = []
        for n, step in enumerate(self.steps, start=1):
            lines.append(f"{n}. {step.summary}")
            lines.extend(f"      {detail}" for detail in step.details)
        return lines

    def as_text(self) -> str:
        return "\n".join([self.summary] + [f"  {line}" for line in self.details])


def preview_plan(controller, steps) -> ProposedPlan:
    if not isinstance(steps, list) or len(steps) < 2:
        raise ActionError("A plan needs two or more steps; propose a single change on its own.")
    if len(steps) > MAX_PLAN_STEPS:
        raise ActionError(f"{len(steps)} steps is more than one plan may hold ({MAX_PLAN_STEPS} max).")
    built, used = [], set()
    for n, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ActionError(f"Step {n} should be {{action_id, arguments}}.")
        action_id = str(step.get("action_id") or "").strip()
        if action_id in DESTRUCTIVE_ACTIONS:
            raise ActionError(
                f"Step {n} ({action_id}) moves or removes things, and a plan may only hold changes that "
                "add. Propose it on its own, so the user can weigh it by itself."
            )
        if action_id in used:
            raise ActionError(f"{action_id} is in the plan twice; put everything for it in one step.")
        used.add(action_id)
        try:
            proposal = preview(controller, action_id, step.get("arguments"))
        except ActionError as e:
            raise ActionError(f"Step {n} ({action_id or 'no action_id'}): {e}")
        if proposal.destructive:
            raise ActionError(f"Step {n} ({action_id}) turned out to be destructive, so it can't be in a plan.")
        built.append(proposal)
    return ProposedPlan(steps=built)


def _execute_plan(controller, plan, log) -> str:
    # Every step's target first: a timeline switched since the card appeared
    # stops the whole plan before any of it has been applied.
    for step in plan.steps:
        _check_target(controller, step.action_id, step.target)
    outcomes = []
    for n, step in enumerate(plan.steps, start=1):
        try:
            outcomes.append(ACTIONS[step.action_id][1](controller, step, log))
        except Exception as e:  # noqa: BLE001 - reported with what did get done
            done = " ".join(outcomes) or "nothing"
            raise ActionError(f"step {n} ({step.summary}) failed: {e}. Applied before it: {done}")
    return " ".join(outcomes)


def execute(controller, proposal, log=lambda m: None) -> str:
    if isinstance(proposal, ProposedPlan):
        return _execute_plan(controller, proposal, log)
    if proposal.action_id not in ACTIONS:
        raise ActionError(f"Unknown action '{proposal.action_id}'.")
    # One place for every action: the card was built from one project and
    # timeline, and Apply must not land on another.
    _check_target(controller, proposal.action_id, proposal.target)
    return ACTIONS[proposal.action_id][1](controller, proposal, log)


# ----------------------------------------------------------------- revert
#
# Resolve's scripting API has no Undo. For the changes whose old values are
# known - a clip's name, its colour, a setting, a marker that wasn't there -
# the executor notes, per item that Resolve accepted, what was there before
# (ProposedAction.undo), and revert() puts exactly those back. It is as
# careful as Apply: the same project and timeline, and only an item that is
# still as Buddy left it - a clip renamed again since, a marker moved or
# recoloured, is left alone and said so. A marker Buddy added is the one
# thing a revert deletes, and only that marker.

REVERTIBLE_ACTIONS = frozenset({
    "rename_clips", "add_markers", "set_clip_colors", "set_project_settings", "set_timeline_settings",
})


def _same_value(a, b) -> bool:
    """A setting read back against what was written: "24" and "24.0" are one."""
    a, b = str(a).strip(), str(b).strip()
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def _revert_rename_clips(controller, proposal, log) -> str:
    done = 0
    for clip, old, new in reversed(proposal.undo):
        if _safe(lambda c=clip: c.GetClipProperty("Clip Name"), "") != new:
            log(f"Left '{new}': it has been renamed again since.")
        elif clip.SetClipProperty("Clip Name", old):
            done += 1
            log(f"Renamed '{new}' back to '{old}'.")
        else:
            log(f"Resolve refused to rename '{new}' back.")
    return f"Renamed {done} of {len(proposal.undo)} clip(s) back."


def _revert_add_markers(controller, proposal, log) -> str:
    timeline = _require_timeline(controller)
    now = {float(f): info for f, info in (_safe(timeline.GetMarkers, {}) or {}).items()}
    done = 0
    for frame, color, label in reversed(proposal.undo):
        info = now.get(float(frame))
        if info is None:
            log(f"The marker at frame {frame} is already gone.")
        elif info.get("color", "") != color or info.get("name", "") != label:
            log(f"Left the marker at frame {frame}: it has been changed since.")
        elif timeline.DeleteMarkerAtFrame(frame):
            done += 1
            log(f"Took off the marker at frame {frame}.")
        else:
            log(f"Resolve refused to take off the marker at frame {frame}.")
    return f"Took off {done} of {len(proposal.undo)} marker(s) Buddy added."


def _revert_set_clip_colors(controller, proposal, log) -> str:
    done = 0
    for item, before, color in reversed(proposal.undo):
        name = _safe(item.GetName, "")
        if _safe(item.GetClipColor, "") != color:
            log(f"Left '{name}': its colour has been changed since.")
        elif (item.SetClipColor(before) if before else item.ClearClipColor()):
            done += 1
            log(f"Put '{name}' back to {before}." if before else f"Took the colour off '{name}' again.")
        else:
            log(f"Resolve refused to put back the colour of '{name}'.")
    return f"Put back the colour of {done} of {len(proposal.undo)} clip(s)."


def _revert_settings(target, proposal, log, label) -> str:
    done = 0
    for key, before, value in reversed(proposal.undo):
        if not _same_value(_safe(lambda k=key: target.GetSetting(k), ""), value):
            log(f"Left {key}: it has been changed since.")
        elif target.SetSetting(key, before):
            done += 1
            log(f"Set {key} back to {before}.")
        else:
            log(f"Resolve refused to set {key} back to {before}.")
    return f"Put back {done} of {len(proposal.undo)} {label} setting(s)."


def _revert_set_project_settings(controller, proposal, log) -> str:
    return _revert_settings(_require_project(controller), proposal, log, "project")


def _revert_set_timeline_settings(controller, proposal, log) -> str:
    return _revert_settings(_require_timeline(controller), proposal, log, "timeline")


REVERTS = {
    "rename_clips": _revert_rename_clips,
    "add_markers": _revert_add_markers,
    "set_clip_colors": _revert_set_clip_colors,
    "set_project_settings": _revert_set_project_settings,
    "set_timeline_settings": _revert_set_timeline_settings,
}


def _steps(proposal) -> list:
    return list(proposal.steps) if isinstance(proposal, ProposedPlan) else [proposal]


def will_be_revertible(proposal) -> bool:
    """Before Apply: this is a kind of change Revert can put back."""
    return any(step.action_id in REVERTIBLE_ACTIONS for step in _steps(proposal))


def can_revert(proposal) -> bool:
    """After Apply: something was changed whose old value is known."""
    return any(step.action_id in REVERTS and step.undo for step in _steps(proposal))


def revert(controller, proposal, log=lambda m: None) -> str:
    """Put back what an applied proposal changed, newest step first. A plan's
    steps that can't be put back (a bin or timeline it made) stay, and the
    answer says so. The undo notes are used up: a second revert does nothing."""
    steps = [step for step in _steps(proposal) if step.action_id in REVERTS and step.undo]
    if not steps:
        raise ActionError("There is nothing to revert.")
    for step in steps:
        _check_target(controller, step.action_id, step.target)
    outcomes = []
    for step in reversed(steps):
        outcomes.append(REVERTS[step.action_id](controller, step, log))
        step.undo = []
    kept = [step.summary for step in _steps(proposal) if step.action_id not in REVERTS]
    if kept:
        outcomes.append("Not reverted - Buddy can't take these back: " + " ".join(kept))
    return " ".join(outcomes)


def describe_actions() -> str:
    """For the system prompt - the model should not guess action ids."""
    return "\n".join(f"- {aid}" for aid in sorted(ACTIONS))


def help_lines():
    """(additive, destructive) lists of plain-language capability lines.

    Built from the registry, so an action added without a summary shows up
    as a test failure rather than as a silent gap in what Buddy claims it
    can do.
    """
    additive, destructive = [], []
    for aid in sorted(ACTIONS):
        line = ACTION_SUMMARIES.get(aid, aid)
        (destructive if aid in DESTRUCTIVE_ACTIONS else additive).append(line)
    return additive, destructive
