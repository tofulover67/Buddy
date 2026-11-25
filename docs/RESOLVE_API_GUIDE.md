# DaVinci Resolve Scripting API — Field Guide

What Buddy's tools have learned about Resolve's scripting API, and the
traps that aren't in Blackmagic's own reference (the `README.txt` in
Resolve's `Support\Developer\Scripting` folder). Read it before wiring a
new tool into Resolve or changing how an existing one talks to it.

Buddy doesn't read this file; it's for people working on the code.

Paths, process names and system calls are given as they are on Windows,
where this was written. On a Mac the script host is `fuscript`, the
scripting library `fusionscript.so`, and `%TEMP%` the temporary folder.

---

## 1. Decision tree

- **A one-shot action on the open project** (import media, rename clips,
  make bins, export stills)? → Use the shell's shared in-process
  connection: `host.ensure_connected()` gives a `ResolveController`
  (`app/core/resolve_bridge.py`).
- **Polling Resolve on a timer for as long as Buddy is open** (Time
  Tracker)? → Do each check in a **short-lived subprocess**. See §3.
- **Manipulating Fusion's node graph** (Text+, BezierSpline, pasted
  shapes), not just the project / media pool / timeline API? → Read §5
  first. It's a much rougher API surface.

---

## 2. Connecting in-process

Buddy is a persistent app: it can be open before Resolve is, or after
Resolve has quit. A script launched from Resolve's Workspace > Scripts menu
can assume Resolve is running; Buddy can't. So `core/resolve_bridge.py`
first **probes** in a disposable child process (`core/resolve_probe_worker.py`,
exit code only), and imports `DaVinciResolveScript` in-process only once
that probe says Resolve is up. From then on, per-action calls go straight
through the in-process controller.

The bootstrap every connection shares:

```python
import os, sys

def _bootstrap_resolve_env():
    if "RESOLVE_SCRIPT_API" not in os.environ:
        os.environ["RESOLVE_SCRIPT_API"] = os.path.expandvars(
            r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
        )
    if "RESOLVE_SCRIPT_LIB" not in os.environ:
        os.environ["RESOLVE_SCRIPT_LIB"] = (
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
        )
    modules_dir = os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules")
    if modules_dir not in sys.path:
        sys.path.append(modules_dir)
    # On a Mac the bridges set the macOS paths instead.

import DaVinciResolveScript as dvr_script
resolve = dvr_script.scriptapp("Resolve")
```

Requires **Resolve Preferences > System > General > "External scripting
using" = Local**. It's the first thing to check when a connection fails
silently.

Which Resolve allows which connection:

- **Studio:** both. That covers Buddy launched from Workspace > Scripts
  (inside `fuscript.exe`) and a Buddy started outside Resolve (Start menu or
  desktop shortcut, the "start with Resolve" watcher). It also covers Buddy's
  own helper processes, such as Transcribe's `resolve_child.py`.
- **Free, up to 21.0.4:** only a Buddy launched from Workspace > Scripts.
  Nothing started outside Resolve can connect.
- **Free, 21.1 and later:** none. From 21.1 on, only Studio supports Python
  scripting, so Workspace > Scripts doesn't run Buddy either.

**Every step in the chain can return `None` instead of raising:**

```
resolve.GetProjectManager()          -> can be None
  .GetCurrentProject()               -> can be None (no project open)
    .GetMediaPool()                  -> can be None
    .GetCurrentTimeline()            -> can be None (no active timeline)
```

