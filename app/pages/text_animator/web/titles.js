/* Animation > Titles: a hook typed in, composed into a styled, animated title - and
   every part of it editable on the frame itself.

   Python composes (titles/studio.py: the layout, each word's style, the clock) and
   sends numbers; this draws them (titles_svg.js) and plays them (titles_motion.js),
   so playing, scrubbing and dragging never wait on Python. A change goes to Python at
   once - one request out at a time, the newest waiting replacing any older - and
   comes back as the next layout, a few ms later. A drag shows straight away on its
   own ("local": words moved, scaled or turned, bars moved, a curve being drawn) and
   is sent when it's let go; the local view stays up until Python's answer to it
   arrives, so nothing jumps.

   The frame: click a word (shift-click for more), drag it to nudge it, a corner to
   size it, the knob to turn it; drag the title's empty space to move the whole title
   (it snaps to the centre and the nine spots). The toolbar over the frame changes the
   clicked words only; the inspector's own changes go to what its Change says - those
   words, their lines, their role, or every word.

   The dock under it: a lane per word to retime it (drag its bar; its right edge
   lengthens its entrance), and the entrance's curves to reshape - the selected words'
   own, or the whole title's. It folds down to a strip (remembered). Loaded after
   animation.js (uses its $, el, send, state). */
"use strict";

const TI = {
    view: null,                 // the last "ti": layout numbers, styles, clock, title...
    side: {alternates: [], gallery: {}, mine: []},
    title: null,                // the title as edited here: the last "ti"'s plus what's waiting to go
    pending: {}, inFlight: false, seq: 0, sentSeq: 0, sentAt: 0, releaseWaiting: false,
    local: null,                // what a drag shows before Python's answer: see tiAdjust / tiClockWord
    sel: [], scope: "word", hover: null,
    T: 1.2, playing: 0, loop: true, lastTick: 0,
    ui: {dock: true, back: "dark", units: "s", askReset: true, expand: null, guides: {safe: true, thirds: false}}, uiLoaded: false,
    cvIo: "in", cvView: null, ch: "scale", shown: {alpha: true, scale: true, move: true, rot: true, blur: true}, cvSel: {},
    drag: null, inspBusy: false, stageScale: 0.5,
    undo: [], redo: [], undoKeys: "", undoAt: 0, merging: false,
};
const TI_KINDS = {pop: "Pop", slam: "Slam", slide: "Slide", blur: "Blur", type: "Type", fade: "Fade"};
const TI_ROLES = {normal: "Normal", key: "Key", accent: "Accent", small: "Small"};
const TI_ARRANGE = [["auto", "Look's"], ["center", "Centre"], ["left", "Left"], ["right", "Right"], ["stagger", "Stagger"], ["cascade", "Cascade"], ["kinetic", "Kinetic"]];
/* Kinetic: what a word's mark does - starts a block underneath, or turned (titles/kinetic.py):
   [mark, symbol, what it does, its name on a button]. */
const TI_MARKS = [["none", "·", "Carries on the block", "None"], ["line", "↓", "New block underneath", "Under"],
                  ["cw", "↻", "Turns clockwise", "Clockwise"], ["ccw", "↺", "Turns anticlockwise", "Anticlockwise"]];
const TI_MARK_ORDER = ["none", "line", "cw", "ccw"];
const TI_SPOTS = ["tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br"];
const TI_SWATCHES = ["#ffffff", "#FFD93D", "#39FF14", "#2ee6a6", "#3fd0ff", "#ff5fa2", "#ff9a3c", "#E64B3D", "#9d6bff", "#141414"];
const TI_CH = {
    scale: {label: "Scale", fmt: v => v.toFixed(2)},
    alpha: {label: "Opacity", fmt: v => `${Math.round(v * 100)}%`},
    move: {label: "Move", fmt: v => `${Math.round(v * 100)}%`},
    rot: {label: "Rotate", fmt: v => `${v.toFixed(0)}°`},
    blur: {label: "Blur", fmt: v => `${Math.round(v * 100)}%`},
};
const TI_ICON = {
    pop: '<circle cx="9" cy="6" r="2"/><circle cx="9" cy="6" r="5" opacity=".5"/>',
    slam: '<rect x="3" y="2" width="12" height="8" opacity=".45"/><rect x="6" y="4" width="6" height="4" fill="currentColor"/>',
    slide: '<path d="M2 6h10M9 3l3 3-3 3"/>',
    blur: '<path d="M3 3h12M3 6h12M3 9h12" opacity=".6"/>',
    type: '<path d="M3 3h6M6 3v6M12 2v8"/>',
    fade: '<rect x="2" y="3" width="4" height="6" fill="currentColor" opacity=".25"/><rect x="7" y="3" width="4" height="6" fill="currentColor" opacity=".55"/><rect x="12" y="3" width="4" height="6" fill="currentColor"/>',
    custom: '<path d="M2 10 Q6 -2 9 5 T16 3"/>',
    reverse: '<path d="M15 6H4M7 3L4 6l3 3"/>',
    off: '<path d="M4 6h10" opacity=".6"/>',
    look: '<rect x="2" y="2" width="14" height="8" rx="2"/>',
};
const tiEsc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const tiClamp = (v, a, b) => Math.max(a, Math.min(b, v));

/* Times as the person wants to read them: seconds, or frames at the timeline's rate. */
const tiFps = () => (TI.view && TI.view.fps) || 24;
const tiFrames = () => TI.ui.units === "f";
const tiTime = (sec, digits = 1) => tiFrames() ? `${Math.round(sec * tiFps())} f` : `${sec.toFixed(digits)} s`;
/* A length typed, stepped or dragged: whole frames, or tenths of a second - inside 1-10 s. */
function tiSnapLen(sec) {
    const fps = tiFps();
    return tiClamp(tiFrames() ? Math.round(sec * fps) / fps : Math.round(sec * 10) / 10, 1, 10);
}
const tiClone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));

/* A drag starting: the browser's own reading of it - selecting the page's text as the
   pointer sweeps over it - is turned off until it ends. */
function tiDragStart(e) {
    e.preventDefault();
    getSelection().removeAllRanges();
    document.documentElement.classList.add("ti-dragging");
    const end = () => {
        document.documentElement.classList.remove("ti-dragging");
        removeEventListener("pointerup", end);
        removeEventListener("pointercancel", end);
    };
    addEventListener("pointerup", end);
    addEventListener("pointercancel", end);
}

/* ===================================================== talking to Python */

/* A change to the title. Top-level keys go whole (a map like "over" in full), the
   newest waiting replacing any older; release: the end of a drag, whose local view
   stays up until Python's answer to it. */
function tiSend(change, release = false, undoable = true) {
    if (!TI.title) return;
    if (undoable) tiRemember(change);
    Object.assign(TI.title, change);
    Object.assign(TI.pending, change);
    if (release) TI.releaseWaiting = true;
    tiPump();
}

/* The title before a change, kept to go back to. Every drag, click and nudge is a step
   of its own; one continuous gesture (TI.merging: a slider held, typing, the length
   dragged) is one step, however many changes it sends. */
function tiRemember(change) {
    const keys = Object.keys(change).sort().join(), now = performance.now();
    if (!(TI.merging && keys === TI.undoKeys && now - TI.undoAt < 1500)) {
        TI.undo.push(tiClone(TI.title));
        if (TI.undo.length > 200) TI.undo.shift();
        TI.redo = [];
    }
    TI.undoKeys = TI.merging ? keys : "";
    TI.undoAt = now;
    tiUndoButtons();
}

/* A change sent while a continuous gesture goes on (see tiRemember). */
function tiMerged(fn) {
    TI.merging = true;
    try { fn(); } finally { TI.merging = false; }
}

function tiStep(from, to) {
    if (!from.length || !TI.title) return;
    to.push(tiClone(TI.title));
    const back = from.pop();
    TI.undoKeys = "";
    tiSend(back, false, false);
    tiUndoButtons();
}

function tiUndoButtons() {
    $("ti-undo").disabled = !TI.undo.length;
    $("ti-redo").disabled = !TI.redo.length;
}

function tiPump() {
    if (TI.inFlight && performance.now() - TI.sentAt < 3000) return;
    if (!Object.keys(TI.pending).length) return;
    const change = TI.pending;
    TI.pending = {};
    TI.seq += 1;
    TI.inFlight = true;
    TI.sentAt = performance.now();
    TI.sentSeq = TI.seq;
    if (TI.releaseWaiting && TI.local) TI.local.until = TI.seq;
    TI.releaseWaiting = false;
    send("ti_change", {change, seq: TI.seq});
}

/* The title's "over" with one of its maps changed for these words (null: back to the look's). */
function tiOver(name, ids, value) {
    const over = tiClone(TI.title.over);
    over[name] = {...(over[name] || {})};
    for (const id of ids) {
        const v = typeof value === "function" ? value(id, over[name][id]) : value;
        if (v === null || v === undefined) delete over[name][id]; else over[name][id] = v;
    }
    return over;
}

Buddy.on("ti", data => {
    if (data.seq != null && data.seq === TI.sentSeq) TI.inFlight = false;
    const before = tiCameraNow();
    TI.view = data;
    tiEaseCamera(before);
    TitlesMotion.setCurves(data.curves);
    TI.title = {...tiClone(data.title), ...tiClone(TI.pending)};
    if (TI.local && TI.local.until != null && (data.seq ?? -1) >= TI.local.until) TI.local = null;
    if (!TI.uiLoaded && data.ui) { TI.ui = data.ui; TI.uiLoaded = true; tiApplyUi(); }
    TI.sel = TI.sel.filter(id => data.boxes.some(b => b.id === id));
    if (TI.T > TI.title.seconds) TI.T = Math.min(TI.title.seconds, data.intro + 0.3);
    tiRenderAll();
    tiPump();
});

Buddy.on("ti_side", data => {
    Object.assign(TI.side, data);
    if (data.gallery || data.mine) tiLooks();
    tiSuggestions();
});

Buddy.on("state", () => tiPlace());

/* ============================================================ the model */

const tiBoxes = () => (TI.view ? TI.view.boxes : []);
const tiIndex = id => tiBoxes().findIndex(b => b.id === id);
const tiToken = id => (TI.view.tokens || []).find(t => t.id === id) || {};
const tiFirstOf = line => (TI.view.lines[line] || [])[0];
const tiLineBig = line => Math.max(...tiBoxes().filter(b => b.line === line).map(b => b.size));
const tiRoleColour = st => (st.gradient && st.gradient.stops ? st.gradient.stops[0][1]
    : st.fill && st.fill !== "none" ? st.fill : (st.strokes && st.strokes[0] && st.strokes[0].colour) || "#ffffff");
const tiLook = () => (TI.view.looks || []).find(l => l.id === TI.title.look) || {label: ""};
const tiKinetic = () => !!(TI.view && TI.view.kinetic);
const tiLineWord = () => (tiKinetic() ? "block" : "line");

/* The kinetic camera at t, by the clock - or, while it eases into a new framing
   (tiEaseCamera), on its way there. null for a title that isn't kinetic. */
function tiCamera(t = TI.T) {
    const v = TI.view;
    if (!v || !v.kinetic) return null;
    const cam = TitlesMotion.cameraOf({kinetic: v.kinetic, camera: v.clock.camera, t, frame: v.frame});
    const e = TI.camEase;
    if (!cam || !e || TI.playing) return cam;
    const u = (performance.now() - e.at) / TI_CAMERA_EASE;
    if (u >= 1) { TI.camEase = null; return cam; }
    return TitlesMotion.cameraAt([{t: 0, ...e.from}, {t: 1, ...cam}], u);
}
const tiCameraNow = () => tiCamera();

/* A new framing for the same moment - a word nudged, sized or turned, a mark changed:
   the camera eases there from where it was rather than jumping (not while playing,
   where the clock moves it). */
