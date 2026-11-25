# Panels - the contract its modules are built to

What each of Panels' modules gives the others: the shapes the solver, the styles, the
clock, the view and the comp all agree on. Change a shape here and in the module together.

Panels makes a mock app window - built from ready-made layouts and components, in a
style - that has pages, boxes that answer a cursor, and a recorded cursor that drives
it. It goes to the timeline as a NEW Fusion clip with everything written as keys:
nothing in Resolve is live. The pieces:

    model.py      a panel as data, made valid (clean); the edits (with_*)
    solve.py      where every box of a page sits (the layout solver)
    styles.py     how each role is drawn in each style -> the display list
    catalogue.py  the kinds of window, the 22 layouts, the components
    clock.py      what happens on every frame: the cursor, the page, each box's
                  hover and press, the build-on - the one clock the preview and
                  the comp's keys are both made from
    comp.py       the Fusion comp's text, from the display lists and the clock
    place.py      the comp as a new clip at the playhead
    page.py       the WebToolPage (Data Graphics' GraphicPage)
    web/          the view

No module but page.py imports Qt. No module but place.py touches Resolve.

## Units

A panel is designed in **panel px**: the window is `width` panel px wide and as tall
as its chrome and its tallest page make it. On the frame it is drawn `zoom` times
`frame_width / 1920` larger, its middle at `x`, `y` (% of the frame, across and down).
Everything the solver and the display list say is in panel px from the window's top
left corner. `clock.py` turns the cursor's % of the frame into panel px.

## The spec (model.clean(raw) -> this, always whole and valid)

    {
      "kind": "app" | "browser" | "phone" | "dialog" | "widget",
      "style": "mac" | "plain" | "glass" | "windows" | "cartoon",
      "accent": "#rrggbb", "dark": true,
      "title": "Booking app",                # the window bar's text ("" = the page's name)
      "width": 620.0,                        # panel px, MIN_WIDTH..MAX_WIDTH (320..1600)
      "zoom": 1.6, "x": 50.0, "y": 50.0,
      "font": "Arial", "face": "Regular", "bold_face": "Bold",
      "pages": [{"id": "p1", "name": "Sign in", "layout": "signin", "root": <box>}],   # 1..MAX_PAGES (8)
      "start": "p1",                         # the page showing at 0 s
      "seconds": 6.0,                        # MIN_SECONDS..MAX_SECONDS (1..60)
      "path": [[t, x, y], ...],              # the cursor: seconds, % of the frame (as Interactions')
      "clicks": [[down, up], ...],           # seconds
      "cursor": "windows" | "mac" | "dot" | "none", "cursor_size": 5.0,
      "answers": {"hover": {"grow": 4.0, "lift": 2.0, "glow": 50.0},
                  "press": {"grow": -4.0, "lift": 0.0, "glow": 0.0},
                  "hover_seconds": 0.2, "press_seconds": 0.1},     # every reacting box's, unless it has its own
      "go_with": "slide", "go_seconds": 0.4,  # a page change's transition unless the link says
      "rules": [{"id": "r1", "box": "b12", "over": 60.0, "page": "p3", "with": "match"}],  # 0..MAX_RULES (8)
      "page_keys": [[t, "p2", "fade"], ...], # page changes keyed by hand, seconds
      "build": {"on": true, "stagger": 0.04, "seconds": 0.35},     # each page builds on as it arrives
    }

### A box

    {"id": "b7", "role": ROLE, "name": "Card",        # name: shown in the view; Match pairs boxes by it
     # layout
     "dir": "row" | "column", "gap": 8.0, "pad": [top, right, bottom, left],
     "align": "start" | "center" | "end" | "stretch",     # across the line
     "justify": "start" | "center" | "end",               # along it
     "wrap": false, "equal": 0.0,       # wrap: children that don't fit go to the next line;
                                        # equal > 0: equal columns each at least this wide, last line not stretched
     "w": "fill" | "hug" | <px>, "h": "hug" | "fill" | <px>,
     "minw": 0.0, "maxw": 0.0, "minh": 0.0,   # 0 = none
     "aspect": 0.0,                     # width / height, 0 = none (images, charts)
     "hide_below": 0.0,                 # gone, with its space, when the window is narrower than this
     "center": false,                   # a box narrower than its room sits in the middle of it
     # look
     "variant": "",                     # per role, below
     "text": "", "right": false, "strong": false, "on": false,
     "p": 60.0,                         # a progress bar's %
     "chart": {"shape": "bars" | "line" | "area" | "pie" | "donut" | "spark", "rows": [{"label", "value"}]},
     "hue": 0.0,                        # an image placeholder's tint
     # behaviour
     "reacts": true,                    # answers the cursor (buttons, cards, rows, nav items: on to start with)
     "states": null | {"hover": {...}, "press": {...}},   # its own, instead of the panel's "answers"
     "go": null | {"page": "p2", "with": "fade" | "slide" | "match" | ""},   # when clicked
     "children": [<box>, ...]}

ROLES: section, text, button, field, image, icon, divider, badge, progress, chart.
Variants: section "" | card | tint | row | navitem | hero | bubble | bubble-me;
text body | h1 | h2 | caption | label | num | up | down; button "" | ghost;
icon "" | avatar | sm.

Ids are unique across the whole panel ("b" + a number); clean() gives a box without one
a new id and drops what it doesn't know.

As built (model.py):
- A page's "layout" is "" when it wasn't made from one (a new, empty page). A page's root
  is always a section.
- A box that says nothing gets: "w" "fill" for a section, image, chart, field, progress
  and divider, and for text in a column - "hug" for the rest; "align" "center" in a row,
  "stretch" in a column; "gap" 8 for a section; "reacts" true for a button and for a
  section that is a card, row or navitem; "name" its role's ("Text", "Card", "Row" ...).
  An image or a chart always has an "aspect" unless it has a height in px.
- "go" to the box's own page, or to a page there isn't, is null. "with": "" on a link
  means the panel's "go_with".
- A rule's "box" must be a chart or a progress bar (only they are drawn on); its "over"
  is 0..99. A rule or a page key naming a page or a box there isn't is dropped.
- clean({}) - and clean() of anything without a "pages" list - is the template: its pages
  and, unless given, its title, path and clicks. model.is_template(spec) says whether a
  panel still is it. DEFAULTS holds everything but the pages and the cursor.
- find(spec, box_id) -> {"box", "parent" (None for a root), "page"} or None. Also there:
  page_of(spec, page_id), every(box), boxes(spec).
- with_answers(spec, box_id | "", state, key, value): key is "grow", "lift" or "glow";
  "seconds" (the panel's only) sets "hover_seconds" / "press_seconds"; "reset" (a box's
  only) gives the box the panel's answers again.
- with_rule(spec, "", key, value) makes a new rule (the first chart or progress bar, over
  60, to the next page, "match") and then sets key (key "" sets nothing).
- with_insert into a box that isn't a section puts the component after it, in its parent.
- A component's id is never a layout's (catalogue.py: "data_table", "stat_tiles",
  "line_chart", "area_chart", "bar_chart" beside the layouts' "table", "stats",
  "linechart", "barchart"), so tile() knows which is meant by the id alone.

## solve.py

    solve(spec, page_id) -> {"w": width, "h": height, "bar": bar_height, "area": [x, y, w, h], "boxes": [placed, ...]}
    solve_all(spec) -> {page_id: solve()}     # every page at once: the window's height needs them all

`placed` is, back to front (a parent before its children):
`{"id", "role", "variant", "name", "x", "y", "w", "h", "depth", "parent", "lines": [...]}`
plus the box's look keys. Text is measured by rule, never by a font: a character is
`CHAR_W[variant] * size` wide; `lines` are the words broken greedily to the box's width -
the view and the comp draw exactly these lines, so they always agree. A phone's window
is PHONE_ASPECT tall; every other kind hugs its tallest page (pages shorter than the
window are top-aligned).

As built (solve.py):
- "area" is the room the pages have: inside the bar, a phone's home strip and its body's
  sides (BAR, FOOT, INSET by kind - the same in every style).
- A phone is PHONE_ASPECT times its width tall at least: a page that needs more makes
  the window taller, as it does every other kind (nothing is clipped, nothing scrolls).
- A placed box also has "size" (its text's letter size, 0 for a box without words),
  "center" and "reacts". A box hidden by "hide_below" is not in "boxes" at all.
- A button's, a field's and a badge's "lines" are their one line, cut with "…" if it
  can't fit. A line of text i sits with its baseline at y + i * LINE * size +
  (LINE * size + CAP * size) / 2; a button's, field's or badge's at y + (h + CAP * size) / 2.
- Children of a row that ask for more width than there is are all narrowed alike, so no
  box leaves the window. Only rows wrap or make equal columns. "center" on text also
  centres its lines; "right" ends them at the box's right side.
- A page's root is made as tall as the window's room, so a sidebar in a stretching row
  runs the whole way down.
- to_panel(spec, window_height, x, y, frame) and to_frame(...) turn % of the frame into
  panel px and back (the Units above).

## styles.py - the display list

    drawn(spec, page_id, solved=None) -> [prim, ...]   # back to front, the window's own chrome first
                                                # (solved: solve_all(spec), if already in hand)
    chrome(spec, page_id="") -> [prim, ...]     # the window's frame, bar and bar buttons only; the bar's
                                                # title is spec.title or that page's name (the start page's)
    tile(layout_or_component, spec) -> {"w", "h", "prims": [...]}   # a shelf tile's small picture, in the
                                                # panel's style, accent and mode; None for an unknown id
    backdrop(spec) -> "#rrggbb"                 # what the style takes to be behind the window (text is
                                                # checked for 4.5:1 over it; legible(), contrast(), over())

A prim is one of:

    {"box": id, "kind": "rect", "x", "y", "w", "h", "radius", "fill": "#rrggbbaa" | "",
     "stroke": "#rrggbbaa" | "", "stroke_w": 0.0, "shadow": 0.0 (blur px, 0 = none)}
    {"box": id, "kind": "ellipse", "x", "y", "w", "h", "fill", "stroke", "stroke_w"}
    {"box": id, "kind": "line", "x1", "y1", "x2", "y2", "stroke", "stroke_w"}
    {"box": id, "kind": "text", "x", "y", "text", "size", "bold": bool, "align": "left" | "center" | "right",
     "fill"}        # one line; x is where the line starts, its middle or its end (by align), y its baseline
    {"box": id, "kind": "poly", "points": [[x, y], ...], "closed": bool, "fill", "stroke", "stroke_w",
     "draw": true}  # charts: "draw" marks a stroke that is drawn on by length (0..1) as the chart builds
    {"box": id, "kind": "wedge", "cx", "cy", "r", "hole", "from", "to", "fill"}    # degrees clockwise from the top

As built (styles.py): what is drawn on as a chart builds or a progress bar fills carries
"draw" - a poly's stroke `"draw": true` (by length), and also, there only when they
are drawn on: a rect with `"draw": "up"` (a bar: its height times the share, its foot
staying put) or `"draw": "across"` (a progress bar's fill: its width times the share,
from its left), a closed, filled poly with `"draw": "across"` (the room under a line or
area chart: wiped on left to right - only what lies left of its points' leftmost x plus
share times their width shows) and a wedge with `"draw": true` (a pie sweeps: only what
lies within share * 360 degrees of the top shows). The share is clock()'s "draw" for the
prim's box.
A prim's "radius" is never more than half its shorter side. A style with a hard shadow
(Cartoon) draws it as a second rect under the first, not as "shadow".

"box" is the id of the box the prim belongs to ("" for the chrome). Colours carry their
alpha. Charts are drawn from Data Graphics' own arithmetic (pages/data_graphics/graphs/
model.py: clean, layout, pie_of, colours) so that what Graphs learns, a panel's chart
gains - styles.py turns that plan into prims and never keeps a copy of its rules.

## clock.py

    frames(spec, fps) -> total frames
    clock(spec, fps, frame=(1920, 1080)) -> {   # frame: the timeline's size - the cursor's % are of it
      "total": n,
      "cursor": [[x, y] per frame],            # panel px
      "down": [0 | 1 per frame],
      "pages": [{"at": frame, "page": id, "from": id | "", "with": "fade" | "slide" | "match" | "cut",
                 "frames": n, "why": "start" | "click" | "rule" | "key", "box": id | ""}, ...],
      "amounts": {box_id: {"hover": [0..1 per frame], "press": [0..1 per frame]}},   # only boxes that ever answer
      "build": {page_id: {box_id: [start_frame, frames]}},    # presence: 0 before start, eased to 1 over frames;
                                               # start_frame counts from the frame the page starts arriving
                                               # (the "at" of its change), every time it arrives
      "draw": {box_id: [0..1 per frame]},      # a chart's or a progress bar's drawn-on share
      "events": [{"frame", "kind": "over" | "out" | "press" | "release" | "page" | "rule", "box", "page"}],
    }

Rules of the clock:
- The cursor answers only the page showing (the page being arrived at, once a change has
  begun), and only its topmost reacting box under the hot spot.
- hover: the share of the last `hover_seconds` of frames the cursor was over the box, eased
  (so leaving part-way runs back from where it had got to: "reverse"). press: the same over
  `press_seconds`, with the button down inside the box.
- A click (button down, on the frame it goes down) on a box with "go" starts a page change.
- A rule fires once, the first frame its box's drawn-on share (as %) is over its number.
- page_keys change the page at their time. Changes are sorted by frame; one that would
  start while another is still running starts when that one ends.
- With build on, a page's boxes arrive in reading order (top to bottom, then left to
  right), each `stagger` seconds after the one before, from when the page starts arriving;
  charts and progress bars draw on over the page's build.

    pose(spec, clock, frame) -> {
      "pages": [{"page": id, "alpha": 0..1, "dx": panel px,
                 "boxes": {box_id: {"alpha", "dx", "dy", "scale", "sx", "sy", "glow", "own"}}}],
      "cursor": [x, y], "down": 0 | 1, "draw": {box_id: share}}
    carried(solved_page, boxes, box_id) -> {"alpha", "dx", "dy", "scale", "sx", "sy", "glow", "own"}
                                             # how a box is drawn: its pose and those of all it is in

