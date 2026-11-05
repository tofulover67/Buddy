#!/usr/bin/env python3
"""
Text Edit's transcript model - pure: no Qt, no Resolve.

The transcript is kept per SOURCE (a file, or some channels of one) and laid
onto the timeline afresh every time the timeline is read, from where each
clip sits now: a trim, a slip, a move or a cut made in Resolve shows in the
text at the next read, with nothing to re-transcribe.

    timeline   what resolve_ext.read_timeline() gives: fps, start (frames),
               tracks {"audio": [{index, name, locked}], "video": [...]},
               items [{kind, track, uid, name, path, channels, start, end
               (frames, absolute, end exclusive), offset (seconds into the
               source where the clip starts), locked}]
    words      collect_words(): every word heard on the chosen tracks, in
               seconds from the timeline's start - {s, e, t, tr, src, idx,
               p, key, lang}
    tokens     build_tokens(): the words, and the pauses between them,
               tiling the dialogue from end to end - so striking any run of
               tokens is a stretch of time to cut, and nothing is left over
    cuts       cut_frames(): struck tokens as [first, end) frame ranges

Words carry stable keys (their source and index there), so what's struck
survives a re-read of the timeline - the same word is struck wherever its
clip has moved to.

More than one mic: each mic hears everyone, its own person loudest. Each
word is checked against the levels of every mic at that moment (the
worker's --levels, every 50 ms) and dropped where another mic was clearly
the one being spoken into - so every person's words come from their own mic,
once, and a person's turn reads as one paragraph. levels.py has the maths.
"""

import bisect
import hashlib
import re

import numpy as np

# A word next to a long pause keeps this much air either side of it: striking
# the pause leaves 2 x PAD of it - a breath, not a jump cut.
PAD = 0.12
PAUSE_MIN = 0.5             # a gap this long or longer is a pause token of its own
PAUSE_REMOVE_MIN = 0.5      # "Remove pauses over" goes no lower
PARA_PAUSE = 2.0            # one person talking: a new paragraph after a pause this long
PARA_SOFT_WORDS = 90        # ... or after a sentence, once a paragraph has this many words
LOW_CONFIDENCE = 0.5
DUP_WINDOW = 0.7            # seconds: the same word on two mics this close is one word heard twice

# Hesitations, per language: what "Remove filler words" takes out. Only
# sounds that are never words of their own in that language ("er" is German
# for "he", so German doesn't get it).
FILLERS = {
    "en": {"um", "uh", "uhm", "umm", "uhh", "er", "erm", "ah", "hmm", "hm", "mm", "mhm", "mmm"},
    "de": {"äh", "ähm", "öh", "öhm", "hm", "hmm", "mhm"},
    "fr": {"euh", "heu", "hum", "hmm", "bah"},
    "es": {"eh", "ehm", "em", "mmm", "hmm"},
    "it": {"ehm", "eh", "uhm", "mmm", "hmm"},
    "nl": {"eh", "ehm", "uh", "uhm", "hmm"},
    "pt": {"hum", "éh", "ãh", "hmm"},
    "ja": {"えー", "えっと", "あのー", "うーん", "えーと"},
}
ANY_FILLERS = {"hmm", "mmm", "mhm"}


def normalise(text):
    """A word as compared - lower case, letters/digits/apostrophes only."""
    return re.sub(r"[^\w']+", "", str(text or "").lower())


def is_filler(text, language=""):
    word = normalise(text)
    if not word:
        return False
    fillers = FILLERS.get((language or "").split("-")[0].lower())
    if fillers is None:
        fillers = FILLERS["en"] if not language else ANY_FILLERS
    return word in fillers


def source_key(path, channels=None):
    """What a transcript is kept under: the file, and the channels heard."""
    chans = ".".join(str(int(c)) for c in channels) if channels else ""
    return f"{path}#{chans}" if chans else str(path)


def short_id(text):
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:10]


