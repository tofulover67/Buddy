"""Where every box of a panel's page sits: the layout solver. It reads a clean panel
(model.clean) and says, in panel px from the window's top left corner, the box of
every box - the one answer the view, the display list (styles.py), the clock and the
comp all draw from. No Qt, no Resolve.

The rules are a small flex: a section puts its children in a row or a column, "gap"
apart, inside its "pad". Along a row a child is as wide as it asks - a number, "hug"
(as wide as what's in it) or "fill" (a share of what the others leave, from "minw"
up to "maxw") - and children that ask for more than there is are all narrowed alike,
so nothing leaves the window. "wrap" sends the ones that don't fit to a next line;
"equal" makes equal columns, each at least that wide, as many as fit. "aspect" makes
a box as tall as its width says, "hide_below" takes one out, with its space, in a
window narrower than that, and "center" puts a box narrower than its room in the
middle of it. Across the line "align" places a child (or stretches a section to the
line's height); along it "justify" places what's left over. Only rows wrap.

Text is measured by rule, never by a font, so it comes out the same everywhere: a
character is CHAR_W[variant] * SIZE[variant] wide (BOLD_W for strong text and for a
button's or a badge's), a line LINE times the size tall, and the words are broken
greedily to the box's width. Those "lines" are exactly what is drawn.
"""

FRAME = (1920, 1080)        # the frame a cursor's place is worked out for until a timeline is read
PHONE_ASPECT = 2.05         # a phone's height, of its width - at least: a taller page makes it taller
# The window's own parts, panel px: the bar over the pages, the strip under them, and
# how far the pages stand in from its sides (a phone's body shows round its screen).
BAR = {"app": 32.0, "browser": 66.0, "phone": 38.0, "dialog": 36.0, "widget": 0.0}
FOOT = {"app": 0.0, "browser": 0.0, "phone": 30.0, "dialog": 0.0, "widget": 0.0}
INSET = {"app": 0.0, "browser": 0.0, "phone": 8.0, "dialog": 0.0, "widget": 0.0}

SIZE = {"body": 13.0, "h1": 22.0, "h2": 15.0, "caption": 11.0, "label": 10.5, "num": 22.0, "up": 11.0, "down": 11.0}
CHAR_W = {"body": 0.5, "h1": 0.55, "h2": 0.55, "caption": 0.5, "label": 0.52, "num": 0.56, "up": 0.52, "down": 0.52}
BOLD = ("h1", "h2", "num")  # the variants set in the bold face
BOLD_W = 0.55
LINE = 1.35                 # a line's height, of the letter size
CAP = 0.7                   # a capital's height, of the letter size - about (Data Graphics' text_look.CAP)
# The leaves that aren't text: their letter size, height, the room each side of their words.
BUTTON = {"size": 13.0, "h": 30.0, "pad": 14.0}
FIELD = {"size": 13.0, "h": 30.0, "pad": 10.0, "w": 140.0}
BADGE = {"size": 11.0, "h": 20.0, "pad": 8.0}
ICON = {"": 28.0, "avatar": 28.0, "sm": 14.0}
DIVIDER = 1.0
PROGRESS = 6.0
ROOMY = {"image": 120.0, "chart": 120.0, "progress": 80.0, "divider": 8.0}     # how wide a "fill" leaf is when hugged
CUT = "…"

# What a placed box carries of its box, besides where it is.
LOOK = ("variant", "name", "text", "right", "strong", "on", "center", "p", "chart", "hue", "reacts", "image", "type", "arrive")


def letter(box):
    """(letter size, a character's width) of a box's words."""
    role = box["role"]
    if role == "text":
        size = SIZE.get(box["variant"], SIZE["body"])
        return size, size * (BOLD_W if box["strong"] and box["variant"] not in BOLD else CHAR_W.get(box["variant"], 0.5))
    size = {"button": BUTTON, "field": FIELD, "badge": BADGE}.get(role, BUTTON)["size"]
    return size, size * (CHAR_W["body"] if role == "field" else BOLD_W)


