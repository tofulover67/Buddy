"""Where the nodes of a clip's Fusion comp sit, so Buddy's move reads cleanly
on the Fusion page instead of every node piled on one spot.

Scripting can't place a node here: a clip's comp has no flow view off the
Fusion page (comp.CurrentFrame is None), and AddTool's position doesn't
stick without one (measured 21.1 - Buddy's tools came out with no position
at all, so all at the origin). But every change motion_resolve makes ends
with the comp exported to a .comp file and imported back, and positions are
plain text in it (ViewInfo = OperatorInfo { Pos = { x, y } }), as the SVG
importer writes them. So the exported text is laid out before it goes back.

The layout, in the file's own units (Fusion's native grid is 110 across by
33 down; this is twice that, as the SVG importer spaces its nodes):

- each node's column is how many steps it is upstream of the MediaOut,
  the MediaOut furthest right, so the picture flows left to right;
- the clip's own path - the MediaOut's input, back to where the picture
  starts - is one row, and whatever joins it comes in above or below;
- a mask sits straight above the node it masks (blinds' slats in a stack);
- the key holders Buddy leaves unconnected (their keys play through
  expressions) go in a row underneath;
- the person's own nodes that already have a place stay there, and
  Buddy's line up to the right of where the clip's picture comes in.

Only operators are placed: modifiers (spline curves, paths, the text
Follower) live on their inputs, not in the flow."""

import re

COLUMN = 220.0
ROW = 66.0
BUDDY = ("BuddyMotion", "BuddyHighlight", "BuddyZoom")   # Buddy's own nodes: a preset's, the highlight's, the zooms
ECHO = "BuddyMotionEcho"                # the trails' last Merge (motion_resolve.ECHO_NAME)

_TOOL = re.compile(r"\n\t\t(\w+) = (\w+) \{")
_LINK = re.compile(r"\n\t\t\t\t(\S+) = Input \{\n\t\t\t\t\tSourceOp = \"([^\"]+)\",\n\t\t\t\t\tSource = \"([^\"]+)\"")
_POS = re.compile(r"\n\t\t\tViewInfo = OperatorInfo \{ Pos = \{ ([-\d.e]+), ([-\d.e]+) \} \},")
_ANY_VIEW = re.compile(r"\n\t\t\tViewInfo = ")
_FLOW_OUTPUTS = ("Output", "Mask")      # what an operator hands on; a modifier hands on "Value", "Position"...


def parse(text):
    """The comp's tools: {name: {"type", "at" (header's end), "pos" (or
    None), "fixed" (a position of a form this doesn't rewrite), "links":
    [(input, source tool, source output)]}}, in file order - or None when
    the text isn't laid out as expected."""
    start = text.find("\n\tTools = {")
    if start < 0:
        return None
    end = text.find("\n\t},", start)
    if end < 0:
        return None
    tools = {}
    for m in _TOOL.finditer(text, start, end):
        close = text.find("\n\t\t}", m.end())
        if close < 0 or close > end:
            return None
        block = text[m.end():close]
        pos = _POS.search(block)
        other_view = pos is None and _ANY_VIEW.search(block) is not None
        tools[m.group(1)] = {
            "type": m.group(2), "at": m.end(), "close": close,
            "pos": (float(pos.group(1)), float(pos.group(2))) if pos else None,
            "fixed": other_view,
            "links": [(a, b, c) for a, b, c in _LINK.findall(block)],
        }
    return tools