const TI_CAMERA_EASE = 350;     // ms
function tiEaseCamera(before) {
    const after = tiCamera();
    if (!before || !after || TI.playing) return;
    const moved = Math.hypot(after.cx - before.cx, after.cy - before.cy) * after.z > 0.5
        || Math.abs(after.z / before.z - 1) > 1e-3 || Math.abs(after.angle - before.angle) > 0.01;
    if (!moved) return;
    const running = !!TI.camEase;
    TI.camEase = {from: before, at: performance.now()};
    if (running) return;
    const step = () => {
        if (!TI.camEase || TI.playing) { TI.camEase = null; return; }
        tiDrawTitle();
        tiDrawOver();
        requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
}

/* The boxes in the frame's px at the playhead: a kinetic title's where the camera has
   them then (the overlay, the handles and drags work on these); any other's as they are. */
function tiScreen(t = TI.T) {
    const v = TI.view;
    if (!v || !v.kinetic) return v ? v.boxes : [];
    const cam = tiCamera(t);
    return cam ? v.boxes.map(b => TitlesMotion.screenBox(b, cam, v.frame)) : v.boxes;
}

/* The marks the person set for these words (null: back to Buddy's suggestion). */
function tiTurns(ids, mark) {
    const turns = {...(TI.title.turns || {})};
    for (const id of ids) { if (mark === null) delete turns[id]; else turns[id] = mark; }
    return turns;
}
const tiTitleKind = () => (TI.title.anim === "look" ? tiLook().anim || "pop" : TI.title.anim);

/* What the inspector's changes go to: the selected words, their lines, their role(s)
   or every word (the inspector's Change). */
function tiTargets() {
    const boxes = tiBoxes();
    if (!TI.sel.length) return boxes.map(b => b.id);
    const sel = boxes.filter(b => TI.sel.includes(b.id));
    if (TI.scope === "line") { const lines = new Set(sel.map(b => b.line)); return boxes.filter(b => lines.has(b.line)).map(b => b.id); }
    if (TI.scope === "role") { const roles = new Set(sel.map(b => b.role)); return boxes.filter(b => roles.has(b.role)).map(b => b.id); }
    if (TI.scope === "all") return boxes.map(b => b.id);
    return sel.map(b => b.id);
}

/* A box as a drag in progress shows it: moved, scaled about its centre, turned. */
function tiAdjust(b) {
    const L = TI.local;
    if (!L) return b;
    const dx = (L.dx && L.dx[b.id]) || 0, dy = (L.dy && L.dy[b.id]) || 0;
    const f = (L.f && L.f[b.id]) || 1, r = (L.r && L.r[b.id]) || 0;
    if (!dx && !dy && f === 1 && !r) return b;
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    return {...b, size: b.size * f, w: b.w * f, h: b.h * f, track: (b.track || 0) * f,
            x: cx - b.w * f / 2 + dx, y: cy - b.h * f / 2 + dy, baseline: cy + (b.baseline - cy) * f + dy,
            rot: (b.rot || 0) + r};
}

/* A word's clock as a drag in progress shows it: retimed, lengthened, redrawn. */
function tiClockWord(i) {
    const w = TI.view.clock.words[i];
    const L = TI.local;
    if (!L || !w) return w;
    let out = w;
    if (L.curve && L.curve.io === "out" && L.curve.ids.includes(w.id) && w.exit) {
        out = {...out, exit: {...w.exit, curve: L.curve.obj, len: L.curve.obj.len || w.exit.len}};
    } else if (L.curve && L.curve.ids.includes(w.id)) {
        out = {...out, curve: L.curve.obj, kind: "custom"};
        if (L.curve.obj.len) out.len = L.curve.obj.len;
    }
    if (L.start && L.start[w.id] != null) out = {...out, start: L.start[w.id]};
    if (L.len && L.len[w.id] != null) out = {...out, len: L.len[w.id]};
    if (w.exit && L.exit && L.exit[w.id]) out = {...out, exit: {...w.exit, ...L.exit[w.id]}};
    if (out !== w && w.spread) out.spread = {...w.spread, start: out.start + 0.5 * out.len};
    return out;
}

function tiCase(t) {
    const v = TI.view;
    const boxes = tiScreen(t).map(tiAdjust);
    let bars = v.bars;
    if (TI.local && TI.local.block) bars = bars.map(b => ({...b, x: b.x + TI.local.block[0], y: b.y + TI.local.block[1]}));
    const states = v.boxes.map((b, i) => TitlesMotion.state(tiClockWord(i), t));
    return {frame: v.frame, boxes, styles: v.styles, bars, states, outScale: TI.stageScale, id: "tim", screen: true};
}

/* ================================================================ frame */

/* Every size a redraw needs, read once before anything's written: reading one after a
   write makes the browser lay the page out again, and each part doing that cost more
   than the drawing. */
function tiMeasure() {
    const wrap = $("ti-stage-wrap"), cv = $("ti-curves");
    TI.size = {wrapW: wrap.clientWidth, wrapH: wrap.clientHeight, rowsW: $("ti-rows").clientWidth,
               cvW: cv.clientWidth, cvH: cv.clientHeight};
}

function tiFit() {
    const stage = $("ti-stage");
    if (!TI.view || !TI.size || !TI.size.wrapW) return;
    const [fw, fh] = TI.view.frame;
    const w = Math.min(TI.size.wrapW, TI.size.wrapH * fw / fh);
    stage.style.width = `${Math.floor(w)}px`;
    stage.style.height = `${Math.floor(w * fh / fw)}px`;
    TI.stageScale = w / fw;
    $("ti-over").setAttribute("viewBox", `0 0 ${fw} ${fh}`);
}

function tiDrawTitle() {
    if (!TI.view) return;
    $("ti-title").innerHTML = TitlesSvg.draw(tiCase(TI.T));
    const t = TI.T, fps = TI.view.fps || 24;
    $("ti-tc").textContent = tiFrames() ? `frame ${Math.round(t * fps)} · ${t.toFixed(2)} s` : `${t.toFixed(2)} s · frame ${Math.round(t * fps)}`;
    const ws = TI.view.clock.words.map((w, i) => tiClockWord(i));
    const ins = Math.max(0, ...ws.map(w => w.start + w.len));
    const outs = Math.min(...ws.map(w => (w.exit ? w.exit.at : Infinity)));
    $("ti-phase").textContent = t < ins ? "coming in" : t < outs ? "held" : "going out";
    tiMovePlayhead();
}

/* The overlay: guides, the title's box, each word's box, the selection's handles. */
function tiDrawOver() {
    if (!TI.view) return;
    const v = TI.view, [fw, fh] = v.frame;
    const k = 1 / TI.stageScale;            // one screen px, in frame px
    const [sx0, sy0, sx1, sy1] = v.safe;
    let s = "";
    if (TI.ui.guides.safe) s += `<rect class="ti-guide" x="${sx0}" y="${sy0}" width="${sx1 - sx0}" height="${sy1 - sy0}" stroke-width="${k}" stroke-dasharray="${5 * k} ${4 * k}"/>`;
    if (TI.ui.guides.thirds) for (const f of [1 / 3, 2 / 3]) s += `<line class="ti-guide" x1="${fw * f}" y1="0" x2="${fw * f}" y2="${fh}" stroke-width="${k}"/><line class="ti-guide" x1="0" y1="${fh * f}" x2="${fw}" y2="${fh * f}" stroke-width="${k}"/>`;
    const blk = tiBlock();
    const pad = 14 * k;
    // The whole title's box - to drag it about; a kinetic title's camera places it instead.
    if (!v.kinetic) s += `<rect class="ti-block${TI.sel.length ? "" : " on"}" x="${blk.x - pad}" y="${blk.y - pad}" width="${blk.w + 2 * pad}" height="${blk.h + 2 * pad}" data-hit="block" stroke-width="${1.2 * k}" stroke-dasharray="${6 * k} ${4 * k}"/>`;
    for (const b0 of tiScreen()) {
        const b = tiAdjust(b0);
        const on = TI.sel.includes(b.id), hv = TI.hover === b.id;
        const p = b.size * 0.06;
        const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
        s += `<g transform="rotate(${(b.rot || 0).toFixed(2)} ${cx.toFixed(1)} ${cy.toFixed(1)})">`;
        s += `<rect class="ti-word${on ? " on" : hv ? " hover" : ""}" x="${b.x - p}" y="${b.y - p}" width="${b.w + 2 * p}" height="${b.h + 2 * p}" data-hit="word" data-id="${tiEsc(b.id)}" stroke-width="${(on ? 1.5 : 1) * k}"${hv && !on ? ` stroke-dasharray="${4 * k} ${3 * k}"` : ""}/>`;
        if (on && TI.sel.length === 1 && !(TI.drag && TI.drag.moved)) {
            s += `<line class="ti-arm" x1="${cx}" y1="${b.y - p}" x2="${cx}" y2="${b.y - p - 18 * k}" stroke-width="${1.5 * k}"/>`;
            s += `<circle class="ti-knob" cx="${cx}" cy="${b.y - p - 23 * k}" r="${5 * k}" stroke-width="${1.5 * k}" data-hit="rotate" data-id="${tiEsc(b.id)}"><title>Turn it (Shift: 15° steps)</title></circle>`;
            for (const [x, y] of [[b.x - p, b.y - p], [b.x + b.w + p, b.y - p], [b.x - p, b.y + b.h + p], [b.x + b.w + p, b.y + b.h + p]]) {
                s += `<rect class="ti-handle" x="${x - 4 * k}" y="${y - 4 * k}" width="${8 * k}" height="${8 * k}" stroke-width="${1.5 * k}" data-hit="scale" data-id="${tiEsc(b.id)}"><title>Size it</title></rect>`;
            }
        }
        s += `</g>`;
    }
    if (TI.drag && TI.drag.mode === "block" && TI.drag.moved) {
        for (const [x, y] of tiSpots(blk)) s += `<circle class="ti-spot" cx="${x}" cy="${y}" r="${4 * k}"/>`;
        for (const g of TI.drag.snaps || []) s += g.replace(/SW/g, k);
    }
    $("ti-over").innerHTML = s;
    tiPlaceToolbar();
}

/* The whole title's box, where a drag in progress has it - a kinetic title's, around its
   words as the camera shows them now (inside the frame). */
function tiBlock() {
    if (tiKinetic()) {
        const pts = tiScreen().flatMap(b => {
            const cx = b.x + b.w / 2, cy = b.y + b.h / 2, r = (b.rot || 0) * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
            return [[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([u, w]) => [cx + u * b.w / 2 * c - w * b.h / 2 * s, cy + u * b.w / 2 * s + w * b.h / 2 * c]);
        });
        if (!pts.length) return {x: 0, y: 0, w: 0, h: 0};
        const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
        const [fw, fh] = TI.view.frame;
        const x0 = tiClamp(Math.min(...xs), 0, fw), y0 = tiClamp(Math.min(...ys), 0, fh);
        return {x: x0, y: y0, w: tiClamp(Math.max(...xs), 0, fw) - x0, h: tiClamp(Math.max(...ys), 0, fh) - y0};
    }
    const b = TI.view.block || {x: 0, y: 0, w: 0, h: 0};
    const d = (TI.local && TI.local.block) || [0, 0];
    return {x: b.x + d[0], y: b.y + d[1], w: b.w, h: b.h};
}

function tiSpots(blk) {
    const [x0, y0, x1, y1] = TI.view.safe;
    const out = [];
    for (const r of "tmb") for (const c of "lcr") {
        out.push([c === "l" ? x0 + blk.w / 2 : c === "r" ? x1 - blk.w / 2 : (x0 + x1) / 2,
                  r === "t" ? y0 + blk.h / 2 : r === "b" ? y1 - blk.h / 2 : (y0 + y1) / 2]);
    }
    return out;
}

/* ------------------------------------------------------- frame: dragging */

const tiPoint = e => new DOMPoint(e.clientX, e.clientY).matrixTransform($("ti-over").getScreenCTM().inverse());

$("ti-over").addEventListener("pointerdown", e => {
    if (!TI.view || e.button !== 0) return;
    tiDragStart(e);
    const hit = e.target.dataset.hit, id = e.target.dataset.id;
    const p = tiPoint(e);
    tiStop();
    if (hit === "word") {
        if (e.shiftKey) { tiSelect([id], true); return; }
        if (!TI.sel.includes(id)) tiSelect([id]);
        TI.drag = {mode: "move", p0: p, ids: [...TI.sel]};
    } else if (hit === "scale" || hit === "rotate") {
        const b = tiScreen()[tiIndex(id)];
        const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
        TI.drag = {mode: hit, p0: p, ids: [...TI.sel], cx, cy, d0: Math.hypot(p.x - cx, p.y - cy) || 1, a0: Math.atan2(p.y - cy, p.x - cx)};
    } else if (hit === "block") {
        if (TI.sel.length) tiSelect([]);
        TI.drag = {mode: "block", p0: p};
    } else {
        if (TI.sel.length) tiSelect([]);
        return;
    }
    $("ti-over").setPointerCapture(e.pointerId);
});

$("ti-over").addEventListener("pointermove", e => {
    if (!TI.view) return;
    const d = TI.drag;
    if (!d) {
        const id = e.target.dataset.hit === "word" ? e.target.dataset.id : null;
        if (id !== TI.hover) { TI.hover = id; tiDrawOver(); }
        return;
    }
    const p = tiPoint(e), dx = p.x - d.p0.x, dy = p.y - d.p0.y;
    if (!d.moved && Math.hypot(dx, dy) * TI.stageScale < 3) return;
    d.moved = true;
    const L = TI.local = {...(TI.local && TI.local.until == null ? TI.local : {}), until: null};
    if (d.mode === "move") {
        L.dx = {}; L.dy = {};
        for (const id of d.ids) { L.dx[id] = dx; L.dy[id] = dy; }
    } else if (d.mode === "scale") {
        const f = tiClamp(Math.hypot(p.x - d.cx, p.y - d.cy) / d.d0, 0.25, 4);
        L.f = {};
        for (const id of d.ids) L.f[id] = f;
    } else if (d.mode === "rotate") {
        let a = (Math.atan2(p.y - d.cy, p.x - d.cx) - d.a0) * 180 / Math.PI;
        a = ((a + 180) % 360 + 360) % 360 - 180;
        if (e.shiftKey) a = Math.round(a / 15) * 15;
        L.r = {};
        for (const id of d.ids) L.r[id] = a;
    } else if (d.mode === "block") {
        const blk0 = TI.view.block;
        let cx = blk0.x + blk0.w / 2 + dx, cy = blk0.y + blk0.h / 2 + dy;
        const snap = 10 / TI.stageScale, [fw, fh] = TI.view.frame;
        d.snaps = [];
        const spots = tiSpots(blk0);
        for (const x of [...new Set(spots.map(s => s[0]))]) if (Math.abs(cx - x) < snap) { cx = x; d.snaps.push(`<line class="ti-snap" x1="${x}" y1="0" x2="${x}" y2="${fh}" stroke-width="SW"/>`); break; }
        for (const y of [...new Set(spots.map(s => s[1]))]) if (Math.abs(cy - y) < snap) { cy = y; d.snaps.push(`<line class="ti-snap" x1="0" y1="${y}" x2="${fw}" y2="${y}" stroke-width="SW"/>`); break; }
        const bx = cx - blk0.w / 2 - blk0.x, by = cy - blk0.h / 2 - blk0.y;
        L.block = [bx, by];
        L.dx = {}; L.dy = {};
        for (const b of tiBoxes()) { L.dx[b.id] = bx; L.dy[b.id] = by; }
        d.gpos = [cx / fw, cy / fh];
    }
    tiDrawTitle();
    tiDrawOver();
});

$("ti-over").addEventListener("pointerup", () => {
    const d = TI.drag;
    TI.drag = null;
    if (!d || !d.moved || !TI.local) { tiDrawOver(); return; }
    const L = TI.local, boxes = tiScreen();
    const size = id => boxes[tiIndex(id)].size;
    if (d.mode === "move") {
        // A nudge is kept in the word's own size, along its block - a kinetic block may be
        // turned on the frame: the drag turned back into the block's own directions.
        tiSend({over: tiOver("offset", d.ids, (id, old) => {
            const o = old || [0, 0], r = -(boxes[tiIndex(id)].frameRot || 0) * Math.PI / 180;
            const ux = L.dx[id] * Math.cos(r) - L.dy[id] * Math.sin(r), uy = L.dx[id] * Math.sin(r) + L.dy[id] * Math.cos(r);
            return [o[0] + ux / size(id), o[1] + uy / size(id)];
        })}, true);
    } else if (d.mode === "scale") {
        tiSend({over: tiOver("size", d.ids, (id, old) => tiClamp((old || 1) * L.f[id], 0.25, 4))}, true);
    } else if (d.mode === "rotate") {
        tiSend({over: tiOver("rot", d.ids, (id, old) => {
            const r = (old || 0) + L.r[id];
            return Math.abs(r) < 0.05 ? null : ((r + 180) % 360 + 360) % 360 - 180;
        })}, true);
    } else if (d.mode === "block") {
        tiSend({custom: true, gpos: d.gpos}, true);
    }
    tiDrawOver();
});

$("ti-over").addEventListener("pointerleave", () => { if (TI.hover && !TI.drag) { TI.hover = null; tiDrawOver(); } });

/* -------------------------------------------------------- frame: toolbar */

/* Over the title, never on it: the clicked words' role, colour and size. */
function tiPlaceToolbar() {
    const ftb = $("ti-ftb");
    if (!TI.view || !TI.sel.length || (TI.drag && TI.drag.moved)) { ftb.hidden = true; return; }
    const blk = tiBlock();
    const wrap = $("ti-stage-wrap").getBoundingClientRect(), stage = $("ti-stage").getBoundingClientRect();
    ftb.hidden = false;
    const fw = ftb.offsetWidth, fhgt = ftb.offsetHeight;
    const k = TI.stageScale;
    const topY = stage.top + blk.y * k - 46;
    const below = topY - fhgt < wrap.top + 2;
    const y = below ? stage.top + (blk.y + blk.h) * k + 18 : topY - fhgt;
    const x = stage.left + (blk.x + blk.w / 2) * k - fw / 2;
    ftb.style.left = `${tiClamp(x - wrap.left, 4, wrap.width - fw - 4)}px`;
    ftb.style.top = `${tiClamp(y - wrap.top, 2, wrap.height - fhgt - 2)}px`;
}

function tiToolbar() {
    if (!TI.view || !TI.sel.length) { $("ti-ftb").hidden = true; return; }
    const role = (tiBoxes()[tiIndex(TI.sel[0])] || {}).role;
    const roles = TI.view.roleStyles || {};
    let h = Object.entries(TI_ROLES).map(([r, label]) =>
        `<button type="button" class="ti-tb-txt" data-trole="${r}" aria-pressed="${r === role}" title="Make ${TI.sel.length === 1 ? "it" : "them"} ${label}">` +
        `<span class="ti-dot" style="background:${tiRoleColour(roles[r] || {})}"></span>${label}</button>`).join("");
    h += `<span class="ti-sep"></span>` + TI_SWATCHES.slice(0, 8).map(c => `<button type="button" data-tcol="${c}" title="${c}"><span class="ti-sw" style="background:${c}"></span></button>`).join("");
    h += `<span class="ti-sep"></span><button type="button" class="ti-tb-txt" data-tsize="0.9" title="Smaller">A−</button><button type="button" class="ti-tb-txt" data-tsize="1.1" title="Bigger">A+</button>`;
    if (tiKinetic() && !(TI.view.boxes[0] && TI.sel.includes(TI.view.boxes[0].id))) {
        const mark = tiToken(TI.sel[0]).mark || "none";
        h += `<span class="ti-sep"></span>` + TI_MARKS.map(([m, sym, tip]) =>
            `<button type="button" class="ti-tb-txt" data-tturn="${m}" aria-pressed="${mark === m}" title="${tip}">${sym}</button>`).join("");
    }
    h += `<span class="ti-sep"></span><button type="button" class="ti-tb-txt" data-treset title="Back to the look's">↺ Reset</button>`;
    $("ti-ftb").innerHTML = h;
}

$("ti-ftb").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b || !TI.sel.length) return;
    const ids = [...TI.sel];          // the toolbar: the clicked words only, whatever Change says
    if (b.dataset.trole) tiSend({over: tiOver("roles", ids, b.dataset.trole)});
    else if (b.dataset.tcol) tiSend({over: tiOver("colour", ids, b.dataset.tcol)});
    else if (b.dataset.tsize) tiSend({over: tiOver("size", ids, (id, old) => tiClamp((old || 1) * Number(b.dataset.tsize), 0.25, 4))});
    else if (b.dataset.tturn) tiSend({turns: tiTurns(ids, b.dataset.tturn)});
    else if (b.dataset.treset !== undefined) {
        const over = tiClone(TI.title.over);
        for (const name of Object.keys(over)) for (const id of ids) delete over[name][id];
        const times = {...TI.title.times};
        for (const id of ids) delete times[id];
        tiSend({over, times});
    }
});

