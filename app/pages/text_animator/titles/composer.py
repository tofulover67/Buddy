"""The composer - a title's layout from its text: which words matter (roles), where the
lines break, how big each line is and where the block sits. Pure: text, a look, the frame
size and a measure function in, numbers out. No Qt, no Resolve, no drawing - the preview,
the contact sheet and the Text+ clips all draw what this decides, so it's composed once.

    compose(doc, frame, measure, metrics=None, safe=None, prev=None) -> layout

measure(text, font, size) is the advance width of text in font ((family, style)) at a
letter size of size px; metrics(font) is {"asc", "desc", "cap"} as shares of the letter
size. Tests use fakes; Buddy uses canvas_math's, calibrated against Text+.

How it chooses: a hook is a handful of words, so every set of line breaks is tried (1 to
maxWords words a line, the look's line range +-1) and each is scored; lower is better.
A line's size is closed-form - text width grows with the letter size, so the size that
fills the block width W is W / (width at 100 / 100). A short line grows, so a key word
alone on its line comes out big with no extra rule. The roles' scale is left out of
that fit and applied after: fitted with it, a bigger key word alone on its line would
be shrunk back to the same width as every other line.

The document is the saved title - see new_doc(). What the composer decides (roles,
breaks, sizes) is never stored in it; what the person changed is, in sparse maps keyed
by word ids that survive editing the text ("secrets#1": the first "secrets").
"""

import math
import re
import statistics

from . import kinetic
from . import looks as L

# Connectors - never the key, and a line shouldn't end on one: English, German, Spanish,
# French, Portuguese and Italian, less the ones that are also English content words
# ("die", "con", "pour"...), since a hook's language isn't always known.
STOPWORDS = frozenset("""
a an the of to in on at for by with from and or but nor so as is are was were be been being
am it its it's this that these those i i'm you you're your yours we our us he she his her they
them their my me do does did if then than into onto over under up out about just will can
der das den dem des ein eine einen einem einer und oder aber zu im am auf mit von für ist sind
war du ich er sie es wir ihr nicht wie was wenn dass beim vom zum zur
el la los las un una unos unas y o pero del al en por para que es son te se lo le mi tu su nos
le les une et ou mais du à au aux dans sur avec ce cet cette est ne vous nous je il elle on qui
um uma os da dos das em no na é ao
il gli di della che è non
""".split()) - frozenset("die con par per pas pour son man sale".split())
# Words a hook leans on.
INTENSIFIERS = frozenset("""
most best worst never always only free secret nobody everyone everything stop why how biggest
ultimate instantly guaranteed proven insane crazy huge massive real truth mistake hack fast
faster easy simple new now must exactly every first last zero
""".split())
_NUMBER = re.compile(r"^[$€£¥]?\d[\d,.:]*(%|k|m|bn|x|st|nd|rd|th)?$")

ACCENT_MIN = 1.5            # the least score an auto accent needs
TWO_KEYS_FROM = 9           # words in a hook that gets two keys
MAX_CANDIDATES = 6000
DEFAULT_METRICS = {"asc": 0.93, "desc": 0.24, "cap": 0.70}
# Capitals sit on the baseline and reach the cap height - except $ , ; Q J and the like,
# which dip below it, and accented capitals (İ Ö É), which rise above it: they'd touch
# the next line. ink(text, font) says by how much; without it, these characters do:
_BELOW_BASELINE = set("$,;QJ()[]{}|@/_")
_ABOVE_CAP = set("ÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÝŸÑÇİŞĞŌŪĀĒĪŚŹŻĆŃŁ$")
DEFAULT_SAFE = (0.1, 0.1, 0.9, 0.9)     # title safe: (left, top, right, bottom) of the frame
STAGGER = 0.06              # stagger: lines pushed this share of the block's width
CASCADE = 0.07              # cascade: each line stepped this share sideways
POSITIONS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
ARRANGES = ("center", "left", "right", "stagger", "cascade", "kinetic")

# The score's terms and their starting weights - tuned on the contact sheet.
WEIGHTS = {
    "fill": 2.0,            # coverage of the safe zone against the look's target
    "overflow": 50.0,       # wider than the safe zone (lines at the minimum size)
    "hierarchy": 3.0,       # key lines outside the "clearly bigger, not absurdly" band
    "inverted": 8.0,        # a normal line bigger than a key line
    "ratio": 20.0,          # sizes too far apart, or below the readable minimum
    "isolation": 1.0,       # key alone on its line (bonus) / buried in a long line
    "lines": 1.0,           # distance from the preferred line count
    "orphans": 1.5,         # a line ending on a connector, a line of only connectors
    "rag": 2.0,             # ratio looks: very uneven line widths
    "captions": 6.0,        # a caption (stretch words) sharing a line with the title's, or split over lines
    "forced": 1000.0,       # a break the person set (or forbade) not kept
}
HIERARCHY_BAND = (1.25, 2.6)    # key size over the median normal line's
MAX_SPREAD = 0.15               # a caption's extra letter spacing, at most, as a share of its letter size
MAX_RATIO = 4.5                 # biggest line over smallest
# While typing: keep the previous layout's breaks unless another beats it by this much,
# so the title doesn't flip between two near-equal layouts on every keystroke.
STICK = 0.6


