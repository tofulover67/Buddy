/*
 * Generative's view: the picture, with the tools on it, and a short panel
 * beside it saying what's next (the layout picked from mock-ups,
 * 2026-10-08). page.py owns the picture (in the engine's process) and sends
 * previews; this draws them, lets the person paint, and sends what they
 * painted back.
 *
 * First a task is picked - nothing opens or drops before it (state.tab):
 *
 *   remove     Paint (or Erase, red) over what should go, then Remove.
 *   cutout     The subject is found as the picture opens, shown cut out;
 *              Keep (green) / Take away (red) fix its edge - each stroke
 *              sent as it ends. Picked the wrong thing: draw round it (blue),
 *              then find it only in there, or use the drawing as it is.
 *   separate   the same subject, then Make the clean background: two layers.
 *
 * The brush means one thing in each (`using`: "remove", "fix", or
 * "outline" while drawing round the subject). One Export button holds every
 * way out - Save, Save as, Replace in Resolve, the Media Pool.
 *
 * Footage (doc.kind "video"): the frame shown, a scrubber under it, and the
 * same paint - kept per frame (keyframes, `keyed`): what's painted on the
 * frame shown is `strokes` / `outline` as ever, put away when another frame
 * is shown. Its panel is a list of steps, the next one open: paint it,
 * follow it through the shot (doc.tracked, a mask picture a frame), run the
 * job (every keyframe's paint sent), export - and the job's frames can be
 * looked through after.
 *
 * Strokes are kept in the PICTURE's pixels - {size, feather, erase | keep,
 * points: [x0, y0, x1, y1 ...]} - whatever the zoom, so the engine draws at
 * full size what's seen here. A shape is a stroke too, {shape, feather,
 * erase | keep, points: [x0, y0, x1, y1]} - its box's corners: a rectangle
 * dragged corner to corner, an ellipse out from its centre - or a lasso,
 * {shape: "lasso", points: its outline}, closed back to where it began. The previews may be smaller than the picture
 * (doc.scale): picture = preview / scale.
 *
 * With Rectangle or Ellipse picked, a shape already drawn is selected by
 * pressing inside it: dragged to move, its handles to resize (Shift keeps
 * its proportions), arrow keys to nudge, Delete to take it away; Soft edge
 * (`feather`) is then its own. Undo / redo go back through the paint as it
 * was before each change - moves and resizes too - and then (Remove)
 * through the engine's own history.
 *
 * While the engine works, a card over the dimmed picture says what it's
 * doing (state.busy: stage, detail, progress) and for how long.
 *
 * Mouse: paint with the left button (Alt: the other brush of the pair),
 * drag with the middle or right button or Space held to move, wheel to
 * zoom, Alt + wheel for the brush's size, Shift to drag a square or
 * circle. Keys (the ? on the tools lists them): B / R / O / L brush,
 * rectangle, ellipse, lasso, E erase, A / S keep and take away, [ ] size,
 * Enter remove, Esc let go of the shape (again: clear the paint), Delete
 * the shape, arrows nudge it (Shift: 10 px), Ctrl+Z undo, Ctrl+Shift+Z /
 * Ctrl+Y redo, F fit, 1 actual pixels, \ held: the original.
 */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);

const RED = "rgb(255, 64, 96)";
const GREEN = "rgb(64, 220, 120)";
const BLUE = "rgb(80, 160, 255)";       // drawn round the subject
const PAINT_ALPHA = 0.5;
const MIN_ZOOM = 0.02, MAX_ZOOM = 32;
const BUSY_CARD_AFTER_MS = 250;     // a quick step (undo) doesn't flash a card

let state = null;
let doc = null;
const pics = {};                    // kind -> {url, img}: preview, original, subject, matte, background
let strokes = [];                   // paint to remove, in picture pixels
let fixes = [];                     // Add / Subtract strokes on the cut-out
let outline = [];                   // Separate: drawn round the subject, to find it in or use as it is
let outlining = false;              // Separate, a subject found: drawing round it again
// Each list as it was before each change: undo goes back, redo forward.
const history = {remove: {back: [], ahead: [], key: ""}, fix: {back: [], ahead: [], key: ""},
    outline: {back: [], ahead: [], key: ""}};
let selected = -1;                  // the shape being moved / resized: its place in the list in use
let hovered = null;                 // the masks list's row under the pointer: the strokes it stands for
let pending = null;                 // an Add / Subtract stroke being painted
let removeMode = "brush";           // brush / erase
let shape = "brush";                // what a drag draws: brush / rect / ellipse / lasso
let fixMode = "keep";               // keep / drop
let using = "remove";               // which list the paint goes in: "remove", "fix" or "outline"
let cutView = "cutout";             // cutout / matte / overlay
let sepView = "both";               // both / subject / background
let brush = 0;
let feather = 0;                    // 0-100: how much of the brush's radius fades
let view = {s: 1, x: 0, y: 0, fitted: true};
let pointer = null;
let drag = null;
let space = false, alt = false, altSized = false, comparing = false;
// Footage: the frame shown (or asked for), and the paint on each frame.
let vframe = 0;
let keyed = {remove: {}, outline: {}};   // which list -> frame -> strokes
let askedAt = 0;                    // when the view last asked for a frame: Python's own moves are followed after
const trackPics = {};               // url -> Image: a frame's tracked mask

// Two canvases, one over the other: the picture, drawn again only when what
// it shows changes (the zoom, where it is, a new preview) - and over it the
// marks (paint, outlines, handles, the brush), drawn again as the mouse
// moves. Redrawing the scaled picture with every move made it flicker on
// some GPUs (AMD's, through Direct3D 11 on 12) at the zooms it's smoothed at.
const canvas = $("canvas");
const ctx = canvas.getContext("2d");
const marks = $("marks");
const mctx = marks.getContext("2d");
const layer = document.createElement("canvas");     // paint, and the overlay's tint
const lctx = layer.getContext("2d");
const layer2 = document.createElement("canvas");    // the cut-out's compositing
const l2ctx = layer2.getContext("2d");
const layer3 = document.createElement("canvas");    // Margin's band round the paint, kept while nothing it shows changes
const l3ctx = layer3.getContext("2d");
let marginKey = "";
let marginDragged = null;           // Margin's slider as it's dragged, before Python has it

// ------------------------------------------------------------ the state --

Buddy.on("toast", t => Buddy.toast(t.text, 4500));
Buddy.on("drop_hover", on => {
    if (on === "refused") flashPick();                  // no task picked yet
    else $("stage").classList.toggle("drop", !!on);
});

Buddy.on("state", s => {
    const was = doc;
    const tabWas = state && state.tab;
    state = s;
    marginDragged = null;
    doc = s.doc;
    if (!doc || !was || doc.path !== was.path || doc.original !== was.original) {
        strokes = []; fixes = []; outline = []; outlining = false;
        forget("remove"); forget("fix"); forget("outline"); brush = 0; view.fitted = true;
        for (const k of Object.keys(pics)) delete pics[k];
        keyed = {remove: {}, outline: {}};
        vframe = doc && doc.kind === "video" ? doc.frame : 0;
    }
    // Footage moved to another frame by Python (opened at the playhead, a
    // job's newest frame) - not one the view's just asked for: follow it.
    if (isVideo() && doc.frame !== vframe && Date.now() - askedAt > 1500) adoptFrame(doc.frame);
    if (doc && was && was.matte && !doc.matte) { fixes = []; forget("fix"); }
    // A subject found (or cut out by hand): done drawing round it.
    if (doc && doc.matte && (!was || !was.matte || was.matte.matte !== doc.matte.matte) && !doc.matte.strokes) outlining = false;
    if (s.tab !== tabWas) { outlining = false; stepPicked = 0; }
    // The brush means one thing in each task: what to remove; drawn round
    // the subject until it's found (and on footage); fixing the cut-out after.
    const usingWas = using;
    if (!doc || !s.tab || s.tab === "remove") using = "remove";
    else if (doc.kind === "video") using = "outline";
    else using = !doc.matte || outlining ? "outline" : "fix";
    if (using !== usingWas) selected = -1;
    if (doc) {
        want("preview", doc.preview, () => {
            if (was && was.preview !== doc.preview && !isVideo()) {      // the picture changed: its paint is used
                strokes = []; forget("remove");
                if (using === "remove") selected = -1;
            }
        });
        want("original", doc.original);
        want("subject", doc.matte ? doc.matte.subject : "");
        want("matte", doc.matte ? doc.matte.matte : "");
        want("background", doc.background);
    }
    drawAll();
});

/* Loads a preview once its URL changes; the old one stays drawn until the new one's in. */
function want(kind, url, then) {
    if (!url) { delete pics[kind]; return; }
    if (pics[kind] && pics[kind].url === url) return;
    const i = new Image();
    i.onload = () => {
        const first = kind === "preview" && (!pics.preview || view.fitted);
        pics[kind] = {url, img: i};
        if (kind === "preview") {
            if (!brush) brush = defaultBrush();
            if (first) fit();
        }
        if (then) then();
        drawAll();
    };
    i.onerror = () => Buddy.toast("Couldn't show the picture.");
    i.src = url;
}

// Kept while there's paint or unsaved work: the page holds the paint alone.
Buddy.holdWhile(() => strokes.length > 0 || Object.keys(keyed.remove).length > 0 || !!(doc && doc.dirty));

// ------------------------------------------------------------ the parts --

const busy = () => !!(state && state.busy);
const tab = () => state ? state.tab : null;
const missing = t => state && (t || tab()) ? state.engine.missing[t || tab()] || [] : [];
const engineOk = () => state && state.engine.env;
const ready = t => !!(engineOk() && (t || tab()) && !missing(t).length);
const matteReady = () => !!(doc && doc.matte && pics.matte);
const subjectTask = () => tab() === "cutout" || tab() === "separate";
const showingCut = () => matteReady() && subjectTask() && !outlining;

const isVideo = () => !!(doc && doc.kind === "video");

function drawAll() {
    if (!state) return;
    drawHead(); drawPanel(); drawCenter(); drawViews(); drawZoom(); drawBusy(); drawScrub();
    draw();
}

function drawHead() {
    const f = $("file");
    f.hidden = !doc;
    if (doc) {
        const more = isVideo() ? [` · ${+doc.fps.toFixed(3)} fps · ${doc.frames} frames`] : [];
        f.replaceChildren(el("b", {text: doc.name, translate: "no"}), ` · ${doc.width} × ${doc.height} · `,
            el("span", {text: doc.depth}), ...more);
        f.title = doc.path;
    }
    const off = !engineOk() || busy() || !!(doc && doc.running);
    $("close").hidden = !doc;
    $("close").replaceChildren(icon("close"));
    $("close").title = "Close it – the original is never changed";
    $("close").disabled = off;
    // Opening another: only once something's open (before that the middle of the page has them).
    $("from-resolve").hidden = $("open").hidden = !doc;
    $("from-resolve").replaceChildren(icon("film"), el("span", {text: "From Resolve"}));
    $("from-resolve").title = "The clip under the playhead – or the one selected in the Media Pool";
    $("open").replaceChildren(icon("folder"), el("span", {text: "Open"}));
    $("from-resolve").disabled = $("open").disabled = off;
}

// ---------------------------------------------------------------- tasks --
//
// What the page is for, picked first (the user's call, 2026-10-08: nothing
// opens or drops before it). Python keeps it (state.tab); the view only asks.

const TASKS = {
    remove: {name: "Remove an object", icon: "eraser", color: RED,
        about: "Paint over a sign, a wire or a passer-by and it's filled with what would be behind it."},
    cutout: {name: "Cut out the subject", icon: "subject", color: GREEN,
        about: "Keep the person, animal or product and lose the background – found by itself, hair and all."},
    separate: {name: "Split into layers", icon: "layers", color: BLUE,
        about: "The subject on its own and a clean background without it – two layers to stack in Resolve."},
};
const TASK_ORDER = ["remove", "cutout", "separate"];
// What each task's footage job makes.
const JOB_KINDS = {remove: ["clean"], cutout: ["subject"], separate: ["subject", "background"]};
let prevTab = null;                 // the task before the last change: Back from a download goes to it

function taskIcon(id) {
    const i = genIcon(TASKS[id].icon);
    i.style.color = TASKS[id].color;
    return i;
}

function pickTask(id) {
    if (busy() || (doc && doc.running) || id === tab()) return;
    prevTab = tab();
    send("tab", {id});
}

/* A menu under (or, near the bottom, over) a button - Buddy.menu, lined up with it. */
function menuAt(anchor, items) {
    const r = anchor.getBoundingClientRect();
    Buddy.menu({x: r.left, y: r.bottom + 4, items});
    const pop = document.querySelector(".menu-pop");
    if (!pop) return;
    pop.style.minWidth = `${r.width}px`;
    if (pop.getBoundingClientRect().top < r.bottom) pop.style.top = `${Math.max(6, r.top - pop.offsetHeight - 4)}px`;
}

// ------------------------------------------------------------ the bits --

function segmented(label, options, value, pick, disabled) {
    return el("div.segmented", {role: "group", "aria-label": label}, options.map(([id, text, tip, ic]) =>
        el("button", {type: "button", title: tip || "", "aria-pressed": String(id === value), disabled,
            onclick: () => pick(id)}, [ic ? icon(ic) : "", el("span", {text})])));
}

function section(title, children, extra) {
    return el(`div.gen-sec${extra || ""}`, {}, [title ? el("div.gen-sec-title", {text: title}) : "", ...children]);
}

const hint = text => el("div.gen-hint", {text});

/* A button: an icon (Buddy's by name, or a node) and words. */
function action(text, ic, onclick, {accent, disabled, title, wide = true} = {}) {
    return el(`button.btn${accent ? ".accent" : ""}${wide ? ".wide" : ""}`, {type: "button", disabled, title: title || "", onclick},
        [ic ? (typeof ic === "string" ? icon(ic) : ic) : "", el("span", {text})]);
}

function link(text, onclick, {disabled, title} = {}) {
    return el("button.btn.ghost.gen-link", {type: "button", text, disabled, title: title || "", onclick});
}

