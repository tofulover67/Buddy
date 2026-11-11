#!/usr/bin/env python3
"""
Media Relink's list of clips, without Qt: what a scan found, what a folder
search proposes for each clip, and which clips a relink may touch.

Two modes, each with its own list:
    fix       only offline clips are ever reconsidered - clips that work
              are left alone even if a same-named file turns up
    relocate  every clip is fair game (moving media to a new drive), but
              only the clips the user selected are relinked
"""

import os
import re

from .relink_engine import MatchStatus, classify_match, find_candidates, media_exists, same_path

MODE_FIX = "fix"
MODE_RELOCATE = "relocate"
MODES = (MODE_FIX, MODE_RELOCATE)

STATUS_LABELS = {
    MatchStatus.ONLINE: "Online",
    MatchStatus.MATCH_FOUND: "Match found",
    MatchStatus.AMBIGUOUS: "Pick one",
    MatchStatus.NOT_FOUND: "Offline",
}


def make_rows(entries, get_path, get_name, exists=media_exists, get_id=lambda clip: None):
    """Rows for scanned (clip, bin_path) entries. Returns (rows, skipped):
    clips with no single file path (generated media) are skipped - there's
    nothing to check or relink. The same clip twice (a scan that reaches
    it two ways) is one row."""
    rows, skipped, seen = [], 0, set()
    for clip, bin_path in entries:
        uid = get_id(clip)
        if uid is not None:
            if uid in seen:
                continue
            seen.add(uid)
        path = get_path(clip)
        if not path:
            skipped += 1
            continue
        rows.append({
            "id": len(rows),
            "uid": uid,
            "clip": clip,
            "name": get_name(clip),
            "bin": bin_path,
            "old_path": path,
            "status": classify_match(exists(path), []),
            "candidates": [],
            "resolved": None,
        })
    return rows, skipped


def apply_search(rows, index, mode):
    """Fills in proposed matches from a folder index. Returns
    (matched, ambiguous). A clip that already has a single match (found
    earlier, or picked by hand) keeps it when this search is ambiguous."""
    matched = ambiguous = 0
    for row in rows:
        if mode == MODE_FIX and row["status"] == MatchStatus.ONLINE:
            continue
        # The clip's own file isn't somewhere new to go (a relocate search
        # of the folder it already lives in).
        candidates = [c for c in find_candidates(row["old_path"], index)
                      if not same_path(c, row["old_path"])]
        if not candidates:
            continue
        if row["status"] == MatchStatus.MATCH_FOUND and len(candidates) > 1:
            continue
        row["candidates"] = candidates
        row["status"] = classify_match(False, candidates)
        row["resolved"] = candidates[0] if len(candidates) == 1 else None
        if row["status"] == MatchStatus.MATCH_FOUND:
            matched += 1
        else:
            ambiguous += 1
    return matched, ambiguous


def pick(row, path):
    """The user chose `path` for this clip (from its candidates or by
    browsing)."""
    row["resolved"] = path
    row["status"] = MatchStatus.MATCH_FOUND
    if path not in row["candidates"]:
        row["candidates"] = [path]


# ------------------------------------------------- picking many at once --
#
# A clip that's "Pick one" has the same file in several places - usually
# because the media was copied: D:\Footage\Day1\A001\X.MXF and
# E:\Backup\Footage\Day1\A001\X.MXF. What tells the copies apart is where
# each copy is: the part of its path before the folders they share. Here
# that's "D:\" and "E:\Backup". Every clip from those copies has the same
# two places, so one choice can do for all of them.

def _parts(path):
    return [p.casefold() for p in re.split(r"[\\/]+", path.rstrip("\\/"))]


def _key(location):
    return os.path.normcase(os.path.normpath(location))


def locations(candidates):
    """Where each candidate is, as the part of its path before the folders
    and name all the candidates share. Each candidate keeps at least its
    first folder (or drive), so no place is empty."""
    if len(candidates) < 2:
        return list(candidates)
    parts = [_parts(c) for c in candidates]
    shared, shortest = 0, min(len(p) for p in parts)
    while shared < shortest - 1 and len({p[-1 - shared] for p in parts}) == 1:
        shared += 1
    out = []
    for path in candidates:
        for _ in range(shared):
            path = os.path.dirname(path)
        out.append(path)
    return out


def _closest(old_path, candidates):
    """The candidate whose path ends the most like the clip's recorded
    path, when one does more than every other - otherwise None. A camera
    that reuses its numbering puts A001.MXF in Day1 and Day2; a clip
    recorded at ...\\Day2\\A001.MXF is the Day2 one."""
    old = _parts(old_path)

    def shared(path):
        parts, n = _parts(path), 0
        while n < min(len(parts), len(old)) and parts[-1 - n] == old[-1 - n]:
            n += 1
        return n
    scores = [shared(c) for c in candidates]
    best = max(scores, default=0)
    return scores.index(best) if scores.count(best) == 1 else None


