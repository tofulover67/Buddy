"""Resolve reads and verified metadata writes for Dailies.

The page asks for these through core/resolve_reader.py's process when it can
(scan_rows, apply_by_id, current_location: JSON in and out), since while the
timeline plays Resolve holds every call and a call made inside Buddy froze
it. The Media Pool items a scan found stay with the process that scanned
(_SCANNED), for apply_by_id to write to."""

from core.resolve_bridge import ResolveConnectionError
from pages.project_setup import metadata

from . import model
from .model import media_type


# The latest scan's Media Pool items in this process, by clip id - what
# apply_by_id writes to and reveal_by_id selects: {"project": id,
# "objects": {clip id: item}, "folders": {clip id: the bin it's in}}.
_SCANNED = {"project": None, "objects": {}, "folders": {}}


def _list(value):
    return list(value.values()) if isinstance(value, dict) else list(value or [])


def current_location(controller):
    """Cheap project/bin identity check for the live Dailies source."""
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("Open a project in Resolve first.")
    pool = project.GetMediaPool()
    if pool is None:
        raise ResolveConnectionError("Could not access the Media Pool.")
    folder = pool.GetCurrentFolder() or pool.GetRootFolder()
    if folder is None:
        raise ResolveConnectionError("Could not access the Media Pool's root bin.")
    return str(project.GetUniqueId()), str(folder.GetUniqueId())


def scan(controller):
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("Open a project in Resolve first.")
    pool = project.GetMediaPool()
    if pool is None:
        raise ResolveConnectionError("Could not access the Media Pool.")
    root = pool.GetRootFolder()
    if root is None:
        raise ResolveConnectionError("Could not access the Media Pool's root bin.")
    current = pool.GetCurrentFolder() or root
    current_id = str(current.GetUniqueId())
    clips = {}
    objects = {}
    folders = {}
    current_ids = []
    tree_ids = []       # the current bin's clips, then its sub-bins', in Media Pool order

    def walk(folder, names, inside=False):
        folder_id = str(folder.GetUniqueId())
        inside = inside or folder_id == current_id
        for clip in _list(folder.GetClipList()):
            try:
                props = clip.GetClipProperty() or {}
                if props.get("Type") == "Timeline":
                    continue
                clip_id = str(clip.GetUniqueId())
                if not clip_id or clip_id in clips:
                    continue
                path = str(props.get("File Path") or "")
                clip_meta = clip.GetMetadata() or {}
                kind = media_type(props.get("Type"), path)
                row = {"id": clip_id, "name": str(props.get("Clip Name") or clip.GetName() or "Untitled"),
                       "bin": "/".join(names), "bin_id": folder_id, "path": path, "type": kind,
                       "duration": str(props.get("Duration") or ""),
                       "seconds": model.clip_seconds(kind, props.get("Frames"), props.get("FPS")),
                       "metadata": {key: str(clip_meta.get(key, "") or "")
                                    for key in ("Scene", "Shot", "Take", "Camera #", "Reel Number", "Comments", "Tag")}}
                row["metadata"]["Clip Color"] = str(clip.GetClipColor() or "")
                clips[clip_id], objects[clip_id], folders[clip_id] = row, clip, folder
                if folder_id == current_id:
                    current_ids.append(clip_id)
                if inside:
                    tree_ids.append(clip_id)
            except Exception:
                continue  # one malformed Media Pool item must not hide the rest
        for child in _list(folder.GetSubFolderList()):
            walk(child, [*names, str(child.GetName() or "Bin")], inside)

    walk(root, [str(root.GetName() or "Master")])
    _SCANNED.update(project=str(project.GetUniqueId()), objects=objects, folders=folders)
    return {"project_id": str(project.GetUniqueId()), "project_name": str(project.GetName() or "Project"),
            "current_bin": str(current.GetName() or "Master"), "current_bin_id": current_id,
            "current_ids": current_ids, "current_tree_ids": tree_ids,
            "clips": clips, "objects": objects}


def apply_drafts(controller, expected_project_id, objects, drafts):
    """Apply only changed fields to existing Media Pool items, checking each result."""
    project = controller.current_project()
    if project is None or str(project.GetUniqueId()) != expected_project_id:
        raise ResolveConnectionError("The open Resolve project changed. Refresh Dailies before applying notes.")
    saved, failures, written = [], [], {}
    for clip_id, draft in drafts.items():
        clip = objects.get(clip_id)
        if clip is None:
            failures.append(f"Clip {clip_id} is no longer in the Media Pool.")
            continue
        try:
            changes = model.changes_for(draft, metadata.values(clip))
            if not changes:
                saved.append(clip_id)
                continue
            _, errors = metadata.apply([clip], changes)
            if errors:
                failures.extend(errors)
            else:
                saved.append(clip_id)
                written[clip_id] = changes
        except Exception as exc:
            failures.append(f"{clip.GetName()}: {exc}")
    return saved, failures, written


def scan_rows(controller):
    """scan() as plain data - its Media Pool items stay here for apply_by_id."""
    catalog = scan(controller)
    return {key: value for key, value in catalog.items() if key != "objects"}


# Pages with no Media Pool to show a selection in: Find in Media Pool goes
# to the Edit page from these.
_NO_POOL_PAGES = ("color", "deliver", "photo")


def reveal_by_id(controller, expected_project_id, clip_id):
    """Find in Media Pool: opens the clip's bin in Resolve's Media Pool and
    selects the clip there - from the Edit page when Resolve is on one with
    no Media Pool. Returns the bin's name."""
    project = controller.current_project()
    if project is None or str(project.GetUniqueId()) != expected_project_id:
        raise ResolveConnectionError("The open Resolve project changed. Refresh Dailies first.")
    if _SCANNED["project"] != expected_project_id or clip_id not in _SCANNED["objects"]:
        scan(controller)
    clip, folder = _SCANNED["objects"].get(clip_id), _SCANNED["folders"].get(clip_id)
    if clip is None:
        raise ResolveConnectionError("That clip isn't in the Media Pool any more.")
    resolve = getattr(controller, "resolve", None)
    try:
        if resolve is not None and str(resolve.GetCurrentPage() or "") in _NO_POOL_PAGES:
            resolve.OpenPage("edit")
    except Exception:
        pass                    # the selection is still made; the user switches page
    pool = project.GetMediaPool()
    if folder is not None and not pool.SetCurrentFolder(folder):
        raise ResolveConnectionError("Resolve wouldn't open the clip's bin.")
    if not pool.SetSelectedClip(clip):
        raise ResolveConnectionError("Resolve wouldn't select the clip.")
    return str(folder.GetName() or "Master") if folder is not None else ""


def apply_by_id(controller, expected_project_id, drafts):
    """apply_drafts() to the clips the latest scan here found - or, when this
    process hasn't scanned that project (a fresh reader process), a scan
    first."""
    if _SCANNED["project"] != expected_project_id or any(c not in _SCANNED["objects"] for c in drafts):
        scan(controller)
    return apply_drafts(controller, expected_project_id, _SCANNED["objects"], drafts)
