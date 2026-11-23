"""What Ask Buddy can hand a tool along with opening it - more than the
words a search starts with (agent.PREFILL_TOOLS): a bin list for Project
Setup, the offline clips' tab for Media Relink. No Qt.

The model writes the handoff; clean() here decides what of it is kept, so a
tool's page only ever gets the shape it expects (ToolPage.handoff), and the
card can show the user what will be handed over before they press the
button. Nothing a handoff carries is applied by itself: Project Setup shows
the list in its box and waits for Create bins, Media Relink only scans.
"""

from __future__ import annotations

MAX_BINS = 80
MAX_BIN_NAME = 80
MAX_DEPTH = 6

RELINK_MODES = ("fix", "relocate")
RELINK_SCOPES = ("project", "bin", "timeline", "selected")

# tool_id -> what its handoff holds, as offer_tool's description tells the model.
HANDOFF_TOOLS = {
    "project_setup": (
        'handoff {"bins": ["Footage", ">A-cam", ">B-cam", "Audio"]} - a bin structure for its Bins tab, in '
        "order, one bin per entry, with one '>' in front per level it sits inside the bin above"
    ),
    "media_relink": (
        'handoff {"mode": "fix"} to open on the offline clips (or "relocate" for media that moved to another '
        'drive), and optionally "scope": "project", "bin", "timeline" or "selected"'
    ),
}


def _bins(raw) -> list[str]:
    lines = raw.split("\n") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    out = []
    for line in lines:
        if not isinstance(line, str):
            continue
        text = " ".join(line.split())
        depth = len(text) - len(text.lstrip(">"))
        name = text[depth:].strip()[:MAX_BIN_NAME]
        if name:
            out.append(">" * min(depth, MAX_DEPTH) + name)
        if len(out) >= MAX_BINS:
            break
    return out


def clean(tool_id, raw) -> tuple[dict, str]:
    """(the handoff as the tool's page takes it, what the card says it
    starts with). ({}, "") when the tool takes none or nothing usable came."""
    if not isinstance(raw, dict):
        return {}, ""
    if tool_id == "project_setup":
        bins = _bins(raw.get("bins"))
        if not bins:
            return {}, ""
        return {"bins": bins}, "\n".join(bins)
    if tool_id == "media_relink":
        mode = raw.get("mode") if raw.get("mode") in RELINK_MODES else "fix"
        out = {"mode": mode}
        shown = "Offline clips" if mode == "fix" else "Media that moved"
        if raw.get("scope") in RELINK_SCOPES:
            out["scope"] = raw["scope"]
            shown += {"project": " – the whole project", "bin": " – the open bin",
                      "timeline": " – the open timeline", "selected": " – the selected clips"}[raw["scope"]]
        return out, shown
    return {}, ""
