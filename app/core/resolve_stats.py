#!/usr/bin/env python3
"""
What Resolve has been doing, read for the achievements (core/achievements.py):
the Deliver page's render jobs and the open timeline's size and grades.

Read-only, and made for the Resolve reader's process
(core/resolve_reader_child.py, as "stats.renders" and "stats.timeline"), so
Buddy never waits on them; core/render_watch.py asks every so often and
turns the answers into stats. No Qt, no Buddy imports beyond the shared
connection error, so the child can import this on its own.

Grades: Resolve's scripting doesn't say "a grade was applied". The nearest
it comes is a clip's node tree - more than one node, or a first node whose
colour has been moved from neutral (its CDL) - so that's what "graded"
means here. A long timeline is read only so far (MAX_ITEMS), one call per
clip being what it costs.
"""

from core.resolve_bridge import ResolveConnectionError

MAX_ITEMS = 600         # clips looked at for grades on one timeline
MAX_JOBS = 400          # render jobs listed


def _project(controller):
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("No project is open in Resolve.")
    return project


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def render_jobs(controller):
    """The project's render jobs that export video, each as {id, name,
    timeline, status, ms}: status is Resolve's ("Complete", "Rendering",
    "Ready", "Failed", "Cancelled") and ms how long a finished one took."""
    project = _project(controller)
    jobs = project.GetRenderJobList() or []
    out = []
    for job in jobs[:MAX_JOBS]:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("JobId") or "")
        if not job_id:
            continue
        if job.get("IsExportVideo") is False:
            continue            # audio only: Buddy's own measuring renders, not a delivery
        try:
            status = project.GetRenderJobStatus(job_id) or {}
        except Exception:  # noqa: BLE001 - a job Resolve won't describe
            status = {}
        out.append({"id": job_id, "name": str(job.get("RenderJobName") or "")[:120],
                    "timeline": str(job.get("TimelineName") or "")[:120],
                    "status": str(status.get("JobStatus") or ""),
                    "ms": max(0, _int(status.get("TimeTakenToRenderInMs")))})
    return out


def _identity_cdl(cdl) -> bool:
    """A CDL that leaves the picture alone: slope and power 1, offset 0, saturation 1."""
    try:
        slope = [float(x) for x in str(cdl.get("Slope", "1 1 1")).split()]
        offset = [float(x) for x in str(cdl.get("Offset", "0 0 0")).split()]
        power = [float(x) for x in str(cdl.get("Power", "1 1 1")).split()]
        sat = float(cdl.get("Saturation", 1))
    except (TypeError, ValueError, AttributeError):
        return True
    near = lambda values, to: all(abs(v - to) < 1e-4 for v in values)   # noqa: E731
    return near(slope, 1) and near(offset, 0) and near(power, 1) and abs(sat - 1) < 1e-4


def _graded(item) -> bool:
    """Whether a clip carries a grade, as far as scripting tells: more than
    one node, or a first node whose colour isn't neutral."""
    nodes = 0
    try:
        graph = item.GetNodeGraph() if hasattr(item, "GetNodeGraph") else None
        nodes = _int(graph.GetNumNodes()) if graph else _int(item.GetNumNodes())
    except Exception:  # noqa: BLE001 - not every clip kind has a node tree
        nodes = 0
    if nodes > 1:
        return True
    try:
        cdl = item.GetCDL()
    except Exception:  # noqa: BLE001
        return False
    return isinstance(cdl, dict) and bool(cdl) and not _identity_cdl(cdl)


def timeline_facts(controller):
    """The open timeline: {id, name, clips, seconds, graded, looked} - clips
    on every video track, its length in seconds, how many of the first
    `looked` clips carry a grade. {} when no timeline is open."""
    project = _project(controller)
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        return {}
    try:
        fps = float(timeline.GetSetting("timelineFrameRate")) or 24.0
    except (TypeError, ValueError):
        fps = 24.0
    frames = max(0, _int(timeline.GetEndFrame()) - _int(timeline.GetStartFrame()))
    clips, graded, looked = 0, 0, 0
    for track in range(1, _int(timeline.GetTrackCount("video")) + 1):
        items = timeline.GetItemListInTrack("video", track) or []
        clips += len(items)
        for item in items:
            if looked >= MAX_ITEMS:
                break
            looked += 1
            if _graded(item):
                graded += 1
    try:
        uid = str(timeline.GetUniqueId() or "")
    except Exception:  # noqa: BLE001 - an older Resolve
        uid = ""
    return {"id": uid or str(timeline.GetName() or ""), "name": str(timeline.GetName() or "")[:120],
            "clips": clips, "seconds": int(frames / fps), "graded": graded, "looked": looked}
