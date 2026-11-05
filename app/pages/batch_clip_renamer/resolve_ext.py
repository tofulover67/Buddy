#!/usr/bin/env python3
"""
Batch Clip Renamer's own Resolve calls - kept separate from
core/resolve_bridge.py, which only owns the shared connect/bootstrap
plumbing every tool needs, not any tool-specific logic.

The preview's reads ("renamer.signature", "renamer.preview") and a big
rename and its Undo ("renamer.rename", "renamer.undo" - core/busy_job.py)
are made in the Resolve reader's process, so plain data in and out, and no
Qt here: that process has none.
"""

from . import renamer


def _is_real_media_clip(clip):
    """A Media Pool bin's own GetClipList()/GetSelectedClips() includes the
    project's own TIMELINES as pseudo-clips, indistinguishable from real
    footage by has_video/has_audio alone (a timeline reports "Audio Ch" > 0
    despite having no real audio). Left unfiltered, a timeline
    sitting in the same bin as real clips both breaks the sequential
    renamer's numbering (it counts a slot for something that isn't really
    "clip N" of the batch) and gets its own name silently overwritten by
    Find & Replace. GetClipProperty("Type") is the one property that
    actually tells them apart."""
    try:
        return clip.GetClipProperty("Type") != "Timeline"
    except Exception:
        return True  # can't tell - err on the side of including it


def get_current_clips(controller):
    """Clips in the Media Pool's currently open bin/folder (timelines
    filtered out), or None if no project is open."""
    project = controller.current_project()
    if not project:
        return None

    media_pool = project.GetMediaPool()
    current_folder = media_pool.GetCurrentFolder()
    clips = current_folder.GetClipList()
    if not clips:
        return clips
    return [c for c in clips if _is_real_media_clip(c)]


def get_selected_clips(controller):
    """Clips currently selected in the Media Pool (timelines filtered
    out), or None if no project is open. Some older Resolve versions
    don't support GetSelectedClips() at all - callers should be ready to
    catch an exception from this."""
    project = controller.current_project()
    if not project:
        return None
    clips = project.GetMediaPool().GetSelectedClips()
    if not clips:
        return clips
    return [c for c in clips if _is_real_media_clip(c)]


SCOPE_BIN = "bin"
SCOPE_SELECTED = "selected"


class TargetError(Exception):
    """Why there is nothing to rename, worded for the user."""


def clip_name(clip):
    """What the Media Pool shows: the Clip Name property, falling back to
    GetName() for a clip that has never had one set."""
    return clip.GetClipProperty("Clip Name") or clip.GetName() or ""


def clip_key(clip):
    """Stable identity for a clip across API calls - the wrapper objects
    Resolve hands back are new each call, so they can't be compared."""
    try:
        return clip.GetUniqueId()
    except Exception:
        return clip_name(clip)


def read_targets(controller, scope):
    """(clips, where) for the chosen scope - `where` names the bin, or says
    "Selected clips". Raises TargetError when there is nothing to work on."""
    project = controller.current_project()
    if not project:
        raise TargetError("No project is open in Resolve.")
    if scope == SCOPE_SELECTED:
        try:
            clips = get_selected_clips(controller)
        except Exception as exc:
            raise TargetError(
                f"Could not read the Media Pool selection ({exc}). Your Resolve version may "
                "not support this – use the current bin instead."
            ) from exc
        if not clips:
            raise TargetError("No clips are selected in the Media Pool.")
        return clips, "Selected clips"
    folder = project.GetMediaPool().GetCurrentFolder()
    where = (folder.GetName() if folder else "") or "Current bin"
    clips = get_current_clips(controller)
    if not clips:
        raise TargetError(f"The bin \"{where}\" has no clips in it.")
    return clips, where


def target_signature(controller, scope):
    """A cheap fingerprint of what the scope points at, for the page to
    poll while it's on screen: the open bin and how many items it holds,
    or which clips are selected. No per-clip property reads for a bin -
    polling must stay far cheaper than read_targets(). None when there's
    no project."""
    project = controller.current_project()
    if not project:
        return None
    pool = project.GetMediaPool()
    if scope == SCOPE_SELECTED:
        try:
            return tuple(clip_key(c) for c in (pool.GetSelectedClips() or []))
        except Exception:
            return None
    folder = pool.GetCurrentFolder()
    if not folder:
        return None
    return (folder.GetUniqueId(), len(folder.GetClipList() or []))


