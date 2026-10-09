#!/usr/bin/env python3
"""
Highlight - the Presets tab's word highlight: on a Text+ caption, the word
being said gets a coloured box behind it and/or a colour of its own, and
pops, then hands the highlight on to the next word as it's said. The
timing and the box's moves here; highlight_resolve.py puts them on the clip.

When each word is said comes from Transcribe: every run keeps its word
timings (~/.buddy/transcribe/transcripts/*.json, seconds from the
timeline's first frame - the same 0 its subtitles are placed from). A Text+
made by Subtitle Conversion sits where its subtitle did, so the transcript's
words inside the clip's time are its words. They're matched to the Text+'s
own words in order (difflib), so a word fixed by hand or a comma added
still lines up; a word with no match gets a time between its neighbours'.
A clip with too few matches (translated, or rewritten) is timed evenly, by
each word's share of the characters - as Transcribe times a segment that
has no word timings.

The box is one Rectangle mask moving from word to word: at each word's
start it slides (or jumps) over in SLIDE seconds and pops - a little
bigger, then back - and it stays on that word until the next one starts,
pauses included. Its keys are motion.plan's format ({frame: {"value", "lh",
"rh"}}, handles as points), so motion_resolve._key writes them.

No Resolve and no Qt in here: tests/test_highlight.py runs it on plain Python.
"""

import difflib
import json
import re
from pathlib import Path

from . import units as un

# The box with the word in a colour of its own (word_color) on it, the box
# alone, or no box and the word in the highlight colour. box_color is the
# highlight colour: the box's, or the word's when there's no box.
LOOKS = ("both", "box", "color")
MOVES = ("slide", "jump")
POPS = {"off": 1.0, "small": 1.08, "big": 1.18}
SHAPES = {"square": 0.0, "rounded": 0.3, "pill": 1.0}   # Rectangle mask Corner Radius
DEFAULTS = {"look": "both", "box_color": "#ffd60a", "word_color": "#111111",
            "pop": "small", "move": "slide", "shape": "rounded"}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")

SLIDE = 0.1          # seconds the box takes to reach the next word (a jump: one frame)
SETTLE = 0.16        # seconds a pop takes to come back to size
ENTER = 0.7          # the box's size, as a share, as it comes in on the first word
# Round the word's ink, as shares of its line's height: the box (behind the
# word) and the colour (on it - tight, so it never reaches the next word).
BOX_PAD = (0.16, 0.08)
COLOR_PAD = (0.06, 0.06)
MIN_MATCH = 0.5      # matched words / the Text+'s words, at least, to use the transcript's times
NEAR = 0.3           # seconds round the clip a transcript word may start and still be its

_PUNCT = re.compile(r"[^\w]+", re.UNICODE)


def clean_options(raw):
    """The Highlight choices from settings or the page, made safe."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, allowed in (("look", LOOKS), ("move", MOVES), ("pop", tuple(POPS)), ("shape", tuple(SHAPES))):
        out[key] = raw.get(key) if raw.get(key) in allowed else DEFAULTS[key]
    for key in ("box_color", "word_color"):
        value = raw.get(key)
        out[key] = value.lower() if isinstance(value, str) and _HEX.fullmatch(value) else DEFAULTS[key]
    return out


def rgb(hex_color):
    """'#rrggbb' as (r, g, b), each 0-1."""
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def color_gain(target, fill):
    """The gain per channel that turns the Text+'s fill colour into `target`
    (both (r, g, b) 0-1). A gain, not a new colour: a black outline or
    shadow stays black. A channel the fill has none of can't be raised; it's
    left as it is."""
    return tuple(t / f if f > 0.004 else 1.0 for t, f in zip(target, fill))


# ------------------------------------------------------------------ words

def words_of(text):
    """The Text+'s words: [(first char, last char + 1, word)] - its runs of
    non-space, so the character numbers are the Text+'s own (line breaks
    count, as the Follower and Write On count them)."""
    out, begin = [], None
    text = text or ""
    for i, ch in enumerate(text + " "):
        if ch in un._SPACE:
            if begin is not None:
                out.append((begin, i, text[begin:i]))
                begin = None
        elif begin is None:
            begin = i
    return out


def _norm(word):
    return _PUNCT.sub("", str(word or "").lower())


def transcript_words(data):
    """A transcript's words in time order: [{"start", "end", "text"}], seconds
    from the timeline's start. Segments with no word timings give none."""
    words = []
    for seg in (data or {}).get("segments") or []:
        for w in seg.get("words") or []:
            text = str(w.get("word") or w.get("text") or "").strip()
            try:
                start, end = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if text:
                words.append({"start": start, "end": max(start, end), "text": text})
    words.sort(key=lambda w: w["start"])
    return words