**The rule (a box carries what is in it).** A pose's boxes are LOCAL. A box's alpha,
move (dx, dy) and size (scale, sx, sy) apply to it and to everything inside it, about
its own middle; what is inside adds its own on top: alphas multiply, sizes multiply
(each about its own middle), moves add. "glow" and "own" are the box's alone - "own" 0
hides the box's own prims (a box whose pair glides in a match) while what is in it still
shows. carried() composes a box's chain into one pose about its own middle; the view
draws each prim with its box's carried pose, and the comp nests each moving box's
subtree on a canvas of its own put on by one Merge. A box's arrival, match glide and
fade are worked out against what it is in, so they come out carried as its own (a box
arriving inside one that arrives fades in once and rises RISE px, not twice); a box is
never more seen than what it is in. A hover or a press is the box's own and carries
what is in it: a hovered card grows and lifts with its words.

pose() is the one place that says how a frame looks: the view draws it for the preview's
frame, and comp.py samples it on every frame to write its keys (thinned to what changes).
"scale", "dx", "dy" apply about the box's middle, after the solve, and never move a
neighbour. In a "match" change, boxes with the same name on both pages glide and resize
from one to the other; the rest fade.

As built (clock.py):
- pose()'s "pages" are the pages that show, back to front: one, or the one being left and
  then the one arriving while a change runs. "boxes" lists only the boxes whose local
  pose is not at rest; a box not listed is {"alpha": 1, "dx": 0, "dy": 0, "scale": 1,
  "sx": 1, "sy": 1, "glow": 0, "own": 1} (clock.REST). "sx" and "sy" stretch a box's
  width and height about its middle, on top of "scale" (a "match", or what makes up a
  glide inside a gliding box). "glow" is 0..1. Every pose is local: see the rule above.
  "draw" has the charts and progress bars of the pages that show.