/* A setting as a row: its name, then its control. */
function field(label, control, tip) {
    return el("label.gen-field", {title: tip || ""}, [el("span", {text: label}), control]);
}

function choice(options, value, onchange, disabled, tip) {
    const sel = el("select.field", {disabled, title: tip || "", onchange: e => onchange(e.target.value)},
        options.map(([id, text, off]) => el("option", {value: id, text, disabled: !!off})));
    sel.value = value;
    return sel;
}

function slider(label, min, max, step, value, shown, tip, onInput, id, onDone) {
    const input = el("input", {type: "range", min: String(min), max: String(max), step: String(step), value: String(value)});
    const val = el("span.val", {text: shown, id});
    input.addEventListener("input", () => onInput(Number(input.value), val));
    if (onDone) input.addEventListener("change", () => onDone(Number(input.value)));
    return el("label.gen-slider", {title: tip}, [el("span", {text: label}), input, val]);
}

// Generative's own icons, drawn like Buddy's (24 x 24, stroked).
const ICONS = {
    brush: '<path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/>'
        + '<path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>',
    eraser: '<path d="M7 21l-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/>'
        + '<path d="M22 21H7"/><path d="M5 11l9 9"/>',
    eyeOff: '<path d="M10.7 5.1A10.7 10.7 0 0 1 21.9 11.7a1 1 0 0 1 0 .7 10.7 10.7 0 0 1-1.4 2.5"/>'
        + '<path d="M14.1 14.2a3 3 0 0 1-4.2-4.2"/><path d="M17.5 17.5A10.8 10.8 0 0 1 2.1 12.3a1 1 0 0 1 0-.7 10.8 10.8 0 0 1 4.4-5.1"/>'
        + '<path d="M2 2l20 20"/>',
    rect: '<rect x="4" y="6" width="16" height="12" rx="1"/>',
    keyPrev: '<path d="M14 6l-6 6 6 6"/><path d="M19 12l-2.5-2.5L14 12l2.5 2.5z"/>',
    keyNext: '<path d="M10 6l6 6-6 6"/><path d="M5 12l2.5-2.5L10 12l-2.5 2.5z"/>',
    ellipse: '<ellipse cx="12" cy="12" rx="8.5" ry="6"/>',
    lasso: '<path d="M7 17.5C4.2 16.2 2.5 14.2 2.5 11.8 2.5 7.5 6.8 4 12 4s9.5 3.5 9.5 7.8-4.3 7.7-9.5 7.7c-1.4 0-2.7-.2-3.9-.6"/>'
        + '<circle cx="6.5" cy="18.5" r="1.8"/><path d="M6 20.3c-.2 1-.9 1.7-2 1.7"/>',
    subject: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="12" cy="10" r="3.2"/><path d="M6.5 21a5.5 5.5 0 0 1 11 0"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 12.5l9 5 9-5"/><path d="M3 17l9 5 9-5"/>',
    compare: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18"/><path d="M12 7l4.5 0M12 11h6.5M12 15h6M12 19h3"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.3a2.6 2.6 0 0 1 5 1c0 1.8-2.5 2.2-2.5 3.9"/><path d="M12 17.3v.2"/>',
    route: '<circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8.2 18H15a3.5 3.5 0 0 0 0-7H9a3.5 3.5 0 0 1 0-7h6.8"/>',
};
function genIcon(id) {
    const span = document.createElement("span");
    span.innerHTML = `<svg class="icon-svg" viewBox="0 0 24 24" aria-hidden="true">${ICONS[id]}</svg>`;
    return span.firstChild;
}

// --------------------------------------------------- the tools on the picture --
//
// What a drag does, over the picture where the eye already is: what the
// paint means (one meaning per task - remove it, keep / take away from the
// cut-out, draw round the subject), the shape, its size and soft edge, and
// undo, redo, the original. The panel beside it only says what's next.

function pickShape(id) {
    shape = id;
    if (id === "brush") selected = -1;
    drawAll();
}
function pickRemove(m) {
    if (using !== "remove") selected = -1;
    removeMode = m; using = "remove";
    drawAll();
}
function pickFix(m) {
    if (using !== "fix") selected = -1;
    fixMode = m; using = "fix";
    drawAll();
}
function pickOutline(m) {
    removeMode = m;
    drawAll();
}

function tool(content, title, pressed, onclick, disabled, cls = "") {
    return el(`button.gen-tool${cls}`, {type: "button", title, disabled, onclick,
        "aria-pressed": pressed === undefined ? undefined : String(!!pressed)}, content);
}
const toolSep = () => el("span.gen-tool-sep");

function drawTools() {
    const box = $("tools");
    const on = !!(doc && pics.preview && tab() && ready());
    box.hidden = !on;
    if (!on) { box.replaceChildren(); return; }
    const b = busy();
    const paint = !b && canPaint();
    const parts = [];
    // What the paint means.
    const modes = using === "fix"
        ? [["keep", "Keep", "Paint over what belongs to the subject (A)", icon("plus"), fixMode === "keep", () => pickFix("keep"), ".keep"],
            ["drop", "Take away", Buddy.keys("Paint over what doesn't (S, or hold Alt)"), icon("minus"), fixMode === "drop", () => pickFix("drop"), ".drop"]]
        : using === "outline"
            ? [["brush", "Draw", "Draw round the subject, or paint over it (B)", genIcon("lasso"), removeMode === "brush", () => pickOutline("brush"), ""],
                ["erase", "Erase", Buddy.keys("Take some of the drawing away (E, or hold Alt)"), genIcon("eraser"), removeMode === "erase", () => pickOutline("erase"), ""]]
            : [["brush", "Paint", "Paint over what to remove (B)", genIcon("brush"), removeMode === "brush", () => pickRemove("brush"), ""],
                ["erase", "Erase", Buddy.keys("Take paint off (E, or hold Alt)"), genIcon("eraser"), removeMode === "erase", () => pickRemove("erase"), ""]];
    for (const [, text, tip, ic, pressed, fn, cls] of modes) parts.push(tool([ic, el("span", {text})], tip, pressed, fn, !paint, `.wide${cls}`));
    parts.push(toolSep());
    for (const [id, tip] of [["brush", "Brush – paint freehand (B)"],
        ["rect", "Rectangle – drag one, Shift for a square; press inside one to move it, on its handles to resize (R)"],
        ["ellipse", "Ellipse – drag out from its centre, Shift for a circle (O)"],
        ["lasso", "Lasso – draw round something; it closes itself (L)"]]) {
        parts.push(tool([genIcon(id)], tip, shape === id, () => pickShape(id), !paint));
    }
    parts.push(toolSep(), ...historyTools(b));
    parts.push(tool([genIcon("help")], "Keyboard and mouse (?)", undefined, showKeys, false));
    // Under them, how the brush or the selected shape draws.
    const sel = selectedShape();
    const opts = sel ? shapeControls(sel, b) : [
        shape === "brush" ? slider("Size", 0, 1000, 1, Math.round(toSlider(brush) * 1000), `${Math.round(brush)} px`,
            Buddy.keys("Brush size – [ and ], or Alt + scroll wheel"), v => setBrush(fromSlider(v / 1000)), "size-val") : "",
        slider("Soft edge", 0, 100, 5, feather, `${feather}%`,
            using === "remove" ? "How soft the edge is – a removal takes all of what's painted and blends out past it"
                : "How soft the edge is – it fades out across it",
            (v, val) => { feather = v; val.textContent = `${v}%`; draw(); }),
    ];
    box.replaceChildren(el("div.gen-tools-row", {}, parts), el("div.gen-tools-row.opts", {}, opts));
}

/* The selected shape: what and how big, its own soft edge, take it away / done. */
function shapeLabel(s) {
    const [x0, y0, x1, y1] = box(s);
    const what = {ellipse: "Ellipse", lasso: "Lasso"}[s.shape] || "Rectangle";
    const turned = s.angle ? ` · ${Math.round(s.angle)}°` : "";
    return `${what} · ${Math.round(x1 - x0)} × ${Math.round(y1 - y0)} px${turned}`;
}
function updateShapeLabel() {
    const s = selectedShape(), label = document.querySelector(".gen-selected-name");
    if (s && label) label.textContent = shapeLabel(s);
}

function shapeControls(s, b) {
    return [
        el("span.gen-selected-name", {translate: "no", text: shapeLabel(s),
            title: "Drag it to move, its handles to resize (Shift keeps its shape), arrow keys to nudge"}),
        slider("Soft edge", 0, 100, 5, s.feather || 0, `${s.feather || 0}%`,
            "How soft this shape's edge is – a share of half its shorter side",
            (v, val) => {
                remember(using, `feather ${selected}`);
                s.feather = feather = v;
                val.textContent = `${v}%`;
                draw();
            }, undefined, () => { if (using === "fix") sendFixes(); else drawPanel(); }),
        tool([icon("trash")], "Take this shape away (Delete)", undefined, deleteSelected, b),
        tool([icon("check")], "Done with this shape (Esc)", undefined, () => { selected = -1; drawAll(); }, b),
    ];
}

function historyTools(b) {
    const h = history[using];
    const undoable = h.back.length || (using === "remove" && doc.undo);
    const redoable = h.ahead.length || (using === "remove" && doc.redo);
    const redoBtn = tool([icon("undo")], Buddy.keys("Redo (Ctrl+Shift+Z)"), undefined, redo, b || !redoable);
    redoBtn.firstChild.style.transform = "scaleX(-1)";
    const out = [tool([icon("undo")], Buddy.keys("Undo (Ctrl+Z)"), undefined, undo, b || !undoable), redoBtn];
    if (!isVideo()) {
        const before = tool([genIcon("compare")], "Hold to see the original (or hold \\)", comparing, null, !pics.original, ".gen-compare");
        before.addEventListener("pointerdown", () => compare(true));
        for (const ev of ["pointerup", "pointerleave", "pointercancel"]) before.addEventListener(ev, () => compare(false));
        out.push(before);
    }
    return out;
}

function showKeys() {
    const row = (keys, what) => el("div.gen-key", {}, [el("span", {}, keys.flatMap((k, i) =>
        [i ? " " : "", el("kbd", {text: k})])), el("span", {text: what})]);
    Buddy.modal({title: "Keyboard and mouse", body: el("div.gen-keys", {}, [
        row(["Wheel"], "Zoom"), row(["Space", "Right drag"], "Move the picture"),
        row([Buddy.keys("Alt"), "Wheel"], "Brush size – or [ and ]"), row([Buddy.keys("Alt")], "Held: the other brush (Erase, Take away)"),
        row(["B", "R", "O", "L"], "Brush, rectangle, ellipse, lasso"), row(["E"], "Erase"), row(["A", "S"], "Keep, take away"),
        row(["Enter"], "Remove what's painted"), row([Buddy.keys("Ctrl+Z"), Buddy.keys("Ctrl+Y")], "Undo, redo"),
        row(["\\"], "Held: the original"), row(["F", "1"], "Fit, actual pixels"),
        row(["Delete", "Arrows"], "Take the shape away, nudge it"), row(["Esc"], "Let go of the shape – again: clear the paint"),
        row(["←", "→"], "Footage: a frame back, on (Shift: 10)"), row(["Shift+I", "Shift+O"], "Footage: start, end here"),
        row(["T"], "Footage: show or hide where it's followed"),
    ]), buttons: [{label: "Close", kind: "accent"}]});
}

// ------------------------------------------------- the switch under the picture --

const RESULT_NAMES = {source: "Original", clean: "Removed", subject: "Subject", background: "Background"};

function drawViews() {
    const box = $("views");
    let seg = null, note = "";
    if (doc && pics.preview && !isVideo() && showingCut() && !comparing) {
        seg = tab() === "cutout"
            ? segmented("Show", [["cutout", "Cut-out", "The subject on its own"],
                ["matte", "Black and white", "White is kept, black is taken away"],
                ["overlay", "Over the picture", "The picture, with what's taken away tinted"]], cutView,
            v => { cutView = v; drawAll(); }, false)
            : segmented("Show", [["both", "Both", "The subject over the clean background"],
                ["subject", "Subject", "The subject layer"], ["background", "Background", "The clean background layer"]],
            sepView, v => { sepView = v; drawAll(); }, false);
    } else if (isVideo() && doc.results.length) {
        seg = segmented("Show", [["source", "Original", "The clip as it is"],
            ...doc.results.map(k => [k, RESULT_NAMES[k], `What the job made: ${RESULT_NAMES[k].toLowerCase()}`])],
        doc.show, v => send("video_show", {show: v}), false);
        if (doc.shown === "missing") note = el("span.gen-views-note", {text: "Not made for this frame yet"});
    }
    box.hidden = !seg;
    if (seg) box.replaceChildren(seg, note);
}

// --------------------------------------------------------------- panel --

function drawPanel() {
    drawTools();
    const box = $("panel");
    box.hidden = !doc || !tab();
    if (box.hidden) { box.replaceChildren(); return; }
    const b = busy();
    const parts = [taskHead(b)];
    if (!ready()) parts.push(hint(TASKS[tab()].about));
    else if (isVideo()) footagePanel(parts, b);
    else ({remove: panelRemove, cutout: panelCutout, separate: panelSeparate})[tab()](parts, b);
    box.replaceChildren(...parts);
}

function taskHead(b) {
    const t = tab();
    return el("div.gen-task", {}, [taskIcon(t), el("span.gen-task-name", {text: TASKS[t].name}),
        link("Change", e => menuAt(e.currentTarget, TASK_ORDER.map(id => ({label: TASKS[id].name, checked: id === t,
            onClick: () => pickTask(id)}))), {disabled: b || !!doc.running, title: "Do something else with it"})]);
}

function panelRemove(parts, b) {
    const has = paintToRemove();
    parts.push(hint(has ? "Press Remove – or paint over more first."
        : doc.undo ? "Paint over anything else that should go – or export it."
            : "Paint over what should go, then press Remove."));
    parts.push(marksSection(b));
    parts.push(section("", [qualityGroup(b, false), marginSlider(b, "what's painted"),
        action("Remove", "spark", remove, {accent: has, disabled: b || !has,
            title: "Fill what's painted with what would be behind it (Enter)"})]));
    parts.push(el("div.gen-grow"), exportBlock(b, !has && !!doc.undo && (doc.dirty || !doc.saved)));
}

