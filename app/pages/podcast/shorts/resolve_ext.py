#!/usr/bin/env python3
"""
Shorts' calls to Resolve. No Qt: both run in the Resolve reader's process
(core/resolve_reader_child.py JOBS "podshorts.*").

    read(controller)        the open timeline's name, size and the clips
                            selected on it (Timeline.GetSelectedClips,
                            Resolve 21.0.4 and later). Read-only.
    build(controller, ...)  NEW timelines only, when the person presses Make:
                            the open timeline duplicated (so every clip keeps
                            its grade, zoom, titles and links), everything
                            that wasn't selected deleted from the COPY, the
                            sections left moved up against each other, and
                            the copy given the short's shape. A copy that
                            didn't come out right is deleted again. The open
                            timeline isn't touched.
    open_timeline(...)      back to the timeline the shorts were made from.
"""

from pages.podcast.text_edit.resolve_ext import _call, get_timeline, timeline_fps

from . import model as M


class BuildError(RuntimeError):
    """The short couldn't be made; the open timeline is as it was."""


def _int(value, default=0):
    try:
        return int(float(str(value).split()[0]))
    except (TypeError, ValueError, IndexError):
        return default


def _size(timeline):
    return (_int(_call(timeline, "GetSetting", "timelineResolutionWidth", default=0)),
            _int(_call(timeline, "GetSetting", "timelineResolutionHeight", default=0)))


def _can_select(timeline):
    # Resolve's objects answer hasattr() True for any name; dir() tells.
    try:
        return "GetSelectedClips" in dir(timeline)
    except Exception:  # noqa: BLE001
        return False


def read(controller):
    """{"id", "name", "fps", "size", "can_select", "selection": [{"kind",
    "track", "start", "end", "name"}]} - frames from the timeline's start."""
    timeline = get_timeline(controller)
    origin = int(_call(timeline, "GetStartFrame", default=0) or 0)
    can = _can_select(timeline)
    selection = []
    for item in (_call(timeline, "GetSelectedClips", default=[]) or []) if can else []:
        where = _call(item, "GetTrackTypeAndIndex", default=None)
        first, end = _call(item, "GetStart"), _call(item, "GetEnd")
        if not where or len(where) < 2 or first is None or end is None:
            continue
        selection.append({"kind": str(where[0]).lower(), "track": _int(where[1]), "start": int(first) - origin,
                          "end": int(end) - origin, "name": str(_call(item, "GetName", default="") or "")})
    return {"id": str(_call(timeline, "GetUniqueId", default="") or ""),
            "name": str(_call(timeline, "GetName", default="") or ""),
            "fps": timeline_fps(timeline), "size": list(_size(timeline)), "can_select": can, "selection": selection}


def _items(timeline, origin):
    """{(kind, track, start, end): item} for everything on the timeline."""
    out = {}
    for kind in M.KINDS:
        for track in range(1, int(_call(timeline, "GetTrackCount", kind, default=0) or 0) + 1):
            for item in _call(timeline, "GetItemListInTrack", kind, track, default=[]) or []:
                first, end = _call(item, "GetStart"), _call(item, "GetEnd")
                if first is not None and end is not None:
                    out[(kind, track, int(first) - origin, int(end) - origin)] = item
    return out


def _timelines(project):
    out = []
    for index in range(1, int(_call(project, "GetTimelineCount", default=0) or 0) + 1):
        timeline = _call(project, "GetTimelineByIndex", index, default=None)
        if timeline is not None:
            out.append(timeline)
    return out


def _discard(project, timeline):
    """A copy that went wrong, deleted again (only ever one made here)."""
    try:
        project.GetMediaPool().DeleteTimelines([timeline])
    except Exception:  # noqa: BLE001 - it stays, named as the short; the error says so
        pass


def _close_up(copy, parts, origin):
    """Moves each section up against the one before. True if every clip is
    where the short wants it; False (and the clips of a section Resolve
    refused left where they were) if not."""
    for section, to in M.placed(parts):
        if section["start"] == to:
            continue
        have = _items(copy, origin)
        clips = sorted(section["clips"], key=lambda c: c[2])
        items = [have.get(c) for c in clips]
        if any(i is None for i in items):
            return False
        if _call(copy, "MoveItems", items, {"startFrame": origin + to}, False, default=False):
            continue
        # One at a time, earliest first: each lands on frames the ones before it left free.
        shift, done = to - section["start"], []
        for clip, item in zip(clips, items):
            if not _call(item, "SetPosition", {"startFrame": origin + clip[2] + shift}, False, default=False):
                for was, back in reversed(done):
                    _call(back, "SetPosition", {"startFrame": origin + was[2]}, False, default=False)
                return False
            done.append((clip, item))
    wanted = {new for _old, new in M.moved(parts)}
    return set(_items(copy, origin)) == wanted