- A click lands on the topmost box under the hot spot that answers or has a "go": a
  linked box takes its click (starts its page change) whether or not it answers; one
  that doesn't answer shows no hover or press. The view's Try it does the same.
- "fade": the page left goes to alpha 0 as the one arriving comes to 1. "slide": the same
  cross-fade while both move left by 20 % of the window's width (clock.SLIDE), so nothing
  needs clipping to the window. "match": both pages stay at alpha 1; a box on the arriving
  page whose role, name and text are a box's on the page left (the first such with the
  first, the second with the second: clock.matched) glides and resizes from that box's
  place, the box it came from doesn't show, and every other box fades (out on the page
  left, in on the one arriving).
- A page builds on every time it arrives, whatever the change: a box's presence is its
  alpha, and it rises RISE (8) px into place. On a page of many boxes "stagger" is
  shortened so the last box starts within BUILD_MOST (1.5) seconds. A chart or progress
  bar draws on from the frame it arrives until the page's build ends, and for at least
  twice build.seconds. With build off, "build" is {page_id: {}} and every "draw" is 1.
  "draw" has rows only for pages that are shown at some time.
- A press is on the box the button went down on, while the cursor stays on it (so a
  button held through a page change still presses the box that was clicked, and nothing
  on the new page). A change to the page already showing is skipped. A "page" event's
  "box" is the box clicked or the rule's box ("" for a key); the other events' "page" is
  the page their box is on.