def new_doc(text="", look="hormozi"):
    """A title's saved state. "over" holds per-word overrides, role*/line* the per-role and
    per-line ones (a line is keyed by its first word's id); times: word id -> seconds;
    exitTimes: word id -> when its exit starts, seconds."""
    return {
        "text": text, "look": look, "lines": "auto", "arrange": "auto", "bw": None,
        "pos": "look", "custom": False, "gpos": None, "forced": None, "lang": "",
        "over": {"roles": {}, "size": {}, "offset": {}, "colour": {}, "brk": {}, "anim": {}, "rot": {}},
        "roleColour": {}, "roleAnim": {}, "roleStyle": {},
        "lineColour": {}, "lineAnim": {}, "lineOffset": {}, "lineRot": {},
        "anim": "look", "dir": None, "plays": "word", "stagger": 0.1, "animLen": None,
        "exit": {"on": False, "style": "reverse", "order": "same", "dir": None},
        "exitCurves": None,         # the title's own exit, drawn (timing.clean_curve)
        "turns": {},                # kinetic: the person's marks, word id -> line | cw | ccw | none
        "camera": "each",           # kinetic: zoom to each block, or keep "all" in view
        "pulse": False,             # copies that fan out breathe while held (timing.py)
        "times": {},
        "exitTimes": {},
    }


# ---------------------------------------------------------------- words --

def _norm(word):
    """A word for matching: lower-case, without the punctuation around it."""
    low = word.lower()
    return re.sub(r"^[^\w$€£¥#@]+|[^\w%]+$", "", low) or low


def tokenize(text):
    """The words, each with an id that survives editing: its normalised spelling and
    which occurrence it is ("the#2")."""
    tokens, seen = [], {}
    for raw in (text or "").split():
        norm = _norm(raw)
        seen[norm] = seen.get(norm, 0) + 1
        tokens.append({"id": f"{norm}#{seen[norm]}", "text": raw, "norm": norm})
    return tokens


def is_number(norm):
    return bool(_NUMBER.match(norm)) or any(c.isdigit() for c in norm)


def is_stopword(norm):
    return norm in STOPWORDS


def word_score(tokens, i):
    """How much a word asks to be the key: numbers most, then intensifiers; connectors
    never; longer content words a little, the last content word a little (hooks put the
    payoff last)."""
    norm = tokens[i]["norm"]
    score = 0.0
    if is_number(norm):
        score += 3.0
    if norm in INTENSIFIERS or norm.rstrip("s") in INTENSIFIERS:
        score += 2.0
    if is_stopword(norm):
        return score - 3.0
    if len(norm) >= 6:
        score += 0.5
    content = [k for k, t in enumerate(tokens) if not is_stopword(t["norm"])]
    if content and i == content[-1]:
        score += 0.5
    return score


def assign_roles(tokens, look, manual=None):
    """Each word's role and where it came from: [(role, "manual" | "auto")]. Exactly one
    key (two from TWO_KEYS_FROM words), at most one accent and only for a look that
    takes one; a role the person set always wins and counts toward those limits."""
    manual = manual or {}
    n = len(tokens)
    roles = [(manual[t["id"]], "manual") if manual.get(t["id"]) in L.ROLES else ("normal", "auto")
             for t in tokens]
    free = [i for i in range(n) if roles[i][1] == "auto"]
    scores = {i: word_score(tokens, i) for i in free}
    # Content words first, best score first, later first on a tie (the payoff).
    order = sorted(free, key=lambda i: (is_stopword(tokens[i]["norm"]), -scores[i], -i))
    want = (2 if n >= TWO_KEYS_FROM else 1) - sum(1 for r, _src in roles if r == "key")
    picks = [i for i in order if not is_stopword(tokens[i]["norm"])][:max(0, want)]
    if want > 0 and not picks and order:
        picks = [max(order, key=lambda i: len(tokens[i]["norm"]))]    # all connectors
    for i in picks:
        roles[i] = ("key", "auto")
    has_accent = any(r == "accent" for r, _src in roles)
    if look.get("accent") == "auto" and not has_accent and n >= 3:
        for i in order:
            if roles[i][0] == "normal" and not is_stopword(tokens[i]["norm"]) and scores[i] >= ACCENT_MIN:
                roles[i] = ("accent", "auto")
                break
    # A look whose plain words are another role ("others": "small" - a caption under
    # the key) gives them it.
    others = look.get("others")
    if others in L.ROLES and others != "normal":
        roles = [(others, src) if r == "normal" and src == "auto" else (r, src) for r, src in roles]
    return roles


def upper(text, lang=""):
    """Capitals the way the language writes them (Turkish dotted i)."""
    if lang == "tr":
        text = text.replace("i", "İ")
    return text.upper()


# --------------------------------------------------------------- breaks --

