/*
 * Arrange: the whole piece at once - a browser of sounds on the left, the
 * tracks over the song lane, and the mixer along the bottom. For building a
 * cue with several parts. A track's knobs are in Console (double-click a clip,
 * or "Edit in Console" in the track's menu).
 *
 *   Tracks    click a clip to select it, drag to move it (Shift: by a
 *             sixteenth, not a beat; Alt: a copy), drag its right end to
 *             lengthen it, double-click it to edit its notes in Console;
 *             double-click an empty stretch for a new clip; Delete and
 *             Ctrl+D on the selected clip. A track's menu (right-click its
 *             name): rename, duplicate, delete
 *   Ruler     two rows. The loop strip on top: drag across it to loop just
 *             those bars (lit down the tracks, the playhead to their start),
 *             drag the lit bars to slide them or an end for more or fewer,
 *             click them to turn the loop on or off, double-click a bar for
 *             just that one. The bar numbers under it: click to move the
 *             playhead, drag to scrub. A clip's menu (right-click) loops its bars
 *   Picture   under the ruler, with a timeline open: the edit in Resolve - its
 *             cuts, its markers in their colours, where the picture ends - with
 *             faint lines down the tracks, to write music to. Click one for the
 *             playhead there. Follow: the playhead goes where Resolve's is moved
 *             to while the DAW is stopped
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
        UI.zoom = el("div.zoom", {}, [
            el("button.btn.ghost.icon", {type: "button", title: "Zoom out (Alt + scroll)", onclick: () => zoom(1 / 1.4)}, [icon("minus")]),
            el("button.btn.ghost", {type: "button", text: "Fit", title: "The whole piece in view", onclick: () => fitAll()}),
            el("button.btn.ghost.icon", {type: "button", title: "Zoom in (Alt + scroll)", onclick: () => zoom(1.4)}, [icon("plus")])]);
        root.append(el("div.arr", {}, [UI.browser, el("div.arr-mid", {}, [UI.lanes, UI.zoom]), UI.mixer]));
        UI.lanes.addEventListener("wheel", wheel, {passive: false});
        UI.lanes.addEventListener("scroll", () => placeHead(Daw.playing() ? Daw.position() : null));
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
        clearTimeout(UI.settle);
        UI.settle = setTimeout(crisp, ZOOM_SETTLE_MS);
    }

    /* Zooming settled: what's drawn at a size drawn again at the new one, in place - the
       waveform, each clip's notes, as many bar numbers as fit. Nothing else is rebuilt. */
    function crisp() {
        if (!UI.grid || !UI.grid.isConnected) return;
        barNumbers();
        if (UI.wave) drawWave(UI.wave, (Daw.projectBeats() + 16) * px);
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
            return zoomSoon(Math.pow(1.0015, -pxStep), e.clientX);
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
        const group = (label, items) => {
            items = items.filter(Boolean);
            return items.length ? el("div.bgroup", {}, [el("span.daw-label", {text: label}), ...items]) : null;
        };
        UI.browserList.replaceChildren(...[
            group("Instruments", Daw.INSTRUMENTS.map(n => item("inst", n, Instruments.KINDS[n]))),
            group("Drum kits", Daw.KITS.map(n => item("drums", n, Drums.KITS[n]))),
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
        UI.wave = null;
        UI.clipCanvases = [];
        const rows = [ruler(width, beats)];
        const pic = picture();
        UI.guides = el("div.pic-guides");
        if (pic) rows.push(pictureLane(width, pic));
        if (Daw.song) rows.push(songLane(width));
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
        const strip = el("div.loopbar", {title: Buddy.t("Drag across to choose the bars that loop")});
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
        const step = e => (e.shiftKey ? 1 : 4);
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
            const b = beatAt(e), g = step(e);
            if (drag.kind === "new") {
                // From the bar it began in to the bar it's in now, whichever way it went.
                const a = Math.floor(Math.min(drag.from, b) / g) * g;
                setRange(a, Math.max(a + g, Math.ceil(Math.max(drag.from, b) / g) * g));
            }
            if (drag.kind === "slide") {
                const by = Math.max(-drag.start, Math.round((b - drag.from) / g) * g);
                setRange(drag.start + by, drag.end + by);
            }
            if (drag.kind === "edge") {
                const at = Math.round(b / g) * g;
                if (drag.edge === "start") setRange(Math.max(0, Math.min(at, P.loop.end - g)), P.loop.end);
                else setRange(P.loop.start, Math.max(at, P.loop.start + g));
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
        return el("div.lane.ruler", {}, [el("div.head", {}, [el("span.daw-label", {text: "Loop"}), el("span.daw-label", {text: "Bars"})]), time]);
    }

    /* The bar numbers: every bar, or every 4th or 8th when they'd crowd. */
    function barNumbers(beats = Daw.projectBeats() + 16) {
        const every = px * 4 < 26 ? (px * 4 < 10 ? 8 : 4) : 1;
        if (!UI.bars || UI.barsEvery === every && UI.barsBeats === beats) return;
        UI.barsEvery = every;
        UI.barsBeats = beats;
        for (const n of UI.bars.querySelectorAll("span")) n.remove();
        const numbers = [];
        for (let b = 0; b * 4 < beats; b += every) numbers.push(el("span", {text: String(b + 1), style: `left:${at(b * 4)}`, translate: "no"}));
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
            const first = Math.floor(P.loop.start / 4) + 1, last = Math.ceil(P.loop.end / 4);
            const whole = P.loop.start % 4 === 0 && P.loop.end % 4 === 0;
            // Not whole bars (chosen with Shift): bar.beat of its first beat and its last, as the display shows them.
            const at = beat => `${Math.floor(beat / 4) + 1}.${Math.floor(beat % 4) + 1}`;
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
        const P = Daw.P, most = width / px;
        const body = el("div.body.pic", {style: "width:var(--w)"});
        const guides = [];
        const tc = frame => DawApp.timecode(frame);
        const inView = beat => beat >= 0 && beat <= most;
        for (const c of pic.cuts) {
            if (!inView(c.beat)) continue;
            body.append(el("i.cut", {style: `left:${at(c.beat)}`, title: `${Buddy.t("Cut in the edit")} · ${tc(c.frame)}`,
                translate: "no", dataset: {beat: String(c.beat)}}));
            guides.push(el("i.guide", {style: `left:calc(${HEAD}px + ${at(c.beat)})`}));
        }
        for (const m of pic.markers) {
            if (!inView(m.beat)) continue;
            const node = el("i.mark", {style: `left:${at(m.beat)};width:max(9px, ${at(m.end - m.beat)});--mark:${m.color}`,
                title: `${m.name || Buddy.t("Marker")} · ${tc(m.frame)}`, translate: "no", dataset: {beat: String(m.beat)}});
            body.append(node);
            guides.push(el("i.guide.marked", {style: `left:calc(${HEAD}px + ${at(m.beat)});--mark:${m.color}`}));
        }
        if (pic.end !== null && pic.end < most) {
            body.append(el("i.pic-end", {style: `left:${at(Math.max(0, pic.end))}`, title: Buddy.t("The picture ends here"),
                dataset: {beat: String(Math.max(0, pic.end))}}));
        }
        UI.guides.replaceChildren(...guides);
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

    function songLane(width) {
        const S = Daw.song, P = Daw.P;
        const canvas = el("canvas.wave");
        UI.wave = canvas;
        const body = el("div.body", {style: "width:var(--w)"}, [canvas]);
        body.addEventListener("pointerdown", e => {
            if (e.button === 0) DawApp.moveCursor(Math.max(0, Math.round((e.clientX - body.getBoundingClientRect().left) / px)));
        });
        requestAnimationFrame(() => drawWave(canvas, width));
        const head = el("div.head", {}, [
            el("div.tn", {}, [el("i", {style: "background:var(--daw-song)"}), el("span", {text: S.name, translate: "no", title: S.name})]),
            el("div.sub", {translate: "no", text: `${S.key ? S.key.name : Buddy.t("No key heard")} · ${Daw.songBpm() ? `${Math.round(Daw.songBpm())} BPM` : Buddy.t("No steady beat")}`}),
            el("div.ms", {translate: "no"}, [el("button.ms-m", {type: "button", text: "M", title: "Mute the song", "aria-pressed": String(!!P.song_mute),
                onclick: () => Daw.edit("Mute the song", () => { P.song_mute = !P.song_mute; }, "mixer")})])]);
        return el("div.lane.song", {}, [head, body]);
    }

    function drawWave(canvas, width) {
        const S = Daw.song;
        if (!S || !S.wave || !canvas.isConnected) return;
        const h = canvas.parentNode.clientHeight || 60, dpr = window.devicePixelRatio || 1;
        const w = Math.min(width, 16000);
        canvas.style.width = at(w / px);
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        const g = canvas.getContext("2d");
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.fillStyle = getComputedStyle(canvas).color || "#8d9099";
        const per = S.wave_per_s || 50;
        for (let x = 0; x < w; x += 2) {
            const t = Daw.songTime(x / px), i = Math.floor(t * per);
            if (i < 0 || i >= S.wave.length) continue;
            let peak = 0;
            const j = Math.max(i + 1, Math.floor(Daw.songTime((x + 2) / px) * per));
            for (let k = i; k < Math.min(j, S.wave.length); k++) peak = Math.max(peak, S.wave[k]);
            const a = peak / 255 * (h / 2 - 3);
            g.fillRect(x, h / 2 - a, 1.4, Math.max(1, a * 2));
        }
    }

    function lane(t, width) {
        const P = Daw.P, color = Parts.color(t), sel = P.sel.track === t.id;
        const name = el("span", {text: t.name, translate: "no", title: t.name});
        const head = el("div.head", {tabindex: "0",
            onclick: e => { if (!e.target.closest("button")) Daw.select(t.id); },
            ondblclick: e => { if (!e.target.closest("button")) rename(t, name); },
            oncontextmenu: e => { e.preventDefault(); trackMenu(t, name, e); }}, [
            el("div.tn", {}, [el("i", {style: `background:${color}`}), name]),
            el("div.sub", {text: Parts.soundInfo(t).label}),
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
        const node = el(`div.lane${sel ? ".sel" : ""}${Daw.audible(t) ? "" : ".quiet"}`, {dataset: {track: t.id}}, [head, body]);
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
        const node = el(`div.clip${sel ? ".sel" : ""}`, {dataset: {clip: c.id}, title: Buddy.t("Double-click to edit its notes")}, [
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
            const step = e.shiftKey ? 0.25 : 1;
            const db = Math.round((e.clientX - drag.x) / px / step) * step;
            if (drag.edge) {
                c.len = Math.max(step, drag.len + db);
                node.style.width = `max(6px, calc(${at(c.len)} - 1px))`;
                return;
            }
            c.start = Math.max(0, drag.start + db);
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
                {sep: true},
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
        if (what === "notes" || what === "clips" || what === "loop" || what === "picture") {
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

    /* Keys this layout takes: Delete and Ctrl+D on the selected clip. */
    function keydown(e) {
        const t = Daw.selTrack(), c = Daw.selClip();
        if (!t || !c) return false;
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