- clock() keeps what it solved for the panel it was last given (the same object), so
  pose() on every frame costs little; treat a spec as never changed in place.

## comp.py

    comp_text(spec, frame, fps, comp_frames, metrics=None) -> the .comp text   # TAG "BuddyPanel"

As built: pose() sampled on every frame, thinned to keys. A moving box and everything in
it is put together on a canvas of its own and put on by one Merge (its local alpha,
size, move), nested inside the canvas of the moving box it is in. Pages are merged in
the panel's order; a page arriving over one that comes after it (a change back) is put
on again by `<Page>OnTop` above them all on the frames it does, so the stack is always
pose's back to front - a cross-fade's middle differs by which is on top wherever both
pages draw.

## page.py - the protocol

    to the view    state, fonts, picked, toast, alert
    from the view  set {key, value}                       a top-level key of the spec
                   box {id, key, value}                   one thing about a box
                   insert {component, into, at}           a component into a box
                   remove_box {id}
                   layout {page, layout}                  a page becomes a layout from the shelf
                   page_add {} / page_remove {id} / page_rename {id, name} / page_show {id}
                   answers {id | "", state, key, value}   a state's value ("" = the panel's)
                   rule {id | "", key, value} / rule_remove {id}
                   recorded {samples, clicks, seconds}
                   option, add, refresh, undo, redo, reset, switch_tab

