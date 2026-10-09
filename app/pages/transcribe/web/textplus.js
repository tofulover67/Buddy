/*
 * The Text+ tabs on Transcribe (Font styling, Timeline layout, Word-by-word, Timeline
 * animation, Custom animation). text_animator/text_plus.py owns every setting and makes every Resolve call;
 * this draws what it sends and reports what the user did. The placement canvases are
 * canvas.js. Its messages are "tp_"-prefixed both ways, so they never meet Transcribe's own;
 * the tabs themselves, the toasts, alerts and activity log are transcribe.js's.
 */
(() => {
"use strict";

const {el, icon} = Buddy;
const send = (name, data) => Buddy.send(`tp_${name}`, data);
const on = (name, fn) => Buddy.on(`tp_${name}`, fn);
const $ = id => document.getElementById(id);
const $$ = sel => [...document.querySelectorAll(sel)];

for (const node of $$("[data-tp]")) node.addEventListener("click", () => send(node.dataset.tp));
for (const b of $$("[data-undo]")) { if (!b.textContent) b.append(icon("undo")); b.onclick = () => send("undo"); }
for (const b of $$("[data-redo]")) {
    if (!b.textContent) { const i = icon("undo"); i.style.transform = "scaleX(-1)"; b.append(i); }
    b.onclick = () => send("redo");
}

// --------------------------------------------------------------- tabs --

// Which of the page's tabs is on screen: transcribe.js says (the "buddy-tab" event).
const TABS = new Set(["style", "layout", "wordbyword", "animation", "words"]);
let TAB = "subtitles";
document.addEventListener("buddy-tab", e => { TAB = e.detail; closeGuides(); });

// Undo / Redo anywhere on a Text+ tab, outside a text box.
document.addEventListener("keydown", e => {
    if (!TABS.has(TAB) || e.target.closest("input, textarea, select") || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
    e.preventDefault();
    send(e.shiftKey ? "redo" : "undo");
});

// -------------------------------------------------------------- state --

let STATE = null;

// Specific track's choices: the timeline's own video tracks (and a saved one past them, so
// the choice still shows) - an Apply only changes tracks that are there.
function trackOptions(select, count, value) {
    const n = Math.max(count, value, 1);
    if (select.options.length !== n) {
        select.replaceChildren(...Array.from({length: n}, (_, i) => el("option", {value: i + 1, text: String(i + 1)})));
    }
    select.value = String(value);
}

on("state", s => {
    STATE = s;
    if (OPT) {
        drawScopes(OPT);
    }
    $("bounding-on").checked = s.bounding.on;
    $("apply-bounding").disabled = !s.bounding.on;
    canvases.layout.setBounding(s.bounding);
    if (OPT) drawLooks(OPT);
    drawPreview();
});

$("bounding-on").onchange = e => send("bounding", {on: e.target.checked});

// ------------------------------------------------------------ options --

let OPT = null;
let FONTS = [];
const set = (name, value) => send("set", {name, value});

on("fonts", list => {
    FONTS = list;
    $("font").replaceChildren(...list.map(f => el("option", {value: f, text: f})));
    if (OPT) $("font").value = OPT.font_display;
});
$("font").onchange = e => set("font_name", e.target.value);

// How a slider's value reads: Size as the text's height in pixels on this timeline's frame
// (Text+ makes ascent + descent TEXT_PLUS_HEIGHT x Size x the frame's width), opacity as a %.
// scale: shown = value x scale; it's typed in the same units.
const frameWidth = () => ((STATE && STATE.resolution) || [1920, 1080])[0];
const PCT = {scale: () => 100, digits: 0, suffix: "%"};
const UNITS = {
    font_size: {scale: () => TEXT_PLUS_HEIGHT * frameWidth(), digits: 0, suffix: " px"},
    outline_opacity: PCT, shadow_opacity: PCT, background_opacity: PCT,
};
const shown = (unit, v) => `${(v * unit.scale()).toFixed(unit.digits)}${unit.suffix}`;

// A value a slider's steps don't land on (a typed one) is kept as it is, not snapped to a step.
function place(input, v, step) {
    const n = (v - Number(input.min)) / step;
    input.step = Math.abs(n - Math.round(n)) < 1e-6 ? step : "any";
    input.value = v;
}

/* Double-click a slider's value to type one, in the units it reads in. Enter or clicking
   away sets it, Esc leaves it as it was. put(value) gets it in the slider's own units,
   rounded to decimals and kept between the slider's ends. */
function typeIn(out, input, unit, decimals, put) {
    out.title = "Double-click to type a value";
    out.addEventListener("dblclick", e => {
        e.preventDefault();
        if (out.querySelector("input")) return;
        const old = out.textContent;
        const box = el("input.field.typein", {type: "number", step: "any", "aria-label": input.getAttribute("aria-label") || ""});
        box.value = +(Number(input.value) * unit.scale()).toFixed(unit.digits);       // as it read
        out.replaceChildren(box);
        box.focus();
        box.select();
        let done = false;
        const finish = keep => {
            if (done) return;
            done = true;
            out.textContent = old;
            const n = Number(box.value);
            if (!keep || box.value.trim() === "" || !Number.isFinite(n)) return;
            const v = Math.min(Number(input.max), Math.max(Number(input.min), +(n / unit.scale()).toFixed(decimals)));
            put(v);
        };
        box.addEventListener("keydown", ev => {
            ev.stopPropagation();
            if (ev.key === "Enter") finish(true);
            else if (ev.key === "Escape") finish(false);
        });
        box.addEventListener("blur", () => finish(true));
        // Clicks in the box stay in it: not the label's, which would hand them to the slider.
        box.addEventListener("click", ev => ev.preventDefault());
    });
}

function buildSlider(host, name, spec) {
    const input = el("input", {type: "range", min: spec.min, max: spec.max, step: spec.step, "aria-label": spec.label,
                               title: `Double-click to reset to ${spec.default}`});
    const value = el("b.val");
    const unit = UNITS[name] || {scale: () => 1, digits: spec.decimals, suffix: ""};
    const show = () => {
        const v = Number(input.value);
        value.textContent = shown(unit, v);
        value.title = UNITS[name] ? `Text+ ${spec.label}: ${v.toFixed(spec.decimals)} – double-click to type a value`
                                  : "Double-click to type a value";
    };
    const put = v => { place(input, v, spec.step); show(); drawPreview(); set(name, v); };
    input.addEventListener("input", () => { show(); drawPreview(); });
    input.addEventListener("change", () => set(name, Number(input.value)));
    // Double-click puts it back to its default; double-clicking its value types one.
    input.addEventListener("dblclick", () => put(spec.default));
    typeIn(value, input, unit, spec.decimals, put);
    host.replaceChildren(el("label.slider", {}, [el("span", {text: spec.label}), input, value]));
    host._input = input;
    host._show = show;
    host._place = v => place(input, v, spec.step);
}

// A colour: a few usual ones to click, and any colour from Buddy's own picker.
function colours(host, name, hex) {
    if (!host._button) {
        host._quick = (host.dataset.quick || "").split(" ").filter(Boolean).map(q => el("button.quick", {
            type: "button", title: q, "aria-label": q, style: `background:${q}`, onclick: () => pickColour(host, name, q)}));
        host._button = el("button.colour", {type: "button", title: "Any colour", onclick: () => Buddy.pickColor({
            hex: OPT.colors[name], title: "Choose a colour", at: host._button.getBoundingClientRect(),
            onPick: value => pickColour(host, name, value),
        })}, [el("i"), el("span")]);
        host.replaceChildren(...host._quick, host._button);
    }
    for (const q of host._quick) q.setAttribute("aria-pressed", String(q.title.toUpperCase() === hex.toUpperCase()));
    host._button.firstChild.style.background = hex;
    host._button.lastChild.textContent = hex;
}

function pickColour(host, name, value) {
    OPT.colors[name] = value;
    colours(host, name, value);
    drawPreview();
    set(name, value);
}

on("options", o => {
    OPT = o;
    for (const host of $$("[data-slider]")) {
        const name = host.dataset.slider, spec = o.sliders[name];
        if (!host._input) buildSlider(host, name, spec);
        if (document.activeElement !== host._input) host._place(spec.value);
        host._show();
    }
    for (const host of $$("[data-color]")) colours(host, host.dataset.color, o.colors[host.dataset.color]);
    for (const input of $$("[data-toggle-input]")) input.checked = o.toggles[input.dataset.toggleInput];
    for (const group of $$("[data-toggle]")) group.classList.toggle("off", !o.toggles[group.dataset.toggle]);
    if (FONTS.length) $("font").value = o.font_display;

    segmented($("anim-direction"), o.anim_directions.map(d => [d, d.replace("From ", "")]), o.anim_direction, v => set("anim_direction", v));
    segmented($("anim-speed"), o.anim_speeds.map(s => [s, s]), o.anim_speed, v => set("anim_speed", v));
    $("direction-row").hidden = !o.anim_preset.startsWith("Slide");
    presetCards($("anim-presets"), ANIMATIONS.filter(a => o.anim_presets.includes(a.id)), o.anim_preset, v => set("anim_preset", v));
    presetCards($("presets"), LAYOUTS.filter(l => o.layout_presets.includes(l.id)), o.layout_preset, v => { set("layout_preset", v); });
    $("preset-note").textContent = (LAYOUTS.find(l => l.id === o.layout_preset) || {}).note || "";
    segmented($("wbw-lines"), [["auto", "Auto"], ["1", "1"], ["2", "2"], ["3", "3"]], o.wbw_lines, v => set("wbw_lines", v));
    if (document.activeElement !== $("wbw-scope")) $("wbw-scope").value = o.wbw_scope;
    $("wbw-note").textContent = o.wbw_scope === "timeline"
        ? "Each group of single-word clips that are on screen together, anywhere on the timeline. Auto uses the fewest lines, up to 3, that fit in 90% of the frame's width."
        : "The words selected in the preview, or every single-word clip under the playhead. Auto uses the fewest lines, up to 3, that fit in 90% of the frame's width.";
    drawScopes(o);
    drawLooks(o);
    drawPreview();
});

for (const input of $$("[data-toggle-input]")) {
    input.onchange = () => {
        const name = input.dataset.toggleInput;
        for (const group of $$(`[data-toggle="${name}"]`)) group.classList.toggle("off", !input.checked);
        // Switching a layer on shows its settings.
        const tab = input.closest("[data-layer]");
        if (tab && input.checked) showLayer(tab.dataset.layer);
        if (OPT) { OPT.toggles[name] = input.checked; drawPreview(); }
        set(name, input.checked);
    };
}

// Font Styling's layers - Text, Outline, Shadow, Box: one's settings at a time.
function showLayer(layer) {
    for (const tab of $$("[data-layer]")) {
        tab.classList.toggle("selected", tab.dataset.layer === layer);
        tab.querySelector("[role=tab]").setAttribute("aria-selected", String(tab.dataset.layer === layer));
    }
    for (const body of $$("[data-layer-body]")) body.hidden = body.dataset.layerBody !== layer;
}
for (const tab of $$("[data-layer]")) tab.querySelector("[role=tab]").onclick = () => showLayer(tab.dataset.layer);
showLayer("font");
$("reset-font").append(icon("refresh"));

// Each Text+ tab's "Apply to": which clips its Apply buttons change, and for Specific
// track, which one. Resolve can report selected clips (21.0.4 and later), not tracks.
const SCOPE_TRACKS = {style_scope: ["style-track", "style_track"], anim_scope: ["anim-track", "anim_track"],
                      layout_scope: ["layout-track", "layout_track"]};
function drawScopes(o) {
    for (const select of $$("[data-scope-select]")) {
        const name = select.dataset.scopeSelect, [trackId, trackName] = SCOPE_TRACKS[name];
        if (document.activeElement !== select) select.value = o[name];
        document.querySelector(`[data-scope-track="${name}"]`).hidden = o[name] !== "track";
        if (STATE) trackOptions($(trackId), STATE.tracks.video, o.tracks[trackName]);
    }
    // Apply position copies FROM the clips under the playhead: there's nothing to copy onto.
    const position = $("apply-position");
    position.disabled = o.layout_scope === "playhead";
    position.title = position.disabled ? "Copies from the clips under the playhead – choose another Apply to" : "";
}
for (const select of $$("[data-scope-select]")) select.onchange = () => set(select.dataset.scopeSelect, select.value);
for (const [trackId, trackName] of Object.values(SCOPE_TRACKS)) {
    $(trackId).onchange = e => set(trackName, Number(e.target.value));
}

function segmented(node, choices, value, onPick) {
    if (choices && node.children.length !== choices.length) {
        node.replaceChildren(...choices.map(([v, label]) => el("button", {type: "button", dataset: {value: v}, text: label})));
    }
    for (const b of node.children) {
        b.setAttribute("aria-pressed", String(b.dataset.value === value));
        b.onclick = () => onPick(b.dataset.value);
    }
}

const ANIMATIONS = [
    {id: "Pop (Scale)", title: "Pop", text: "Scales up from nothing with a little overshoot."},
    {id: "Bounce (Extra Rebound)", title: "Bounce", text: "Like Pop, with an extra rebound."},
    {id: "Fade (Opacity)", title: "Fade", text: "Fades in and out."},
    {id: "Slide (Direction + Fade)", title: "Slide", text: "Slides in from a side while fading."},
    {id: "Typewriter (Letters)", title: "Typewriter", text: "Types the text on, one letter at a time."},
    {id: "Letter Fade (Letters)", title: "Letter Fade", text: "Each letter fades in, one after another."},
    {id: "Letter Pop (Letters)", title: "Letter Pop", text: "Each letter pops up from nothing, one after another."},
];
const LAYOUTS = [
    {id: "Hero + Stack", title: "Hero + Stack", text: "One big word, the rest above and below it.",
     note: "Click a word in the preview to make it the hero – otherwise it's the middle word by timing."},
    {id: "Side-by-Side", title: "Side-by-Side", text: "Every word in one row, evenly spaced.",
     note: "Shrinks the row to fit the frame if it has to."},
    {id: "Grid", title: "Grid", text: "The words in a tidy grid.", note: ""},
];

function presetCards(node, list, value, onPick) {
    if (node.children.length !== list.length) {
        node.replaceChildren(...list.map(p => el("button.preset", {type: "button", dataset: {value: p.id}}, [
            el("b", {text: p.title}), el("span.muted.small", {text: p.text}),
        ])));
    }
    for (const b of node.children) {
        b.setAttribute("aria-pressed", String(b.dataset.value === value));
        b.onclick = () => onPick(b.dataset.value);
    }
}

// ------------------------------------------------------- style preview --

function rgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

// Text+ sizes a font so that its ascent + descent is this x Size x frame width - measured in
// Resolve 21.1 on 10 faces, 0.7987-0.8010 (text_animator/canvas_math.py TEXT_PLUS_HEIGHT).
const TEXT_PLUS_HEIGHT = 0.800;
const fontHeights = new Map();

/* A font's ascent + descent per pixel of CSS font-size - the browser's own metrics, the
   fallback when page.py hasn't sent Text+'s (font_css.height, from the font file Resolve
   uses): on Windows the browser can read a font's Windows metrics, which Text+ doesn't -
   Noto Sans JP's are 21% taller than its real ones. */
function fontHeightPerPx(css) {
    const font = `${css.italic ? "italic " : ""}${css.weight} 100px "${css.family}", sans-serif`;
    if (!fontHeights.has(font)) {
        const ctx = document.createElement("canvas").getContext("2d");
        ctx.font = font;
        const m = ctx.measureText("Hg");
        const h = (m.fontBoundingBoxAscent + m.fontBoundingBoxDescent) / 100;
        fontHeights.set(font, h > 0 ? h : 1.117);   // 1.117: Arial's, if a browser can't say
    }
    return fontHeights.get(font);
}

// A vertical (9:16) timeline gets the tall layout: the preview down the left.
const frameSize = () => (STATE && STATE.resolution) || [1920, 1080];
const isTall = () => frameSize()[1] > frameSize()[0];
// Behind the preview's text: a dark or a light stand-in picture, or the checkerboard.
let BACKDROP = "dark";

/* The preview box is the timeline's frame. Wide: as wide as the stage, no taller than about
   half the window. Tall: as tall as the window allows, up to 640 px. Returns [width, height]. */
function sizePreview(box) {
    const [rw, rh] = frameSize();
    let w;
    if (rh > rw) {
        w = Math.max(260, Math.min(640, innerHeight - 190)) * rw / rh;
    } else {
        w = Math.min($("style-stage").clientWidth || 600, Math.max(180, innerHeight * 0.48) * rw / rh);
    }
    box.style.width = `${w}px`;
    box.style.aspectRatio = `${rw} / ${rh}`;
    // Tall, the stage's column is the preview's width - its bar and note wrap to it.
    $("style-stage").style.width = rh > rw ? `${w}px` : "";
    return [w, w * rh / rw];
}

// The style controls' values now, by control name - a slider being dragged as it stands.
function currentValues() {
    const v = {...OPT.colors, ...OPT.toggles};
    for (const name of Object.keys(OPT.sliders)) {
        const host = document.querySelector(`[data-slider="${name}"]`);
        v[name] = host && host._input ? Number(host._input.value) : OPT.sliders[name].value;
    }
    return v;
}

/* Text dressed as a look - a CSS stand-in for Text+'s; Resolve draws the real one.
   v: the look's values by control name; w, h: the frame's size here; fs: the font size. */
function dressText(span, v, css, w, h, fs) {
    span.style.fontFamily = `"${css.family}", sans-serif`;
    span.style.fontWeight = css.weight;
    span.style.fontStyle = css.italic ? "italic" : "normal";
    span.style.fontSize = `${fs}px`;
    span.style.color = v.font_color;
    span.style.webkitTextStroke = v.outline_on
        ? `${Math.max(0.5, v.outline_thickness * fs * 2)}px ${rgba(v.outline_color, v.outline_opacity)}` : "0";
    span.style.paintOrder = "stroke fill";
    span.style.textShadow = v.shadow_on
        // Offsets are fractions of the frame width, like Size.
        ? `${v.shadow_offset_x * w}px ${-v.shadow_offset_y * w}px ${v.shadow_blur * w / 400}px ${rgba(v.shadow_color, v.shadow_opacity)}` : "none";
    if (v.background_on) {
        const ph = Math.max(2, w * 0.017 + v.background_extend_horizontal * w);
        const pv = Math.max(1, h * 0.02 + v.background_extend_vertical * h);
        span.style.background = rgba(v.background_color, v.background_opacity);
        span.style.padding = `${pv}px ${ph}px`;
        span.style.borderRadius = `${v.background_corner_radius * fs}px`;
    } else {
        span.style.background = "none";
        span.style.padding = "0";
        span.style.borderRadius = "0";
    }
}

function drawPreview() {
    if (!OPT) return;
    const box = $("preview"), sample = $("sample");
    $("style-layout").classList.toggle("tall", isTall());
    box.className = `preview ${BACKDROP}`;
    const [w, h] = sizePreview(box);
    const v = currentValues();
    // Size is a fraction of frame width; this box stands in for the frame.
    const fs = Math.max(8, v.font_size * w * TEXT_PLUS_HEIGHT / (OPT.font_css.height || fontHeightPerPx(OPT.font_css)));
    // A clip's own text is the user's; only the stand-in is translated.
    const userSample = (STATE && STATE.sample) || "";
    sample.translate = !userSample;
    sample.textContent = userSample || "Sample Text";
    sample.style.lineHeight = OPT.font_css.line ? String(OPT.font_css.line) : "normal";   // Text+'s line step
    dressText(sample, v, OPT.font_css, w, h, fs);
    drawSafe(w, h);
}

// Where a phone app's own buttons and caption go, shaded over a tall preview (the placement
// canvases' Safe zone setting - one choice for every tab).
function drawSafe(w, h) {
    const svg = $("safe-shade");
    $("safe-pick").hidden = !isTall() || !OVERLAY;
    const rects = isTall() && OVERLAY && OVERLAY.safe_rects[OVERLAY.safe_type];
    if (!rects) { svg.replaceChildren(); return; }
    const ns = "http://www.w3.org/2000/svg";
    const path = (d, attrs) => {
        const node = document.createElementNS(ns, "path");
        node.setAttribute("d", d);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        return node;
    };
    const holes = rects.map(([l, t, r, b]) => `M${l * w} ${t * h}V${b * h}H${r * w}V${t * h}Z`).join("");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.replaceChildren(
        path(`M0 0H${w}V${h}H0Z${holes}`, {"fill-rule": "evenodd", fill: rgba(OVERLAY.safe_color, OVERLAY.safe_opacity * 0.75)}),
        path(holes, {fill: "none", stroke: "rgba(255,255,255,.6)", "stroke-dasharray": "4 3"}),
    );
}

$("style-safe").onchange = e => send("overlay", {name: "safe_type", value: e.target.value});
function drawSafePick() {
    const select = $("style-safe");
    if (select.options.length !== OVERLAY.safe_types.length) {
        select.replaceChildren(...OVERLAY.safe_types.map(t => el("option", {value: t, text: t === "None" ? "Off" : t})));
    }
    select.value = OVERLAY.safe_type;
}

function drawBackdrop() {
    segmented($("backdrop"), null, BACKDROP, v => { BACKDROP = v; drawBackdrop(); if (OPT) { drawLooks(OPT); drawPreview(); } });
}
drawBackdrop();

// The card's own width changes it, and in a tall layout the window's height too.
let stageWidth = 0;
new ResizeObserver(() => {
    const w = $("style-stage").clientWidth;
    if (w === stageWidth) return;
    stageWidth = w;
    drawPreview();
}).observe($("style-stage"));
addEventListener("resize", () => { if (isTall()) drawPreview(); });

// --------------------------------------------------------------- looks --

/* The ready-made looks for this timeline's shape, each a card drawn with its own colours over
   the controls' other values; the one picked last, and whether it's been changed since. */
function drawLooks(o) {
    const L = o.looks, tall = isTall(), kind = tall ? "tall" : "wide", cur = L.current;
    const base = currentValues();
    $("looks-hint").textContent = tall ? "Made for vertical video – start from one, then adjust it below"
                                       : "Start from one, then adjust it below";
    $("looks").replaceChildren(...L[kind].map(look => {
        const span = el("span", {text: tall ? "Big text" : "Subtitle"});
        const thumb = el(`div.look-thumb.${BACKDROP === "light" ? "light" : "dark"}`, {}, [span]);
        const css = look.bold ? {...o.font_css, weight: Math.max(o.font_css.weight, 800)} : o.font_css;
        const v = {...base, ...look.values}, w = tall ? 90 : 160, h = tall ? 160 : 90;
        dressText(span, v, css, w, h, tall ? 15 : 13);
        return el("button.look", {
            type: "button", "aria-pressed": String(!!cur && cur[0] === kind && cur[1] === look.name),
            onclick: () => send("look", {kind, name: look.name}),
        }, [thumb, el("span.look-name", {text: look.name})]);
    }));

    const now = $("look-now"), update = $("update-look");
    update.hidden = true;
    if (!cur) {
        now.replaceChildren("Your own settings – Save look keeps them to use again");
    } else {
        const name = el("b", {text: cur[1], translate: cur[0] === "saved" ? "no" : undefined});
        now.replaceChildren(...(L.changed ? ["Changed from ", name] : ["Using ", name]));
        if (cur[0] === "saved" && L.changed) {
            update.hidden = false;
            update.translate = false;
            update.textContent = `Update "${cur[1]}"`;
            update.onclick = () => send("save_look", {name: cur[1]});
        }
    }
    $("look-tag").textContent = !cur ? "" : L.changed ? `${cur[1]} – changed` : cur[1];
}

$("saved-looks").append(icon("bookmark"));
$("saved-looks").onclick = () => {
    const L = OPT.looks, cur = L.current, at = $("saved-looks").getBoundingClientRect();
    const items = [{heading: "Your saved looks"}];
    if (!L.saved.length) items.push({label: "None yet – Save look keeps the one on screen", disabled: true});
    for (const look of L.saved) {
        items.push({label: `${look.name}   ${look.tall ? "9:16" : "16:9"}`, raw: true, swatch: look.look.values.font_color,
                    checked: !!cur && cur[0] === "saved" && cur[1] === look.name,
                    onClick: () => send("look", {kind: "saved", name: look.name})});
    }
    items.push({sep: true}, {label: "Save current look…", onClick: () => askName()});
    if (cur && cur[0] === "saved") {
        items.push({label: `Rename "${cur[1]}"…`, raw: true, onClick: () => askName(cur[1])},
                   {label: `Delete "${cur[1]}"`, raw: true, danger: true, onClick: () => deleteLook(cur[1])});
    }
    Buddy.menu({x: at.left, y: at.bottom + 4, items});
};
$("save-look").onclick = () => askName();

/* Asks a name to save the look on screen as - or, renaming, a saved look's new name. */
function askName(renaming) {
    const saved = OPT.looks.saved;
    const input = el("input.field.name-field", {maxlength: 40, spellcheck: "false", translate: "no",
                                                 value: renaming || "", placeholder: "Reels yellow"});
    const error = el("p.field-error");
    const note = el("p.muted.small.name-note");
    const check = () => {
        error.textContent = "";
        input.classList.remove("invalid");
        const name = input.value.trim().toLowerCase();
        const same = !renaming && saved.find(look => look.name.toLowerCase() === name);
        note.textContent = same ? `Replaces your saved look "${same.name}".` : "Kept in Buddy, so it's there in every project.";
    };
    const submit = close => {
        const name = input.value.trim();
        if (!name) { error.textContent = "Enter a name first"; input.classList.add("invalid"); input.focus(); return; }
        if (!renaming) send("save_look", {name});
        else if (name !== renaming) send("rename_look", {name: renaming, to: name});
        close();
    };
    input.oninput = check;
    check();
    const dialog = Buddy.modal({title: renaming ? "Rename look" : "Save look", body: [input, error, note], buttons: [
        {label: "Cancel"}, {label: renaming ? "Rename" : "Save", kind: "accent", onClick: submit}]});
    input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); submit(dialog.close); } });
}

