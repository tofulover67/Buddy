"""Where Resolve is: the open project, Media Pool bin and timeline, and the
playhead - a handful of quick calls, for a page to poll through the Resolve
reader's process (core/resolve_reader.py) and see whether anything it shows
has changed, without asking Resolve anything inside Buddy."""


def _id(obj):
    """An object's unique id (its name if it has none), or None."""
    if obj is None:
        return None
    try:
        return obj.GetUniqueId() or obj.GetName()
    except Exception:  # noqa: BLE001
        try:
            return obj.GetName()
        except Exception:  # noqa: BLE001
            return None


def where(controller):
    """{"project", "bin", "root", "timeline", "timecode"}: ids (None for what
    isn't open) and the playhead's timecode ("" with no timeline)."""
    project = controller.current_project()
    if project is None:
        return {"project": None, "bin": None, "root": None, "timeline": None, "timecode": ""}
    pool = project.GetMediaPool()
    root = pool.GetRootFolder() if pool is not None else None
    folder = (pool.GetCurrentFolder() or root) if pool is not None else None
    timeline = project.GetCurrentTimeline()
    return {"project": _id(project), "bin": _id(folder), "root": _id(root), "timeline": _id(timeline),
            "timecode": str(timeline.GetCurrentTimecode() or "") if timeline is not None else ""}
