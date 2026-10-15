/*
 * Audio Assistant's view. page.py reads and writes Resolve and decodes the
 * waveforms; this draws the Timeline tab - the audio tracks on one canvas,
 * the track names beside it, the selected clips' controls under it - and
 * reports what the user did: select, seek, set_levels, match, crossfade,
 * curve, remove_curve, undo. Match aims at the same LUFS this page shows
 * (levels.py does the sum again).
 *
 * A waveform arrives once per file as bytes, one per 1/rate s: 0 is
 * `floor` dB or quieter, 255 is 0 dBFS (peaks.py). Grey is the file as it
 * is; blue is the same through the clip's volume and fades, capped at the
 * clip's edge, with red where it would go past 0 dBFS. Loudness arrives
 * with it - K-weighted energy per block - and is gated here into LUFS for
 * whatever is selected, through its volume and fades, so it follows a drag.
 *
 * A change is drawn at once and sent when it's let go (a slider released, a
 * field left, a handle dropped). For HOLD_MS after, the live reads of Resolve
 * don't move that clip back - they may have been made before the write.
 * Levels that come back from a write itself always win.
 *
 * A volume curve is keys on the selected clip's line: double-click to add
 * one, drag a key (Shift: one way only) or a stretch between two, the grip to
 * move the whole line. Click a key to select it, Ctrl-click for more (Shift
 * stays the drag's axis lock), Ctrl+A for all: a drag moves the selected keys
 * together, and the right-click
 * menu, the Curve panel and Delete act on all of them. Each key has its own
 * ease, as Resolve names them: Linear, Ease in, Ease out, Ease in and out (the
 * key's shape shows which). The keys
 * are a draft (DRAFT, as heard) until Apply sends them - page.py makes them
 * Resolve keyframes. A clip's keys (keys.py) are file times: {t, db, ease};
 * curveDb() is keys.py's curve_db(). A Buddy curve clip is heard at its keys
 * with its volume on top; a clip keyed in Resolve at its keys alone.
 */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);

const RULER_H = 26, LANE_H = 64;
const MAX_PPF = 60;                   // px per frame, zoomed all the way in
const SELECTED = "#F2A33A", CLIPPING = "#E5393B", LINE = "#F2C94C";
const HOLD_MS = 1500;
const HANDLE = 4;                     // half a handle's size, px
const KEY_R = 4.5;                    // a key's radius, px

let TL = null, CLIPS = {}, PEAKS = {}, SEL = new Set(), FROM_RESOLVE = false, HEAD = null;
let OPTIONS = {presets: [], crossfades: [], leveler_modes: [], loud_block: 0.1};
let UNDO = null;
let ppf = 0, fit = true, LOADING = 0;
const HOLD = {};                      // clip id -> performance.now() until which live reads leave it alone
const GEOM = {};                      // clip id -> where it was last drawn, for the handles
// The curve being drawn: {id, keys (as heard), applying, applied: the new clip's id}, or null.
// KEYSEL: the selected keys, {id: their clip, picked: Set of indices in shownKeys()}, or null.
let DRAFT = null, KEYSEL = null;
// A curve drawn and not applied (or being applied) lives only here: the page
// is kept, never cleared away while hidden (buddy.js keepAlive, core/tool_memory.py).
Buddy.holdWhile(() => DRAFT !== null);

$("refresh").append(icon("refresh"));
for (const node of document.querySelectorAll("[data-action]")) {
    node.addEventListener("click", () => send(node.dataset.action));
}
$("undo").prepend(icon("undo"));
$("undo").onclick = () => send("undo");

// ------------------------------------------------------------ timecode --

function timecode(frame, absolute = true) {
    if (!TL || frame === null || frame === undefined) return "";
    const fps = Math.round(TL.fps);
    let f = Math.max(0, Math.round(frame));
    if (TL.drop_frame && absolute) {
        const drop = Math.round(TL.fps * 0.066666), per10 = fps * 600 - drop * 9, perMin = fps * 60 - drop;
        const d = Math.floor(f / per10), m = f % per10;
        f += drop * 9 * d + (m > drop ? drop * Math.floor((m - drop) / perMin) : 0);
    }
    const pad = n => String(n).padStart(2, "0");
    const s = Math.floor(f / fps);
    const sep = TL.drop_frame && absolute ? ";" : ":";
    return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}${sep}${pad(f % fps)}`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const signed = (v, digits = 1) => `${v > 0.00001 ? "+" : v < -0.00001 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;
const dB = v => `${signed(v)} dB`;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const clipsOf = ids => [...ids].map(id => CLIPS[id]).filter(c => c && !c.transition);

// ------------------------------------------------------------- peaks --

function b64bytes(data) {
    const bin = atob(data), out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

/* The bytes, plus coarser copies (each the max of pairs of the one before),
   so a zoomed-out column reads a handful of values, not thousands. */
function decodePeaks(p) {
    if (!p.data) return {error: p.error || "No waveform"};
    const codes = b64bytes(p.data);
    const levels = [codes];
    for (let prev = codes; prev.length > 1;) {
        const next = new Uint8Array(Math.ceil(prev.length / 2));
        for (let i = 0; i < next.length; i++) next[i] = Math.max(prev[2 * i], prev[2 * i + 1] || 0);
        levels.push(next);
        prev = next;
    }
    // Amplitude (0-1) for each byte.
    const amp = new Float32Array(256);
    for (let code = 1; code < 256; code++) amp[code] = Math.pow(10, (p.floor + code / 255 * -p.floor) / 20);
    const loud = p.loud ? new Float32Array(b64bytes(p.loud).buffer) : null;
    return {codes, levels, amp, loud, rate: p.rate, floor: p.floor};
}

/* The loudest peak byte in [a, b) - read from the level where that span is
   4-8 values long (so it can reach up to one value past either end). */
function spanMax(peak, a, b) {
    a = Math.max(0, a);
    b = Math.min(peak.codes.length, Math.max(a + 1, b));
    if (a >= b) return 0;
    const k = Math.min(peak.levels.length - 1, Math.max(0, Math.floor(Math.log2((b - a) / 4))));
    const level = peak.levels[k];
    let max = 0;
    for (let i = a >> k, end = (b - 1) >> k; i <= end; i++) if (level[i] > max) max = level[i];
    return max;
}

/* The loudest peak byte a clip plays between two timeline frames. */
function peakCode(peak, clip, fa, fb) {
    const toBucket = f => (clip.offset + (f - clip.start) / TL.fps) * peak.rate;
    return spanMax(peak, Math.floor(toBucket(fa)), Math.ceil(toBucket(fb)));
}

// --------------------------------------------------------------- curve --

/* A key's ease (keys.py's): false Linear, "in" Ease in (flat arriving), "out"
   Ease out (flat leaving), true Ease in and out - what older curves saved. */
const EASES = [{value: false, label: "Linear"}, {value: "in", label: "Ease in"},
               {value: "out", label: "Ease out"}, {value: true, label: "Ease in and out"}];
const easeOf = v => v === true || v === "in" || v === "out" ? v : false;
const easesIn = k => k.ease === true || k.ease === "in";
const easesOut = k => k.ease === true || k.ease === "out";

/* keys.py's curve_db: the dB the keys give at file time t. Straight between
   two keys, curving to flat on a side where a key eases. */
function curveDb(keys, t) {
    const n = keys.length;
    if (!n) return 0;
    if (n === 1 || t <= keys[0].t) return keys[0].db;
    if (t >= keys[n - 1].t) return keys[n - 1].db;
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (keys[mid].t <= t) lo = mid; else hi = mid; }
    const a = keys[lo], b = keys[hi];
    const u = clamp((t - a.t) / Math.max(b.t - a.t, 1e-9), 0, 1), u2 = u * u, u3 = u2 * u;
    const chord = b.db - a.db, ma = easesOut(a) ? 0 : chord, mb = easesIn(b) ? 0 : chord;
    return (2 * u3 - 3 * u2 + 1) * a.db + (u3 - 2 * u2 + u) * ma + (-2 * u3 + 3 * u2) * b.db + (u3 - u2) * mb;
}

const keyedInResolve = clip => !!(clip.keys && clip.keys.length) && !clip.curve;

/* The keys a clip is heard at (the draft's, or its own - with its volume on
   top, on a Buddy curve clip), or null: it's heard at its volume. */
function heardKeys(clip) {
    if (DRAFT && DRAFT.id === clip.id) return DRAFT.keys.length ? DRAFT.keys : null;
    if (!clip.keys || !clip.keys.length) return null;
    const add = clip.curve ? clip.volume || 0 : 0;
    return add ? clip.keys.map(k => ({t: k.t, db: k.db + add, ease: k.ease})) : clip.keys;
}

const tOf = (clip, frame) => clip.offset + (frame - clip.start) / TL.fps;      // timeline frame -> file time
const frameOfT = (clip, t) => clip.start + (t - clip.offset) * TL.fps;

/* A clip's heard dB at a frame, given heardKeys(clip). */
const heardAt = (clip, keys, frame) => keys ? curveDb(keys, tOf(clip, frame)) : clip.volume || 0;

/* How far a clip's fades turn it down at a frame (1 = not at all). */
function fadeAt(clip, frame) {
    let g = 1;
    if (clip.fade_in > 0 && frame < clip.start + clip.fade_in) g *= Math.max(0, (frame - clip.start) / clip.fade_in);
    if (clip.fade_out > 0 && frame > clip.end - clip.fade_out) g *= Math.max(0, (clip.end - frame) / clip.fade_out);
    return g;
}

// ------------------------------------------------------------ loudness --

/* A clip's 400 ms gating blocks (BS.1770: four 100 ms blocks, stepping one):
   {play: through its volume and fades, raw: the same without the volume -
   the -70 gate reads raw, so a clip turned right down still measures}.
   levels.py's clip_blocks() is the same sum. */
function gatingBlocks(clip) {
    const peak = clip.peaks ? PEAKS[clip.peaks] : null;      // its file, for the channels it plays
    if (!peak || !peak.loud) return null;
    const block = OPTIONS.loud_block || 0.1;
    const first = Math.max(0, Math.floor(clip.offset / block));
    const last = Math.min(peak.loud.length, Math.ceil((clip.offset + (clip.end - clip.start) / TL.fps) / block));
    const keys = heardKeys(clip), flat = clip.volume || 0;
    const z = [], h = [];
    for (let j = first; j < last; j++) {
        const t = (j + 0.5) * block;
        const g = fadeAt(clip, frameOfT(clip, t));
        z.push(peak.loud[j] * g * g);
        h.push(z[z.length - 1] * Math.pow(10, (keys ? curveDb(keys, t) : flat) / 10));   // power, not amplitude
    }
    const four = a => { const out = []; for (let i = 0; i + 4 <= a.length; i++) out.push((a[i] + a[i + 1] + a[i + 2] + a[i + 3]) / 4); return out; };
    return {raw: four(z), play: four(h)};
}

/* Integrated loudness of the clips together, gated (absolute -70 on the raw
   blocks, relative -10 LU); null while a waveform is still coming or it's silence. */
function lufs(clips) {
    const play = [], raw = [];
    for (const clip of clips) {
        const b = gatingBlocks(clip);
        if (b === null) return null;
        for (let i = 0; i < b.play.length; i++) { play.push(b.play[i]); raw.push(b.raw[i]); }
    }
    const L = z => -0.691 + 10 * Math.log10(Math.max(z, 1e-20));
    const loud = play.filter((z, i) => L(raw[i]) > -70);
    if (!loud.length) return null;
    const gate = L(loud.reduce((a, b) => a + b, 0) / loud.length) - 10;
    const kept = loud.filter(z => L(z) > gate);
    return kept.length ? L(kept.reduce((a, b) => a + b, 0) / kept.length) : null;
}

/* The loudest moment of clips as played - the files' peaks through their volume or keys. */
function peakDb(clips) {
    let best = null;
    for (const clip of clips) {
        const peak = clip.peaks ? PEAKS[clip.peaks] : null;      // its file, for the channels it plays
        if (!peak || !peak.codes) continue;
        const keys = heardKeys(clip);
        if (!keys) {
            const code = peakCode(peak, clip, clip.start, clip.end);
            if (!code) continue;
            const v = peak.floor + code / 255 * -peak.floor + (clip.volume || 0);
            best = best === null ? v : Math.max(best, v);
            continue;
        }
        const a = Math.max(0, Math.floor(clip.offset * peak.rate));
        const b = Math.min(peak.codes.length, Math.ceil(tOf(clip, clip.end) * peak.rate));
        for (let i = a; i < b; i++) {
            if (!peak.codes[i]) continue;
            const v = peak.floor + peak.codes[i] / 255 * -peak.floor + curveDb(keys, (i + 0.5) / peak.rate);
            best = best === null ? v : Math.max(best, v);
        }
    }
    return best;
}

// -------------------------------------------------------------- layout --

const scroller = $("scroll"), canvas = $("canvas"), ctx = canvas.getContext("2d");

function fitPpf() {
    return TL ? Math.max(0.001, (scroller.clientWidth - 24) / Math.max(1, TL.end - TL.start)) : 1;
}
const xOf = frame => (frame - TL.start) * ppf - scroller.scrollLeft;
const frameAt = x => TL.start + (x + scroller.scrollLeft) / ppf;

function setZoom(next, anchorX) {
    // No width while Sound Design shows: left as it was, for the ResizeObserver once it's back.
    if (!TL || !scroller.clientWidth) return;
    const anchor = ppf > 0 ? frameAt(anchorX) : TL.start;
    const lo = fitPpf();
    ppf = Math.min(MAX_PPF, Math.max(lo, next));
    fit = ppf <= lo * 1.001;
    $("inner").style.width = `${Math.max(scroller.clientWidth, (TL.end - TL.start) * ppf + 24)}px`;
    scroller.scrollLeft = (anchor - TL.start) * ppf - anchorX;
    placeHead();
    requestDraw();       // a burst of wheel steps draws once a frame
}

/* The playhead is its own element over the canvas, so moving it (playback,
   scrubbing) never redraws a waveform. It scrolls with the tracks. */
function placeHead() {
    const head = $("playhead");
    head.hidden = !TL || HEAD === null || HEAD === undefined;
    if (!head.hidden) head.style.transform = `translateX(${Math.round((HEAD - TL.start) * ppf)}px)`;
}

$("zoom-in").onclick = () => setZoom(ppf * 1.6, scroller.clientWidth / 2);
$("zoom-out").onclick = () => setZoom(ppf / 1.6, scroller.clientWidth / 2);
$("zoom-fit").onclick = () => setZoom(0, 0);
// Alt + scroll zooms around the mouse, as on Resolve's own timeline. Ctrl + scroll
// (what zoomed before) is still swallowed here, so it can't zoom the whole page -
// on a Mac it zooms as well, since that is how a trackpad pinch arrives.
// Qt on Windows turns an Alt + wheel into a sideways one, so the step can come
// as deltaX with deltaY 0 - whichever moved is the step; line steps count ~40px.
// Over the track names too: there it zooms around the timeline's left edge.
$("tl").addEventListener("wheel", e => {
    if (!TL || !(e.altKey || e.ctrlKey)) return;
    e.preventDefault();
    if (!e.altKey && !Buddy.IS_MAC) return;
    const step = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
    if (!step) return;
    const px = e.deltaMode === 1 ? step * 40 : e.deltaMode === 2 ? step * 400 : step;
    const x = Math.max(0, e.clientX - scroller.getBoundingClientRect().left);
    setZoom(ppf * Math.pow(1.0015, -px), x);
}, {passive: false});
scroller.addEventListener("scroll", () => requestDraw());
// A frame later: setZoom resizes what's observed (a scrollbar may come or go).
new ResizeObserver(() => requestAnimationFrame(() => { if (TL) setZoom(fit ? 0 : ppf, 0); })).observe(scroller);

let drawQueued = false;
function requestDraw() {
    if (drawQueued) return;
    drawQueued = true;
    requestAnimationFrame(() => { drawQueued = false; draw(); });
}

// ---------------------------------------------------------------- draw --

function colours() {
    const cs = getComputedStyle($("tl-card"));
    const v = name => cs.getPropertyValue(name).trim();
    return {text: v("--text"), dim: v("--text-dim"), strong: v("--text-strong"), line: v("--card-border"),
            raised: v("--raised-bg"), file: v("--wave-file"), mixed: v("--wave-mixed"), font: v("--font") || "sans-serif"};
}

/* The canvas covers only the part of the timeline on screen (and
   VIEW_MARGIN either side), moved down to it - as tall as every track, 64
   tracks were a 40 MB picture, nearly all of it out of sight. The timeline's
   full height is the inner box's; drawing and the mouse (yAt) use the
   timeline's own y. Scrolling draws again. */
const VIEW_MARGIN = 160;
const SHOWN = {top: 0, height: 0};

function onScreen(h) {
    const inner = $("inner"), r = inner.getBoundingClientRect();
    let top = 0, bottom = window.innerHeight;
    for (let p = inner.parentElement; p; p = p.parentElement) {     // what clips it
        if (getComputedStyle(p).overflowY !== "visible") {
            const q = p.getBoundingClientRect();
            top = Math.max(top, q.top);
            bottom = Math.min(bottom, q.bottom);
        }
    }
    const from = Math.max(0, Math.min(h - RULER_H, Math.floor(top - r.top - VIEW_MARGIN)));
    const to = Math.min(h, Math.max(from + RULER_H, Math.ceil(bottom - r.top + VIEW_MARGIN)));
    return {top: from, height: to - from};
}

// The mouse's height in the timeline (not in the canvas, which starts lower down).
const yAt = e => e.clientY - $("inner").getBoundingClientRect().top;

function draw() {
    if (!TL || !scroller.clientWidth) return;      // hidden: the last picture stays until it shows
    const c = colours();
    const w = scroller.clientWidth, h = RULER_H + TL.tracks.length * LANE_H;
    $("inner").style.height = `${h}px`;
    const span = onScreen(h);
    SHOWN.top = span.top;
    SHOWN.height = span.height;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(span.height * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(span.height * dpr);
        canvas.style.width = `${w}px`;
        canvas.style.height = `${span.height}px`;
    }
    canvas.style.transform = span.top ? `translateY(${span.top}px)` : "";
    ctx.setTransform(dpr, 0, 0, dpr, 0, -span.top * dpr);
    ctx.clearRect(0, span.top, w, span.height);
    ctx.font = `11px ${c.font}`;
    ctx.textBaseline = "middle";
    for (const id in GEOM) delete GEOM[id];

    if (span.top < RULER_H) drawRuler(c, w);
    TL.tracks.forEach((track, i) => {
        const y = RULER_H + i * LANE_H;
        if (y + LANE_H > span.top && y < span.top + span.height) drawLane(c, track, y, w);
    });
    $("playhead").style.height = `${h}px`;
}
document.addEventListener("scroll", e => { if (e.target !== scroller) requestDraw(); }, true);
addEventListener("resize", () => requestDraw());

function drawRuler(c, w) {
    ctx.fillStyle = c.raised;
    ctx.fillRect(0, 0, w, RULER_H);
    ctx.fillStyle = c.line;
    ctx.fillRect(0, RULER_H - 1, w, 1);
    const fps = TL.fps;
    const steps = [1, 2, 5, 10, fps, 2 * fps, 5 * fps, 10 * fps, 15 * fps, 30 * fps, 60 * fps, 120 * fps,
                   300 * fps, 600 * fps, 900 * fps, 1800 * fps, 3600 * fps];
    const step = steps.find(s => s * ppf >= 90) || steps[steps.length - 1];
    const minor = step / 5 * ppf >= 8 ? step / 5 : null;
    const first = Math.floor((frameAt(0) - TL.start) / step) * step + TL.start;
    ctx.fillStyle = c.dim;
    for (let f = first; xOf(f) < w; f += step) {
        const x = Math.round(xOf(f)) + 0.5;
        ctx.fillRect(x - 0.5, RULER_H - 9, 1, 8);
        if (minor) for (let m = 1; m < 5; m++) ctx.fillRect(Math.round(xOf(f + m * minor)), RULER_H - 5, 1, 4);
        if (x > -80) ctx.fillText(timecode(f), x + 4, 9);
    }
    for (const m of TL.markers) {
        const x = xOf(m.frame);
        if (x < -6 || x > w + 6) continue;
        ctx.fillStyle = m.color;
        ctx.beginPath(); ctx.moveTo(x - 4, RULER_H - 8); ctx.lineTo(x + 4, RULER_H - 8); ctx.lineTo(x, RULER_H - 2); ctx.closePath(); ctx.fill();
    }
}

function drawLane(c, track, y, w) {
    ctx.fillStyle = c.line;
    ctx.fillRect(0, y + LANE_H - 1, w, 1);
    ctx.globalAlpha = track.enabled ? 1 : 0.45;
    for (const clip of track.clips) {
        if (clip.transition) continue;
        const x0 = xOf(clip.start), x1 = xOf(clip.end);
        if (x1 < 0 || x0 > w) continue;
        drawClip(c, clip, x0, x1, y + 3, LANE_H - 7, w);
    }
    for (const clip of track.clips) {
        if (clip.transition) drawCrossfade(c, clip, y + 3, LANE_H - 7, w);
    }
    ctx.globalAlpha = 1;
}

/* A crossfade: its span over the cut, with the two fades crossing in it. */
function drawCrossfade(c, t, top, height, w) {
    const x0 = xOf(t.start), x1 = xOf(t.end);
    if (x1 < 0 || x0 > w) return;
    ctx.save();
    ctx.fillStyle = c.strong;
    ctx.globalAlpha *= 0.18;
    ctx.fillRect(x0, top, Math.max(2, x1 - x0), height);
    ctx.globalAlpha /= 0.18;
    ctx.strokeStyle = c.strong;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x0, top + 16); ctx.lineTo(x1, top + height - 3);
    ctx.moveTo(x0, top + height - 3); ctx.lineTo(x1, top + 16);
    ctx.stroke();
    ctx.restore();
}

