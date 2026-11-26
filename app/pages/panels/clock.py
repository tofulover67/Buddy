"""What happens in a panel on every frame: where the cursor is, which page is showing
and how it got there, how far each box is hovered and pressed, how far each page has
built on and each chart drawn - the one clock the preview in Buddy and the keys in
Resolve are both made from. Nothing in Resolve is live: clock() plays the panel
through once, here, and pose() says how any frame of that looks. No Qt, no Resolve.

The rules:

- The cursor answers only the page showing (the one being arrived at, once a change
  has begun), and on it only the topmost reacting box under its hot spot.
- A box's hover is the share of the last "hover_seconds" of frames the cursor was on
  it, eased - so a cursor that leaves part-way runs it back from where it had got to.
  Its press is the same over "press_seconds", with the button down - on the box it
  went down on, while the cursor stays on it: a button held as the page changes goes
  on pressing the box that was clicked, and presses nothing on the new page.
- A click lands on the topmost box under the hot spot that answers or has a "go" - a
  linked box takes its click whether or not it answers (one that doesn't shows no hover
  or press).
- The frame the button goes down on a box with a "go", the panel starts for that page.
  A rule fires once: the first frame its chart or progress bar, on the page showing,
  is drawn further (as %) than the rule's number. "page_keys" start a change at their
  time. A change asked for while another is running starts when that one ends, in
  the order they were asked; one to the page already showing is nothing.
- With "build" on, a page's boxes arrive in reading order (top to bottom, then left
  to right), each "stagger" after the one before - less, on a page of many boxes, so
  the last has started within BUILD_MOST seconds - each time the page arrives. Its
  charts and progress bars draw on from when they arrive until the page's build ends.
- A box carries what is in it. pose() gives each box its LOCAL pose; carried() puts a
  box's together with those of everything it is in: a box's alpha, move (dx, dy) and
  size (scale, sx, sy) apply to it and to all inside it, about its own middle, and what's
  inside adds its own on top - alphas multiply, sizes multiply (each about its own
  middle), moves add. "glow" and "own" (whether its own prims show: 0 for a box whose
  pair glides on the other page in a match, while what's in it still fades) are its own.
  So a hovered card grows and lifts with its words in it. What is a box's own to begin
  with - its arrival, its glide in a match, its fade - is worked out against what it is
  in, so that carried it comes out as its own: a box arriving inside an arriving one
  fades in once and rises RISE px, not twice that; it is never more seen than what it's in.
- A box with its own "arrive" arrives its way in its place in the build ("rise" as the
  build does, "fade" without the rise, "pop" from POP of its size, overshooting a little;
  "none": it has no arrival of its own) - and arrives even on a page that doesn't build on.
- A box that types ("type") shows its letters one after another, `speed` a second, the
  first on the frame it starts: "page" - when the box has arrived, each time its page
  arrives; "click" - on the frame of the first click (button down) inside it on that
  arrival of its page, whether or not it answers the cursor. Its caret (a thin upright
  bar after the last letter shown, caret()) stands from then until the last letter and
  for CARET_HOLD seconds after, blinking CARET_BLINK times a second (on for the first
  half of each blink).
- A box with a "run" - a progress bar or a chart drawing on, a text counting up - starts
  on its own terms and goes at a steady rate over its seconds: "by" "" from when the box
  has arrived (a drawn one: from when its draw-on would start), each time its page
  arrives; "by" a box, from the frame of the first click (button down) inside that box
  on that arrival of the page - until then a bar is empty and a count says nothing yet
  (its number at 0). A count shows model.counted(text, share), stepped once a frame.
"""

from pages.data_graphics import easing
from pages.interactions import model as interactions

from . import model, solve

FRAME = solve.FRAME
BUILD_MOST = 1.5            # seconds within which a page's last box has started to arrive
RISE = 8.0                  # panel px a box rises through as it arrives
SLIDE = 0.2                 # how far a page slides in a "slide", of the window's width - it fades as it goes
REST = {"alpha": 1.0, "dx": 0.0, "dy": 0.0, "scale": 1.0, "sx": 1.0, "sy": 1.0, "glow": 0.0, "own": 1.0}
ARRIVES = easing.CURVES["ease_out"]
MOVES = easing.CURVES["ease_in_out"]
POPS = easing.CURVES["overshoot"]
POP = 0.85                  # a box that pops in starts this share of its size
CARET_HOLD = 1.5            # seconds a caret stays after the last letter, blinking
CARET_BLINK = 1.0           # its blinks a second
CARET_W = 0.08              # its width, of the letter size (1 px at least)
CARET_UP, CARET_DOWN = 0.9, 0.2     # how far it reaches above and below the baseline, of the letter size