def pick_groups(rows):
    """The clips still to pick, grouped by the places their files are in:
    one choice per group. Clips with the same places but a different
    closest one (_closest) are separate groups, so one choice is never
    wrong for some of them. Biggest groups first. JSON-able."""
    groups = {}
    for row in rows:
        if row["status"] != MatchStatus.AMBIGUOUS:
            continue
        places = locations(row["candidates"])
        closest = _closest(row["old_path"], row["candidates"])
        order = sorted(range(len(places)), key=lambda i: _key(places[i]))
        key = (tuple(_key(places[i]) for i in order), None if closest is None else _key(places[closest]))
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "locations": [places[i] for i in order],
                "keys": list(key[0]),
                "examples": [row["candidates"][i] for i in order],
                "closest": key[1],
                "ids": [],
            }
        group["ids"].append(row["id"])
    return sorted(groups.values(), key=lambda g: (-len(g["ids"]), g["ids"][0]))


def pick_location(rows, location, ids=None):
    """Picks, for clips still to pick, their file at `location` (one of
    locations()). With ids, just those clips - the user's choice for that
    group stands. Without, every clip with a file there, except one whose
    recorded path points at another of its places (_closest). Returns how
    many were picked."""
    wanted = None if ids is None else set(ids)
    key, picked = _key(location), 0
    for row in rows:
        if row["status"] != MatchStatus.AMBIGUOUS or (wanted is not None and row["id"] not in wanted):
            continue
        places = [_key(p) for p in locations(row["candidates"])]
        if places.count(key) != 1:
            continue
        at = places.index(key)
        if wanted is None:
            closest = _closest(row["old_path"], row["candidates"])
            if closest is not None and closest != at:
                continue
        pick(row, row["candidates"][at])
        picked += 1
    return picked


def matched_rows(rows):
    return [r for r in rows if r["status"] == MatchStatus.MATCH_FOUND and r["resolved"]]


def relink(rows, replace):
    """Relinks each row that has a file to go to. replace(clip, path) ->
    bool. Returns (relinked, skipped, failed). A relinked row remembers
    how it was, for confirm()."""
    relinked = skipped = failed = 0
    for row in rows:
        if row["status"] == MatchStatus.ONLINE:
            continue
        if not row["resolved"]:
            skipped += 1
            continue
        if replace(row["clip"], row["resolved"]):
            row["before"] = {k: row[k] for k in ("old_path", "status", "candidates", "resolved")}
            row.update(old_path=row["resolved"], status=MatchStatus.ONLINE, candidates=[], resolved=None)
            relinked += 1
        else:
            failed += 1
    return relinked, skipped, failed


def confirm(rows, current_paths):
    """Resolve can answer yes to a relink and keep the old file. With the
    paths read afresh afterwards ({uid: path}), each relinked row whose
    clip doesn't point at its new file goes back to how it was. Returns
    how many didn't take; a clip that can't be found again is taken on
    Resolve's word."""
    unchanged = 0
    for row in rows:
        before = row.pop("before", None)
        now = current_paths.get(row["uid"]) if before else None
        if now is None or same_path(now, row["old_path"]):
            continue
        row.update(before)
        unchanged += 1
    return unchanged


def counts(rows):
    out = {"total": len(rows), "online": 0, "offline": 0, "matched": 0, "ambiguous": 0}
    for row in rows:
        out[{MatchStatus.ONLINE: "online", MatchStatus.NOT_FOUND: "offline",
             MatchStatus.MATCH_FOUND: "matched", MatchStatus.AMBIGUOUS: "ambiguous"}[row["status"]]] += 1
    return out


def view(row):
    """What the table shows for one row (JSON-able). With several
    candidates: where each one is (locations(), and the same normalised
    for comparing) and which is closest to the recorded path."""
    many = len(row["candidates"]) > 1
    places = locations(row["candidates"]) if many else []
    closest = _closest(row["old_path"], row["candidates"]) if many else None
    return {
        "locations": places,
        "location_keys": [_key(p) for p in places],
        "closest": None if closest is None else row["candidates"][closest],
        "id": row["id"],
        "name": row["name"],
        "bin": row["bin"],
        "status": row["status"],
        "label": STATUS_LABELS[row["status"]] + (f" ({len(row['candidates'])})"
                                                 if row["status"] == MatchStatus.AMBIGUOUS else ""),
        "old_path": row["old_path"],
        "new_path": row["resolved"],
        "candidates": row["candidates"],
    }