function panelCutout(parts, b) {
    if (!matteReady() || outlining) { panelOutline(parts, b); return; }
    parts.push(hint("Paint Keep where it missed some of the subject, Take away where it kept too much."));
    parts.push(marksSection(b));
    parts.push(section("", [modelPicker(b)]));
    parts.push(section("Picked the wrong thing?", [el("div.gen-row", {}, [
        action("Draw round it", genIcon("lasso"), drawRoundAgain, {disabled: b, wide: false,
            title: "Draw round the subject yourself – then find it in there, or use what you drew"}),
        el("button.btn.icon", {type: "button", disabled: b, title: "Find the subject again in the whole picture, without the fixes",
            onclick: () => send("find_subject")}, [icon("refresh")]),
    ])]));
    parts.push(el("div.gen-grow"), exportBlock(b, !(doc.layers || {}).subject));
}

function panelSeparate(parts, b) {
    const found = matteReady() && !outlining;
    parts.push(el("div.gen-checks", {}, [
        check(found, found ? "Subject found" : "Find the subject",
            found ? link("Draw round it", drawRoundAgain, {disabled: b, title: "Draw round the subject yourself – then find it in there"}) : ""),
        check(!!doc.background, doc.background ? "Clean background made" : "Make the clean background"),
    ]));
    if (!found) { panelOutline(parts, b); return; }
    parts.push(hint(doc.background ? "Look through Both, Subject and Background under the picture. Fixing the edge now means making the background again."
        : "Fix the subject's edge first if it needs it – Keep where it missed, Take away where it kept too much. Then make the background."));
    parts.push(marksSection(b));
    parts.push(section("", [qualityGroup(b, false), modelPicker(b), marginSlider(b, "the subject's edge"),
        action(doc.background ? "Make the background again" : "Make the clean background", "spark", () => send("make_background"),
            {accent: !doc.background, disabled: b, title: "Fill where the subject is with what would be behind it"})]));
    parts.push(el("div.gen-grow"), exportBlock(b, !!doc.background && !(doc.layers || {}).background));
}

function check(done, text, extra) {
    return el(`div.gen-check${done ? ".done" : ""}`, {}, [done ? icon("check") : el("span.gen-dot"),
        el("span.gen-check-text", {text}), extra || ""]);
}

/* Finding the subject: by itself - or, when it picks the wrong thing, inside what's drawn round it. */
function panelOutline(parts, b) {
    const drawn = outline.some(s => !s.erase && !s.hidden);
    parts.push(hint(drawn ? "Now find the subject inside what you drew – hair and all – or use what you drew as it is."
        : outlining ? "Draw round the subject – Buddy then looks only in there."
            : "Buddy finds the subject by itself. If it picks the wrong thing, draw round the subject first."));
    parts.push(marksSection(b));
    parts.push(section("", [
        action(drawn ? "Find the subject in here" : "Find the subject", "search",
            () => send("find_subject", drawn ? {region: shownOf(outline)} : {}), {accent: true, disabled: b,
                title: drawn ? "Buddy finds the subject only inside what you drew – it traces the fine edges itself"
                    : "Buddy looks at the whole picture and finds what it's of"}),
        drawn ? action("Use what I drew", genIcon("lasso"), () => send("use_outline", {region: shownOf(outline)}),
            {disabled: b, title: "What you drew is the cut-out, as it is – its edge as soft as its Soft edge"}) : "",
        drawn ? link("Find it in the whole picture", () => send("find_subject"), {disabled: b}) : "",
        modelPicker(b),
        outlining && matteReady() ? link("Keep the subject as it was", () => {
            outlining = false; using = "fix"; selected = -1; drawAll();
        }, {disabled: b}) : "",
    ]));
    parts.push(el("div.gen-grow"));
}

function drawRoundAgain() {
    if (busy()) return;
    outlining = true;
    using = "outline";
    selected = -1;
    if (shape === "brush") shape = "lasso";
    drawAll();
}

// ----------------------------------------------------------------- export --

const EXPORT_LABELS = {remove: "Export", cutout: "Export cut-out", separate: "Export layers"};

function exportItems() {
    const t = tab(), src = doc.source, conn = state.connected;
    const folder = {label: "Show in folder", onClick: () => send("reveal")};
    if (t === "separate") {
        return [{label: "Save both layers", onClick: () => send("save_layers")},
            {label: "Add both to the Media Pool", onClick: () => send("add_layers"), disabled: !conn},
            {sep: true}, folder];
    }
    const swap = src ? [{label: "Replace in Resolve", onClick: () => send("swap"), disabled: !conn}] : [];
    if (t === "cutout") {
        return [{label: "Save the cut-out", onClick: () => send("save")}, {label: "Save as…", onClick: () => send("save_as")},
            ...swap, {label: "Add to the Media Pool", onClick: () => send("add_to_pool"), disabled: !conn}, {sep: true}, folder];
    }
    return [{label: doc.saved ? "Save again" : "Save a copy", onClick: () => send("save"), disabled: !!doc.saved && !doc.dirty},
        {label: "Save as…", onClick: () => send("save_as")},
        ...swap, {label: "Add to the Media Pool", onClick: () => send("add_to_pool"), disabled: !conn}, {sep: true}, folder];
}

/* What's saved, then one button for every way out - Save, Save as, Replace in Resolve, the Media Pool. */
function exportBlock(b, accent) {
    const t = tab();
    const layers = doc.layers || {};
    const made = t === "separate" ? !!doc.background : t === "cutout" ? !!doc.matte : !!(doc.undo || doc.saved);
    const status = t === "separate" ? (layers.background ? `Saved as ${layers.subject} and ${layers.background}` : made ? "Not saved yet" : "")
        : t === "cutout" ? (layers.subject ? `Saved as ${layers.subject}` : made ? "Not saved yet" : "")
            : doc.saved && !doc.dirty ? `Saved as ${doc.saved}` : doc.saved ? "Changed since it was saved" : made ? "Not saved yet" : "";
    return section("", [
        status ? el("div.gen-status", {text: status, translate: "no"}) : "",
        el(`button.btn.wide${accent && made ? ".accent" : ""}`, {type: "button", disabled: b || !made,
            title: made ? "Save it beside the original, or put it in Resolve" : "Nothing made yet",
            onclick: e => menuAt(e.currentTarget, exportItems())},
        [icon("download"), el("span", {text: EXPORT_LABELS[t]})]),
    ]);
}

// ------------------------------------------------------------- settings --

function modelPicker(b) {
    const have = ["birefnet-lite", "birefnet"].filter(m => state.engine.models[m]);
    if (have.length < 2) return "";
    return field("Edge detail", choice([["birefnet-lite", "Standard – faster"], ["birefnet", "Best – finer hair"]],
        state.engine.matte_model, id => send("matte_model", {id}), b), "Which model finds the subject – Best is about twice as slow");
}

/* Quality: Standard (LaMa; footage filled from the frames round it) or High (the High quality add-on:
   FLUX.2 klein for pictures, ROSE for footage) - only once the add-on's model for it is there. */
function qualityGroup(b, video) {
    const h = state.hq || {};
    if (!(video ? h.footage : h.pictures)) return "";
    const tip = video
        ? "Standard fills from the frames round it – faster, on any computer. High (ROSE) draws the shot again without it, shadows and reflections too – about a minute for every two seconds of footage"
        : "Standard (LaMa) takes a second or two – best for small things. High (FLUX.2 klein) draws what's behind it again – a street, a doorway – about 7 s";
    const rows = [field("Quality", choice([["standard", "Standard"], ["high", "High"]], h.on ? "high" : "standard",
        v => send("hq", {on: v === "high"}), b), tip)];
    if (h.on && video) {
        rows.push(el("label.check", {title: "ROSE takes out what the thing does to the scene too – its shadow on the ground, its reflection in water or glass – where that's next to it"}, [
            el("input", {type: "checkbox", checked: !!h.effects, disabled: b, onchange: e => send("hq_effects", {on: e.target.checked})}),
            el("span", {text: "Also its shadow and reflection"})]));
        rows.push(el("label.check", {title: "ROSE draws at about 720p instead of 480p – sharper where what's removed covers a big part of a 4K frame, about five times as long"}, [
            el("input", {type: "checkbox", checked: !!h.fine, disabled: b, onchange: e => send("hq_fine", {on: e.target.checked})}),
            el("span", {text: "Finer detail (about 5× slower)"})]));
    }
    return el("div.gen-group", {}, rows);
}

/* Margin: how far past what's painted (or the subject's edge) the removal reaches, in picture pixels -
   for a glow, motion blur or a soft edge the paint missed. Shown round the paint as it's dragged. */
const marginPx = () => marginDragged !== null ? marginDragged : (state && state.margin) || 0;

function marginSlider(b, what) {
    const m = marginPx();
    const row = slider("Margin", 0, 200, 2, m, `${m} px`,
        `How far past ${what} the removal reaches – for a glow, motion blur or a soft edge the paint missed`,
        (v, val) => { marginDragged = v; val.textContent = `${v} px`; draw(); }, undefined, v => send("margin", {px: v}));
    row.querySelector("input").disabled = !!b;
    return row;
}

function formatPicker(b, kinds) {
    const f = doc.formats.find(x => x.id === doc.video_format);
    const notes = [f ? f.about : ""];
    if (kinds.includes("subject") && f && f.ext && doc.video_format !== "prores_4444")
        notes.push("The subject is ProRes 4444, to keep its transparency.");
    if (!doc.ffmpeg) notes.push("Video files need ffmpeg – install it to get one.");
    return el("div.gen-group", {}, [
        field("Saves as", choice(doc.formats.map(x => [x.id, x.ext ? `${x.label} (${x.ext})` : x.label, !!x.ext && !doc.ffmpeg]),
            doc.video_format, id => send("video_format", {id}), b), "What the result is saved as"),
        el("div.gen-hint.small", {text: notes.filter(Boolean).join(" ")})]);
}

// ------------------------------------------------------- footage, in steps --
//
// Footage is the one place the order matters, so its panel is a list of
// steps - paint (or draw round it), follow it through the shot, run the job,
// export - with only one open: the one that's next, unless another's picked.
// Track carries the paint to every frame of the part, shown in colour over
// the picture (T, or the switch, hides it); the job then uses that tracking
// as it is. Changing the paint or the part after asks for it again:
// trackSig() is what was tracked.

let showTrack = true;
let stepPicked = 0;                 // a step opened by hand - until the one that's next changes
let stepAuto = 0;
let moreOpen = false;               // Follow's More options, kept open across redraws

function trackSig(which) {
    const s = JSON.stringify([doc.range, doc.tracker, doc.track_direction, keyframesFor(which)]);
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return `${s.length}.${h.toString(16)}`;
}

function trackState(which) {
    const keys = keyFrames(which);
    const has = keys.length > 0;
    // Painted on every frame of the part: there's nothing to follow (page.py runs it without tracking).
    const [a, b] = doc.range, set = new Set(keys);
    let every = has;
    for (let i = a; i < b && every; i++) every = set.has(i);
    const sig = has ? trackSig(which) : "";
    return {has, sig, every, keys, tracked: !!doc.track_sig, current: has && (every || doc.track_sig === sig)};
}

function toggleTrack() {
    if (!isVideo() || !Object.keys(doc.tracked).length) return;
    showTrack = !showTrack;
    drawAll();
}

const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

function footagePanel(parts, b) {
    const t = tab();
    const which = t === "remove" ? "remove" : "outline";
    const tr = trackState(which);
    const optional = t !== "remove";
    const stopped = doc.stopped && doc.stopped.op === t ? doc.stopped : null;
    const made = doc.finished && JOB_KINDS[t].some(k => doc.results.includes(k));
    const [a, z] = doc.track_span || doc.range;
    // The step that's next.
    const auto = doc.running || stopped ? 3
        : tr.every ? (made ? 4 : 3)
        : tr.has && !tr.tracked ? 1
            : tr.has && !tr.current ? 2
                : made ? 4
                    : !tr.has ? (optional ? 3 : 1) : 3;
    if (auto !== stepAuto) { stepAuto = auto; stepPicked = 0; }
    const open = doc.running ? 3 : stepPicked || auto;
    const verb = {remove: "Remove it", cutout: "Cut it out", separate: "Separate it"}[t];
    const steps = [
        {title: optional ? "Draw round it" : "Paint it", done: tr.has,
            note: tr.has ? plural(keyFrames(which).length, "frame") : optional ? "Optional" : "",
            body: () => stepPaint(b, which, tr, optional)},
        {title: "Follow it through the shot", done: tr.current,
            note: tr.every ? "Not needed" : tr.current ? plural(z - a, "frame") : tr.has && tr.tracked ? "Follow it again"
                : optional && !tr.has ? "Not needed" : "",
            body: () => stepFollow(b, which, tr, optional)},
        {title: verb, done: made && !doc.running,
            note: doc.running ? `${Math.round(((state.busy || {}).progress || 0) * 100)}%` : made ? "Done"
                : stopped ? `${stopped.done} of ${stopped.of} done` : "",
            body: () => stepRun(b, t, tr, stopped)},
        {title: "Export", done: false, note: "", body: () => stepExport(b, t)},
    ];
    parts.push(el("div.gen-steps", {}, steps.map((s, i) => {
        const n = i + 1, isOpen = n === open;
        const head = el("button.gen-step-head", {type: "button", disabled: !!doc.running || isOpen,
            onclick: () => { stepPicked = n; drawPanel(); }}, [
            el(`span.gen-step-n${s.done ? ".done" : ""}`, {}, s.done ? [icon("check")] : [String(n)]),
            el("span.gen-step-title", {text: s.title}), el("span.gen-step-note", {text: isOpen ? "" : s.note})]);
        return el(`div.gen-step${isOpen ? ".open" : ""}`, {}, [head, isOpen ? el("div.gen-step-body", {}, s.body()) : ""]);
    })));
}