def default_tracks(timeline):
    """(audio, video) track numbers chosen when the person hasn't: every
    audio track with media on it; every video track holding the same media
    as that audio (the cameras), plus V1 when it has media."""
    items = timeline.get("items") or []
    audio = sorted({i["track"] for i in items if i["kind"] == "audio" and not i.get("locked") and i.get("path")})
    paths = {i["path"] for i in items if i["kind"] == "audio" and i["track"] in audio and i.get("path")}
    video = {i["track"] for i in items if i["kind"] == "video" and i.get("path") in paths}
    if any(i["kind"] == "video" and i["track"] == 1 and i.get("path") for i in items):
        video.add(1)
    return audio or [1], sorted(video)


def clip_window(item, timeline):
    """(source start s, source end s, timeline start s) of a clip."""
    fps = float(timeline["fps"])
    a = float(item.get("offset") or 0.0)
    b = a + (int(item["end"]) - int(item["start"])) / fps
    return a, b, (int(item["start"]) - int(timeline["start"])) / fps


def collect_words(timeline, tracks, transcripts):
    """Every word heard on audio `tracks`, placed on the timeline - sorted
    by start, then track. transcripts: {source_key: {"language", "words":
    [{s, e, w, p}], "overrides": {index: text}}}."""
    words, seen = [], {}
    for item in timeline.get("items") or []:
        if item["kind"] != "audio" or item["track"] not in tracks or item.get("locked") or not item.get("path"):
            continue
        src = source_key(item["path"], item.get("channels"))
        data = transcripts.get(src)
        if not data:
            continue
        a, b, rec0 = clip_window(item, timeline)
        overrides = data.get("overrides") or {}
        language = data.get("language") or ""
        sid = short_id(src)
        for idx, w in enumerate(data.get("words") or []):
            s, e = float(w["s"]), float(w["e"])
            mid = (s + e) / 2.0
            if not a <= mid < b:
                continue
            key = f"{sid}:{idx}"
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:               # the same clip twice on the timeline
                key = f"{key}#{seen[key]}"
            text = overrides.get(str(idx), w.get("w", ""))
            words.append({"s": rec0 + max(s, a) - a, "e": rec0 + min(e, b) - a, "t": str(text).strip(),
                          "tr": item["track"], "src": src, "idx": idx, "p": w.get("p"),
                          "key": key, "lang": language, "fixed": str(idx) in overrides})
    words = [w for w in words if w["t"] and w["e"] > w["s"]]
    words.sort(key=lambda w: (w["s"], w["tr"]))
    return words


def managed_span(timeline, tracks):
    """[first, last] seconds the chosen audio tracks cover."""
    fps, start = float(timeline["fps"]), int(timeline["start"])
    spans = [(int(i["start"]) - start, int(i["end"]) - start) for i in timeline.get("items") or []
             if i["kind"] == "audio" and i["track"] in tracks]
    if not spans:
        return 0.0, 0.0
    return min(s for s, _ in spans) / fps, max(e for _, e in spans) / fps


def build_tokens(words, span, pause_min=PAUSE_MIN):
    """The words and the pauses between them, tiling `span` (seconds) end to
    end: a word's token is the word and the short silence before it (cutting
    a word takes the breath before it too); a gap of pause_min or longer is a
    pause token of its own, the words either side keeping PAD of air."""
    a, b = span
    tokens = []

    def add(kind, s, e, word=None, key=""):
        tok = {"id": len(tokens), "k": kind, "s": round(s, 4), "e": round(max(e, s), 4)}
        if word is not None:
            tok.update(t=word["t"], tr=word["tr"], p=word.get("p"), key=word["key"],
                       f=is_filler(word["t"], word.get("lang")), lang=word.get("lang", ""),
                       fixed=bool(word.get("fixed")))
        else:
            tok["key"] = key
        tokens.append(tok)

    if not words:
        if b > a:
            add("p", a, b, key="p|^|$")
        return tokens
    cursor = a
    prev_key = "^"
    for i, w in enumerate(words):
        s = max(w["s"], cursor)
        gap = s - cursor
        if gap >= pause_min:
            if i == 0:
                add("p", cursor, s - PAD, key=f"p|{prev_key}|{w['key']}")
                cursor = s - PAD
            else:
                # The previous word runs PAD past its end.
                tokens[-1]["e"] = round(cursor + PAD, 4)
                add("p", cursor + PAD, s - PAD, key=f"p|{prev_key}|{w['key']}")
                cursor = s - PAD
        nxt = words[i + 1]["s"] if i + 1 < len(words) else b
        end = max(w["e"], cursor)
        if i + 1 < len(words):
            end = min(end, max(nxt, cursor))      # an overlapping next word starts where it starts
        add("w", cursor, end, word=w)
        cursor = end
        prev_key = w["key"]
    gap = b - cursor
    if gap >= pause_min:
        tokens[-1]["e"] = round(cursor + PAD, 4)
        add("p", cursor + PAD, b, key=f"p|{prev_key}|$")
    elif gap > 0:
        tokens[-1]["e"] = round(b, 4)
    # Keys must be unique (a pause between the same two words can't repeat,
    # but be safe): a repeat gets a suffix.
    seen = {}
    for tok in tokens:
        n = seen.get(tok["key"], 0)
        seen[tok["key"]] = n + 1
        if n:
            tok["key"] = f"{tok['key']}~{n}"
    return tokens


