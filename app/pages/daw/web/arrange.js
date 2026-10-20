/*
 * Arrange: the whole piece at once - a browser of sounds on the left, the
 * tracks over the song lane, and the mixer along the bottom. For building a
 * cue with several parts. A track's knobs are in Console (double-click a clip,
 * or "Edit in Console" in the track's menu).
 *
 *   Tracks    click a clip to select it, drag to move it (Alt: a copy), drag
 *             its right end to lengthen it, double-click it to edit its notes
 *             in Console; double-click an empty stretch for a new clip; Delete,
 *             Ctrl+C, Ctrl+X, Ctrl+D and D (disable or enable, when the tracks were
 *             clicked last - else D plays a note) on the selected clip, and in its
 *             menu. Ctrl+V pastes on the selected track at the playhead (click an
 *             empty stretch for both), and again straight after; right-click an
 *             empty stretch: Paste here.
 *             Snapping (the magnet, N): a clip's start or end goes to the
 *             nearest line of the grid (the Snap setting's) or beat of the song
 *             - which, lined up with the timeline, can fall between them; off,
 *             it goes where it's let go. A track's menu (right-click its name):
 *             rename, duplicate, disable, delete
 *   Ruler     two rows. The loop strip on top: drag across it to loop just
 *             that stretch (lit down the tracks, the playhead to its start) -
 *             its ends snap as clips do (the magnet), Shift for whole bars -
 *             drag the lit stretch to slide it or an end for more or less,
 *             click them to turn the loop on or off, double-click a bar for
 *             just that one. The bar numbers under it: click to move the
 *             playhead, drag to scrub. A clip's menu (right-click) loops its bars
 *   Picture   under the ruler, with a timeline open: the edit in Resolve - its
 *             cuts, its markers in their colours, where the picture ends - with
 *             faint lines down the tracks, to write music to. Click one for the
 *             playhead there. Follow: the playhead goes where Resolve's is moved
 *             to while the DAW is stopped
 *   Song      the music the DAW plays along with, its waveform on the bars. Under it,
 *             the timeline's audio: each audio track of the edit, its clips and their
 *             waveforms, to write around (shown or hidden with its heading; never played)
 *   Scrolling the mouse wheel scrolls the tracks up and down, or along when they
 *             all fit; Shift + scroll goes along. Alt + scroll zooms (as on
 *             Resolve's timeline; Ctrl + scroll too), or the - Fit + buttons
 *   Browser   click a sound to give it to the selected track (a new track
 *             when it's the other kind), + for a new track, or drag it onto
 *             a track
 */
"use strict";

window.DawViews = window.DawViews || {};