/* ============================================================== selection */

function tiSelect(ids, add = false) {
    if (add) {
        const set = new Set(TI.sel);
        for (const id of ids) set.has(id) ? set.delete(id) : set.add(id);
        TI.sel = [...set];
    } else TI.sel = ids.slice();
    TI.cvSel = {};
    tiRenderAll();
}

/* ============================================================ words strip */

function tiWords() {
    const v = TI.view, boxes = v.boxes;
    const starts = new Set(v.lines.map(ids => ids[0]));
    const forced = Object.keys(TI.title.over.brk || {}).length || TI.title.forced;
    let h = "";
    const kin = !!v.kinetic;
    boxes.forEach((b, i) => {
        if (i && kin) {
            // Kinetic: the gap before a word holds its mark - click to go round them.
            const tok = tiToken(b.id), m = tok.mark || "none", mk = TI_MARKS.find(x => x[0] === m);
            const sugg = tok.mark_from === "auto" && m !== "none";
            h += `<button type="button" class="ti-gap${m !== "none" ? " brk" : ""}${tok.mark_from === "manual" ? " mine" : ""}${sugg ? " sugg" : ""}" data-turn="${tiEsc(b.id)}" data-mark="${m}" title="${mk[2]}${sugg ? " - Buddy's suggestion" : ""} (click: the next)">${mk[1]}</button>`;
        } else if (i) {
            const brk = starts.has(b.id);
            const mine = (TI.title.over.brk || {})[b.id] !== undefined;
            h += `<button type="button" class="ti-gap${brk ? " brk" : ""}${mine ? " mine" : ""}" data-gap="${tiEsc(b.id)}" data-brk="${brk}" title="${brk ? "Join these lines" : "Break the line here"}">${brk ? "↵" : "·"}</button>`;
        }
        const st = v.styles[i] || {};
        const c = st.gradient && st.gradient.stops ? `linear-gradient(90deg, ${st.gradient.stops.map(s => s[1]).join(", ")})` : tiRoleColour(st);
        const tok = tiToken(b.id);
        h += `<span class="ti-chip${TI.sel.includes(b.id) ? " on" : ""}" data-chip="${tiEsc(b.id)}" title="${TI_ROLES[b.role] || b.role}${tok.role_from === "auto" ? " - Buddy picked it" : ""}">` +
             `<span class="ti-dot" style="background:${c}"></span>${tiEsc(b.text)}</span>`;
    });
    if (kin && Object.keys(TI.title.turns || {}).length) h += `<button type="button" class="btn ghost small ti-auto-breaks" id="ti-auto-turns" title="Every mark back to Buddy's suggestions">Buddy's turns</button>`;
    else if (!kin && forced) h += `<button type="button" class="btn ghost small ti-auto-breaks" id="ti-auto-breaks" title="Let Buddy break the lines again">Buddy's breaks</button>`;
    $("ti-words").innerHTML = h;
}

$("ti-words").addEventListener("click", e => {
    const chip = e.target.closest("[data-chip]"), gap = e.target.closest("[data-gap]"), turn = e.target.closest("[data-turn]");
    if (turn) {
        const next = TI_MARK_ORDER[(TI_MARK_ORDER.indexOf(turn.dataset.mark) + 1) % TI_MARK_ORDER.length];
        return tiSend({turns: tiTurns([turn.dataset.turn], next)});
    }
    if (e.target.id === "ti-auto-turns") return tiSend({turns: {}});
    if (chip) tiSelect([chip.dataset.chip], e.shiftKey);
    else if (gap) tiSend({over: tiOver("brk", [gap.dataset.gap], gap.dataset.brk !== "true"), forced: null});
    else if (e.target.id === "ti-auto-breaks") tiSend({over: {...tiClone(TI.title.over), brk: {}}, forced: null});
});
$("ti-words").addEventListener("mouseover", e => {
    const c = e.target.closest("[data-chip]"), id = c ? c.dataset.chip : null;
    if (id !== TI.hover) { TI.hover = id; tiDrawOver(); }
});
$("ti-words").addEventListener("mouseleave", () => { TI.hover = null; tiDrawOver(); });
$("ti-text").addEventListener("input", e => tiMerged(() => tiSend({text: e.target.value})));

/* ============================================================== thumbnails */

function tiThumb(numbers, id) {
    if (!numbers || !numbers.boxes) return "";
    return TitlesSvg.draw({...numbers, states: null, outScale: 0.07, id});
}

function tiLooks() {
    if (!TI.view) return;
    const cur = TI.title.look;
    // A header where the looks' group changes (looks.GROUPS: Bold, Clean, Glow...).
    let h = (TI.view.looks || []).map((l, i, all) =>
        (l.group && l.group !== (all[i - 1] || {}).group ? `<div class="ti-rail-sub">${tiEsc(l.group)}</div>` : "") +
        `<button type="button" class="ti-look" role="option" data-look="${l.id}" aria-selected="${l.id === cur}" title="${tiEsc(l.label)}">` +
        `<span class="ti-thumb">${tiThumb(TI.side.gallery[l.id], "gl" + l.id)}</span><span class="ti-look-name">${tiEsc(l.label)}</span></button>`).join("");
    const mine = TI.view.mine || [];
    if (mine.length) {
        h += `<div class="ti-rail-sub">Yours</div>` + mine.map((name, i) =>
            `<div class="ti-look ti-mine" role="option" tabindex="0" data-mine="${i}" title="${tiEsc(name)}">` +
            `<span class="ti-thumb">${tiThumb((TI.side.mine || [])[i], "gm" + i)}</span><span class="ti-look-name">${tiEsc(name)}</span>` +
            `<button type="button" class="ti-forget" data-forget="${i}" title="Remove this look">×</button></div>`).join("");
    }
    $("ti-looks").innerHTML = h;
    TI.looksFor = TI.title.look;
}

/* Only which look is chosen - the thumbnails are drawn when they change (ti_side). */
function tiLooksChosen() {
    if (TI.looksFor === TI.title.look) return;
    for (const n of $("ti-looks").querySelectorAll("[data-look]")) n.setAttribute("aria-selected", String(n.dataset.look === TI.title.look));
    TI.looksFor = TI.title.look;
}

$("ti-looks").addEventListener("click", e => {
    const forget = e.target.closest("[data-forget]");
    if (forget) { e.stopPropagation(); send("ti_forget_look", {index: Number(forget.dataset.forget)}); return; }
    const mine = e.target.closest("[data-mine]");
    if (mine) { send("ti_use_look", {index: Number(mine.dataset.mine)}); return; }
    const look = e.target.closest("[data-look]");
    if (look && look.dataset.look !== TI.title.look) tiSend({look: look.dataset.look, roleColour: {}, roleStyle: {}, curves: null});
});

function tiSuggestions() {
    if (!TI.view) return;
    const alts = TI.side.alternates || [];
    $("ti-sugg").innerHTML = alts.length ? `<span class="muted small">Suggestions</span>` + alts.map((a, i) =>
        `<button type="button" class="ti-alt" data-alt="${i}" title="Use this layout"><span class="ti-thumb">${tiThumb(a, "ga" + i)}</span>` +
        `<span class="small">${a.count} ${a.count === 1 ? "line" : "lines"}</span></button>`).join("") : "";
}

$("ti-sugg").addEventListener("click", e => {
    const b = e.target.closest("[data-alt]");
    const a = b && (TI.side.alternates || [])[Number(b.dataset.alt)];
    if (a) tiSend({forced: a.key, over: {...tiClone(TI.title.over), brk: {}}});
});

/* =============================================================== inspector */

const tiSeg = (act, opts, cur) => `<div class="segmented ti-chipset">${opts.map(([v, l]) =>
    `<button type="button" data-act="${act}" data-v="${v}" aria-pressed="${String(cur) === String(v)}">${l}</button>`).join("")}</div>`;

function tiSwatches(act, cur, extra = "") {
    const c = String(cur || "").toLowerCase();
    return `<div class="ti-sws">${TI_SWATCHES.map(s => `<button type="button" data-act="${act}" data-v="${s}"${extra} style="background:${s}" aria-pressed="${c === s.toLowerCase()}" title="${s}"></button>`).join("")}` +
           `<button type="button" class="ti-any" data-act="${act}" data-any="1"${extra} title="Any colour"></button></div>`;
}

function tiKindChips(act, cur, first) {
    return `<div class="ti-kinds">${[first, ...Object.entries(TI_KINDS)].map(([k, l]) =>
        `<button type="button" class="ti-kind" data-act="${act}" data-v="${k}" aria-pressed="${cur === k}"><svg viewBox="0 0 18 12" fill="none" stroke="currentColor">${TI_ICON[k] || TI_ICON.look}</svg>${l}</button>`).join("")}</div>`;
}

/* Which way a word moves: coming in, an arrow where it comes from; going out, where it goes. */
function tiDirPad(act, cur, leaving = false) {
    const cells = leaving
        ? [["", ""], ["u", "↑"], ["", ""], ["l", "←"], ["", "•"], ["r", "→"], ["", ""], ["d", "↓"], ["", ""]]
        : [["", ""], ["d", "↓"], ["", ""], ["r", "→"], ["", "•"], ["l", "←"], ["", ""], ["u", "↑"], ["", ""]];
    const tip = leaving ? {u: "Leaves upwards", d: "Leaves downwards", l: "Leaves to the left", r: "Leaves to the right"}
        : {u: "Comes in from below", d: "Comes in from above", l: "Comes in from the right", r: "Comes in from the left"};
    return `<div class="ti-dirpad">${cells.map(([v, l]) => v ? `<button type="button" data-act="${act}" data-v="${v}" aria-pressed="${cur === v}" title="${tip[v]}">${l}</button>` : `<span>${l}</span>`).join("")}</div>`;
}

/* How a word can leave: the first choice ("Title's" or "None"), the entrance reversed,
   or a built-in (not Type). */
function tiExitChips(act, cur, first) {
    const opts = [first, ["reverse", "Reversed"], ...Object.entries(TI_KINDS).filter(([k]) => k !== "type")];
    return `<div class="ti-kinds">${opts.map(([k, l]) =>
        `<button type="button" class="ti-kind" data-act="${act}" data-v="${k}" aria-pressed="${cur === k}"><svg viewBox="0 0 18 12" fill="none" stroke="currentColor">${TI_ICON[k] || TI_ICON.look}</svg>${l}</button>`).join("")}</div>`;
}

/* A word's exit as it plays, drawn from at rest (0) to gone (1): its own drawing, else
   its built-in played backwards. */
function tiExitCurveOf(w) {
    const ex = w && w.exit;
    if (!ex) return {len: 0.27, ch: {}};
    const base = ex.curve || TitlesMotion.mirror(ex.reverse ? (w.curve || TitlesMotion.curveOf(w.kind)) : TitlesMotion.curveOf(ex.kind));
    return {...base, len: ex.len};
}
const tiMoves = c => ((c && c.ch && c.ch.move) || []).some(k => Math.abs(k.v) > 1e-6);

/* A time typed in the inspector, in the units the person reads times in. */
const tiTimeField = (act, sec) => tiFrames()
    ? `<input class="field ti-num" type="number" data-act="${act}" min="0" step="1" value="${Math.round(sec * tiFps())}"><span class="muted small">f</span>`
    : `<input class="field ti-num" type="number" data-act="${act}" min="0" step="0.01" value="${sec.toFixed(2)}"><span class="muted small">s</span>`;
const tiIoHead = (io, text, reset = "") => `<div class="ti-sub"><span class="ti-io ${io}">${io === "in" ? "In" : "Out"}</span><span class="muted small">${text}</span>${reset}</div>`;
const tiExitName = style => (style === "reverse" ? "Reversed" : style === "custom" ? "Your own" : TI_KINDS[style] || style);

/* Where a word comes in and goes out along the title: In's bar, the hold, Out's bar. */
function tiIoStrip(w) {
    const T = TI.title.seconds, pc = s => `${tiClamp(s / T * 100, 0, 100).toFixed(2)}%`;
    const inEnd = w.start + w.len, outAt = w.exit ? w.exit.at : T;
    return `<div class="ti-full ti-iostrip" title="The title's length: when it comes in, is held, and goes out">
        <span class="ti-io-hold" style="left:${pc(inEnd)};width:calc(${pc(outAt)} - ${pc(inEnd)})"></span>
        <span class="ti-io-bar in" style="left:${pc(w.start)};width:max(3px, ${pc(w.len)})"></span>
        ${w.exit ? `<span class="ti-io-bar out" style="left:${pc(w.exit.at)};width:max(3px, ${pc(w.exit.len)})"></span>` : ""}
        <span class="ti-io-t">0</span><span class="ti-io-t end">${tiTime(T)}</span>
    </div>`;
}

const tiReset = (key, show) => (show ? `<button type="button" class="ti-reset" data-reset="${key}" title="Back to the look's">↺</button>` : "");

/* Kinetic, the selected words' mark: none, a block underneath, or turned either way. */
function tiKineticGroup(sel) {
    const t = TI.title, tok = tiToken(sel[0].id);
    const manual = TI.sel.some(id => (t.turns || {})[id] !== undefined);
    if (sel.some(x => tiIndex(x.id) === 0)) {
        return `<div class="ti-grp"><h4>Kinetic</h4><p class="ti-note">The title starts here - the first block.</p></div>`;
    }
    const why = tok.mark_from === "manual" ? "Set by you." : tok.mark ? "Buddy's suggestion." : "No mark: it carries on the block before.";
    return `<div class="ti-grp">
        <h4>Kinetic ${tiReset("turn", manual)}</h4>
        <span class="ti-lbl">Mark</span>${tiSeg("turn", TI_MARKS.map(([m, sym, tip, name]) => [m, `<span title="${tip}">${sym} ${name}</span>`]), tok.mark || "none")}
        <p class="ti-note">${why} ↓ starts a block underneath; ↻ ↺ start one turned, and the camera turns with it.</p>
    </div>`;
}