/* The volume line's height for a clip volume: 0 dB at 70% of the wave area,
   +30 dB at the top, -40 dB and below at the bottom. dbAtY() is the way back. */
function volumeY(db, wTop, wBottom) {
    const frac = db >= 0 ? 0.7 + 0.3 * Math.min(db, 30) / 30 : 0.7 * Math.max(0, 1 + db / 40);
    return wBottom - frac * (wBottom - wTop);
}
function dbAtY(y, wTop, wBottom) {
    const frac = clamp((wBottom - y) / Math.max(1, wBottom - wTop), 0, 1);
    return frac >= 0.7 ? (frac - 0.7) / 0.3 * 30 : frac <= 0 ? -100 : (frac / 0.7 - 1) * 40;
}

function drawClip(c, clip, x0, x1, top, height, w) {
    const trackAlpha = ctx.globalAlpha;
    const left = Math.max(-2, x0), right = Math.min(w + 2, x1);
    const cw = right - left;
    const selected = SEL.has(clip.id);
    ctx.save();
    ctx.globalAlpha = trackAlpha * (clip.enabled ? 1 : 0.45);
    ctx.beginPath();
    ctx.roundRect(x0 + 0.5, top + 0.5, Math.max(1, x1 - x0 - 1), height, 4);
    ctx.fillStyle = clip.color ? `color-mix(in srgb, ${clip.color} 22%, ${c.raised})` : c.raised;
    ctx.fill();
    ctx.clip();

    // The waveform, under the name.
    const wTop = top + 16, wBottom = top + height - 3;
    const mid = (wTop + wBottom) / 2, half = (wBottom - wTop) / 2;
    const peak = clip.peaks ? PEAKS[clip.peaks] : null;      // its file, for the channels it plays
    if (peak && peak.codes && cw > 0) {
        // One outline per wave, filled once: a canvas call per pixel column
        // cost ~30 ms a frame to paint on 11on12.
        const x0px = Math.floor(left), n = Math.max(0, Math.ceil(right) - x0px);
        const file = new Float32Array(n), mixed = new Float32Array(n);
        const keys = heardKeys(clip), volume = Math.pow(10, (clip.volume || 0) / 20);
        const over = new Path2D();
        for (let i = 0; i < n; i++) {
            const fa = frameAt(x0px + i), fb = frameAt(x0px + i + 1);
            const amp = peak.amp[peakCode(peak, clip, fa, fb)];
            const g = keys ? Math.pow(10, curveDb(keys, tOf(clip, (fa + fb) / 2)) / 20) : volume;
            const m = amp * g * fadeAt(clip, (fa + fb) / 2);
            file[i] = Math.min(1, amp) * half;
            mixed[i] = Math.min(1, m) * half;
            if (m > 1) over.rect(x0px + i, wTop, 1, wBottom - wTop);
        }
        ctx.fillStyle = c.file;
        ctx.fill(envelope(file, x0px, mid));
        ctx.globalAlpha = trackAlpha * (clip.enabled ? 0.72 : 0.35);
        ctx.fillStyle = c.mixed;
        ctx.fill(envelope(mixed, x0px, mid));
        ctx.fillStyle = CLIPPING;
        ctx.fill(over);
        ctx.globalAlpha = trackAlpha * (clip.enabled ? 1 : 0.45);
    } else if (cw > 60) {
        ctx.fillStyle = c.dim;
        ctx.fillRect(left, Math.round(mid), cw, 1);
        const why = !clip.media ? "No audio file" : peak && peak.error ? peak.error : "Reading the waveform…";
        ctx.fillText(Buddy.t(why), Math.max(x0, 0) + 8, mid - 8);
    }

    // The volume line (through its keys) and the fades - on a selected clip, with handles to drag.
    const keys = heardKeys(clip);
    const xIn = xOf(clip.start + (clip.fade_in || 0)), xOut = xOf(clip.end - (clip.fade_out || 0));
    const lineY = frame => volumeY(heardAt(clip, keys, frame), wTop, wBottom);
    const yIn = lineY(clip.start + (clip.fade_in || 0)), yOut = lineY(clip.end - (clip.fade_out || 0));
    ctx.strokeStyle = selected ? LINE : c.strong;
    ctx.globalAlpha = trackAlpha * (selected ? 1 : 0.55);
    ctx.lineWidth = selected ? 1.5 : 1;
    ctx.beginPath();
    ctx.moveTo(x0, clip.fade_in > 0 ? wBottom : yIn);
    ctx.lineTo(xIn, yIn);
    if (keys) {
        const a = Math.max(xIn, -4), b = Math.min(xOut, w + 4);
        for (let x = a; x < b; x += 2) ctx.lineTo(x, lineY(frameAt(x)));
    }
    ctx.lineTo(xOut, yOut);
    ctx.lineTo(x1, clip.fade_out > 0 ? wBottom : yOut);
    ctx.stroke();
    ctx.globalAlpha = trackAlpha * (clip.enabled ? 1 : 0.45);

    // Name, and the clip volume when it isn't 0 dB (and is heard).
    if (cw > 24) {
        const volume = Math.abs(clip.volume || 0) >= 0.05 && !keyedInResolve(clip) ? dB(clip.volume) : "";
        const vw = volume ? ctx.measureText(volume).width + 10 : 0;
        const tx = Math.max(x0, 0) + 6;
        ctx.fillStyle = clip.enabled ? c.text : c.dim;
        const room = Math.min(x1, w) - tx - vw - 4;
        if (room > 12) ctx.fillText(ellipsize(clip.name, room), tx, top + 9);
        if (volume && cw > vw + 24) {
            ctx.fillStyle = c.strong;
            ctx.fillText(volume, Math.min(x1, w) - vw, top + 9);
        }
    }
    ctx.restore();

    // Outline outside the clip's clipping, so a selected one stands out whole.
    ctx.save();
    ctx.globalAlpha = trackAlpha;
    ctx.beginPath();
    ctx.roundRect(x0 + 0.5, top + 0.5, Math.max(1, x1 - x0 - 1), height, 4);
    ctx.strokeStyle = selected ? SELECTED : clip.color || c.line;
    ctx.lineWidth = selected ? 2 : 1;
    ctx.stroke();
    if (selected) {
        ctx.fillStyle = LINE;
        ctx.fillRect(Math.round(xIn) - HANDLE, Math.round(yIn) - HANDLE, HANDLE * 2, HANDLE * 2);
        ctx.fillRect(Math.round(xOut) - HANDLE, Math.round(yOut) - HANDLE, HANDLE * 2, HANDLE * 2);
        const g = {x0, x1, xIn, xOut, yIn, yOut, top, bottom: top + height, wTop, wBottom, lineY, keys: []};
        // Keys, and the grip that moves the whole line - on one clip at a time.
        if (SEL.size === 1) {
            for (const [i, k] of (keys || []).entries()) {
                const f = frameOfT(clip, k.t), x = xOf(f), y = lineY(f);
                if (x < x0 - 1 || x > x1 + 1) continue;
                g.keys.push({i, x, y});
                drawKey(x, y, k.ease, isPicked(clip, i));
            }
            if (xOut - xIn > 70) {
                const gx = Math.round(Math.min(xOut, w) - 18), gy = Math.round(lineY(frameAt(gx)));
                ctx.fillStyle = LINE;
                ctx.beginPath();
                ctx.roundRect(gx - 5, gy - 8, 10, 16, 3);
                ctx.fill();
                ctx.fillStyle = "rgba(0,0,0,.55)";
                for (const d of [-3, 0, 3]) ctx.fillRect(gx - 3, gy + d, 6, 1);
                g.grip = {x: gx, y: gy};
            }
        }
        GEOM[clip.id] = g;
    }
    ctx.restore();
}