_eases = {}


def _eased(share, curve=MOVES):
    """easing.ease(), remembered: the shares asked for are few (so many frames of so many)."""
    share = easing.clamp(share)
    if share in (0.0, 1.0):
        return share
    key = (id(curve), round(share, 6))
    if key not in _eases:
        if len(_eases) > 20000:
            _eases.clear()
        _eases[key] = round(easing.clamp(easing.ease(curve, share)), 6)
    return _eases[key]


def _popped(share):
    """A popping box's size, share of the way through its arrival: POP to 1, past it a little."""
    share = easing.clamp(share)
    if share in (0.0, 1.0):
        return POP + (1.0 - POP) * share
    key = ("pop", round(share, 6))
    if key not in _eases:
        _eases[key] = round(POP + (1.0 - POP) * easing.ease(POPS, share), 6)
    return _eases[key]


_known = []                 # [the panel last asked about, what was worked out for it]


def _facts(spec):
    """What the clock needs of a panel, worked out once for the same panel: every page
    solved, which page each box is on, and each box's own states."""
    if _known and _known[0] is spec:
        return _known[1]
    solved = solve.solve_all(spec)
    facts = {"solved": solved, "home": {b["id"]: pid for pid, page in solved.items() for b in page["boxes"]},
             "states": {box["id"]: box["states"] for box in model.boxes(spec)}, "pairs": {}, "plans": {},
             "arrive": {box["id"]: box["arrive"]["kind"] for box in model.boxes(spec) if box["arrive"]},
             "typers": {b["id"]: b for page in solved.values() for b in page["boxes"] if b.get("type")}}
    _known[:] = [spec, facts]
    return facts


def frames(spec, fps):
    """The clip's frames."""
    return model.frames(spec, fps)


# ---------------------------------------------------------------- build-on --

def _plan(spec, page, fps, kinds=None):
    """A page's build: {"boxes": {id: [start, frames]}, "draws": {id: [start, frames]},
    "span": the frames the whole build takes} - frames counted from when the page arrives.
    kinds: {box id: its own arrival}; a box whose own is "none" has no place in the
    build, and with the build off a box with its own arrives from the page's start."""
    build, kinds = spec["build"], kinds or {}
    drawn_on = [b["id"] for b in page["boxes"] if b["role"] in ("chart", "progress")]
    length = max(1, int(round(build["seconds"] * fps)))
    if not build["on"]:
        own = {b["id"]: [0, length] for b in page["boxes"] if kinds.get(b["id"], "none") != "none"}
        return {"boxes": own, "draws": {i: [0, 0] for i in drawn_on}, "span": length if own else 0}
    order = sorted((b for b in page["boxes"] if kinds.get(b["id"]) != "none"), key=lambda b: (b["y"], b["x"]))
    # (a parent is never under or right of its child)
    step = min(build["stagger"], BUILD_MOST / max(1, len(order) - 1))
    starts = {b["id"]: [int(round(i * step * fps)), length] for i, b in enumerate(order)}
    span = max((start for start, _n in starts.values()), default=0) + length
    at = {i: starts[i][0] if i in starts else 0 for i in drawn_on}
    return {"boxes": starts, "span": span, "draws": {i: [at[i], max(2 * length, span - at[i])] for i in drawn_on}}


def _share(plan, box_id, since):
    """How much of a chart or a progress bar is drawn, `since` frames after its page arrived."""
    start, length = plan["draws"][box_id]
    return 1.0 if length <= 0 else _eased((since - start) / length, ARRIVES)


def _window(on, n):
    """The share of the last n frames that were on, on each frame - a frame before the
    first counts as the first."""
    out, run = [], n * on[0]
    for f in range(len(on)):
        if f:
            run += on[f] - on[max(0, f - n)]
        out.append(run / n)
    return out


# --------------------------------------------------------------- the clock --