def break_sets(n, min_lines, max_lines, max_words, limit=MAX_CANDIDATES):
    """Every way to cut n words into min_lines..max_lines lines of 1..max_words words,
    as [[(first, end)] per line] - up to limit of them."""
    out = []

    def rec(start, lines):
        if len(out) >= limit:
            return
        if start == n:
            if len(lines) >= min_lines:
                out.append(list(lines))
            return
        if len(lines) >= max_lines:
            return
        for k in range(1, max_words + 1):
            if start + k > n:
                break
            lines.append((start, start + k))
            rec(start + k, lines)
            lines.pop()

    rec(0, [])
    return out


def _line_range(n, look, doc):
    forced = doc.get("lines")
    if isinstance(forced, int) and not isinstance(forced, bool) and forced > 0:
        lo = hi = min(forced, n)
    else:
        lo, hi = look["lines"]
        lo, hi = max(1, lo - 1), min(n, hi + 1)
        lo = min(lo, n)
    max_words = max(look["maxWords"], math.ceil(n / hi))
    return lo, hi, max_words


# --------------------------------------------------------------- layout --

class _Word:
    """One word, measured once at letter size 100 in its role's font."""
    __slots__ = ("i", "id", "text", "norm", "role", "role_from", "font", "caps", "scale", "user", "w100", "space100",
                 "asc", "desc", "cap", "above", "below", "top", "bottom", "rot", "tuck", "track", "stretch", "bar",
                 "reach")


def _words(tokens, roles, look, doc, measure, metrics, ink):
    over = doc.get("over") or {}
    sizes, rots = over.get("size") or {}, over.get("rot") or {}
    words = []
    for i, (token, (role, src)) in enumerate(zip(tokens, roles)):
        style = L.role_style(look, role, doc)      # with the role's own face, if the person set one
        w = _Word()
        w.i, w.id, w.norm, w.role, w.role_from = i, token["id"], token["norm"], role, src
        w.font = tuple(style.get("font") or ("Arial", "Bold"))
        w.caps = style.get("caps", look.get("caps", False))
        w.text = upper(token["text"], doc.get("lang", "")) if w.caps else token["text"]
        w.user = float(sizes.get(w.id, 1.0) or 1.0)
        w.scale = float(style.get("scale", 1.0)) * w.user
        # Letter spacing: a share of the letter size added between letters (not after the last).
        w.track = float(style.get("track", 0.0))
        w.stretch = bool(style.get("stretch"))
        w.bar = style.get("bar") if isinstance(style.get("bar"), dict) else None
        w.w100 = float(measure(w.text, w.font, 100.0)) + w.track * 100.0 * max(len(w.text) - 1, 0)
        # In letter-spaced text a space is spaced too, on both sides - as one line would set it.
        w.space100 = float(measure(" ", w.font, 100.0)) + 2 * w.track * 100.0
        m = {**DEFAULT_METRICS, **(metrics(w.font) if metrics else {})}
        w.asc, w.desc, w.cap = m["asc"], m["desc"], m["cap"]
        if ink:
            above, below = ink(w.text, w.font)
            w.above, w.below = max(w.cap, float(above)), max(0.0, float(below))
            # The ink itself, lower case too ("can" reaches only its x-height): what a
            # stack beside the key lines up with.
            w.top, w.bottom = max(0.0, float(above)), w.below
        else:
            w.above = w.cap * 1.2 if _ABOVE_CAP & set(w.text) else w.cap
            w.below = w.desc * 0.8 if _BELOW_BASELINE & set(w.text) else 0.0
            w.top = w.above if w.caps else w.cap * 1.05
            w.bottom = w.below if w.caps else (w.desc * 0.8 if set("gjpqy") & set(w.text) else 0.0)
        w.rot = float(style.get("rot", 0.0)) + float(rots.get(w.id, 0.0) or 0.0)
        w.tuck = float(style.get("tuck", 0.0)) if role == "accent" else 0.0
        # How far the word's copies (echo, extrude) reach above and below it, in its size:
        # its line makes room for them, so they don't run into the next.
        w.reach = [0.0, 0.0]
        for group in style.get("copies") or []:
            dy = float((group.get("step") or (0, 0))[1]) * int(group.get("n", 0))
            for side in ((1, -1) if group.get("both") else (1,)):
                if dy * side < 0:
                    w.reach[0] = max(w.reach[0], -dy * side)
                else:
                    w.reach[1] = max(w.reach[1], dy * side)
        # A box behind the word (bg) stands its padding out past the ink, above and below.
        if isinstance(style.get("bg"), dict):
            pad = float(style["bg"].get("pad", 0.15))
            w.reach = [max(w.reach[0], pad), max(w.reach[1], pad)]
        words.append(w)
    return words


def _k(words, scaled, look=None):
    """A line's width at letter size 1: its words plus the spaces between them (each
    space in the font and size of the word before it). A line that stacks words beside
    its key (look's "stack") is as wide as its stacked layout."""
    if look is not None and _sides(words, look):
        return _lay(words, 1.0, look)["w"]
    total = 0.0
    for n, w in enumerate(words):
        f = w.scale if scaled else 1.0
        total += w.w100 / 100.0 * f
        if n < len(words) - 1:
            total += w.space100 / 100.0 * f
    return total