function stepPaint(b, which, tr, optional) {
    const n = keyFrames(which).length;
    const text = !tr.has
        ? (optional ? "Optional: if Buddy might pick the wrong thing, draw round the subject on a frame or two. Otherwise go straight on."
            : "Paint over what should go on this frame. Where it moves a lot, paint it on a few frames – scrub to another to add one.")
        : `Painted on ${plural(n, "frame")}. Where it moves a lot, add another – scrub to that frame and ${optional ? "draw" : "paint"} there too.`;
    return [hint(text), marksSection(b),
        tr.has ? action("Next: follow it", genIcon("route"), () => { stepPicked = 2; drawPanel(); }, {accent: true, disabled: b}) : ""];
}

function stepFollow(b, which, tr, optional) {
    if (optional && !tr.has) return [hint("Not needed – nothing's drawn round the subject. It's found on every frame by itself.")];
    if (tr.every) {
        return [hint("Not needed – every frame of the part is painted, so there's nothing to follow."),
            action("Next", "right", () => { stepPicked = 3; drawPanel(); }, {disabled: b})];
    }
    // Painted on an unbroken run of frames inside a wider part: working on just those needs no following either.
    const k = tr.keys;
    const run = k.length > 1 && k[k.length - 1] - k[0] + 1 === k.length;
    const justPainted = run ? action(`Work on just the painted frames (${k[0]}–${k[k.length - 1]})`, "",
        () => send("video_range", {range: [k[0], k[k.length - 1] + 1]}), {disabled: b || doc.running,
            title: "In and out set to the frames you painted – each has its own paint, so nothing needs following"}) : "";
    const object = doc.tracker === "object";
    const hintText = !tr.has ? (which === "remove" ? "Paint over what to remove first." : "Draw round the subject first.")
        : tr.current ? "Scrub through to check the colour stays on it. Where it drifts, paint that frame too and follow it again."
            : tr.tracked ? "The paint or the part changed since it was followed – follow it again."
                : "Works out where it is on every frame. Then scrub through to check it.";
    const more = el("details.gen-more", {}, [el("summary", {text: "More options"}),
        field("Direction", choice([["both", "Both ways"], ["forward", "Forward from the paint"], ["backward", "Back from the paint"]],
            doc.track_direction, id => send("video_direction", {id}), b || doc.running),
        "Forward or back: only the frames from the painted one to the out or in point are worked on")]);
    if (moreOpen) more.open = true;
    more.addEventListener("toggle", () => { moreOpen = more.open; });
    return [
        field("Does it move?", choice([["standard", "No – it stays put"], ["object", "Yes – it moves around"]], doc.tracker,
            id => send("video_tracker", {id}), b || doc.running),
        "Stays put: the paint is followed as the shot moves – a sign, a logo, a mark. Fast. "
            + "Moves around: SAM 2 follows the thing itself as it moves, turns or passes behind something – about 1.5 s a frame"),
        object && !doc.sam_ready ? el("div.gen-group", {}, [
            hint(`Following something that moves needs SAM 2.1 Tiny (${doc.sam_mb} MB, once). It runs on this computer.`),
            action(`Download SAM 2 (${doc.sam_mb} MB)`, "download", () => send("install", {models: ["sam2-tiny"]}),
                {disabled: b || !!state.install})]) : "",
        justPainted,
        action(tr.tracked && tr.has ? "Follow it again" : "Follow it", genIcon("route"),
            () => send("video_track", {keyframes: keyframesFor(which), sig: trackSig(which)}),
            {accent: tr.has && !tr.current, disabled: b || !tr.has || (object && !doc.sam_ready),
                title: "Works out where it is on every frame of the part to work on"}),
        hint(hintText),
        Object.keys(doc.tracked).length ? el("label.check", {title: "Show where it was followed to (T)"}, [
            el("input", {type: "checkbox", checked: showTrack, onchange: e => { showTrack = e.target.checked; drawAll(); }}),
            el("span", {text: "Show where it's followed"})]) : "",
        tr.current ? action("Next", "right", () => { stepPicked = 3; drawPanel(); }, {disabled: b}) : "",
        more,
    ];
}

function stepRun(b, t, tr, stopped) {
    const ok = t === "remove" ? tr.current : !tr.has || tr.current;
    const [a, z] = tr.has && tr.current && !tr.every && doc.track_span ? doc.track_span : doc.range;
    const lock = b || doc.running;
    const verb = {remove: "Remove", cutout: "Cut out", separate: "Separate"}[t];
    return [
        stopped && !doc.running ? el("div.gen-group", {}, [
            hint(`Stopped part-way – ${stopped.done} of ${stopped.of} frames done (frames ${stopped.first}–${stopped.last}).`),
            action("Carry on", "spark", () => send("video_resume"), {accent: true, disabled: b,
                title: "Picks the job up at the first frame not done yet – with the paint, tracking and settings it was started with"}),
        ]) : "",
        t !== "cutout" ? qualityGroup(lock, true) : "",
        t !== "remove" ? modelPicker(lock) : "",
        t !== "cutout" ? marginSlider(lock, t === "remove" ? "what's painted" : "the subject's edge") : "",
        formatPicker(lock, JOB_KINDS[t]),
        doc.running ? hint("The settings stay as they are while it works. Stop keeps what's done.")
            : action(`${verb} ${plural(z - a, "frame")}`, "spark",
                () => send("video_run", {keyframes: keyframesFor(t === "remove" ? "remove" : "outline"), sig: tr.sig}),
                {accent: ok && !stopped, disabled: b || !ok,
                    title: ok ? "Frames appear as they're done – Stop keeps what's done" : "Follow it through the shot first"}),
    ];
}

function stepExport(b, t) {
    const have = JOB_KINDS[t].filter(k => doc.results.includes(k));
    const files = have.map(k => doc.files[k]).filter(Boolean);
    const text = !have.length || !doc.finished ? "Nothing made yet."
        : files.length ? `Saved as ${files.join(" and ")}, in “Buddy Generative” beside the clip.`
            : "Saved as image sequences, in “Buddy Generative” beside the clip.";
    return [el("div.gen-hint", {text, title: doc.out || "", translate: "no"}),
        action(have.length > 1 ? "Add both to the Media Pool" : "Add to the Media Pool", "plus",
            () => send("video_add", {kinds: have}),
            {accent: doc.finished && !!have.length, disabled: b || !have.length || !doc.finished || doc.running || !state.connected,
                title: "Into the original clip's bin – which is then shown, with it selected"}),
        action("Show in folder", "folder", () => send("reveal"), {disabled: b})];
}

// ------------------------------------------------- the middle of the picture --

const MB = id => state.engine.sizes[id];

/* A task's first time: what it needs, downloaded - right where it was picked. */
function downloadCard(need, t) {
    const env = engineOk() ? 0 : state.engine.env_mb;
    const err = state.install_error ? el("p.bad", {text: state.install_error}) : "";
    const extra = need.filter(m => m !== "matte").reduce((n, m) => n + MB(m), 0);
    const parts = [taskIcon(t), el("div.strong", {text: `${TASKS[t].name} needs a one-time download`}),
        el("p", {text: "It runs on this computer – nothing you work on is uploaded."})];
    if (need.includes("matte")) {
        parts.push(el("div.gen-choices", {}, [
            pick("Standard", `${env + extra + MB("birefnet-lite")} MB · about 3 s a picture`, "standard"),
            pick("Best", `${env + extra + MB("birefnet")} MB · about 7 s · finer hair`, "best"),
        ]));
    } else {
        parts.push(el("div.row", {}, [el("button.btn.accent", {type: "button", disabled: !!state.install,
            onclick: () => send("install", {models: need})},
            [icon("download"), el("span", {text: `Download (${env + extra} MB)`})])]));
    }
    parts.push(el("p.small", {text: "Every file is checked against its published hash."}), err,
        link("Back", () => send("tab", {id: doc ? prevTab : null}), {disabled: !!state.install || (doc && !prevTab)}));
    return el("div.gen-card", {}, parts);

    function pick(label, sub, quality) {
        return el("button.btn.gen-choice", {type: "button", disabled: !!state.install,
            onclick: () => send("install", {models: need, quality})},
            [el("span", {text: label}), el("span.small", {text: sub})]);
    }
}

/* Made once for a download, then only its line and bar change: made again with every line,
   the bar's slide (and the spinner) started over each time. */
let installNodes = null;
function installCard() {
    const i = state.install;
    if (!installNodes) {
        const fill = el("div"), bar = el("div.gen-bar", {}, [fill]), line = el("div.gen-line", {translate: "no"});
        installNodes = {fill, bar, line, card: el("div.gen-card.gen-install", {}, [
            el("div.gen-busy-title", {}, [el("span.gen-spin"), el("span", {text: "Downloading…"})]),
            bar, line,
            el("button.btn", {type: "button", text: "Stop", onclick: () => send("cancel_install")}),
        ])};
    }
    // A step that doesn't say how far it is (pip installing PyTorch): the bar slides instead of sitting at nothing.
    installNodes.bar.classList.toggle("endless", i.known === false);
    installNodes.fill.style.width = `${Math.max(2, i.pct)}%`;
    installNodes.line.textContent = i.line || "";
    return installNodes.card;
}

function drawCenter() {
    const box = $("center");
    const t = tab();
    let content = null, dim = false;
    if (!state.install) installNodes = null;
    if (state.install) { content = [installCard()]; dim = !!doc; }
    else if (!t) content = taskPicker();
    else if (state.engine.checking) content = [el("div.muted", {text: "Checking Generative's engine…"})];
    else if (!ready(t)) { content = [downloadCard(missing(t), t)]; dim = !!doc; }
    else if (!doc) content = dropZone(t);
    box.hidden = !content;
    box.classList.toggle("dim", dim);
    $("stage").classList.toggle("bare", !doc);
    // The download's card stays put (see installCard) - put back only if something else was shown.
    if (content && !(content.length === 1 && box.firstChild === content[0] && box.childNodes.length === 1)) {
        box.replaceChildren(...content);
    }
}

/* Nothing picked: the three tasks - and nothing else to do yet. */
function taskPicker() {
    return [el("div.gen-pick", {}, [
        el("div.gen-pick-title", {text: "What do you want to do?"}),
        el("div.gen-tasks", {}, TASK_ORDER.map(id => el("button.gen-taskcard", {type: "button", disabled: busy(),
            onclick: () => pickTask(id)}, [taskIcon(id), el("span.gen-taskcard-name", {text: TASKS[id].name}),
            el("span.gen-taskcard-about", {text: TASKS[id].about})]))),
        el("div.gen-pick-note#pick-note", {text: "Pictures and footage both work. Pick one, then bring in a file."}),
        offerLine(),
    ])];
}

/* Turned away: dropped before a task was picked - the choice says so. */
let flashTimer = 0;
function flashPick() {
    const note = $("pick-note");
    if (!note) return;
    note.textContent = "Pick what you want to do first – then drop it again.";
    note.classList.add("warn");
    $("center").querySelector(".gen-tasks")?.classList.add("warn");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => drawCenter(), 3500);
}

/* A task picked, nothing open yet: where it comes from. */
function dropZone(t) {
    return [el("div.gen-drop", {}, [
        el("div.gen-chip", {}, [taskIcon(t), el("span", {text: TASKS[t].name}),
            link("Change", () => send("tab", {id: null}), {disabled: busy()})]),
        icon("image"),
        el("div.strong", {text: "Drop a picture or clip here"}),
        el("div", {text: "A file from Explorer, or a clip off Resolve's timeline. The original is never changed."}),
        el("div.row", {}, [
            action("Open a file", "folder", () => send("open_file"), {accent: true, wide: false, disabled: busy()}),
            action("From Resolve", "film", () => send("from_resolve"), {wide: false, disabled: busy(),
                title: "The clip under the playhead – or the one selected in the Media Pool"}),
        ]),
        el("div.small", {text: "Pictures: JPEG, PNG, TIFF (16-bit too), WebP, EXR. Footage: MOV, MP4, MXF and more."}),
    ])];
}

/* An RTX Generative isn't using yet, or the High quality add-on it could run: one line, one at a time (Settings has them after). */
function offerLine() {
    const g = state.gpu || {}, h = state.hq || {};
    const off = busy() || !!state.install || !engineOk();
    if (!engineOk()) return "";
    if (g.hint) {
        return el("div.gen-offer", {title: "NVIDIA TensorRT for RTX, checked against its published hash. Each model is prepared for the card "
            + "the first time it's used (a minute or two, once). It's in Settings › Generative after."}, [
            icon("spark"), el("span", {text: `Your ${g.card} can make this many times faster`}), el("span.dim", {text: `· ${g.mb} MB`}),
            el("span.spacer"), action("Set up", "", () => send("install_gpu"), {wide: false, disabled: off}),
            link("Not now", () => send("dismiss_gpu_hint"))]);
    }
    if (h.hint) {
        return el("div.gen-offer", {title: `Where Standard leaves a smear, High draws what's behind again. For your ${h.card}: `
            + "PyTorch and two models (Apache 2.0), every file checked against its published hash. It's in Settings › Generative after."}, [
            icon("spark"), el("span", {text: `High quality removal for your ${h.card}`}), el("span.dim", {text: `· ${h.gb} GB`}),
            el("span.spacer"), action("Set up", "", () => send("install_hq"), {wide: false, disabled: off}),
            link("Not now", () => send("dismiss_hq_hint"))]);
    }
    return "";
}

function drawZoom() {
    const box = $("zoom");
    box.hidden = !doc || !pics.preview;
    if (box.hidden) return;
    const pct = Math.round(view.s * doc.scale * (window.devicePixelRatio || 1) * 100);
    const seg = el("div.segmented", {role: "group", "aria-label": "Zoom"}, [
        el("button", {type: "button", text: "Fit", title: "Fit the picture (F)", onclick: fit}),
        el("button.gen-pct#zoom-pct", {type: "button", text: `${pct}%`, title: "How big the picture is shown", tabindex: "-1"}),
        el("button", {type: "button", text: "100%", title: "Actual pixels (1)", onclick: actualPixels}),
    ]);
    box.replaceChildren(seg);
}