/* A symmetric wave's outline: along the tops left to right, back along the bottoms. */
function envelope(heights, x0, mid) {
    const path = new Path2D();
    const n = heights.length;
    if (!n) return path;
    path.moveTo(x0, mid - heights[0]);
    for (let i = 0; i < n; i++) path.lineTo(x0 + i + 0.5, mid - Math.max(0.5, heights[i]));
    path.lineTo(x0 + n, mid);
    for (let i = n - 1; i >= 0; i--) path.lineTo(x0 + i + 0.5, mid + Math.max(0.5, heights[i]));
    path.closePath();
    return path;
}

function ellipsize(text, room) {
    if (ctx.measureText(text).width <= room) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (ctx.measureText(text.slice(0, mid) + "…").width <= room) lo = mid; else hi = mid - 1;
    }
    return lo ? text.slice(0, lo) + "…" : "";
}

// ------------------------------------------------------------- editing --

/* A change drawn now; commit() sends it. */
function preview(ids, values) {
    for (const id of ids) if (CLIPS[id]) Object.assign(CLIPS[id], values(CLIPS[id]));
    requestDraw();
    syncInspector();
}

function commit(label, ids, request) {
    const until = performance.now() + HOLD_MS;
    for (const id of ids) HOLD[id] = until;
    send("set_levels", Object.assign({label, ids: [...ids]}, request));
}

/* A key, its shape its ease: a diamond Linear, a circle Ease in and out, and
   round on one side only - the side it eases - for Ease in (left, arriving)
   or Ease out (right, leaving). A selected one is bigger, white with an
   orange ring, on a glow, so a few picked among many stand out. */
function drawKey(x, y, ease, picked) {
    const r = picked ? KEY_R + 1.5 : KEY_R;
    ctx.save();
    if (picked) {
        ctx.beginPath();
        ctx.arc(x, y, r + 5, 0, 2 * Math.PI);
        ctx.fillStyle = "rgba(242,163,58,.35)";
        ctx.fill();
    }
    ctx.beginPath();
    ease = easeOf(ease);
    if (ease === true) {
        ctx.arc(x, y, r, 0, 2 * Math.PI);
    } else if (ease === "in") {
        ctx.moveTo(x, y - r);
        ctx.arc(x, y, r, -Math.PI / 2, Math.PI / 2, true);      // round through the left
        ctx.lineTo(x + r + 1, y);
        ctx.closePath();
    } else if (ease === "out") {
        ctx.moveTo(x, y - r);
        ctx.arc(x, y, r, -Math.PI / 2, Math.PI / 2, false);     // round through the right
        ctx.lineTo(x - r - 1, y);
        ctx.closePath();
    } else {
        ctx.moveTo(x, y - r - 1); ctx.lineTo(x + r + 1, y); ctx.lineTo(x, y + r + 1); ctx.lineTo(x - r - 1, y);
        ctx.closePath();
    }
    ctx.fillStyle = picked ? "#FFFFFF" : LINE;
    ctx.fill();
    ctx.strokeStyle = picked ? SELECTED : "rgba(0,0,0,.55)";
    ctx.lineWidth = picked ? 2 : 1;
    ctx.stroke();
    ctx.restore();
}

/* The draft for a clip, started from the keys it's heard at - or null, having
   said why (another clip's curve isn't applied yet). */
function draftFor(clip) {
    if (DRAFT && DRAFT.id === clip.id) return DRAFT;
    if (DRAFT) {
        Buddy.toast(Buddy.t("Apply or discard the curve on the other clip first"), 3500);
        return null;
    }
    // The same keys in the same order, so a key picked before stays picked.
    DRAFT = {id: clip.id, keys: (heardKeys(clip) || []).map(k => ({t: k.t, db: k.db, ease: easeOf(k.ease)}))};
    return DRAFT;
}

/* The keys drawn on a clip, in the order KEYSEL counts them. */
const shownKeys = clip => DRAFT && DRAFT.id === clip.id ? DRAFT.keys : heardKeys(clip) || [];

/* The selected keys' indices on a clip, in order (only ones it still has). */
function pickedOf(clip) {
    if (!KEYSEL || KEYSEL.id !== clip.id) return [];
    const n = shownKeys(clip).length;
    return [...KEYSEL.picked].filter(i => i >= 0 && i < n).sort((a, b) => a - b);
}
const isPicked = (clip, i) => !!KEYSEL && KEYSEL.id === clip.id && KEYSEL.picked.has(i);
const pickOnly = (clip, indices) => { KEYSEL = indices.length ? {id: clip.id, picked: new Set(indices)} : null; };

/* A key clicked with Ctrl/Shift: in or out of the selection. */
function togglePick(clip, i) {
    const picked = new Set(pickedOf(clip));
    if (picked.has(i)) picked.delete(i); else picked.add(i);
    pickOnly(clip, [...picked]);
}

/* One ease for the selected keys (the right-click menu, the Curve panel). */
function setEase(clip, value) {
    const which = pickedOf(clip);
    if (which.length) editKeys(clip, keys => { for (const i of which) keys[i].ease = value; });
}

/* What the selected keys share - their ease, their level - or undefined where they differ. */
function commonKey(clip, field) {
    const keys = shownKeys(clip), which = pickedOf(clip);
    const values = which.map(i => field === "ease" ? easeOf(keys[i].ease) : keys[i][field]);
    return values.length && values.every(v => v === values[0]) ? values[0] : undefined;
}

/* fn(keys) on a clip's draft, started now if need be: false if it can't be. */
function editKeys(clip, fn) {
    const draft = draftFor(clip);
    if (!draft || draft.applying) return false;
    fn(draft.keys);
    draftChanged();
    return true;
}

/* A key at a frame of the clip (whole frames, from its start), on the line where it is. */
function addKey(clip, frame) {
    frame = clamp(Math.round(frame - clip.start), 0, clip.end - clip.start) + clip.start;
    const t = +tOf(clip, frame).toFixed(6);
    if (shownKeys(clip).some(k => Math.abs(k.t - t) < 0.5 / TL.fps)) return;
    const db = +heardAt(clip, heardKeys(clip), frame).toFixed(2);
    editKeys(clip, keys => {
        keys.push({t, db, ease: false});
        keys.sort((a, b) => a.t - b.t);
        pickOnly(clip, [keys.findIndex(k => k.t === t)]);
    });
}

/* The selected keys go. */
function deleteKeys(clip) {
    const which = pickedOf(clip);
    if (!which.length) return;
    editKeys(clip, keys => {
        for (const i of which.slice().reverse()) keys.splice(i, 1);
        KEYSEL = null;
    });
}

function draftChanged() {
    requestDraw();
    syncInspector();
    drawDraftBar();
}

function applyDraft() {
    if (!DRAFT || DRAFT.applying) return;
    const clip = CLIPS[DRAFT.id];
    if (!clip) return discardDraft();
    if (!DRAFT.keys.length && !clip.curve) return discardDraft();
    DRAFT.applying = true;
    send("curve", {id: DRAFT.id, keys: DRAFT.keys});
    draftChanged();
    renderInspector();
}

function discardDraft() {
    DRAFT = null;
    KEYSEL = null;
    draftChanged();
    renderInspector();
}

/* The bar over the tracks while a curve isn't applied yet - it may be on a clip scrolled away. */
function drawDraftBar() {
    const bar = $("draft");
    const clip = DRAFT && CLIPS[DRAFT.id];
    bar.hidden = !clip;
    if (!clip) return;
    bar.replaceChildren(
        el("span", {}, [el("b", {text: DRAFT.applying ? "Applying the curve…" : "Curve not applied yet"}), " · ",
                        el("span", {text: clip.name, translate: "no"})]),
        el("button.btn.accent", {type: "button", text: "Apply", disabled: !!DRAFT.applying, onclick: applyDraft}),
        el("button.btn.ghost", {type: "button", text: "Discard", disabled: !!DRAFT.applying, onclick: discardDraft}));
}

// --------------------------------------------------------------- mouse --

function clipAt(x, y) {
    const lane = Math.floor((y - RULER_H) / LANE_H);
    const track = TL && TL.tracks[lane];
    if (!track) return null;
    const f = frameAt(x);
    return track.clips.find(c => !c.transition && f >= c.start && f < c.end) || null;
}

/* A selected clip's handle under the pointer: a key, the grip, fade in, fade
   out or the volume line ("volume" when it's flat, "segment" through keys). */
function handleAt(x, y) {
    for (const [id, g] of Object.entries(GEOM)) {
        if (y < g.top || y > g.bottom) continue;
        const key = g.keys.find(k => Math.abs(x - k.x) <= KEY_R + 2 && Math.abs(y - k.y) <= KEY_R + 2);
        if (key) return {id, kind: "key", index: key.i};
        if (g.grip && Math.abs(x - g.grip.x) <= 7 && Math.abs(y - g.grip.y) <= 10) return {id, kind: "grip"};
        if (Math.abs(x - g.xIn) <= HANDLE + 2 && Math.abs(y - g.yIn) <= HANDLE + 2) return {id, kind: "fade_in"};
        if (Math.abs(x - g.xOut) <= HANDLE + 2 && Math.abs(y - g.yOut) <= HANDLE + 2) return {id, kind: "fade_out"};
        if (x > g.xIn && x < g.xOut && Math.abs(y - g.lineY(frameAt(x))) <= 4) {
            return {id, kind: heardKeys(CLIPS[id]) && SEL.size === 1 ? "segment" : "volume"};
        }
    }
    return null;
}

/* What the grip or a line drag does to a clip: move its volume (flat, or a
   Buddy curve's - heard on top of its keys) unless there are keys to move. */
function lineDrag(clip) {
    return (DRAFT && DRAFT.id === clip.id) || keyedInResolve(clip) ? "shift" : "volume";
}

let scrubbing = false, lastSeek = 0, DRAG = null;
function seekTo(x, final) {
    const frame = Math.round(frameAt(x));
    HEAD = frame;
    placeHead();
    const now = performance.now();
    if (final || now - lastSeek > 120) {
        lastSeek = now;
        send("seek", {frame, final: !!final});   // final: the click or drag is over (a Mac's seek may wait for it)
    }
}

canvas.addEventListener("mousedown", e => {
    if (!TL || e.button !== 0) return;
    const x = e.offsetX, y = yAt(e);
    if (y < RULER_H) {
        scrubbing = true;
        seekTo(x, false);
        return;
    }
    const handle = handleAt(x, y);
    if (handle) {
        const clip = CLIPS[handle.id];
        if (handle.kind === "grip") handle.kind = lineDrag(clip);
        if (["key", "segment", "shift"].includes(handle.kind)) {
            // The draft starts with the first move: a click only picks. Ctrl-click (Cmd on a
            // Mac) adds a key to the selection or takes it out - Shift is the drag's axis lock.
            if (handle.kind === "key") {
                if (e.ctrlKey || e.metaKey) {
                    togglePick(clip, handle.index);
                    requestDraw();
                    syncInspector();
                    e.preventDefault();
                    return;
                }
                if (!isPicked(clip, handle.index)) pickOnly(clip, [handle.index]);
            }
            DRAG = Object.assign(handle, {ids: [clip.id], x0: x, y0: y, g: GEOM[clip.id], picked: pickedOf(clip),
                                          keys0: shownKeys(clip).map(k => Object.assign({}, k))});
            requestDraw();
            syncInspector();
            e.preventDefault();
            return;
        }
        // Dragging one selected clip's volume moves every selected clip's by as much.
        const ids = handle.kind === "volume" ? [...SEL].filter(id => CLIPS[id]) : [handle.id];
        DRAG = Object.assign(handle, {ids, y0: y, base: Object.fromEntries(ids.map(id => [id, CLIPS[id].volume || 0]))});
        e.preventDefault();
        return;
    }
    const clip = clipAt(x, y);
    const add = e.ctrlKey || e.shiftKey || e.metaKey;
    if (KEYSEL && !add) {               // a click off the keys lets them go
        KEYSEL = null;
        syncInspector();
    }
    if (clip && add) {
        if (SEL.has(clip.id)) SEL.delete(clip.id); else SEL.add(clip.id);
    } else if (clip) {
        SEL = new Set([clip.id]);
    } else if (!add) {
        SEL = new Set();
    }
    select();
});

function select() {
    FROM_RESOLVE = false;
    send("select", {ids: [...SEL]});
    requestDraw();
    renderInspector();
}

addEventListener("mousemove", e => {
    const x = e.clientX - canvas.getBoundingClientRect().left, y = yAt(e);
    if (scrubbing) return seekTo(x, false);
    if (!DRAG) return;
    const clip = CLIPS[DRAG.id];
    if (!clip) return;
    if (["key", "segment", "shift"].includes(DRAG.kind)) return dragKeys(clip, x, y, e.shiftKey);
    if (DRAG.kind === "volume") {
        const step = e.shiftKey ? 0.1 : 0.5;          // dB per pixel; Shift for fine
        const delta = Math.round((DRAG.y0 - y) * step * 10) / 10;
        preview(DRAG.ids, c => ({volume: clamp(Math.round((DRAG.base[c.id] + delta) * 10) / 10, -100, 30)}));
    } else if (DRAG.kind === "fade_in") {
        const len = clip.end - clip.start;
        preview(DRAG.ids, c => ({fade_in: clamp(Math.round(frameAt(x) - c.start), 0, len - (c.fade_out || 0))}));
    } else {
        const len = clip.end - clip.start;
        preview(DRAG.ids, c => ({fade_out: clamp(Math.round(c.end - frameAt(x)), 0, len - (c.fade_in || 0))}));
    }
    DRAG.moved = true;
});
addEventListener("mouseup", e => {
    if (scrubbing) {
        scrubbing = false;
        return seekTo(e.clientX - canvas.getBoundingClientRect().left, true);
    }
    if (!DRAG) return;
    const drag = DRAG;
    DRAG = null;
    if (!drag.moved) return;
    if (["key", "segment", "shift"].includes(drag.kind)) return;       // a draft, until Apply
    if (drag.kind === "volume") {
        const delta = (CLIPS[drag.id].volume || 0) - drag.base[drag.id];
        commit("Clip volume", drag.ids, drag.ids.length > 1 ? {volume_by: delta} : {volume: CLIPS[drag.id].volume});
    } else {
        commit("Fades", drag.ids, {[drag.kind]: CLIPS[drag.id][drag.kind]});
    }
});
/* A key, a stretch between two, or the whole line, dragged. dB from the
   pointer's height; a key moves in time too, between its neighbours. Shift
   keeps a key to the way it first went. */