def clock(spec, fps, frame=FRAME):
    """The panel played through, frame by frame (see CONTRACT.md / this module's rules):
    {"total", "cursor", "down", "pages", "amounts", "build", "draw", "events", "typed",
    "caret"}.
    frame: the timeline's (width, height) - the cursor's path is in % of it."""
    total = frames(spec, fps)
    facts = _facts(spec)
    solved = facts["solved"]
    height = solved[spec["start"]]["h"]
    where = [solve.to_panel(spec, height, x, y, frame) for x, y in interactions.cursor(spec, fps, total)]
    held = interactions.down(spec, fps, total)
    links = {box["id"]: box["go"] for box in model.boxes(spec) if box["go"]}
    hot = {pid: [(b["id"], b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]) for b in reversed(page["boxes"]) if b["reacts"]]
           for pid, page in solved.items()}
    # What a click lands on: the topmost box that answers or has a link - a linked box takes
    # its click (and changes the page) whether or not it answers the cursor.
    clickable = {pid: [(b["id"], b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]) for b in reversed(page["boxes"])
                       if b["reacts"] or b["id"] in links] for pid, page in solved.items()}
    plans = {pid: _plan(spec, page, fps, facts["arrive"]) for pid, page in solved.items()}
    # What a click starts typing in: boxes that type from a click, whether or not they answer.
    typed_by_click = {pid: [(b["id"], b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]) for b in page["boxes"]
                            if b["id"] in facts["typers"] and b["type"]["start"] == "click"] for pid, page in solved.items()}
    clicked_in = []                         # [(frame, box id)]: clicks on boxes that type from one
    # What runs on its own terms (a "run"): where each begins after its page arrives, and the
    # boxes whose click starts one.
    runs = {b["id"]: b["run"] for page in solved.values() for b in page["boxes"] if b.get("run")}
    run_frames = {bid: max(1, int(round(run["seconds"] * fps))) for bid, run in runs.items()}
    counters = {b["id"]: b["text"] for page in solved.values() for b in page["boxes"] if b.get("count") and b["id"] in runs}
    run_here = {pid: [b["id"] for b in page["boxes"] if b["id"] in runs] for pid, page in solved.items()}
    starters = {pid: [(b["id"], b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]) for b in page["boxes"]
                      if any(runs[r]["by"] == b["id"] for r in run_here[pid])] for pid, page in solved.items()}
    run_rows = {bid: [None] * total for bid in runs}
    began = {}                              # {box id: the frame its run begins on}, of the page showing

    def arrive(page_id, f):
        for bid in run_here[page_id]:
            began.pop(bid, None)
            if not runs[bid]["by"]:
                plan = plans[page_id]
                began[bid] = f + (plan["draws"][bid][0] if bid in plan["draws"] else sum(plan["boxes"].get(bid, (0, 0))))

    def run_share(bid, f):
        return min(run_frames[bid], max(0, f - began[bid])) / run_frames[bid] if bid in began else 0.0
    go_frames = max(0, int(round(spec["go_seconds"] * fps)))
    keys = [(min(total - 1, int(round(t * fps))), page, how) for t, page, how in spec["page_keys"]]
    rules = [rule for rule in spec["rules"] if rule["box"] in facts["home"]]

    changes = [{"at": 0, "page": spec["start"], "from": "", "with": "cut", "frames": 0, "why": "start", "box": ""}]
    events, waiting, fired = [], [], set()
    showing, arrived, busy, next_key = spec["start"], 0, 0, 0
    over, pressed = [""] * total, [""] * total
    was_over, was_pressed, grabbed = "", "", None
    arrive(showing, 0)
    for f in range(total):
        while next_key < len(keys) and keys[next_key][0] <= f:
            waiting.append(("key", keys[next_key][1], keys[next_key][2], ""))
            next_key += 1
        for rule in rules:
            if rule["id"] not in fired and facts["home"][rule["box"]] == showing \
                    and (run_share(rule["box"], f) if rule["box"] in runs
                         else _share(plans[showing], rule["box"], f - arrived)) * 100.0 > rule["over"]:
                fired.add(rule["id"])
                events.append({"frame": f, "kind": "rule", "box": rule["box"], "page": rule["page"]})
                waiting.append(("rule", rule["page"], rule["with"], rule["box"]))
        x, y = where[f]
        under = next((box for box in hot[showing] if box[1] <= x < box[3] and box[2] <= y < box[4]), None)
        hit = under[0] if under else ""
        over[f] = hit
        if hit != was_over:
            if was_over:
                events.append({"frame": f, "kind": "out", "box": was_over, "page": facts["home"][was_over]})
            if hit:
                events.append({"frame": f, "kind": "over", "box": hit, "page": showing})
            was_over = hit
        down_now = held[f] and (f == 0 or not held[f - 1])
        if down_now or not held[f]:
            grabbed = under if down_now else None       # a press is the box the button went down on, while the cursor stays on it
        now_pressed = grabbed[0] if grabbed and grabbed[1] <= x < grabbed[3] and grabbed[2] <= y < grabbed[4] else ""
        pressed[f] = now_pressed
        if now_pressed != was_pressed:
            if was_pressed:
                events.append({"frame": f, "kind": "release", "box": was_pressed, "page": facts["home"][was_pressed]})
            if now_pressed:
                events.append({"frame": f, "kind": "press", "box": now_pressed, "page": facts["home"][now_pressed]})
            was_pressed = now_pressed
        if down_now:
            target = next((box[0] for box in clickable[showing] if box[1] <= x < box[3] and box[2] <= y < box[4]), "")
            if target in links:
                waiting.append(("click", links[target]["page"], links[target]["with"] or spec["go_with"], target))
            clicked_in += [(f, box[0]) for box in typed_by_click[showing] if box[1] <= x < box[3] and box[2] <= y < box[4]]
            for box in starters[showing]:
                if box[1] <= x < box[3] and box[2] <= y < box[4]:
                    for bid in run_here[showing]:
                        if runs[bid]["by"] == box[0]:
                            began.setdefault(bid, f)
        for bid in run_here[showing]:
            run_rows[bid][f] = min(run_frames[bid], max(0, f - began[bid])) if bid in began else 0
            if began.get(bid) == f:
                events.append({"frame": f, "kind": "run", "box": bid, "page": showing})
        while waiting and f >= busy:
            why, page, how, box = waiting.pop(0)
            if page == showing:
                continue
            changes.append({"at": f, "page": page, "from": showing, "with": how, "frames": go_frames, "why": why, "box": box})
            events.append({"frame": f, "kind": "page", "box": box, "page": page})
            showing, arrived, busy = page, f, f + go_frames
            was_over = ""                       # the page under the cursor is another: its boxes start afresh
            arrive(page, f)
            break

    n_hover = interactions.look_back(spec["answers"]["hover_seconds"], fps)
    n_press = interactions.look_back(spec["answers"]["press_seconds"], fps)
    amounts = {}
    for box_id in sorted(set(over) - {""}, key=lambda i: int(i[1:])):
        amounts[box_id] = {"hover": [_eased(share) for share in _window([hit == box_id for hit in over], n_hover)],
                           "press": [_eased(share) for share in _window([hit == box_id for hit in pressed], n_press)]}

    draw = {}
    for i, change in enumerate(changes):
        plan = plans[change["page"]]
        again = next((later["at"] for later in changes[i + 1:] if later["page"] == change["page"]), total)
        for box_id, (start, length) in plan["draws"].items():
            row = draw.setdefault(box_id, [0.0] * total)
            done = min(again, change["at"] + start + length)
            for f in range(change["at"], done):
                row[f] = _share(plan, box_id, f - change["at"])
            row[done:again] = [1.0] * (again - done)

    # What runs on its own terms: how far along each is on every frame (held as it was
    # while its page is leaving or away), in place of the build's draw-on - and what a
    # count says at each step.
    said = {}
    for bid, row in run_rows.items():
        last = 0
        for f, at in enumerate(row):
            if at is None:
                row[f] = last
            else:
                last = at
        n = run_frames[bid]
        if bid in draw:
            draw[bid] = [at / n for at in row]
        if bid in counters:
            said[bid] = {"steps": [model.counted(counters[bid], i / n) for i in range(n + 1)], "at": row}

    typed, carets = _typing(facts, changes, plans, clicked_in, fps, total, events)
    events.sort(key=lambda event: event["frame"])
    return {"total": total,
            "cursor": [[round(x, 2), round(y, 2)] for x, y in where],
            "down": [1 if d else 0 for d in held],
            "pages": changes, "amounts": amounts,
            "build": {pid: plan["boxes"] for pid, plan in plans.items()},
            "draw": draw, "events": events, "typed": typed, "caret": carets, "said": said}


