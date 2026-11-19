# Data Graphics: the plan

Where it stands: the tab is in the sidebar (`app/pages/data_graphics/`, under
Editing Tools) with two sub tabs, Counters and Graphs. Both are empty - each
says what it will do and that it's coming. This is how they're meant to be
built. Delete this file once both are.

## What it's for

Graphics made from numbers, quickly, by someone who doesn't open Fusion:

- **Counters** - a number that runs up or down to where it's set: followers,
  money, a percentage, a timer.
- **Graphs** - bar, line and pie charts from numbers typed or pasted, that
  draw themselves on.

It is its own tab and not part of Animation because Animation moves text and
clips that already exist; this starts from numbers. It is not called Fusion
because tabs are named for what they make, not how.

## What stays true

- **It only adds.** A graphic is a new clip at the playhead on a free track.
  Nothing already on the timeline is replaced or changed. Changing a graphic
  Buddy made earlier replaces only that one (tag its comp, as titles are
  tagged `BuddyTitle` in `text_animator/titles/titles_resolve.py`).
- **Still editable in Resolve.** What lands is a Fusion comp of ordinary
  tools, not a rendered movie, so the user can restyle it afterwards.
- **What plays in Buddy is what plays in Resolve.** One clock drives the
  preview and the keyframes, as `text_animator/titles/timing.py` does.

## The route onto the timeline

Use the one Animation's Titles already proved on Studio 21.1, in
`text_animator/titles/`:

1. `textplus_comp.py` writes a Fusion comp as the text of a `.comp` file.
   Pure Python, no Resolve, tested on its own.
2. `titles_resolve.py` places a Text+ from Buddy's template bin with
   `AppendToTimeline` (lands on an exact frame and track), then
   `ImportFusionComp` replaces its comp. An imported comp renders on the Edit
   page at once; nodes built through the API don't.

So each graphic needs a pure "numbers and options -> comp text" module and a
thin Resolve module that places it. Read `docs/RESOLVE_API_GUIDE.md` first.

Shared code: placing a comp (`pick_track`, `in_the_way`, `_template`,
`place`) and the easing curves (`timing.py`) are what both tools need. Move
those into a shared module that Animation and Data Graphics both import
instead of copying them. The Text+ look (fill, stroke, shadow, font) comes
from `textplus_comp.shading` and `titles/fonts.py`.

## Order of work

1. **A spike in Resolve, before any UI** (ask first; on a scratch timeline
   that is deleted afterwards). Two things to find out:
   - Counter: does a Text+ whose `StyledText` is keyed frame by frame import
     and render, and how heavy is a 10 second count? The alternative is an
     expression on `StyledText` that formats a keyed number, which is lighter
     and stays a number the user can retime - check it survives
     `ImportFusionComp`.
   - Graph: which tools draw a bar, a line and a pie slice cleanly and can be
     keyed - Background with Rectangle and Ellipse masks, or the shape tools
     (sRectangle, sEllipse, sRender). Whichever it is, record what was
     measured in `RESOLVE_API_GUIDE.md`.
2. **Counters.** From, to, how long, the easing, decimals, thousands
   separator, text before and after (a currency sign, %), a clock form
   (m:ss), font, size and colour. A live preview in the page. One main
   button: Add to timeline.
3. **Graphs: bars first.** A small table to type in, and paste from a
   spreadsheet (tabs or commas). Labels, values, colours from the theme or
   picked, value labels on or off, bars growing one after another. Then
   lines (drawn on left to right), then pie and donut (swept round).
4. **Afterwards.** Saved looks, steps for Automation, Command Center actions,
   and the real capability lines in `app/core/tools_kb.json` (its entry now
   says nothing can be made yet - `more_tools.py` and the README say so too).

## For whoever builds it

- Each sub tab is its own page package (`counters/`, `graphs/`) with a
  `model.py` of pure arithmetic, tested without Qt or Resolve - the pattern
  of `pages/podcast/shorts/`.
- The pages are `WebToolPage`s today with only `switch_tab`. Resolve work
  goes through `JOBS` in `core/resolve_reader_child.py` and
  `core/busy_job.run`, in a module with no Qt in it.
- `AGENTS.md` has the wiring checklist. Already done for this tab: the
  registry, `BUCKETS`, the More tools line, the translations file
  (`data_graphics.json`), the Ask Buddy entry and `tests/test_data_graphics.py`.
- It is `mac=True` in the registry because an empty page has nothing
  Windows-only in it. Check it on a Mac again once it does real work.