function tiInspector() {
    if (!TI.view || TI.inspBusy) return;
    // Not under the person's typing: a field of its own that has the focus stays put.
    const active = document.activeElement;
    if (active && $("ti-insp").contains(active) && active.matches("input, select")) return;
    const v = TI.view, t = TI.title, over = t.over;
    const boxes = v.boxes;
    if (TI.sel.length) {
        const sel = boxes.filter(b => TI.sel.includes(b.id));
        const b = sel[0], i = tiIndex(b.id), st = v.styles[i] || {};
        const one = sel.length === 1, tg = tiTargets(), cw = tiClockWord(i);
        const has = name => tg.some(id => (over[name] || {})[id] !== undefined);
        const fill = tiRoleColour(st);
        const moves = TitlesMotion.CHANNELS.includes("move") && ((cw.curve || TitlesMotion.curveOf(cw.kind)).ch.move || []).some(k => Math.abs(k.v) > 1e-6);
        const animCur = over.curves[b.id] ? "custom" : over.anim[b.id] || "";
        const offs = over.offset[b.id] || [0, 0];
        const roleWord = TI_ROLES[b.role] || b.role;
        $("ti-insp-hd").innerHTML = `Inspector <span class="muted">· ${one ? "one word" : sel.length + " words"}</span>`;
        $("ti-insp").innerHTML = `
        <div class="ti-ihead">
            <div class="ti-big" style="color:${st.fill === "none" ? "var(--text-strong)" : fill}">${tiEsc(sel.map(x => x.text).join(" "))}</div>
            <div class="muted small">${roleWord} · ${tiToken(b.id).role_from === "auto" ? "Buddy picked the role" : "you set the role"} · ${tiLineWord()} ${b.line + 1}</div>
        </div>
        <div class="ti-grp">
            <h4>Change</h4>
            <div class="ti-full">${tiSeg("scope", [["word", one ? "This word" : "These words"], ["line", one ? `${tiKinetic() ? "Block" : "Line"} ${b.line + 1}` : `Their ${tiLineWord()}s`],
                                                   ["role", `All ${roleWord.toLowerCase()}s`], ["all", "Every word"]], TI.scope)}</div>
            <p class="ti-note">${tg.length} ${tg.length === 1 ? "word" : "words"}: ${tiEsc(boxes.filter(x => tg.includes(x.id)).map(x => x.text).join(", "))}</p>
        </div>
        ${tiKinetic() ? tiKineticGroup(sel) : ""}
        <div class="ti-grp">
            <h4>Role ${tiReset("roles", has("roles"))}</h4>
            <div class="ti-full">${tiSeg("role", Object.entries(TI_ROLES).map(([r, l]) => [r, `<span class="ti-dot" style="background:${tiRoleColour((v.roleStyles || {})[r] || {})}"></span>${l}`]), b.role)}</div>
        </div>
        <div class="ti-grp">
            <h4>Style</h4>
            <span class="ti-lbl">Colour ${tiReset("colour", has("colour"))}</span>${tiSwatches("colour", fill)}
            ${(st.strokes || []).length ? `<span class="ti-lbl">Outline ${tiReset("stroke", has("stroke"))}</span><div class="ti-ctl"><input type="range" data-act="stroke" min="0" max="2.5" step="0.05" value="${over.stroke[b.id] ?? 1}"><span class="ti-val" title="Double-click to type a value">${Math.round((over.stroke[b.id] ?? 1) * 100)}%</span></div>` : ""}
            <span class="ti-lbl">Size ${tiReset("size", has("size"))}</span><div class="ti-ctl"><input type="range" data-act="size" min="0.4" max="2.5" step="0.01" value="${over.size[b.id] || 1}"><span class="ti-val" title="Double-click to type a value">${Math.round((over.size[b.id] || 1) * 100)}%</span></div>
            <span class="ti-lbl">Turn ${tiReset("rot", has("rot"))}</span><div class="ti-ctl"><input type="range" data-act="rot" min="-45" max="45" step="0.5" value="${over.rot[b.id] || 0}"><span class="ti-val" title="Double-click to type a value">${(over.rot[b.id] || 0).toFixed(1)}°</span></div>
            <span class="ti-lbl">Nudge ${tiReset("offset", has("offset"))}</span><div class="ti-ctl"><span class="muted small">X</span><input class="field ti-num" type="number" data-act="ox" value="${Math.round(offs[0] * b.size)}"><span class="muted small">Y</span><input class="field ti-num" type="number" data-act="oy" value="${Math.round(offs[1] * b.size)}"><span class="muted small">px</span></div>
        </div>
        <div class="ti-grp">
            <h4>Animation ${tiReset("anim", has("anim") || has("curves") || has("len") || has("dir") || has("exitLen") || tg.some(id => t.times[id] !== undefined || t.exitTimes[id] !== undefined))}</h4>
            ${tiIoStrip(cw)}
            ${tiIoHead("in", one ? "how it comes in" : "how they come in", tiReset("inAll", has("anim") || has("curves") || has("len") || has("dir") || tg.some(id => t.times[id] !== undefined)))}
            <div class="ti-full">${tiKindChips("wanim", animCur, ["", "Title's"])}</div>
            ${animCur === "custom" ? `<p class="ti-note">${one ? "Its" : "Their"} own curve - drawn in Curves below.</p>` : ""}
            ${moves ? `<span class="ti-lbl">From</span>${tiDirPad("wdir", over.dir[b.id] || cw.dir)}` : ""}
            <span class="ti-lbl">Starts ${tiReset("times", tg.some(id => t.times[id] !== undefined))}</span>
            <div class="ti-ctl">${tiTimeField("wstart", cw.start)}<span class="muted small">${t.times[b.id] !== undefined ? "· moved by you" : "· on the stagger"}</span></div>
            <span class="ti-lbl">Length ${tiReset("len", has("len"))}</span><div class="ti-ctl">${tiTimeField("wlen", cw.len)}</div>
            <div class="ti-full"><button type="button" class="btn ghost small" data-act="editcurve" data-v="in">Edit ${one ? "its" : "their"} entrance curve</button></div>
            ${tiIoHead("out", one ? "how it leaves" : "how they leave", cw.exit ? tiReset("outAll", has("exitAnim") || has("exitCurves") || has("exitLen") || has("exitDir") || tg.some(id => t.exitTimes[id] !== undefined)) : "")}
            ${cw.exit ? `
            <div class="ti-full">${tiExitChips("wxanim", over.exitCurves[b.id] ? "custom" : over.exitAnim[b.id] || "", ["", "Title's"])}</div>
            ${over.exitCurves[b.id] ? `<p class="ti-note">${one ? "Its" : "Their"} own exit curve - drawn in Curves below (Out).</p>`
              : `<p class="ti-note">Now: ${tiExitName(cw.exit.style)}${over.exitAnim[b.id] ? "" : " - the title's"}.</p>`}
            ${tiMoves(tiExitCurveOf(cw)) ? `<span class="ti-lbl">Towards ${tiReset("exitDir", has("exitDir"))}</span>${tiDirPad("wxdir", cw.exit.towards, true)}` : ""}
            <span class="ti-lbl">Starts ${tiReset("exitTimes", tg.some(id => t.exitTimes[id] !== undefined))}</span>
            <div class="ti-ctl">${tiTimeField("xstart", cw.exit.at)}<span class="muted small">${t.exitTimes[b.id] !== undefined ? "· moved by you" : "· with the others"}</span></div>
            <span class="ti-lbl">Length ${tiReset("exitLen", has("exitLen"))}</span><div class="ti-ctl">${tiTimeField("xlen", cw.exit.len)}</div>
            <div class="ti-full"><button type="button" class="btn ghost small" data-act="editcurve" data-v="out">Edit ${one ? "its" : "their"} exit curve</button></div>`
            : `<p class="ti-note">The title has no exit - every word stays to the end.</p>
            <div class="ti-full"><button type="button" class="btn ghost small" data-act="exiton">Add an exit</button></div>`}
        </div>`;
    } else {
        const look = tiLook();
        const roles = [...new Set(boxes.map(b => b.role))];
        const kind = tiTitleKind();
        const titleCurve = t.curves || TitlesMotion.curveOf(kind);
        const moves = (titleCurve.ch.move || []).some(k => Math.abs(k.v) > 1e-6);
        // A word that leaves the title's way (no exit of its own): what "the title's" exit is.
        const xw = v.clock.words.find(w => w.exit && !t.over.exitAnim[w.id] && !t.over.exitCurves[w.id] && !t.over.exitDir[w.id]);
        $("ti-insp-hd").innerHTML = `Inspector <span class="muted">· whole title</span>`;
        $("ti-insp").innerHTML = `
        <div class="ti-ihead">
            <div class="ti-big">${tiEsc(look.label)}</div>
            <div class="muted small">Click a word on the frame to change just that word.</div>
        </div>
        ${v.kinetic ? `
        <div class="ti-grp">
            <h4>Layout</h4>
            <span class="ti-lbl">Arrange</span>${tiSeg("arrange", TI_ARRANGE, t.arrange)}
            <span class="ti-lbl">Camera</span>${tiSeg("camera", [["each", "Zoom to each"], ["all", "Keep all in view"]], t.camera || "each")}
            <p class="ti-note">Each mark starts a block: ↓ underneath, ↻ ↺ turned. Set them between the words above, or click a word. ${Object.keys(t.turns || {}).length ? `${Object.keys(t.turns).length} set by you.` : "These are Buddy's suggestions."}</p>
            ${Object.keys(t.turns || {}).length ? `<div class="ti-full"><button type="button" class="btn ghost small" data-act="autoturns">Back to Buddy's turns</button></div>` : ""}
        </div>` : `
        <div class="ti-grp">
            <h4>Layout</h4>
            <span class="ti-lbl">Lines</span>${tiSeg("lines", [["auto", "Auto"], [1, "1"], [2, "2"], [3, "3"], [4, "4"]], t.lines)}
            <span class="ti-lbl">Arrange</span>${tiSeg("arrange", TI_ARRANGE, t.arrange)}
            <span class="ti-lbl">Width ${tiReset("bw", t.bw != null)}</span><div class="ti-ctl"><input type="range" data-act="bw" min="0.3" max="1" step="0.01" value="${t.bw ?? 0.9}"><span class="ti-val" title="Double-click to type a value">${t.bw == null ? "Look's" : Math.round(t.bw * 100) + "%"}</span></div>
        </div>
        <div class="ti-grp">
            <h4>Position ${tiReset("gpos", t.custom)}</h4>
            <span class="ti-lbl">Where</span>
            <div class="ti-ctl"><div class="ti-spots">${TI_SPOTS.map(p => `<button type="button" data-act="pos" data-v="${p}" aria-pressed="${!t.custom && t.pos === p}"></button>`).join("")}</div>
                <button type="button" class="btn ghost small" data-act="pos" data-v="look" aria-pressed="${!t.custom && t.pos === "look"}">Look's</button></div>
            <p class="ti-note">${t.custom ? `Your own spot: ${Math.round(t.gpos[0] * 100)}%, ${Math.round(t.gpos[1] * 100)}% - or drag the title on the frame.` : "Or drag the title on the frame."}</p>
        </div>`}
        <div class="ti-grp">
            <h4>Words by role</h4>
            ${roles.map(r => {
                const rs = (v.roleStyles || {})[r] || {};
                const face = (t.roleStyle[r] && t.roleStyle[r].font) || rs.font || ["", ""];
                const faces = (v.faces || []).some(f => f[0] === face[0] && f[1] === face[1]) ? v.faces : [face, ...(v.faces || [])];
                return `<div class="ti-rolerow"><span class="ti-rn"><span class="ti-dot" style="background:${tiRoleColour(rs)}"></span>${TI_ROLES[r] || r} ${tiReset("role-" + r, !!(t.roleColour[r] || t.roleStyle[r]))}</span>
                    <select class="field" data-act="rfont" data-role="${r}">${faces.map(f => `<option value="${tiEsc(f.join("|"))}"${f[0] === face[0] && f[1] === face[1] ? " selected" : ""}>${tiEsc(f[0] + " " + f[1])}</option>`).join("")}</select>
                    <span></span>${tiSwatches("rcol", tiRoleColour(rs), ` data-role="${r}"`)}</div>`;
            }).join("")}
            <p class="ti-note">Buddy picks each word's role; click a word to set it yourself.</p>
        </div>
        <div class="ti-grp">
            <h4>Animation</h4>
            ${tiIoHead("in", "how the words come in")}
            <div class="ti-full">${tiKindChips("anim", t.curves ? "custom" : t.anim, ["look", "Look's"])}</div>
            ${t.curves ? `<p class="ti-note">Your own curve - reshape it in Curves below.</p>` : ""}
            <div class="ti-full"><button type="button" class="btn ghost small" data-act="editcurve" data-v="in">Edit the entrance curve</button></div>
            ${moves ? `<span class="ti-lbl">From</span>${tiDirPad("dir", t.dir || "u")}` : ""}
            <span class="ti-lbl">Plays by</span>${tiSeg("plays", [["word", "Word"], ["line", "Line"], ["all", "All at once"]], t.plays)}
            <span class="ti-lbl">Stagger</span><div class="ti-ctl"><input type="range" data-act="stagger" min="0" max="0.5" step="${tiFrames() ? 1 / tiFps() : 0.01}" value="${t.stagger}"><span class="ti-val" title="Double-click to type a value">${tiValText("stagger", t.stagger)}</span></div>
            ${Object.keys(t.times).length ? `<p class="ti-note">${Object.keys(t.times).length} ${Object.keys(t.times).length === 1 ? "word starts" : "words start"} where you put ${Object.keys(t.times).length === 1 ? "it" : "them"} - Even out in the timeline puts them back.</p>` : ""}
            ${v.pulses ? `<span class="ti-lbl">Echoes</span>${tiSeg("pulse", [["off", "Still"], ["on", "Pulse"]], t.pulse ? "on" : "off")}` : ""}
            ${tiIoHead("out", "how the words leave")}
            <div class="ti-full">${tiExitChips("exit", !t.exit.on ? "off" : t.exitCurves ? "custom" : t.exit.style, ["off", "None"])}</div>
            ${t.exit.on && t.exitCurves ? `<p class="ti-note">Your own exit curve - reshape it in Curves below (Out).</p>` : ""}
            ${t.exit.on && xw && tiMoves(t.exitCurves || tiExitCurveOf(xw)) ? `<span class="ti-lbl">Towards ${tiReset("xdir", !!t.exit.dir)}</span>${tiDirPad("xdir", t.exit.dir || xw.exit.towards, true)}` : ""}
            ${t.exit.on ? `<span class="ti-lbl">Order</span>${tiSeg("order", [["same", "Same"], ["reverse", "Reversed"], ["all", "All at once"]], t.exit.order)}` : ""}
            ${t.exit.on ? `<div class="ti-full"><button type="button" class="btn ghost small" data-act="editcurve" data-v="out">Edit the exit curve</button></div>` : ""}
            ${t.exit.on && Object.keys(t.exitTimes).length + Object.keys(t.over.exitLen).length ? `<p class="ti-note">Some words leave at a time of their own - click one to see it.</p>` : ""}
            <div class="ti-sub"><span class="ti-io all">Title</span><span class="muted small">how long it's on screen</span></div>
            <span class="ti-lbl">Length</span><div class="ti-ctl"><input type="range" data-act="seconds" min="1" max="10" step="${tiFrames() ? 1 / tiFps() : 0.1}" value="${t.seconds}"><span class="ti-val" title="Double-click to type a value">${tiTime(t.seconds)}</span></div>
            ${v.min_seconds > t.seconds ? `<p class="ti-note">This animation needs ${tiTime(v.min_seconds)} - the title will be placed that long.</p>` : ""}
        </div>`;
    }
}