def paragraphs(tokens, multi):
    """[(first token id, last token id, track)] - a turn per person when
    several mics are heard (multi); otherwise a new paragraph after a long
    pause, or after a sentence once a paragraph runs long."""
    out = []
    first, track, count = None, None, 0

    def track_of(i):
        for tok in tokens[i:i + 6]:
            if tok["k"] == "w":
                return tok["tr"]
        for tok in reversed(tokens[:i]):
            if tok["k"] == "w":
                return tok["tr"]
        return None

    for i, tok in enumerate(tokens):
        if first is None:
            first, track, count = i, track_of(i), 0
            if tok["k"] == "w":
                count = 1
            continue
        if multi:
            if tok["k"] == "w" and tok["tr"] != track:
                out.append((first, i - 1, track))
                first, track, count = i, tok["tr"], 1
                continue
        else:
            prev = tokens[i - 1]
            long_pause = prev["k"] == "p" and prev["e"] - prev["s"] >= PARA_PAUSE
            sentence = (prev["k"] == "w" and prev["t"][-1:] in ".?!" and count >= PARA_SOFT_WORDS)
            if long_pause or sentence:
                out.append((first, i - 1, track))
                first, track, count = i, track_of(i), 0
        if tok["k"] == "w":
            count += 1
    if first is not None:
        out.append((first, len(tokens) - 1, track))
    return out


def edit_points(timeline, tracks, tokens):
    """Token ids a clip boundary on the dialogue tracks falls before - the
    view's thin ticks."""
    if not tokens:
        return []
    fps, start = float(timeline["fps"]), int(timeline["start"])
    starts = [t["s"] for t in tokens]
    first = min((int(i["start"]) for i in timeline.get("items") or []
                 if i["kind"] == "audio" and i["track"] in tracks), default=None)
    out = set()
    for item in timeline.get("items") or []:
        if item["kind"] != "audio" or item["track"] not in tracks or int(item["start"]) == first:
            continue
        at = (int(item["start"]) - start) / fps
        i = bisect.bisect_left(starts, at - 1e-6)
        if 0 < i < len(tokens):
            out.add(tokens[i]["id"])
    return sorted(out)


def cut_frames(tokens, struck, fps):
    """The struck tokens as [first, end) frame ranges from the timeline's
    start, merged where they touch. struck: a set of token keys."""
    spans = []
    for tok in tokens:
        if tok["key"] in struck:
            first, end = int(round(tok["s"] * fps)), int(round(tok["e"] * fps))
            if end > first:
                spans.append([first, end])
    spans.sort()
    merged = []
    for first, end in spans:
        if merged and first <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([first, end])
    return [tuple(s) for s in merged]


def cut_seconds(cuts, fps):
    return sum(e - s for s, e in cuts) / float(fps)


def fillers(tokens):
    return [t["key"] for t in tokens if t["k"] == "w" and t.get("f")]


def pauses_over(tokens, seconds):
    seconds = max(PAUSE_REMOVE_MIN, float(seconds))
    return [t["key"] for t in tokens if t["k"] == "p" and t["e"] - t["s"] >= seconds - 1e-6]