`state` carries: connected, timeline, problem, frame, fps, spec, showing (the page id in
view), solved {page_id: solve()}, drawn {page_id: drawn()}, chrome, clock (clock()),
lanes (for the strip under the stage), shelf {"layouts": [{"id", "group", "name",
"tile"}], "components": [{"id", "name", "tile"}]}, kinds, styles, accents, cursors,
replace, adding, can_undo, can_redo, is_template.

## Round 2 - images, typing, a layer's own arrival, moving layers

Asked for by the owner after first use: a photo placeholder replaced by a real image
file; text that types on; layers that can be picked and moved from a list; every
animated thing reachable cleanly. The page's layout changes with it (web/): Layers is
always in view on the left, the shelf opens from "+ Add", the right column is the
window's when nothing is picked and the picked layer's otherwise - with an
"Animations" list - and one timeline dock holds the player and the lanes.

### A box gains

    "image": null | {"path": "D:\Footage\logo.png", "px": [w, h]}   # role image: the file shown, filling the
                                                                      # box and cropped to it (never stretched)
    "type":  null | {"speed": 18.0, "start": "page" | "click", "caret": true, "text": ""}
                 # roles text and field: its words type on, `speed` letters a second (2..60).
                 # "page": from when the box has arrived; "click": from the first click on it
                 # (until then a text shows nothing, a field its placeholder words).
                 # "text": what a FIELD types (its own "text" is the placeholder); a text types its own.
    "arrive": null | {"kind": "rise" | "fade" | "pop" | "none"}
                 # its own way of arriving instead of the page's build ("none": it is just there).

### model.py gains

    with_image(spec, box_id, path, px)      path "" takes the picture off again
    with_move(spec, box_id, into, at)       a box (and what is in it) to place `at` among `into`'s
                                            children (the end for None); `into` must be a section on the
                                            same page and not the box itself or inside it
    with_box() takes "type" and "arrive" (a dict or None), cleaned like the rest
    animations(spec, box_id) -> [{"id": "arrive" | "type" | "answer" | "draw", "on": bool, ...}]
                                            every way this box can move, for the page's Animations list:
                                            "arrive" always; "type" for text and field; "answer" for any
                                            box (its "reacts"); "draw" for a chart or progress bar (always on)