function updateZoomLabel() {
    const z = $("zoom-pct");
    if (z && doc) z.textContent = `${Math.round(view.s * doc.scale * (window.devicePixelRatio || 1) * 100)}%`;
}

// --- working: what, which step, how far, how long

let busyTimer = 0, busyShown = false;
function drawBusy() {
    const box = $("busy");
    const b = state.busy;
    if (!b) {
        clearInterval(busyTimer); busyTimer = 0; busyShown = false;
        box.hidden = true;
        return;
    }
    const age = Date.now() - b.started * 1000;
    if (!busyShown && age < BUSY_CARD_AFTER_MS) {
        setTimeout(() => { if (state.busy && state.busy.started === b.started) { busyShown = true; drawBusy(); } },
            BUSY_CARD_AFTER_MS - age);
        return;
    }
    busyShown = true;
    if (!busyTimer) busyTimer = setInterval(() => { const t = $("busy-time"); if (t && state.busy) t.textContent = seconds(); }, 500);
    const known = typeof b.progress === "number";
    const job = b.kind === "job";
    box.hidden = false;
    box.classList.toggle("job", job);
    // Made once for each piece of work, then only its words and bar change: made again on
    // every step, the spinner started its turn over each time and jumped instead of turning.
    if (!busyCard || busyCard.started !== b.started || busyCard.job !== job || !box.contains(busyCard.card)) {
        const stage = el("span"), detail = el("div.gen-busy-detail"), fill = el("div"), bar = el("div.gen-bar", {}, [fill]);
        const card = el("div.gen-card", {}, [
            el("div.gen-busy-title", {}, [el("span.gen-spin"), stage]),
            detail, bar,
            el("div.gen-busy-time#busy-time", {text: seconds()}),
            job ? el("button.btn", {type: "button", text: "Stop", title: "What's done is kept – Carry on picks it up, now or after the clip's closed",
                onclick: () => send("video_stop")}) : "",
        ]);
        box.replaceChildren(card);
        busyCard = {started: b.started, job, card, stage, detail, bar, fill};
    }
    busyCard.stage.textContent = `${b.stage}…`;
    busyCard.detail.textContent = b.detail || "";
    busyCard.bar.classList.toggle("endless", !known);
    busyCard.fill.style.width = `${Math.round((b.progress || 0) * 100)}%`;
}
let busyCard = null;                // the busy card's parts, while one piece of work goes on

function seconds() {
    const s = Math.max(0, Math.floor(Date.now() / 1000 - state.busy.started));
    return s < 1 ? "" : `${s} s`;
}

// ------------------------------------------------------------- drawing --

function size() {
    const r = canvas.getBoundingClientRect();
    return {w: r.width, h: r.height, dpr: window.devicePixelRatio || 1};
}

const pictureToScreen = (px, py) => [view.x + px * doc.scale * view.s, view.y + py * doc.scale * view.s];
const screenToPicture = (sx, sy) => [(sx - view.x) / (doc.scale * view.s), (sy - view.y) / (doc.scale * view.s)];

function fit() {
    const img = pics.preview && pics.preview.img;
    if (!img) return;
    const {w, h} = size();
    const s = Math.min((w - 24) / img.naturalWidth, (h - 24) / img.naturalHeight, 4);
    view = {s: Math.max(s, MIN_ZOOM), x: 0, y: 0, fitted: true};
    view.x = (w - img.naturalWidth * view.s) / 2;
    view.y = (h - img.naturalHeight * view.s) / 2;
    draw();
}

function zoomAt(factor, sx, sy) {
    if (!pics.preview) return;
    const s = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.s * factor));
    view.x = sx - (sx - view.x) * (s / view.s);
    view.y = sy - (sy - view.y) * (s / view.s);
    view.s = s;
    view.fitted = false;
    draw();
}

function actualPixels() {
    if (!pics.preview || !doc) return;
    const {w, h} = size();
    zoomAt((1 / doc.scale) / view.s / (window.devicePixelRatio || 1), pointer ? pointer.x : w / 2, pointer ? pointer.y : h / 2);
}

function strokePath(c, s) {
    const pts = s.points;
    c.lineWidth = Math.max(1, s.size * doc.scale * view.s);
    c.beginPath();
    const [x0, y0] = pictureToScreen(pts[0], pts[1]);
    c.moveTo(x0, y0);
    if (pts.length === 2) c.lineTo(x0 + 0.01, y0);
    for (let i = 2; i < pts.length; i += 2) {
        const [x, y] = pictureToScreen(pts[i], pts[i + 1]);
        c.lineTo(x, y);
    }
    c.stroke();
}

const isShape = s => !!s.shape && s.shape !== "brush";

/* A rectangle, ellipse or lasso's path, in screen pixels. */
function shapePath(c, s) {
    if (s.shape === "lasso") {
        c.beginPath();
        for (let i = 0; i < s.points.length; i += 2) {
            const [x, y] = pictureToScreen(s.points[i], s.points[i + 1]);
            if (i) c.lineTo(x, y); else c.moveTo(x, y);
        }
        c.closePath();
        return;
    }
    const [ax, ay] = pictureToScreen(s.points[0], s.points[1]);
    const [bx, by] = pictureToScreen(s.points[2], s.points[3]);
    const x = Math.min(ax, bx), y = Math.min(ay, by), w = Math.abs(bx - ax), h = Math.abs(by - ay);
    c.beginPath();
    if (s.shape === "ellipse") c.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, turnOf(s), 0, Math.PI * 2);
    else if (!s.angle) c.rect(x, y, w, h);
    else {
        // Turned about its middle, as the engine draws it (matte.draw_stroke).
        [[x, y], [x + w, y], [x + w, y + h], [x, y + h]].forEach(([px, py], i) => {
            const [tx, ty] = turnAbout(px, py, x + w / 2, y + h / 2, turnOf(s));
            if (i) c.lineTo(tx, ty); else c.moveTo(tx, ty);
        });
        c.closePath();
    }
}

/* A rectangle or ellipse's turn - its "angle": degrees, clockwise on screen, about its middle - in radians. */
const turnOf = s => (s.angle || 0) * Math.PI / 180;
function turnAbout(x, y, cx, cy, a) {
    const c = Math.cos(a), n = Math.sin(a), dx = x - cx, dy = y - cy;
    return [cx + dx * c - dy * n, cy + dx * n + dy * c];
}
function middle(s) {
    const [x0, y0, x1, y1] = box(s);
    return [(x0 + x1) / 2, (y0 + y1) / 2];
}

/* What a stroke's feather is a share of, in picture pixels (matte.stroke_radius). */
function strokeRadius(s) {
    if (!isShape(s)) return s.size / 2;
    const [x0, y0, x1, y1] = box(s);
    return Math.min(x1 - x0, y1 - y0) / 2;
}

function paintStroke(c, s, color = s.keep ? GREEN : RED, outward = false) {
    // The engine fades a feather across the outline, half its share of the
    // radius in and out (matte.feather_width); a blur of half that is the look.
    // Remove's paint (outward) fades as far, all of it past the outline -
    // all the way inside (matte._ramp_out): blurred twice as wide, then the
    // shape drawn solid again over it.
    const blur = (s.feather || 0) / 100 * strokeRadius(s) / 4 * doc.scale * view.s * (outward ? 2 : 1);
    const draw = () => {
        if (isShape(s)) {
            c.fillStyle = color;
            shapePath(c, s);
            c.fill();
        } else {
            c.strokeStyle = color;
            strokePath(c, s);
        }
    };
    c.filter = blur >= 0.5 ? `blur(${blur.toFixed(1)}px)` : "none";
    draw();
    if (outward && blur >= 0.5) {
        c.filter = "none";
        draw();
    }
}

// ------------------------------------------------- moving and resizing --

const HANDLE = 4;                   // half a handle's side, screen px
const ROTATE_OUT = 24;              // the turning handle: this far out past the top edge, screen px
const GRAB = 7;                     // how near a handle a press takes it
const editing = () => shape !== "brush";
const listOf = which => which === "fix" ? fixes : which === "outline" ? outline : strokes;
const paintList = () => listOf(using);
function setList(which, list) {
    if (which === "fix") fixes = list; else if (which === "outline") outline = list; else strokes = list;
}

function selectedShape() {
    if (!editing()) return null;
    const s = paintList()[selected];
    return s && isShape(s) && !s.hidden ? s : null;
}