def layout(tools):
    """{name: (x, y)} for the nodes to place - Buddy's, the MediaOut, and
    any of the person's that have no place yet."""
    # Operators: anything another tool takes a picture or mask from, and
    # anything nothing takes from at all (the unconnected key holders).
    handed = {}
    for tool in tools.values():
        for _inp, src, out in tool["links"]:
            handed.setdefault(src, set()).add(out)
    ops = {n for n in tools if n not in handed or handed[n] & set(_FLOW_OUTPUTS)}
    inputs = {n: [(inp, src) for inp, src, out in tools[n]["links"] if src in ops and out in _FLOW_OUTPUTS]
              for n in ops}
    outs = sorted(n for n in ops if tools[n]["type"] == "Saver")
    if not outs:
        return {}
    media_out = next((n for n in outs if n.startswith("MediaOut")), outs[0])

    # Columns: steps upstream of the MediaOut, the longest way; a mask in
    # the column of the node it masks.
    col = {media_out: 0}
    for _ in range(len(ops) + 1):
        changed = False
        for name in list(col):
            for inp, src in inputs[name]:
                want = col[name] + (0 if inp == "EffectMask" else 1)
                if want > col.get(src, -1):
                    col[src], changed = want, True
        if not changed:
            break
    else:
        return {}                              # a loop: never in a real comp
    users = {}
    for name in col:
        for inp, src in inputs[name]:
            users.setdefault(src, []).append((name, inp))

    # The clip's own path, back from the MediaOut, on row 0 - nothing else is.
    spine, name = [media_out], media_out
    while True:
        main = _main_input(name, tools, inputs[name])
        if main is None or main in spine:
            break
        spine.append(main)
        name = main
    row = {n: 0 for n in spine}
    taken = {(col[n], 0) for n in spine}

    # The rest, nearest what it feeds first: a mask straight above its node,
    # anything else in the free row closest to the rows of what it feeds.
    for _ in range(len(col)):
        progress = False
        for n in sorted(col, key=lambda n: col[n]):
            placed_users = [(u, inp) for u, inp in users.get(n, []) if u in row]
            if n in row or not placed_users:
                continue
            masked = next((u for u, inp in placed_users if inp == "EffectMask"), None)
            if masked is not None:
                r = row[masked] - 1
                while (col[n], r) in taken or r == 0:
                    r -= 1
            else:
                r = _free(col[n], round(sum(row[u] for u, _i in placed_users) / len(placed_users)), taken)
            row[n] = r
            taken.add((col[n], r))
            progress = True
        if not progress:
            break

    # Anchored on the person's own node nearest the MediaOut along the clip's
    # path (where the picture comes into Buddy's move), if one has a place.
    anchor = next((n for n in spine[1:] if _keeps(n, tools) and tools[n]["pos"]), None)
    if anchor is not None:
        ax, ay = tools[anchor]["pos"]
        ox, oy = ax + col[anchor] * COLUMN, ay - row[anchor] * ROW
    else:
        ox, oy = max(col[n] for n in spine) * COLUMN, 0.0
    out = {n: (ox - col[n] * COLUMN, oy + row[n] * ROW) for n in row
           if n == media_out or not _keeps(n, tools)}

    # Buddy's unconnected key holders: rows under the lot, left to right.
    loose = sorted(n for n in ops if n not in row and n.startswith(BUDDY) and not tools[n]["fixed"])
    if loose:
        spots = list(out.values()) + [tools[n]["pos"] for n in row if n not in out and tools[n]["pos"]]
        left = min(x for x, _y in spots)
        below = max(y for _x, y in spots) + 2 * ROW
        per_row = max(4, len(spine))
        for i, n in enumerate(loose):
            out[n] = (left + (i % per_row) * COLUMN, below + (i // per_row) * ROW)
    return out


def _keeps(name, tools):
    """A node left where it is: the person's own, already placed (or placed
    in a way this doesn't rewrite)."""
    tool = tools[name]
    return tool["fixed"] or (tool["pos"] is not None and not name.startswith(BUDDY))


def _main_input(name, tools, inputs):
    """Which way into a node the clip's own picture comes: its Input; for
    a Merge, the side that isn't a plain colour (Buddy's clear canvas, the
    sweep's light) - and when both are pictures, the Background, the one
    underneath, but for the trails' last Merge, whose Foreground is the
    clip (its copies come in behind)."""
    by_input = {inp: src for inp, src in inputs if inp != "EffectMask"}
    if not by_input:
        return None
    if "Input" in by_input:
        return by_input["Input"]
    fg, bg = by_input.get("Foreground"), by_input.get("Background")
    if fg or bg:
        sides = [s for s in (bg, fg) if s and tools[s]["type"] != "Background"]
        if len(sides) == 1:
            return sides[0]
        if len(sides) == 2:
            return fg if name == ECHO else bg
        return bg or fg
    return next(iter(by_input.values()))


def _free(column, near, taken):
    """The free row in a column nearest `near` - above first - never row 0,
    which is the clip's own path."""
    for step in range(0, 64):
        for r in ((near - step, near + step) if step else (near,)):
            if r and (column, r) not in taken:
                return r
    return near + 64


def _num(v):
    return str(int(v)) if float(v).is_integer() else f"{v:.6g}"


def apply(text, positions):
    """The comp text with those nodes at those places."""
    tools = parse(text)
    if not tools or not positions:
        return text
    edits = []
    for name, (x, y) in positions.items():
        tool = tools.get(name)
        if tool is None or tool["fixed"]:
            continue
        line = f"\n\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {_num(x)}, {_num(y)} }} }},"
        block = text[tool["at"]:tool["close"]]
        old = _POS.search(block)
        if old:
            edits.append((tool["at"] + old.start(), tool["at"] + old.end(), line))
        else:
            edits.append((tool["at"], tool["at"], line))       # first in the block: the rest follow its comma
    for a, b, line in sorted(edits, reverse=True):
        text = text[:a] + line + text[b:]
    return text


def tidy(text):
    """The comp text laid out (layout); unchanged when it can't be read."""
    try:
        tools = parse(text)
        return apply(text, layout(tools)) if tools else text
    except Exception:
        return text