### styles.py

    {"box": id, "kind": "image", "x", "y", "w", "h", "radius", "path", "px": [w, h],
     "crop": [sx, sy, sw, sh]}          # the part of the picture (its own px) that fills the box
An image box with a picture draws this in place of its placeholder. A text prim of a
box that types carries "type": [first, last) - which of the box's letters (counted
through its lines in order, spaces included, the break between lines not) the line holds.

### clock.py

    clock()["typed"] = {box_id: [letters shown per frame]}      # only boxes that type
    clock()["events"] gains {"kind": "type", "frame", "box", "page"} where each starts
    pose()["typed"] = {box_id: letters shown}                   # on the frame
Letters appear in place - the line is laid out whole and its letters are shown from the
first, so nothing moves as it types. With "caret" a thin upright bar the letter's
colour stands after the last letter shown while typing and for CARET_HOLD seconds
after, blinking at CARET_BLINK a second; `caret(solved_page, box_id, shown)` gives its
rect in panel px (by the same letter-width rule the solver measures with), or None.
A box's own "arrive" replaces its build-on: the same clock, its kind.

### comp.py

An image is a Loader written as text (never AddTool: docs/RESOLVE_API_GUIDE.md), sized
and cropped to its box by a Merge and a mask as Interactions' image shapes are. Typing
is keyed: what Text+ shows on each frame is the letters shown. The caret is a keyed mask.

### page.py

    from the view  image {id}                    ask for a picture file for an image box (a file dialog)
                   image_clear {id}
                   move {id, into, at}
                   box {id, key: "type" | "arrive", value}
    files dropped on the page go to the picked image box, else the first image box on the
    page in view that has no picture
    state gains    images {box_id: file url}, animations {box_id: model.animations()}
                   for the picked box only is NOT enough - send it for every box of the page in view

### Round 2 as built (the Python side)

Additions to the shapes above - nothing above was changed, only filled in:

- **page.py gains `pick {id}`** (from the view, "" for none): which box is picked, so a
  dropped picture can go to it. Nothing is sent back and it is no undo step. A view that
  never sends it still works: drops then go to the first image box with no picture on
  the page in view. Several files dropped at once fill the picked box, then each empty
  image box in turn; with no box to take one the page toasts "There's no image box for
  a picture here. Add one from + Add first." `image {id}` uses Interactions' dialog
  ("Choose an image", its filter, the settings key "image_dir"); a picture set or
  dropped is answered with `picked {id}` of its box. `move` `at`: None or -1 is the end.