Null-check each hop and raise a `ResolveConnectionError` with a specific,
user-facing message ("No project is open in Resolve.", "No timeline is
open.") rather than letting a raw `None.GetX()` `AttributeError` bubble up.

### Launched from the Scripts menu

Buddy is deployed as `Buddy.py` plus `buddy.zip` in Resolve's
`Scripts\Utility` folder, and runs inside Resolve's script host
(`fuscript.exe`). That environment has quirks:

- Resolve runs scripts via something like `exec()` **without a real
  `__file__`**. The launcher falls back to
  `inspect.currentframe().f_code.co_filename`, then to a hard-coded path
  under `.../Scripts/Utility/`.
- No console is attached. Fatal errors must go to a message box and/or a
  log file under `%TEMP%`, never just a traceback on stderr. A native crash
  leaves nothing at all unless `faulthandler` is writing somewhere
  (`core/crash_log.py` → `~/.buddy/crash.log`).
- `Buddy.py` extracts the zip to `%TEMP%\Buddy\<hash of the zip's size and
  mtime>\`, so a new zip gets a fresh extraction automatically, puts it on
  `sys.path`, then runs `import main; main.main()`.

---

## 3. Polling through a subprocess

**The crash this avoids:** connecting to Resolve's scripting DLL
(`fusionscript.dll`) when Resolve isn't reachable can **hard-crash the
Python interpreter**: a native segfault, not a catchable exception. A
background poller running for hours will hit that state (Resolve closing,
restarting, sitting at the project manager).

How Time Tracker does it (`pages/time_tracker/resolve_bridge.py`,
`resolve_poll_worker.py`):

- The bridge never imports `DaVinciResolveScript` itself. It runs
  `sys.executable resolve_poll_worker.py` with `subprocess.run(...,
  timeout=...)` on a plain background `threading.Thread`, not `QProcess`,
  which can't take the custom `STARTUPINFO` flags below.
- The **worker** connects and queries in its own short-lived process:
  bootstrap → `scriptapp("Resolve")` → `GetProjectManager()` →
  `GetCurrentProject()` → `GetName()`, prints just the project name and
  exits 0/1.
- If the worker crashes, only that child dies. The parent sees empty
  stdout or a non-zero exit and treats the poll as "no project".
- **Results come back through stdout and the exit code only.** Take the
  **last non-blank line** of stdout, not the whole capture: Resolve's
  scripting library prints a startup banner to stdout the first time a
  process connects.
- The thread reports through a Qt `Signal(object, bool)`, which Qt delivers
  on the GUI thread.
- Set `STARTUPINFO` flags `_STARTF_FORCEOFFFEEDBACK` (0x80) and
  `CREATE_NO_WINDOW`. Otherwise Windows' "app starting" cursor spins again
  on every poll.
- **No `tasklist` "is Resolve.exe running" pre-check.** It would save a
  process per poll, but inside Resolve's script host a bare `tasklist`
  lookup can fail to see a Resolve that is running, which would report
  "offline" forever. Every poll runs the real worker.

**"Untitled Project":** `GetCurrentProject()` returns Resolve's default
"Untitled Project" while Resolve sits at the Project Manager, not just
inside a real project. Checking `GetCurrentPage()` to tell the two apart
made Resolve itself laggy and broke detection, so the rule is simply: a
project named exactly `"Untitled Project"` counts as no project. Cheap, no
extra API call; the cost is that a real project with that name is ignored.

---

## 4. Project / media pool / timeline API: gotchas by function

| Call | Gotcha |
|---|---|
| `GetCurrentProject()` / `GetMediaPool()` / `GetCurrentTimeline()` | Return `None` on failure, never raise. Null-check every hop. |
| `media_pool.ImportMedia(paths)` | Returns falsy/empty on failure with **no error detail**: it could mean bad paths, or that the target bin wasn't current when called. |
| `media_pool.AddSubFolder(parent, name)` | Can return `None` (Resolve refused to create the bin). Don't assume success. |
| `media_pool.SetCurrentFolder(folder)` | Returns a bool that's easy to ignore; a `False` here explains a later `ImportMedia` failure. |
| `bin.GetClipList()` / `GetSelectedClips()` | **Include the project's timelines as pseudo-clips**, indistinguishable from footage by `has_video`/`has_audio` (a timeline reports `Audio Ch > 0`). Tell them apart with `clip.GetClipProperty("Type") != "Timeline"`, or renumbering and renaming get thrown off. |
| `GetSelectedClips()` | **Missing on some older Resolve versions.** Wrap it in try/except and fall back to "all clips in the bin". |
| `clip.GetClipProperty(...)` | Can throw. If it does, treat the clip as real media: better to include something than silently drop it. |
| `timeline.GetSetting("timelineFrameRate")` | Returns a **string**; cast it with `float()`. |
| `timeline.AddMarker(frame, color, name, note, duration, customData)` | Returns a bool. Resolve **silently refuses a second marker on a frame that already has one**, which is the usual cause of `False`. |
| `timeline.GetMarkers()` | Can return `None` instead of `{}`. Keys are frame numbers. |
| `timeline.GrabStill()` | Reliable only after `resolve.OpenPage("color")`; re-fetch the timeline object after switching pages. Returns falsy per frame on failure: log it and carry on rather than abandoning the batch. |
| `gallery.GetCurrentStillAlbum()` / `album.ExportStills(...)` / `album.DeleteStills(...)` | All return booleans. Treat a failed export as fatal, a failed delete as a warning (a failed clean-up shouldn't undo a good export). |
| `clip.LinkProxyMedia(path)` | Returns `False` with no reason when Resolve won't take the proxy - the manual's requirements are a **supported codec** and the **same frame rate** as the source, and (measured on 21.1) the **same start timecode** as the clip's `Start TC`. ffmpeg drops a Sony MP4's timecode (it's on the `rtmd` track), so stamp `-timecode`. Report per clip and carry on (see `pages/project_setup/proxy.py`). |

---

## 5. Fusion node-graph API: much rougher

This applies only when manipulating Fusion's comp/tool graph directly
(Text Animator's Text+ work, SVG Importer's shapes) rather than the
project-level API above.

### Getting the right comp

There are **two unrelated ways** to reach a comp, and mixing them up is
the most common failure:

- `resolve.Fusion()` → `fusion.GetCurrentComp()` returns something only
  once that comp is the **active tab on the Fusion page**. It knows nothing
  about the Edit page's selection.
- `TimelineItem.GetFusionCompByIndex()` reaches a specific clip's comp
  from the Edit page, whatever the Fusion page shows. Use it for "the clip
  the user has selected" workflows.

`GetCurrentComp()` returning `None` almost always means the user isn't on
the Fusion page, not that the connection failed.

### Remote-object pitfalls

- **`hasattr()` lies.** A missing attribute on a Resolve/Fusion remote
  object returns `None` rather than raising, so `hasattr(obj, name)` is
  almost always `True`. Use `callable(getattr(obj, name, None))`.
- **Remote objects are unhashable** (`PyRemoteObject` raises on `hash()`).
  Don't use them as dict keys or set members; build an identity tuple from
  readable properties. Unit-test mocks (plain Python objects hash fine)
  never catch this.
- **Multi-value returns can be mangled over the external bridge.**
  `flow.GetPos(tool)`, documented to return an (x, y) tuple, comes back as
  a bare float. `Paste()` from a bridge connection silently does nothing
  beyond a small graph (a 44-tool graph pasted nothing, every time). The
  same calls behave correctly **inside Fusion's own interpreter**: run a
  worker script with `fusion.RunScript(path)` (the same context as a
  Workspace > Scripts script) and have it write its results to a temp JSON
  file that the app reads.
- **`fusion.ActionManager.GetActions()` isn't a usable automation
  surface.** Nearly all of its ~1100 actions come back as empty
  placeholders over the bridge, and pushing on it can destabilise the
  scripting connection until it crashes. UI automation (e.g. pywinauto)
  doesn't work against Resolve's themed Qt menus either.

### Editing the node graph

- **Text Follower (per-line, per-word, per-letter animation).** Measured
  on Studio 21.1, 2026-10-02. `pages/text_animator/motion_resolve.py`'s
  `apply_units` is the working code.
  - **Attaching it:** `comp.AddTool("Follower")` returns `None`, and
    `AddModifier("StyledText", "Follower")` attaches nothing. Use
    `text_tool.AddModifier("StyledText", "StyledTextFollower")`, then
    find it through `text_tool.StyledText.GetConnectedOutput().GetTool()`.
    The words now live on the Follower's `Text` input.
  - **Which inputs:** `Line*`, `Word*` and `Character*` inputs (`WordSizeX`,
    `LineAngleZ`, `CharacterOffset`…) move each line, word or letter as one
    piece. Size needs `TransformSize = 1` and rotation
    `TransformRotation = 1`. The offsets are points, so use an XYPath on
    them, which rests at 0.
  - **Timing:** `Delay` counts **characters** at every level, so a long
    word holds the next one back. For even timing, set `Order` to Manual
    Curve, which is stored as **6**. (The dropdown's numbers aren't in
    display order: a fresh Follower reads 7, which is "Automatic".) Then
    key `DelayByCharacterPosition` with one key per character index,
    giving each character its delay in frames, and keep `Delay` at 1.
  - **Fades:** `Opacity<n>` fades per character too. With Manual Curve
    timing, every letter of a word shares its delay, so the word fades as
    one.
  - **Shading:** attaching the Follower keeps the Text+'s outline and
    shadow. To fade them, switch on the Follower's `Enabled<n>` and key its
    `Opacity<n>` for each element the Text+ shows.
- **A Text+'s text takes one modifier.** Measured on Studio 21.1.0.17,
  2026-10-09, on Text+ clips from `InsertFusionTitleIntoTimeline`.
  Character Level Styling is the modifier `StyledTextCLS` (its tool comes up
  as `CharacterLevelStyling1`, its words on its own `Text` input).
  - `text_tool.AddModifier("StyledText", "StyledTextFollower")` on a Text+
    that has Character Level Styling returns `True`, **deletes the styling
    tool** and leaves the words reading back `''`. The other way round
    (a Follower first, then `StyledTextCLS` asked for on `StyledText`)
    deletes the Follower the same way. So look at
    `text_tool.StyledText.GetConnectedOutput()` before attaching anything,
    and never attach over what's there (`motion_resolve.text_source`).
  - The two can be chained - `follower.AddModifier("Text", "StyledTextCLS")`
    returns `True` and the Follower's `Text` reads as fed by the styling -
    but the next `text_tool.GetInput("StyledText")` **froze Resolve for
    good** (Not Responding, no CPU; it had to be ended). Walking the
    connections (`GetConnectedOutput`, `GetTool`, `GetAttrs`) was fine.
    So every read of a Text+'s words checks for a chain first
    (`motion_resolve.chained` / `_text_of`,
    `FusionAnimationEngine.chained` / `words_of`, Audit's `_chained`, the
    node dump's `_through_a_chain`), and no feature rebuilds or takes one
    apart: the clip is left as it is and the person told.
  - Not measured: styling with a Follower behind it; what the right-click
    menu in the Inspector does in each case; and whether rendering the
    Text+'s picture through a chain (`Output.GetDoD`, which a whole-clip
    Reveal or Shine preset asks for) freezes as the read did.
- **Whose Follower it is.** Three features put a text Follower on a Text+,
  each with its mark in the Text+'s tool data: Animate by
  (`BuddyMotionText`), the highlight's pop (`BuddyHighlight` with `pop`)
  and Subtitles > Timeline animation's letter presets
  (`BuddyTextAnimation` with `follower`). A mark can outlast its Follower
  (deleted by hand in Fusion), so the Follower itself is looked at too:
  Animate by's and the pop's always have `DelayByCharacterPosition` on a
  curve, a letter preset's and Resolve's own titles' never do
  (`motion_resolve.timed`).
- **Keyframing that works:**
  1. `spline = comp.AddTool("BezierSpline")`
  2. `getattr(text_tool, input_name).ConnectTo(spline.Value)`: connect to
     the spline's **`Value` output**; connecting to the tool itself
     returns `False`.
  3. `getattr(text_tool, input_name)[frame] = value`: bracket-index the
     **connected input**, as in Fusion's own examples
     (`comp.Merge1.Blend[1] = 1`).
  - Repeated `SetInput(param, value, time)` calls do **not** build a
    curve: reading back returns the last value whatever the time.
  - `spline.SaveSettings()['KeyFrames']` doesn't reliably show keyframes
    that do work in a render. Treat "no exception" as success rather than
    reading the settings back.
  - Re-animating a clip without first removing what's connected to the
    input **piles up stray keyframes**: disconnect and delete the old
    spline first.
  - Animatable Text+ inputs: `LayoutSize` (not `TransformSize`) for scale,
    `Opacity1` (not `Opacity`/`Blend`).
  - Text+ `Size` is a fraction of the **composition width**, not height
    (`rendered cap height ≈ 0.4444 × Size × width`).
- **A whole title as one generated comp (Animation > Titles).** Measured on
  Studio 21.1, 2026-10-05. A Text+ title placed from the Buddy Text+ bin has a
  two-tool comp: `TextPlus` (named `Template`) into `MediaOut1` (a `Saver` with
  `Index = "0"`). Writing a whole comp as text (one `TextPlus` per word, a chain
  of `Merge`s, `MediaOut1`) and calling `item.ImportFusionComp(path)` puts it on
  the clip, and the Edit page renders it straight away (the import is the
  round trip). The exported text lists only inputs that differ from their
  defaults, so a generated comp can be short. Words land where `canvas_math`
  says to within the glyphs' side bearings; vertically, to the face's baseline
  offset (`text_measure`; Arial Black sat ~0.02 em higher than the 0.375 em
  fallback).
  - **Shading inputs only exist once their element is on.** On a fresh Text+,
    `GetInput("Thickness2")` returns `None` until `Enabled2 = 1`; then element 2
    is an outline (`ElementShape2 = 1`, `Thickness2 = 0.02`) and element 3 a
    shadow (`Offset3 = (0.05, -0.05)`, `SoftnessX3`/`SoftnessY3 = 5`).
    `GetInputList()` doesn't list them at all. Element 1 has a native gradient:
    `Type1` and `ShadingGradient1`.
  - **The box behind the text** (element 4, `Level4 = 0`, "Text"), measured on
    Studio 21.1.2, 2026-11-17, from `TimelineItem.ExportFrame` of a Text+ alone
    (Arial, 1920x1080, Size 0.1 and 0.2): as tall as the lines themselves
    (ascent to descent each, no padding), as wide as the widest line's advance
    plus 0.15 x Size x width either side. `ExtendHorizontal4` and
    `ExtendVertical4` each add themselves x Size x width a side - the vertical
    one goes by the frame's width too - and negative ones shrink it. `Round4` is
    the corners' radius in the same units, capped at half the box (1.0 at Size
    0.1 is a full pill).
  - **Animation in the comp text works too:** `BezierSpline` tools with
    `KeyFrames = { [frame] = { value, LH = {...}, RH = {...}, Flags = { Linear = true } } }`
    wired to `Merge.Blend`, `Transform.Size`/`Angle`, `Blur.XBlurSize`, and an
    `XYPath` (X and Y splines) on `Transform.Center` all play on the Edit page
    after `ImportFusionComp`. Keys sampled every frame from Buddy's own clock
    (titles/timing.py) matched the preview frame for frame. Fusion's
    `XBlurSize` is about twice a Gaussian's sigma-based radius (factor 0.5).
  - **Measured units for Text+ shading:** an outline reaches
    `0.716 x Thickness x Size x width` px whatever the font; a shadow's `Offset`
    is in `Size x width` px; gradient `ShadingMappingLevel` 0 frame, 1 text,
    2 line, 3 word, 4 character (default); `ShadingMappingAngle` 0 runs bottom
    to top and turns counter-clockwise; `AngleZ` turns counter-clockwise.
  - **On a title clip, `ImportFusionComp` replaces the comp** (the count stays
    1), and the comp's `CustomData` reads back with `comp.GetData(key)`: Buddy
    tags its titles `BuddyTitle` to recognise them.
  - **`AppendToTimeline`'s `endFrame` is exclusive:** `startFrame 0, endFrame 95`
    placed 95 frames.
  - **More Text+ and tool units (2026-10-05):** `CharacterSpacing` adds
    `(s - 1) x Size x width` px between letters, the same for every letter (so
    1.5 at Size 0.08 on 1920 adds ~77 px a gap). With `Enabled1 = 0` and an
    outline without `OutsideOnly`, a word is hollow; such an outline reaches
    `0.716 x Thickness x Size x width` px both in and out. A `RectangleMask`'s
    `Width`/`Height` are fractions of the frame's width/height. A Merge's
    `Center` offsets its foreground and its `Blend` fades it: Buddy's titles
    build echo/extrude copies that way, and animate them by keying each copy
    Merge's `Center` through an `XYPath` (two linear BezierSplines) and its
    `Blend` with a BezierSpline - played frame for frame like the preview
    (21.1). **`Duplicate` came back pass-through**
    (`TOOLB_PassThrough` true, no output) when written in a comp file, so it
    isn't used.
  - **`fusion.FontManager.AddFont(path)`** adds a just-installed font to the
    running Resolve (it shows in `GetFontList()` at once, no restart);
    `ScanDir` did nothing. Fonts activated through Adobe Fonts are listed by
    Fusion but invisible to Qt.
  - **`fusion.FontManager.GetFontList()`** is `{family: {style: file}}` -
    the exact `Font` and `Style` a Text+ takes, and the list to offer people.
    On one Windows 11 PC it had 421 faces where the registry's .ttf/.otf
    entries gave 186: it includes .ttc and variable fonts (Bahnschrift,
    Cambria, Yu Gothic) and Adobe Fonts. Families aren't the registry's:
    "Franklin Gothic Medium" is a family (style "Regular"), "Sitka" has
    styles like "Text Bold", "Segoe UI" has "Semilight".
  - **`project.ExportCurrentFrameAsStill(path)`** writes the timeline frame
    under the playhead as a PNG: the way to compare Resolve's picture with
    Buddy's preview.
- **Data Graphics' comps (counters and bar graphs).** Measured on Studio
  21.1.2, 2026-11-20, from `TimelineItem.ExportFrame` of comps imported with
  `ImportFusionComp` onto a Text+ from Buddy's template bin, on a scratch
  1920x1080 23.976 timeline. `core/fusion_comp.py` writes these;
  `pages/data_graphics/` uses them.
  - **Text can be keyed.** A `BezierSpline` whose keys carry text -
    `[f] = { n, Flags = { Linear = true, LockedY = true }, Value = Text { Value = "$1,234" } }`
    - wired to `StyledText` (`SourceOp`, `Source = "Value"`) draws each key's
    text from its frame until the next key. 300 keys (31 KB of comp text)
    imported in 13 ms; a 30 s timer changing on every frame (about 720
    keys) was placed, clip and comp, in 0.4 s. Fusion renames the spline
    after the input (`CounterText` came back as `CounterStyledText`).
  - **An expression on `StyledText` works too:**
    `StyledText = Input { Expression = "Text(string.format(\"%.0f\", Holder.NumberIn1))", }`
    rendered a number keyed on another tool's input (a Custom tool's
    `NumberIn1`, a Blur's `XBlurSize`), and `Text(time)` the frame. Reading a
    `UserControls` number declared on the Text+ itself drew nothing, though
    the control was there and read back keyed.
  - **A Text+'s side:** `HorizontalLeftCenterRight` -1 starts the text at its
    Center, 0 centres it, 1 ends it there (the older `HorizontalJustification`
    0/1/2 does the same). `HorizontalJustificationNew` 0..3 moved nothing.
  - **Mask units:** a `RectangleMask`'s `Width` and `Height` are shares of the
    frame's width and height, its `Center` 0..1 with y up. Both can be keyed
    (a `BezierSpline` on `Height`, an `XYPath` on `Center`), so a bar grows
    from a fixed floor; a Height of 0 draws nothing. `CornerRadius` 0.5
    rounded a 192 px wide bar's corners by about 48 px (a quarter of its
    width), all four corners.
  - **Shape tools work as well:** `sRectangle` into `sRender` into the Saver.
    Its `Width` and `Height` are both shares of the frame's WIDTH, its
    `Translate.X`/`.Y` (written `["Translate.X"] = Input {...}`) offsets from
    the middle in the same unit, y up; it carries its own `Red`/`Green`/`Blue`.
    Shapes and masks both have `WritePosition` and `WriteLength`.
  - **Lines and arcs (2026-11-23, same setup).** A `PolylineMask`'s points
    (`Polyline = Input { Value = Polyline { Points = { { Linear = true, X = -0.4, Y = -0.2 }, ... } } }`,
    `Closed = true,` before `Points` to close it) are offsets from the middle
    as shares of the frame's width (X) and height (Y), y up. Left open, with a
    `BorderWidth` (a share of the frame's width, the whole thickness), it is
    a stroke. `WriteLength` 0..1 draws it on by its length **in pixels**
    (half of a 648 px upright plus a 768 px flat ended 60 px into the flat),
    and keyed it plays; at 0 nothing is drawn, not even a cap. `sPolygon`
    takes the same points but reads Y as a share of the WIDTH.
  - **`CapStyle`** 0 butt, 1 round, 2 square; **`JoinStyle`** 0 bevel, 1
    round, 2 miter. Masks' strokes start with round caps.
  - **An `EllipseMask`'s** `Width`, `Height` and `BorderWidth` are all shares
    of the frame's WIDTH (unlike a RectangleMask's Height), Width/Height the
    diameter of the stroke's middle. With `Solid = 0` it is a ring; its
    stroke starts at 3 o'clock and runs counterclockwise; `WritePosition` is
    where it starts and `WriteLength` how much of the way round (not where
    it ends). A pie slice of radius R: Width = Height = R, BorderWidth = R,
    CapStyle 0 - slices (0, .3), (.3, .2), (.5, .5) tiled a whole disc
    with no seam. A donut is the same with a thinner border. Past the start
    it wraps: position 0.9 for 0.3 drew across 3 o'clock. `Angle = 90` puts
    the start at 12 o'clock (still counterclockwise), so a slice running
    clockwise from 12 o'clock over [a, b] is WritePosition 1 - b, WriteLength
    b - a. A negative Width draws nothing, and so does WriteLength 0.
  - **Masks chain:** a mask's `EffectMask` fed another mask's `Mask` output,
    with `PaintMode = Input { Value = FuID { "Multiply" }, }`, keeps it to
    their overlap - an area under a line wiped on by a RectangleMask.
  - **`sMerge` in a written comp** made `ImportFusionComp` refuse the whole
    comp; not looked into, since the masks do everything needed.
  - **`TimelineItem.ExportFrame(offset, {"outputType": "file", "filePath": path})`**
    writes a still of the clip's frame; `path` or `fileName` instead fails.
  - **`AppendToTimeline` leaves the playhead at the new clip's end.**
    `timeline.SetCurrentTimecode()` with the timecode read beforehand puts it
    back (`core/fusion_place.py` does, so the graphic is seen from its start
    and adding again lands on the same spot).
  - **Where the text sat:** with `canvas_math`'s fallbacks (0.716 em per
    Size x width, the baseline 0.375 em below the Center), Arial Bold's
    baseline came out about 0.02 em lower than asked - 10 px on a 432 px
    letter.
- **Shapes that animate, as masks** (SVG Importer's Lottie side). Measured on
  Studio 21.1.2, 2026-11-23, by importing the generated comp onto a scratch
  clip, exporting frames with `TimelineItem.ExportFrame`, and laying them
  over the same frames from lottie-web:
  - **A mask's outline is keyed with a spline of its own.** `Polyline = Input
    { SourceOp = "<name>", Source = "Value" }` on the `PolylineMask`, and a
    `BezierSpline` whose keys are numbered 0, 1, 2 ... with `Flags = { LockedY
    = true }` and `Value = Polyline { ... }` holding that key's outline (every
    key the same number of points). Fusion moves each point and handle in a
    straight line by how far the curve has got from one number to the next,
    so `LH`/`RH` on those numbers ease the morph.
  - **Every step is a picture the size of the comp.** What a mask draws
    outside the frame is gone before a later `Transform` can bring it back.
    Geometry far from where it is drawn (a layer's own coordinates, with the
    layer's origin at the frame's middle) lost whole lines this way; draw it
    round its own middle and let the Transform's Center and Pivot make up the
    difference.
  - **A border's corners and ends**, as the mask's controls number them from
    0: `JoinStyle` Bevel, Round, Miter, Miter Clip; `CapStyle` Flat, Rounded,
    Square. `MiterLimit` defaults to 10.
  - **Opacity goes on the mask's `Level`, not the Background's alpha.** A
    `Background` with `TopLeftAlpha` below 1 still puts its colour out at full
    strength, and the `Merge` after it reads a picture as already multiplied
    by its alpha - white at 50% over blue came out solid white. `Level` on the
    mask that feeds the Background scales colour and alpha together. (The
    importer's drop and inner shadows still use the alpha: their strength was
    tuned by eye against that, and moving them made the tuned files worse.)
  - **A Point input reads an `XYPath`** written with no `ViewInfo`, with a
    plain `BezierSpline` on X and on Y - on `Transform.Center`, pasted or
    imported.
  - **A sibling reading a `GroupOperator`'s `Output1` gets nothing** in an
    imported comp too, not only in a paste: a `Merge` fed from the group came
    out empty. Test comps keep their tools flat.
- **An expression can read where another tool's input was on earlier frames**
  - a hover that eases in with no keys of its own. Measured on Studio 21.1.2,
  2026-11-25, from `TimelineItem.ExportFrame` of an imported comp: a `Custom`
  tool (`Cursor`) holding a keyed point on `PointIn1` and keyed numbers on
  `NumberIn1..3`, and per button a `Custom` whose `NumberIn1` averages "was the
  cursor inside" over the last six frames, read by a `RectangleMask`'s
  `Width`, `Height` and `Level`.
  - `Cursor:GetValue("PointIn1", time - k).X` and
    `Cursor:GetValue("NumberIn2", time - k)` both look back: the button grew
    over exactly six frames and shrank over six.
  - `Cursor.PointIn1[time - k].X` does **not**: it reads the current frame
    whatever `k` is (the button snapped to full size on the frame the cursor
    arrived).
  - A `:` at the start makes the expression a block of Lua statements ending in
    `return` - `:local s = 0; for k = 0, 5 do ... end; return s / 6` drew the
    same frames as the written-out sum.
  - A tool's expression reads its own inputs by bare name (`NumberIn1 *
    NumberIn1`), and an expression reading another expression's input chains.
  - Before the clip's first frame the look-back reads the first key: a cursor
    that starts on a shape has it fully hovered on frame 0.
  - Inside such a block another tool's point and numbers read as usual
    (`Mask.Center.X`, `Mask.Width`), a Point input takes another's whole
    (`Pivot = Input { Expression = "Mask.Center", }`) or one built
    (`Point(Mask.Center.X, Mask.Center.Y + 0.01)` on a Text+'s `Center`), and
    the loop's length can be another tool's input (`for k = 0, Cursor.NumberIn2 - 1`).
    Interactions' comps are made of these (`pages/interactions/comp.py`) and
    rendered to within a pixel of the arithmetic on every frame.
  - A `PolylineMask`'s `Center` moves its points with it (wired to an
    `XYPath`, a pointer's tip stays on the path), its `Size` scales them about
    there, and a closed one with a `BorderWidth` is the shape grown by half
    that width all round - an edge, with another mask of the same points over
    it.
  - A `RectangleMask` or an `EllipseMask` with `Solid = 0` and a `BorderWidth`
    (a share of the frame's width, the whole thickness) is the shape's outline,
    rounded corners and all, half inside the edge and half out; its `Level`
    takes an expression like any other input.
  - Cost: 40 such buttons took about 550 ms a frame to export against about
    160 ms for the same tools with plain values - roughly 10 ms a button a
    frame, each button's state being read by five inputs. Four buttons were
    no slower than any small comp (about 110 ms).
- **A still image in a generated comp is a `Loader`, written in the comp's
  text - never added through the API.** Measured on Studio 21.1.2, 2026-11-25.
  - `comp.AddTool("Loader")` from a script **opens Resolve's "Open File"
    dialog** and leaves it open (the call itself returns, and `loader.Clip =
    path` still works). While that dialog is up every `ImportFusionComp`
    returns False at once and `resolve.GetCurrentPage()` reads None; nothing
    says why. Someone has to press Cancel in Resolve. When imports that worked
    a minute ago all fail, look at Resolve's window title before the comp.
  - Written as text it imports with no dialog and holds the still for the
    whole clip: `Name = Loader { Clips = { Clip { ID = "Clip1", Filename =
    "D:\\Footage\\logo.png", FormatID = "PNGFormat", StartFrame = -1,
    LengthSetManually = true, TrimIn = 0, TrimOut = 0, ExtendFirst = 0,
    ExtendLast = 0, Loop = 0, AspectMode = 0, Depth = 0, TimeCode = 0,
    GlobalStart = 0, GlobalEnd = 0 }, }, Inputs = { ... }, }` - what
    `SaveSettings` writes for one. A JPEG's `FormatID` is `"JpegFormat"`. A
    PNG takes `["Clip1.PNGFormat.PostMultiply"] = Input { Value = 1, }`, and
    its see-through parts then merge cleanly.
  - The Loader's picture is its own size, not the comp's. A `Merge` puts it on
    a frame-sized Background: `Size` 1 is the picture's own px, `Center` where
    its middle goes, and an `EffectMask` on that Merge crops it (rounded
    corners). `pages/interactions/comp.py` `_fill` is the working code.
- **A shape's answers as states, still live** (Interactions, `pages/interactions/comp.py`).
  Measured on Studio 21.1.2, 2026-11-25: the template, and one with a lift and an
  outline for every shape and one button with states of its own, placed on a scratch
  1920x1080 timeline, 18 frames of each exported and laid over the preview: within
  0.3 of 255 on average on every frame.
  - **A `Custom` tool's Point inputs hold a pair of numbers** (X hovered, Y pressed)
    and expressions read them through `.X` / `.Y` - by bare name inside the tool
    (`PointIn1.X`), by the tool's name from another (`Cursor.PointIn2`), and as
    `Point(Cursor.NumberIn4, Cursor.NumberIn5)`. A Custom has 8 number inputs; the
    pairs are how a shape carries four answers for two states.
  - **A Transform's `Center = Point(0.5, 0.5 + lift)` with its Pivot at the shape's
    middle** moves the shape straight up by `lift` of the frame's height and still
    sizes it about that middle; with a lift of 0 it lands where it did.
  - 18 frames exported in 1.9 s with four shapes; not timed against the build before.
- **A whole mock window baked to keys** (Panels, `pages/panels/comp.py`).
  Measured on Studio 21.1.2, 2026-11-25: the template (three pages, a click,
  hovers, a build-on, two charts; 779 tools) and a Cartoon browser window that
  slides forward a page, glides back with Match and fades on, placed with
  `ImportFusionComp` on a scratch 1920x1080 timeline, 14-16 frames of each
  exported with `TimelineItem.ExportFrame` and laid over the page's own
  preview of the same frame. Every frame came within 1.2 of 255 on average
  (most 0.3-0.7), the difference in the text's and edges' antialiasing.
  - **A `Merge`'s `Size` sizes a full-frame foreground about the frame's
    middle**, so a box on its own full-frame canvas grows about its own
    middle (mx, my) with `Center = (1 - s) x (mx, my) + s x 0.5 + the move`.
    Hover, press, build-on and a Match glide are all this one Merge.
  - **Canvases nest.** A moving box's subtree on its own cleared Background,
    put on by one Merge, inside another box's the same way, inside the
    page's: what is in a box grows, lifts and fades with it.
  - **A page's Merge takes an `EffectMask`** (two masks multiplied: the
    window's inside and its rounded body) and a sliding page is cropped to it.
  - **One output can feed two Merges**: a page arriving over one later in the
    chain is merged a second time above them all, its first Merge held at 0.
  - **An `XYPath` with a spline on one side and a plain number on the other**
    plays (the side that holds needs no spline).
  - **Fade a group as one picture.** The comp blends a canvas, so the preview
    has to as well: drawn prim by prim at the same alpha, a box's hard shadow
    shows through its fill mid-fade (11.8 of 255 off in a Cartoon Match).
  - **A box drawn as nothing of its own has nothing to glow**: a soft mask of
    its bare place put a slab behind a hovered table row.
  - **Pictures and typing** (the same day, one panel: a 1200x400 PNG in a
    16:9 box, a 400x1000 JPEG in a narrow one arriving with a pop, a centred
    heading typing with the page, a field typing when clicked, both with a
    caret; 16 frames, each within 0.4 of 255 of the preview):
    a Loader written as text, put on by a Merge whose `Size` is the box's
    width over the cropped part's width and cropped by a RectangleMask on the
    Merge's `EffectMask`, fills its box unstretched; a Text+ set from its left
    (`HorizontalLeftCenterRight = -1`) at the line's start with its
    `StyledText` keyed letter by letter (`text_spline`) types in place, the
    letters already shown not moving; a field's placeholder goes by its
    Merge's `Blend` on the first letter's frame; a caret as a RectangleMask
    with its `Center` keyed in steps stands after the last letter.
    A centred line that types starts where the letter-width rule puts it, so
    it sits a few px to one side of where the font would centre it - the same
    in the preview and in Resolve.
  - Not measured: how long a comp this size takes to import and to render,
    3840x2160 and upright timelines, faces other than Arial.
- **A drawn design as masks and Backgrounds** (Design, `pages/design/comp.py`).
  Measured on Studio 21.1.2, 2026-11-25: designs placed with
  `ImportFusionComp` on a scratch timeline, a frame exported with
  `TimelineItem.ExportFrame` and laid over the same design drawn by the
  page's own canvas (a 2D context), on 1920x1080, 3840x2160 and 1080x1920
  timelines. `ExportFrame`'s `filePath` must be a whole path: a bare file
  name answers `{'success': False, 'error': 'Failed to write image'}`.
  - **Three ways to draw the same shapes came out the same.** A
    `RectangleMask`/`EllipseMask` per rectangle and ellipse, a `PolylineMask`
    for everything, and the SVG Importer's engine (`build_group_lua`, flat)
    fed the design as an SVG: each within 0.07 of 255 of the canvas on
    average, the difference all in the edges' antialiasing, and within a few
    thousand edge pixels of each other. Design writes its own (the first):
    29 tools where the engine's had 38, a rectangle stays a rectangle in
    Fusion, and text, images, groups and combined shapes go through one path.
  - **`Angle` turns a `RectangleMask`, an `EllipseMask` and a `Merge`'s
    foreground counter-clockwise** about their `Center`, in pixels - a
    rectangle turned 20 degrees on a 16:9 or a 9:16 frame stays a rectangle.
    Clockwise on screen is a negative Angle.
  - **`CornerRadius`** is the radius over half the shorter side (1 is a
    pill), a true arc.
  - **A `RectangleMask` with `Solid = 0` and a `BorderWidth`** has sharp
    outer corners when its `CornerRadius` is 0: it matched a mitred stroke.
    A `PolylineMask`'s `JoinStyle` 0, 1, 2 matched bevel, round and miter
    (with `MiterLimit = 10`, the canvas's own), and its `CapStyle` 0, 1, 2
    butt, round and square.
  - **A linear gradient**: a `Background` with `Type = FuID { "Gradient" }`,
    `GradientType = FuID { "Linear" }`, `Start` and `End` as points 0..1 with
    y up, and `Gradient = Gradient { Colors = { [0] = { r, g, b, a }, ... } }`
    matched a canvas gradient between the same two points.
  - **Opacity as a `Merge`'s `Blend`** matched the canvas's alpha, for a
    shape, an image, and a whole group merged onto a canvas of its own first.
  - **Combining masks**: a mask whose `EffectMask` is the mask before it,
    with `PaintMode = FuID { "Merge" }` unites them, `"Subtract"` cuts the
    later one out of what came before, `"Multiply"` keeps their overlap and
    `"Invert"` everything but it - three in a chain too. All four matched the
    canvas's `source-over`, `destination-out`, `destination-in` and `xor`.
  - **How soft**: against a Gaussian of sigma px (the canvas's `blur(sigma
    px)`), a mask's `SoftEdge` of `2.15 x sigma / frame width` and a `Blur`'s
    `XBlurSize` of `1670 x sigma / frame width` (0.87 per px on a 1920 px
    frame) gave the same 10%-90% edge to within a tenth. `XBlurSize` is a
    share of the frame's width like the masks' units: the same number blurred
    twice as many px on a frame twice as wide, and by the width on an upright
    one. A Text+ shadow's `SoftnessX3`/`Y3` of `120 x sigma / letter size`
    was within a tenth as well.
  - **Text** written with `Center` where the canvas turns it, `Size = letter
    size / (0.716 x width)` for Arial and `HorizontalLeftCenterRight` -1, 0
    or 1 was the same width to the pixel and started, centred or ended on the
    same x; it sat 0.02 em lower than a baseline drawn 0.375 em below the
    Center (4 px on 200 px letters), as noted for Arial above.
  - **An image**: a `Merge`'s `Size = wanted width / the picture's own
    width`, `Center`, `Angle` and a `RectangleMask` as its `EffectMask`
    (rounded corners, turned with it) matched the canvas's picture, turned
    and rounded, to the edges' antialiasing.
- **Flips, components and auto layout frames** (Design, same setup and method,
  2026-11-25 - three test designs, each within the canvas's antialiased edges and
  the 2-3 px text offset noted above):
  - A Transform with `FlipHoriz = 1` / `FlipVert = 1` between a Loader and the Merge
    that places and turns it mirrors the picture before it is turned, as the canvas
    does.
  - `AngleY = 180` (across) or `AngleX = 180` (up and down) on a Text+ mirrors it about
    its Center, before its `AngleZ` turn.
  - **A Text+ shadow's `Offset3` turns and mirrors with the text**: 40 px to the right
    of text turned 90 degrees came out 40 px below it, and mirrored across it came out
    on the left. Design keeps shadows where they are on screen, so it writes the
    offset taken back through the text's turn and flips (`comp._text_shadow`).
  - Tool names with an underscore (`Button4_Card1Mask`, an instance's) import as
    written.
- **Corners rounded each their own way, and rounded polygons and stars** (Design,
  same setup and method, 2026-11-25): a `RectangleMask` has one `CornerRadius`, so a
  box whose four corners differ is written as a `PolylineMask`, each rounded corner two
  points with quarter-circle handles (`geometry._box`). A rounded polygon or star is a
  polyline with each corner cut back along both sides and joined by an arc
  (`geometry.rounded`). One test design held these with a stroke, a shadow, a gradient,
  a turn and a flip, an image's mask, a frame's background, a boolean's base and hole,
  and an instance scaling its main's corners. All of them matched the canvas: mean
  difference 0.11/255 over the frame, nothing over 64/255 outside antialiased edges.
- **Another program polling Resolve can stop the script server starting.**
  Seen 2026-11-23: after Resolve crashed, every restart logged `Started script
  server` then `Failed to connect to script server`, and nothing - not
  `fuscript` either - could reach it. The server's own log (in
  `ResolveDebug.txt`) showed two incoming connections the instant it opened
  and the server quitting 0.2 s later. A separate app that asks Resolve for
  its timecode every couple of minutes was running; with it closed, the next
  start worked. When `scriptapp("Resolve")` is `None` with Resolve open, read
  that log before anything else.
- **Pasting drops cross-group connections.** Fusion's clipboard text (Lua
  table syntax) silently loses any `Inputs` entry that references a node
  across a group boundary. That's a Fusion bug; verify wiring after a paste
  when groups are involved.
- **A group's output pin is `Output1`, not `Output`.** Connecting to the
  wrong name fails silently: no error, just a missing input downstream.
- None of Buddy's tools use `comp.Lock()`/`Unlock()` or
  `StartUndo()`/`EndUndo()`; they make plain `SetInput` calls and keep
  their own undo stacks.
- **Streaming UI changes will crash Resolve.** The API can't take
  real-time (60 fps) updates: push `SetInput` calls at discrete moments
  (e.g. on mouse release), never continuously (e.g. on every mouse move).
- A title inserted with `InsertFusionTitleIntoTimeline` doesn't register a
  reusable `MediaPoolItem`, and making one with `CreateCompoundClip` breaks
  things. Text Animator imports a Text+ template bin
  (`pages/text_animator/text_plus_template.drb`) instead.
- To match Resolve's own SVG/shape import, diff against a real native
  import captured with Fusion's "Dump Selected Node Settings". Guessing from
  a tool's inputs is unreliable (an unused `Polyline2` input looks like the
  mask shape and isn't).

---

## 6. Python and pip under Resolve

- `sys.executable` inside a Resolve-launched script is usually
  **`fuscript.exe`**, Resolve's script runner, not a general Python.
  `fuscript.exe -m pip install ...` fails with `pip.lua not found`.
- Install packages into the **real `python.exe`** Resolve uses. That's
  typically a per-user install, not the Python on `PATH`, for example:
  ```
  C:\Users\<you>\AppData\Local\Python\pythoncore-3.14-64\python.exe
  ```
  Confirm the path first (Buddy's launcher prints `sys.executable` in its
  error dialog and `%TEMP%\buddy_launch.log`), then:
  ```
  "<that path>\python.exe" -m pip install PySide6
  ```

---

## 7. Checklist for a new Resolve-connected tool

- [ ] Go through the shell's connection (`host.ensure_connected()`) for
      one-shot actions; don't import `DaVinciResolveScript` in a page.
- [ ] Null-check every hop: `GetProjectManager()` → `GetCurrentProject()`
      → `GetMediaPool()` / `GetCurrentTimeline()`, with a specific,
      user-facing error for each.
- [ ] Polling in the background? Use the subprocess pattern in §3.
- [ ] Touching the Fusion node graph? Read §5 in full first. Several of its
      traps (unhashable objects, `hasattr()`, mangled multi-value returns)
      are invisible to unit tests with plain Python mocks.

---

## Appendix: where each tool talks to Resolve

| Tool | Connection | Notable | Code |
|---|---|---|---|
| Shell | probe subprocess, then in-process | One shared controller for every page | `core/resolve_bridge.py`, `core/resolve_probe_worker.py` |
| Asset Manager | in-process | Imports files, optionally into a new bin | `pages/asset_manager/resolve_ext.py` |
| Data Graphics | reader's process (`busy_job`), else in-process | A counter or bar graph as a generated comp on a new clip at the playhead | `pages/data_graphics/place.py`, `core/fusion_place.py` |
| Project Setup | in-process | Bins, folder import, timeline population, sync, proxy rendering | `pages/project_setup/resolve_ext.py`, `pages/project_setup/proxy.py` |
| Batch Clip Renamer | in-process | Timeline pseudo-clips in bin listings; `GetSelectedClips()` differs by version | `pages/batch_clip_renamer/resolve_ext.py` |
| Image Importer | in-process | `ImportMedia` failures give no reason (bad path vs. bin not current) | `pages/image_importer/resolve_ext.py` |
| Media Relink | in-process | Finds and relinks offline clips | `pages/media_relink/resolve_ext.py` |
| Stills Exporter | in-process | `GrabStill()` needs `OpenPage("color")` first; duplicate markers are silently refused | `pages/stills_exporter/resolve_ext.py` |
| YouTube Chapters | in-process | Reads the open timeline's markers | `pages/youtube_chapters/resolve_ext.py` |
| Transcribe | in-process | Renders timeline audio, writes subtitle tracks | `pages/transcribe/resolve_ext.py` |
| Time Tracker | **subprocess per poll** | Crash isolation; "Untitled Project" rule | `pages/time_tracker/resolve_bridge.py`, `resolve_poll_worker.py` |
| SVG Importer | in-process, plus `RunScript` worker | `Paste()` size limit, mangled multi-value returns, `Output1`, cross-group paste bug | `pages/svg_importer/engine.py` |
| Text Animator | in-process, plus `RunScript` for diagnostics | Keyframing via `BezierSpline`; `hasattr()`/unhashable pitfalls; 60 fps crash | `pages/text_animator/` |
| Color Palette Manager | none | No scripting API use | — |