def spoken_in(words, start, end):
    """The transcript words said inside start..end seconds (NEAR either side)."""
    return [w for w in words if start - NEAR <= w["start"] < end + NEAR]


def even_times(text_words, start, end):
    """Each word's start, sharing start..end out by characters."""
    sizes = [len(w[2]) + 1 for w in text_words]
    total = float(sum(sizes)) or 1.0
    out, at = [], start
    for size in sizes:
        out.append(at)
        at += (end - start) * size / total
    return out


def match(text_words, spoken):
    """(start of each Text+ word or None, how many matched) - the Text+'s
    words lined up with the transcript's in order."""
    ours = [_norm(w[2]) for w in text_words]
    theirs = [_norm(w["text"]) for w in spoken]
    times = [None] * len(ours)
    matcher = difflib.SequenceMatcher(None, ours, theirs, autojunk=False)
    matched = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = spoken[j1 + k]["start"]
                matched += 1
        elif tag == "replace" and i2 - i1 == j2 - j1:
            # Words changed by hand ("colour" for "color"): the same places.
            for k in range(i2 - i1):
                times[i1 + k] = spoken[j1 + k]["start"]
        elif tag == "replace":
            # Uneven: each of ours to the nearest-spelled one of theirs after
            # the last one taken, when it's close enough.
            j = j1
            for i in range(i1, i2):
                best, at = 0.0, None
                for k in range(j, j2):
                    ratio = difflib.SequenceMatcher(None, ours[i], theirs[k]).ratio()
                    if ratio > best:
                        best, at = ratio, k
                if at is not None and best >= 0.6:
                    times[i] = spoken[at]["start"]
                    j = at + 1
    return times, matched


def filled(times, text_words, start, end):
    """Times with every None given one between its neighbours' (shared out by
    characters with the word before the gap), and kept in order - never
    before the one before it."""
    n = len(times)
    out = list(times)
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < n and out[j] is None:
            j += 1
        hi = out[j] if j < n else end
        if i > 0:
            out[i:j] = even_times(text_words[i - 1:j], out[i - 1], max(out[i - 1], hi))[1:]
        else:
            out[i:j] = even_times(text_words[i:j], start, max(start, hi))
        i = j
    for k in range(1, n):
        out[k] = max(out[k], out[k - 1])
    return out


def word_times(text, words, start, end):
    """(start of each of the Text+'s words, "transcript" or "even") for a
    clip from `start` to `end` seconds, `words` the transcript's (or None)."""
    text_words = words_of(text)
    if not text_words:
        return [], "even"
    if words:
        times, matched = match(text_words, spoken_in(words, start, end))
        if matched and matched >= MIN_MATCH * len(text_words):
            times = filled(times, text_words, start, end)
            return [min(max(t, start), end) for t in times], "transcript"
    return even_times(text_words, start, end), "even"


def matched_count(texts_and_spans, words):
    """How many words of the clips [(text, start, end)] a transcript matches -
    to choose between transcripts."""
    total = 0
    for text, start, end in texts_and_spans:
        text_words = words_of(text)
        if text_words:
            total += match(text_words, spoken_in(words, start, end))[1]
    return total