- **model**: a box always has "image", "type" and "arrive" (None when it hasn't one).
  "image" only on role image ({"path": a .png/.jpg/.jpeg path, "px": two ints
  1..32768}, else None); "type" only on text and field: {"speed" 2..60 (18), "start"
  ("page" for a text, "click" for a field, unless said), "caret" (true), "text" (a
  field's, cleaned like a box's text; always "" on a text)}; "arrive": {"kind"} or None.
  with_box("type" | "arrive", dict) lays the dict over what the box has ({"speed": 30}
  keeps its start); None takes it off. "image" is not a with_box key: with_image(spec,
  id, path, px) sets it (refused for a box that isn't an image or a picture that can't
  be one), with_image(spec, id, "") takes it off. with_move's `at` is counted among the
  section's children WITHOUT the box moved. model.images(spec) lists the boxes with
  pictures. panel_id also hashes each picture file's size and time, so a picture
  changed on disk (the same path) is another panel.
- **animations(spec, id)** entries, in this order:
  `{"id": "arrive", "on", "kind": own kind or "", "build": the page builds on}` - "on"
  is false for "none", and for "" when the build is off;
  `{"id": "type", "on", "speed", "start", "caret", "text"}` (text and field only; when
  off, the values a new one would get); `{"id": "answer", "on": reacts, "own": has its
  own states, "go": its link's page or ""}`; `{"id": "draw", "on": true}` (chart,
  progress).
- **solve**: a placed box also carries "image", "type", "arrive" and **"type_lines"**:
  the lines whose letters type ([] for a box that doesn't) - a text's own "lines", a
  field's typed words as one line, cut with "…" by the rule its placeholder is.
  `solve.typing(placed) -> [(line, first, last, x, baseline)]`: where each such line
  starts and its baseline. A typing line always starts where the WHOLE line would start
  by the letter-width rule (a centred line: x + (w - len * CHAR_W * size) / 2), so its
  letters appear in place.
- **styles**: a text prim of a box that types has `"type": [first, last)` AND is always
  `"align": "left"` with "x" its line's start (solve.typing) - draw its first
  `clamp(typed - first, 0, last - first)` letters from x; it never re-centres. A field
  that types draws its rect, then its placeholder line(s) - in the dim colour, marked
  `"placeholder": true`: shown only while its typed count is 0 - then its typed line in
  the text colour (legible over the field), with "type". An image box with a picture
  draws, in place of its placeholder, the "image" prim (its "radius" the style's image
  radius, never more than half its shorter side) - in an outlined style (Cartoon) with
  a hard-shadow rect under it and a stroke-only rect over it. `styles.crop(px, w, h)`
  is the crop rule.
- **clock**: `clock()` gains **"typed"** {box id: [letters shown per frame]} and
  **"caret"** {box id: [[x, y, w, h] or null per frame]} (a box with "caret" only;
  null where it doesn't show). The view can read the caret's rect and blink straight
  from "caret" instead of working them out. `pose()` always has "typed" {id: letters}
  (every typing box on the pages that show) and **"caret"** {id: [x, y, w, h]} (only the
  carets that show on that frame). The rules:
  - letters shown on frame f, from the frame typing begins (b): `min(n, 1 +
    floor((f - b) * speed / fps))` - the first letter on frame b; n = the letters of
    "type_lines". 0 before b. Each arrival of the box's page types it afresh.
  - b for "page": the frame the box has arrived (its page's change "at" + its build
    start + frames; "at" when it has no place in the build). For "click": the first
    button-down frame, while its page shows, with the hot spot inside the box (topmost
    or not, answering or not) on that arrival; never clicked, it never types.
  - a "type" event `{"frame": b, "kind": "type", "box", "page"}`; events are sorted by
    frame.
  - the caret: from b until the frame the last letter shows (d), standing; then on while
    `(f - d) % (fps / CARET_BLINK) < fps / CARET_BLINK / 2`, for f - d <= CARET_HOLD * fps;
    then gone. CARET_HOLD 1.5 s, CARET_BLINK 1 a second.
  - `caret(solved_page, id, shown)`: after letter `shown`, on the line holding it (the
    first line's start for 0): x = line start + (shown - first) * char_w, y = baseline -
    CARET_UP * size, w = max(1, CARET_W * size), h = (CARET_UP + CARET_DOWN) * size
    (CARET_UP 0.9, CARET_DOWN 0.2, CARET_W 0.08), each rounded to 0.01.
  - "arrive": "rise" is the build's own; "fade" the same alpha with no rise; "pop" the
    same alpha, its size from POP (0.85) to 1 on Data Graphics' "overshoot" curve (so a
    little past 1), as pose's "sx" = "sy" (its "scale" stays the hover's); "none": the
    box has no place in "build" - it never moves and is seen as much as what it is in.
    With the build off, a box with its own kind (not "none") still arrives, from its
    page's arrival, over build.seconds; "build" then lists only those boxes.
- **comp**: see comp.py's docstring - a picture's Loader / Merge (Size = box width x
  zoom / crop width, Center = box middle) / RectangleMask; a typing line's StyledText
  keyed with text_spline, the Text+ left-set at the line's start; a field's placeholder
  faded out by its Merge's Blend; the caret a keyed RectangleMask, blinking by its
  Merge's Blend. Tools: `<Page>_<Box>_PictureImage` (Loader), `..._PictureMask`,
  `..._PictureBack`, `..._PictureFill` (Merge); `..._Label<n>Typed` (the text spline);
  `..._CaretMask`, `..._CaretFill`.
- **place**: a picture whose file is gone is refused before anything is placed:
  'The image "<name>" isn't where it was. Put it back, or take it off its image box.'