/* A shape's box, smallest corner first: x0, y0, x1, y1 in picture pixels. */
function box(s) {
    const xs = s.points.filter((_, i) => i % 2 === 0), ys = s.points.filter((_, i) => i % 2);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/* The eight handles round a box - turned with it - and the turning
   handle out past its top, as [name, screen x, screen y]. */
function handles(s) {
    const [x0, y0, x1, y1] = box(s);
    const [ax, ay] = pictureToScreen(x0, y0), [bx, by] = pictureToScreen(x1, y1);
    const mx = (ax + bx) / 2, my = (ay + by) / 2, a = turnOf(s);
    const out = [["nw", ax, ay], ["n", mx, ay], ["ne", bx, ay], ["e", bx, my],
        ["se", bx, by], ["s", mx, by], ["sw", ax, by], ["w", ax, my], ["rot", mx, ay - ROTATE_OUT]];
    return out.map(([n, x, y]) => [n, ...turnAbout(x, y, mx, my, a)]);
}

function inside(s, px, py) {
    if (s.angle) [px, py] = turnAbout(px, py, ...middle(s), -turnOf(s));   // into its own, unturned, frame
    const [x0, y0, x1, y1] = box(s);
    if (px < x0 || px > x1 || py < y0 || py > y1) return false;
    if (s.shape === "lasso") return inPolygon(s.points, px, py);
    if (s.shape !== "ellipse") return true;
    const rx = (x1 - x0) / 2, ry = (y1 - y0) / 2;
    return ((px - x0 - rx) / rx) ** 2 + ((py - y0 - ry) / ry) ** 2 <= 1;
}

/* Even-odd: a lasso that crosses itself is filled as the engine fills it. */
function inPolygon(pts, px, py) {
    let inside = false;
    for (let i = 0, j = pts.length - 2; i < pts.length; j = i, i += 2) {
        const [xi, yi, xj, yj] = [pts[i], pts[i + 1], pts[j], pts[j + 1]];
        if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
}

/* What a press at screen (x, y) would take hold of: the selected shape's
   handle, else the top-most shape under it - {index, handle | "move"}. */
function hitTest(x, y) {
    if (!editing() || !doc) return null;
    const list = paintList();
    const sel = selectedShape();
    if (sel) {
        for (const [name, hx, hy] of handles(sel)) {
            if (Math.abs(x - hx) <= GRAB && Math.abs(y - hy) <= GRAB) return {index: selected, handle: name};
        }
    }
    const [px, py] = screenToPicture(x, y);
    if (sel && inside(sel, px, py)) return {index: selected, handle: "move"};
    for (let i = list.length - 1; i >= 0; i--) {
        if (isShape(list[i]) && !list[i].hidden && inside(list[i], px, py)) return {index: i, handle: "move"};
    }
    return null;
}

const CURSORS = {nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
    n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize", move: "move", rot: "grab"};
const HANDLE_ANGLE = {n: 0, ne: 45, e: 90, se: 135, s: 180, sw: 225, w: 270, nw: 315};

/* A handle's cursor, pointing the way it pulls once the shape's turned. */
function cursorFor(handle, s) {
    if (!(handle in HANDLE_ANGLE) || !s || !s.angle) return CURSORS[handle];
    const a = ((HANDLE_ANGLE[handle] + s.angle) % 180 + 180) % 180;
    return ["ns-resize", "nesw-resize", "ew-resize", "nwse-resize"][Math.round(a / 45) % 4];
}

/* The selected shape's points, from where they were when the drag began. */
function editTo(d, px, py, keepShape) {
    const s = paintList()[d.index];
    if (!s) return;
    const dx = px - d.from[0], dy = py - d.from[1];
    let [x0, y0, x1, y1] = d.box;
    if (d.handle === "rot") {
        // Turned about its middle by how far round it the pointer's gone (Shift: in 15° steps).
        const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        const by = (Math.atan2(py - cy, px - cx) - Math.atan2(d.from[1] - cy, d.from[0] - cx)) * 180 / Math.PI;
        let angle = (d.angle || 0) + by;
        if (keepShape) angle = Math.round(angle / 15) * 15;
        angle = Math.round(((((angle + 180) % 360) + 360) % 360 - 180) * 10) / 10;      // -180 .. 180
        if (s.shape === "lasso") {
            // A lasso's own points turn: its box stays square to the picture.
            const t = (angle - (d.angle || 0)) * Math.PI / 180, pts = [];
            for (let i = 0; i < d.points.length; i += 2) pts.push(...turnAbout(d.points[i], d.points[i + 1], cx, cy, t).map(round));
            s.points = pts;
        } else {
            s.angle = angle || undefined;
        }
        d.moved = d.moved || by !== 0;
        return;
    }
    if (d.handle === "move") {
        x0 += dx; x1 += dx; y0 += dy; y1 += dy;
    } else if (s.angle && s.shape !== "lasso") {
        // A turned shape: pulled along its own sides, the opposite side staying where it was.
        const a = turnOf(s), cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        const [lx, ly] = turnAbout(dx, dy, 0, 0, -a);
        let [l, t, r, b] = [x0 - cx, y0 - cy, x1 - cx, y1 - cy];
        const [ol, ot, or, ob] = [l, t, r, b];
        if (d.handle.includes("w")) l += lx;
        if (d.handle.includes("e")) r += lx;
        if (d.handle.includes("n")) t += ly;
        if (d.handle.includes("s")) b += ly;
        if (keepShape && d.handle.length === 2) {
            const k = Math.max(Math.abs(r - l) / Math.max(or - ol, 1e-6), Math.abs(b - t) / Math.max(ob - ot, 1e-6));
            const w = Math.sign(r - l || 1) * (or - ol) * k, h = Math.sign(b - t || 1) * (ob - ot) * k;
            if (d.handle.includes("w")) l = r - w; else r = l + w;
            if (d.handle.includes("n")) t = b - h; else b = t + h;
        }
        const [mx, my] = turnAbout((l + r) / 2, (t + b) / 2, 0, 0, a);
        const hw = Math.abs(r - l) / 2, hh = Math.abs(b - t) / 2;
        [x0, y0, x1, y1] = [cx + mx - hw, cy + my - hh, cx + mx + hw, cy + my + hh];
    } else {
        if (d.handle.includes("w")) x0 += dx;
        if (d.handle.includes("e")) x1 += dx;
        if (d.handle.includes("n")) y0 += dy;
        if (d.handle.includes("s")) y1 += dy;
        if (keepShape && d.handle.length === 2) {
            // A corner with Shift: the box grows or shrinks as it was, from the opposite corner.
            const [ox0, oy0, ox1, oy1] = d.box;
            const k = Math.max(Math.abs(x1 - x0) / Math.max(ox1 - ox0, 1e-6), Math.abs(y1 - y0) / Math.max(oy1 - oy0, 1e-6));
            const w = Math.sign(x1 - x0 || 1) * (ox1 - ox0) * k, h = Math.sign(y1 - y0 || 1) * (oy1 - oy0) * k;
            if (d.handle.includes("w")) x0 = x1 - w; else x1 = x0 + w;
            if (d.handle.includes("n")) y0 = y1 - h; else y1 = y0 + h;
        }
    }
    if (s.shape === "lasso") {
        // Every point of the outline, from the box it was in to the new one.
        const [ox0, oy0, ox1, oy1] = d.box;
        const kx = (x1 - x0) / Math.max(ox1 - ox0, 1e-6), ky = (y1 - y0) / Math.max(oy1 - oy0, 1e-6);
        s.points = d.points.map((v, i) => round(i % 2 ? y0 + (v - oy0) * ky : x0 + (v - ox0) * kx));
    } else {
        s.points = [round(x0), round(y0), round(x1), round(y1)];
    }
    d.moved = d.moved || dx !== 0 || dy !== 0;
}

/* Remembers the list in use as it is, before a change - unless the change
   carries on the last one (the same key: a slider dragged, keys nudging). */
function remember(which, key) {
    if (key && history[which].key === key) return;
    commit(which, copyList(listOf(which)), key);
}
/* A copy of the list taken before a change, kept once it did change. */
function commit(which, before, key) {
    const h = history[which];
    h.back.push(before);
    h.ahead = [];
    h.key = key || "";
}
function forget(which) {
    history[which] = {back: [], ahead: [], key: ""};
}
const copyList = list => list.map(s => ({...s, points: [...s.points]}));

/* After the list in use changed: the engine hears of a fix, the view redraws. */
function edited(soon) {
    if (using === "fix") {
        clearTimeout(edited.timer);
        if (soon) edited.timer = setTimeout(sendFixes, 300);
        else sendFixes();
    }
    drawPanel();
    draw();
}

function deleteSelected() {
    if (busy() || !selectedShape()) return;
    remember(using);
    paintList().splice(selected, 1);
    selected = -1;
    edited();
}

function nudge(dx, dy) {
    const s = selectedShape();
    if (!s || busy()) return;
    remember(using, `nudge ${selected}`);
    s.points = s.points.map((v, i) => round(v + (i % 2 ? dy : dx)));
    edited(true);
}

// ----------------------------------------------------------- the masks --
//
// Everything painted in the list in use, one row each, in the order it was
// painted (later paint goes over earlier; an eraser takes off what's before
// it): a rectangle or ellipse is a row of its own, a run of brush strokes of
// one kind is one "Brush" row. Each can be hidden - kept, but left out of
// what's removed or sent as a fix - or taken away; a shape's row selects it.

/* A new shape's number: one more than the highest of its kind still in the list. */
function nextNumber(list, kind) {
    return list.reduce((n, s) => s.shape === kind ? Math.max(n, s.n || 0) : n, 0) + 1;
}

/* The rows: {indices, shape: "brush" | "rect" | "ellipse", s: the first stroke}. */
function maskRows(list) {
    const rows = [];
    list.forEach((s, i) => {
        const last = rows[rows.length - 1];
        if (!isShape(s) && last && last.shape === "brush" && !!last.s.erase === !!s.erase
            && !!last.s.keep === !!s.keep && !!last.s.hidden === !!s.hidden) {
            last.indices.push(i);
        } else {
            rows.push({indices: [i], shape: isShape(s) ? s.shape : "brush", s});
        }
    });
    return rows;
}

function rowName(row) {
    const s = row.s;
    const kind = {ellipse: "ellipse", rect: "rectangle", lasso: "lasso"}[row.shape] || "brush";
    const verb = using === "fix" ? (s.keep ? "Keep" : "Take away") : s.erase ? "Erase" : "";
    const name = verb ? `${verb} ${kind}` : kind[0].toUpperCase() + kind.slice(1);
    if (row.shape !== "brush") return `${name} ${s.n || ""}`.trim();
    const n = row.indices.length;
    return `${name} · ${n} stroke${n === 1 ? "" : "s"}`;
}

/* Changes the list in use as one undoable step, then tells the engine (a fix) and redraws. */
function changeMasks(fn) {
    if (busy()) return;
    remember(using);
    fn(paintList());
    edited();
}

function toggleRow(row) {
    const hide = !row.s.hidden;
    changeMasks(list => {
        for (const i of row.indices) list[i].hidden = hide;
        if (hide && row.indices.includes(selected)) selected = -1;
    });
}

function deleteRow(row) {
    hovered = null;
    changeMasks(list => {
        for (const i of [...row.indices].sort((a, b) => b - a)) list.splice(i, 1);
        // The selected shape keeps its place among what's left.
        if (row.indices.includes(selected)) selected = -1;
        else selected -= row.indices.filter(i => i < selected).length;
    });
}

function clearMasks() {
    hovered = null;
    selected = -1;
    changeMasks(list => list.splice(0));
}

function pickRow(row) {
    if (row.shape === "brush" || row.s.hidden || busy()) return;
    selected = row.indices[0];
    shape = row.shape;                                  // its tool, so its handles show
    drawAll();
}

let marksOpen = false;              // the list of what's painted, opened

function marksSection(b) {
    const list = paintList();
    if (!list.length) return "";
    const rows = maskRows(list).map(row => {
        const s = row.s;
        const isSel = row.shape !== "brush" && row.indices[0] === selected && !!selectedShape();
        const swatch = el(`span.gen-mask-swatch${s.erase ? ".erase" : ""}`,
            {style: `--swatch: ${using === "outline" ? BLUE : s.keep ? GREEN : RED}`});
        const name = el("button.gen-mask-name", {type: "button", disabled: b,
            title: row.shape === "brush" ? "Brush strokes – hover to see them on the picture"
                : s.hidden ? "Hidden – show it to move or resize it" : "Select it to move or resize it",
            onclick: () => pickRow(row)}, [swatch, genIcon(row.shape), el("span", {text: rowName(row)})]);
        const node = el(`div.gen-mask${isSel ? ".selected" : ""}${s.hidden ? ".hidden" : ""}`, {}, [
            el("button.btn.icon.gen-mask-btn", {type: "button", disabled: b, "aria-pressed": String(!s.hidden),
                title: s.hidden ? "Show it – it's left out while hidden" : "Hide it – kept, but left out",
                onclick: () => toggleRow(row)}, [s.hidden ? genIcon("eyeOff") : icon("eye")]),
            name,
            el("button.btn.icon.gen-mask-btn", {type: "button", disabled: b, title: "Take it away",
                onclick: () => deleteRow(row)}, [icon("close")]),
        ]);
        node.addEventListener("pointerenter", () => { hovered = row.indices.map(i => list[i]); draw(); });
        node.addEventListener("pointerleave", () => { hovered = null; draw(); });
        return node;
    });
    hovered = null;
    const [one, many] = using === "fix" ? ["fix", "fixes"] : using === "outline" ? ["drawing", "drawings"]
        : ["painted area", "painted areas"];
    const head = el("div.gen-row.gen-marks-head", {}, [
        el("button.btn.ghost.gen-marks-toggle", {type: "button", "aria-expanded": String(marksOpen),
            title: marksOpen ? "Hide the list" : "List them – to hide one, take one away, or select a shape",
            onclick: () => { marksOpen = !marksOpen; drawPanel(); }},
        [el(`span.gen-chev${marksOpen ? ".open" : ""}`, {}, [icon("right")]),
            el("span", {text: `${rows.length} ${rows.length === 1 ? one : many}`})]),
        link("Clear", clearMasks, {disabled: b,
            title: Buddy.keys(using === "fix" ? "Take every fix away (Ctrl+Z puts them back)" : "Take it all away (Ctrl+Z puts it back)")}),
    ]);
    return el("div.gen-sec.gen-marks", {}, [head, marksOpen ? el("div.gen-masks", {role: "list"}, rows) : ""]);
}

/* Where Track carried the paint to on this frame: tinted as paint is, a little fainter. */
function drawTracked(url, w, h, dpr, color) {
    let im = trackPics[url];
    if (!im) {
        im = trackPics[url] = new Image();
        im.onload = draw;
        im.src = url;
        return;
    }
    if (!im.complete || !im.naturalWidth) return;
    lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    lctx.clearRect(0, 0, w, h);
    lctx.filter = "none";
    lctx.globalCompositeOperation = "source-over";
    lctx.drawImage(im, ...picRect());
    lctx.globalCompositeOperation = "source-in";
    lctx.fillStyle = color;
    lctx.fillRect(0, 0, w, h);
    lctx.globalCompositeOperation = "source-over";
    mctx.save();
    mctx.setTransform(1, 0, 0, 1, 0, 0);
    mctx.globalAlpha = PAINT_ALPHA * 0.8;
    mctx.drawImage(layer, 0, 0);
    mctx.restore();
}

/* Margin, round the paint on the paint layer (`layer`, drawn just before): the paint drawn again all
   round at the margin's distance - a ring of copies at a few distances - less the paint itself, faintly.
   Made again only when the paint, the view or the margin changes, and not while the paint's dragged. */
function drawMargin(shown, w, h, dpr) {
    const r = marginPx() * doc.scale * view.s * dpr;
    if (r < 1.5 || !shown.some(s => !s.erase)) return;
    let sum = 0;
    for (const s of shown) for (const v of s.points) sum += v;
    const key = [r, view.x, view.y, layer.width, layer.height, shown.length, sum,
        shown.map(s => `${s.erase ? "e" : ""}${s.feather || 0}${s.size || ""}${s.angle || ""}`).join()].join("|");
    if (key !== marginKey && !drag?.paint) {
        marginKey = key;
        sized(layer3, w, h, dpr);
        l3ctx.setTransform(1, 0, 0, 1, 0, 0);
        l3ctx.clearRect(0, 0, layer3.width, layer3.height);
        const steps = Math.min(36, Math.max(12, Math.ceil(r / 4)));
        for (const k of [1, 2 / 3, 1 / 3]) {
            for (let i = 0; i < steps; i++) {
                const a = (i / steps) * Math.PI * 2 + k;
                l3ctx.drawImage(layer, Math.cos(a) * r * k, Math.sin(a) * r * k);
            }
        }
        l3ctx.globalCompositeOperation = "destination-out";
        l3ctx.drawImage(layer, 0, 0);
        l3ctx.globalCompositeOperation = "source-over";
    }
    if (drag?.paint) return;                            // the band follows once the stroke's done
    mctx.save();
    mctx.setTransform(1, 0, 0, 1, 0, 0);
    mctx.globalAlpha = 0.28;
    mctx.drawImage(layer3, 0, 0);
    mctx.restore();
}

/* The masks of the row under the pointer, outlined on the picture. */
function drawHovered(list) {
    mctx.save();
    for (const s of list) {
        if (s.hidden) mctx.setLineDash([3, 4]);
        if (isShape(s)) {
            mctx.lineWidth = 3; mctx.strokeStyle = "rgba(0,0,0,.6)";
            shapePath(mctx, s); mctx.stroke();
            mctx.lineWidth = 1.5; mctx.strokeStyle = "rgba(255,255,255,.95)";
            shapePath(mctx, s); mctx.stroke();
        } else {
            mctx.lineCap = "round"; mctx.lineJoin = "round";
            mctx.globalAlpha = s.hidden ? 0.3 : 0.45;
            mctx.strokeStyle = "#fff";
            strokePath(mctx, s);
            mctx.globalAlpha = 1;
        }
        mctx.setLineDash([]);
    }
    mctx.restore();
}

/* The selected shape's outline and handles. */
function drawSelection(s) {
    mctx.save();
    mctx.lineWidth = 1;
    for (const [style, dash] of [["rgba(0,0,0,.75)", []], ["rgba(255,255,255,.95)", [5, 4]]]) {
        mctx.strokeStyle = style;
        mctx.setLineDash(dash);
        shapePath(mctx, s);
        mctx.stroke();
    }
    mctx.setLineDash([]);
    const hs = handles(s);
    const at = name => hs.find(h => h[0] === name);
    if (s.shape === "ellipse") {
        // Its box, faintly (turned with it): where the handles are.
        mctx.strokeStyle = "rgba(255,255,255,.35)";
        mctx.beginPath();
        for (const n of ["nw", "ne", "se", "sw"]) mctx.lineTo(at(n)[1], at(n)[2]);
        mctx.closePath();
        mctx.stroke();
    }
    // The turning handle, round, on a stalk out of the top edge.
    const [, nx, ny] = at("n"), [, rx, ry] = at("rot");
    mctx.strokeStyle = "rgba(255,255,255,.7)";
    mctx.beginPath(); mctx.moveTo(nx, ny); mctx.lineTo(rx, ry); mctx.stroke();
    mctx.fillStyle = "#fff";
    mctx.strokeStyle = "rgba(0,0,0,.8)";
    mctx.beginPath(); mctx.arc(rx, ry, HANDLE + 1, 0, Math.PI * 2); mctx.fill(); mctx.stroke();
    for (const [name, hx, hy] of hs) {
        if (name === "rot") continue;
        mctx.fillRect(hx - HANDLE, hy - HANDLE, HANDLE * 2, HANDLE * 2);
        mctx.strokeRect(hx - HANDLE + 0.5, hy - HANDLE + 0.5, HANDLE * 2 - 1, HANDLE * 2 - 1);
    }
    mctx.restore();
}

let frame = 0;
function draw() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; paint(); });
}

