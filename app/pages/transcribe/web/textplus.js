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
// Whether the style on the controls is on the clips: null until something says, false once a
// control changes, true after an Apply style that went through.
let APPLIED = null;
const STYLE_CONTROL = /^(font|outline|shadow|background)_/;
function set(name, value) {
    if (STYLE_CONTROL.test(name)) styleChanged(false);
    else if (name.startsWith("style_") && APPLIED) styleChanged(null);     // other clips now
    send("set", {name, value});
}
function styleChanged(applied) {
    APPLIED = applied;
    const state = $("style-state");
    state.hidden = applied === null;
    state.classList.toggle("done", !!applied);
    state.replaceChildren(applied ? icon("check") : el("i"), applied ? "Applied in Resolve" : "Not in Resolve yet");
}
on("styled", () => styleChanged(true));

// ---------------------------------------------------------------- font --

// Every face Resolve can draw, as [family, style]: the picker lists the families, each in its
// own letters, and the dropdown beside it the picked family's styles.
let FACES = [];
on("fonts", list => { FACES = list; drawFont(); });

const stylesOf = family => FACES.filter(f => f[0] === family).map(f => f[1])
    .sort((a, b) => (b === "Regular") - (a === "Regular") || a.localeCompare(b));
const inItsOwnLetters = (node, family) => { node.style.fontFamily = `"${family.replace(/["\\]/g, "")}", var(--font)`; };

function drawFont() {
    if (!OPT) return;
    const button = $("font-family"), select = $("font-style");
    button.textContent = OPT.font_name;
    inItsOwnLetters(button, OPT.font_name);
    const styles = stylesOf(OPT.font_name);
    if (!styles.includes(OPT.font_style)) styles.unshift(OPT.font_style);     // not one of Resolve's (yet)
    if (styles.join("\n") !== [...select.options].map(o => o.value).join("\n")) {
        select.replaceChildren(...styles.map(s => el("option", {value: s, text: s})));
    }
    if (document.activeElement !== select) select.value = OPT.font_style;
}
$("font-style").onchange = e => set("font_face", [OPT.font_name, e.target.value]);

// A family picked: in the style it's in now if the family has it, else its Regular, else its first.
function pickFamily(family) {
    const styles = stylesOf(family);
    const style = styles.includes(OPT.font_style) ? OPT.font_style : styles[0] || "Regular";
    closeFonts();
    $("font-family").focus();
    if (family !== OPT.font_name) set("font_face", [family, style]);
}

// The font list, under its button: type to narrow it, Up/Down and Enter to pick, Esc to close.
let fontPop = null;
function closeFonts() {
    if (!fontPop) return;
    fontPop.node.remove();
    fontPop = null;
    $("font-family").setAttribute("aria-expanded", "false");
}
function openFonts() {
    if (fontPop) return closeFonts();
    const button = $("font-family");
    const families = [...new Set(FACES.map(f => f[0]))];
    const rows = families.map(family => {
        const row = el("button.font-opt", {type: "button", role: "option", text: family,
                                           "aria-selected": String(family === OPT.font_name), onclick: () => pickFamily(family)});
        inItsOwnLetters(row, family);
        return row;
    });
    const none = el("p.muted.small.font-none", {text: "No font by that name", hidden: true});
    const list = el("div.font-list", {role: "listbox", translate: "no", "aria-label": "Font"}, rows);
    const search = el("input.field", {type: "text", placeholder: "Search fonts", "aria-label": "Search fonts", spellcheck: "false"});
    const node = el("div.font-pop", {}, [search, list, none]);
    const shownRows = () => rows.filter(r => !r.hidden);
    const activate = row => {
        for (const r of rows) r.classList.toggle("active", r === row);
        if (row) row.scrollIntoView({block: "nearest"});
    };
    search.oninput = () => {
        const q = search.value.trim().toLowerCase();
        for (const r of rows) r.hidden = !r.textContent.toLowerCase().includes(q);
        none.hidden = shownRows().length > 0;
        activate(q ? shownRows()[0] : null);
    };
    node.addEventListener("keydown", e => {
        e.stopPropagation();
        const visible = shownRows(), at = visible.findIndex(r => r.classList.contains("active"));
        if (e.key === "Escape") { closeFonts(); button.focus(); }
        else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            const start = at >= 0 ? at : visible.findIndex(r => r.getAttribute("aria-selected") === "true");
            activate(visible[Math.max(0, Math.min(visible.length - 1, start + (e.key === "ArrowDown" ? 1 : -1)))]);
        } else if (e.key === "Enter" && visible[at]) { e.preventDefault(); visible[at].click(); }
    });
    document.body.append(node);
    const r = button.getBoundingClientRect();
    const width = Math.max(r.width, 260);
    node.style.width = `${width}px`;
    node.style.left = `${Math.max(6, Math.min(r.left, innerWidth - width - 6))}px`;
    const below = innerHeight - r.bottom - 12, above = r.top - 12, up = below < 220 && above > below;
    list.style.maxHeight = `${Math.max(120, Math.min(340, (up ? above : below) - 52))}px`;
    if (up) node.style.bottom = `${innerHeight - r.top + 4}px`;
    else node.style.top = `${r.bottom + 4}px`;
    fontPop = {node};
    button.setAttribute("aria-expanded", "true");
    const current = rows.find(row => row.getAttribute("aria-selected") === "true");
    if (current) current.scrollIntoView({block: "center"});
    search.focus();
}
$("font-family").onclick = openFonts;
document.addEventListener("mousedown", e => {
    if (fontPop && !fontPop.node.contains(e.target) && !e.target.closest("#font-family")) closeFonts();
}, true);
addEventListener("resize", closeFonts);
document.addEventListener("buddy-tab", closeFonts);