function dragKeys(clip, x, y, lock) {
    const d = DRAG, g = d.g;
    if (!d.moved && Math.hypot(x - d.x0, y - d.y0) < 2) return;
    if (!d.moved && (!draftFor(clip) || DRAFT.applying)) { DRAG = null; return; }
    const keys = DRAFT.keys;
    const dy = dbAtY(y, g.wTop, g.wBottom) - dbAtY(d.y0, g.wTop, g.wBottom);
    const step = v => Math.round(clamp(v, -100, 30) * 10) / 10;
    if (d.kind === "key") {
        // Every selected key moves as the one held does. In time, as far as the
        // tightest of them can go: none passes a key that isn't selected, or the clip's ends.
        if (lock && !d.axis && Math.hypot(x - d.x0, y - d.y0) > 4) d.axis = Math.abs(x - d.x0) > Math.abs(y - d.y0) ? "x" : "y";
        const moving = d.picked.length ? d.picked : [d.index], set = new Set(moving);
        const frameOf = i => Math.round(frameOfT(clip, d.keys0[i].t));
        let shift = 0;
        if (!lock || d.axis !== "y") {
            let lo = -Infinity, hi = Infinity;
            for (const i of moving) {
                let before = i - 1, after = i + 1;
                while (before >= 0 && set.has(before)) before--;
                while (after < keys.length && set.has(after)) after++;
                lo = Math.max(lo, (before >= 0 ? frameOf(before) + 1 : clip.start) - frameOf(i));
                hi = Math.min(hi, (after < keys.length ? frameOf(after) - 1 : clip.end) - frameOf(i));
            }
            shift = clamp(Math.round((x - d.x0) / ppf), Math.min(0, lo), Math.max(0, hi));
        }
        for (const i of moving) {
            const k0 = d.keys0[i], k = keys[i];
            k.db = !lock || d.axis !== "x" ? step(k0.db + dy) : k0.db;
            k.t = shift ? +tOf(clip, frameOf(i) + shift).toFixed(6) : k0.t;
        }
    } else {
        // The keys either side of the stretch (only the one, before the first or after the last), or all.
        let which = keys.map((_, i) => i);
        if (d.kind === "segment") {
            const t = tOf(clip, frameAt(d.x0));
            const after = d.keys0.findIndex(k => k.t > t);
            which = after === -1 ? [keys.length - 1] : after === 0 ? [0] : [after - 1, after];
        }
        for (const i of which) keys[i].db = step(d.keys0[i].db + dy);
    }
    d.moved = true;
    draftChanged();
}

canvas.addEventListener("dblclick", e => {
    if (!TL || yAt(e) < RULER_H || SEL.size !== 1) return;
    const clip = clipAt(e.offsetX, yAt(e));
    if (!clip || !SEL.has(clip.id) || handleAt(e.offsetX, yAt(e))?.kind === "key") return;
    addKey(clip, frameAt(e.offsetX));
});

canvas.addEventListener("contextmenu", e => {
    if (!TL || yAt(e) < RULER_H) return;
    const handle = handleAt(e.offsetX, yAt(e));
    const clip = clipAt(e.offsetX, yAt(e));
    if (handle && handle.kind === "key") {
        e.preventDefault();
        const c = CLIPS[handle.id], i = handle.index;
        if (!isPicked(c, i)) pickOnly(c, [i]);          // a key outside the selection: just that one
        draftChanged();
        const n = pickedOf(c).length, now = commonKey(c, "ease");
        Buddy.menu({x: e.clientX, y: e.clientY, items: [
            n > 1 ? {heading: `${n} keys`} : null,
            ...EASES.map(ease => ({label: ease.label, checked: now === ease.value, onClick: () => setEase(c, ease.value)})),
            {sep: true},
            {label: n > 1 ? `Delete ${n} keys` : "Delete key", danger: true, onClick: () => deleteKeys(c)},
        ].filter(Boolean)});
    } else if (clip && SEL.size === 1 && SEL.has(clip.id)) {
        e.preventDefault();
        const frame = frameAt(e.offsetX);
        Buddy.menu({x: e.clientX, y: e.clientY, items: [
            {label: "Add a key here", onClick: () => addKey(clip, frame)},
            DRAFT && DRAFT.id === clip.id ? {label: "Apply the curve", onClick: applyDraft} : null,
            DRAFT && DRAFT.id === clip.id ? {label: "Discard the curve", onClick: discardDraft} : null,
        ].filter(Boolean)});
    }
});

canvas.addEventListener("mousemove", e => {
    if (!TL || scrubbing || DRAG) return;
    const y = yAt(e);
    const handle = y >= RULER_H && handleAt(e.offsetX, y);
    canvas.style.cursor = y < RULER_H ? "ew-resize"
        : handle ? (handle.kind === "key" ? "move" : handle.kind === "fade_in" || handle.kind === "fade_out" ? "ew-resize" : "ns-resize")
        : clipAt(e.offsetX, y) ? "pointer" : "default";
});
addEventListener("keydown", e => {
    if (e.target.closest("input, select, textarea") || $("panel-timeline").hidden) return;     // not on Sound Design
    if ((e.key === "Delete" || e.key === "Backspace") && KEYSEL && CLIPS[KEYSEL.id]) {
        e.preventDefault();
        return deleteKeys(CLIPS[KEYSEL.id]);
    }
    // Ctrl+A (Cmd+A): every key on the clip whose line is shown.
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && SEL.size === 1) {
        const clip = CLIPS[[...SEL][0]];
        const n = clip ? shownKeys(clip).length : 0;
        if (n) {
            e.preventDefault();
            pickOnly(clip, [...Array(n).keys()]);
            return draftChanged();
        }
    }
    if (e.key === "Escape" && KEYSEL) {
        KEYSEL = null;
        return draftChanged();
    }
    if (e.key === "Escape" && SEL.size) {
        SEL = new Set();
        select();
    }
});

// --------------------------------------------------------- track heads --

function kindLabel(kind) {
    const k = String(kind || "").toLowerCase();
    if (k === "mono") return "Mono";
    if (k === "stereo") return "Stereo";
    const adaptive = /^adaptive(\d+)$/.exec(k);
    if (adaptive) return `Adaptive ${adaptive[1]}`;
    return kind || "";
}

function drawHeads() {
    $("heads").replaceChildren(
        el("div.tl-head.ruler", {style: `height:${RULER_H}px`}),
        ...TL.tracks.map(t => el(`div.tl-head${t.enabled ? "" : ".off"}`, {
            style: `height:${LANE_H}px`, title: Buddy.keys("Click to select the track's clips (Ctrl to add them)"),
            onclick: e => {
                const ids = t.clips.filter(c => !c.transition).map(c => c.id);
                if (e.ctrlKey || e.shiftKey || e.metaKey) ids.forEach(id => SEL.add(id)); else SEL = new Set(ids);
                select();
            },
        }, [
            el("div.name", {}, [el("b", {text: `A${t.index}`}), el("span", {text: t.name, translate: "no"})]),
            el("div.tags", {}, [
                kindLabel(t.kind) ? el("span.chip", {text: kindLabel(t.kind)}) : null,
                t.enabled ? null : el("span.chip", {text: "Off"}),
                t.locked ? el("span.chip", {text: "Locked"}) : null,
            ]),
        ])));
}

// ----------------------------------------------------------- inspector --
//
// Built when the selection changes (renderInspector); its values follow the
// clips from then on (syncInspector) without rebuilding, so a slider being
// dragged or a field being typed in is never replaced under the pointer.

let INSP = null;

const tile = (label) => {
    const value = el("b"), sub = el("span", {text: label});
    const node = el("div.tile", {}, [value, sub]);
    return {node, value, sub};
};

/* One value for all of them, or null when they differ. */
function common(clips, get) {
    const first = get(clips[0]);
    return clips.every(c => JSON.stringify(get(c)) === JSON.stringify(first)) ? first : null;
}

function slider(min, max, step) {
    return el("input.range", {type: "range", min, max, step});
}

function number(step, min, max) {
    return el("input.field.num", {type: "number", step, min, max, spellcheck: "false"});
}

function renderInspector() {
    const box = $("inspector");
    box.hidden = !TL;
    INSP = null;
    if (!TL) return;
    const chosen = clipsOf(SEL);
    if (!chosen.length) {
        box.replaceChildren(el("div.muted", {text: "Click a clip to change its audio – or select one in Resolve. Click a track's name for all its clips."}));
        return;
    }
    const many = chosen.length > 1;
    const ids = chosen.map(c => c.id);
    // A preview of a clip no longer selected stops.
    if (FXPLAY && !ids.includes(FXPLAY.id)) { AUDIO.pause(); FXPLAY = null; }
    if (FITPLAY && !ids.includes(FITPLAY.id)) { AUDIO.pause(); FITPLAY = null; }
    const I = INSP = {ids, many};

    // --- head
    I.title = el("h2.section-title", many ? {text: `${chosen.length} clips selected`}
                                          : {text: chosen[0].name, translate: "no", title: chosen[0].name});
    I.chips = el("span.chips");
    I.info = el("div.muted.small.info");

    // --- levels
    I.volMode = "set";
    I.vol = slider(-40, 30, 0.1);
    I.volNum = number(0.1, -100, 30);
    I.volModes = many ? el("div.segmented", {}, ["Set to", "Change by"].map((label, i) => el("button", {
        type: "button", text: label, "aria-pressed": String(i === 0),
        onclick: () => {
            I.volMode = i ? "by" : "set";
            for (const [j, b] of [...I.volModes.children].entries()) b.setAttribute("aria-pressed", String(j === i));
            syncInspector(true);
        },
    }))) : null;
    I.volWhy = el("span.muted.small");
    I.pan = slider(-100, 100, 1);
    I.panNum = number(1, -100, 100);
    I.fadeIn = number(1, 0);
    I.fadeOut = number(1, 0);

    const volumeNow = () => Number(I.vol.value);
    I.vol.addEventListener("input", () => {
        const v = volumeNow();
        if (I.volMode === "by") {
            if (!I.volBase) I.volBase = Object.fromEntries(chosen.map(c => [c.id, c.volume || 0]));
            preview(ids, c => ({volume: clamp(Math.round((I.volBase[c.id] + v) * 10) / 10, -100, 30)}));
        } else {
            preview(ids, () => ({volume: v}));
        }
    });
    I.vol.addEventListener("change", () => {
        const v = volumeNow();
        if (I.volMode === "by") {
            I.volBase = null;
            commit("Clip volume", ids, {volume_by: v});
            I.vol.value = 0;
        } else {
            commit("Clip volume", ids, {volume: v});
        }
    });
    I.vol.addEventListener("dblclick", () => {
        if (I.volMode === "by") return;
        preview(ids, () => ({volume: 0}));
        commit("Clip volume", ids, {volume: 0});
    });
    I.volNum.addEventListener("change", () => {
        const v = Number(I.volNum.value);
        if (!isFinite(v) || I.volNum.value === "") return syncInspector(true);
        if (I.volMode === "by") {
            preview(ids, c => ({volume: clamp((c.volume || 0) + v, -100, 30)}));
            commit("Clip volume", ids, {volume_by: v});
        } else {
            preview(ids, () => ({volume: clamp(v, -100, 30)}));
            commit("Clip volume", ids, {volume: v});
        }
    });
    I.pan.addEventListener("input", () => preview(ids, () => ({pan: Number(I.pan.value)})));
    I.pan.addEventListener("change", () => commit("Pan", ids, {pan: Number(I.pan.value)}));
    I.pan.addEventListener("dblclick", () => { preview(ids, () => ({pan: 0})); commit("Pan", ids, {pan: 0}); });
    I.panNum.addEventListener("change", () => {
        const v = Number(I.panNum.value);
        if (!isFinite(v) || I.panNum.value === "") return syncInspector(true);
        preview(ids, () => ({pan: clamp(v, -100, 100)}));
        commit("Pan", ids, {pan: v});
    });
    for (const [field, key] of [[I.fadeIn, "fade_in"], [I.fadeOut, "fade_out"]]) {
        field.addEventListener("change", () => {
            const v = Math.round(Number(field.value));
            if (!isFinite(v) || field.value === "" || v < 0) return syncInspector(true);
            const other = key === "fade_in" ? "fade_out" : "fade_in";
            preview(ids, c => ({[key]: clamp(v, 0, c.end - c.start - (c[other] || 0))}));
            commit("Fades", ids, {[key]: v});
        });
    }

    // --- loudness
    I.lufs = tile("Loudness, measured by Buddy");
    I.peak = tile("Peak, with the clip volume");
    I.preset = el("select.field", {}, OPTIONS.presets.map(p => el("option", {value: p.id, text: p.label})));
    I.each = el("input", {type: "checkbox", checked: true});
    I.match = el("button.btn.accent", {type: "button", text: "Match", onclick: () => send("match", {
        ids, preset: I.preset.value, independent: !many || I.each.checked})});

    // --- clean-up
    I.iso = el("input", {type: "checkbox"});
    I.isoAmount = slider(0, 100, 1);
    I.isoWhy = el("span.muted.small");
    I.iso.addEventListener("change", () => {
        preview(ids, c => c.isolation ? {isolation: Object.assign({}, c.isolation, {on: I.iso.checked})} : {});
        commit("Voice isolation", ids, {isolation: {on: I.iso.checked}});
    });
    I.isoNum = number(1, 0, 100);
    const isoAmount = v => preview(ids, c => c.isolation ? {isolation: Object.assign({}, c.isolation, {amount: v})} : {});
    I.isoAmount.addEventListener("input", () => isoAmount(Number(I.isoAmount.value)));
    I.isoAmount.addEventListener("change", () => commit("Voice isolation", ids, {isolation: {amount: Number(I.isoAmount.value)}}));
    I.isoNum.addEventListener("change", () => {
        const v = Math.round(Number(I.isoNum.value));
        if (!isFinite(v) || I.isoNum.value === "") return syncInspector(true);
        isoAmount(clamp(v, 0, 100));
        commit("Voice isolation", ids, {isolation: {amount: clamp(v, 0, 100)}});
    });

    I.lev = el("input", {type: "checkbox"});
    I.levMode = el("select.field", {}, OPTIONS.leveler_modes.map((m, i) => el("option", {value: i, text: m})));
    I.levFlags = {reduce_loud: el("input", {type: "checkbox"}), lift_soft: el("input", {type: "checkbox"}),
                  background: el("input", {type: "checkbox"})};
    I.levGain = slider(0, 6, 0.1);
    I.levWhy = el("span.muted.small");
    const leveler = (label, change) => {
        preview(ids, c => c.leveler ? {leveler: Object.assign({}, c.leveler, change)} : {});
        commit(label, ids, {leveler: change});
    };
    I.lev.addEventListener("change", () => leveler("Dialogue leveler", {on: I.lev.checked}));
    I.levMode.addEventListener("change", () => leveler("Dialogue leveler", {mode: Number(I.levMode.value)}));
    for (const [key, box] of Object.entries(I.levFlags)) box.addEventListener("change", () => leveler("Dialogue leveler", {[key]: box.checked}));
    I.levGain.addEventListener("input", () => { I.levGainOut.textContent = `${Number(I.levGain.value).toFixed(1)} dB`; });
    I.levGain.addEventListener("change", () => leveler("Dialogue leveler", {gain: Number(I.levGain.value)}));

    // --- cuts
    I.cuts = cutsBetween(ids);
    I.xfKind = el("select.field", {}, OPTIONS.crossfades.map(k => el("option", {value: k, text: k, translate: "no"})));
    I.xfFrames = number(1, 2, 240);
    I.xfFrames.value = 12;
    I.xfGo = el("button.btn", {type: "button", text: "Crossfade the cuts", onclick: async () => {
        const n = I.cuts.length;
        const yes = await Buddy.confirm({
            title: n === 1 ? "Add a crossfade?" : `Add ${n} crossfades?`, ok: "Add",
            text: "A crossfade goes on each cut between the selected clips, centred on it.\n\nBuddy can't take them off again – remove them in Resolve if you change your mind.",
        });
        if (yes) send("crossfade", {ids, kind: I.xfKind.value, frames: Number(I.xfFrames.value) || 12});
    }});

    const row = (label, controls, hint) => el("div.ctl", {}, [el("span.ctl-label", {text: label}), el("div.ctl-body", {}, controls), hint || null]);
    box.replaceChildren(
        el("div.head", {}, [I.title, I.chips]),
        I.info,
        el("div.groups", {}, [
            el("section.group", {}, [
                el("h3.group-title", {text: "Levels"}),
                row("Clip volume", [I.volModes, I.vol, I.volNum, el("span.unit", {text: "dB"})], I.volWhy),
                row("Pan", [I.pan, I.panNum]),
                row("Fades", [el("span.unit", {text: "In"}), I.fadeIn, el("span.unit", {text: "Out"}), I.fadeOut, el("span.unit", {text: "frames"})]),
                el("p.muted.small.hint", {text: "Or drag on the clip: the yellow line is its volume, the squares its fades. Shift for fine steps."}),
            ]),
            many ? null : curveGroup(I, chosen[0]),
            el("section.group", {}, [
                el("h3.group-title", {text: "Loudness"}),
                el("div.tiles", {}, [I.lufs.node, I.peak.node]),
                row("Match", [I.preset, I.match]),
                many ? el("label.check", {}, [I.each, " Match each clip on its own"]) : null,
            ]),
            el("section.group", {}, [
                el("h3.group-title", {text: "Clean-up"}),
                row("Voice isolation", [el("label.check", {}, [I.iso, " On"]), I.isoAmount, I.isoNum], I.isoWhy),
                row("Dialogue leveler", [el("label.check", {}, [I.lev, " On"]), I.levMode], null),
                el("div.ctl-body.flags", {}, [
                    el("label.check", {}, [I.levFlags.reduce_loud, " Reduce loud dialogue"]),
                    el("label.check", {}, [I.levFlags.lift_soft, " Lift soft dialogue"]),
                    el("label.check", {}, [I.levFlags.background, " Reduce background"]),
                ]),
                row("Output gain", [I.levGain, (I.levGainOut = el("span.unit"))], I.levWhy),
            ]),
            duckGroup(I, chosen),
            rideGroup(I, chosen),
            many ? null : effectsGroup(I, chosen[0]),
            many ? null : beatsGroup(I, chosen[0]),
            many ? null : fitGroup(I, chosen[0]),
            I.cuts.length ? el("section.group", {}, [
                el("h3.group-title", {text: "Cuts"}),
                el("p.muted.small.hint", {text: I.cuts.length === 1 ? "1 cut between the selected clips." : `${I.cuts.length} cuts between the selected clips.`}),
                row("Crossfade", [I.xfKind, I.xfFrames, el("span.unit", {text: "frames"}), I.xfGo]),
            ]) : null,
        ]),
    );
    syncInspector(true);
}