def _typing(facts, changes, plans, clicked_in, fps, total, events):
    """({box id: [letters shown per frame]}, {box id: [its caret's [x, y, w, h], or None,
    per frame]}) of every box that types (the caret's only for a box with one), and a
    "type" event where each starts. Each arrival of a box's page types it afresh."""
    typed, carets = {}, {}
    hold = int(round(CARET_HOLD * fps))
    blink = fps / CARET_BLINK if CARET_BLINK > 0 else 0.0
    for box_id, placed in facts["typers"].items():
        home, how = facts["home"][box_id], placed["type"]
        n = sum(len(line) for line in placed["type_lines"])
        row, marks = [0] * total, [None] * total if how["caret"] else None
        solved = facts["solved"][home]
        rects = {}
        for i, change in enumerate(changes):
            if change["page"] != home:
                continue
            at = change["at"]
            again = next((later["at"] for later in changes[i + 1:] if later["page"] == home), total)
            if how["start"] == "page":
                start, length = plans[home]["boxes"].get(box_id, (0, 0))
                begins = at + start + length
            else:
                begins = next((f for f, clicked in clicked_in if clicked == box_id and at <= f < again), None)
            if begins is None or begins >= again:
                continue
            events.append({"frame": begins, "kind": "type", "box": box_id, "page": home})
            done = None                     # the frame its last letter shows on
            for f in range(begins, again):
                shown = min(n, 1 + int((f - begins) * how["speed"] / fps + 1e-9))
                row[f] = shown
                if done is None and shown >= n:
                    done = f
                if marks is None:
                    continue
                if done is not None and f > done and (f - done > hold or (blink and (f - done) % blink >= blink / 2)):
                    continue
                if shown not in rects:
                    rects[shown] = caret(solved, box_id, shown)
                marks[f] = rects[shown]
        typed[box_id] = row
        if marks is not None:
            carets[box_id] = marks
    return typed, carets