// ------------------------------------------------------------- sliders --

// How a slider's value reads, in place of Text+'s own fractions. Size is the text's height in
// pixels on this timeline's frame (Text+ makes ascent + descent TEXT_PLUS_HEIGHT x Size x the
// frame's width); a shadow's offsets and a box's padding are pixels of the frame too (Offset Y
// counted downwards, where Text+'s goes up); an outline's thickness is a share of the text's
// size; opacity and rounding are a %. scale: shown = value x scale; it's typed in the same units.
const frame = () => (STATE && STATE.resolution) || [1920, 1080];
const frameWidth = () => frame()[0];
const PCT = {scale: () => 100, digits: 0, suffix: "%"};
const ACROSS = {scale: () => frame()[0], digits: 0, suffix: " px"};
const UNITS = {
    font_size: {scale: () => TEXT_PLUS_HEIGHT * frameWidth(), digits: 0, suffix: " px"},
    outline_thickness: {scale: () => 100, digits: 1, suffix: "%"},
    outline_opacity: PCT, shadow_opacity: PCT, background_opacity: PCT, background_corner_radius: PCT,
    shadow_blur: {scale: () => 1, digits: 1, suffix: ""},
    shadow_offset_x: ACROSS,
    shadow_offset_y: {scale: () => -frame()[0], digits: 0, suffix: " px"},
    background_extend_horizontal: ACROSS,
    background_extend_vertical: {scale: () => frame()[1], digits: 0, suffix: " px"},
};
function shown(unit, v) {
    const text = (v * unit.scale()).toFixed(unit.digits);
    return `${Number(text) === 0 ? text.replace("-", "") : text}${unit.suffix}`;
}

// The part of a setting's range its slider covers, and its step: [from, to, step]. Text+'s own
// ranges run far past anything a subtitle uses, which left the useful values within a few
// pixels of one end. A value outside it - typed, or from a look - widens the slider to hold it.
const REACH = {
    font_size: [0.01, 0.3, 0.005],
    outline_thickness: [0, 0.2, 0.005],
    shadow_offset_x: [-0.03, 0.03, 0.001],
    shadow_offset_y: [-0.03, 0.03, 0.001],
    shadow_blur: [0, 30, 0.5],
    background_extend_horizontal: [-0.02, 0.1, 0.001],
    background_extend_vertical: [-0.02, 0.1, 0.001],
};