async function deleteLook(name) {
    const yes = await Buddy.confirm({title: "Delete this look?", danger: true, ok: "Delete",
        text: `"${name}" goes from Saved looks. Clips already styled with it keep their style.`});
    if (yes) send("delete_look", {name});
}

// ----------------------------------------------------------- canvases --

const canvases = {
    layout: PlacementCanvas($("canvas-layout"), {tab: "layout", multi: false, send}),
    wordbyword: PlacementCanvas($("canvas-wordbyword"), {tab: "wordbyword", multi: true, send}),
    words: PlacementCanvas($("canvas-words"), {tab: "words", multi: true, send}),
};
on("canvas", c => canvases[c.tab] && canvases[c.tab].update(c));

let OVERLAY = null;
on("overlay", o => {
    OVERLAY = o;
    for (const c of Object.values(canvases)) c.setOverlay(o);
    if (!$("guides-pop").hidden) drawGuides();
    drawSafePick();
    drawPreview();
});

$("apply-layout").onclick = () => send("apply_layout", {preset: OPT.layout_preset, selected: canvases.words.selected()});
$("wbw-scope").onchange = e => set("wbw_scope", e.target.value);
$("space-words").onclick = () => send("space_words", {selected: canvases.wordbyword.selected()});

on("history", h => {
    for (const b of $$("[data-undo]")) { b.disabled = !h.undo; b.title = !h.undo ? "Nothing to undo" : Buddy.keys(h.undo === 1 ? "Undo (Ctrl+Z) - 1 step" : `Undo (Ctrl+Z) - ${h.undo} steps`); }
    for (const b of $$("[data-redo]")) { b.disabled = !h.redo; b.title = h.redo ? Buddy.keys("Redo (Ctrl+Shift+Z)") : "Nothing to redo"; }
});