def plain_signature(signature):
    """target_signature() as it comes back from the reader's process (JSON:
    a list), whichever process read it - so the two compare."""
    return list(signature) if isinstance(signature, (list, tuple)) else signature


def preview(controller, scope):
    """What the page's preview needs, as plain data - read in the Resolve
    reader's process (core/resolve_reader.py), so it never waits on Resolve
    inside Buddy: {"names", "keys", "where", "error", "signature"}. Nothing to
    rename is an "error" here (TargetError's words), not raised: the
    signature still comes, or every poll would read it all again. The rename
    itself reads the clips afresh (rename())."""
    signature = plain_signature(target_signature(controller, scope))
    try:
        clips, where = read_targets(controller, scope)
    except TargetError as exc:
        return {"names": [], "keys": [], "where": "", "error": str(exc), "signature": signature}
    return {"names": [clip_name(c) for c in clips], "keys": [clip_key(c) for c in clips],
            "where": where, "error": "", "signature": signature}


# How often a rename made in Buddy says how far it's got.
PROGRESS_EVERY = 25

# The clips the latest rename here renamed, by key - for its Undo, in the
# same process: Resolve's objects can't go to the page and back.
_RENAMED = {}


def rename(controller, scope, keys, names, inputs, progress=None):
    """The rename, as plain data. The targets are read afresh first, and
    nothing is written unless they are exactly the clips and names the
    preview showed (`keys`, `names`) - else {"changed": True, "names",
    "keys", "where"}, for the page to show. Otherwise every clip whose name
    the rules (`inputs`, renamer.plan) change is renamed: {"changed":
    False, "where", "todo", "done", "failed"}, done and failed [key, old,
    new]. Raises TargetError when there's nothing to rename. progress(n,
    total) every PROGRESS_EVERY clips - in Buddy only (it's no JSON)."""
    clips, where = read_targets(controller, scope)
    names_now, keys_now = [clip_name(c) for c in clips], [clip_key(c) for c in clips]
    if keys_now != list(keys) or names_now != list(names):
        return {"changed": True, "names": names_now, "keys": keys_now, "where": where}
    rows, problem = renamer.plan(names_now, inputs)
    todo = [] if problem else [(clip, key, row) for clip, key, row in zip(clips, keys_now, rows) if row["changed"]]
    done, failed, renamed = [], [], {}
    for n, (clip, key, row) in enumerate(todo, 1):
        try:
            ok = bool(clip.SetClipProperty("Clip Name", row["new"]))
        except Exception:
            ok = False
        (done if ok else failed).append([key, row["old"], row["new"]])
        if ok:
            renamed[key] = clip
        if progress is not None and n % PROGRESS_EVERY == 0:
            progress(n, len(todo))
    if done:                        # the page's Undo is for this rename now
        _RENAMED.clear()
        _RENAMED.update(renamed)
    return {"changed": False, "where": where, "todo": len(todo), "done": done, "failed": failed}


def find_clips(controller, keys):
    """{key: clip} for the Media Pool clips with these keys (clip_key) -
    the open bin looked in first, then every bin."""
    wanted, found = set(keys), {}
    project = controller.current_project() if wanted else None
    if not project:
        return found

    def look(folder):
        for clip in folder.GetClipList() or []:
            key = clip_key(clip)
            if key in wanted and key not in found:
                found[key] = clip

    pool = project.GetMediaPool()
    current = pool.GetCurrentFolder()
    if current:
        look(current)
    root = pool.GetRootFolder()
    stack = [root] if root else []
    while stack and len(found) < len(wanted):
        folder = stack.pop()
        look(folder)
        stack.extend(folder.GetSubFolderList() or [])
    return found


def undo(controller, items):
    """Puts back the names a rename replaced (items: its done, [key, old,
    new]) - but only on clips still carrying the name it gave them: one
    renamed again since is left alone. The clips are the rename's own when
    it was made in this process, else found again in the Media Pool by key.
    {"restored", "skipped"}."""
    clips = {key: _RENAMED[key] for key, _old, _new in items if key in _RENAMED}
    missing = [key for key, _old, _new in items if key not in clips]
    if missing:
        clips.update(find_clips(controller, missing))
    restored = skipped = 0
    for key, old, new in items:
        clip = clips.get(key)
        try:
            if clip is not None and clip_name(clip) == new and clip.SetClipProperty("Clip Name", old):
                restored += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    return {"restored": restored, "skipped": skipped}