def broken(words, char_w, width):
    """The words broken greedily into lines no wider than `width` - a word too long
    for a line by itself is broken where it must be."""
    most = max(1, int((width + 1e-6) // char_w)) if char_w > 0 else 1
    lines, line = [], ""
    for word in str(words).split():
        while len(word) > most:
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:most])
            word = word[most:]
        if not word:
            continue
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= most:
            line += " " + word
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _one_line(words, char_w, width):
    """A button's, a field's or a badge's words on their one line, cut short if they must be."""
    most = max(0, int((width + 1e-6) // char_w))
    return words if len(words) <= most else (words[:max(0, most - 1)].rstrip() + CUT if most > 1 else "")


def typing(placed):
    """[(line, first, last, x, baseline)] of a placed box that types: each line of its
    "type_lines", which of the box's letters it holds ([first, last), counted through the
    lines in order, spaces included, the break between lines not), where it starts and
    its baseline - panel px. A line that types is always laid out from its start (x), so
    its letters appear in place as they type: a centred or right-aligned text's start is
    where the whole line would start by the letter-width rule. [] for a box that doesn't."""
    lines = placed.get("type_lines") or []
    if not lines:
        return []
    size, char_w = letter(placed)
    out, first = [], 0
    if placed["role"] == "field":
        line = lines[0]
        return [(line, 0, len(line), placed["x"] + FIELD["pad"], placed["y"] + (placed["h"] + CAP * size) / 2)]
    step = size * LINE
    for i, line in enumerate(lines):
        wide = len(line) * char_w
        x = placed["x"] + (placed["w"] - wide if placed["right"] else (placed["w"] - wide) / 2 if placed["center"] else 0.0)
        out.append((line, first, first + len(line), x, placed["y"] + i * step + (step + CAP * size) / 2))
        first += len(line)
    return out


# ----------------------------------------------------------------- widths --

def _shown(box, ctx):
    return [c for c in box["children"] if not (c["hide_below"] > 0 and ctx["width"] < c["hide_below"])]


def _bounded(box, w):
    if box["maxw"] > 0:
        w = min(w, box["maxw"])
    return max(w, box["minw"])


def _natural(box, ctx):
    """How wide a box is by itself: what "hug" means."""
    known = ctx["natural"].get(box["id"])
    if known is not None:
        return known
    role = box["role"]
    if isinstance(box["w"], (int, float)):
        w = float(box["w"])
    elif role == "section":
        kids = [_natural(c, ctx) for c in _shown(box, ctx)]
        along = box["dir"] == "row"
        inside = (sum(kids) + box["gap"] * max(0, len(kids) - 1)) if along else max(kids, default=0.0)
        w = _bounded(box, inside + box["pad"][1] + box["pad"][3])
    else:
        size, char_w = letter(box)
        if role == "text":
            w = len(box["text"]) * char_w
        elif role == "button":
            w = len(box["text"]) * char_w + 2 * BUTTON["pad"]
        elif role == "badge":
            w = len(box["text"]) * char_w + 2 * BADGE["pad"]
        elif role == "field":
            w = FIELD["w"]
        elif role == "icon":
            w = ICON.get(box["variant"], ICON[""])
        else:
            w = ROOMY.get(role, 0.0)
        w = _bounded(box, w)
    ctx["natural"][box["id"]] = w
    return w


def _asks(box, ctx):
    """What a child asks of a row before the leftover is shared out."""
    if box["w"] == "fill":
        return box["minw"]
    return _natural(box, ctx)


def _shared(kids, room, gap, ctx):
    """Each child's width along a row `room` wide."""
    asks = [_asks(k, ctx) for k in kids]
    room = max(0.0, room - gap * max(0, len(kids) - 1))
    total = sum(asks)
    if total > room:
        return [a * room / total for a in asks]         # all narrowed alike
    widths, left = list(asks), room - total
    fills = [i for i, k in enumerate(kids) if k["w"] == "fill"]
    while fills and left > 1e-9:
        share, left, still = left / len(fills), 0.0, []
        for i in fills:
            most = kids[i]["maxw"] if kids[i]["maxw"] > 0 else float("inf")
            give = max(0.0, min(share, most - widths[i]))
            widths[i] += give
            left += share - give
            if give == share:
                still.append(i)
        if len(still) == len(fills):
            break
        fills = still
    return widths


def _wrapped(kids, room, gap, ctx):
    """The children in lines: each goes on the line while it fits."""
    lines, line, used = [], [], 0.0
    for kid in kids:
        # A "fill" child with no least width still needs what's in it to fit.
        ask = _asks(kid, ctx) if (kid["w"] != "fill" or kid["minw"] > 0) else _natural(kid, ctx)
        ask = min(ask, room)
        if line and used + gap + ask > room + 1e-6:
            lines.append(line)
            line, used = [], 0.0
        used += (gap if line else 0.0) + ask
        line.append(kid)
    if line:
        lines.append(line)
    return lines


# ---------------------------------------------------------------- the fit --

def _fit(box, w, ctx):
    """A box at the width it was given: its height, its lines of text, and its children
    fitted in their lines. A node: {"box", "w", "h", "flow": [{"kids": [node], "h"}], "lines"}."""
    role = box["role"]
    node = {"box": box, "w": w, "h": 0.0, "flow": [], "lines": []}
    if role == "section":
        top, right, bottom, left = box["pad"]
        inner, gap = max(0.0, w - left - right), box["gap"]
        kids = _shown(box, ctx)
        if box["dir"] == "row":
            if box["equal"] > 0:
                n = max(1, int((inner + gap + 1e-6) // (box["equal"] + gap)))
                cell = max(0.0, (inner - gap * (n - 1)) / n)
                groups = [(kids[i:i + n], [cell] * len(kids[i:i + n])) for i in range(0, len(kids), n)]
            else:
                lines = _wrapped(kids, inner, gap, ctx) if box["wrap"] else [kids] if kids else []
                groups = [(line, _shared(line, inner, gap, ctx)) for line in lines]
            for line, widths in groups:
                nodes = [_fit(k, kw, ctx) for k, kw in zip(line, widths)]
                node["flow"].append({"kids": nodes, "h": max(n["h"] for n in nodes)})
            inside = sum(line["h"] for line in node["flow"]) + gap * max(0, len(node["flow"]) - 1)
        else:
            nodes = []
            for kid in kids:
                kw = inner if kid["w"] == "fill" else _natural(kid, ctx)
                nodes.append(_fit(kid, min(inner, _bounded(kid, kw)), ctx))
            inside = sum(n["h"] for n in nodes) + gap * max(0, len(nodes) - 1)
            node["flow"] = [{"kids": nodes, "h": inside}] if nodes else []
        h = inside + top + bottom
    elif role == "text":
        size, char_w = letter(box)
        node["lines"] = broken(box["text"], char_w, w)
        h = max(1, len(node["lines"])) * size * LINE
    elif role in ("button", "field", "badge"):
        part = {"button": BUTTON, "field": FIELD, "badge": BADGE}[role]
        node["lines"] = [_one_line(box["text"], letter(box)[1], w - 2 * part["pad"])]
        if role == "field" and box.get("type"):
            # What a field types is its own one line, cut as its placeholder is.
            node["typing"] = [_one_line(box["type"]["text"], letter(box)[1], w - 2 * part["pad"])]
        h = part["h"]
    elif role == "icon":
        h = w
    elif role == "divider":
        h = DIVIDER
    elif role == "progress":
        h = PROGRESS
    else:                                       # an image, a chart
        h = w / (box["aspect"] or 16 / 9)
    if isinstance(box["h"], (int, float)):
        h = float(box["h"])
    elif box["aspect"] > 0:
        h = w / box["aspect"]
    node["h"] = max(h, box["minh"])
    return node


def _grows(node):
    """Whether a row's "stretch" (or a column's leftover) may make this box taller."""
    box = node["box"]
    return not isinstance(box["h"], (int, float)) and not box["aspect"] > 0 and (box["role"] == "section" or box["h"] == "fill")


def _place(node, x, y, depth, parent, out):
    box = node["box"]
    size = letter(box)[0] if box["role"] in ("text", "button", "field", "badge") else 0.0
    out.append({"id": box["id"], "role": box["role"], "x": round(x, 3), "y": round(y, 3), "w": round(node["w"], 3),
                "h": round(node["h"], 3), "depth": depth, "parent": parent, "lines": node["lines"], "size": size,
                **{key: box.get(key) for key in LOOK},
                # The lines whose letters type on (a text's own, a field's typed words), [] for none.
                "type_lines": (node.get("typing") or node["lines"]) if box.get("type") else []})
    if not node["flow"]:
        return
    top, right, bottom, left = box["pad"]
    inner_w, inner_h = max(0.0, node["w"] - left - right), max(0.0, node["h"] - top - bottom)
    gap, align, justify = box["gap"], box["align"], box["justify"]
    spare = {"start": 0.0, "center": 0.5, "end": 1.0}
    if box["dir"] == "row":
        if len(node["flow"]) == 1:
            node["flow"][0]["h"] = max(node["flow"][0]["h"], inner_h)       # a stretched row's one line is as tall as it
        cy = y + top
        for line in node["flow"]:
            used = sum(k["w"] for k in line["kids"]) + gap * (len(line["kids"]) - 1)
            cx = x + left + (0.0 if box["equal"] > 0 else max(0.0, inner_w - used) * spare[justify])
            for kid in line["kids"]:
                if align == "stretch" and _grows(kid):
                    kid["h"] = max(kid["h"], line["h"])
                room = max(0.0, line["h"] - kid["h"])
                _place(kid, cx, cy + room * spare.get(align, 0.0), depth + 1, box["id"], out)
                cx += kid["w"] + gap
            cy += line["h"] + gap
        return
    kids = node["flow"][0]["kids"]
    over = inner_h - sum(k["h"] for k in kids) - gap * (len(kids) - 1)
    growing = [k for k in kids if k["box"]["h"] == "fill" and _grows(k)]
    cy = y + top
    if over > 0 and growing:
        for kid in growing:
            kid["h"] += over / len(growing)
    elif over > 0:
        cy += over * spare[justify]
    for kid in kids:
        room = max(0.0, inner_w - kid["w"])
        side = 0.5 if kid["box"]["center"] else spare.get(align, 0.0)
        _place(kid, x + left + room * side, cy, depth + 1, box["id"], out)
        cy += kid["h"] + gap


# -------------------------------------------------------------- the window --

def solve_all(spec):
    """{page id: solve()} for every page of a panel - one window, as tall as its tallest
    page needs."""
    kind, width = spec["kind"], spec["width"]
    bar, foot, inset = BAR[kind], FOOT[kind], INSET[kind]
    room = width - 2 * inset
    fitted = {}
    for page in spec["pages"]:
        ctx = {"width": width, "natural": {}}
        root = page["root"]
        w = room if not isinstance(root["w"], (int, float)) else min(room, float(root["w"]))
        fitted[page["id"]] = _fit(root, min(room, _bounded(root, w)), ctx)
    tall = max(node["h"] for node in fitted.values())
    if kind == "phone":
        tall = max(tall, width * PHONE_ASPECT - bar - foot)
    out = {}
    for page in spec["pages"]:
        node, boxes = fitted[page["id"]], []
        if _grows(node):
            node["h"] = tall                    # a page's own box is the window's whole room, so a sidebar runs down it
        side = 0.5 if page["root"]["center"] else 0.0
        _place(node, inset + (room - node["w"]) * side, bar, 0, "", boxes)
        out[page["id"]] = {"w": width, "h": round(bar + tall + foot, 3), "bar": bar,
                           "area": [inset, bar, round(room, 3), round(tall, 3)], "boxes": boxes}
    return out


def solve(spec, page_id):
    """{"w", "h", "bar", "area", "boxes"} of one page: the window's size, its bar's
    height, the room its pages have ([x, y, w, h]) and every box of the page that shows,
    back to front - a parent before its children. The start page for an id there isn't."""
    solved = solve_all(spec)
    return solved.get(page_id) or solved[spec["start"]]


# ---------------------------------------------------------------- the units --

def scale(spec, frame=FRAME):
    """Frame px to a panel px: its zoom, on a frame this wide."""
    return spec["zoom"] * frame[0] / 1920.0


def to_panel(spec, height, x, y, frame=FRAME):
    """A place on the frame (% across and down) in panel px, for a window `height` tall."""
    by = scale(spec, frame)
    return ((x - spec["x"]) / 100.0 * frame[0] / by + spec["width"] / 2.0,
            (y - spec["y"]) / 100.0 * frame[1] / by + height / 2.0)


def to_frame(spec, height, x, y, frame=FRAME):
    """A place in panel px as % of the frame: to_panel() the other way."""
    by = scale(spec, frame)
    return (spec["x"] + (x - spec["width"] / 2.0) * by / frame[0] * 100.0,
            spec["y"] + (y - height / 2.0) * by / frame[1] * 100.0)