/* The Curve group: what the clip's keys are, and - for the selected keys - their
   level and ease, and Apply and Discard. With several selected, a level typed
   sets them all; the field is blank (and the ease "Mixed") where they differ. */
function curveGroup(I, clip) {
    I.curveText = el("p.muted.small.hint");
    I.keyLabel = el("span.ctl-label");
    I.keyDb = number(0.1, -100, 30);
    I.keyEase = el("select.field.ease-pick", {"aria-label": "Ease"}, [
        el("option", {value: "mixed", text: "Mixed", disabled: true}),
        ...EASES.map((ease, i) => el("option", {value: String(i), text: ease.label}))]);
    I.keyRow = el("div.ctl", {}, [I.keyLabel, el("div.ctl-body", {}, [
        I.keyDb, el("span.unit", {text: "dB"}), I.keyEase,
        el("button.btn.ghost", {type: "button", text: "Delete", onclick: () => deleteKeys(clip)})])]);
    I.keyDb.addEventListener("change", () => {
        const v = Number(I.keyDb.value), which = pickedOf(clip);
        if (!which.length || !isFinite(v) || I.keyDb.value === "") return syncInspector(true);
        editKeys(clip, keys => { for (const i of which) keys[i].db = clamp(Math.round(v * 10) / 10, -100, 30); });
    });
    I.keyEase.addEventListener("change", () => {
        const ease = EASES[Number(I.keyEase.value)];
        if (ease) setEase(clip, ease.value);
        syncInspector(true);
    });
    I.apply = el("button.btn.accent", {type: "button", text: "Apply", onclick: applyDraft});
    I.discard = el("button.btn.ghost", {type: "button", text: "Discard", onclick: discardDraft});
    I.remove = el("button.btn.ghost", {type: "button", text: "Take the curve off", onclick: async () => {
        const yes = await Buddy.confirm({title: "Take the curve off?", ok: "Take it off",
            text: "The curve clip goes, and the clip it stood in for is turned back on. Undo puts the curve back."});
        if (yes) send("remove_curve", {id: clip.id});
    }});
    I.curveButtons = el("div.ctl-body", {}, [I.apply, I.discard, I.remove]);
    return el("section.group", {}, [el("h3.group-title", {text: "Volume curve"}), I.curveText, I.keyRow, I.curveButtons]);
}

function syncCurve(I, clip) {
    if (!I.curveText) return;
    const draft = DRAFT && DRAFT.id === clip.id ? DRAFT : null;
    // Sentences, each its own node, so each is translated whole.
    let text;
    const n = draft ? draft.keys.length : (clip.keys || []).length;
    if (draft && draft.applying) text = ["Putting the curve in Resolve…"];
    else if (draft && !n) text = [clip.curve ? "No keys left – Apply takes the curve off." : "No keys yet."];
    else if (draft) text = [n === 1 ? "1 key – not in Resolve until you Apply." : `${n} keys – not in Resolve until you Apply.`];
    else if (clip.curve) {
        const under = CLIPS[clip.curve.original];
        text = [clip.curve.edited ? "A Buddy curve, with keys changed in Resolve since." : "A Buddy curve: Resolve keyframes, in a nested clip.",
                under ? `The clip it stands in for is turned off on A${trackOf(under)}.` : null,
                "Its clip volume moves the whole line."];
    } else if (keyedInResolve(clip)) {
        text = [n === 1 ? "1 keyframe from Resolve sets this clip's volume." : `${n} keyframes from Resolve set this clip's volume.`,
                "Change the line here and Apply to replace them – the clip is kept, turned off."];
    } else {
        text = ["Double-click the line on the clip to add a key, then drag it.",
                "Ctrl-click keys to select several; right-click one to ease or delete them.",
                "Apply makes the keys Resolve keyframes."];
    }
    I.curveText.replaceChildren(...text.filter(Boolean).map(Buddy.keys).flatMap((s, i) => i ? [" ", el("span", {text: s})] : [el("span", {text: s})]));
    const picked = pickedOf(clip).length;
    I.keyRow.hidden = !picked;
    if (picked) {
        I.keyLabel.textContent = picked === 1 ? "Selected key" : `${picked} selected keys`;
        const db = commonKey(clip, "db"), ease = commonKey(clip, "ease");
        if (document.activeElement !== I.keyDb) {
            I.keyDb.value = db === undefined ? "" : db.toFixed(1);
            I.keyDb.placeholder = db === undefined ? "Mixed" : "";
        }
        I.keyEase.value = ease === undefined ? "mixed" : String(EASES.findIndex(e => e.value === ease));
    }
    I.apply.hidden = I.discard.hidden = !draft;
    I.apply.disabled = I.discard.disabled = !!(draft && draft.applying);
    I.apply.disabled ||= !!(draft && !draft.keys.length && !clip.curve);
    I.remove.hidden = !!draft || !clip.curve;
    I.curveButtons.hidden = I.apply.hidden && I.remove.hidden;
}

const trackOf = clip => (TL.tracks.find(t => t.clips.includes(clip)) || {}).index;

/* The cuts between selected clips (as page.py's levels.cuts works them out) - for the count. */
function cutsBetween(ids) {
    const chosen = new Set(ids), out = [];
    for (const track of TL.tracks) {
        const clips = track.clips.filter(c => !c.transition).sort((a, b) => a.start - b.start);
        const faded = track.clips.filter(c => c.transition);
        for (let i = 0; i + 1 < clips.length; i++) {
            const a = clips[i], b = clips[i + 1];
            if (chosen.has(a.id) && chosen.has(b.id) && a.end === b.start
                    && !faded.some(t => t.start <= a.end && a.end <= t.end)) out.push(a.id);
        }
    }
    return out;
}

/* The inspector's values from the clips. force: the fields too, even the one being typed in. */
function syncInspector(force = false) {
    const I = INSP;
    if (!I) return;
    const chosen = clipsOf(I.ids);
    if (!chosen.length) return;
    const busy = n => !force && document.activeElement === n;
    const set = (node, value) => { if (!busy(node)) node.value = value === null ? "" : value; };

    // Head: chips and the facts.
    const one = chosen.length === 1 ? chosen[0] : null;
    I.chips.replaceChildren(...[
        FROM_RESOLVE ? el("span.chip", {text: "Selected in Resolve"}) : null,
        one && !one.enabled ? el("span.chip", {text: "Disabled in Resolve"}) : null,
    ].filter(Boolean));
    if (one) {
        const m = one.media;
        const format = m ? [m.rate ? `${+(m.rate / 1000).toFixed(1)} kHz` : "", m.channels ? `${m.channels} ch` : "", m.codec].filter(Boolean).join(" · ") : "";
        // The facts whole; only the path gives way, from its start, so the file name shows.
        I.info.replaceChildren(
            el("span.facts", {translate: "no", text: [timecode(one.start), timecode(one.end - one.start, false), format].filter(Boolean).join(" · ")}),
            m ? el("span.path", {translate: "no", title: m.path}, [el("bdi", {text: m.path})]) : null);
    } else {
        const frames = chosen.reduce((sum, c) => sum + (c.end - c.start), 0);
        I.info.replaceChildren(el("span", {text: `Total length ${timecode(frames, false)}`}));
    }

    // Levels. A clip keyed in Resolve isn't heard at its volume.
    const unheard = chosen.every(keyedInResolve);
    for (const n of [I.vol, I.volNum]) n.disabled = unheard;
    I.volWhy.textContent = unheard ? "Its keyframes set its volume – change the line on the clip."
        : chosen.some(keyedInResolve) ? "Clips with keyframes from Resolve are left as they are." : "";
    if (one) syncCurve(I, one);
    if (one) syncEffects(I, one);
    if (one) syncBeats(I, one);
    if (one) syncFit(I, one);
    const vol = common(chosen, c => Math.round((c.volume || 0) * 10) / 10);
    if (I.volMode === "by") {
        if (!I.volBase) set(I.vol, 0);
        I.vol.min = -20; I.vol.max = 20;
        set(I.volNum, "");
        I.volNum.placeholder = "±0.0";
    } else {
        I.vol.min = -40; I.vol.max = 30;
        set(I.vol, vol === null ? chosen[0].volume || 0 : vol);
        set(I.volNum, vol === null ? "" : vol.toFixed(1));
        I.volNum.placeholder = vol === null ? Buddy.t("Mixed") : "";
    }
    const pan = common(chosen, c => Math.round(c.pan || 0));
    set(I.pan, pan === null ? chosen[0].pan || 0 : pan);
    set(I.panNum, pan === null ? "" : pan);
    I.panNum.placeholder = pan === null ? Buddy.t("Mixed") : "";
    const fin = common(chosen, c => Math.round(c.fade_in || 0)), fout = common(chosen, c => Math.round(c.fade_out || 0));
    set(I.fadeIn, fin);
    set(I.fadeOut, fout);
    I.fadeIn.placeholder = I.fadeOut.placeholder = Buddy.t("Mixed");

    // Loudness, live through the volume and fades.
    const L = lufs(chosen), P = peakDb(chosen);
    I.lufs.value.textContent = L === null ? "–" : `${signed(L)} LUFS`;
    I.peak.value.textContent = P === null ? "–" : `${signed(P)} dBFS`;
    I.peak.node.classList.toggle("hot", P !== null && P > 0);
    I.peak.sub.textContent = P !== null && P > 0 ? "Peak – over 0 dBFS, it will clip" : "Peak, with the clip volume";

    // Clean-up: only where Resolve has it for every selected clip.
    const why = "Resolve doesn't offer it for this clip's media" + (TL.studio ? "." : " – it may need DaVinci Resolve Studio.");
    const isoAll = chosen.every(c => c.isolation), levAll = chosen.every(c => c.leveler);
    const isoSome = chosen.some(c => c.isolation), levSome = chosen.some(c => c.leveler);
    for (const n of [I.iso, I.isoAmount, I.isoNum]) n.disabled = !isoSome;
    I.isoWhy.textContent = isoAll ? "" : isoSome ? "Some of these clips don't have it; they're left as they are." : why;
    if (isoSome) {
        const withIso = chosen.filter(c => c.isolation);
        const on = common(withIso, c => c.isolation.on);
        I.iso.checked = !!on;
        I.iso.indeterminate = on === null;
        const amount = common(withIso, c => Math.round(c.isolation.amount));
        set(I.isoAmount, amount === null ? withIso[0].isolation.amount : amount);
        set(I.isoNum, amount);
        I.isoNum.placeholder = amount === null ? Buddy.t("Mixed") : "";
    } else {
        set(I.isoNum, null);
        I.isoNum.placeholder = "";
    }
    for (const n of [I.lev, I.levMode, I.levGain, ...Object.values(I.levFlags)]) n.disabled = !levSome;
    I.levWhy.textContent = levAll ? "" : levSome ? "Some of these clips don't have it; they're left as they are." : why;
    if (levSome) {
        const lv = chosen.filter(c => c.leveler);
        const on = common(lv, c => c.leveler.on);
        I.lev.checked = !!on;
        I.lev.indeterminate = on === null;
        const mode = common(lv, c => c.leveler.mode);
        if (!busy(I.levMode) && mode !== null) I.levMode.value = String(mode);
        for (const [key, box] of Object.entries(I.levFlags)) {
            const v = common(lv, c => c.leveler[key]);
            box.checked = !!v;
            box.indeterminate = v === null;
        }
        set(I.levGain, lv[0].leveler.gain);
        I.levGainOut.textContent = `${Number(lv[0].leveler.gain).toFixed(1)} dB`;
    } else {
        I.levGainOut.textContent = "";
    }
}

