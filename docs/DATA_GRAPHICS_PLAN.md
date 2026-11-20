# Data Graphics: the plan

Where it stands: Counters and bar Graphs are built (`app/pages/data_graphics/`,
under Editing Tools). Line and pie charts, saved looks and the hooks into
Automation and Command Center are not. This is what's there and how the rest
is meant to be built. Delete this file once it all is.

## What it's for

Graphics made from numbers, quickly, by someone who doesn't open Fusion:

- **Counters** - a number that runs up or down to where it's set: followers,
  money, a percentage, a timer.
- **Graphs** - charts from numbers typed or pasted, that draw themselves on.
  Bars today; lines and pies to come.

It is its own tab and not part of Animation because Animation moves text and
clips that already exist; this starts from numbers. It is not called Fusion
because tabs are named for what they make, not how.

## What stays true

- **It only adds.** A graphic is a new clip at the playhead on a free track.
  Nothing already on the timeline is replaced or changed. Adding again on the
  same spot replaces only Buddy's own earlier graphic of that kind (its comp
  carries `BuddyCounter` or `BuddyGraph`), and the box in the footer turns
  even that off.
- **Still editable in Resolve.** What lands is a Fusion comp of ordinary
  tools (Text+, Background, RectangleMask, Merge), not a rendered movie.
- **Simple first, detail on request.** The page has the few things most
  people set, and every part plays a sensible timing by itself. Anything
  finer is asked for: the timing dock under the preview (a lane per part, as
  Animation's Titles have a lane per word - drag a bar to retime the part,
  click it to reshape its curve), and small panels over the page for other
  detail (a graph's "Axes and titles"). Nothing in them changes the graphic
  until it's touched, and a part goes "Back to automatic".
- **What plays in Buddy is what plays in Resolve.** One clock - each page's
  `model.py` - makes both the preview's frames and the comp's keys.

## How it's built

```
core/fusion_comp.py     a comp as the text of a .comp file (shared with Animation's titles)
core/fusion_place.py    that text put on the timeline as a new clip: place_comp()
pages/data_graphics/
  easing.py             the timing curve: a cubic Bezier's two handles, dragged in the dock
  parts.py              a part's timing (start, length, curve), how one arrives, a dock lane
  figures.py            a number as text: decimals, separators, before/after, the clock
  text_look.py          a Text+ of the shared look (font, colour, outline, shadow)
  place.py              the jobs: read, counter, bars (named in resolve_reader_child.JOBS)
  graphic_page.py       what both pages share: state, the read, Add behind the overlay
  web/graphics.js       what both views share: the fields, the dock, the player, the panels
  counters/  graphs/    each: model.py (arithmetic), comp.py (the comp), page.py, web/
```

What the spike in Resolve settled (Studio 21.1.2; the measurements are in
`RESOLVE_API_GUIDE.md`, "Data Graphics' comps"):

- A counter is one Text+ whose `StyledText` is keyed with text on every frame
  the number changes. An expression reading a keyed number also renders, but
  keyed text is exactly the preview's text and needs no formatting in Lua.
- A bar is a Background kept to a RectangleMask whose Height and Center are
  keyed. The shape tools (sRectangle into sRender) work too; masks were kept
  because their units were already known.

A graph's value axis (numbers up the side, lines across, a typed top), its
subtitle and its axis titles are in `graphs/model.py`'s `scale_of` and `layout`,
and in the "Axes and titles" panel. A bar's own colour is picked by
double-clicking its swatch in the table (Buddy's colour picker).

## Still to build

1. **Lines** - drawn on left to right. A PolylineMask's points, or a stroke
   with `WriteLength` keyed (both masks and shapes have `WritePosition` and
   `WriteLength` inputs - not yet measured).
2. **Pie and donut** - swept round. Not measured yet: an EllipseMask has no
   sweep; try sEllipse with `WriteLength` on a thick border, or a keyed
   polygon wedge.
3. **A way out at the end** - every part has an arrival (`parts.py`), none
   has an exit yet. In the dock it would be a second bar on the lane, as a
   word's exit is in Animation's Titles.
4. **Saved looks**, steps for Automation, Command Center actions.
5. **A Mac check.** It is `mac=True` in the registry and has nothing
   Windows-only in it, but placing has only been run on Windows.

## For whoever builds it

- A new kind of graph is a `model.py` (layout and growth, tested without Qt
  or Resolve) and a `comp.py`, a job in `place.py`, and its drawing in the
  page's script. `tests/_fake_graphics.py` is the fake Resolve.
- Run a spike in a live Resolve first, on a scratch timeline that is deleted
  afterwards (ask first), and write what was measured into the guide.