def find(tokens, query):
    """[[token ids]] of every place the phrase `query` is said - every word
    whole but the last, which may be the start of one; pauses between are
    skipped."""
    wanted = [normalise(q) for q in str(query or "").split()]
    wanted = [q for q in wanted if q]
    if not wanted:
        return []
    words = [t for t in tokens if t["k"] == "w"]
    norm = [normalise(t["t"]) for t in words]
    hits = []
    for i in range(len(words) - len(wanted) + 1):
        if all(norm[i + j] == q for j, q in enumerate(wanted[:-1])) and norm[i + len(wanted) - 1].startswith(wanted[-1]):
            first, last = words[i]["id"], words[i + len(wanted) - 1]["id"]
            hits.append([first, last])
    return hits


# ----------------------------------------------------------------- live cuts --
#
# "Cut the open timeline live" checks each cut against what a ripple delete
# of exactly that range should leave: every clip on the cut tracks, start,
# end and where in its source it starts.

def snapshot(timeline, audio, video):
    """{(kind, track): [(start, end, offset frames), ...]} of the cut tracks,
    in timeline order - offset in timeline frames, so a slipped clip shows."""
    fps = float(timeline["fps"])
    out = {("audio", t): [] for t in audio}
    out.update({("video", t): [] for t in video})
    for item in timeline.get("items") or []:
        key = (item["kind"], item["track"])
        if key in out:
            out[key].append((int(item["start"]), int(item["end"]),
                             int(round(float(item.get("offset") or 0.0) * fps))))
    for key in out:
        out[key].sort()
    return out


def rippled(snap, first, end):
    """What `snap` should be after a ripple delete of [first, end) absolute
    frames: what's inside goes, what's after moves left by its length, a clip
    across the range is split in two (the second half starting later in its
    source)."""
    length = end - first
    out = {}
    for key, clips in snap.items():
        rows = []
        for s, e, off in clips:
            if e <= first:
                rows.append((s, e, off))
            elif s >= end:
                rows.append((s - length, e - length, off))
            else:
                if s < first:
                    rows.append((s, first, off))
                if e > end:
                    rows.append((first, e - length, off + (max(end, s) - s)))
        out[key] = sorted(rows)
    return out


def same_layout(a, b):
    """Two snapshots alike, a clip split in two halves that Resolve may keep
    as one (nothing removed between them) counted the same. A one-frame
    difference anywhere is not."""
    def joined(rows):
        out = []
        for s, e, off in rows:
            if out and out[-1][1] == s and out[-1][2] + (out[-1][1] - out[-1][0]) == off:
                out[-1] = (out[-1][0], e, out[-1][2])
            else:
                out.append((s, e, off))
        return out
    if set(a) != set(b):
        return False
    return all(joined(a[k]) == joined(b[k]) for k in a)


def layout_problem(expected, actual, names):
    """Why a live cut didn't come out as expected, in words - or ""."""
    if same_layout(expected, actual):
        return ""
    wrong = [k for k in expected if k in actual and
             not same_layout({k: expected[k]}, {k: actual[k]})]
    if not wrong:
        return "Resolve made a different cut than expected."
    unchanged = [k for k in wrong if k in names.get("before", {}) and
                 same_layout({k: names["before"][k]}, {k: actual[k]})]
    label = ", ".join(f"{'A' if k[0] == 'audio' else 'V'}{k[1]}" for k in (unchanged or wrong)[:4])
    if unchanged and len(unchanged) == len(expected):
        return "Resolve didn't make the cut."
    if unchanged:
        return f"Resolve cut some tracks but not {label} – turn on their Auto Track Selector."
    return f"Resolve cut {label} differently than expected."


# ---------------------------------------------------------- level envelopes --

def level_array(raw):
    """The worker's --levels {"hop", "db"} as (hop, float32 dBFS array)."""
    import base64
    try:
        hop = float(raw["hop"])
        data = np.frombuffer(base64.b64decode(raw["db"]), dtype=np.uint8).astype(np.float32) - 100.0
    except (KeyError, TypeError, ValueError):
        return None
    return (hop, data) if hop > 0 else None