// -------------------------------------------------------------- ducking --
//
// The Ducking group: the selected clips dip under the speech on the tracks
// ticked (page.py's on_duck, ducking.py). One clip: the curve comes back as
// a draft to see and change, then Apply. Several: each gets its curve now.

// What was set last, for the next clips: {under: Set of track indices (null: not chosen yet), depth, fade, pause}.
const DUCK = {under: null, depth: -12, fade: 0.3, pause: 0.8};

function duckGroup(I, chosen) {
    const mine = new Set(chosen.map(trackOf));
    const others = TL.tracks.filter(t => !mine.has(t.index));
    if (!others.length) return null;
    if (OPTIONS.ducking && DUCK.under === null) Object.assign(DUCK, OPTIONS.ducking);
    // At first, every other track with something playing on it.
    const under = DUCK.under || new Set(others.filter(t => t.enabled && t.clips.some(c => !c.transition && c.enabled)).map(t => t.index));
    DUCK.under = under;
    const boxes = others.map(t => {
        const box = el("input", {type: "checkbox", checked: under.has(t.index)});
        box.addEventListener("change", () => { if (box.checked) under.add(t.index); else under.delete(t.index); });
        return el("label.check.duck-track", {}, [box, el("b", {text: `A${t.index}`}), el("span", {text: t.name, translate: "no"})]);
    });
    const field = (key, step, min, max, unit) => {
        const n = number(step, min, max);
        n.value = DUCK[key];
        n.addEventListener("change", () => {
            const v = Number(n.value);
            if (n.value === "" || !isFinite(v)) n.value = DUCK[key];
            else DUCK[key] = n.value = clamp(v, min, max);
        });
        return [n, el("span.unit", {text: unit})];
    };
    const many = chosen.length > 1;
    const go = el("button.btn", {type: "button", text: many ? `Duck ${chosen.length} clips` : "Make the ducking curve",
        onclick: async () => {
            if (DRAFT && !many && DRAFT.id !== chosen[0].id) {
                return Buddy.toast(Buddy.t("Apply or discard the curve on the other clip first"), 3500);
            }
            if (many && !await Buddy.confirm({title: `Duck ${chosen.length} clips?`, ok: "Duck them",
                    text: "Each clip gets its ducking curve in Resolve straight away – a curve clip on a Buddy track, the clip under it turned off. Undo takes them off one at a time."})) return;
            send("duck", {ids: chosen.map(c => c.id), under: [...under], depth: DUCK.depth, fade: DUCK.fade, pause: DUCK.pause});
        }});
    return el("section.group", {}, [
        el("h3.group-title", {text: "Ducking"}),
        el("p.muted.small.hint", {text: many ? "Turns these clips down while someone's talking on the tracks you tick."
                                             : "Turns this clip down while someone's talking on the tracks you tick. You see the curve first; Apply puts it in Resolve."}),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Under"}), el("div.ctl-body.duck-tracks", {}, boxes)]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Down by"}), el("div.ctl-body", {}, field("depth", 1, -40, -1, "dB"))]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Fade"}), el("div.ctl-body", {}, field("fade", 0.05, 0.05, 3, "s"))]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Stay down through pauses up to"}), el("div.ctl-body", {}, field("pause", 0.1, 0, 5, "s"))]),
        el("div.ctl-body", {}, [go]),
    ]);
}

/* One clip's ducking or riding curve: a draft, as if drawn - changed, applied or discarded like any. */
Buddy.on("draft", d => {
    const clip = CLIPS[d.id];
    if (!clip) return;
    if (DRAFT && DRAFT.id !== d.id) return Buddy.toast(Buddy.t("Apply or discard the curve on the other clip first"), 3500);
    DRAFT = {id: d.id, keys: d.keys.map(k => ({t: k.t, db: k.db, ease: easeOf(k.ease)}))};
    KEYSEL = null;
    draftChanged();
    renderInspector();
    Buddy.toast(d.note, 4000);
});

/* The Riding group: the selected voice clips evened out phrase by phrase
   (page.py's on_ride, riding.py) - a draft on one clip, applied on several. */
const RIDE = {amount: null, most: null};

function rideGroup(I, chosen) {
    if (RIDE.amount === null) Object.assign(RIDE, OPTIONS.riding || {amount: 0.7, most: 6});
    const many = chosen.length > 1;
    const amount = slider(0, 100, 5), pct = el("span.unit.fx-pct");
    const show = () => { pct.textContent = `${amount.value}%`; };
    amount.value = Math.round(RIDE.amount * 100);
    show();
    amount.addEventListener("input", show);
    amount.addEventListener("change", () => { RIDE.amount = Number(amount.value) / 100; });
    const most = number(0.5, 1, 15);
    most.value = RIDE.most;
    most.addEventListener("change", () => {
        const v = Number(most.value);
        if (most.value === "" || !isFinite(v)) most.value = RIDE.most; else RIDE.most = most.value = clamp(v, 1, 15);
    });
    const go = el("button.btn", {type: "button", text: many ? `Even out ${chosen.length} clips` : "Make the riding curve",
        onclick: async () => {
            if (DRAFT && !many && DRAFT.id !== chosen[0].id) {
                return Buddy.toast(Buddy.t("Apply or discard the curve on the other clip first"), 3500);
            }
            if (many && !await Buddy.confirm({title: `Even out ${chosen.length} clips?`, ok: "Even them out",
                    text: "Each clip gets its riding curve in Resolve straight away – a curve clip on a Buddy track, the clip under it turned off. Undo takes them off one at a time."})) return;
            send("ride", {ids: chosen.map(c => c.id), amount: RIDE.amount, most: RIDE.most});
        }});
    return el("section.group", {}, [
        el("h3.group-title", {text: "Riding"}),
        el("p.muted.small.hint", {text: "Evens out a voice phrase by phrase – quiet ones up, loud ones down – changing only in the pauses."}),
        el("div.ctl", {}, [el("span.ctl-label", {text: "How much"}), el("div.ctl-body", {}, [amount, pct])]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "At most"}), el("div.ctl-body", {}, [most, el("span.unit", {text: "dB up or down"})])]),
        el("div.ctl-body", {}, [go]),
    ]);
}

// ------------------------------------------------------------- the mix --
//
// Mix to a target (page.py's mix_*): the whole timeline rendered and
// measured, a plan shown - one gain for every clip - and Apply.

const MIX = {open: false, preset: "youtube", plan: null, error: ""};

$("mix-open").addEventListener("click", () => { MIX.open = !MIX.open; drawMix(); });

function drawMix() {
    const box = $("mix");
    box.hidden = !MIX.open || !TL;
    if (box.hidden) return;
    const busy = FXBUSY.doing === "mix";
    const presets = (OPTIONS.presets || []).filter(p => p.id !== "peak");
    const pick = el("select.field", {disabled: busy}, presets.map(p => el("option", {value: p.id, text: p.label, selected: p.id === MIX.preset})));
    pick.addEventListener("change", () => { MIX.preset = pick.value; MIX.plan = null; MIX.error = ""; drawMix(); });
    const measure = el("button.btn", {type: "button", disabled: busy || (FXBUSY.doing && !busy),
        text: busy ? "Measuring…" : MIX.plan ? "Measure again" : "Measure the mix",
        onclick: () => { MIX.plan = null; MIX.error = ""; send("mix_measure", {preset: MIX.preset}); drawMix(); }});
    const parts = [
        el("div.row.mix-head", {}, [el("h2.section-title", {text: "Mix to a target"}), el("span.spacer"),
            el("button.btn.ghost.icon", {type: "button", title: "Close", onclick: () => { MIX.open = false; drawMix(); }}, [icon("close")])]),
        el("p.muted.small.hint", {text: "Renders the whole timeline in Resolve, measures it, then moves every clip by the same amount – so the mix keeps its balance – to the target, without its peaks going over the ceiling. Your Deliver settings stay as they are."}),
        el("div.ctl-body", {}, [pick, measure]),
    ];
    if (busy) parts.push(el("div.fx-bar", {}, el("i", {style: `width:${Math.round((FXBUSY.progress || 0) * 100)}%`})));
    if (MIX.error) parts.push(el("p.mix-error", {text: MIX.error}));
    const p = MIX.plan;
    if (p && !busy) {
        const after = p.lufs + p.gain, peak = p.peak + p.gain;
        parts.push(el("div.tiles", {}, [
            tileOf("Loudness now", `${signed(p.lufs)} LUFS`), tileOf("True peak now", `${signed(p.peak)} dBTP`),
            tileOf("Every clip", `${signed(p.gain)} dB`),
            tileOf("Afterwards", `${signed(after)} LUFS · ${signed(peak)} dBTP`)]));
        const notes = [];
        if (Math.abs(p.gain) < 0.05) notes.push(Buddy.t("The mix is on target already."));
        if (p.limited) notes.push(Buddy.t(`The target needs ${signed(p.wanted)} dB, but the peaks would go over ${signed(p.ceiling)} dBTP – a limiter in Fairlight gets the rest.`));
        if (p.keyed) notes.push(Buddy.t(p.keyed === 1 ? "1 clip with volume keyframes from Resolve is left as it is." : `${p.keyed} clips with volume keyframes from Resolve are left as they are.`));
        if (p.clamped) notes.push(Buddy.t(p.clamped === 1 ? "1 clip can't go that far – Resolve's clip volume stops at +30 dB." : `${p.clamped} clips can't go that far – Resolve's clip volume stops at +30 dB.`));
        notes.push(Buddy.t("Compressors on Fairlight tracks or buses change what a gain does – measure again afterwards."));
        parts.push(el("p.muted.small.hint", {}, notes.flatMap((n, i) => i ? [" ", n] : [n])));
        parts.push(el("div.ctl-body", {}, [el("button.btn.accent", {type: "button", disabled: Math.abs(p.gain) < 0.05 || !p.clips,
            text: p.clips === 1 ? "Move 1 clip" : `Move ${p.clips} clips`,
            onclick: () => { send("mix_apply", {gain: p.gain}); MIX.plan = null; drawMix(); }})]));
    }
    box.replaceChildren(...parts);
}

function tileOf(label, value) {
    return el("div.tile", {}, [el("b", {text: value, translate: "no"}), el("span", {text: label})]);
}

Buddy.on("mix_plan", p => {
    MIX.plan = p.error ? null : p;
    MIX.error = p.error || "";
    MIX.open = true;
    drawMix();
});
Buddy.on("mix_done", () => drawMix());

// -------------------------------------------------------------- automix --
//
// Automix (page.py's on_automix, automix.py): a mic on each track ticked,
// each one up only while its person talks. What it would do is shown as
// the ticks and settings change; Apply puts a curve on each clip it changes.

const AUTO = {open: false, tracks: new Set(), depth: null, hold: null, plan: null, error: "", timer: 0, out: null};

$("automix-open").addEventListener("click", () => { AUTO.open = !AUTO.open; drawAutomix(); askAutomix(); });

/* What it would do, asked again (a moment after the last change). */
function askAutomix() {
    clearTimeout(AUTO.timer);
    if (!AUTO.open || !TL) return;
    const ticked = [...AUTO.tracks].filter(i => TL.tracks.some(t => t.index === i));
    AUTO.plan = null;
    AUTO.error = "";
    drawAutomixResult();
    if (ticked.length < 2) return;
    AUTO.timer = setTimeout(() => send("automix", {tracks: ticked, depth: AUTO.depth, hold: AUTO.hold}), 250);
}

function drawAutomix() {
    const box = $("automix");
    box.hidden = !AUTO.open || !TL;
    if (box.hidden) return;
    if (AUTO.depth === null) Object.assign(AUTO, OPTIONS.automix || {depth: -15, hold: 0.8});
    const boxes = TL.tracks.map(t => {
        const tick = el("input", {type: "checkbox", checked: AUTO.tracks.has(t.index), disabled: !t.enabled});
        tick.addEventListener("change", () => {
            if (tick.checked) AUTO.tracks.add(t.index); else AUTO.tracks.delete(t.index);
            askAutomix();
        });
        return el("label.check.duck-track", {}, [tick, el("b", {text: `A${t.index}`}), el("span", {text: t.name, translate: "no"})]);
    });
    const field = (key, step, min, max, unit) => {
        const n = number(step, min, max);
        n.value = AUTO[key];
        n.addEventListener("change", () => {
            const v = Number(n.value);
            if (n.value === "" || !isFinite(v)) n.value = AUTO[key];
            else { AUTO[key] = n.value = clamp(v, min, max); askAutomix(); }
        });
        return [n, el("span.unit", {text: unit})];
    };
    const parts = [
        el("div.row.mix-head", {}, [el("h2.section-title", {text: "Automix"}), el("span.spacer"),
            el("button.btn.ghost.icon", {type: "button", title: "Close", onclick: () => { AUTO.open = false; drawAutomix(); }}, [icon("close")])]),
        el("p.muted.small.hint", {text: "For a podcast or interview with a mic for each person: each mic is up only while its person talks, and down the rest of the time – so the other voices it picks up, and its room noise, go. Buddy listens to the whole recording first, so a mic opens just before its person starts. Works best on the long recordings, before you cut them."}),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Mics"}), el("div.ctl-body.duck-tracks", {}, boxes)]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Others down by"}), el("div.ctl-body", {}, field("depth", 1, -40, -3, "dB"))]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Stay up through pauses up to"}), el("div.ctl-body", {}, field("hold", 0.1, 0, 5, "s"))]),
        AUTO.out = el("div.automix-out"),
    ];
    box.replaceChildren(...parts);
    drawAutomixResult();
}