// A value a slider's steps don't land on (a typed one) is kept as it is, not snapped to a step,
// and one past either end moves that end.
function place(input, v, step) {
    if (v < Number(input.min)) input.min = v;
    if (v > Number(input.max)) input.max = v;
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
            // Between the setting's own ends (data-lo, data-hi), which a slider may not reach.
            const lo = Number(input.dataset.lo || input.min), hi = Number(input.dataset.hi || input.max);
            put(Math.min(hi, Math.max(lo, +(n / unit.scale()).toFixed(decimals))));
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
    const [from, to, step] = REACH[name] || [spec.min, spec.max, spec.step];
    const input = el("input", {type: "range", min: from, max: to, step, "aria-label": spec.label,
                               dataset: {lo: spec.min, hi: spec.max}, title: `Double-click to reset to ${spec.default}`});
    const value = el("b.val");
    const unit = UNITS[name] || {scale: () => 1, digits: spec.decimals, suffix: ""};
    spec = {...spec, step};
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
    // data-compact: the name and the value alone (a shadow's offsets, set on the pad beside them).
    host.replaceChildren(el(`label.slider${"compact" in host.dataset ? ".compact" : ""}`, {},
                            [el("span", {text: spec.label}), input, value]));
    host._input = input;
    host._show = show;
    host._place = v => place(input, v, spec.step);
    host._put = put;
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
    drawFont();

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

/* Font Styling's four parts - Text, Outline, Shadow, Box - one under the other, each with its
   switch and, beside its name, what it's set to. A part that's on shows its settings, unless
   it's been folded shut; one that's off is a single row, and clicking it switches it on. */
const PARTS = {font: "font_on", outline: "outline_on", shadow: "shadow_on", background: "background_on"};
const folded = new Set();

function switchPart(part, on) {
    const name = PARTS[part];
    if (on) folded.delete(part);
    if (OPT) { OPT.toggles[name] = on; drawPreview(); }
    set(name, on);
}

// What a part is set to, for its row: a few words and its colour.
function partSummary(part, v) {
    const words = text => el("span", {text, translate: "no"});
    const dot = hex => el("i.part-dot", {style: `background:${hex}`});
    if (part === "font") return [words(`${OPT.font_display} · ${shown(UNITS.font_size, v.font_size)}`), dot(v.font_color)];
    if (part === "outline") return [words(shown(UNITS.outline_thickness, v.outline_thickness)), dot(v.outline_color)];
    if (part === "shadow") return [dot(v.shadow_color)];
    return [words(shown(PCT, v.background_opacity)), dot(v.background_color)];
}

function drawParts() {
    if (!OPT) return;
    const v = currentValues();
    for (const node of $$("[data-part]")) {
        const part = node.dataset.part, on = !!OPT.toggles[PARTS[part]], open = on && !folded.has(part);
        node.classList.toggle("off", !on);
        node.querySelector(".part-body").hidden = !open;
        node.querySelector(".part-open").setAttribute("aria-expanded", String(open));
        node.querySelector(".part-summary").replaceChildren(
            ...(on ? partSummary(part, v) : [part === "font" ? "Clips keep their own" : "Off"]));
    }
}

for (const node of $$("[data-part]")) {
    const part = node.dataset.part, input = node.querySelector("[data-toggle-input]");
    node.querySelector(".part-chevron").append(icon("right"));
    input.onchange = () => switchPart(part, input.checked);
    node.querySelector(".part-open").onclick = () => {
        if (!OPT) return;
        if (!OPT.toggles[PARTS[part]]) { input.checked = true; return switchPart(part, true); }
        if (!folded.delete(part)) folded.add(part);
        drawParts();
    };
}
$("reset-font").append(icon("refresh"));

/* A shadow's position: a dot to drag about a square, its middle the text itself - no offset, a
   glow. The square reaches PAD_REACH of the frame's width each way, or as far as the shadow
   already is. The offsets themselves are the two values beside it (typed with a double-click). */
const PAD_REACH = 0.03;
const pad = $("shadow-pad");
let padHeld = null;         // while the dot is dragged: the reach it started with
const offsetHost = axis => document.querySelector(`[data-slider="shadow_offset_${axis}"]`);
const offsetNow = axis => (offsetHost(axis)._input ? Number(offsetHost(axis)._input.value) : 0);

function drawPad() {
    const x = offsetNow("x"), y = offsetNow("y");
    const reach = padHeld || Math.max(PAD_REACH, Math.abs(x), Math.abs(y));
    pad.firstElementChild.style.left = `${50 + 42 * x / reach}%`;
    pad.firstElementChild.style.top = `${50 - 42 * y / reach}%`;       // Text+'s Offset Y goes up
}

// The offsets while the dot moves: on the page only, until keep says the move is over.
function moveShadow(x, y, keep) {
    for (const [axis, v] of [["x", x], ["y", y]]) {
        const host = offsetHost(axis);
        if (!host._input) return;
        const value = Math.max(-1, Math.min(1, +v.toFixed(3)));
        if (keep) { if (value !== OPT.sliders[`shadow_offset_${axis}`].value) host._put(value); }
        else { host._place(value); host._show(); }
    }
    drawPreview();
}

function padPoint(e) {
    const r = pad.getBoundingClientRect();
    const fx = Math.max(-1, Math.min(1, (e.clientX - r.left - r.width / 2) / (r.width * 0.42)));
    const fy = Math.max(-1, Math.min(1, (e.clientY - r.top - r.height / 2) / (r.height * 0.42)));
    const snap = Math.hypot(fx, fy) < 0.1;          // near the middle is the middle
    return snap ? [0, 0] : [fx * padHeld, -fy * padHeld];
}
pad.addEventListener("pointerdown", e => {
    if (e.button !== 0 || !OPT) return;
    padHeld = Math.max(PAD_REACH, Math.abs(offsetNow("x")), Math.abs(offsetNow("y")));
    pad.setPointerCapture(e.pointerId);
    moveShadow(...padPoint(e), false);
});
pad.addEventListener("pointermove", e => { if (padHeld) moveShadow(...padPoint(e), false); });
for (const type of ["pointerup", "pointercancel"]) {
    pad.addEventListener(type, () => {
        if (!padHeld) return;
        padHeld = null;
        moveShadow(offsetNow("x"), offsetNow("y"), true);
    });
}
pad.addEventListener("dblclick", () => { if (OPT) moveShadow(0, 0, true); });
pad.addEventListener("keydown", e => {
    const step = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]}[e.key];
    if (!step || !OPT) return;
    e.preventDefault();
    const by = e.shiftKey ? 0.005 : 0.001;
    moveShadow(offsetNow("x") + step[0] * by, offsetNow("y") + step[1] * by, true);
});

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
    drawParts();
    drawPad();
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