function sized(c, w, h, dpr) {
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
        c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    }
}

function picRect() {
    const img = pics.preview.img;
    return [view.x, view.y, img.naturalWidth * view.s, img.naturalHeight * view.s];
}

/* A preview at the size it's shown: scaled (smoothed) once per zoom and
   kept, then copied pixel for pixel - so moving the picture doesn't scale it
   again. Zoomed in to 2 screen pixels a picture pixel and more, it's drawn
   straight on, still smoothed - as Resolve's viewer zooms: drawn as hard
   squares, a soft shot zoomed in looked blocky beside it (2026-10-08). */
const scaledPics = new WeakMap();   // img -> {w, h, canvas}
function drawPic(c, img, x, y, pw, ph) {
    const dpr = window.devicePixelRatio || 1;
    const sw = Math.round(pw * dpr), sh = Math.round(ph * dpr);
    if (view.s * dpr >= 2 || sw < 1 || sh < 1 || sw * sh > 40e6) {
        c.imageSmoothingEnabled = true;
        c.imageSmoothingQuality = "high";
        c.drawImage(img, x, y, pw, ph);
        return;
    }
    let made = scaledPics.get(img);
    if (!made || made.w !== sw || made.h !== sh) {
        const cv = made ? made.canvas : document.createElement("canvas");
        cv.width = sw; cv.height = sh;
        const k = cv.getContext("2d");
        k.imageSmoothingEnabled = true;
        k.imageSmoothingQuality = "high";
        k.drawImage(img, 0, 0, sw, sh);
        made = {w: sw, h: sh, canvas: cv};
        scaledPics.set(img, made);
    }
    c.save();
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.drawImage(made.canvas, Math.round(x * dpr), Math.round(y * dpr));
    c.restore();
}

/* The subject alone - its colours cut by the matte - onto c. */
function drawSubject(c, w, h, dpr) {
    const [x, y, pw, ph] = picRect();
    sized(layer2, w, h, dpr);
    l2ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    l2ctx.clearRect(0, 0, w, h);
    l2ctx.globalCompositeOperation = "source-over";
    drawPic(l2ctx, (pics.subject || pics.preview).img, x, y, pw, ph);
    l2ctx.globalCompositeOperation = "destination-in";
    drawPic(l2ctx, pics.matte.img, x, y, pw, ph);
    l2ctx.globalCompositeOperation = "source-over";
    c.save();
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.drawImage(layer2, 0, 0);
    c.restore();
}

/* The mouse pointer for what a press would do here: a crosshair at the
   brush's centre when it paints (its circle drawn round it), a hand to
   move the picture, arrows to move or resize a shape, else the normal
   arrow - never hidden. */
function setCursor() {
    let c = drag?.pan ? "grabbing" : space && doc ? "grab" : canPaint() ? "crosshair" : "default";
    if (drag?.edit) c = drag.edit.handle === "rot" ? "grabbing" : cursorFor(drag.edit.handle, paintList()[drag.edit.index]);
    else if (c === "crosshair" && pointer && !drag) {
        const hit = hitTest(pointer.x, pointer.y);
        if (hit) c = cursorFor(hit.handle, paintList()[hit.index]);
    }
    if (canvas.style.cursor !== c) canvas.style.cursor = c;
}

function paint() {
    setCursor();
    updateZoomLabel();
    const {w, h, dpr} = size();
    if (w < 1 || h < 1) return;                     // laid out at no size (hidden): nothing to draw on
    sized(canvas, w, h, dpr);
    sized(marks, w, h, dpr);
    sized(layer, w, h, dpr);
    paintPicture(w, h, dpr);
    paintMarks(w, h, dpr);
}

/* What the picture canvas shows, as a key: the same key, nothing to draw. */
let pictureKey = "";

function paintPicture(w, h, dpr) {
    const ok = !!(doc && pics.preview);
    const t = tab();
    const cut = ok && showingCut() && !comparing;
    const key = ok ? JSON.stringify([canvas.width, canvas.height, dpr, view.s, view.x, view.y, t, cut, comparing,
        cutView, sepView, ...["preview", "original", "subject", "matte", "background"].map(k => pics[k] ? pics[k].url : "")])
        : "";
    if (key === pictureKey && ok) return;
    pictureKey = key;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!ok) return;
    const [x, y, pw, ph] = picRect();

    if (comparing && pics.original) {
        drawPic(ctx, pics.original.img, x, y, pw, ph);
    } else if (!cut) {
        drawPic(ctx, pics.preview.img, x, y, pw, ph);
    } else if (t === "cutout" && cutView === "matte") {
        ctx.fillStyle = "#000";
        ctx.fillRect(x, y, pw, ph);
        drawPic(ctx, pics.matte.img, x, y, pw, ph);
    } else if (t === "cutout" && cutView === "overlay") {
        drawPic(ctx, pics.preview.img, x, y, pw, ph);
        lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        lctx.clearRect(0, 0, w, h);
        lctx.globalCompositeOperation = "source-over";
        lctx.fillStyle = RED;
        lctx.fillRect(x, y, pw, ph);
        lctx.globalCompositeOperation = "destination-out";
        drawPic(lctx, pics.matte.img, x, y, pw, ph);
        lctx.globalCompositeOperation = "source-over";
        ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 0.45; ctx.drawImage(layer, 0, 0); ctx.restore();
    } else if (t === "separate" && sepView === "background") {
        drawPic(ctx, (pics.background || pics.preview).img, x, y, pw, ph);
    } else {
        if (t === "separate" && sepView === "both" && pics.background) drawPic(ctx, pics.background.img, x, y, pw, ph);
        drawSubject(ctx, w, h, dpr);
    }
}

function paintMarks(w, h, dpr) {
    mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    mctx.clearRect(0, 0, w, h);
    if (!doc || !pics.preview) return;
    const t = tab();
    const own = t === "remove" ? strokes : outline;
    if (isVideo() && showTrack && !comparing && !own.some(s => !s.hidden) && doc.tracked[vframe]) {
        drawTracked(doc.tracked[vframe], w, h, dpr, t === "remove" ? RED : BLUE);
    }
    // The paint: strokes to remove, and the Add / Subtract stroke being
    // painted - or the fix being moved, until the engine's matte has it.
    const sel = !comparing && canPaint() ? selectedShape() : null;
    const moving = using === "fix" && sel && drag?.edit ? [sel] : [];
    const shown = [...(t === "remove" ? strokes : []), ...(t !== "remove" && using === "outline" ? outline : []),
        ...(pending ? [pending] : []), ...moving].filter(s => !s.hidden);
    if (!comparing && shown.length) {
        lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        lctx.clearRect(0, 0, w, h);
        lctx.lineCap = "round"; lctx.lineJoin = "round";
        for (const s of shown) {
            lctx.globalCompositeOperation = s.erase ? "destination-out" : "source-over";
            paintStroke(lctx, s, using === "outline" && !s.keep ? BLUE : undefined, t === "remove" && strokes.includes(s));
        }
        lctx.filter = "none";
        lctx.globalCompositeOperation = "source-over";
        if (t === "remove" && using === "remove") drawMargin(shown, w, h, dpr);
        mctx.save();
        mctx.setTransform(1, 0, 0, 1, 0, 0);
        mctx.globalAlpha = PAINT_ALPHA;
        mctx.drawImage(layer, 0, 0);
        mctx.restore();
    }
    if (hovered && !comparing) drawHovered(hovered);
    if (sel) drawSelection(sel);
    // The brush, where it would paint (a shape's corner is the crosshair).
    if (pointer && canPaint() && !drag?.pan && !space && shape === "brush") {
        const r = Math.max(1, brush * doc.scale * view.s / 2);
        const fix = using === "fix";
        mctx.lineWidth = 1;
        mctx.strokeStyle = "rgba(0,0,0,.7)";
        mctx.beginPath(); mctx.arc(pointer.x, pointer.y, r + 1, 0, Math.PI * 2); mctx.stroke();
        mctx.strokeStyle = fix ? (keeping() ? "rgba(170,255,200,.95)" : "rgba(255,200,210,.95)")
            : (erasing() ? "rgba(255,255,255,.95)" : "rgba(255,200,210,.95)");
        mctx.setLineDash(!fix && erasing() ? [4, 3] : []);
        mctx.beginPath(); mctx.arc(pointer.x, pointer.y, r, 0, Math.PI * 2); mctx.stroke();
        mctx.setLineDash([]);
        // Feathered: dashed where the brush is still at full strength, and where its fade ends -
        // a removal's fades all past the edge, as far.
        if (feather > 0 && r * feather / 200 >= 2) {
            mctx.setLineDash([3, 3]);
            const out = tab() === "remove" && using === "remove";
            for (const k of out ? [1 + feather / 100] : [1 - feather / 200, 1 + feather / 200]) {
                mctx.beginPath(); mctx.arc(pointer.x, pointer.y, r * k, 0, Math.PI * 2); mctx.stroke();
            }
            mctx.setLineDash([]);
        }
    }
}

new ResizeObserver(() => { if (view.fitted) fit(); else draw(); }).observe($("stage"));

// --------------------------------------------------------------- brush --

function brushRange() {
    const long = doc ? Math.max(doc.width, doc.height) : 2000;
    return [2, Math.max(16, long / 4)];
}
function toSlider(px) {
    const [lo, hi] = brushRange();
    return Math.log(Math.max(lo, Math.min(hi, px || lo)) / lo) / Math.log(hi / lo);
}
function fromSlider(v) {
    const [lo, hi] = brushRange();
    return lo * Math.pow(hi / lo, v);
}
function defaultBrush() {
    return Math.max(8, Math.round(Math.max(doc.width, doc.height) / 40));
}
function setBrush(px) {
    const [lo, hi] = brushRange();
    brush = Math.max(lo, Math.min(hi, px));
    const v = $("size-val");
    if (v) {
        v.textContent = `${Math.round(brush)} px`;
        const input = v.parentNode.querySelector("input");
        if (input) input.value = String(Math.round(toSlider(brush) * 1000));
    }
    draw();
}

const erasing = () => removeMode === "erase" || (alt && !altSized);
const keeping = () => (fixMode === "keep") !== (alt && !altSized);
const canPaint = () => !!(doc && !busy() && ready() && (using === "remove" ? tab() === "remove"
    : using === "outline" ? subjectTask() : matteReady()));

// ------------------------------------------------------------ footage --

/* Puts the paint on the frame shown away with that frame. */
function stash() {
    if (!isVideo()) return;
    for (const [which, list] of [["remove", strokes], ["outline", outline]]) {
        if (list.length) keyed[which][vframe] = copyList(list);
        else delete keyed[which][vframe];
    }
}

/* Frame i is the one painted on now: its own paint out, history fresh. */
function adoptFrame(i) {
    stash();
    vframe = i;
    strokes = copyList(keyed.remove[i] || []);
    outline = copyList(keyed.outline[i] || []);
    forget("remove"); forget("outline");
    selected = -1;
}

function goFrame(i) {
    if (!isVideo()) return;
    i = Math.max(0, Math.min(doc.frames - 1, Math.round(i)));
    if (i === vframe) return;
    adoptFrame(i);
    askedAt = Date.now();
    send("video_frame", {index: i});
    drawAll();
}

/* The frames with paint (of one list) - the one shown as it is now. */
function keyFrames(which = using === "outline" ? "outline" : "remove") {
    const got = new Set(Object.keys(keyed[which]).map(Number));
    const here = which === "outline" ? outline : strokes;
    if (here.some(s => !s.hidden)) got.add(vframe);
    else got.delete(vframe);
    return [...got].sort((a, b) => a - b);
}

/* What a job is sent: frame -> the paint shown on it (hidden masks left out). */
function keyframesFor(which) {
    stash();
    const out = {};
    for (const [k, list] of Object.entries(keyed[which])) {
        const shown = shownOf(list);
        if (shown.length) out[k] = shown;
    }
    return out;
}

function jumpKey(dir) {
    const ks = keyFrames();
    const to = dir > 0 ? ks.find(k => k > vframe) : [...ks].reverse().find(k => k < vframe);
    if (to !== undefined) goFrame(to);
}

function setRange(first, last) {
    if (!isVideo() || busy()) return;
    let [a, b] = doc.range;
    if (first !== null) { a = first; if (b <= a) b = doc.frames; }
    if (last !== null) { b = last + 1; if (a >= b) a = 0; }
    send("video_range", {range: [a, b]});
}

function timecode(i) {
    const r = Math.max(1, Math.round(doc.fps));
    const f = i % r, sec = Math.floor(i / r);
    const two = n => String(n).padStart(2, "0");
    return `${two(Math.floor(sec / 3600))}:${two(Math.floor(sec / 60) % 60)}:${two(sec % 60)}:${two(f)}`;
}

let scrub = null;                   // the scrubber's parts, made once per clip

function drawScrub() {
    const box = $("scrub");
    box.hidden = !isVideo();
    if (box.hidden) { scrub = null; box.replaceChildren(); return; }
    if (!scrub || scrub.path !== doc.path) buildScrub();
    const n = Math.max(1, doc.frames - 1);
    const at = i => `${(i / n) * 100}%`;
    const span = (node, a, b) => { node.style.left = at(a); node.style.width = `${(Math.max(0, b - a) / n) * 100}%`; };
    const [a, b] = doc.range;
    span(scrub.range, a, b - 1);
    const rr = doc.results_range;
    scrub.made.hidden = !(rr && doc.results.length);
    if (rr) span(scrub.made, rr[0], rr[1] - 1);
    scrub.keys.replaceChildren(...keyFrames().map(i =>
        el("div.gen-track-key", {style: `left:${at(i)}`, title: `Painted on frame ${i}`})));
    scrub.head.style.left = at(vframe);
    scrub.label.textContent = `${timecode(vframe)} · frame ${vframe}`;
    scrub.span.textContent = a === 0 && b === doc.frames ? `The whole clip · ${((b - a) / doc.fps).toFixed(1)} s`
        : `Frames ${a}–${b - 1} · ${((b - a) / doc.fps).toFixed(1)} s`;
    const locked = busy() && state.busy.kind !== "job";
    for (const x of scrub.moves) x.disabled = locked;
    scrub.part.disabled = busy() || doc.running;
}