# ---------------------------------------------------------------- stacks --
# A look with "stack" sets the words beside a big key in rows (the "like / this can"
# title): the rows' tops and baselines span the key's ink, from its tallest letter to
# its baseline, so the column is exactly as tall as the key. A single word beside the
# key stays on its baseline - one row filling the key's height would be as big as it -
# and so do words whose rows would come out smaller than the title's other words (a
# key with no tall letters, "never", leaves its column too little height).

STACK_LEAD = 0.82       # rows at least this far apart, baseline to baseline, in their letter size
STACK_GAP = 0.14        # and this much clear between one row's ink and the next
STACK_MIN = 0.9         # rows at least this share of the words' own size on the line


def _row_w(ws):
    """Words in a row, at letter size 1 (the person's size changes on)."""
    return sum(w.w100 / 100.0 * w.user + (w.space100 / 100.0 * w.user if n < len(ws) - 1 else 0.0)
               for n, w in enumerate(ws))


def _stack_rows(ws):
    """The side words cut into rows - two, or three for five words or more - so the
    widest row is as narrow as it can be."""
    count = min(len(ws), 2 if len(ws) <= 4 else 3)
    best = None
    for cuts in break_sets(len(ws), count, count, len(ws)):
        rows = [ws[i:j] for i, j in cuts]
        widest = max(_row_w(r) for r in rows)
        if best is None or widest < best[0] - 1e-9:
            best = (widest, rows)
    return best[1]


def _stack(ws, height):
    """Rows of ws filling height px: (rows, their letter size, each row's baseline as an
    offset up from the key's baseline - the last row's is 0)."""
    rows = _stack_rows(ws)
    pitches = [max(STACK_LEAD, max(w.bottom for w in a) + STACK_GAP + max(w.top for w in b))
               for a, b in zip(rows, rows[1:])]
    size = height / max(max(w.top for w in rows[0]) + sum(pitches), 1e-9)
    ups, up = [], 0.0
    for p in reversed(pitches):
        up += p * size
        ups.append(up)
    return rows, size, list(reversed(ups)) + [0.0]


def _sides(ws, look):
    """A line's key and which of its sides stack, as (key index, left stacks, right
    stacks) - None when nothing on it does: one key, two or more words on a side, and
    rows no smaller than STACK_MIN of those words' size on the baseline."""
    if not look.get("stack") or any(w.stretch for w in ws):
        return None
    keys = [n for n, w in enumerate(ws) if w.role == "key"]
    if len(keys) != 1:
        return None
    k = keys[0]
    height = ws[k].scale * ws[k].top            # the key's ink at body size 1

    def stacks(side):
        if len(side) < 2:
            return False
        _rows, size, _ups = _stack(side, height)
        return size >= STACK_MIN * max(w.scale / w.user for w in side)

    left, right = stacks(ws[:k]), stacks(ws[k + 1:])
    return (k, left, right) if left or right else None


def _lay(ws, s, look, spread=0.0):
    """A line at body letter size s: each word's x, letter size and baseline offset (down
    from the line's baseline), and the line's width. Words follow one another on the
    baseline, or - on a stacking line - stand in rows beside the key."""
    xs, sizes, oys = [0.0] * len(ws), [0.0] * len(ws), [0.0] * len(ws)

    def inline(idx, x):
        """ws[idx] one after another on the baseline from x; returns where they end. In
        a stacking look a space between a big word and a small one is the small one's -
        the big one's would leave a gap as wide as a letter of it."""
        for m, n in enumerate(idx):
            sizes[n] = s * ws[n].scale
            xs[n] = x
            x += ws[n].w100 / 100.0 * sizes[n] + spread * max(len(ws[n].text) - 1, 0)
            if m < len(idx) - 1:
                nxt = ws[idx[m + 1]]
                if look.get("stack") and nxt.scale < ws[n].scale:
                    x += nxt.space100 / 100.0 * s * nxt.scale + spread
                else:
                    x += ws[n].space100 / 100.0 * sizes[n] + spread
        return x

    sides = _sides(ws, look)
    if sides is None:
        return {"xs": xs, "sizes": sizes, "oys": oys, "w": inline(range(len(ws)), 0.0), "stacked": False}
    k, stack_left, stack_right = sides
    key = ws[k]
    sizes[k] = s * key.scale
    height = sizes[k] * key.top

    def stacked(idx, x, align_right):
        """ws[idx] in rows from x (pressed against the key on its side); returns where
        they end and the space they keep from the key, in their size."""
        rows, size, ups = _stack([ws[n] for n in idx], height)
        widths = [_row_w(r) * size for r in rows]
        n = idx[0]
        for row, up, rw in zip(rows, ups, widths):
            rx = x + (max(widths) - rw if align_right else 0.0)
            for m, w in enumerate(row):
                sizes[n], oys[n], xs[n] = size * w.user, -up, rx
                rx += w.w100 / 100.0 * sizes[n] + (w.space100 / 100.0 * sizes[n] if m < len(row) - 1 else 0.0)
                n += 1
        return x + max(widths)

    def gap(idx, stacks):
        """The space between a side and the key - a space in the side's size."""
        if stacks:
            return ws[idx[0]].space100 / 100.0 * _stack([ws[n] for n in idx], height)[1]
        n = idx[-1] if idx[-1] < k else idx[0]
        return ws[n].space100 / 100.0 * s * ws[n].scale

    left, right = list(range(k)), list(range(k + 1, len(ws)))
    x = 0.0
    if left:
        x = (stacked(left, 0.0, True) if stack_left else inline(left, 0.0)) + gap(left, stack_left)
    xs[k] = x
    x += key.w100 / 100.0 * sizes[k]
    if right:
        x += gap(right, stack_right)
        x = stacked(right, x, False) if stack_right else inline(right, x)
    return {"xs": xs, "sizes": sizes, "oys": oys, "w": x, "stacked": True}