/* The looks, in one strip: the ready-made ones for this timeline's shape, then the saved ones,
   then a tile that saves what's on the controls. Each is a card drawn with its own colours over
   the controls' other values (a saved one in its own font too). Above them: the look picked
   last, whether it's been changed since, and - if it has - putting it back, or keeping the
   changes in it. */
function lookTile(kind, look, values, css, o) {
    const tall = isTall(), cur = o.looks.current;
    const span = el("span", {text: tall ? "Big text" : "Subtitle"});
    const thumb = el(`div.look-thumb.${BACKDROP === "light" ? "light" : "dark"}`, {}, [span]);
    dressText(span, {...currentValues(), ...values}, css, tall ? 90 : 160, tall ? 160 : 90, tall ? 15 : 13);
    return el("button.look", {
        type: "button", "aria-pressed": String(!!cur && cur[0] === kind && cur[1] === look.name),
        onclick: () => { styleChanged(false); send("look", {kind, name: look.name}); },
    }, [thumb, el("span.look-name", {text: look.name, translate: kind === "saved" ? "no" : undefined})]);
}

function savedLookMenu(name, x, y) {
    Buddy.menu({x, y, items: [
        {label: "Rename…", onClick: () => askName(name)},
        {label: "Delete", danger: true, onClick: () => deleteLook(name)},
    ]});
}

function drawLooks(o) {
    const L = o.looks, tall = isTall(), kind = tall ? "tall" : "wide", cur = L.current;
    $("looks").replaceChildren(...L[kind].map(look => {
        const css = look.bold ? {...o.font_css, weight: Math.max(o.font_css.weight, 800)} : o.font_css;
        return lookTile(kind, look, look.values, css, o);
    }));

    const add = el("button.look.look-new", {type: "button", onclick: () => askName(),
                                            title: "Keeps what's on the controls, font included, to use again in any project"},
                   [el("div.look-thumb", {}, [icon("plus")]), el("span.look-name", {text: "Save this look"})]);
    $("saved-looks").replaceChildren(...L.saved.map(look => {
        const tile = lookTile("saved", look, look.look.values, look.css || o.font_css, o);
        // Made for the other shape of timeline: it says so.
        if (look.tall !== tall) tile.lastChild.append(el("span.muted", {text: look.tall ? " 9:16" : " 16:9"}));
        const more = el("button.btn.icon.look-more", {type: "button", title: "Rename or delete", "aria-haspopup": "menu", onclick: () => {
            const r = more.getBoundingClientRect();
            savedLookMenu(look.name, r.left, r.bottom + 4);
        }}, [icon("more")]);
        return el("div.look-wrap", {oncontextmenu: e => { e.preventDefault(); savedLookMenu(look.name, e.clientX, e.clientY); }},
                  [tile, more]);
    }), add);

    const now = $("look-now"), update = $("update-look"), revert = $("revert-look");
    const changed = !!cur && L.changed;
    $("look-edited").hidden = !changed;
    revert.hidden = !changed;
    update.hidden = !(changed && cur[0] === "saved");
    if (!cur) {
        now.replaceChildren("Your own settings");
    } else {
        now.replaceChildren(el("b", {text: cur[1], translate: cur[0] === "saved" ? "no" : undefined}));
        revert.onclick = () => { styleChanged(false); send("look", {kind: cur[0], name: cur[1]}); };
        if (!update.hidden) {
            update.translate = false;
            update.textContent = `Update "${cur[1]}"`;
            update.onclick = () => send("save_look", {name: cur[1]});
        }
    }
}

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