def _shape(copy, fmt):
    """The copy's own size, filled by the picture (cropped, not letterboxed). True if Resolve took it."""
    _label, width, height = M.FORMATS[fmt]
    if width is None:
        return True
    _call(copy, "SetSetting", "useCustomSettings", "1", default=False)
    _call(copy, "SetSetting", "timelineResolutionWidth", str(width), default=False)
    _call(copy, "SetSetting", "timelineResolutionHeight", str(height), default=False)
    if _size(copy) != (width, height):
        return False
    _call(copy, "SetSetting", "timelineInputResMismatchBehavior", "scaleToCrop", default=False)
    return True


def _make(project, timeline, parts, name, fmt, warnings):
    copy = _call(timeline, "DuplicateTimeline", name, default=None)
    if copy is None:
        raise BuildError("Resolve didn't copy the timeline.")
    try:
        origin = int(_call(copy, "GetStartFrame", default=0) or 0)
        keep = {clip for section in parts for clip in section["clips"]}
        have = _items(copy, origin)
        if keep - set(have):
            raise BuildError("The timeline has changed since Buddy read what's selected – select the clips again.")
        drop = [item for key, item in have.items() if key not in keep]
        if drop:
            _call(copy, "DeleteClips", drop, False, default=False)
            if set(_items(copy, origin)) != keep:
                raise BuildError("Resolve couldn't take the other clips out of the copy – unlock every track and "
                                 "try again.")
        if not _close_up(copy, parts, origin):
            warnings.append(f"“{name}”: the gaps between the sections couldn't be closed – check the clips are "
                            "still in sync, then Edit > Delete Gaps in Resolve closes them.")
        if not _shape(copy, fmt):
            warnings.append(f"“{name}”: Resolve kept the timeline's own size – set it under Timeline > Timeline "
                            "Settings.")
    except Exception:  # noqa: BLE001 - whatever went wrong, no half-made short is left behind
        _discard(project, copy)
        raise
    return {"name": str(_call(copy, "GetName", default="") or name), "frames": M.length(parts),
            "clips": len(keep), "id": str(_call(copy, "GetUniqueId", default="") or "")}, copy


def build(controller, clips, timeline_id="", name="", fmt="vertical", separate=False, open_new=True):
    """{"made": [{"name", "frames", "clips", "id"}], "warnings", "source":
    {"id", "name"}, "opened"} - see the module docstring."""
    project = controller.current_project()
    timeline = get_timeline(controller)
    source = {"id": str(_call(timeline, "GetUniqueId", default="") or ""),
              "name": str(_call(timeline, "GetName", default="") or "")}
    if timeline_id and source["id"] != timeline_id:
        raise BuildError("A different timeline is open now – select the clips again.")
    todo = M.plans(M.clean(clips), bool(separate))
    if not todo:
        raise BuildError("Nothing is selected on the timeline.")
    if fmt not in M.FORMATS:
        fmt = "vertical"
    typed = M.clean_name(name)
    taken = {str(_call(t, "GetName", default="") or "") for t in _timelines(project)}
    new_names = M.names(typed or M.default_name(source["name"]), len(todo), taken,
                        numbered=not typed or len(todo) > 1)
    made, copies, warnings = [], [], []
    try:
        for parts, new_name in zip(todo, new_names):
            try:
                row, copy = _make(project, timeline, parts, new_name, fmt, warnings)
            except BuildError as exc:
                if not made:
                    raise
                warnings.append(f"Only {len(made)} of {len(todo)} shorts were made: {exc}")
                break
            made.append(row)
            copies.append(copy)
    finally:
        # Resolve opens each copy as it's made: the first short, or back where it was.
        try:
            project.SetCurrentTimeline(copies[0] if open_new and copies else timeline)
        except Exception:  # noqa: BLE001 - the shorts are made either way
            pass
    return {"made": made, "warnings": warnings, "source": source, "opened": made[0]["name"] if open_new else ""}


def open_timeline(controller, timeline_id):
    """The timeline with this id made the open one. Its name."""
    project = controller.current_project()
    if project is None:
        raise BuildError("No project is open in Resolve.")
    for timeline in _timelines(project):
        if str(_call(timeline, "GetUniqueId", default="") or "") == timeline_id:
            if not project.SetCurrentTimeline(timeline):
                raise BuildError("Resolve didn't open the timeline.")
            return str(_call(timeline, "GetName", default="") or "")
    raise BuildError("That timeline isn't in this project any more.")


# ----------------------------------------------- in the reader's process --

def read_here(controller):
    return read(controller)


def build_here(controller, clips, timeline_id="", name="", fmt="vertical", separate=False, open_new=True):
    return build(controller, clips, timeline_id, name, fmt, separate, open_new)


def open_here(controller, timeline_id):
    return open_timeline(controller, timeline_id)