def _sizes(lines, look, W, room, frame_h):
    """Each line's letter size (before the roles' scale): fit mode widens every line to
    W within [minSize, maxSize]; ratio mode gives every line the narrowest fit. The roles'
    scale goes on after, so a scaled-up word may take its line past W - up to room, the
    safe zone's width."""
    max_s, min_s = look["maxSize"] * frame_h, look["minSize"] * frame_h
    if look["mode"] == "ratio":
        body = min(room / max(_k(ws, True, look), 1e-9) for ws in lines)
        body = min(body, min(W / max(_k(ws, False, look), 1e-9) for ws in lines))
        body = min(body, max_s)
        # The readable minimum, unless it would push a scaled-up word out of the safe
        # zone - then smaller (the score's "ratio" term counts it against the layout).
        if max(min_s * _k(ws, True, look) for ws in lines) <= room:
            body = max(body, min_s)
        return [body] * len(lines)
    sizes = []
    for ws in lines:
        s = min(W / max(_k(ws, False, look), 1e-9), max_s)
        if s * _k(ws, True, look) > room:     # a scaled-up word pushed it out of the safe zone
            s = room / _k(ws, True, look)
        sizes.append(max(s, min_s))
    # A short line with no key would widen to bigger than the key - "THIS" alone above
    # "CHANGES EVERYTHING". Plain lines go no bigger than the key instead (and narrower
    # than the block; the arrangement places them); how much smaller is the score's.
    keys = [s * w.scale for ws, s in zip(lines, sizes) for w in ws if w.role == "key"]
    if keys:
        cap = min(keys)
        for k, ws in enumerate(lines):
            if not any(w.role == "key" for w in ws):
                sizes[k] = max(min(sizes[k], cap / max(w.scale for w in ws)), min_s)
    # A caption (stretch words) takes the title's letter size - its role's scale then
    # makes it small - rather than being fitted to the width: split over short lines,
    # it would otherwise grow line by line.
    titles = [s for ws, s in zip(lines, sizes) if not all(w.stretch for w in ws)]
    if titles:
        for k, ws in enumerate(lines):
            if all(w.stretch for w in ws):
                fits = room / max(_k(ws, True, look), 1e-9)
                sizes[k] = max(min(max(titles), fits), min_s)
    return sizes


def _geometry(lines, sizes, look):
    """Each line's words' sizes, x within the line, its width, and its baseline from the
    block's top. A line is as tall as its biggest word: capitals by their cap height,
    mixed case by ascent and descent. An accent with tuck sinks into the line below.
    A stacked word's baseline sits its "oy" below the line's (up is negative)."""
    def natural(ws, s):
        return _lay(ws, s, look)["w"]

    # A line of stretch words (a caption) is tracked out to the widest other line: the
    # extra goes between its letters and its words alike ("spread", px a gap).
    stretched = [all(w.stretch for w in ws) for ws in lines]
    others = [natural(ws, s) for ws, s, st in zip(lines, sizes, stretched) if not st]
    target = max(others, default=0.0)
    geo, prev = [], None
    for ws, s, st in zip(lines, sizes, stretched):
        spread = 0.0
        if st and target:
            gaps = sum(max(len(w.text) - 1, 0) for w in ws) + len(ws) - 1
            spread = max(0.0, (target - natural(ws, s)) / gaps) if gaps else 0.0
            # No wider than MAX_SPREAD of the letter size: a short caption stays short
            # rather than becoming letters scattered across the frame.
            spread = min(spread, MAX_SPREAD * s * max(w.scale for w in ws))
        lay = _lay(ws, s, look, spread)
        x, xs, wsz, oys = lay["w"], lay["xs"], lay["sizes"], lay["oys"]
        if look.get("inkLines"):    # lower case by its letters' ink, not the face's ascent and descent
            asc = max(z * (w.top + w.reach[0]) - oy for w, z, oy in zip(ws, wsz, oys))
            desc = max(oy + z * (w.bottom + w.reach[1]) for w, z, oy in zip(ws, wsz, oys))
        else:
            asc = max(z * ((w.above if w.caps else w.asc) + w.reach[0]) - oy for w, z, oy in zip(ws, wsz, oys))
            desc = max(oy + z * ((w.below if w.caps else w.desc) + w.reach[1]) for w, z, oy in zip(ws, wsz, oys))
        big = max(wsz)
        if prev is None:
            base = asc
        else:
            gap = look["gap"] * max(prev["big"], big)
            tuck = max((z * w.tuck * (w.cap if w.caps else w.asc) for w, z in zip(prev["words"], prev["sizes"])),
                       default=0.0)
            base = prev["base"] + prev["desc"] + gap + asc - tuck
        prev = {"words": ws, "sizes": wsz, "xs": xs, "oys": oys, "w": x, "asc": asc, "desc": desc, "big": big,
                "base": base, "spread": spread, "stacked": lay["stacked"]}
        geo.append(prev)
    return geo


