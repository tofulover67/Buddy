#!/usr/bin/env python3
"""
Project Setup's long Resolve steps - the ones behind the shell's overlay -
as plain data in and plain data out, so the Resolve reader's process can
make them (core/busy_job.py; their names in core/resolve_reader_child.py
JOBS are "project_setup.*") while Buddy keeps drawing. The page makes the
same calls itself, behind its overlay, where that process can't run.

No Resolve object crosses between the two processes: each step finds what
it works on again - the open timeline, the open bin, the Media Pool's
selection - and answers with names, ids, counts, log lines ([text, kind])
and file paths. A Sync job's export is left on disk for the page's worker
to read (read_timeline), and the worker writes the rebuilt timeline there
for the import to find (import_timeline).

Each is called as function(controller, **args): the controller is the
reader's (core.resolve_bridge.ResolveController) or the page's own
(resolve_ext.ProjectSetupController). progress, where there is one, is
the page's alone - the reader's process has no overlay to report to.

No Qt here, directly or through what's imported: that process has none.
"""

from . import align_engine, bins, importer, metadata, otio_engine, sync_report
from .resolve_ext import ProjectSetupController


def _wrap(controller):
    """Project Setup's controller around `controller` - or it, if it's one already."""
    return controller if isinstance(controller, ProjectSetupController) else ProjectSetupController(controller)


def _lines(lines):
    return [[text, kind] for text, kind in lines]


def sync_key(controller, timeline):
    """[project id, timeline id] - the page's _sync_key, as plain data: the
    timelines Remove gaps is offered for."""
    return [metadata.identity(controller.get_project()), metadata.identity(timeline)]


def plain_signature(signature):
    """metadata.signature's (project id, (clip ids)) as JSON keeps it."""
    project, clips = signature
    return [str(project), [str(clip) for clip in clips]]


# ------------------------------------------------------------ Metadata --

def apply_metadata(controller, changes, signature):
    """The Metadata tab's Apply ("project_setup.metadata"): `changes` written
    to the clips selected in the Media Pool - only while they are still the
    ones loaded (`signature`, plain_signature's). {"refused": why} when
    they aren't, or can't be read (nothing written); else {"updated",
    "count", "failures"} (metadata.apply's)."""
    try:
        project = _wrap(controller).get_project()
        clips = metadata.selected_clips(project)
        if plain_signature(metadata.signature(project, clips)) != plain_signature(signature):
            raise metadata.MetadataError("The project or selected clips changed. "
                                         "Load the selection again before applying.")
    except Exception as exc:  # noqa: BLE001 - said in the page, as when it checked there
        return {"refused": str(exc)}
    updated, failures = metadata.apply(clips, changes)
    return {"updated": updated, "count": len(clips), "failures": list(failures)}


# --------------------------------------------------------------- Bins --

def create_bins(controller, rows):
    """The Bins tab's Create ("project_setup.bins"): `rows` (bins.plan's) as
    bins under Master. {"created", "failed", "lines"}."""
    lines = []
    pool = _wrap(controller).get_project().GetMediaPool()
    created, failed = bins.create(pool, rows, lambda text, kind="info": lines.append([text, kind]))
    return {"created": created, "failed": failed, "lines": lines}


# ------------------------------------------------------------- Import --

def import_folder(controller, folder, to_master=False, progress=None):
    """The Import tab's Import ("project_setup.import_folder"): `folder` on
    disk as a bin - in the bin open in the Media Pool, or Master with
    to_master - its subfolders as bins inside, their media imported, and
    the new bin left open. {"result": importer.import_folder's answer
    without its bin object, or None; "lines": what it logged; "failed":
    whether it stopped on an error, "error": that error's text}."""
    lines = []
    try:
        pool = _wrap(controller).get_project().GetMediaPool()
        destination = pool.GetRootFolder() if to_master else (pool.GetCurrentFolder() or pool.GetRootFolder())
        result = importer.import_folder(pool, folder, destination,
                                        lambda text, kind="info": lines.append([text, kind]), progress=progress)
        if result is not None:
            # Leave the new bin open, so what came in is what's on screen.
            try:
                pool.SetCurrentFolder(result["top_bin"])
            except Exception:  # noqa: BLE001
                pass
            result = {key: value for key, value in result.items() if key != "top_bin"}
    except Exception as exc:  # noqa: BLE001 - no project open, or the connection dropped
        return {"result": None, "lines": lines, "failed": True, "error": str(exc)}
    return {"result": result, "lines": lines, "failed": False, "error": ""}


# ----------------------------------------------------------- Populate --