def caret(solved_page, box_id, shown):
    """The caret of a box that types, after its `shown`th letter: [x, y, w, h] in panel
    px, by the letter-width rule the solver measures with (solve.typing) - or None for a
    box that isn't on the page, doesn't type or has no caret."""
    placed = next((b for b in solved_page["boxes"] if b["id"] == box_id), None)
    if placed is None or not placed.get("type") or not placed["type"]["caret"]:
        return None
    lines = solve.typing(placed)
    if not lines:
        return None
    size, char_w = solve.letter(placed)
    shown = max(0, int(shown))
    # After the letter shown: on the line that holds it (the first line's start before any).
    line, first, _last, x, baseline = next((row for row in lines if row[1] < shown <= row[2]), lines[0] if shown <= 0 else lines[-1])
    along = min(len(line), max(0, shown - first))
    return [round(x + along * char_w, 2), round(baseline - CARET_UP * size, 2), round(max(1.0, CARET_W * size), 2),
            round((CARET_UP + CARET_DOWN) * size, 2)]


# ---------------------------------------------------------------- one frame --

def matched(spec, from_page, to_page):
    """{a box's id on to_page: the box on from_page it glides from} in a "match" change:
    boxes of the same role, name and words, the first such with the first, the second
    with the second."""
    facts = _facts(spec)
    key = (from_page, to_page)
    if key not in facts["pairs"]:
        waiting = {}
        for box in facts["solved"][from_page]["boxes"]:
            waiting.setdefault((box["role"], box["name"], box["text"]), []).append(box)
        pairs = {}
        for box in facts["solved"][to_page]["boxes"]:
            same = waiting.get((box["role"], box["name"], box["text"]))
            if same:
                pairs[box["id"]] = same.pop(0)
        facts["pairs"][key] = pairs
    return facts["pairs"][key]


def _span(clock_, page_id):
    return max((start + n for start, n in clock_["build"].get(page_id, {}).values()), default=0)


# A move and a size on the page as one map, x -> a * x + t on each side: (ax, ay, tx, ty).
_SAME = (1.0, 1.0, 0.0, 0.0)


def _about(mx, my, kx, ky, dx, dy):
    """Sized kx, ky about (mx, my), then moved dx, dy."""
    return (kx, ky, mx - kx * mx + dx, my - ky * my + dy)


def _then(f, g):
    """g, then f."""
    return (f[0] * g[0], f[1] * g[1], f[0] * g[2] + f[2], f[1] * g[3] + f[3])


def _undo(f):
    return (1.0 / f[0], 1.0 / f[1], -f[2] / f[0], -f[3] / f[1])