def _arrange(geo, how):
    """Each line's left edge in the block, then the block re-centred on its ink."""
    widest = max(g["w"] for g in geo)
    median = statistics.median(g["big"] for g in geo)
    n = len(geo)
    for k, g in enumerate(geo):
        if how == "left":
            g["x"] = 0.0
        elif how == "right":
            g["x"] = widest - g["w"]
        else:
            g["x"] = (widest - g["w"]) / 2
            if how == "stagger" and n > 1:
                g["x"] += STAGGER * widest * (1 if g["big"] >= median else -1)
            elif how == "cascade":
                g["x"] += CASCADE * widest * (k - (n - 1) / 2)
    left = min(g["x"] for g in geo)
    for g in geo:
        g["x"] -= left
    return max(g["x"] + g["w"] for g in geo), geo[-1]["base"] + geo[-1]["desc"]


def _score(geo, block_w, block_h, words, cuts, look, doc, safe_w, safe_h, frame_h):
    terms = dict.fromkeys(WEIGHTS, 0.0)
    target = look["fill"]
    cover = block_w * block_h / max(safe_w * safe_h, 1e-9)
    terms["fill"] = abs(cover - target) / target
    if block_w > safe_w * 1.001:
        terms["overflow"] = block_w / safe_w - 1
    key_sizes = [z for g in geo for w, z in zip(g["words"], g["sizes"]) if w.role == "key"]
    plain = [g["big"] for g in geo if all(w.role in ("normal", "small") for w in g["words"])
             and not all(w.role == "small" for w in g["words"])]
    if key_sizes and plain:
        r = min(key_sizes) / statistics.median(plain)
        lo, hi = HIERARCHY_BAND
        terms["hierarchy"] = max(0.0, lo - r) + max(0.0, r - hi)
        if max(plain) > min(key_sizes) * 1.001:
            terms["inverted"] = max(plain) / min(key_sizes) - 1
    bigs = [g["big"] for g in geo]
    if max(bigs) / max(min(bigs), 1e-9) > MAX_RATIO:
        terms["ratio"] += max(bigs) / min(bigs) / MAX_RATIO - 1
    min_s = look["minSize"] * frame_h
    smallest = min(z for g in geo for z in g["sizes"])
    if smallest < min_s * 0.999:
        terms["ratio"] += 1 - smallest / min_s
    for g in geo:
        n = len(g["words"])
        for w in g["words"]:
            if w.role == "key":
                # Words stacked beside a key leave it standing out as if alone.
                terms["isolation"] += -1.0 if n == 1 or g["stacked"] else (1.0 if n >= 4 else 0.0)
                if n > 1 and look.get("keyAlone"):      # Echo: its copies need the line to itself
                    terms["isolation"] += 6.0
    pref = doc["lines"] if isinstance(doc.get("lines"), int) else preferred_lines(look, safe_w, safe_h)
    terms["lines"] = abs(len(geo) - min(pref, len(words)))
    for k, g in enumerate(geo):
        last = g["words"][-1]
        if k < len(geo) - 1 and is_stopword(last.norm) and last.role != "small":
            # Twice over at the foot of a stack: "the" tucked beside the key reads as
            # finished there, a phrase cut off from the next line.
            terms["orphans"] += 2.0 if g["stacked"] else 1.0
        if all(is_stopword(w.norm) for w in g["words"]) and not all(w.role == "small" for w in g["words"]):
            terms["orphans"] += 1.5
    if look["mode"] == "ratio" and len(geo) > 1:
        widest = max(g["w"] for g in geo)
        terms["rag"] = sum((1 - g["w"] / widest) ** 2 for g in geo) / len(geo)
    # A caption is a line of its own, and one line if it can be.
    stretch_lines = 0
    for g in geo:
        kinds = {w.stretch for w in g["words"]}
        if len(kinds) > 1:
            terms["captions"] += 1.0
        elif kinds == {True}:
            stretch_lines += 1
    terms["captions"] += max(0, stretch_lines - 1) * 0.5
    starts = {words[i].id for i, _end in cuts}
    for wid, brk in ((doc.get("over") or {}).get("brk") or {}).items():
        if wid in {w.id for w in words} and wid != words[0].id and bool(brk) != (wid in starts):
            terms["forced"] += 1.0
    total = sum(WEIGHTS[t] * v for t, v in terms.items())
    return total, terms


def preferred_lines(look, safe_w, safe_h):
    """The line count a look prefers - one more in a tall frame, whose narrow lines
    hold fewer words."""
    return look.get("pref", 3) + (1 if safe_h > safe_w * 1.2 else 0)