/* What it would do (or why not) - only this part changes as the ticks and settings do. */
function drawAutomixResult() {
    if (!AUTO.out || $("automix").hidden) return;
    const parts = [];
    const ticked = [...AUTO.tracks].filter(i => TL.tracks.some(t => t.index === i));
    if (ticked.length < 2) {
        parts.push(el("p.muted.small.hint", {text: "Tick two or more tracks – one for each mic."}));
    } else if (AUTO.error) {
        parts.push(el("p.mix-error", {text: AUTO.error}));
    } else if (AUTO.plan) {
        const p = AUTO.plan;
        const pct = v => `${Math.round(v * 100)}%`;
        parts.push(el("div.tiles", {}, [
            ...p.tracks.map(t => {
                const track = TL.tracks.find(x => x.index === t.index);
                return el("div.tile", {}, [el("b", {text: Buddy.t(`Talks ${pct(t.talk)}`)}),
                    el("span", {translate: "no", text: `A${t.index} ${track ? track.name : ""}`}),
                    el("span.muted.small", {text: Buddy.t(`Up ${pct(t.up)} of the time`)})]);
            }),
            tileOf("Two at once", pct(p.together)),
        ]));
        parts.push(el("div.ctl-body", {}, [el("button.btn.accent", {type: "button", disabled: !p.clips,
            text: p.clips === 1 ? "Apply to 1 clip" : `Apply to ${p.clips} clips`,
            onclick: async () => {
                if (DRAFT) return Buddy.toast(Buddy.t("Apply or discard the curve on the other clip first"), 3500);
                if (!await Buddy.confirm({title: "Automix these mics?", ok: "Automix",
                        text: "Each clip that changes gets its curve in Resolve – a curve clip on a Buddy track, the clip under it turned off. Undo takes them off one at a time."})) return;
                send("automix", {tracks: ticked, depth: AUTO.depth, hold: AUTO.hold, apply: true});
            }})]));
    } else {
        parts.push(el("p.muted.small.hint", {text: "Listening…"}));
    }
    AUTO.out.replaceChildren(...parts);
}

Buddy.on("automix_plan", p => {
    if (p.done) { AUTO.open = false; AUTO.plan = null; return drawAutomix(); }
    AUTO.plan = p.error ? null : p;
    AUTO.error = p.error || "";
    drawAutomixResult();
});

// ---------------------------------------------------------------- beats --
//
// The Beats group: markers on the beats (or bars) of the selected music clip
// (page.py's on_beats, beats.py). Buddy's own markers can all be cleared.

const BEATS = {every: "beat", tempo: "auto", color: null};

function beatsGroup(I, clip) {
    if (!clip.media) return null;
    BEATS.color = BEATS.color || OPTIONS.beat_color || "Cyan";
    const pick = (key, choices) => {
        const s = el("select.field", {}, choices.map(([value, label]) => el("option", {value, text: label, selected: BEATS[key] === value})));
        s.addEventListener("change", () => { BEATS[key] = s.value; });
        return s;
    };
    const colors = pick("color", (OPTIONS.marker_colors || []).map(c => [c.name, c.name]));
    const swatch = el("i.beat-swatch");
    const paint = () => { swatch.style.background = ((OPTIONS.marker_colors || []).find(c => c.name === BEATS.color) || {}).hex || "#888"; };
    colors.addEventListener("change", paint);
    paint();
    I.beatGo = el("button.btn", {type: "button", text: "Mark the beats",
        onclick: () => send("beats", {id: clip.id, every: BEATS.every, tempo: BEATS.tempo, color: BEATS.color})});
    const clear = I.beatClear = el("button.btn.ghost", {type: "button", onclick: async () => {
        if (await Buddy.confirm({title: "Clear the beat markers?", ok: "Clear",
                text: "Every beat marker Buddy put on this timeline comes off – your own markers stay. Undo puts them back."})) send("clear_beats");
    }});
    return el("section.group", {}, [
        el("h3.group-title", {text: "Beats"}),
        el("p.muted.small.hint", {text: "Listens to this clip and puts a timeline marker on each beat – to cut on. Works on music with a clear beat."}),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Mark"}), el("div.ctl-body", {}, [pick("every", [["beat", "Every beat"], ["bar", "Every bar (4 beats)"]])])]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Tempo"}), el("div.ctl-body", {}, [pick("tempo", [["auto", "As Buddy hears it"], ["double", "Twice as fast"], ["half", "Half as fast"]])])]),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Colour"}), el("div.ctl-body", {}, [swatch, colors])]),
        el("div.ctl-body", {}, [I.beatGo, clear]),
    ]);
}

function syncBeats(I, clip) {
    if (!I.beatGo) return;
    const busy = FXBUSY.doing === "beats" && FXBUSY.id === clip.id;
    I.beatGo.disabled = !!FXBUSY.doing;
    I.beatGo.textContent = Buddy.t(busy ? "Listening…" : "Mark the beats");
    const ours = (TL.markers || []).filter(m => m.beat).length;
    I.beatClear.hidden = !ours;
    I.beatClear.textContent = Buddy.t(ours === 1 ? "Clear 1 beat marker" : `Clear ${ours} beat markers`);
}

// ------------------------------------------------------------------ fit --
//
// Fit to length (page.py's fit_*, fit.py): a music clip made to end, on its
// own ending, at the length asked - whole bars cut out (or played again)
// where the music sounds the same either side. Find lists the ways to do
// it; Hear the cut plays the result around its cut; Apply puts the fitted
// song on a Buddy track, the clip under it turned off.

const FIT = {id: null, frames: null, plans: null, pick: 0, bpm: null, error: "", asked: null};
let FITPLAY = null;                   // {id, plan} while the fitted song's preview plays

/* A length typed: "45", "45.5" (seconds), "1:30" (minutes and seconds),
   "1:02:03" or a timecode "00:01:30:12" - in frames, or null. */
function parseLength(text) {
    const parts = String(text || "").trim().replace(/;/g, ":").split(":");
    if (!parts[0] || parts.some(p => p === "" || !isFinite(Number(p)))) return null;
    const n = parts.map(Number), fps = TL.fps;
    if (n.length === 1) return Math.round(n[0] * fps);
    if (n.length === 2) return Math.round((n[0] * 60 + n[1]) * fps);
    if (n.length === 3) return Math.round((n[0] * 3600 + n[1] * 60 + n[2]) * fps);
    if (n.length === 4) return Math.round((n[0] * 3600 + n[1] * 60 + n[2]) * fps + n[3]);
    return null;
}

const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function fitWhat(p) {
    if (p.a === null) return "No cut";
    if (p.b > p.a) return p.bars === 1 ? `Cuts 1 bar at ${mmss(p.a)}` : `Cuts ${p.bars} bars at ${mmss(p.a)}`;
    if (p.times === 1) return p.bars === 1 ? `Plays 1 bar again at ${mmss(p.a)}` : `Plays ${p.bars} bars again at ${mmss(p.a)}`;
    return `Plays ${p.bars} bars ${p.times} more times at ${mmss(p.a)}`;
}

function fitNotes(p) {
    const out = [];
    if (p.start_s > 0.05) out.push(`Starts ${p.start_s.toFixed(1)} s late`);
    if (p.start_s < -0.005) out.push(`Starts ${(-p.start_s).toFixed(2)} s into the song`);
    if (p.end_s > 0.05) out.push(`${p.end_s.toFixed(1)} s of silence after it ends`);
    return out;
}

const FIT_SOUND = {seamless: "Seamless", smooth: "Smooth", heard: "May be heard"};

function fitGroup(I, clip) {
    if (!clip.media || clip.curve) return null;
    if (clip.fit) {
        const was = clip.fit.song_s ? mmss(clip.fit.song_s) : "";
        I.fitOff = el("button.btn.ghost", {type: "button", text: "Take the edit off", onclick: async () => {
            if (await Buddy.confirm({title: "Take the edit off?", ok: "Take it off",
                    text: "The fitted music goes, and the clip it was made from is turned back on. Undo puts it back."})) {
                fitStop();
                send("fit_remove", {id: clip.id});
            }
        }});
        return el("section.group", {}, [
            el("h3.group-title", {text: "Fit to length"}),
            el("p.muted.small.hint", {text: was ? `Fitted by Buddy – ${was} of music made ${mmss(clip.fit.length_s)}.` : "Fitted by Buddy."}),
            el("div.ctl-body", {}, [I.fitOff])]);
    }
    if (FIT.id !== clip.id) {
        Object.assign(FIT, {id: clip.id, plans: null, pick: 0, error: "", asked: null,
            frames: TL.video_end && TL.video_end - clip.start > TL.fps * 2 ? TL.video_end - clip.start : clip.end - clip.start});
    }
    I.fitLen = el("input.field.fit-len", {type: "text", spellcheck: false, value: timecode(FIT.frames, false), translate: "no"});
    I.fitLen.addEventListener("change", () => {
        const f = parseLength(I.fitLen.value);
        if (f && f > 0) { FIT.frames = f; FIT.plans = null; FIT.error = ""; }
        I.fitLen.value = timecode(FIT.frames, false);
        syncFit(I, clip);
    });
    const setLen = f => { FIT.frames = f; FIT.plans = null; FIT.error = ""; I.fitLen.value = timecode(f, false); syncFit(I, clip); };
    const next = (TL.markers || []).map(m => m.frame).filter(f => f > clip.start + TL.fps * 2).sort((a, b) => a - b)[0];
    const quick = [
        TL.video_end && TL.video_end > clip.start + TL.fps * 2
            ? el("button.btn.small", {type: "button", text: "To the picture's end", onclick: () => setLen(TL.video_end - clip.start)}) : null,
        next ? el("button.btn.small", {type: "button", text: "To the next marker", onclick: () => setLen(next - clip.start)}) : null,
    ].filter(Boolean);
    I.fitFind = el("button.btn", {type: "button", onclick: () => {
        fitStop();
        FIT.plans = null; FIT.error = ""; FIT.asked = FIT.frames;
        send("fit_find", {id: clip.id, frames: FIT.frames});
        syncFit(I, clip);
    }});
    I.fitText = el("p.muted.small.hint");
    I.fitList = el("div.fit-list");
    I.fitBar = el("div.fx-bar", {}, el("i"));
    I.fitPlay = el("button.btn", {type: "button", onclick: () => {
        if (FITPLAY && FITPLAY.id === clip.id) return fitStop();
        send("fit_preview", {id: clip.id, plan: FIT.pick});
    }});
    I.fitApply = el("button.btn.accent", {type: "button", text: "Apply", onclick: () => {
        fitStop();
        send("fit_apply", {id: clip.id, plan: FIT.pick});
    }});
    return el("section.group", {}, [
        el("h3.group-title", {text: "Fit to length"}),
        el("p.muted.small.hint", {text: "Makes this music end on its own ending at the length you want – whole bars cut out, or played again, where the music sounds the same either side, so the edit isn't heard."}),
        el("div.ctl", {}, [el("span.ctl-label", {text: "Length"}), el("div.ctl-body", {}, [I.fitLen, I.fitFind])]),
        quick.length ? el("div.ctl", {}, [el("span"), el("div.ctl-body", {}, quick)]) : null,
        I.fitBar, I.fitText, I.fitList,
        el("div.ctl-body.fx-buttons", {}, [I.fitPlay, el("span.spacer"), I.fitApply]),
    ]);
}

function syncFit(I, clip) {
    if (!I.fitFind) return;
    const busy = FXBUSY.id === clip.id && ["fit", "fit_preview", "fit_apply", "place"].includes(FXBUSY.doing) ? FXBUSY.doing : null;
    const elsewhere = FXBUSY.doing && FXBUSY.doing !== "preview" && !busy;
    I.fitFind.disabled = !!busy || !!elsewhere;
    I.fitFind.textContent = Buddy.t(busy === "fit" ? "Listening…" : FIT.plans && FIT.asked === FIT.frames ? "Find again" : "Find edits");
    I.fitBar.hidden = busy !== "fit" && busy !== "fit_apply";
    I.fitBar.firstChild.style.width = `${Math.round((FXBUSY.progress || 0) * 100)}%`;
    const plans = FIT.plans && FIT.asked === FIT.frames ? FIT.plans : null;
    let text = "";
    if (busy === "fit_apply") text = "Making the fitted music…";
    else if (busy === "place") text = "Putting the fitted music in Resolve…";
    else if (FIT.error) text = FIT.error;
    else if (plans) text = FIT.bpm ? `${FIT.bpm} BPM. Pick an edit and hear it first.` : "Pick an edit and hear it first.";
    else if (!busy) text = `The music is ${timecode(clip.end - clip.start, false)} long now. Set the length, then Find edits.`;
    I.fitText.textContent = Buddy.t(text);
    I.fitText.classList.toggle("mix-error", !!FIT.error);
    I.fitList.replaceChildren(...(plans || []).map((p, i) => {
        const pick = el("input", {type: "radio", name: "fit-plan", checked: i === FIT.pick});
        pick.addEventListener("change", () => { FIT.pick = i; fitStop(); syncFit(I, clip); });
        return el("label.fit-plan", {}, [pick, el("span.fit-what", {}, [
            el("b", {text: fitWhat(p)}),
            el(`span.fit-sound.${p.sound}`, {text: FIT_SOUND[p.sound]}),
            ...fitNotes(p).map(n => el("span.muted.small", {text: n}))])]);
    }));
    const playing = FITPLAY && FITPLAY.id === clip.id;
    I.fitPlay.replaceChildren(icon(playing ? "stop" : "play"), el("span", {text: playing ? "Stop" : busy === "fit_preview" ? "Getting it ready…" : "Hear the cut"}));
    I.fitPlay.hidden = I.fitApply.hidden = !plans;
    I.fitPlay.disabled = I.fitApply.disabled = !!busy || !!elsewhere;
}

function fitStop() {
    if (FITPLAY) { AUDIO.pause(); FITPLAY = null; }
}

Buddy.on("fit_plans", p => {
    if (p.id !== FIT.id) return;
    FIT.error = p.error || "";
    FIT.plans = p.error ? null : p.plans;
    FIT.bpm = p.bpm || null;
    FIT.pick = 0;
    syncInspector();
});

Buddy.on("fit_preview", p => {
    if (p.error) { FITPLAY = null; syncInspector(); return Buddy.toast(p.error, 4000); }
    if (p.id !== FIT.id || p.plan !== FIT.pick) return;
    if (FXPLAY) FXPLAY = null;
    FITPLAY = {id: p.id, plan: p.plan};
    AUDIO.src = p.url;
    AUDIO.currentTime = 0;
    AUDIO.play().catch(() => {});
    syncInspector();
    Buddy.toast(Buddy.t(`The cut comes ${Math.round(p.cut)} s in`), 3000);
});