def add_to_timeline(controller, recursive=True):
    """Populate's Add ("project_setup.populate"): the clips in the bin open in
    the Media Pool (and the bins inside it, with recursive) appended to the
    open timeline - or made into a new one named after the bin when none
    is open. {"stage": "" when they went on, else where it stopped - "bin"
    (no bin open: "error" empty), "clips", "empty" or "append" - "error",
    "name" (the bin's), "count" (its clips), "created_new", "placed"}."""
    wrapped = _wrap(controller)
    try:
        folder = wrapped.get_current_bin()
    except Exception as exc:  # noqa: BLE001
        return {"stage": "bin", "error": str(exc)}
    if folder is None:
        return {"stage": "bin", "error": ""}
    name = folder.GetName() or "Master"
    try:
        clips = wrapped.get_bin_clips(folder, recursive=recursive)
    except Exception as exc:  # noqa: BLE001
        return {"stage": "clips", "name": name, "error": str(exc)}
    if not clips:
        return {"stage": "empty", "name": name, "count": 0}
    try:
        created_new, placed = wrapped.append_clips_to_timeline(clips, new_timeline_name=name)
    except Exception as exc:  # noqa: BLE001
        return {"stage": "append", "name": name, "count": len(clips), "error": str(exc)}
    return {"stage": "", "name": name, "count": len(clips), "created_new": bool(created_new), "placed": placed}


# --------------------------------------------------------------- Sync --
# Every one writes a NEW timeline; the one open is never changed.

def expand(controller, progress=None):
    """Expand ("project_setup.expand"): the open timeline one clip a track.
    {"lines"}."""
    _timeline, stats, warnings = otio_engine.expand_timeline(_wrap(controller), progress_cb=progress)
    return {"lines": _lines(sync_report.expand(stats, warnings))}


def align(controller):
    """Align by timecode ("project_setup.align"): each clip moved to its
    offset from the earliest - where the group ends up is Shift's job.
    {"lines", "key": the new timeline's sync_key}."""
    wrapped = _wrap(controller)
    document, clips, workdir = otio_engine.read_current_timeline(wrapped)
    offsets, warnings = otio_engine.compute_offsets_timecode(clips)
    fps = wrapped.get_timeline_fps()
    by_id = {c.clip_id: c for c in clips}
    anchor_id = min(offsets, key=offsets.get)
    anchor = by_id.get(anchor_id)
    frames, shifted = align_engine.offsets_to_frames(
        offsets, fps, snap_to_start=False, anchor_id=anchor_id,
        anchor_frame=anchor.record_frame if anchor else 0)
    timeline, stats = otio_engine.apply_alignment(wrapped, document, clips, frames, workdir)
    key = sync_key(wrapped, timeline)
    return {"lines": _lines(sync_report.align(stats, anchor.name if anchor else None,
                                              anchor.record_frame if anchor else 0, shifted, warnings)),
            "key": key}


def shift(controller, close_gaps=False, targets=None, progress=None):
    """Shift ("project_setup.shift"): the open timeline moved to its first
    frame, the gaps between groups closed with close_gaps. With `targets`
    (sync_keys) it's Remove gaps: only for a timeline among them - synced or
    aligned this session - and the answer's "key" is the one it was.
    {"lines", "key"}."""
    wrapped = _wrap(controller)
    key = None
    if targets is not None:
        key = sync_key(wrapped, wrapped.get_current_timeline())
        if key not in [list(target) for target in targets]:
            raise otio_engine.OtioError("Open a timeline successfully synced or aligned in this session first.")
    _timeline, stats = otio_engine.shift_timeline(wrapped, close_gaps=close_gaps, progress_cb=progress)
    return {"lines": _lines(sync_report.shift(stats, close_gaps)), "key": key}


def read_timeline(controller, tracks=False):
    """A Sync job's start ("project_setup.read_timeline"): the open timeline
    exported, every item where Resolve really has it
    (otio_engine.read_current_timeline), and left on disk at "path" for the
    job's worker to read (otio_engine.load_document). {"path", "workdir",
    "name", and with tracks "tracks": [video, audio] - how many it has}."""
    wrapped = _wrap(controller)
    document, _clips, workdir = otio_engine.read_current_timeline(wrapped)
    timeline = wrapped.get_current_timeline()
    answer = {"path": otio_engine.write_document(document, workdir, "current.otio"), "workdir": workdir,
              "name": timeline.GetName()}
    if tracks:
        answer["tracks"] = [timeline.GetTrackCount("video"), timeline.GetTrackCount("audio")]
    return answer


def import_timeline(controller, path, base_name, suffix, what="rebuilt", key=False):
    """A Sync job's end ("project_setup.import_timeline"): the rebuilt
    timeline its worker wrote at `path` imported as a NEW timeline,
    "<base_name> <suffix>" (otio_engine.import_file). {"name", "tracks":
    [video, audio], and with key "key": its sync_key}."""
    wrapped = _wrap(controller)
    timeline = otio_engine.import_file(wrapped, path, base_name, suffix, what)
    answer = {"name": timeline.GetName(),
              "tracks": [timeline.GetTrackCount("video"), timeline.GetTrackCount("audio")]}
    if key:
        answer["key"] = sync_key(wrapped, timeline)
    return answer