/* One inspector control: what it changes, sent at once (sliders as they move). */
function tiAct(act, value, node) {
    const t = TI.title, tg = tiTargets(), boxes = tiBoxes();
    const size = id => boxes[tiIndex(id)].size;
    switch (act) {
    case "scope": TI.scope = value; return tiRenderAll();
    case "role": return tiSend({over: tiOver("roles", tg, value)});
    case "colour": return tiSend({over: tiOver("colour", tg, value)});
    case "stroke": return tiSend({over: tiOver("stroke", tg, Number(value) === 1 ? null : Number(value))});
    case "size": return tiSend({over: tiOver("size", tg, Number(value) === 1 ? null : Number(value))});
    case "rot": return tiSend({over: tiOver("rot", tg, Number(value) ? Number(value) : null)});
    case "ox": case "oy": {
        const k = act === "ox" ? 0 : 1;
        return tiSend({over: tiOver("offset", tg, (id, old) => { const o = (old || [0, 0]).slice(); o[k] = Number(value) / size(id); return o; })});
    }
    case "wanim": {
        const over = tiOver("anim", tg, value || null);
        for (const id of tg) delete over.curves[id];
        return tiSend({over});
    }
    case "wdir": return tiSend({over: tiOver("dir", tg, value)});
    case "wxanim": {
        const over = tiOver("exitAnim", tg, value || null);
        for (const id of tg) delete over.exitCurves[id];
        return tiSend({over});
    }
    case "wxdir": return tiSend({over: tiOver("exitDir", tg, value)});
    case "xdir": return tiSend({exit: {...t.exit, dir: value}});
    case "editcurve":
        tiCvSetIo(value === "out" ? "out" : "in");
        if (!TI.ui.dock) tiSetDock(true);
        $("ti-curves").closest(".ti-cv").classList.add("ti-flash");
        setTimeout(() => $("ti-curves").closest(".ti-cv").classList.remove("ti-flash"), 700);
        return;
    case "lines": return tiSend({lines: value === "auto" ? "auto" : Number(value), forced: null});
    case "arrange": return tiSend({arrange: value});
    case "camera": return tiSend({camera: value});
    case "turn": return tiSend({turns: tiTurns(TI.sel, value)});
    case "autoturns": return tiSend({turns: {}});
    case "bw": return tiSend({bw: Number(value)});
    case "pos": return tiSend({pos: value, custom: false});
    case "rfont": return tiSend({roleStyle: {...tiClone(t.roleStyle), [node.dataset.role]: {font: value.split("|")}}});
    case "rcol": return tiSend({roleColour: {...t.roleColour, [node.dataset.role]: value}});
    case "anim": return tiSend({anim: value, curves: null});
    case "dir": return tiSend({dir: value});
    case "plays": return tiSend({plays: value});
    case "exit": return tiSend(value === "off" ? {exit: {...t.exit, on: false}} : {exit: {...t.exit, on: true, style: value}, exitCurves: null});
    case "order": return tiSend({exit: {...t.exit, order: value}});
    case "pulse": return tiSend({pulse: value === "on"});
    case "seconds": return tiSend({seconds: Number(value)});
    case "stagger": return tiSend({stagger: Number(value)});
    case "exiton": return tiSend({exit: {...t.exit, on: true}});
    case "wstart": case "wlen": case "xstart": case "xlen": {
        const n = Number(value), fps = tiFps();
        if (value === "" || !Number.isFinite(n)) return tiInspector();
        // Frames typed land on frames; seconds as typed.
        const sec = tiFrames() ? n / fps : Math.round(n * 1000) / 1000, min = 1 / fps;
        if (act === "wstart") {
            const times = {...t.times};
            for (const id of tg) times[id] = tiClamp(sec, 0, t.seconds - min);
            return tiSend({times});
        }
        if (act === "xstart") {
            const exitTimes = {...t.exitTimes};
            for (const id of tg) exitTimes[id] = tiClamp(sec, 0, t.seconds);
            return tiSend({exitTimes});
        }
        return tiSend({over: tiOver(act === "wlen" ? "len" : "exitLen", tg, tiClamp(sec, min, 4))});
    }
    }
}

$("ti-insp").addEventListener("click", e => {
    const r = e.target.closest("[data-reset]");
    if (r) {
        const k = r.dataset.reset, tg = tiTargets(), t = TI.title;
        if (k === "anim") {
            const over = tiClone(t.over), times = {...t.times}, exitTimes = {...t.exitTimes};
            for (const id of tg) {
                for (const name of ["anim", "curves", "len", "dir", "exitAnim", "exitCurves", "exitLen", "exitDir"]) delete over[name][id];
                delete times[id]; delete exitTimes[id];
            }
            tiSend({over, times, exitTimes});
        } else if (k === "bw") tiSend({bw: null});
        else if (k === "turn") tiSend({turns: tiTurns(TI.sel, null)});
        else if (k === "gpos") tiSend({custom: false});
        else if (k === "xdir") tiSend({exit: {...t.exit, dir: null}});
        else if (k === "inAll" || k === "outAll") {
            const over = tiClone(t.over), key = k === "inAll" ? "times" : "exitTimes", m = {...t[key]};
            const maps = k === "inAll" ? ["anim", "curves", "len", "dir"] : ["exitAnim", "exitCurves", "exitLen", "exitDir"];
            for (const id of tg) { for (const name of maps) delete over[name][id]; delete m[id]; }
            tiSend({over, [key]: m});
        } else if (k === "times" || k === "exitTimes") {
            const m = {...t[k]};
            for (const id of tg) delete m[id];
            tiSend({[k]: m});
        }
        else if (k.startsWith("role-")) {
            const role = k.slice(5), rc = {...t.roleColour}, rs = tiClone(t.roleStyle);
            delete rc[role]; delete rs[role];
            tiSend({roleColour: rc, roleStyle: rs});
        } else tiSend({over: tiOver(k, tg, null)});
        return;
    }
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    if (b.dataset.any) {
        const cur = b.dataset.role ? tiRoleColour((TI.view.roleStyles || {})[b.dataset.role] || {}) : "#FFFFFF";
        Buddy.pickColor({hex: /^#[0-9a-f]{6}$/i.test(cur) ? cur : "#FFFFFF", title: "Colour", at: b,
                         onPick: hex => tiAct(b.dataset.act, hex, b)});
        return;
    }
    tiAct(b.dataset.act, b.dataset.v, b);
});
$("ti-insp").addEventListener("input", e => {
    const n = e.target;
    if (n.type !== "range" || !n.dataset.act) return;
    const val = n.parentElement.querySelector(".ti-val");
    if (val) val.textContent = tiValText(n.dataset.act, Number(n.value));
    tiMerged(() => tiAct(n.dataset.act, n.value, n));
});
$("ti-insp").addEventListener("change", e => {
    const n = e.target;
    if (n.tagName === "SELECT" || n.type === "number") tiAct(n.dataset.act, n.value, n);
});
/* A slider's value as it reads (rot: degrees; seconds and stagger: a time; the rest: %),
   the number shown when it's typed instead, and that number read back. */
const tiIsTime = act => act === "seconds" || act === "stagger";
function tiValText(act, v) {
    return act === "rot" ? `${v.toFixed(1)}°` : tiIsTime(act) ? tiTime(v, act === "stagger" ? 2 : 1) : `${Math.round(v * 100)}%`;
}
function tiValNumber(act, v) {
    return act === "rot" ? +v.toFixed(1) : tiIsTime(act) ? (tiFrames() ? Math.round(v * tiFps()) : +v.toFixed(2)) : Math.round(v * 100);
}
function tiValFrom(act, n) {
    return act === "rot" ? n : tiIsTime(act) ? (tiFrames() ? n / tiFps() : n) : n / 100;
}

/* Double-click a slider's value to type one, in the units it reads in. Enter or clicking
   away sets it (one undo step), Esc leaves it as it was. */
function tiTypeIn(val) {
    const range = val.parentElement.querySelector("input[type=range]");
    if (!range || val.querySelector("input")) return;
    const act = range.dataset.act || "stagger";
    const old = val.textContent;
    const box = document.createElement("input");
    box.type = "number";
    box.step = "any";
    box.className = "field ti-num ti-typein";
    box.value = tiValNumber(act, Number(range.value));
    val.replaceChildren(box);
    box.focus();
    box.select();
    let done = false;
    const finish = keep => {
        if (done) return;
        done = true;
        const n = Number(box.value);
        val.textContent = old;
        if (!keep || box.value.trim() === "" || !Number.isFinite(n)) return;
        let v = tiClamp(tiValFrom(act, n), Number(range.min), Number(range.max));
        if (act === "seconds") v = tiSnapLen(v);
        else if (act === "stagger") v = tiFrames() ? Math.round(v * tiFps()) / tiFps() : Math.round(v * 100) / 100;
        range.value = v;
        val.textContent = tiValText(act, v);
        if (act === "stagger") tiSend({stagger: v}); else tiAct(act, v, range);
    };
    box.addEventListener("keydown", ev => {
        ev.stopPropagation();
        if (ev.key === "Enter") finish(true);
        else if (ev.key === "Escape") finish(false);
    });
    box.addEventListener("blur", () => finish(true));
}
// Two clicks on the same value close together, as well as the browser's own double-click.
let tiValClick = {node: null, at: 0};
$("panel-titles").addEventListener("click", e => {
    const val = e.target.closest(".ti-val, #ti-stagger-v");
    if (!val) return;
    const now = performance.now(), again = e.detail >= 2 || (tiValClick.node === val && now - tiValClick.at < 500);
    tiValClick = {node: val, at: now};
    if (again) tiTypeIn(val);
});
$("panel-titles").addEventListener("dblclick", e => {
    const val = e.target.closest(".ti-val, #ti-stagger-v");
    if (val) tiTypeIn(val);
});

// While a slider's held, the inspector isn't redrawn under it.
$("ti-insp").addEventListener("pointerdown", e => { if (e.target.type === "range") TI.inspBusy = true; });
addEventListener("pointerup", () => { if (TI.inspBusy) { TI.inspBusy = false; tiInspector(); } });

/* ================================================================ timeline */

const TI_NAME_W = 118;

function tiTimeline() {
    if (!TI.view) return;
    const v = TI.view, t = TI.title, fps = v.fps || 24;
    const lane = Math.max(40, TI.size.rowsW - TI_NAME_W);
    const pps = lane / t.seconds;
    $("ti-tl-sub").textContent = `· ${tiTime(t.seconds)} · ${v.boxes.length} ${v.boxes.length === 1 ? "word" : "words"}`;
    let ticks = "";
    if (tiFrames()) {
        // Ticks on frames that divide a second (24 fps: 1 2 3 4 6 8 12, 30 fps: 1 2 3 5 6 10 15),
        // as close as they fit; numbered every so often.
        const F = Math.max(1, Math.round(fps)), fpx = pps / fps;
        const nice = [...Array.from({length: F}, (_, k) => k + 1).filter(n => F % n === 0), F * 2, F * 4, F * 8];
        const minor = nice.find(n => n * fpx >= 5) || F * 8;
        const major = nice.find(n => n % minor === 0 && n > minor && n * fpx >= 34) || F * 8;
        for (let f = 0; f <= t.seconds * fps + 1e-6; f += minor) {
            ticks += `<div class="ti-tick${f % major ? " minor" : ""}" style="left:${f / fps * pps}px">${f % major ? "" : `<b>${f}</b>`}</div>`;
        }
    } else {
        for (let s = 0; s <= t.seconds + 1e-6; s += 0.1) {
            const major = Math.abs(s * 2 - Math.round(s * 2)) < 1e-6;
            ticks += `<div class="ti-tick${major ? "" : " minor"}" style="left:${s * pps}px">${major ? `<b>${s.toFixed(1)}</b>` : ""}</div>`;
        }
    }
    ticks += `<div class="ti-endgrip" id="ti-endgrip" style="left:${lane - 4}px" title="Drag to change the title's length"></div>`;
    $("ti-ruler").innerHTML = ticks;
    $("ti-ruler").style.marginLeft = `${TI_NAME_W}px`;
    let h = "";
    v.lines.forEach((ids, k) => {
        h += `<div class="ti-lline">${tiKinetic() ? "Block" : "Line"} ${k + 1}</div>`;
        for (const id of ids) {
            const i = tiIndex(id), b = v.boxes[i], w = tiClockWord(i), st = v.styles[i] || {};
            const col = tiRoleColour(st);
            const inX = w.start * pps, inW = Math.max(w.len * pps, 5);
            const exX = w.exit ? w.exit.at * pps : lane;
            const own = w.kind === "custom" || t.over.anim[id];
            h += `<div class="ti-row${TI.sel.includes(id) ? " on" : ""}">
                <div class="ti-name" data-pick="${tiEsc(id)}" style="width:${TI_NAME_W}px"><span class="ti-dot" style="background:${col}"></span>${tiEsc(b.text)}${own ? ' <span class="muted" title="Its own animation">✎</span>' : ""}</div>
                <div class="ti-lane">
                    <div class="ti-hold" style="left:${inX + inW}px;width:${Math.max(exX - inX - inW, 0)}px;background:${col}"></div>
                    <div class="ti-bar" data-bar="${tiEsc(id)}" style="left:${inX}px;width:${inW}px;background:${col}" title="${w.kind === "custom" ? "Its own curve" : TI_KINDS[w.kind] || w.kind} · ${tiFrames() ? `${Math.round(w.len * fps)} frames` : `${w.len.toFixed(2)} s`} - drag to move it, its right edge to lengthen it">${t.times[id] !== undefined ? '<span class="ti-moved"></span>' : ""}</div>
                    ${w.exit ? `<div class="ti-bar out" data-out="${tiEsc(id)}" style="left:${exX}px;width:${Math.max(w.exit.len * pps, 5)}px;background:${col}" title="Its exit · ${tiFrames() ? `${Math.round(w.exit.len * fps)} frames` : `${w.exit.len.toFixed(2)} s`} - drag to move it, an edge to lengthen it">${t.exitTimes[id] !== undefined || t.over.exitLen[id] !== undefined ? '<span class="ti-moved"></span>' : ""}</div>` : ""}
                </div></div>`;
        }
    });
    h += `<div class="ti-ph" id="ti-ph"></div>`;
    $("ti-rows").innerHTML = h;
    $("ti-stagger").step = tiFrames() ? 1 / fps : 0.01;
    $("ti-stagger").value = t.stagger;
    if (!$("ti-stagger-v").querySelector("input")) $("ti-stagger-v").textContent = tiValText("stagger", t.stagger);
    tiMini();
    tiMovePlayhead();
}

function tiMovePlayhead() {
    if (!TI.view) return;
    const t = TI.title;
    const ph = $("ti-ph");
    if (ph && TI.size) ph.style.left = `${TI_NAME_W + TI.T * (Math.max(40, TI.size.rowsW - TI_NAME_W) / t.seconds)}px`;
    const mh = $("ti-mini-head");
    if (mh) mh.style.left = `${TI.T / t.seconds * 100}%`;
    const cv = $("ti-cv-head");
    if (cv && TI.cv) {
        const w = tiCurveWord();
        const p = !w ? -1 : TI.cvIo === "out" ? (w.exit ? (TI.T - w.exit.at) / w.exit.len : -1) : (TI.T - w.start) / w.len;
        cv.style.display = p >= 0 && p <= 1 ? "" : "none";
        if (p >= 0 && p <= 1) { const x = TI.cv.X(p); cv.setAttribute("x1", x); cv.setAttribute("x2", x); }
    }
}