def _page_pose(spec, clock_, facts, page_id, f, since, fade, glides, gone):
    """The boxes of a page that aren't at rest on frame f, `since` frames after it
    arrived, each as its LOCAL pose - what it adds to what it's in (carried() puts them
    together). fade: how far every box but a gliding one has faded; glides: {id: its
    glide, from its own place}; gone: ids whose own prims don't show (their pair on the
    other page does) - what's in them still does.

    Each box's arrival, glide and fade are its own - worked out here against what it's
    in, so that carried they come out as the box's own (a box arriving inside one that is
    arriving rises 8 px, not 16, and fades in once). A hover or a press is a box's own
    answer and carries what is in it."""
    home, own, kinds = facts["home"], facts["states"], facts["arrive"]
    building = {}
    if since < _span(clock_, page_id):
        for box_id, (start, n) in clock_["build"][page_id].items():
            if since < start + n and box_id not in glides:
                there = _eased((since - start) / n, ARRIVES)
                kind = kinds.get(box_id, "rise")
                building[box_id] = (there, round((1.0 - there) * RISE, 4) if kind == "rise" else 0.0,
                                    _popped((since - start) / n) if kind == "pop" else 1.0)
    answering = {}
    for box_id, rows in clock_["amounts"].items():
        hover, press = rows["hover"][f], rows["press"][f]
        if home.get(box_id) != page_id or not (hover or press):
            continue
        answers = own.get(box_id) or spec["answers"]
        answering[box_id] = (
            round(1.0 + (answers["hover"]["grow"] * hover + answers["press"]["grow"] * press) / 100.0, 4),
            answers["hover"]["lift"] * hover + answers["press"]["lift"] * press,
            round(min(1.0, max(0.0, (answers["hover"]["glow"] * hover + answers["press"]["glow"] * press) / 100.0)), 4))
    gone = set(gone)
    out = {}
    # Each box's own alpha and arrival (its rise and pop, as a move and a size about its
    # middle); where its own arrival and glide put it.
    seen, base = {}, {}
    for box in facts["solved"][page_id]["boxes"]:
        box_id, parent = box["id"], box["parent"]
        mx, my = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
        there, rise, size = building.get(box_id, (1.0, 0.0, 1.0))
        arrival = _about(mx, my, size, size, 0.0, rise) if (rise or size != 1.0) else _SAME
        up_alpha, up_arrival = seen.get(parent, (1.0, _SAME))
        up_base = base.get(parent, _SAME)
        alpha = min(1.0, there / up_alpha) if up_alpha > 0 else 0.0
        if fade < 1.0 and box_id not in glides and (parent not in seen or parent in glides):
            alpha *= fade                   # fades once: in what it's in, unless that glides
        seen[box_id] = (there, arrival)
        glide = glides.get(box_id)
        if glide:
            local = _then(_undo(up_base), _about(mx, my, glide["sx"], glide["sy"], glide["dx"], glide["dy"]))
        elif up_arrival is _SAME:
            local = arrival
        else:
            local = _then(_undo(up_arrival), arrival)       # its own arrival, less what it's in already makes
        base[box_id] = _then(up_base, local)
        scale, lift, glow = answering.get(box_id, (1.0, 0.0, 0.0))
        if box_id in answering:
            local = _then(_about(mx, my, scale, scale, 0.0, -lift), local)
        look = {"alpha": round(alpha, 4), "dx": round(local[0] * mx + local[2] - mx, 4),
                "dy": round(local[1] * my + local[3] - my, 4), "scale": scale,
                "sx": round(local[0] / scale, 4), "sy": round(local[1] / scale, 4), "glow": glow,
                "own": 0.0 if box_id in gone else 1.0}
        if look != REST:
            out[box_id] = look
    return out


def carried(solved_page, boxes, box_id):
    """How a box is drawn, everything it is in put together: {"alpha", "dx", "dy",
    "scale", "sx", "sy"} about its own middle (as a pose is), "glow" and "own" its own.
    solved_page: solve()'s page; boxes: the pose's "boxes" for that page. The rule: a
    box's alpha, move and size apply to it and everything in it, about its own middle;
    what's in it adds its own on top - alphas multiply, sizes multiply (each about its
    own middle), moves add."""
    placed = {b["id"]: b for b in solved_page["boxes"]}
    chain, at = [], box_id
    while at in placed:
        chain.append(at)
        at = placed[at]["parent"]
    move, alpha, scale = _SAME, 1.0, 1.0
    for at in reversed(chain):
        b, look = placed[at], boxes.get(at) or REST
        mx, my = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        move = _then(move, _about(mx, my, look["scale"] * look["sx"], look["scale"] * look["sy"], look["dx"], look["dy"]))
        alpha *= look["alpha"]
        scale *= look["scale"]
    b, look = placed[box_id], boxes.get(box_id) or REST
    mx, my = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    return {"alpha": alpha, "dx": move[0] * mx + move[2] - mx, "dy": move[1] * my + move[3] - my, "scale": scale,
            "sx": move[0] / scale, "sy": move[1] / scale, "glow": look["glow"], "own": look.get("own", 1.0)}