// Guides & snapping settings, under the canvas's button.
let guidesAnchor = null;
for (const b of $$("[data-guides]")) {
    b.onclick = e => {
        e.stopPropagation();
        if (!$("guides-pop").hidden && guidesAnchor === b) return closeGuides();
        guidesAnchor = b;
        drawGuides();
        const pop = $("guides-pop");
        pop.hidden = false;
        const r = b.getBoundingClientRect();
        pop.style.top = `${Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8)}px`;
        pop.style.left = `${Math.max(8, r.right - pop.offsetWidth)}px`;
    };
}
function closeGuides() { $("guides-pop").hidden = true; }
document.addEventListener("mousedown", e => {
    if (!$("guides-pop").hidden && !e.target.closest("#guides-pop, [data-guides], .picker")) closeGuides();
});

function drawGuides() {
    const o = OVERLAY;
    if (!o) return;
    const put = (name, value) => send("overlay", {name, value});
    const select = (name, options) => {
        const s = el("select.field", {onchange: e => put(name, e.target.value)}, options.map(v => el("option", {value: v, text: v})));
        s.value = o[name];
        return s;
    };
    const range = (name, min, max, step, value, unit, decimals) => {
        const out = el("b.val", {text: shown(unit, value)});
        const input = el("input", {type: "range", min, max, step});
        place(input, value, step);
        input.addEventListener("input", () => { out.textContent = shown(unit, Number(input.value)); });
        input.addEventListener("change", () => put(name, Number(input.value)));
        typeIn(out, input, unit, decimals, v => { place(input, v, step); out.textContent = shown(unit, v); put(name, v); });
        return [input, out];
    };
    const colour = name => {
        const b = el("button.colour", {type: "button", onclick: () => Buddy.pickColor({
            hex: o[name], at: b.getBoundingClientRect(), onPick: v => put(name, v),
        })}, [el("i", {style: `background:${o[name]}`}), el("span", {text: o[name]})]);
        return b;
    };
    const check = (name, label, disabled) => el("label.check", {}, [
        el("input", {type: "checkbox", checked: o[name], disabled, onchange: e => put(name, e.target.checked)}), label]);
    const res = STATE ? STATE.resolution[0] : 1920;
    const spacing = o.grid_spacing || o.default_spacing;
    const [spaceInput, spaceOut] = range("grid_spacing", 1 / 64, 1 / 4, 1 / 640, spacing,
                                         {scale: () => res, digits: 0, suffix: " px"}, 6);
    spaceInput.addEventListener("dblclick", () => put("grid_spacing", 0));
    spaceInput.title = "Double-click for the standard spacing for this resolution";
    $("guides-pop").replaceChildren(
        el("div.pop-section", {}, [
            el("b", {text: "Grid"}),
            el("label.lbl", {}, ["Type", select("grid_type", o.grid_types)]),
            o.grid_type === "Standard" ? el("label.lbl", {}, [el("span.row", {}, ["Spacing", el("span.spacer"), spaceOut]), spaceInput]) : null,
            el("div.row", {}, [el("span.small.muted", {text: "Colour"}), colour("grid_color")]),
            el("label.lbl", {}, (([i, v]) => [el("span.row", {}, ["Opacity", el("span.spacer"), v]), i])(range("grid_opacity", 0, 1, 0.01, o.grid_opacity, PCT, 2))),
        ]),
        el("div.pop-section", {}, [
            el("b", {text: "Safe zone"}),
            el("label.lbl", {}, ["Platform", select("safe_type", o.safe_types)]),
            el("div.row", {}, [el("span.small.muted", {text: "Colour"}), colour("safe_color")]),
            el("label.lbl", {}, (([i, v]) => [el("span.row", {}, ["Opacity", el("span.spacer"), v]), i])(range("safe_opacity", 0, 1, 0.01, o.safe_opacity, PCT, 2))),
        ]),
        el("div.pop-section", {}, [
            el("b", {text: "Snapping"}),
            check("snap", "Snap to the frame centre"),
            check("snap_elements", "Snap to other text", !o.snap),
            check("snap_safe", "Snap to safe-zone edges", !o.snap),
        ]),
    );
}

// ---------------------------------------------------------- animation --

$("remove-anims").onclick = async () => {
    const n = OPT.tracks.anim_track;
    // Only this tab's own. Not "any other animation stays": on a clip an earlier Buddy animated there's
    // no note of what was Buddy's, and a hand-keyed fade-in from nothing looks the same as a preset's.
    const stays = "Animate by, the highlight and anything Buddy can tell isn't its own stay.";
    const text = {
        selected: `Removes the animations this tab put on the selected Text+ clips. ${stays}`,
        playhead: `Removes the animations this tab put on the Text+ clips under the playhead. ${stays}`,
        track: `Removes the animations this tab put on the Text+ clips on video track ${n}. ${stays}`,
    }[OPT.anim_scope] || `Removes the animations this tab put on every Text+ clip on the timeline. ${stays}`;
    const yes = await Buddy.confirm({title: "Remove animations?", danger: true, ok: "Remove", text});
    if (yes) send("remove_animations");
};
})();