/* The folded dock: the title's length as a strip with its words' entrances and the playhead. */
function tiMini() {
    if (!TI.view) return;
    const t = TI.title;
    $("ti-mini").innerHTML = TI.view.clock.words.map((w0, i) => {
        const w = tiClockWord(i);
        return `<span class="ti-mini-in" style="left:${w.start / t.seconds * 100}%;width:${Math.max(w.len / t.seconds * 100, 0.6)}%;background:${tiRoleColour(TI.view.styles[i] || {})}"></span>`;
    }).join("") + `<span class="ti-mini-head" id="ti-mini-head"></span>`;
}

function tiScrub(e, left, width) {
    tiDragStart(e);
    tiStop();
    const set = ev => { TI.T = tiClamp((ev.clientX - left) / width * TI.title.seconds, 0, TI.title.seconds); tiDrawTitle(); };
    set(e);
    const mv = ev => set(ev), up = () => { removeEventListener("pointermove", mv); removeEventListener("pointerup", up); };
    addEventListener("pointermove", mv);
    addEventListener("pointerup", up);
}

$("ti-rows").addEventListener("pointerdown", e => {
    if (!TI.view || e.button !== 0) return;
    tiDragStart(e);
    const pick = e.target.closest("[data-pick]"), bar = e.target.closest("[data-bar]"), out = e.target.closest("[data-out]");
    tiStop();
    if (pick) { tiSelect([pick.dataset.pick], e.shiftKey); return; }
    if (out) {
        // An exit: its middle moves it, its left edge starts it sooner or later (its end
        // staying put), its right edge lengthens it.
        const id = out.dataset.out;
        const r = out.getBoundingClientRect();
        const mode = e.clientX < r.left + 6 ? "xlead" : e.clientX > r.right - 6 ? "xlen" : "xmove";
        if (!TI.sel.includes(id)) tiSelect([id]);
        const pps = Math.max(40, $("ti-rows").clientWidth - TI_NAME_W) / TI.title.seconds;
        const ids = TI.sel.filter(x => tiClockWord(tiIndex(x)).exit);
        const words = Object.fromEntries(ids.map(x => [x, tiClockWord(tiIndex(x))]));
        TI.drag = {mode, x0: e.clientX, pps, ids, words};
        $("ti-rows").setPointerCapture(e.pointerId);
        return;
    }
    if (bar) {
        const id = bar.dataset.bar;
        const r = bar.getBoundingClientRect();          // before a selection redraws the rows
        const edge = e.clientX > r.right - 6;
        if (!TI.sel.includes(id)) tiSelect([id]);
        const pps = Math.max(40, $("ti-rows").clientWidth - TI_NAME_W) / TI.title.seconds;
        const ids = [...TI.sel];
        const words = Object.fromEntries(ids.map(x => [x, tiClockWord(tiIndex(x))]));
        TI.drag = {mode: edge ? "len" : "start", x0: e.clientX, pps, ids, words};
        $("ti-rows").setPointerCapture(e.pointerId);
        return;
    }
    const lane = e.target.closest(".ti-lane");
    if (lane) { const r = lane.getBoundingClientRect(); tiScrub(e, r.left, r.width); }
});
const TI_LANE_MODES = ["start", "len", "xmove", "xlead", "xlen"];

/* Where a word's exit can go: after its entrance, done by the title's end (as timing.py
   keeps it), on whole frames. */
function tiExitAt(w, at, len) {
    const fps = tiFps();
    return tiClamp(Math.round(at * fps) / fps, w.start + w.len, Math.max(w.start + w.len, TI.title.seconds - len));
}

$("ti-rows").addEventListener("pointermove", e => {
    const d = TI.drag;
    if (!d || !TI_LANE_MODES.includes(d.mode)) return;
    d.moved = true;
    const dt = (e.clientX - d.x0) / d.pps, fps = TI.view.fps || 24;
    const L = TI.local = {...(TI.local && TI.local.until == null ? TI.local : {}), until: null};
    if (d.mode[0] === "x") {
        L.exit = {};
        for (const id of d.ids) {
            const w = d.words[id], ex = w.exit, end = ex.at + ex.len;
            let at = ex.at, len = ex.len;
            if (d.mode === "xmove") at = tiExitAt(w, ex.at + dt, len);
            else if (d.mode === "xlen") len = tiClamp(Math.round((ex.len + dt) * fps) / fps, 1 / fps, Math.min(4, TI.title.seconds - ex.at));
            else {
                at = tiClamp(Math.round((ex.at + dt) * fps) / fps, w.start + w.len, end - 1 / fps);
                len = Math.min(4, end - at);
                at = end - len;
            }
            L.exit[id] = {at, len};
        }
    } else if (d.mode === "start") {
        L.start = {};
        for (const id of d.ids) L.start[id] = tiClamp(Math.round((d.words[id].start + dt) * fps) / fps, 0, TI.title.seconds - 0.1);
    } else {
        L.len = {};
        for (const id of d.ids) L.len[id] = tiClamp(Math.round((d.words[id].len + dt) * fps) / fps, 1 / fps, 4);
    }
    tiTimeline();
    tiDrawTitle();
});
$("ti-rows").addEventListener("pointerup", () => {
    const d = TI.drag;
    if (!d || !TI_LANE_MODES.includes(d.mode)) return;
    TI.drag = null;
    if (!d.moved || !TI.local) return;
    const L = TI.local;
    if (d.mode[0] === "x" && L.exit) {
        // Kept where it's shown: the time always (a longer exit would otherwise move the
        // ones on the title's clock), the length when an edge changed it.
        const exitTimes = {...TI.title.exitTimes};
        for (const id of d.ids) exitTimes[id] = +L.exit[id].at.toFixed(4);
        const change = {exitTimes};
        if (d.mode !== "xmove") change.over = tiOver("exitLen", d.ids, id => +L.exit[id].len.toFixed(4));
        tiSend(change, true);
    } else if (d.mode === "start") tiSend({times: {...TI.title.times, ...L.start}}, true);
    else tiSend({over: tiOver("len", d.ids, id => L.len[id])}, true);
});
$("ti-ruler").addEventListener("pointerdown", e => {
    if (!TI.view || e.button !== 0) return;
    tiDragStart(e);
    tiStop();
    if (e.target.id === "ti-endgrip") {
        const x0 = e.clientX, s0 = TI.title.seconds, pps = Math.max(40, $("ti-rows").clientWidth - TI_NAME_W) / s0;
        const mv = ev => tiMerged(() => tiSend({seconds: tiSnapLen(s0 + (ev.clientX - x0) / pps)}));
        const up = () => { removeEventListener("pointermove", mv); removeEventListener("pointerup", up); };
        addEventListener("pointermove", mv);
        addEventListener("pointerup", up);
        return;
    }
    const r = $("ti-ruler").getBoundingClientRect();
    tiScrub(e, r.left, r.width);
});
$("ti-mini").addEventListener("pointerdown", e => { if (e.button !== 0) return; const r = $("ti-mini").getBoundingClientRect(); tiScrub(e, r.left, r.width); });
$("ti-stagger").addEventListener("input", e => {
    $("ti-stagger-v").textContent = tiValText("stagger", Number(e.target.value));
    tiMerged(() => tiSend({stagger: Number(e.target.value)}));
});
$("ti-even").addEventListener("click", () => tiSend({times: {}, exitTimes: {}, over: {...tiClone(TI.title.over), exitLen: {}}}));

/* ================================================================== curves */

/* In or Out: which half of the animation Curves edits (Out only with an exit on). */
function tiCvSetIo(io) {
    if (io === "out" && !(TI.title && TI.title.exit.on)) io = "in";
    if (io === TI.cvIo) return;
    TI.cvIo = io;
    TI.cvSel = {};
    if (TI.local && TI.local.curve && TI.local.until == null) delete TI.local.curve;
    tiCurves();
}
$("ti-cv-io").addEventListener("click", e => {
    const b = e.target.closest("[data-v]");
    if (b && !b.disabled) tiCvSetIo(b.dataset.v);
});

/* Whose curve Curves shows: the selected words' (the inspector's Change says which),
   else the whole title's. */
function tiCurveTarget() {
    const v = TI.view, t = TI.title;
    if (TI.sel.length) {
        const ids = tiTargets();
        return {ids, word: true, label: ids.length === 1 ? v.boxes[tiIndex(ids[0])].text : `${ids.length} words`};
    }
    // The title's: every word that follows it (not one with an entrance - or exit - of its own).
    const ids = TI.cvIo === "out"
        ? v.clock.words.filter(w => w.exit && !t.over.exitAnim[w.id] && !t.over.exitCurves[w.id]).map(w => w.id)
        : v.clock.words.filter(w => w.drawn === "title").map(w => w.id);
    return {ids, word: false, label: "whole title"};
}

function tiCurveWord() {
    const tg = tiCurveTarget();
    const id = tg.ids[0] || (TI.view.boxes[0] || {}).id;
    const i = tiIndex(id);
    return i >= 0 ? tiClockWord(i) : null;
}

/* The curve set shown: the drag's own while one's going, else the target's. */
function tiCurveShown() {
    const tg = tiCurveTarget();
    if (TI.local && TI.local.curve) return TI.local.curve.obj;
    if (TI.cvIo === "out") {
        if (!tg.word && TI.title.exitCurves) return {...TI.title.exitCurves, len: (tiCurveWord() || {exit: {len: 0.27}}).exit.len};
        return tiExitCurveOf(tiCurveWord());
    }
    if (tg.word) {
        const w = tiCurveWord();
        return {len: w ? w.len : 0.27, ch: (w && (w.curve || TitlesMotion.curveOf(w.kind)).ch) || {}};
    }
    const t = TI.title;
    if (t.curves) return t.curves;
    const w = tiCurveWord();
    return {len: w ? w.len : 0.27, ch: TitlesMotion.curveOf(tiTitleKind()).ch};
}

/* The set a drag in Curves edits: a copy of what's shown, every channel there. */
function tiCurveEditing() {
    if (TI.local && TI.local.curve && TI.local.until == null) return TI.local.curve.obj;
    const shown = tiClone(tiCurveShown());
    for (const name of TitlesMotion.CHANNELS) {
        if (!shown.ch[name] || !shown.ch[name].length) {
            const r = TitlesMotion.CHANNEL_REST[name];
            shown.ch[name] = [{t: 0, v: r, hi: [0, 0], ho: [1 / 3, 0]}, {t: 1, v: r, hi: [-1 / 3, 0], ho: [0, 0]}];
        }
    }
    const tg = tiCurveTarget();
    TI.local = {...(TI.local && TI.local.until == null ? TI.local : {}), until: null, curve: {ids: tg.ids, word: tg.word, io: TI.cvIo, obj: shown}};
    return shown;
}

function tiCurveCommit() {
    const L = TI.local;
    if (!L || !L.curve) return;
    const obj = L.curve.obj;
    if (L.curve.io === "out") {
        if (L.curve.word) {
            const over = tiOver("exitCurves", L.curve.ids, tiClone(obj));
            for (const id of L.curve.ids) { delete over.exitAnim[id]; delete over.exitLen[id]; }
            tiSend({over}, true);
        } else tiSend({exitCurves: tiClone(obj)}, true);
        return;
    }
    if (L.curve.word) {
        const over = tiOver("curves", L.curve.ids, tiClone(obj));
        for (const id of L.curve.ids) { delete over.anim[id]; delete over.len[id]; }
        tiSend({over}, true);
    } else tiSend({curves: tiClone(obj)}, true);
}