def _evaluate(words, cuts, look, doc, W, safe_w, safe_h, frame_h):
    lines = [words[i:j] for i, j in cuts]
    sizes = _sizes(lines, look, W, safe_w, frame_h)
    geo = _geometry(lines, sizes, look)
    block_w, block_h = _arrange(geo, _arrange_of(look, doc))
    # Too tall for the safe zone: everything scales together (all of it is linear in size).
    f = safe_h / block_h if block_h > safe_h else 1.0
    # Ratio mode sizes every line to the narrowest fit, which leaves nothing to stop a
    # block of short lines (words stacked beside the key) from growing to fill the
    # frame: past the look's fill, it comes down to it (area goes with size squared).
    if look["mode"] == "ratio":
        cover = block_w * f * block_h * f / max(safe_w * safe_h, 1e-9)
        if cover > look["fill"]:
            f *= math.sqrt(look["fill"] / cover)
    # Too wide too: lines fitted to the width can still pass it once arranged (stagger
    # pushes them sideways) or letter-spaced.
    if block_w * f > safe_w:
        f = safe_w / block_w
    if f < 1.0:
        sizes = [s * f for s in sizes]
        geo = _geometry(lines, sizes, look)
        block_w, block_h = _arrange(geo, _arrange_of(look, doc))
    score, terms = _score(geo, block_w, block_h, words, cuts, look, doc, safe_w, safe_h, frame_h)
    return {"cuts": cuts, "geo": geo, "w": block_w, "h": block_h, "score": score, "terms": terms}


def _arrange_of(look, doc):
    how = doc.get("arrange")
    return how if how in ARRANGES else look.get("arrange", "center")


def _place(block_w, block_h, look, doc, frame, safe_px):
    """The block's top-left: one of nine spots in the safe zone, or a custom centre."""
    fw, fh = frame
    x0, y0, x1, y1 = safe_px
    if doc.get("custom") and doc.get("gpos"):
        cx, cy = doc["gpos"]
        return cx * fw - block_w / 2, cy * fh - block_h / 2
    pos = doc.get("pos")
    pos = pos if pos in POSITIONS else look.get("place", "mc")
    row, col = pos[0], pos[1]
    x = {"l": x0, "c": (x0 + x1 - block_w) / 2, "r": x1 - block_w}[col]
    y = {"t": y0, "m": (y0 + y1 - block_h) / 2, "b": y1 - block_h}[row]
    return x, y


def _key_of(words, cuts):
    """A layout's breaks as the ids of each line's first word - kept by a Suggestion or
    the previous layout, and still meaning the same breaks after the text is edited."""
    return tuple(words[i].id for i, _end in cuts)


def compose(doc, frame, measure, metrics=None, safe=None, prev=None, ink=None):
    """The layout of doc's title in a frame of (width, height) px. safe: the safe zone as
    (left, top, right, bottom) fractions of the frame. prev: the key of the layout shown
    before this edit, kept unless another clearly beats it (see STICK). ink(text, font):
    how far the text's ink reaches (above, below) its baseline, in ems."""
    look = L.get(doc.get("look"))
    fw, fh = frame
    l, t, r, b = safe or DEFAULT_SAFE
    safe_px = (l * fw, t * fh, r * fw, b * fh)
    safe_w, safe_h = safe_px[2] - safe_px[0], safe_px[3] - safe_px[1]
    tokens = tokenize(doc.get("text", ""))
    out = {"frame": (fw, fh), "safe": safe_px, "look": look["id"], "tokens": [], "lines": [], "boxes": [], "bars": [],
           "block": None, "key": (), "score": 0.0, "terms": {}, "alternates": [], "candidates": 0}
    if not tokens:
        return out
    roles = assign_roles(tokens, look, (doc.get("over") or {}).get("roles"))
    words = _words(tokens, roles, look, doc, measure, metrics, ink)
    if _arrange_of(look, doc) == "kinetic":
        return _kinetic(out, words, tokens, look, doc, frame, safe_px)
    bw = doc.get("bw") or look["bw"]
    W = bw * safe_w
    lo, hi, max_words = _line_range(len(words), look, doc)
    cands = [_evaluate(words, cuts, look, doc, W, safe_w, safe_h, fh) for cuts in break_sets(len(words), lo, hi, max_words)]
    for c in cands:
        c["key"] = _key_of(words, c["cuts"])
    best = min(cands, key=lambda c: c["score"])
    by_key = {c["key"]: c for c in cands}
    forced = tuple(doc["forced"]) if doc.get("forced") else None
    if forced in by_key:
        win = by_key[forced]
    elif prev and tuple(prev) in by_key and by_key[tuple(prev)]["score"] <= best["score"] + STICK:
        win = by_key[tuple(prev)]
    else:
        win = best
    out.update(_finish(win, words, look, doc, frame, safe_px))
    out["tokens"] = [{"id": w.id, "text": w.text, "role": w.role, "role_from": w.role_from} for w in words]
    out["alternates"] = _alternates(cands, win)
    out["candidates"] = len(cands)
    return out