// -------------------------------------------------------------- effects --
//
// The Effects group: Buddy's own processing (page.py's fx_*). The chain set
// for a clip is kept here per clip (FXSET) while Buddy runs. Preview plays a
// stretch of the clip before / after in an <audio> here - nothing goes to
// Resolve until Apply, which bakes a WAV and puts it in the clip's place.

const FXSET = {};                     // clip id -> [{id, on, strength}], in OPTIONS.effects order
let FXBUSY = {id: null, doing: null, progress: 0};
// The preview loaded: {id, before, after, frame, seconds, hum, which: "before" | "after"} - or null.
let FXPLAY = null, FXQUEUED = null;
const AUDIO = new Audio();
AUDIO.preload = "auto";

function fxChainFor(clip) {
    if (!FXSET[clip.id]) {
        const had = Object.fromEntries(((clip.fx && clip.fx.chain) || []).map(e => [e.id, e]));
        FXSET[clip.id] = (OPTIONS.effects || []).map(e => ({
            id: e.id, on: !!(had[e.id] && had[e.id].on), strength: had[e.id] ? had[e.id].strength : e.default}));
    }
    return FXSET[clip.id];
}

const fxActive = chain => chain.filter(e => e.on).map(e => `${e.id}:${Math.round(e.strength * 1000)}`).join(",");
const fxChanged = clip => fxActive(fxChainFor(clip)) !== fxActive((clip.fx && clip.fx.chain) || []);
const fxName = id => ((OPTIONS.effects || []).find(e => e.id === id) || {}).name || id;

/* Why Effects can't work on this clip, or "". */
function fxBlocked(clip) {
    if (!clip.media) return "This clip has no audio file Buddy can process.";
    if (clip.curve) return "Take the curve off first – effects go on the clip itself.";
    if (keyedInResolve(clip)) return "This clip has volume keyframes from Resolve, which processing would lose.";
    return "";
}

function effectsGroup(I, clip) {
    const chain = fxChainFor(clip);
    I.fxRows = (OPTIONS.effects || []).map((e, i) => {
        const box = el("input", {type: "checkbox"});
        const range = slider(0, 100, 1);
        const pct = el("span.unit.fx-pct");
        box.addEventListener("change", () => { chain[i].on = box.checked; fxEdited(clip); });
        range.addEventListener("input", () => { pct.textContent = `${range.value}%`; });
        range.addEventListener("change", () => {
            chain[i].strength = Number(range.value) / 100;
            chain[i].on = true;                       // moving it means wanting it
            fxEdited(clip);
        });
        return {box, range, pct, row: el("div.ctl.fx-row", {title: e.about}, [
            el("label.check.fx-name", {}, [box, el("span", {text: e.name})]),
            el("div.ctl-body", {}, [range, pct])])};
    });
    I.fxText = el("p.muted.small.hint");
    I.fxBar = el("div.fx-bar", {}, el("i"));
    I.fxPlay = el("button.btn", {type: "button", onclick: () => fxPlayOrStop(clip)});
    I.fxAB = el("div.segmented", {role: "group", "aria-label": "Listen to"}, [["before", "Before"], ["after", "After"]].map(([which, label]) => el("button", {
        type: "button", text: label, dataset: {which}, onclick: () => fxListen(which)})));
    I.fxApply = el("button.btn.accent", {type: "button", onclick: () => fxApply(clip)});
    I.fxRemove = el("button.btn.ghost", {type: "button", text: "Take effects off", onclick: async () => {
        const yes = await Buddy.confirm({title: "Take the effects off?", ok: "Take them off",
            text: "The processed clip goes, and the clip it stood in for is turned back on. Undo puts the effects back."});
        if (yes) { fxStop(); send("fx_remove", {id: clip.id}); }
    }});
    I.fxButtons = el("div.ctl-body.fx-buttons", {}, [I.fxPlay, I.fxAB, el("span.spacer"), I.fxRemove, I.fxApply]);
    return el("section.group.fx", {}, [el("h3.group-title", {text: "Effects"}), ...I.fxRows.map(r => r.row),
                                       I.fxText, I.fxBar, I.fxButtons]);
}

function syncEffects(I, clip) {
    if (!I.fxRows) return;
    const chain = fxChainFor(clip), blocked = fxBlocked(clip);
    const busy = FXBUSY.id === clip.id ? FXBUSY.doing : null;
    const elsewhere = FXBUSY.doing && FXBUSY.doing !== "preview" && FXBUSY.id !== clip.id;
    I.fxRows.forEach((r, i) => {
        r.box.checked = chain[i].on;
        if (document.activeElement !== r.range) r.range.value = Math.round(chain[i].strength * 100);
        r.pct.textContent = `${r.range.value}%`;
        r.box.disabled = r.range.disabled = !!blocked || busy === "apply" || busy === "place";
        r.row.classList.toggle("off", !chain[i].on);
    });
    const playing = FXPLAY && FXPLAY.id === clip.id;
    const any = chain.some(e => e.on);
    let text;
    if (blocked) text = [blocked];
    else if (busy === "preview") text = ["Getting the preview ready…"];
    else if (busy === "apply") text = ["Processing the clip's audio…"];
    else if (busy === "place") text = ["Putting the processed audio in Resolve…"];
    else if (elsewhere) text = ["Buddy is processing another clip."];
    else if (playing) {
        const hum = chain.find(e => e.id === "hum");
        text = [FXPLAY.which === "before" ? "Playing the clip as it is." : "Playing it with the effects.",
                hum && hum.on ? (FXPLAY.hum ? `Hum found at ${FXPLAY.hum} Hz.` : "No hum found – the Hum remover leaves it alone.") : null];
    } else if (clip.fx) {
        const under = CLIPS[clip.fx.original];
        text = ["Processed by Buddy – a new WAV beside the media.",
                under ? `The clip it stands in for is turned off on A${trackOf(under)}.` : "The clip it stood in for isn't on the timeline any more."];
    } else {
        text = ["Preview to hear it here first.",
                "Apply saves a new WAV beside the media and puts it in the clip's place – the clip stays, turned off."];
    }
    I.fxText.replaceChildren(...text.filter(Boolean).flatMap((s, i) => i ? [" ", el("span", {text: s})] : [el("span", {text: s})]));
    I.fxBar.hidden = !busy;
    I.fxBar.firstChild.style.width = `${Math.round((FXBUSY.progress || 0) * 100)}%`;

    I.fxPlay.replaceChildren(icon(playing ? "stop" : "play"), el("span", {text: playing ? "Stop" : "Preview"}));
    I.fxPlay.disabled = !!blocked || (!any && !playing) || busy === "apply" || busy === "place";
    I.fxAB.hidden = !playing;
    for (const b of I.fxAB.children) b.setAttribute("aria-pressed", String(!!playing && b.dataset.which === FXPLAY.which));
    const changed = fxChanged(clip);
    I.fxApply.textContent = Buddy.t(clip.fx ? (any ? "Apply changes" : "Take effects off") : "Apply");
    I.fxApply.disabled = !!blocked || !!busy || elsewhere || !changed || (!clip.fx && !any);
    I.fxRemove.hidden = !clip.fx || changed;
}

/* A setting changed: shown at once, and heard again if this clip's preview is playing. */
function fxEdited(clip) {
    syncInspector();
    if (FXPLAY && FXPLAY.id === clip.id) {
        clearTimeout(FXQUEUED);
        FXQUEUED = setTimeout(() => send("fx_preview", {id: clip.id, chain: fxChainFor(clip)}), 350);
    }
}

function fxPlayOrStop(clip) {
    if (FXPLAY && FXPLAY.id === clip.id) return fxStop();
    send("fx_preview", {id: clip.id, chain: fxChainFor(clip)});
}

function fxStop() {
    clearTimeout(FXQUEUED);
    AUDIO.pause();
    if (FXBUSY.doing === "preview") send("fx_stop");
    FXPLAY = null;
    syncInspector();
}

/* Before or after, from the same moment. */
function fxListen(which) {
    if (!FXPLAY || FXPLAY.which === which) return;
    const at = AUDIO.currentTime;
    FXPLAY.which = which;
    AUDIO.src = FXPLAY[which];
    AUDIO.currentTime = at;
    AUDIO.play().catch(() => {});
    syncInspector();
}

function fxApply(clip) {
    fxStop();
    const chain = fxChainFor(clip);
    if (clip.fx && !chain.some(e => e.on)) return INSP && INSP.fxRemove && INSP.fxRemove.click();
    send("fx_apply", {id: clip.id, chain});
}

AUDIO.addEventListener("ended", () => { FXPLAY = null; FITPLAY = null; syncInspector(); });

Buddy.on("fx_busy", b => {
    FXBUSY = b;
    syncInspector();
    if (MIX.open) drawMix();
});

Buddy.on("fx_preview", p => {
    if (p.error) {
        FXPLAY = null;
        syncInspector();
        return Buddy.toast(p.error, 4000);
    }
    // A new preview of the same clip and stretch (a setting changed) carries on where it was.
    const same = FXPLAY && FXPLAY.id === p.id && FXPLAY.frame === p.frame;
    const at = same ? AUDIO.currentTime : 0;
    const which = same ? FXPLAY.which : "after";
    FXPLAY = Object.assign({}, p, {which});
    FITPLAY = null;
    AUDIO.src = FXPLAY[which];
    AUDIO.currentTime = at;
    AUDIO.play().catch(() => {});
    syncInspector();
});

/* Apply or Remove done: {id, new} - the new clip keeps what was set for the old. */
Buddy.on("fx_done", d => {
    if (d.new && d.new !== d.id && FXSET[d.id]) FXSET[d.new] = FXSET[d.id].map(e => Object.assign({}, e));
    if (d.new && d.new !== d.id) delete FXSET[d.id];
    syncInspector();
});

// ---------------------------------------------------------------- state --

function drawStatus() {
    $("tl-status").textContent = LOADING > 0 ? `Reading waveforms… ${LOADING} left` : "";
}

let STATE = null;
function drawHeader() {
    const s = STATE;
    if (!s) return;
    const t = $("timeline");
    t.classList.toggle("bad", !s.timeline);
    const clips = TL ? TL.tracks.reduce((n, tr) => n + tr.clips.filter(c => !c.transition).length, 0) : 0;
    t.replaceChildren(s.timeline
        ? el("span", {}, [el("b", {text: s.timeline, translate: "no"}), TL ? " · " : "",
                          TL ? el("span", {text: plural(TL.tracks.length, "audio track")}) : "", TL ? " · " : "",
                          TL ? el("span", {text: plural(clips, "clip")}) : ""])
        : el("span", {text: s.problem || "No timeline open"}));
    // Resolve holds Buddy's calls while the timeline plays: the last read stays shown.
    if (s.busy) t.append(el("span.busy", {text: "Waiting for Resolve – is the timeline playing?"}));
    t.hidden = !s.connected;   // offline is said once, in Buddy's header
    $("no-timeline-why").textContent = s.problem && s.connected ? s.problem : "Its audio tracks show here, with each clip's waveform.";
}

Buddy.on("options", o => { OPTIONS = o; renderInspector(); });

Buddy.on("state", s => {
    STATE = s;
    drawHeader();
});

Buddy.on("timeline", data => {
    const first = !TL || !data || TL.id !== data.id;
    TL = data;
    CLIPS = {};
    for (const track of (TL ? TL.tracks : [])) for (const clip of track.clips) CLIPS[clip.id] = clip;
    $("no-timeline").hidden = !!TL;
    $("tl-card").hidden = !TL;
    if (DRAFT && (DRAFT.applied ? CLIPS[DRAFT.applied] : !CLIPS[DRAFT.id])) { DRAFT = null; KEYSEL = null; }
    drawDraftBar();
    if (!TL) { $("inspector").hidden = true; INSP = null; drawMix(); drawAutomix(); return; }
    drawHeads();
    $("tl").style.height = `${RULER_H + TL.tracks.length * LANE_H}px`;
    if (first) { fit = true; ppf = 0; }
    requestAnimationFrame(() => setZoom(fit ? 0 : ppf, 0));
    // The same clips still selected: keep the controls (and whatever is being typed).
    if (!INSP || INSP.ids.some(id => !CLIPS[id])) renderInspector(); else syncInspector();
    drawHeader();
    if (AUTO.open) askAutomix();
});

/* {clips: {id: levels}, wrote}: from a write (wrote) always taken; from a
   live read, not for a clip changed here in the last HOLD_MS. */
Buddy.on("levels", msg => {
    const now = performance.now();
    for (const [id, next] of Object.entries(msg.clips)) {
        if (!CLIPS[id] || (!msg.wrote && (HOLD[id] > now || (DRAG && DRAG.ids.includes(id))))) continue;
        Object.assign(CLIPS[id], next);
    }
    requestDraw();
    syncInspector();
});

Buddy.on("playhead", p => {
    if (scrubbing) return;       // the drag's own position wins until it's let go
    HEAD = p.frame;
    placeHead();
});

Buddy.on("selection", s => {
    const same = s.ids.length === SEL.size && s.ids.every(id => SEL.has(id));
    SEL = new Set(s.ids);
    FROM_RESOLVE = s.from_resolve;
    requestDraw();
    if (same && INSP) syncInspector(); else renderInspector();
});

Buddy.on("peaks", p => {
    PEAKS[p.key] = decodePeaks(p);
    LOADING = p.left;
    drawStatus();
    requestDraw();
    syncInspector();
    if (AUTO.open && !AUTO.plan) askAutomix();
});

Buddy.on("peaks_progress", p => {
    LOADING = p.left;
    drawStatus();
});

Buddy.on("undo", u => {
    UNDO = u;
    const b = $("undo");
    b.hidden = !u;
    if (u) {
        b.lastChild.textContent = `Undo: ${u.label}`;
        b.title = u.count > 1 ? `${u.count} changes can be undone` : "";
    }
});

/* An Apply (or Remove) is done: {id, done, new}. The draft stays drawn until the
   new clip is read, so the line doesn't jump back meanwhile. */
Buddy.on("curve", c => {
    if (DRAFT && DRAFT.id === c.id) {
        if (c.done && c.new && c.new !== c.id) { DRAFT.applying = false; DRAFT.applied = c.new; }
        else if (c.done) { DRAFT = null; KEYSEL = null; }
        else DRAFT.applying = false;
    }
    draftChanged();
    renderInspector();
});

Buddy.on("toast", t => Buddy.toast(t.text, 3500));
Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}), buttons: [{label: "OK", kind: "accent"}]}));

Buddy.on("theme", () => requestDraw());
Buddy.on("i18n", () => { requestDraw(); syncInspector(true); });