function tiCurves() {
    const svg = $("ti-curves");
    if (!TI.view || !TI.size || !TI.size.cvW) return;
    const W = TI.size.cvW, H = TI.size.cvH;
    if (TI.cvIo === "out" && !TI.title.exit.on) TI.cvIo = "in";
    const out = TI.cvIo === "out";
    for (const b of $("ti-cv-io").querySelectorAll("button")) {
        b.setAttribute("aria-pressed", String(b.dataset.v === TI.cvIo));
        if (b.dataset.v === "out") { b.disabled = !TI.title.exit.on; b.title = TI.title.exit.on ? "Edit how words leave" : "Turn on an exit first (Inspector, Out)"; }
    }
    const c = tiCurveShown(), tg = tiCurveTarget(), fps = TI.view.fps || 24;
    const cw0 = tiCurveWord();
    const kind = out ? (tg.word ? (cw0 && cw0.exit ? cw0.exit.style : "") : (TI.title.exitCurves ? "custom" : TI.title.exit.style))
        : tg.word ? (cw0 || {}).kind : (TI.title.curves ? "custom" : tiTitleKind());
    $("ti-cv-sub").textContent = `· ${out ? tiExitName(kind) : kind === "custom" ? "Your own" : TI_KINDS[kind] || kind} · ${tg.label}`;
    $("ti-cv-frames").value = Math.max(1, Math.round((c.len || 0.27) * fps));
    $("ti-chs").innerHTML = Object.entries(TI_CH).map(([k, ch]) =>
        `<button type="button" class="btn ghost small ti-chbtn${k === TI.ch ? " on" : ""}${TI.shown[k] ? "" : " off"}" data-ch="${k}"><span class="ti-dot" style="background:var(--ti-ch-${k})"></span>${ch.label}</button>`).join("");
    const pad = {l: 44, r: 14, t: 12, b: 20};
    const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const keysOf = name => tiCvKeys(c, name);
    // The selection, without keys that aren't there any more (an undo, another word).
    for (const ch of Object.keys(TI.cvSel)) tiCvSet(ch, TI.cvSel[ch].filter(i => i < keysOf(ch).length));
    const range = (name, margin = 0.2) => {
        const ks = keysOf(name), rest = TitlesMotion.CHANNEL_REST[name];
        let lo = Math.min(rest, ...ks.map(k => Math.min(k.v, k.v + k.hi[1], k.v + k.ho[1])));
        let hi = Math.max(rest, ...ks.map(k => Math.max(k.v, k.v + k.hi[1], k.v + k.ho[1])));
        if (name === "alpha") { lo = Math.min(lo, 0); hi = Math.max(hi, 1); }
        const min = {scale: 0.2, alpha: 1, move: 0.05, rot: 10, blur: 0.05}[name];
        if (hi - lo < min) { const m = (lo + hi) / 2; lo = m - min / 2; hi = m + min / 2; }
        const m = (hi - lo) * margin;
        return [lo - m, hi + m];
    };
    // The values' scale stays put while curves are edited: fitted when a curve's first
    // shown (another word, In/Out) or on Fit; after an edit only grown, when a key or a
    // handle would be off the graph; never during a drag (it moved the key under the pointer).
    const view = `${TI.cvIo}|${tg.word ? tg.ids.join(",") : "title"}`;
    if (!TI.cvView || TI.cvView.key !== view || TI.cvView.fit) {
        TI.cvView = {key: view, rng: Object.fromEntries(TitlesMotion.CHANNELS.map(n => [n, range(n)]))};
    } else if (!(TI.drag && TI.drag.rng)) {
        for (const n of TitlesMotion.CHANNELS) {
            const ks = keysOf(n), [lo, hi] = TI.cvView.rng[n];
            const vals = ks.flatMap(k => [k.v, k.v + k.hi[1], k.v + k.ho[1]]);
            const vmin = Math.min(...vals), vmax = Math.max(...vals), span = hi - lo;
            // Grown past what's off the graph, with room to spare - not a nudge every edit.
            if (vmin < lo || vmax > hi) TI.cvView.rng[n] = [vmin < lo ? vmin - span * 0.25 : lo, vmax > hi ? vmax + span * 0.25 : hi];
        }
    }
    const rng = (TI.drag && TI.drag.rng) || TI.cvView.rng;
    const X = t => pad.l + t * pw, Y = (name, v) => pad.t + (1 - (v - rng[name][0]) / (rng[name][1] - rng[name][0])) * ph;
    TI.cv = {X, Y, rng, pad, pw, ph};
    const frames = Math.max(1, Math.round((c.len || 0.27) * fps));
    let s = "";
    const step = frames > 24 ? Math.ceil(frames / 12) : 1;
    for (let f = 0; f <= frames; f += step) {
        const x = X(f / frames);
        s += `<line class="ti-g-grid" x1="${x}" y1="${pad.t}" x2="${x}" y2="${pad.t + ph}"/><text class="ti-g-axis" x="${x}" y="${H - 5}" text-anchor="middle">${f}</text>`;
    }
    const [lo, hi] = rng[TI.ch];
    for (let i = 0; i <= 4; i++) {
        const val = lo + (hi - lo) * i / 4, y = Y(TI.ch, val);
        s += `<line class="ti-g-grid" x1="${pad.l}" y1="${y}" x2="${pad.l + pw}" y2="${y}"/><text class="ti-g-axis" x="${pad.l - 6}" y="${y + 3}" text-anchor="end">${TI_CH[TI.ch].fmt(val)}</text>`;
    }
    const rest = TitlesMotion.CHANNEL_REST[TI.ch];
    s += `<line class="ti-g-rest" x1="${pad.l}" y1="${Y(TI.ch, rest)}" x2="${pad.l + pw}" y2="${Y(TI.ch, rest)}"/>`;
    const path = name => {
        const ks = keysOf(name);
        let d = `M${X(ks[0].t)} ${Y(name, ks[0].v)}`;
        for (let i = 0; i < ks.length - 1; i++) {
            const a = ks[i], b = ks[i + 1];
            d += a.step ? ` H${X(b.t)} V${Y(name, b.v)}`
                : ` C${X(a.t + a.ho[0])} ${Y(name, a.v + a.ho[1])} ${X(b.t + b.hi[0])} ${Y(name, b.v + b.hi[1])} ${X(b.t)} ${Y(name, b.v)}`;
        }
        return d;
    };
    // Every shown channel's keys - the edited one's on top - and, with one key
    // selected, its handles. Like the Previews editor.
    const dots = name => keysOf(name).map((k, i) => {
        const act = name === TI.ch, cn = `var(--ti-ch-${name})`;
        return `<circle class="ti-g-key${tiCvIs(name, i) ? " on" : ""}${act ? "" : " other"}" cx="${X(k.t)}" cy="${Y(name, k.v)}" r="${act ? 6 : 4.5}" stroke="${cn}" style="color:${cn}" data-hit="key" data-ch="${name}" data-i="${i}"><title>${TI_CH[name].label} ${TI_CH[name].fmt(k.v)} at frame ${Math.round(k.t * frames)}</title></circle>`;
    }).join("");
    for (const name of TitlesMotion.CHANNELS) {
        if (TI.shown[name] && name !== TI.ch) s += `<path class="ti-g-curve other" d="${path(name)}" stroke="var(--ti-ch-${name})" data-chline="${name}"/>`;
    }
    for (const name of TitlesMotion.CHANNELS) if (TI.shown[name] && name !== TI.ch) s += dots(name);
    const col = `var(--ti-ch-${TI.ch})`;
    s += `<path class="ti-g-curve" d="${path(TI.ch)}" stroke="${col}"/>`;
    s += `<path class="ti-g-hit" d="${path(TI.ch)}" data-hit="curve"/>`;
    const one = tiCvSingle();
    if (one) {
        const ks = keysOf(one.ch), k = ks[one.i], i = one.i, cn = `var(--ti-ch-${one.ch})`;
        const x = X(k.t), y = Y(one.ch, k.v);
        if (!k.step && !(i > 0 && ks[i - 1].step)) {
            for (const hk of ["hi", "ho"]) {
                if ((hk === "hi" && i === 0) || (hk === "ho" && i === ks.length - 1)) continue;
                const hx = X(k.t + k[hk][0]), hy = Y(one.ch, k.v + k[hk][1]);
                s += `<line class="ti-g-arm" x1="${x}" y1="${y}" x2="${hx}" y2="${hy}" stroke="${cn}"/><circle class="ti-g-hand" cx="${hx}" cy="${hy}" r="4.5" stroke="${cn}" data-hit="hand" data-ch="${one.ch}" data-i="${i}" data-h="${hk}"/>`;
            }
        }
    }
    s += dots(TI.ch);
    const d = TI.drag;
    if (d && d.mode === "cv-box") {
        s += `<rect class="ti-g-box" x="${Math.min(d.x0, d.x1)}" y="${Math.min(d.y0, d.y1)}" width="${Math.abs(d.x1 - d.x0)}" height="${Math.abs(d.y1 - d.y0)}"/>`;
    }
    s += `<line class="ti-g-head" id="ti-cv-head" x1="0" y1="${pad.t}" x2="0" y2="${pad.t + ph}"/>`;
    svg.innerHTML = s;
    tiMovePlayhead();
}

$("ti-chs").addEventListener("click", e => {
    const b = e.target.closest("[data-ch]");
    if (!b) return;
    const k = b.dataset.ch;
    if (k === TI.ch) TI.shown[k] = !TI.shown[k]; else { TI.ch = k; TI.shown[k] = true; }
    if (!TI.shown[k]) delete TI.cvSel[k];
    tiCurves();
});

/* A channel's keys in a curve set (its resting line when it has none). */
function tiCvKeys(c, name) {
    if (c.ch[name] && c.ch[name].length) return c.ch[name];
    const r = TitlesMotion.CHANNEL_REST[name];
    return [{t: 0, v: r, hi: [0, 0], ho: [1 / 3, 0]}, {t: 1, v: r, hi: [-1 / 3, 0], ho: [0, 0]}];
}

/* Curves' selection: keys of any shown channel, {channel: [indexes]}. One selected
   shows its handles; a drag moves them all. Like the Previews editor. */
const tiCvCount = () => Object.values(TI.cvSel).reduce((n, a) => n + a.length, 0);
const tiCvIs = (ch, i) => (TI.cvSel[ch] || []).includes(i);
function tiCvSet(ch, list) {
    const next = {...TI.cvSel, [ch]: [...new Set(list)].sort((a, b) => a - b)};
    if (!next[ch].length) delete next[ch];
    TI.cvSel = next;
}
function tiCvSingle() {
    if (tiCvCount() !== 1) return null;
    const ch = Object.keys(TI.cvSel).find(k => TI.cvSel[k].length);
    return {ch, i: TI.cvSel[ch][0]};
}
function tiCvSelectAll() {
    const c = tiCurveShown(), sel = {};
    for (const name of TitlesMotion.CHANNELS) {
        if (TI.shown[name] || name === TI.ch) sel[name] = tiCvKeys(c, name).map((k, i) => i);
    }
    TI.cvSel = sel;
    tiCurves();
}
/* A key on the edited curve where it's double-clicked (its value, so nothing moves yet). */
function tiCvAddKey(px) {
    const obj = tiCurveEditing();
    const t = tiClamp((px - TI.cv.pad.l) / TI.cv.pw, 0.01, 0.99);
    const ks = obj.ch[TI.ch];
    const v = TitlesMotion.evalKeys(ks, t);
    const i = ks.findIndex(k => k.t > t);
    if (i <= 0 || ks.length >= 16) return;
    const span = Math.min(t - ks[i - 1].t, ks[i].t - t) / 3;
    ks.splice(i, 0, {t, v, hi: [-span, 0], ho: [span, 0]});
    delete ks[i - 1].step;
    TI.cvSel = {[TI.ch]: [i]};
    tiCurveCommit();
    tiCurves();
}
/* Delete: the selected keys go (never a curve's first or last). */
function tiCvDelete() {
    const obj = tiCurveEditing();
    let gone = 0;
    for (const [ch, idx] of Object.entries(TI.cvSel)) {
        const ks = obj.ch[ch];
        for (const i of [...idx].sort((a, b) => b - a)) if (i > 0 && i < ks.length - 1) { ks.splice(i, 1); gone++; }
    }
    TI.cvSel = {};
    if (gone) tiCurveCommit();
    else if (TI.local && TI.local.until == null) delete TI.local.curve;
    tiCurves();
}

/* Press: a key selects it (Shift adds, Ctrl toggles; pressing one already selected keeps
   the others, to drag them all), a handle turns, another channel's line edits that one,
   empty space draws a box round keys - twice quickly there adds a key. */
let tiCvPress = {at: 0, x: 0, y: 0};
$("ti-curves").addEventListener("pointerdown", e => {
    if (!TI.view || e.button !== 0 || !TI.cv) return;
    tiDragStart(e);
    tiStop();
    $("ti-curves").focus({preventScroll: true});
    const n = e.target, hit = n.dataset.hit, line = n.dataset.chline;
    const r = $("ti-curves").getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    const pin = () => $("ti-curves").setPointerCapture(e.pointerId);
    if (line) { TI.ch = line; tiCurves(); return; }
    if (hit === "key") {
        const ch = n.dataset.ch, i = Number(n.dataset.i);
        TI.ch = ch;
        if (e.ctrlKey || e.metaKey) {
            tiCvSet(ch, tiCvIs(ch, i) ? TI.cvSel[ch].filter(x => x !== i) : [...(TI.cvSel[ch] || []), i]);
            tiCurves();
            return;
        }
        let untick = null;
        if (e.shiftKey) { if (tiCvIs(ch, i)) untick = {ch, i}; else tiCvSet(ch, [...(TI.cvSel[ch] || []), i]); }
        else if (!tiCvIs(ch, i)) TI.cvSel = {[ch]: [i]};
        const obj = tiCurveEditing();
        TI.drag = {mode: "cv-keys", x0: px, y0: py, obj, orig: tiClone(obj), rng: tiClone(TI.cv.rng), untick};
        pin();
        tiCurves();
        return;
    }
    if (hit === "hand") {
        const obj = tiCurveEditing();
        TI.drag = {mode: "cv-hand", ch: n.dataset.ch, i: Number(n.dataset.i), h: n.dataset.h, obj, rng: tiClone(TI.cv.rng)};
        pin();
        return;
    }
    const now = performance.now(), last = tiCvPress;
    tiCvPress = {at: now, x: px, y: py};
    if (now - last.at < 400 && Math.hypot(px - last.x, py - last.y) < 6) { tiCvPress.at = 0; tiCvAddKey(px); return; }
    const adding = e.shiftKey || e.ctrlKey || e.metaKey;
    TI.drag = {mode: "cv-box", x0: px, y0: py, x1: px, y1: py, add: adding, before: tiClone(TI.cvSel)};
    if (!adding) TI.cvSel = {};
    pin();
    tiCurves();
});

/* A drag in Curves: the pointer's newest place, drawn once a frame. */
let tiCvMove = null, tiCvFrame = 0;
$("ti-curves").addEventListener("pointermove", e => {
    const d = TI.drag;
    if (!d || !d.mode.startsWith("cv-")) return;
    tiCvMove = {clientX: e.clientX, clientY: e.clientY, altKey: e.altKey, shiftKey: e.shiftKey};
    if (!tiCvFrame) tiCvFrame = requestAnimationFrame(tiCvApply);
});
function tiCvApply() {
    tiCvFrame = 0;
    const d = TI.drag, e = tiCvMove;
    if (!d || !e || !d.mode.startsWith("cv-")) return;
    tiCvMove = null;
    const r = $("ti-curves").getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    const {pad, pw, ph} = TI.cv;
    if (d.mode === "cv-box") {
        d.x1 = px; d.y1 = py;
        const x0 = Math.min(d.x0, px), x1 = Math.max(d.x0, px), y0 = Math.min(d.y0, py), y1 = Math.max(d.y0, py);
        const c = tiCurveShown(), sel = d.add ? tiClone(d.before) : {};
        for (const name of TitlesMotion.CHANNELS) {
            if (!(TI.shown[name] || name === TI.ch)) continue;
            tiCvKeys(c, name).forEach((k, i) => {
                const x = TI.cv.X(k.t), y = TI.cv.Y(name, k.v);
                if (x >= x0 && x <= x1 && y >= y0 && y <= y1) sel[name] = [...new Set([...(sel[name] || []), i])].sort((a, b) => a - b);
            });
        }
        TI.cvSel = sel;
        tiCurves();
        return;
    }
    if (d.mode === "cv-keys") {
        if (!d.moved && Math.hypot(px - d.x0, py - d.y0) < 3) return;
        d.moved = true;
        // Every selected key from where it was when the drag began (nothing drifts). In
        // time: none past a key that isn't selected; with an end key in, up and down only.
        let dt = (px - d.x0) / pw;
        for (const [ch, idx] of Object.entries(TI.cvSel)) {
            const os = d.orig.ch[ch];
            for (const i of idx) {
                if (i === 0 || i === os.length - 1) { dt = 0; continue; }
                let p = i - 1, q = i + 1;
                while (p > 0 && idx.includes(p)) p--;
                while (q < os.length - 1 && idx.includes(q)) q++;
                dt = Math.max(dt, os[p].t + 0.01 * (i - p) - os[i].t);
                dt = Math.min(dt, os[q].t - 0.01 * (q - i) - os[i].t);
            }
        }
        for (const [ch, idx] of Object.entries(TI.cvSel)) {
            const [lo, hi] = d.rng[ch];
            const dv = e.shiftKey ? 0 : -(py - d.y0) / ph * (hi - lo);         // Shift: in time only
            const ks = d.obj.ch[ch], os = d.orig.ch[ch];
            ks.forEach((k, i) => {
                const o = os[i];
                k.hi = o.hi.slice();
                k.ho = o.ho.slice();
                if (idx.includes(i)) {
                    k.t = o.t + dt;
                    k.v = ch === "alpha" ? tiClamp(o.v + dv, 0, 1) : o.v + dv;
                }
            });
            // Handles stay inside their neighbours.
            ks.forEach((k, i) => {
                if (i > 0) k.hi[0] = tiClamp(k.hi[0], ks[i - 1].t - k.t, 0);
                if (i < ks.length - 1) k.ho[0] = tiClamp(k.ho[0], 0, ks[i + 1].t - k.t);
            });
        }
    } else {
        d.moved = true;
        const ks = d.obj.ch[d.ch], k = ks[d.i], [lo, hi] = d.rng[d.ch];
        const t = (px - pad.l) / pw, v = lo + (1 - (py - pad.t) / ph) * (hi - lo);
        const other = d.h === "hi" ? "ho" : "hi";
        let dt = t - k.t;
        dt = d.h === "hi" ? tiClamp(dt, d.i > 0 ? ks[d.i - 1].t - k.t : 0, -0.005) : tiClamp(dt, 0.005, d.i < ks.length - 1 ? ks[d.i + 1].t - k.t : 0);
        const dv = v - k.v;
        k[d.h] = [dt, dv];
        // Smooth: the other handle turns with it (Alt breaks it).
        const has = other === "hi" ? d.i > 0 : d.i < ks.length - 1;
        if (!e.altKey && has) {
            const ol = Math.abs(k[other][0]) / Math.abs(dt);
            k[other] = [-dt * ol, -dv * ol];
        }
    }
    tiCurves();
    tiDrawTitle();
}
function tiCvEnd() {
    const d = TI.drag;
    if (!d || !d.mode.startsWith("cv-")) return;
    if (tiCvFrame) { cancelAnimationFrame(tiCvFrame); tiCvApply(); }    // where it was let go
    TI.drag = null;
    if (d.untick && !d.moved) tiCvSet(d.untick.ch, (TI.cvSel[d.untick.ch] || []).filter(x => x !== d.untick.i));
    if (d.moved && d.mode !== "cv-box") tiCurveCommit();
    // Pressed, not moved: nothing of its own to show (it would hide the next word's curve).
    else if (d.mode !== "cv-box" && TI.local && TI.local.curve && TI.local.until == null) delete TI.local.curve;
    tiCurves();                                                         // the scale refits
}
$("ti-curves").addEventListener("pointerup", tiCvEnd);
$("ti-curves").addEventListener("pointercancel", tiCvEnd);
$("ti-cv-shape").addEventListener("click", e => {
    const b = e.target.closest("[data-v]");
    if (!b || !TI.view) return;
    const obj = tiCurveEditing();
    const ks = obj.ch[TI.ch];
    const a = ks[0].v, z0 = ks[ks.length - 1].v, d0 = {scale: 0.4, rot: 20, move: 0.1, blur: 0.1, alpha: 1}[TI.ch];
    // From the first key's value to the last's; a flat channel gets a move of its own -
    // into rest for an entrance, out of rest for an exit.
    const from = z0 - a ? a : TI.cvIo === "out" ? a : z0 - d0;
    const z = z0 - a ? z0 : TI.cvIo === "out" ? a - d0 : z0, d = z - from;
    const third = (t0, t1) => (t1 - t0) / 3;
    const seg = (pts, slopes) => pts.map(([t, v], i) => {
        const prev = pts[i - 1], next = pts[i + 1], sl = slopes[i];
        return {t, v, hi: prev ? [-third(prev[0], t), -third(prev[0], t) * sl] : [0, 0], ho: next ? [third(t, next[0]), third(t, next[0]) * sl] : [0, 0]};
    });
    const shapes = {
        linear: [[[0, from], [1, z]], [d, d]],
        out: [[[0, from], [1, z]], [d * 3, 0]],
        inout: [[[0, from], [1, z]], [0, 0]],
        over: [[[0, from], [0.62, z + d * 0.12], [1, z]], [d * 2.5, 0, 0]],
        bounce: [[[0, from], [0.45, z + d * 0.18], [0.72, z - d * 0.06], [1, z]], [d * 2.5, 0, 0, 0]],
    }[b.dataset.v];
    obj.ch[TI.ch] = seg(...shapes);
    delete TI.cvSel[TI.ch];
    tiCurveCommit();
    tiCurves();
    tiDrawTitle();
});
$("ti-cv-frames").addEventListener("change", e => {
    if (!TI.view) return;
    const obj = tiCurveEditing();
    obj.len = tiClamp(Number(e.target.value) || 1, 1, 96) / (TI.view.fps || 24);
    tiCurveCommit();
});
$("ti-cv-fit").addEventListener("click", () => {
    if (TI.cvView) TI.cvView.fit = true;
    tiCurves();
});
$("ti-cv-reset").addEventListener("click", () => {
    if (!TI.view) return;
    const tg = tiCurveTarget();
    if (TI.cvIo === "out") {
        if (tg.word) tiSend({over: tiOver("exitCurves", tg.ids, null)});
        else tiSend({exitCurves: null});
    } else if (tg.word) tiSend({over: tiOver("curves", tg.ids, null)});
    else tiSend({curves: null});
});