def _kinetic(out, words, tokens, look, doc, frame, safe_px):
    """The kinetic arrangement (kinetic.py): blocks by the words' marks, in world px; the
    camera that frames them is the clock's (timing.py). No line choice to make, so no
    Suggestions; no bars."""
    made = kinetic.compose(words, look, doc, tokens, frame, safe_px, _geometry)
    boxes = made["boxes"]
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    marks = kinetic.marks_of(tokens, doc)
    out.update({"lines": made["lines"], "boxes": boxes, "bars": [], "block": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                "key": tuple(ids[0] for ids in made["lines"]), "candidates": 1,
                "kinetic": {"blocks": made["blocks"], "camera": doc.get("camera") if doc.get("camera") in kinetic.CAMERAS else "each"}})
    out["tokens"] = [{"id": w.id, "text": w.text, "role": w.role, "role_from": w.role_from,
                      "mark": m, "mark_from": src} for w, (m, src) in zip(words, marks)]
    return out


def _alternates(cands, win, count=3):
    """The best layout for each other line count - the Suggestions."""
    best_per = {}
    for c in cands:
        n = len(c["cuts"])
        if n != len(win["cuts"]) and (n not in best_per or c["score"] < best_per[n]["score"]):
            best_per[n] = c
    picks = sorted(best_per.values(), key=lambda c: c["score"])[:count]
    return [{"key": c["key"], "lines": len(c["cuts"]), "score": c["score"], "terms": c["terms"]} for c in picks]


def _finish(c, words, look, doc, frame, safe_px):
    """The chosen layout's boxes in frame px: each word's left edge, ink top, advance
    width, ink height, letter size, baseline and rotation. Nudges (a word's offset in its
    own size, a line's in its biggest word's) move words after the block is placed, so
    they don't shift everything else."""
    bx, by = _place(c["w"], c["h"], look, doc, frame, safe_px)
    over = doc.get("over") or {}
    offsets, line_offsets, line_rots = over.get("offset") or {}, doc.get("lineOffset") or {}, doc.get("lineRot") or {}
    boxes, lines, bars = [], [], []
    for k, g in enumerate(c["geo"]):
        first = g["words"][0].id
        ldx, ldy = line_offsets.get(first) or (0.0, 0.0)
        lines.append([w.id for w in g["words"]])
        line_boxes = []
        for w, x, size, oy in zip(g["words"], g["xs"], g["sizes"], g["oys"]):
            dx, dy = offsets.get(w.id) or (0.0, 0.0)
            ink = size * (w.above if w.caps else w.asc)
            left = bx + g["x"] + x + dx * size + ldx * g["big"]
            base = by + g["base"] + oy + dy * size + ldy * g["big"]
            track = w.track * size + g["spread"]        # px between letters
            box = {
                "id": w.id, "text": w.text, "role": w.role, "line": k, "font": w.font,
                "x": left, "y": base - ink, "w": w.w100 / 100.0 * size + g["spread"] * max(len(w.text) - 1, 0),
                "h": ink + size * (w.below if w.caps else w.desc), "size": size, "baseline": base,
                "rot": w.rot + float(line_rots.get(first, 0.0) or 0.0), "track": track,
            }
            boxes.append(box)
            line_boxes.append(box)
        # A bar behind the whole line, when its words all ask for one (a caption bar).
        bar = g["words"][0].bar
        if bar and all(w.bar == bar for w in g["words"]):
            big = max(b["size"] for b in line_boxes)
            px, py = float(bar.get("padX", 0.35)) * big, float(bar.get("padY", 0.3)) * big
            x0, x1 = min(b["x"] for b in line_boxes), max(b["x"] + b["w"] for b in line_boxes)
            y0, y1 = min(b["y"] for b in line_boxes), max(b["baseline"] for b in line_boxes)
            bars.append({"line": k, "x": x0 - px, "y": y0 - py, "w": x1 - x0 + 2 * px, "h": y1 - y0 + 2 * py,
                         "colour": bar.get("colour", "#000000"), "first": first})
    return {"lines": lines, "boxes": boxes, "bars": bars, "block": {"x": bx, "y": by, "w": c["w"], "h": c["h"]},
            "key": c["key"], "score": c["score"], "terms": c["terms"]}


def hit_boxes(layout):
    """Each word's box as fractions of the frame - what a click on the preview maps to."""
    fw, fh = layout["frame"]
    return [{"id": b["id"], "x": b["x"] / fw, "y": b["y"] / fh, "w": b["w"] / fw, "h": b["h"] / fh}
            for b in layout["boxes"]]


def describe(layout):
    """The composer's choice in a few lines - the probe to run before changing anything
    when a layout looks wrong."""
    rows = [f"{layout['look']}: {len(layout['lines'])} lines, score {layout['score']:.2f} "
            f"of {layout['candidates']} candidates"]
    sizes = {}
    for b in layout["boxes"]:
        sizes.setdefault(b["line"], []).append(b)
    for k, ids in enumerate(layout["lines"]):
        words = sizes.get(k, [])
        text = " ".join("%s[%s]" % (b["text"], b["role"][0]) for b in words)
        rows.append(f"  {text}  size {max(b['size'] for b in words):.0f}px")
    rows.append("  terms: " + ", ".join(f"{t} {v:.2f}" for t, v in layout["terms"].items() if v))
    for alt in layout["alternates"]:
        rows.append(f"  alt {alt['lines']} lines: {alt['score']:.2f}")
    return "\n".join(rows)