def load_transcripts(folder, newest=20):
    """[(path, words)] for the newest transcripts in `folder`, newest first.
    One that won't read is passed over."""
    try:
        files = sorted(Path(folder).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:newest]
    except OSError:
        return []
    out = []
    for path in files:
        try:
            words = transcript_words(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if words:
            out.append((path, words))
    return out


def best_transcript(transcripts, clips):
    """(path, words) of the transcript that matches the clips [(text, start,
    end)] best, or (None, None) when none matches a word. A tie goes to the
    newer one."""
    best, score = (None, None), 0
    for path, words in transcripts:
        n = matched_count(clips, words)
        if n > score:
            best, score = (path, words), n
    return best


# ----------------------------------------------------------------- layout

def lines_of(boxes):
    """Each word's line number, from its ink box (cx, cy, w, h) - words whose
    heights overlap by half the smaller one are on one line."""
    lines, marks = [], []
    for cx, cy, w, h in boxes:
        lo, hi = cy - h / 2, cy + h / 2
        for n, (llo, lhi) in enumerate(lines):
            overlap = min(hi, lhi) - max(lo, llo)
            if overlap >= 0.5 * min(h, lhi - llo):
                lines[n] = (min(lo, llo), max(hi, lhi))
                marks.append(n)
                break
        else:
            lines.append((lo, hi))
            marks.append(len(lines) - 1)
    return marks, lines


def word_boxes(ink, aspect, pad):
    """Each word's box (cx, cy, w, h) as shares of the frame: its own ink
    across, its line's ink up and down (so every box on a line is as tall),
    and `pad` (across, up) of the line's height round it. `aspect` is the
    frame's width / height: padding is the same in pixels both ways."""
    marks, lines = lines_of(ink)
    out = []
    for (cx, _cy, w, _h), n in zip(ink, marks):
        lo, hi = lines[n]
        height = hi - lo
        out.append((cx, (lo + hi) / 2, w + 2 * pad[0] * height / aspect, height * (1 + 2 * pad[1])))
    return out


# ------------------------------------------------------------------- keys

def _eased(points):
    """[(frame, value)] as keys with flat handles a third of the way to their
    neighbours - an ease out of every key and into the next, never past
    either value."""
    keys = {}
    for i, (f, v) in enumerate(points):
        key = {"value": v, "lh": None, "rh": None}
        if i > 0:
            key["lh"] = (f - (f - points[i - 1][0]) / 3.0, v)
        if i + 1 < len(points):
            key["rh"] = (f + (points[i + 1][0] - f) / 3.0, v)
        keys[f] = key
    return keys


class _Track:
    """One channel's points, frames always rising: a point on or before the
    last one takes its place."""

    def __init__(self):
        self.points = []

    def add(self, frame, value):
        if self.points and frame <= self.points[-1][0]:
            self.points[-1] = (self.points[-1][0], value)
        else:
            self.points.append((frame, value))


def plan(starts, boxes, frames, fps, options):
    """The box's keys over a clip `frames` long (comp frames from 0, `fps` of
    them a second): {"x", "y", "w", "h", "level"} - where it is, how big
    (shares of the frame) and how much of it shows - for words starting at
    `starts` (frames) with `boxes` (cx, cy, w, h). Each word's box gets
    there SLIDE seconds after the word starts (a jump: one frame), a pop
    bigger, and settles back to size; the first comes in growing. Before the
    first word it's not there."""
    pop = POPS[options["pop"]]
    slide = max(1, round(SLIDE * fps)) if options["move"] == "slide" else 1
    settle = max(1, round(SETTLE * fps))
    last = max(1, frames - 1)
    tracks = {c: _Track() for c in ("x", "y", "w", "h", "level")}
    if not starts:
        return {c: _eased([(0, 0.0)]) for c in tracks}
    first = min(max(0, int(round(starts[0]))), last)
    cx, cy, w, h = boxes[0]
    for c, v in (("x", cx), ("y", cy), ("w", w * ENTER), ("h", h * ENTER), ("level", 0.0)):
        tracks[c].add(0, v)
        if first > 0:
            tracks[c].add(first, v)
    for i, (start, (cx, cy, w, h)) in enumerate(zip(starts, boxes)):
        at = min(max(0, int(round(start))), last)
        nxt = min(max(0, int(round(starts[i + 1]))), last) if i + 1 < len(starts) else last + 1
        room = max(1, nxt - at)
        move = min(slide, max(1, room // 2)) if room > 1 else 1
        arrive = at + move
        # Holds where it was until the word starts, then goes.
        for c in tracks:
            if tracks[c].points and at > tracks[c].points[-1][0]:
                tracks[c].add(at, tracks[c].points[-1][1])
        peak = pop if pop > 1 and arrive < nxt else 1.0
        tracks["x"].add(arrive, cx)
        tracks["y"].add(arrive, cy)
        tracks["w"].add(arrive, w * peak)
        tracks["h"].add(arrive, h * peak)
        tracks["level"].add(min(at + 1, arrive) if i == 0 else arrive, 1.0)
        if peak > 1:
            back = min(arrive + settle, nxt - 1)
            if back > arrive:
                tracks["w"].add(back, w)
                tracks["h"].add(back, h)
    return {c: _eased([(f, v) for f, v in t.points if f <= last] or [(0, t.points[0][1])])
            for c, t in tracks.items()}


def pop_keys(fps, options):
    """The word's own pop, for the Follower to play on each word at its own
    start: its size, 1 -> the pop -> 1, or None with Pop off."""
    pop = POPS[options["pop"]]
    if pop <= 1:
        return None
    slide = max(1, round(SLIDE * fps)) if options["move"] == "slide" else 1
    settle = max(1, round(SETTLE * fps))
    return _eased([(0, 1.0), (slide, pop), (slide + settle, 1.0)])


def delays(text, starts):
    """Each character's delay for the Follower's Manual Curve: its word's
    start (frames); spaces and line breaks take the next word's, as units.py
    gives them."""
    marks = un.unit_of(text, un.WORDS)
    if not starts:
        return [0.0] * len(marks)
    return [float(starts[min(m, len(starts) - 1)]) for m in marks]
