"""What the header says is open in Resolve: the project, the timeline and
that timeline's frame rate and size - so a tool isn't run on the wrong
one. A few quick reads, no Qt: the shell asks through the Resolve reader's
process (core/resolve_reader.py; "resolve.status" in its JOBS)."""


def _number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def fps_text(value) -> str:
    """'24.0' -> '24', '23.976' -> '23.976', 23.976023976 -> '23.976'."""
    number = _number(value)
    if not number or number <= 0:
        return ""
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _setting(timeline, project, key):
    """The timeline's own value (it may not use the project's), else the project's."""
    for owner in (timeline, project):
        try:
            value = owner.GetSetting(key)
        except Exception:  # noqa: BLE001 - an older Resolve, or the object went away
            value = None
        if value not in ("", None):
            return value
    return None


def status(controller) -> dict:
    """{"project", "timeline", "fps", "width", "height"} - names as Resolve
    has them, "" for what isn't open or couldn't be read; fps as fps_text
    writes it, width and height in pixels (0 unknown)."""
    out = {"project": "", "timeline": "", "fps": "", "width": 0, "height": 0}
    try:
        project = controller.current_project()
    except Exception:  # noqa: BLE001
        project = None
    if not project:
        return out
    try:
        out["project"] = str(project.GetName() or "")
        timeline = project.GetCurrentTimeline()
    except Exception:  # noqa: BLE001
        return out
    if not timeline:
        return out
    try:
        out["timeline"] = str(timeline.GetName() or "")
    except Exception:  # noqa: BLE001
        return out
    out["fps"] = fps_text(_setting(timeline, project, "timelineFrameRate"))
    for key, name in (("timelineResolutionWidth", "width"), ("timelineResolutionHeight", "height")):
        number = _number(_setting(timeline, project, key))
        out[name] = int(number) if number and number > 0 else 0
    return out