DawViews.arrange = (() => {
    const {el, icon} = Buddy;
    const HEAD = 150;
    let root = null, UI = null;
    let px = 0;                        // pixels a beat; 0 until it's been fitted
    // Where something is, in beats: it moves and stretches with --beat, so a zoom step
    // draws nothing again (zoom()).
    const at = beats => `calc(var(--beat) * ${+beats.toFixed(6)})`;
    const ZOOM_SETTLE_MS = 180;        // zooming stopped this long: drawn again, crisp
    const FIT_MOST = 32 * 4;           // a first look shows 32 bars at the most - a long song scrolls

    function build(into) {
        root = into;
        UI = {};
        UI.search = el("input.field.daw-search", {type: "search", placeholder: "Search sounds…", "aria-label": "Search sounds"});
        UI.search.addEventListener("input", drawBrowser);
        UI.browserList = el("div.browser-list");
        UI.browser = el("aside.daw-browser", {}, [UI.search, UI.browserList]);
        UI.lanes = el("div.lanes", {tabindex: "-1"});
        UI.mixer = el("div.daw-mixer");
        UI.magnet = el("button.btn.ghost.magnet", {type: "button", onclick: () => toggleSnap()}, [el("i.magnet-icon")]);
        UI.zoom = el("div.zoom", {}, [UI.magnet, el("span.zoom-sep"),
            el("button.btn.ghost.icon", {type: "button", title: "Zoom out (Alt + scroll)", onclick: () => zoom(1 / 1.4)}, [icon("minus")]),
            el("button.btn.ghost", {type: "button", text: "Fit", title: "The whole piece in view", onclick: () => fitAll()}),
            el("button.btn.ghost.icon", {type: "button", title: "Zoom in (Alt + scroll)", onclick: () => zoom(1.4)}, [icon("plus")])]);
        root.append(el("div.arr", {}, [UI.browser, el("div.arr-mid", {}, [UI.lanes, UI.zoom]), UI.mixer]));
        UI.lanes.addEventListener("wheel", wheel, {passive: false});
        // The tracks were clicked last (a clip, a lane): D is for the selected clip, not a note.
        window.addEventListener("pointerdown", e => { UI.armed = !!(UI.lanes.contains(e.target)); }, true);
        syncMagnet();
        UI.lanes.addEventListener("scroll", () => {
            placeHead(Daw.playing() ? Daw.position() : null);
            if (!UI.winDue) UI.winDue = requestAnimationFrame(() => { UI.winDue = 0; syncWindow(); });
        });
        UI.seen = new IntersectionObserver(entries => {
            for (const e of entries) if (e.isIntersecting) { UI.seen.unobserve(e.target); e.target.paint(); }
        }, {root: UI.lanes, rootMargin: "0px 1200px"});
        drawBrowser();
        refresh("all");
    }

    /* A zoom step, around the mouse (clientX) or the middle. Nothing's drawn again: --beat is
       set and all that's placed in beats moves and stretches with it - the waveform and the
       clips' notes too, a little soft. Once the zooming stops, they're drawn again at the new
       size, crisp, with as many bar numbers as fit. */
    function zoom(by, clientX = null) {
        const r = UI.lanes.getBoundingClientRect();
        const atX = clientX !== null ? Math.max(0, clientX - r.left - HEAD) : (UI.lanes.clientWidth - HEAD) / 2;
        const beat = (UI.lanes.scrollLeft + atX) / px;
        px = Math.min(160, Math.max(2, px * by));
        UI.grid.style.setProperty("--beat", `${px}px`);
        UI.grid.style.setProperty("--w", `${(Daw.projectBeats() + 16) * px}px`);
        syncRange();
        placeHead(Daw.playing() ? Daw.position() : null);
        UI.lanes.scrollLeft = beat * px - atX;
        syncWindow();
        clearTimeout(UI.settle);
        UI.settle = setTimeout(crisp, ZOOM_SETTLE_MS);
    }

    /* Zooming settled: what's drawn at a size drawn again at the new one, in place - the
       waveform, each clip's notes, as many bar numbers as fit. Nothing else is rebuilt. */
    function crisp() {
        if (!UI.grid || !UI.grid.isConnected) return;
        syncWindow(true);
        for (const wave of UI.waves) strips(...wave);
        for (const [canvas, t, c] of UI.clipCanvases) drawClip(canvas, t, c);
    }

    /* The wheel's zoom steps, gathered: one a frame, however many the wheel sends. */
    function zoomSoon(by, clientX) {
        if (UI.zoomBy) { UI.zoomBy *= by; UI.zoomX = clientX; return; }
        UI.zoomBy = by;
        UI.zoomX = clientX;
        requestAnimationFrame(() => {
            const step = UI.zoomBy;
            UI.zoomBy = 0;
            zoom(step, UI.zoomX);
        });
    }

    function fitAll() {
        const w = UI.lanes.clientWidth - HEAD - 24;
        if (w > 100) px = Math.min(80, Math.max(2, w / Daw.projectBeats()));
        const top = UI.lanes.scrollTop;
        drawLanes();
        UI.lanes.scrollLeft = 0;
        UI.lanes.scrollTop = top;
    }

    /* The wheel: up and down through the tracks - or along, when they all fit, or with
       Shift. Alt + scroll zooms around the mouse, as on Resolve's own timeline; Ctrl +
       scroll too (a trackpad's pinch). Qt on Windows turns an Alt + wheel into a
       sideways one, so whichever moved is the step; line steps count ~40px. */
    function wheel(e) {
        const step = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
        if (!step) return;
        const pxStep = e.deltaMode === 1 ? step * 40 : e.deltaMode === 2 ? step * 400 : step;
        if (e.altKey || e.ctrlKey || e.metaKey) {
            e.preventDefault();
            return zoomSoon(Math.pow(1.0022, -pxStep), e.clientX);           // a wheel's notch: about a quarter
        }
        const lanes = UI.lanes, tall = lanes.scrollHeight > lanes.clientHeight + 1;
        if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;        // already sideways (a trackpad): as it comes
        if (e.shiftKey || !tall) {
            e.preventDefault();
            lanes.scrollLeft += pxStep;
        }
    }

    // ------------------------------------------------------------ browser --

    function drawBrowser() {
        const q = UI.search.value.trim().toLowerCase();
        const item = (kind, name, info) => {
            if (q && !`${info.label} ${info.about}`.toLowerCase().includes(q)) return null;
            const t = Daw.selTrack();
            const on = t && t.kind === kind && t.sound === name;
            const node = el(`div.bitem${on ? ".on" : ""}`, {draggable: "true", title: info.about, tabindex: "0", role: "button",
                onclick: () => give(kind, name),
                onkeydown: e => { if (e.key === "Enter") give(kind, name); }}, [
                el("i", {style: `background:${kind === "drums" ? Daw.COLORS[2] : Daw.COLORS[0]}`}),
                el("span", {text: info.label}),
                el("button.badd", {type: "button", title: "A new track with this sound", "aria-label": "Add as a new track",
                    onclick: e => { e.stopPropagation(); addTrack(kind, name); }}, [icon("plus")])]);
            node.addEventListener("dragstart", e => {
                e.dataTransfer.setData("text/x-daw-sound", JSON.stringify({kind, name}));
                e.dataTransfer.effectAllowed = "copy";
            });
            return node;
        };
        // Each kind its own box, its heading marked in the kind's colour.
        const group = (label, color, items) => {
            items = items.filter(Boolean);
            return items.length ? el("div.bgroup", {style: `--group:${color}`}, [el("span.daw-label", {text: label}), ...items]) : null;
        };
        UI.browserList.replaceChildren(...[
            group("Instruments", Daw.COLORS[0], Daw.INSTRUMENTS.map(n => item("inst", n, Instruments.KINDS[n]))),
            group("Drum kits", Daw.COLORS[2], Daw.KITS.map(n => item("drums", n, Drums.KITS[n]))),
        ].filter(Boolean));
    }

    /* A sound clicked: the selected track's, when it's the same kind - else a new track. */
    function give(kind, name, t = Daw.selTrack()) {
        if (t && t.kind === kind) return Parts.setSound(t, name);
        addTrack(kind, name);
    }

    function addTrack(kind, name) {
        const P = Daw.P;
        const label = kind === "drums" ? Drums.KITS[name].label : Instruments.KINDS[name].label;
        const used = new Set(P.tracks.map(t => t.color));
        const color = [...Array(10).keys()].find(c => !used.has(c)) ?? P.tracks.length % 10;
        Daw.edit("Add a track", () => {
            const t = Daw.newTrack(kind, name, label, color);
            P.tracks.push(t);
            P.sel.track = t.id;
            P.sel.clip = "";
        }, "tracks");
    }

    // ------------------------------------------------------------ lanes --

    function fitZoom() {
        const w = UI.lanes.clientWidth - HEAD - 24;
        if (w > 100) { px = Math.min(80, Math.max(2, w / Math.min(FIT_MOST, Daw.projectBeats()))); UI.unfitted = false; }
        return px > 0;
    }

    // The edit, from the frame (app.js) - which is still being made on the very first draw.
    const picture = () => { try { return DawApp.picture(); } catch (err) { return null; } };

    function drawLanes() {
        // Hidden (no width yet): a zoom for now, fitted once it shows.
        if (!px && !fitZoom()) { px = 20; UI.unfitted = true; }
        const P = Daw.P, beats = Daw.projectBeats() + 16, width = beats * px;
        // What's drawn at a size, kept track of to draw again in place (crisp()).
        UI.barsEvery = 0;
        UI.waves = [];
        UI.clipCanvases = [];
        UI.editBodies = [];
        UI.win = null;
        UI.seen.disconnect();
        const rows = [ruler(width, beats)];
        const pic = picture();
        UI.guides = el("div.pic-guides");
        if (pic) rows.push(pictureLane(width, pic));
        if (Daw.song) rows.push(songLane(width));
        if (pic && pic.audio.length) rows.push(...editAudio(pic));
        for (const t of P.tracks) rows.push(lane(t, width));
        rows.push(el("div.lane.add", {}, [
            el("div.head", {}, [
                el("button.btn.ghost", {type: "button", onclick: () => addTrack("inst", "pad")}, [icon("plus"), el("span", {text: "Instrument"})]),
                el("button.btn.ghost", {type: "button", onclick: () => addTrack("drums", "tight")}, [icon("plus"), el("span", {text: "Drums"})])]),
            el("div.body", {style: "width:var(--w)"})]));
        UI.playhead = el("i.playhead");
        UI.shade = el("i.loop-shade");
        UI.grid = el("div.lanes-grid", {style: `--w:${width}px;--beat:${px}px`}, [UI.shade, UI.guides, ...rows, UI.playhead]);
        UI.lanes.replaceChildren(UI.grid);
        syncWindow(true);
        syncRange();
        frame(Daw.playing() ? Daw.position() : null);
    }

    /* The playhead: where it's playing, or - stopped - where it'll start. */
    function placeHead(pos) {
        if (!UI.playhead) return;
        const x = HEAD + Math.max(0, pos ?? Daw.P.cursor) * px;
        UI.playhead.style.left = `${x}px`;
        if (UI.handle) UI.handle.style.left = `${x - HEAD}px`;
        // Scrolled out of sight to the left, it would cross the track names: hidden.
        UI.playhead.hidden = x < UI.lanes.scrollLeft + HEAD;
    }

    /* The playhead in sight: scrolled to when it's gone past either side. */
    function showHead(pos, ahead = 40) {
        const x = HEAD + Math.max(0, pos) * px, view = UI.lanes;
        if (x > view.scrollLeft + view.clientWidth - ahead || x < view.scrollLeft + HEAD) view.scrollLeft = Math.max(0, x - HEAD - ahead);
    }

    /* The ruler, two rows - one for the bars that loop, one for the playhead, so a click
       meant for one never does the other.
         Loop strip (top)    drag across it: the bars to loop - whole bars (Shift: by the beat),
                             the loop on, the playhead to their start. Drag the lit bars to slide
                             them, an end for more or fewer; click them to turn the loop on or
                             off; double-click a bar for just that one
         Bar numbers         click: the playhead there; drag: scrub
       The lit bars stay in sight with the loop off (dimmed): what L and "Place the loop" mean.
       Playing, a change of bars goes on from the first of them straight away. */
    function ruler(width, beats) {
        const P = Daw.P;
        const strip = el("div.loopbar", {title: Buddy.t("Drag across to choose what loops (Shift: whole bars)")});
        const bars = el("div.bars", {title: Buddy.t("Click to move the playhead, or drag to scrub")});
        UI.bars = bars;
        barNumbers(beats);
        UI.rangeLabel = el("span.range-label", {translate: "no"});
        UI.range = el("div.range", {title: Buddy.t("Drag to slide the loop, click to turn it on or off")}, [
            el("i.range-edge.start", {title: Buddy.t("Drag to change which bars loop"), dataset: {edge: "start"}}),
            UI.rangeLabel,
            el("i.range-edge.end", {title: Buddy.t("Drag to change which bars loop"), dataset: {edge: "end"}})]);
        UI.handle = el("i.head-handle");
        strip.append(UI.range);
        bars.append(UI.handle);
        const time = el("div.timebar", {style: "width:var(--w)"}, [strip, bars]);

        const beatAt = e => Math.max(0, (e.clientX - time.getBoundingClientRect().left) / px);
        const snapped = b => { const g = P.snap ? 1 / P.snap : 0.25; return Math.round(b / g) * g; };
        // Dragged to an edge: the tracks scroll along.
        const follow = e => {
            const r = UI.lanes.getBoundingClientRect();
            if (e.clientX > r.right - 30) UI.lanes.scrollLeft += 12;
            if (e.clientX < r.left + HEAD + 20) UI.lanes.scrollLeft -= 12;
        };
        let drag = null;

        // The loop strip.
        strip.addEventListener("pointerdown", e => {
            if (e.button !== 0) return;
            e.preventDefault();
            Ctl.capture(strip, e);
            const edge = e.target.dataset && e.target.dataset.edge;
            const on = !edge && e.target.closest(".range");
            drag = edge ? {kind: "edge", edge}
                : on ? {kind: "slide", from: beatAt(e), start: P.loop.start, end: P.loop.end}
                    : {kind: "new", from: beatAt(e)};
            drag.x = e.clientX;
            drag.moved = false;
        });
        strip.addEventListener("pointermove", e => {
            if (!drag || drag.kind === "scrub") return;
            if (!drag.moved) {
                if (Math.abs(e.clientX - drag.x) <= 4) return;
                drag.moved = true;
                Daw.checkpoint(drag.kind === "new" ? "Choose the bars to loop" : drag.kind === "slide" ? "Slide the loop" : "Change which bars loop");
            }
            // Its ends go where a clip's would (snapping: the grid and the song's beats; off,
            // anywhere) - or, with Shift, to whole bars.
            const b = beatAt(e), bars = e.shiftKey, least = bars ? 4 : 0.25;
            const to = x => (bars ? Math.round(x / 4) * 4 : snapBeat(x).beat);
            if (drag.kind === "new") {
                const lo = Math.min(drag.from, b), hi = Math.max(drag.from, b);
                const a = Math.max(0, bars ? Math.floor(lo / 4) * 4 : to(lo));
                setRange(a, Math.max(a + least, bars ? Math.ceil(hi / 4) * 4 : to(hi)));
            }
            if (drag.kind === "slide") {
                const len = drag.end - drag.start;
                const start = bars ? drag.start + Math.max(-drag.start, Math.round((b - drag.from) / 4) * 4) : Math.max(0, to(drag.start + b - drag.from));
                setRange(start, start + len);
            }
            if (drag.kind === "edge") {
                const at = to(b);
                if (drag.edge === "start") setRange(Math.max(0, Math.min(at, P.loop.end - least)), P.loop.end);
                else setRange(P.loop.start, Math.max(at, P.loop.start + least));
            }
            follow(e);
        });
        strip.addEventListener("pointerup", () => {
            const was = drag;
            drag = null;
            if (!was) return;
            if (was.moved) return chosen();
            // A click on the lit bars: the loop on or off.
            if (was.kind === "slide") {
                P.loop.on = !P.loop.on;
                syncRange();
                Daw.changed("loop");
                DawApp.syncBar();
            }
        });
        strip.addEventListener("pointercancel", () => { drag = null; });
        strip.addEventListener("dblclick", e => {
            if (e.target.closest(".range")) return;
            const a = Math.floor(beatAt(e) / 4) * 4;
            Daw.checkpoint("Choose the bars to loop");
            setRange(a, a + 4);
            chosen();
        });

        // The bar numbers: the playhead.
        bars.addEventListener("pointerdown", e => {
            if (e.button !== 0) return;
            e.preventDefault();
            Ctl.capture(bars, e);
            drag = {kind: "scrub"};
            DawApp.scrub.start();
            DawApp.scrub.to(snapped(beatAt(e)));
        });
        bars.addEventListener("pointermove", e => {
            if (!drag || drag.kind !== "scrub") return;
            DawApp.scrub.to(snapped(beatAt(e)));
            follow(e);
        });
        const scrubbed = () => { if (drag && drag.kind === "scrub") { drag = null; DawApp.scrub.end(); } };
        bars.addEventListener("pointerup", scrubbed);
        bars.addEventListener("pointercancel", scrubbed);

        syncRange();
        return el("div.lane.ruler", {}, [el("div.head", {}, [el("span.daw-label", {text: "Loop"}), alignPick()]), time]);
    }

    /* Bars: what they line up with, with a song - its beats (bar 1 its first downbeat) or the
       timeline (bar 1 its first frame). Without one, just the word. */
    function alignPick() {
        // "Bar lines", not "Bars": that word is a letterbox's elsewhere in Buddy (one translation each).
        if (!Daw.song) return el("span.daw-label", {text: "Bar lines"});
        const seg = Ctl.seg([["song", "Song", "Bars on the song's beats – bar 1 is its first downbeat"],
                             ["timeline", "Timeline", "Bars from the timeline's first frame – at the song's tempo, lined up with the edit"]],
            Daw.P.align === "timeline" ? "timeline" : "song", v => { Daw.setAlign(v); DawApp.syncBar(); }, "Line the bars up with");
        seg.node.classList.add("align-pick");
        return el("div.align-row", {}, [el("span.daw-label", {text: "Bar lines", title: "What the bar lines line up with"}), seg.node]);
    }

    // ------------------------------------------------------------ snapping --

    function syncMagnet() {
        const on = Daw.P.snap_clips !== false;
        UI.magnet.setAttribute("aria-pressed", String(on));
        UI.magnet.title = Buddy.t(on ? "Snapping on: clips go to the grid and the song's beats (N)"
            : "Snapping off: clips go where they're let go (N)");
    }

    function toggleSnap() {
        Daw.P.snap_clips = Daw.P.snap_clips === false;
        syncMagnet();
        Daw.changed("snap");
        Buddy.toast(Buddy.t(Daw.P.snap_clips ? "Snapping on" : "Snapping off"), 900);
    }

    /* Beat b, snapped: to the nearest line of the grid (the Snap setting's, a sixteenth when
       it's off) or beat of the song, whichever's nearer - {beat, by} (how far it moved). Off,
       as it is (to a thousandth). */
    function snapBeat(b) {
        if (Daw.P.snap_clips === false) return {beat: +b.toFixed(3), by: 0};
        const g = Daw.P.snap ? 1 / Daw.P.snap : 0.25;
        let best = Math.round(b / g) * g;
        if (Daw.song && Daw.songBpm()) {
            const at = Daw.songAt(), song = at + Math.round(b - at);       // the song's beats: a beat apart from its bar 1
            if (Math.abs(song - b) < Math.abs(best - b)) best = song;
        }
        return {beat: +best.toFixed(4), by: Math.abs(best - b)};
    }

    // ------------------------------------------------------------ the window --
    //
    // A long edit is thousands of things - its cuts and markers, the lines down the tracks
    // from them, the timeline's clips, the bar numbers - and each is placed in beats, so each
    // zoom step lays every one out again. Only those near what's in view are made: from a
    // screen's width before it to two after (UI.win, in beats), made again as it scrolls or
    // zooms past them. (The waveforms are drawn as they come near too: strips().)

    function syncWindow(force = false) {
        if (!UI.grid || !px) return;
        const w = Math.max(200, UI.lanes.clientWidth - HEAD), s = UI.lanes.scrollLeft;
        const from = s / px, to = (s + w) / px;
        if (!force && UI.win && from >= UI.win.b0 && to <= UI.win.b1 && UI.win.px === px) return;
        UI.win = {b0: Math.max(0, (s - w) / px), b1: (s + 3 * w) / px, px};
        barNumbers();
        fillPicture();
        for (const [body, spans] of UI.editBodies) fillEditClips(body, spans);
    }
    const inWin = (a, z = a) => UI.win && z >= UI.win.b0 && a <= UI.win.b1;

    /* The bar numbers in the window: every bar, or every 4th or 8th when they'd crowd. */
    function barNumbers() {
        if (!UI.bars || !UI.win) return;
        const every = px * 4 < 26 ? (px * 4 < 10 ? 8 : 4) : 1;
        for (const n of UI.bars.querySelectorAll("span")) n.remove();
        const numbers = [], beats = Daw.projectBeats() + 16;
        const first = Math.max(0, Math.floor(UI.win.b0 / 4 / every) * every);
        for (let b = first; b * 4 < Math.min(beats, UI.win.b1 + 4); b += every) {
            numbers.push(el("span", {text: String(Daw.barOf(b * 4)), style: `left:${at(b * 4)}`, translate: "no"}));
        }
        UI.bars.prepend(...numbers);
    }

    /* The bars that play, while they're being chosen: lit, and the loop on. */
    function setRange(a, z) {
        const P = Daw.P;
        P.loop.start = a;
        P.loop.end = z;
        P.loop.on = true;
        syncRange();
        DawApp.syncBar();
    }

    /* Chosen: the playhead to their first bar (playing goes on from there), drawn everywhere. */
    function chosen() {
        DawApp.moveCursor(Daw.P.loop.start);
        Daw.changed("loop");
    }

    /* The lit bars in the ruler and down the tracks, and what they say ("Bars 5–8"). */
    function syncRange() {
        const P = Daw.P, on = Daw.loopOn();
        const left = P.loop.start * px, w = (P.loop.end - P.loop.start) * px;
        if (UI.range) {
            UI.range.style.left = `${left}px`;
            UI.range.style.width = `${w}px`;
            UI.range.classList.toggle("off", !on);
            const first = Daw.barOf(P.loop.start), last = Daw.barOf(P.loop.end - 1e-6);
            const whole = P.loop.start % 4 === 0 && P.loop.end % 4 === 0;
            // Not whole bars: bar.beat of its first beat and its last, as the display shows them.
            const at = beat => `${Daw.barOf(beat)}.${Math.floor(beat % 4) + 1}`;
            UI.rangeLabel.textContent = w < 70 ? "" : whole ? (first === last ? `${first}` : `${first}–${last}`)
                : `${at(P.loop.start)}–${at(P.loop.end - 1)}`;
        }
        if (UI.shade) {
            UI.shade.hidden = !on;
            UI.shade.style.left = `${HEAD + left}px`;
            UI.shade.style.width = `${w}px`;
        }
    }

    /* The picture lane: the edit in Resolve, to score to - each cut a tick, each marker a
       flag in its colour (as long as the marker is), and where the picture ends; faint
       lines down the tracks from each. Click one for the playhead there. Follow: the
       playhead goes where Resolve's is moved to while the DAW is stopped. */
    function pictureLane(width, pic) {
        const P = Daw.P;
        const body = el("div.body.pic", {style: "width:var(--w)"});
        UI.pic = {body, pic, most: width / px};
        body.addEventListener("pointerdown", e => {
            const at = e.target.dataset && e.target.dataset.beat;
            if (e.button !== 0 || at === undefined) return;
            e.preventDefault();
            DawApp.moveCursor(Math.max(0, Number(at)));
        });
        const follow = el("button.pic-follow", {type: "button", text: "Follow", "aria-pressed": String(!!P.follow_resolve),
            title: "Move the playhead with Resolve's while the DAW is stopped",
            onclick: () => { P.follow_resolve = !P.follow_resolve; Daw.changed("picture"); }});
        const head = el("div.head", {}, [el("span.daw-label", {text: "Picture"}), follow]);
        return el("div.lane.picture", {}, [head, body]);
    }

    /* The picture lane's cuts, markers and picture end in the window, and their lines down
       the tracks. */
    function fillPicture() {
        if (!UI.pic || !UI.grid.contains(UI.pic.body)) return UI.guides.replaceChildren();
        const {body, pic, most} = UI.pic, tc = frame => DawApp.timecode(frame);
        const show = (a, z = a) => a >= 0 && a <= most && inWin(a, z);
        const items = [], guides = [];
        for (const c of pic.cuts) {
            if (!show(c.beat)) continue;
            items.push(el("i.cut", {style: `left:${at(c.beat)}`, title: `${Buddy.t("Cut in the edit")} · ${tc(c.frame)}`,
                translate: "no", dataset: {beat: String(c.beat)}}));
            guides.push(el("i.guide", {style: `left:calc(${HEAD}px + ${at(c.beat)})`}));
        }
        for (const m of pic.markers) {
            if (!show(m.beat, m.end)) continue;
            items.push(el("i.mark", {style: `left:${at(m.beat)};width:max(9px, ${at(m.end - m.beat)});--mark:${m.color}`,
                title: `${m.name || Buddy.t("Marker")} · ${tc(m.frame)}`, translate: "no", dataset: {beat: String(m.beat)}}));
            guides.push(el("i.guide.marked", {style: `left:calc(${HEAD}px + ${at(m.beat)});--mark:${m.color}`}));
        }
        if (pic.end !== null && show(Math.max(0, pic.end))) {
            items.push(el("i.pic-end", {style: `left:${at(Math.max(0, pic.end))}`, title: Buddy.t("The picture ends here"),
                dataset: {beat: String(Math.max(0, pic.end))}}));
        }
        body.replaceChildren(...items);
        UI.guides.replaceChildren(...guides);
    }

    function songLane(width) {
        const S = Daw.song, P = Daw.P;
        const body = el("div.body", {style: "width:var(--w)"});
        body.addEventListener("pointerdown", e => {
            if (e.button === 0) DawApp.moveCursor(Math.max(0, Math.round((e.clientX - body.getBoundingClientRect().left) / px)));
        });
        strips(body, width / px, paintSong);
        const head = el("div.head", {}, [
            el("div.tn", {}, [el("i", {style: "background:var(--daw-song)"}), el("span", {text: S.name, translate: "no", title: S.name})]),
            el("button.sub.song-tempo", {type: "button", translate: "no", title: "Correct the song's tempo",
                text: `${S.key ? S.key.name : Buddy.t("No key heard")} · ${Daw.songBpm() ? `${Math.round(Daw.songBpm() * 10) / 10} BPM` : Buddy.t("No steady beat")}`,
                onclick: () => DawApp.openTempo()}),
            el("div.ms", {translate: "no"}, [el("button.ms-m", {type: "button", text: "M", title: "Mute the song", "aria-pressed": String(!!P.song_mute),
                onclick: () => Daw.edit("Mute the song", () => { P.song_mute = !P.song_mute; }, "mixer")})])]);
        return el("div.lane.song", {}, [head, body]);
    }

    /* A wide picture along a lane - a waveform - as canvases STRIP_PX wide, each drawn as
       it scrolls near: a long edit close up is far wider than one canvas can be. `beats`
       long; paint(g, b0, w, h) draws the w pixels from beat b0. Drawn again at a new zoom
       (crisp()). */
    const STRIP_PX = 2048;
    function strips(holder, beats, paint) {
        for (const c of holder.querySelectorAll(":scope > canvas.wave")) { UI.seen.unobserve(c); c.remove(); }
        if (!UI.waves.some(x => x[0] === holder)) UI.waves.push([holder, beats, paint]);
        const per = STRIP_PX / px;
        for (let b0 = 0; b0 < beats; b0 += per) {
            const w = Math.max(1, Math.ceil(Math.min(per, beats - b0) * px));
            const canvas = el("canvas.wave", {style: `left:${at(b0)};width:${at(w / px)}`});
            canvas.paint = () => {
                const h = holder.clientHeight || 40, dpr = window.devicePixelRatio || 1;
                canvas.width = Math.round(w * dpr);
                canvas.height = Math.round(h * dpr);
                const g = canvas.getContext("2d");
                g.setTransform(dpr, 0, 0, dpr, 0, 0);
                g.fillStyle = getComputedStyle(canvas).color || "#8d9099";
                paint(g, b0, w, h);
            };
            holder.append(canvas);
            UI.seen.observe(canvas);
        }
    }

    /* The song's waveform: its loudest moment at each pixel, on the bars. */
    function paintSong(g, b0, w, h) {
        const S = Daw.song;
        if (!S || !S.wave) return;
        const per = S.wave_per_s || 50;
        for (let x = 0; x < w; x += 2) {
            const i = Math.floor(Daw.songTime(b0 + x / px) * per);
            if (i < 0 || i >= S.wave.length) continue;
            let peak = 0;
            const j = Math.max(i + 1, Math.floor(Daw.songTime(b0 + (x + 2) / px) * per));
            for (let k = i; k < Math.min(j, S.wave.length); k++) peak = Math.max(peak, S.wave[k]);
            const a = peak / 255 * (h / 2 - 3);
            g.fillRect(x, h / 2 - a, 1.4, Math.max(1, a * 2));
        }
    }

    /* The timeline's audio: a heading to show or hide it, then a lane for each audio track of
       the edit - its clips where they are, their names and waveforms. To write around (what's
       said, the effects, other music); it isn't played. Click for the playhead there. */
    function editAudio(pic) {
        const P = Daw.P, shown = P.edit_audio !== false;
        const n = pic.audio.length;
        const toggle = el("button.edit-toggle", {type: "button", "aria-expanded": String(shown),
            title: shown ? "Hide the timeline's audio" : "Show the timeline's audio",
            onclick: () => { P.edit_audio = !shown; Daw.changed("audio"); }}, [
            el("i.edit-caret"), el("span", {text: "Timeline audio"})]);
        const heading = el("div.lane.edit-head", {}, [
            el("div.head", {}, [toggle]),
            el("div.body", {style: "width:var(--w)"}, [el("span.edit-count", {translate: "no",
                text: `${n} ${Buddy.t(n === 1 ? "track" : "tracks")}`})])]);
        if (!shown) return [heading];
        return [heading, ...pic.audio.map(track => editLane(track, pic))];
    }

    function editLane(track, pic) {
        const body = el("div.body", {style: "width:var(--w)"});
        // Each clip's beats, once (the window fills in their boxes).
        const spans = track.clips.map(c => [c, Math.max(0, Daw.beatOfFrame(c.start)), Daw.beatOfFrame(c.end)]).filter(x => x[2] > x[1]);
        UI.editBodies.push([body, spans]);
        body.addEventListener("pointerdown", e => {
            if (e.button === 0) DawApp.moveCursor(Math.max(0, Math.round((e.clientX - body.getBoundingClientRect().left) / px)));
        });
        strips(body, Daw.projectBeats() + 16, paintClips(track, pic.fps));
        const head = el("div.head", {}, [el("div.tn", {}, [el("b.edit-track", {translate: "no", text: `A${track.index}`}),
            el("span", {text: track.name, translate: "no", title: track.name})])]);
        return el("div.lane.edit-audio", {}, [head, body]);
    }

    /* A timeline track's clips in the window: boxes with their names (the waveforms are
       painted under them). */
    function fillEditClips(body, spans) {
        for (const n of body.querySelectorAll(":scope > .eclip")) n.remove();
        const song = Daw.song ? Daw.song.id : null, boxes = [];
        for (const [c, a, z] of spans) {
            if (!inWin(a, z)) continue;
            boxes.push(el(`div.eclip${c.enabled === false ? ".off" : ""}${c.id === song ? ".is-song" : ""}`, {
                style: `left:${at(a)};width:${at(z - a)}` + (c.color ? `;--clip:${c.color}` : ""),
                title: `${c.name} · ${DawApp.timecode(c.start)}`, translate: "no"}, [el("span", {text: c.name})]));
        }
        body.prepend(...boxes);
    }

    /* A track's clips' waveforms: each pixel's moment of the edit, as a moment of the clip's
       file - the loudest there (dB, from page.py's peaks). */
    function paintClips(track, fps) {
        return (g, b0, w, h) => {
            for (const c of track.clips) {
                const wave = DawApp.peaks(c.peaks);
                if (!wave) continue;
                const x0 = Math.max(0, Math.floor((Daw.beatOfFrame(c.start) - b0) * px));
                const x1 = Math.min(w, Math.ceil((Daw.beatOfFrame(c.end) - b0) * px));
                const second = x => c.offset + (Daw.frameOfBeat(b0 + x / px) - c.start) / fps;
                for (let x = x0; x < x1; x += 2) {
                    const i = Math.floor(second(x) * wave.rate);
                    const j = Math.min(wave.codes.length, Math.max(i + 1, Math.floor(second(x + 2) * wave.rate)));
                    let peak = 0;
                    for (let k = Math.max(0, i); k < j; k++) peak = Math.max(peak, wave.codes[k]);
                    const a = peak / 255 * (h / 2 - 3);
                    if (a > 0.3) g.fillRect(x, h / 2 - a, 1.4, a * 2);
                }
            }
        };
    }

    function lane(t, width) {
        const P = Daw.P, color = Parts.color(t), sel = P.sel.track === t.id;
        const name = el("span", {text: t.name, translate: "no", title: t.name});
        const head = el("div.head", {tabindex: "0",
            onclick: e => { if (!e.target.closest("button")) Daw.select(t.id); },
            ondblclick: e => { if (!e.target.closest("button")) rename(t, name); },
            oncontextmenu: e => { e.preventDefault(); trackMenu(t, name, e); }}, [
            el("div.tn", {}, [el("i", {style: `background:${color}`}), name]),
            el("div.sub", {text: t.off ? Buddy.t("Disabled") : Parts.soundInfo(t).label}),
            el("div.ms", {translate: "no"}, [
                el("button.ms-m", {type: "button", text: "M", title: "Mute", "aria-pressed": String(!!t.mute),
                    onclick: () => Daw.edit(t.mute ? "Unmute" : "Mute", () => { t.mute = !t.mute; }, "mixer")}),
                el("button.ms-s", {type: "button", text: "S", title: "Solo", "aria-pressed": String(!!t.solo),
                    onclick: () => Daw.edit(t.solo ? "Unsolo" : "Solo", () => { t.solo = !t.solo; }, "mixer")})])]);
        const body = el("div.body", {style: "width:var(--w)"});
        for (const c of t.clips) body.append(clipNode(t, c));
        body.addEventListener("pointerdown", e => {
            if (e.button !== 0 || e.target !== body) return;
            Daw.select(t.id, "");
            DawApp.moveCursor(Math.max(0, Math.round((e.clientX - body.getBoundingClientRect().left) / px)));
        });
        body.addEventListener("contextmenu", e => {
            if (e.target !== body) return;
            e.preventDefault();
            const at = (e.clientX - body.getBoundingClientRect().left) / px;
            Daw.select(t.id, "");
            Buddy.menu({x: e.clientX, y: e.clientY, items: [
                {label: "Paste here", disabled: !copied, onClick: () => pasteClip(t, at)},
            ]});
        });
        body.addEventListener("dblclick", e => {
            if (e.target !== body) return;
            const bar = Math.floor((e.clientX - body.getBoundingClientRect().left) / px / 4) * 4;
            if (t.clips.some(c => bar < c.start + c.len && bar + 4 > c.start)) return;
            Daw.edit("Add a clip", () => {
                const c = {id: Daw.uid(), name: t.kind === "drums" ? "Beat" : "Clip", start: bar, len: 4, notes: []};
                t.clips.push(c);
                t.clips.sort((a, b) => a.start - b.start);
                P.sel.track = t.id;
                P.sel.clip = c.id;
            }, "clips");
        });
        const node = el(`div.lane${sel ? ".sel" : ""}${Daw.audible(t) ? "" : ".quiet"}${t.off ? ".off" : ""}`, {dataset: {track: t.id}}, [head, body]);
        node.style.setProperty("--track", color);
        node.addEventListener("dragover", e => { if (e.dataTransfer.types.includes("text/x-daw-sound")) { e.preventDefault(); node.classList.add("drop"); } });
        node.addEventListener("dragleave", () => node.classList.remove("drop"));
        node.addEventListener("drop", e => {
            node.classList.remove("drop");
            const raw = e.dataTransfer.getData("text/x-daw-sound");
            if (!raw) return;
            e.preventDefault();
            const {kind, name: sound} = JSON.parse(raw);
            Daw.select(t.id);
            give(kind, sound, t);
        });
        return node;
    }

    function rename(t, nameNode) {
        const input = el("input.field.rename", {type: "text", value: t.name, maxlength: "40"});
        nameNode.replaceWith(input);
        input.select();
        let done = false;
        const finish = ok => {
            if (done) return;
            done = true;
            const v = input.value.trim();
            if (ok && v && v !== t.name) Daw.edit("Rename a track", () => { t.name = v.slice(0, 40); }, "tracks");
            else input.replaceWith(nameNode);
        };
        input.addEventListener("keydown", e => {
            e.stopPropagation();
            if (e.key === "Enter") finish(true);
            if (e.key === "Escape") finish(false);
        });
        input.addEventListener("blur", () => finish(true));
    }

    function trackMenu(t, nameNode, e) {
        const P = Daw.P;
        Buddy.menu({x: e.clientX, y: e.clientY, items: [
            {label: "Rename", onClick: () => rename(t, nameNode)},
            {label: "Duplicate the track", onClick: () => Daw.edit("Duplicate a track", () => {
                const copy = JSON.parse(JSON.stringify(t));
                copy.id = Daw.uid();
                copy.name = `${t.name} 2`.slice(0, 40);
                copy.solo = false;
                for (const c of copy.clips) c.id = Daw.uid();
                P.tracks.splice(P.tracks.indexOf(t) + 1, 0, copy);
                P.sel.track = copy.id;
            }, "tracks")},
            {label: "Edit in Console", onClick: () => DawApp.openClip(t.id, (t.clips[0] || {}).id || "")},
            {label: t.off ? "Enable the track" : "Disable the track", onClick: () => Daw.edit(t.off ? "Enable a track" : "Disable a track",
                () => { t.off = !t.off; }, "tracks")},
            {sep: true},
            {label: "Delete the track", danger: true, disabled: P.tracks.length <= 1, onClick: () => Daw.edit("Delete a track", () => {
                P.tracks = P.tracks.filter(x => x !== t);
                if (P.sel.track === t.id) { P.sel.track = (P.tracks[0] || {}).id || ""; P.sel.clip = ""; }
            }, "tracks")},
        ]});
    }

    function clipNode(t, c) {
        const P = Daw.P, sel = P.sel.clip === c.id;
        const canvas = el("canvas");
        UI.clipCanvases.push([canvas, t, c]);
        const node = el(`div.clip${sel ? ".sel" : ""}${c.off ? ".off" : ""}`, {dataset: {clip: c.id},
            title: Buddy.t(c.off ? "Disabled – not played or placed (D)" : "Double-click to edit its notes")}, [
            el("span.ct", {text: c.name, translate: "no"}), canvas, el("i.grip")]);
        node.style.left = at(c.start);
        node.style.width = `max(6px, calc(${at(c.len)} - 1px))`;
        requestAnimationFrame(() => drawClip(canvas, t, c));
        let drag = null;
        node.addEventListener("pointerdown", e => {
            if (e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
            Daw.select(t.id, c.id);
            const edge = e.target.classList.contains("grip");
            drag = {x: e.clientX, y: e.clientY, start: c.start, len: c.len, edge, moved: false, copy: e.altKey, into: t};
            node.setPointerCapture(e.pointerId);
        });
        node.addEventListener("pointermove", e => {
            if (!drag) return;
            if (!drag.moved && Math.abs(e.clientX - drag.x) < 4 && Math.abs(e.clientY - drag.y) < 6) return;
            if (!drag.moved) {
                drag.moved = true;
                Daw.checkpoint(drag.edge ? "Lengthen a clip" : drag.copy ? "Copy a clip" : "Move a clip");
                if (drag.copy && !drag.edge) {
                    const copy = JSON.parse(JSON.stringify(c));
                    copy.id = Daw.uid();
                    t.clips.push(copy);          // the copy stays where the clip was
                }
            }
            const raw = (e.clientX - drag.x) / px;
            if (drag.edge) {
                // Its end, snapped.
                c.len = Math.max(0.25, snapBeat(drag.start + drag.len + raw).beat - drag.start);
                node.style.width = `max(6px, calc(${at(c.len)} - 1px))`;
                return;
            }
            // Its start or its end, whichever lands nearer a line.
            const a = snapBeat(drag.start + raw), z = snapBeat(drag.start + drag.len + raw);
            c.start = Math.max(0, z.by < a.by ? +(z.beat - drag.len).toFixed(4) : a.beat);
            node.style.left = at(c.start);
            // Onto another lane of the same kind.
            const hit = document.elementFromPoint(e.clientX, e.clientY);
            const laneNode = hit && hit.closest && hit.closest(".lane[data-track]");
            const other = laneNode && Daw.track(laneNode.dataset.track);
            if (other && other !== drag.into && other.kind === t.kind) {
                drag.into.clips = drag.into.clips.filter(x => x !== c);
                other.clips.push(c);
                drag.into = other;
                laneNode.querySelector(".body").append(node);
            }
        });
        node.addEventListener("pointerup", () => {
            if (!drag) return;
            const moved = drag.moved, into = drag.into;
            drag = null;
            if (!moved) return;
            into.clips.sort((a, b) => a.start - b.start);
            c.notes = c.notes.filter(n => n[1] < c.len);
            P.sel.track = into.id;
            Daw.changed("clips");
        });
        node.addEventListener("dblclick", e => { e.stopPropagation(); DawApp.openClip(t.id, c.id); });
        node.addEventListener("contextmenu", e => {
            e.preventDefault();
            e.stopPropagation();
            Daw.select(t.id, c.id);
            Buddy.menu({x: e.clientX, y: e.clientY, items: [
                {label: "Loop these bars", onClick: () => {
                    Daw.checkpoint("Choose the bars to loop");
                    setRange(c.start, c.start + c.len);
                    chosen();
                }},
                {label: "Edit in Console", onClick: () => DawApp.openClip(t.id, c.id)},
                {label: c.off ? "Enable the clip" : "Disable the clip", onClick: () => disableClip(c)},
                {sep: true},
                {label: "Copy", onClick: () => copyClip(t, c, false)},
                {label: "Cut", onClick: () => copyClip(t, c, true)},
                {label: "Duplicate the clip", onClick: () => keydown({key: "", code: "KeyD", ctrlKey: true})},
                {label: "Delete the clip", danger: true, onClick: () => keydown({key: "Delete", code: "Delete"})},
            ]});
        });
        return node;
    }

    /* A clip's notes, small: a drum clip's rows, an instrument's pitches. */
    function drawClip(canvas, t, c) {
        if (!canvas.isConnected) return;
        const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
        if (!w || !h) return;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        const g = canvas.getContext("2d");
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.fillStyle = "rgba(0,0,0,.6)";
        if (!c.notes.length) return;
        const scaleX = w / c.len;
        if (t.kind === "drums") {
            const rh = h / Drums.ROWS.length;
            for (const [m, s] of c.notes) {
                const row = Drums.rowOf(m);
                if (row >= 0) g.fillRect(s * scaleX, row * rh + 0.5, Math.max(1.5, scaleX * 0.2), Math.max(1, rh - 1));
            }
            return;
        }
        const ms = c.notes.map(n => n[0]), lo = Math.min(...ms) - 1, hi = Math.max(...ms) + 1, rh = h / Math.max(8, hi - lo + 1);
        for (const [m, s, d] of c.notes) g.fillRect(s * scaleX, h - (m - lo + 1) * rh, Math.max(1.5, d * scaleX - 1), Math.max(1.5, rh - 0.5));
    }

    // ------------------------------------------------------------ mixer --

    function drawMixer() {
        UI.strips = [];
        const nodes = [];
        if (Daw.song) { const s = Parts.songStrip(); nodes.push(s.node); }
        for (const t of Daw.P.tracks) { const s = Parts.strip(t); UI.strips.push(s); nodes.push(s.node); }
        const m = Parts.masterStrip();
        UI.strips.push(m);
        nodes.push(m.node);
        UI.mixer.replaceChildren(...nodes);
    }

    /* Mute, solo and selection, in place - a strip being dragged stays. */
    function syncMixer() {
        for (const t of Daw.P.tracks) {
            for (const scope of [UI.mixer, UI.lanes]) {
                const node = scope.querySelector(`[data-track="${t.id}"]`);
                if (!node) continue;
                node.classList.toggle("quiet", !Daw.audible(t));
                node.classList.toggle("sel", Daw.P.sel.track === t.id);
                const m = node.querySelector(".ms-m"), s = node.querySelector(".ms-s");
                if (m) m.setAttribute("aria-pressed", String(!!t.mute));
                if (s) s.setAttribute("aria-pressed", String(!!t.solo));
            }
        }
        for (const b of root.querySelectorAll(".song .ms-m")) b.setAttribute("aria-pressed", String(!!Daw.P.song_mute));
    }

    // ------------------------------------------------------------ view --

    function refresh(what) {
        if (!UI) return;
        if (what === "values") return;
        if (what === "mixer") return syncMixer();
        if (what === "select") {
            syncMixer();
            for (const n of UI.lanes.querySelectorAll(".clip")) n.classList.toggle("sel", n.dataset.clip === Daw.P.sel.clip);
            return drawBrowser();
        }
        if (what === "cursor") {
            placeHead(null);
            // Moved out of sight (the arrow keys, Home): brought back into view, a third in.
            // Never while it's in view - a click or drag in the ruler mustn't move the ruler.
            const x = HEAD + Daw.P.cursor * px, view = UI.lanes;
            if (!Daw.playing() && (x < view.scrollLeft + HEAD || x > view.scrollLeft + view.clientWidth - 2)) {
                view.scrollLeft = Math.max(0, x - HEAD - (view.clientWidth - HEAD) / 3);
            }
            return;
        }
        if (what === "notes" || what === "clips" || what === "loop" || what === "picture" || what === "tempo" || what === "audio") {
            const left = UI.lanes.scrollLeft, top = UI.lanes.scrollTop;
            drawLanes();
            UI.lanes.scrollLeft = left;
            UI.lanes.scrollTop = top;
            return;
        }
        const left = UI.lanes.scrollLeft, top = UI.lanes.scrollTop;
        if (what === "song") px = 0;
        drawLanes();
        UI.lanes.scrollLeft = left;
        UI.lanes.scrollTop = top;
        drawBrowser();
        drawMixer();
    }

    function frame(pos) {
        if (!UI || !UI.playhead) return;
        placeHead(pos);
        UI.playhead.classList.toggle("moving", pos !== null && Daw.playing());
        if (pos !== null && Daw.playing()) showHead(pos);
        for (const s of UI.strips || []) if (s.meter) s.meter.set(Daw.level(s.id));
    }

    function shown() {
        if (!UI || !UI.unfitted) return;
        UI.unfitted = false;
        px = 0;
        refresh("all");
    }

    /* A clip disabled (not played, rendered or placed) or enabled again. */
    function disableClip(c) {
        Daw.edit(c.off ? "Enable a clip" : "Disable a clip", () => { if (c.off) delete c.off; else c.off = true; }, "clips");
    }

    // ------------------------------------------------------------ copy and paste --
    //
    // One clip copied at a time (kept while Buddy's open, across the layouts): pasted on the
    // selected track at the playhead - or, when that track is the other kind (a beat on an
    // instrument), on the track it came from, else the first of its kind.

    let copied = null;                            // {clip, kind, from}

    function copyClip(t, c, cut) {
        copied = {clip: JSON.parse(JSON.stringify(c)), kind: t.kind, from: t.id};
        if (cut) Daw.edit("Cut a clip", () => { t.clips = t.clips.filter(x => x !== c); Daw.P.sel.clip = ""; }, "clips");
        else Buddy.toast(Buddy.t(t.kind === "drums" ? "Beat copied" : "Clip copied"), 900);
    }

    /* The copied clip onto track t (or one of its kind) from beat `at`: a clip it would
       overlap makes way, as for Duplicate. The playhead goes to its end, so pasting again
       puts the next one straight after it. */
    function pasteClip(t, at) {
        if (!copied) return Buddy.toast(Buddy.keys(Buddy.t("Copy a clip first (Ctrl+C).")), 2500);
        const P = Daw.P, from = Daw.track(copied.from);
        const into = t && t.kind === copied.kind ? t
            : from && from.kind === copied.kind ? from : P.tracks.find(x => x.kind === copied.kind);
        if (!into) {
            return Buddy.toast(Buddy.t(copied.kind === "drums" ? "A beat is pasted on a drum track – add one first."
                : "Notes are pasted on an instrument track – add one first."), 3500);
        }
        const clip = JSON.parse(JSON.stringify(copied.clip));
        clip.id = Daw.uid();
        clip.start = snapBeat(Math.max(0, at)).beat;
        Daw.edit("Paste a clip", () => {
            into.clips = into.clips.filter(x => x.start + x.len <= clip.start || x.start >= clip.start + clip.len);
            into.clips.push(clip);
            into.clips.sort((a, b) => a.start - b.start);
            P.sel.track = into.id;
            P.sel.clip = clip.id;
        }, "clips");
        DawApp.moveCursor(clip.start + clip.len, false);
        if (t && into !== t) Buddy.toast(`${Buddy.t("Pasted on")} ${into.name}`, 2000);
    }

    /* Keys this layout takes: N (snapping); Ctrl+V (paste at the playhead); Delete, Ctrl+C,
       Ctrl+X, Ctrl+D and - with the tracks focused - D on the selected clip. */
    function keydown(e) {
        const plain = !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey;
        const ctrl = (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey;
        if (plain && e.code === "KeyN") { if (!e.repeat) toggleSnap(); return true; }
        const t = Daw.selTrack(), c = Daw.selClip();
        if (ctrl && e.code === "KeyV") {
            pasteClip(t, Daw.playing() ? Daw.position() : Daw.P.cursor);
            return true;
        }
        if (!t || !c) return false;
        if (ctrl && (e.code === "KeyC" || e.code === "KeyX")) {
            if (!e.repeat) copyClip(t, c, e.code === "KeyX");
            return true;
        }
        if (plain && e.code === "KeyD" && UI.armed) {
            if (!e.repeat) disableClip(c);
            return true;
        }
        if (e.key === "Delete" || e.key === "Backspace") {
            Daw.edit("Delete a clip", () => { t.clips = t.clips.filter(x => x !== c); Daw.P.sel.clip = ""; }, "clips");
            return true;
        }
        if ((e.ctrlKey || e.metaKey) && e.code === "KeyD") {
            Daw.edit("Duplicate a clip", () => {
                const copy = JSON.parse(JSON.stringify(c));
                copy.id = Daw.uid();
                copy.start = c.start + c.len;
                t.clips = t.clips.filter(x => x.start + x.len <= copy.start || x.start >= copy.start + copy.len);
                t.clips.push(copy);
                t.clips.sort((a, b) => a.start - b.start);
                Daw.P.sel.clip = copy.id;
            }, "clips");
            return true;
        }
        return false;
    }

    return {id: "arrange", label: "Arrange", build, refresh, frame, shown, keydown};
})();