function buildScrub() {
    const track = el("div.gen-track", {title: "Drag through the clip – ← → a frame at a time (Shift: 10)"});
    const range = el("div.gen-track-range"), made = el("div.gen-track-made"), keys = el("div.gen-track-keys");
    const head = el("div.gen-track-head");
    track.append(range, made, keys, head);
    const frameAt = x => {
        const r = track.getBoundingClientRect();
        return Math.round(Math.max(0, Math.min(1, (x - r.left) / r.width)) * (doc.frames - 1));
    };
    let held = false;
    track.addEventListener("pointerdown", e => {
        if (e.button !== 0 || (busy() && state.busy.kind !== "job")) return;
        held = true;
        track.setPointerCapture(e.pointerId);
        goFrame(frameAt(e.clientX));
    });
    track.addEventListener("pointermove", e => { if (held) goFrame(frameAt(e.clientX)); });
    for (const ev of ["pointerup", "pointercancel"]) track.addEventListener(ev, () => { held = false; });
    const btn = (ic, tip, fn) => el("button.btn.icon", {type: "button", title: tip, onclick: fn}, [ic]);
    const prevKey = btn(genIcon("keyPrev"), "The painted frame before", () => jumpKey(-1));
    const nextKey = btn(genIcon("keyNext"), "The painted frame after", () => jumpKey(1));
    const prev = btn(icon("left"), "A frame back (←)", () => goFrame(vframe - 1));
    const next = btn(icon("right"), "A frame on (→)", () => goFrame(vframe + 1));
    // The part to work on: one menu - start or end here, what the timeline uses, the whole clip.
    const spanText = el("span.gen-scrub-span", {translate: "no"});
    const part = el("button.btn.gen-part", {type: "button", title: "The part of the clip to work on",
        onclick: e => menuAt(e.currentTarget, [
            {label: "Start here (Shift+I)", onClick: () => setRange(vframe, null)},
            {label: "End here (Shift+O)", onClick: () => setRange(null, vframe)},
            {label: "The part the timeline uses", disabled: !state.connected, onClick: () => send("video_from_timeline")},
            {label: "The whole clip", onClick: () => send("video_range", {range: [0, doc.frames]})},
        ])}, [el("span.dim", {text: "Working on"}), spanText, icon("more")]);
    const label = el("span.gen-scrub-label", {translate: "no"});
    $("scrub").replaceChildren(track, el("div.gen-scrub-row", {}, [prevKey, prev, label, next, nextKey,
        el("div.spacer"), part]));
    scrub = {path: doc.path, range, made, keys, head, label, span: spanText, part,
        moves: [prevKey, prev, next, nextKey]};
}

// ------------------------------------------------------------- pointer --

canvas.addEventListener("contextmenu", e => e.preventDefault());

canvas.addEventListener("pointerdown", e => {
    if (!doc || !pics.preview) return;
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    canvas.setPointerCapture(e.pointerId);
    if (e.button === 1 || e.button === 2 || (e.button === 0 && space)) {
        drag = {pan: true, x, y, vx: view.x, vy: view.y};
        e.preventDefault();
        draw();
        return;
    }
    if (e.button !== 0 || !canPaint()) return;
    const [px, py] = screenToPicture(x, y);
    const hit = hitTest(x, y);
    if (hit) {
        // Taking hold of a shape: moved or resized from its box as it is now.
        selected = hit.index;
        const s = paintList()[hit.index];
        drag = {edit: {index: hit.index, handle: hit.handle, from: [px, py], box: box(s), points: [...s.points],
            angle: s.angle || 0,
            moved: false}, before: copyList(paintList())};
        drawPanel();
        draw();
        return;
    }
    const size = Math.round(brush * 10) / 10;
    const start = shape === "brush" ? {size, feather, points: [round(px), round(py)]}
        : shape === "lasso" ? {shape, feather, n: nextNumber(paintList(), shape), points: [round(px), round(py)]}
            : {shape, feather, n: nextNumber(paintList(), shape), points: [round(px), round(py), round(px), round(py)]};
    if (using !== "fix") {
        // Remove's paint, or what's drawn round the subject: straight into its list.
        const list = paintList();
        const stroke = {...start, erase: erasing()};
        if (stroke.erase && !list.length) return;        // nothing to take off
        const before = copyList(list);
        list.push(stroke);
        selected = isShape(stroke) ? list.length - 1 : -1;
        drag = {paint: stroke, before, from: [round(px), round(py)], which: using};
    } else {
        pending = {...start, keep: keeping()};
        drag = {paint: pending, fix: true, from: [round(px), round(py)]};
    }
    drawPanel();
    draw();
});

canvas.addEventListener("pointermove", e => {
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    pointer = {x, y};
    if (drag?.pan) {
        view.x = drag.vx + (x - drag.x);
        view.y = drag.vy + (y - drag.y);
        view.fitted = false;
    } else if (drag?.edit) {
        editTo(drag.edit, ...screenToPicture(x, y), e.shiftKey);
        updateShapeLabel();
    } else if (drag?.paint && isShape(drag.paint) && drag.paint.shape !== "lasso") {
        shapeTo(drag.paint, drag.from, ...screenToPicture(x, y), e.shiftKey);
        updateShapeLabel();
    } else if (drag?.paint) {
        const pts = drag.paint.points;
        const [px, py] = screenToPicture(x, y);
        const lx = pts[pts.length - 2], ly = pts[pts.length - 1];
        if (Math.hypot(px - lx, py - ly) * doc.scale * view.s >= 2) pts.push(round(px), round(py));
        if (drag.paint.shape === "lasso") updateShapeLabel();
    }
    draw();
});

/* While a shape's drawn: a rectangle's far corner follows the pointer, an
   ellipse grows out from where the press was, its centre (as Lightroom's
   do). Shift makes a square or circle. */
function shapeTo(s, from, px, py, square) {
    const [x0, y0] = from;
    let dx = px - x0, dy = py - y0;
    if (square) {
        const side = Math.max(Math.abs(dx), Math.abs(dy));
        dx = Math.sign(dx || 1) * side; dy = Math.sign(dy || 1) * side;
    }
    if (s.shape === "ellipse") s.points = [round(x0 - dx), round(y0 - dy), round(x0 + dx), round(y0 + dy)];
    else s.points = [round(x0), round(y0), round(x0 + dx), round(y0 + dy)];
}

/* A shape too small to see on screen was a click, not a drag. */
function tooSmall(s) {
    if (!isShape(s)) return false;
    if (s.shape === "lasso" && s.points.length < 6) return true;
    const k = doc.scale * view.s;
    const [x0, y0, x1, y1] = box(s);
    return (x1 - x0) * k < 2 || (y1 - y0) * k < 2;
}

function endDrag() {
    const d = drag;
    drag = null;
    if (d?.edit) {
        const s = paintList()[d.edit.index];
        if (s && d.edit.moved && tooSmall(s)) s.points = [...d.edit.points];    // squashed flat: as it was
        else if (s && d.edit.moved) {
            if (s.shape !== "lasso") s.points = box(s);         // smallest corner first again, if dragged past
            commit(using, d.before);
            edited();
            return;
        }
        drawPanel();                                            // a press that only picked it
    } else if (d?.paint && tooSmall(d.paint)) {
        // A click with a shape: nothing drawn - and a click off every shape lets go of the one selected.
        const list = listOf(d.which);
        if (d.fix) pending = null;
        else if (list[list.length - 1] === d.paint) list.pop();
        selected = -1;
    } else if (d?.paint && !d.fix) {
        commit(d.which, d.before);
    } else if (d?.fix && pending) {
        remember("fix");
        fixes.push(pending);
        if (isShape(pending)) selected = fixes.length - 1;
        pending = null;
        sendFixes();
    }
    if (d?.paint) drawPanel();
    draw();
}
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);
canvas.addEventListener("pointerleave", () => { pointer = null; draw(); });

canvas.addEventListener("wheel", e => {
    if (!doc) return;
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    // Some mice send Alt + wheel as a sideways scroll: either way is the size.
    const delta = e.deltaY || e.deltaX;
    const factor = Math.pow(1.0015, -delta * (e.deltaMode === 1 ? 33 : 1));
    if (e.altKey) {
        altSized = true;
        pointer = {x: e.clientX - r.left, y: e.clientY - r.top};
        setBrush(brush * Math.min(1.5, Math.max(1 / 1.5, factor)));
        return;
    }
    zoomAt(factor, e.clientX - r.left, e.clientY - r.top);
}, {passive: false});

const round = v => Math.round(v * 10) / 10;

// ------------------------------------------------------------- actions --

// Hidden masks stay in the list, out of what the engine is sent.
const shownOf = list => list.filter(s => !s.hidden);
const paintToRemove = () => strokes.some(s => !s.erase && !s.hidden);

function sendFixes() {
    send("matte_edit", {strokes: shownOf(fixes)});
    drawPanel();
}

function remove() {
    if (busy() || isVideo() || tab() !== "remove" || !paintToRemove()) return;
    send("remove", {strokes: shownOf(strokes)});
}

/* The list in use back (or forward) to how it was; then, for Remove, the engine's own history. */
function step(from, to, engine) {
    if (busy()) return;
    const h = history[using];
    if (h[from].length) {
        h[to].push(copyList(paintList()));
        const list = h[from].pop();
        setList(using, list);
        h.key = "";
        if (selected >= list.length) selected = -1;
        edited();
    } else if (using === "remove" && doc && doc[engine]) send(engine);
}
const undo = () => step("back", "ahead", "undo");
const redo = () => step("ahead", "back", "redo");

/* Esc: lets go of the selected shape - or, with none, clears the paint (undoable). */
function clearPaint() {
    if (selectedShape()) { selected = -1; drawAll(); return; }
    if (using !== "remove" || !strokes.length) return;
    remember("remove");
    strokes = [];
    drawAll();
}

function compare(on) {
    if (comparing === on || (on && !pics.original)) return;
    comparing = on;
    for (const b of document.querySelectorAll(".gen-compare")) b.classList.toggle("held", on);
    draw();
}

$("open").addEventListener("click", () => send("open_file"));
$("close").addEventListener("click", () => send("close"));
$("from-resolve").addEventListener("click", () => send("from_resolve"));

const typing = e => e.target instanceof HTMLInputElement && e.target.type !== "range" || e.target instanceof HTMLTextAreaElement;

addEventListener("keydown", e => {
    if (e.key === "Alt") { alt = true; draw(); e.preventDefault(); }
    if (!doc || typing(e)) return;
    const k = e.key.toLowerCase();
    const mod = e.ctrlKey || e.metaKey;
    const fixable = matteReady() && using === "fix";
    if (mod && k === "z" && !e.shiftKey) { undo(); e.preventDefault(); }
    else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { redo(); e.preventDefault(); }
    else if (mod) return;
    else if (e.key === " ") { space = true; draw(); e.preventDefault(); }
    else if (isVideo() && e.shiftKey && k === "i") setRange(vframe, null);
    else if (isVideo() && e.shiftKey && k === "o") setRange(null, vframe);
    else if (k === "b") { pickShape("brush"); if (using === "remove") pickRemove("brush"); else if (using === "outline") pickOutline("brush"); }
    else if (k === "r") pickShape("rect");
    else if (k === "o") pickShape("ellipse");
    else if (k === "l") pickShape("lasso");
    else if ((e.key === "Delete" || e.key === "Backspace") && selectedShape()) { deleteSelected(); e.preventDefault(); }
    else if (e.key.startsWith("Arrow") && selectedShape()) {
        // A picture pixel at a time - a screen pixel when zoomed out, so each press shows.
        const n = (e.shiftKey ? 10 : 1) * Math.max(1, 1 / (doc.scale * view.s));
        nudge({ArrowLeft: -n, ArrowRight: n}[e.key] || 0, {ArrowUp: -n, ArrowDown: n}[e.key] || 0);
        e.preventDefault();
    }
    else if (isVideo() && (e.key === "ArrowLeft" || e.key === ",")) { goFrame(vframe - (e.shiftKey ? 10 : 1)); e.preventDefault(); }
    else if (isVideo() && (e.key === "ArrowRight" || e.key === ".")) { goFrame(vframe + (e.shiftKey ? 10 : 1)); e.preventDefault(); }
    else if (isVideo() && e.key === "Home") { goFrame(doc.range[0]); e.preventDefault(); }
    else if (isVideo() && e.key === "End") { goFrame(doc.range[1] - 1); e.preventDefault(); }
    else if (using === "remove" && k === "e") pickRemove("erase");
    else if (using === "outline" && k === "e") pickOutline("erase");
    else if (fixable && k === "a") pickFix("keep");
    else if (fixable && k === "s") pickFix("drop");
    else if (e.key === "[") setBrush(brush / 1.15);
    else if (e.key === "]") setBrush(brush * 1.15);
    else if (e.key === "Enter" && tab() === "remove") { remove(); e.preventDefault(); }
    else if (e.key === "Escape") clearPaint();
    else if (k === "f") fit();
    else if (k === "t") toggleTrack();
    else if (e.key === "1") actualPixels();
    else if (e.key === "\\") compare(true);
    else if (e.key === "?") showKeys();
});

addEventListener("keyup", e => {
    if (e.key === "Alt") { alt = false; altSized = false; draw(); }
    if (e.key === " ") { space = false; draw(); }
    if (e.key === "\\") compare(false);
});

addEventListener("blur", () => { alt = space = altSized = false; compare(false); });