def pose(spec, clock, frame):
    """How a frame looks: {"pages": [{"page", "alpha", "dx", "boxes": {id: {"alpha",
    "dx", "dy", "scale", "sx", "sy", "glow", "own"}}}], "cursor", "down", "draw",
    "typed", "caret"} - the pages that show, back to front (two while one is changing
    into another); "boxes" has only the boxes whose LOCAL pose isn't REST (carried()
    gives how one is drawn); "typed" {box id: letters shown} of every box that types on
    the pages that show; "caret" {box id: [x, y, w, h]} of the carets that show."""
    f = min(max(0, int(frame)), clock["total"] - 1)
    facts = _facts(spec)
    changes = clock["pages"]
    i = max(k for k, change in enumerate(changes) if change["at"] <= f)
    now = changes[i]
    since = f - now["at"]
    along = 1.0 if now["frames"] <= 0 else _eased(since / now["frames"])
    wide = facts["solved"][now["page"]]["w"]
    pages = []
    if now["from"] and along < 1.0:
        how, before = now["with"], f - changes[i - 1]["at"]
        if how == "match":
            pairs = matched(spec, now["from"], now["page"])
            glides = {}
            for box in facts["solved"][now["page"]]["boxes"]:
                old = pairs.get(box["id"])
                if old is None:
                    continue
                w = old["w"] + (box["w"] - old["w"]) * along
                h = old["h"] + (box["h"] - old["h"]) * along
                glides[box["id"]] = {
                    "alpha": 1.0,
                    "dx": round((old["x"] + old["w"] / 2) * (1 - along) + (box["x"] + box["w"] / 2) * along - box["x"] - box["w"] / 2, 4),
                    "dy": round((old["y"] + old["h"] / 2) * (1 - along) + (box["y"] + box["h"] / 2) * along - box["y"] - box["h"] / 2, 4),
                    "sx": round(w / box["w"], 4) if box["w"] > 0 else 1.0, "sy": round(h / box["h"], 4) if box["h"] > 0 else 1.0}
            pages.append({"page": now["from"], "alpha": 1.0, "dx": 0.0,
                          "boxes": _page_pose(spec, clock, facts, now["from"], f, before, 1.0 - along, {},
                                              [old["id"] for old in pairs.values()])})
            pages.append({"page": now["page"], "alpha": 1.0, "dx": 0.0,
                          "boxes": _page_pose(spec, clock, facts, now["page"], f, since, along, glides, ())})
        else:
            slide = SLIDE * wide if how == "slide" else 0.0
            pages.append({"page": now["from"], "alpha": round(1.0 - along, 4), "dx": round(-slide * along, 4),
                          "boxes": _page_pose(spec, clock, facts, now["from"], f, before, 1.0, {}, ())})
            pages.append({"page": now["page"], "alpha": round(along, 4), "dx": round(slide * (1.0 - along), 4),
                          "boxes": _page_pose(spec, clock, facts, now["page"], f, since, 1.0, {}, ())})
    else:
        pages.append({"page": now["page"], "alpha": 1.0, "dx": 0.0,
                      "boxes": _page_pose(spec, clock, facts, now["page"], f, since, 1.0, {}, ())})
    showing = {page["page"] for page in pages}
    home = facts["home"]
    return {"pages": pages, "cursor": list(clock["cursor"][f]), "down": clock["down"][f],
            "draw": {box_id: row[f] for box_id, row in clock["draw"].items() if home.get(box_id) in showing},
            "typed": {box_id: row[f] for box_id, row in clock.get("typed", {}).items() if home.get(box_id) in showing},
            # What each counting text says on the frame.
            "said": {box_id: row["steps"][row["at"][f]] for box_id, row in clock.get("said", {}).items()
                     if home.get(box_id) in showing},
            "caret": {box_id: row[f] for box_id, row in clock.get("caret", {}).items()
                      if row[f] is not None and home.get(box_id) in showing}}