/* =================================================================== play */

function tiStop() {
    if (TI.playing) cancelAnimationFrame(TI.playing);
    TI.playing = 0;
    $("ti-play").replaceChildren(Buddy.icon("play"));
    $("ti-play").title = "Play (Space)";
}

function tiPlay() {
    if (TI.playing) return tiStop();
    if (!TI.view) return;
    if (TI.T >= TI.title.seconds - 0.01) TI.T = 0;
    TI.lastTick = performance.now();
    $("ti-play").replaceChildren(Buddy.icon("pause"));
    $("ti-play").title = "Stop (Space)";
    const step = now => {
        if ($("panel-titles").hidden) return tiStop();
        TI.T += (now - TI.lastTick) / 1000;
        TI.lastTick = now;
        if (TI.T >= TI.title.seconds) {
            if (TI.loop) TI.T = 0;
            else { TI.T = TI.title.seconds; tiDrawTitle(); return tiStop(); }
        }
        tiDrawTitle();
        TI.playing = requestAnimationFrame(step);
    };
    TI.playing = requestAnimationFrame(step);
}

$("ti-play").replaceChildren(Buddy.icon("play"));
$("ti-play").onclick = tiPlay;
$("ti-loop").replaceChildren(Buddy.icon("refresh"));
$("ti-loop").onclick = () => { TI.loop = !TI.loop; $("ti-loop").setAttribute("aria-pressed", String(TI.loop)); };

/* ================================================================ the rest */

function tiApplyUi() {
    for (const b of $("ti-back").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.v === TI.ui.back));
    for (const b of $("ti-guides").querySelectorAll("button")) b.setAttribute("aria-pressed", String(!!TI.ui.guides[b.dataset.v]));
    $("ti-stage").dataset.back = TI.ui.back;
    $("panel-titles").classList.toggle("ti-folded", !TI.ui.dock);
    $("ti-strip").hidden = TI.ui.dock;
    $("ti-dock").hidden = !TI.ui.dock;
    for (const b of $("ti-units").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.v === (TI.ui.units || "s")));
    const x = TI.ui.dock ? TI.ui.expand : null;
    $("panel-titles").classList.toggle("ti-x-tl", x === "tl");
    $("panel-titles").classList.toggle("ti-x-cv", x === "cv");
    for (const b of document.querySelectorAll(".ti-expand")) {
        const on = x === b.dataset.x;
        b.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${TI_EXPAND_ICON[on ? "in" : "out"]}</svg>`;
        b.title = on ? "Back to its size (Esc)" : "Make it bigger";
        b.setAttribute("aria-pressed", String(on));
    }
}

/* One of the dock's cards made big - the whole width, most of the height - or back. */
const TI_EXPAND_ICON = {
    out: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    in: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>',
};
function tiSetExpand(which) {
    TI.ui.expand = which;
    tiApplyUi();
    tiMeasure();
    send("ti_ui", {expand: which});
    requestAnimationFrame(() => tiRenderAll());
}
for (const b of document.querySelectorAll(".ti-expand")) {
    b.addEventListener("click", () => tiSetExpand(TI.ui.expand === b.dataset.x ? null : b.dataset.x));
}

function tiSetDock(open) {
    TI.ui.dock = open;
    tiApplyUi();
    tiMeasure();
    send("ti_ui", {dock: open});
    requestAnimationFrame(() => tiRenderAll());
}

$("ti-back").addEventListener("click", e => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    TI.ui.back = b.dataset.v;
    tiApplyUi();
    send("ti_ui", {back: TI.ui.back});
});
$("ti-guides").addEventListener("click", e => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    TI.ui.guides = {...TI.ui.guides, [b.dataset.v]: !TI.ui.guides[b.dataset.v]};
    tiApplyUi();
    tiDrawOver();
    send("ti_ui", {guides: TI.ui.guides});
});
$("ti-units").addEventListener("click", e => {
    const b = e.target.closest("[data-v]");
    if (!b || b.dataset.v === TI.ui.units) return;
    TI.ui.units = b.dataset.v;
    tiApplyUi();
    send("ti_ui", {units: TI.ui.units});
    tiRenderAll();
});
/* Length: in the timeline's header and on the folded strip - typed, or stepped
   (seconds: half a second a click, Shift a tenth; frames: a frame, Shift ten). */
for (const n of document.querySelectorAll(".ti-len-v")) {
    n.addEventListener("change", () => {
        const v = Number(n.value);
        if (n.value !== "" && Number.isFinite(v)) tiSend({seconds: tiSnapLen(tiFrames() ? v / tiFps() : v)});
    });
}
for (const b of document.querySelectorAll(".ti-step[data-len]")) {
    b.addEventListener("click", e => {
        if (!TI.title) return;
        const by = Number(b.dataset.len) * (tiFrames() ? (e.shiftKey ? 10 : 1) / tiFps() : (e.shiftKey ? 0.1 : 0.5));
        tiSend({seconds: tiSnapLen(TI.title.seconds + by)});
    });
}
function tiLenControls() {
    const f = tiFrames(), fps = tiFps();
    for (const n of document.querySelectorAll(".ti-len-v")) {
        n.min = f ? Math.ceil(fps) : 1;
        n.max = f ? Math.floor(fps * 10) : 10;
        n.step = f ? 1 : 0.1;
        n.setAttribute("aria-label", f ? "Length in frames" : "Length in seconds");
        if (document.activeElement !== n) n.value = f ? Math.round(TI.title.seconds * fps) : TI.title.seconds.toFixed(1);
    }
    for (const u of document.querySelectorAll(".ti-len-u")) u.textContent = f ? "f" : "s";
    for (const b of document.querySelectorAll(".ti-step[data-len]")) {
        const more = Number(b.dataset.len) > 0 ? "longer" : "shorter";
        b.title = f ? `1 frame ${more} (Shift: 10 frames)` : `0.5 s ${more} (Shift: 0.1 s)`;
    }
}
$("ti-undo").replaceChildren(Buddy.icon("undo"));
$("ti-redo").replaceChildren(Buddy.icon("undo"));
$("ti-redo").firstChild.style.transform = "scaleX(-1)";
$("ti-undo").onclick = () => tiStep(TI.undo, TI.redo);
$("ti-redo").onclick = () => tiStep(TI.redo, TI.undo);
tiUndoButtons();
$("ti-fold").onclick = () => tiSetDock(false);
$("ti-unfold").onclick = () => tiSetDock(true);
$("ti-save-look").onclick = () => send("ti_save_look", {});

/* Reset all: the title back to how its look comes - its words and look kept. Asked
   first unless the person said not to (Shift-click asks again); one undo step either way. */
function tiResetAll() {
    tiStop();
    TI.sel = [];
    tiSend(tiClone(TI.view.defaults));
    Buddy.toast("Everything's back to how the look comes.", 4000, {label: "Undo", onClick: () => tiStep(TI.undo, TI.redo)});
}
$("ti-reset-all").onclick = e => {
    if (!TI.view) return;
    if (TI.ui.askReset === false && !e.shiftKey) return tiResetAll();
    const box = Buddy.el("input", {type: "checkbox", id: "ti-reset-ask"});
    box.checked = TI.ui.askReset === false;
    Buddy.modal({
        title: "Reset everything?",
        body: [
            Buddy.el("p.modal-text", {text: `Every change goes: styles, sizes and nudges, timing and exits, curves, the length, layout and position - back to how ${tiLook().label} comes. Your words and the look stay. Ctrl+Z brings it all back.`}),
            Buddy.el("label.check.small", {}, [box, " Don't ask again"]),
        ],
        buttons: [
            {label: "Cancel"},
            {label: "Reset all", kind: "danger", id: "ti-reset-yes", onClick: close => {
                const ask = !box.checked;
                if (ask !== (TI.ui.askReset !== false)) { TI.ui.askReset = ask; send("ti_ui", {askReset: ask}); }
                close();
                tiResetAll();
            }},
        ],
    });
};
$("ti-place").onclick = () => { tiStop(); send("ti_place", {}); };
$("ti-refresh").replaceChildren(Buddy.icon("refresh"));
$("ti-refresh").onclick = () => send("ti_refresh", {});

function tiChanges() {
    const t = TI.title;
    let n = Object.values(t.over || {}).reduce((a, m) => a + Object.keys(m || {}).length, 0);
    for (const k of ["lineOffset", "lineRot", "lineColour", "lineAnim", "roleColour", "roleStyle", "roleAnim", "times", "exitTimes"]) n += Object.keys(t[k] || {}).length;
    n += (t.custom ? 1 : 0) + (t.curves ? 1 : 0) + (t.exitCurves ? 1 : 0) + (t.exit.dir ? 1 : 0);
    const look = tiLook().label;
    $("ti-changes").textContent = n ? `${n} ${n === 1 ? "change" : "changes"} on top of ${look}` : `${look}, as it comes`;
}

function tiPlace() {
    if (typeof state === "undefined") return;
    $("ti-place").disabled = !!state.working;
    $("ti-place").textContent = state.working === "title" ? "Placing…" : "Place title";
    $("ti-where").textContent = !state.connected ? "Not connected to Resolve" : "At the playhead, on the topmost free video track";
}

function tiRenderAll() {
    if (!TI.view) return;
    tiMeasure();
    if (document.activeElement !== $("ti-text") && $("ti-text").value !== TI.title.text) $("ti-text").value = TI.title.text;
    $("ti-frame").textContent = `${TI.view.frame[0]}×${TI.view.frame[1]} · ${+(TI.view.fps || 24).toFixed(3)} fps`;
    tiFit();
    tiDrawTitle();
    tiDrawOver();
    tiWords();
    tiToolbar();
    tiPlaceToolbar();
    tiInspector();
    if (TI.ui.dock) {
        tiTimeline();
        tiCurves();
    } else tiMini();
    tiLenControls();
    if ((TI.view.mine || []).length !== $("ti-looks").querySelectorAll("[data-mine]").length) tiLooks();
    else tiLooksChosen();
    tiChanges();
    tiPlace();
}

new ResizeObserver(() => { if (!$("panel-titles").hidden) tiRenderAll(); }).observe($("panel-titles"));

/* A click anywhere else in the tab - not a word, the inspector, the toolbar, the editors,
   a control - lets the selected words go (as a click on the frame's empty space does).
   Popups (the colour picker) live outside the tab, so picking in them keeps it. */
const TI_KEEPS_SEL = "#ti-over, .ti-insp, #ti-ftb, #ti-words, .ti-dock, .ti-strip, .ti-foot, .ti-rail, " +
                     "button, input, select, textarea, label, a, [contenteditable]";
document.addEventListener("pointerdown", e => {
    if ($("panel-titles").hidden || !TI.view || !TI.sel.length || e.button !== 0) return;
    const n = e.target;
    if (!n.closest || !n.closest("#panel-titles") || n.closest(TI_KEEPS_SEL)) return;
    tiSelect([]);
});

addEventListener("keydown", e => {
    if ($("panel-titles").hidden || !TI.view) return;
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
    if (mod && key === "z") { e.preventDefault(); e.shiftKey ? tiStep(TI.redo, TI.undo) : tiStep(TI.undo, TI.redo); }
    else if (mod && key === "y") { e.preventDefault(); tiStep(TI.redo, TI.undo); }
    else if (mod && key === "a" && document.activeElement === $("ti-curves")) { e.preventDefault(); tiCvSelectAll(); }
    else if (e.code === "Space") { e.preventDefault(); tiPlay(); }
    else if (e.key === "Escape" && TI.ui.expand && !tiCvCount()) tiSetExpand(null);
    else if (e.key === "Escape" && tiCvCount()) { TI.cvSel = {}; tiCurves(); }
    else if (e.key === "Escape" && TI.sel.length) tiSelect([]);
    else if ((e.key === "Delete" || e.key === "Backspace") && tiCvCount()) tiCvDelete();
    else if (TI.sel.length && e.key.startsWith("Arrow")) {
        e.preventDefault();
        const step = e.shiftKey ? 20 : 4;
        const [dx, dy] = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[e.key];
        const boxes = tiBoxes();
        tiSend({over: tiOver("offset", TI.sel, (id, old) => {
            const o = old || [0, 0], s = boxes[tiIndex(id)].size;
            return [o[0] + dx / s, o[1] + dy / s];
        })});
    }
});
