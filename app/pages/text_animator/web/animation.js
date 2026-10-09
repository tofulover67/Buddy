/*
 * Animation's view. Tabs: the kind of animation. Previews: every motion
 * preset page.py sends, playing in a little viewer, grouped by pack; a tile
 * chooses the preset, and Apply puts it on the clips selected in Resolve.
 * What moves in the viewers follows the selection (page.py's "shape").
 * The search and the pack/kind filters only narrow what's drawn here.
 *
 * Favorites is the same panel narrowed to the hearted presets. Editor opens
 * a preset as keys (page.py sends them on "keys"; a saved preset has its own)
 * on a spline editor, beside a viewer that plays exactly what's drawn: the
 * same Bezier curves motion.py lays on the clip. The edit can go on the
 * selected clips as it is, or be saved as a preset of the person's own.
 *
 * Presets: the word highlight for Text+ captions (highlight.py) - its
 * choices, a caption playing them, and Apply / Remove for the selected
 * Text+ clips (page.py's "hl" and "hl_selection").
 *
 * Zoom: the one clip selected, playing here with its zooms drawn as Resolve
 * will render them, and a track to drag them about on (zoom.py).
 */
"use strict";

const $ = id => document.getElementById(id);
const {el, send} = Buddy;
const SVG = "http://www.w3.org/2000/svg";
const LAST = 40;            // samples 0..40
const PAUSE = 0.6;          // seconds at rest before a preset plays again
const SAVED = "Saved";      // motion.SAVED's pack: the person's own presets

let presets = [], packs = [], kinds = [];
let state = {tab: "previews", chosen: "", way: "both", speed: 1, direction: "own", play: "all", shape: "card",
             unit: "clip", order: "forward", stagger: 0.08,
             favorites: [], connected: false, selection: null, problem: "", working: ""};
let favorites = new Set();
const view = {q: "", pack: "all", kind: "all"};
const tiles = [];           // {p, node, obj, head, offset, hoverAt, visible, parts, stagger}
let aimed = "own";          // the Direction the presets' curves are turned for (aim)
let big = null;             // the chosen preset's viewer

// ------------------------------------------------------------------- tabs

function showTab(tab) {
    for (const b of document.querySelectorAll(".tabs [data-tab]")) {
        b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    }
    for (const panel of document.querySelectorAll(".panel")) {
        const tabs = (panel.dataset.tabs || panel.id.replace("panel-", "")).split(" ");
        panel.hidden = !tabs.includes(tab);
    }
}

function goTab(tab) {
    const was = state.tab;
    state.tab = tab;
    showTab(tab);
    if ((was === "favorites") !== (tab === "favorites")) drawGroups();
    if (tab === "editor") editorShown();
    send("tab", {tab});
}

for (const b of document.querySelectorAll(".tabs [data-tab]")) {
    b.onclick = () => goTab(b.dataset.tab);
}

// ----------------------------------------------------------------- shapes

function svg(tag, attrs = {}, kids = []) {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    for (const c of kids) n.appendChild(c);
    return n;
}

const GLYPHS = {
    heart: '<path d="M12 20.3s-7.4-4.5-9.3-9C1.3 7.8 3.4 4.6 6.8 4.6c2.1 0 3.9 1.2 5.2 3 1.3-1.8 3.1-3 5.2-3 3.4 0 5.5 3.2 4.1 6.7-1.9 4.5-9.3 9-9.3 9z"/>',
    edit: '<path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M14 7l3 3"/>',
    chevron: '<path d="M9 6l6 6-6 6"/>',
};

function glyph(name) {
    const node = svg("svg", {viewBox: "0 0 24 24", class: "icon-svg", "aria-hidden": "true"});
    node.innerHTML = GLYPHS[name];
    return node;
}

/* A rounded rectangle, or a dot, as a path. Drawn as <rect rx> or <circle>,
   AMD's integrated graphics under Buddy's GPU settings bit a corner off when
   it turned (Turn, Spin, Roll) - and off the ends of the little lines even
   when it didn't; the same outline as a path, starting mid-edge, is whole. */
function rounded({x, y, width: w, height: h, rx: r, ...rest}) {
    const d = `M${x + w / 2} ${y}H${x + w - r}A${r} ${r} 0 0 1 ${x + w} ${y + r}V${y + h - r}`
        + `A${r} ${r} 0 0 1 ${x + w - r} ${y + h}H${x + r}A${r} ${r} 0 0 1 ${x} ${y + h - r}V${y + r}`
        + `A${r} ${r} 0 0 1 ${x + r} ${y}Z`;
    return svg("path", {...rest, d});
}

function dot({cx, cy, r, ...rest}) {
    return svg("path", {...rest, d: `M${cx} ${cy - r}A${r} ${r} 0 0 1 ${cx} ${cy + r}A${r} ${r} 0 0 1 ${cx} ${cy - r}Z`});
}

// Drawn around (0, 0) on the 128x72 stage, about 34x22.
const SHAPES = {
    card: () => [
        rounded({class: "pv-obj", x: -17, y: -11, width: 34, height: 22, rx: 4.5}),
        rounded({class: "pv-line", x: -12, y: -4.5, width: 17, height: 3.4, rx: 1.7, opacity: .6}),
        rounded({class: "pv-line", x: -12, y: 1.6, width: 10, height: 3.4, rx: 1.7, opacity: .35}),
    ],
    title: () => {
        const t = svg("text", {class: "pv-obj pv-word", x: 0, y: 5.5, "font-size": 15, "text-anchor": "middle"});
        t.textContent = "Title";
        return [t];
    },
    photo: () => [
        rounded({class: "pv-obj", x: -16, y: -11, width: 32, height: 22, rx: 3}),
        rounded({class: "pv-line", x: -13.5, y: -8.5, width: 27, height: 17, rx: 1.5, opacity: .9}),
        dot({class: "pv-obj", cx: 6.5, cy: -3.5, r: 2.6}),
        svg("path", {class: "pv-obj", d: "M-13.5 8.5 L-5 -1 L1 5 L5 1.5 L13.5 8.5 Z"}),
    ],
    clip: () => {
        const holes = [];
        for (let i = 0; i < 5; i++) {
            for (const y of [-9.6, 7.2]) {
                holes.push(rounded({class: "pv-line", x: -14 + i * 6.2, y, width: 3.4, height: 2.4, rx: .6, opacity: .7}));
            }
        }
        return [
            rounded({class: "pv-obj", x: -17, y: -11, width: 34, height: 22, rx: 2.5}),
            ...holes,
            svg("path", {class: "pv-line", d: "M-3 -4.5 L5 0 L-3 4.5 Z", opacity: .9}),
        ];
    },
};

let stages = 0;                 // for each stage's own mask id

function stage(obj) {
    // The reveal's mask (motion_resolve._reveal), in the frame's own space:
    // drawn around the frame's middle, as the group it masks is.
    const id = `pv-reveal-${++stages}`;
    const mask = svg("mask", {id, maskUnits: "userSpaceOnUse", maskContentUnits: "userSpaceOnUse",
                              x: -400, y: -300, width: 800, height: 600}, [
        svg("rect", {class: "pv-reveal-rect", fill: "white"}),
        svg("ellipse", {class: "pv-reveal-iris", fill: "white"}),
    ]);
    const holder = svg("g", {transform: "translate(64 36)"}, [obj]);
    holder.__mask = {id, mask};
    // The colours split (motion_resolve._split): red right, blue left, added back.
    const only = (r, g, b) => `${r} 0 0 0 0  0 ${g} 0 0 0  0 0 ${b} 0 0  0 0 0 1 0`;
    const split = svg("filter", {id: `${id}-split`, x: "-50%", y: "-50%", width: "200%", height: "200%",
                                 "color-interpolation-filters": "sRGB"}, [
        svg("feColorMatrix", {in: "SourceGraphic", type: "matrix", values: only(1, 0, 0), result: "r"}),
        svg("feOffset", {in: "r", dx: 0, result: "rs"}),
        svg("feColorMatrix", {in: "SourceGraphic", type: "matrix", values: only(0, 1, 0), result: "g"}),
        svg("feColorMatrix", {in: "SourceGraphic", type: "matrix", values: only(0, 0, 1), result: "b"}),
        svg("feOffset", {in: "b", dx: 0, result: "bs"}),
        svg("feComposite", {in: "rs", in2: "g", operator: "arithmetic", k2: 1, k3: 1, result: "rg"}),
        svg("feComposite", {in: "rg", in2: "bs", operator: "arithmetic", k2: 1, k3: 1}),
    ]);
    holder.__split = {id: `${id}-split`, offsets: split.querySelectorAll("feOffset")};
    return svg("svg", {viewBox: "0 0 128 72", "aria-hidden": "true"}, [
        svg("defs", {}, [mask, split]),
        svg("line", {class: "pv-guide", x1: 0, y1: 36, x2: 128, y2: 36}),
        svg("line", {class: "pv-guide", x1: 64, y1: 0, x2: 64, y2: 72}),
        rounded({class: "pv-safe", x: 8, y: 6, width: 112, height: 60, rx: 3}),
        holder,
    ]);
}

/* Shapes a stage's reveal for m (-1 hidden, 0 all shown, 1 hidden) over the
   shape's own outline (box: its middle and size, stage units) - as Fusion's
   mask goes over the clip's own picture (motion_resolve._reveal): a wipe's
   edge along its way (at its angle), a split opening from the middle, blinds
   slat by slat, an iris from the centre, with a soft edge. Off at 0 - unless
   it's held there, hiding what moves outside the shape's rest. */
function reveal(holder, mask, m, box) {
    const own = holder && holder.__mask;
    if (!own) return;
    if (!mask || !box || (Math.abs(m) < 0.002 && !mask.hold)) {
        if (holder.__masked) { holder.removeAttribute("mask"); holder.__masked = false; }
        return;
    }
    const edge = (mask.soft * 1.5 + 0.01) * 128;
    const soft = `blur(${(mask.soft * 64).toFixed(2)}px)`;
    const open = Math.max(0, 1 - Math.abs(m));
    const shapes = own.mask;
    const rects = () => [...shapes.querySelectorAll("rect")];
    const iris = shapes.querySelector("ellipse");
    const wanted = mask.shape === "iris" ? 0 : mask.shape === "blinds" ? mask.count : 1;
    while (rects().length < wanted) shapes.appendChild(svg("rect", {fill: "white"}));
    rects().forEach((r, i) => { r.style.display = i < wanted ? "" : "none"; r.style.filter = soft; });
    iris.style.display = mask.shape === "iris" ? "" : "none";
    if (mask.shape === "iris") {
        const r = open * (Math.hypot(box.w, box.h) + 2 * edge) / 2;
        Object.entries({cx: box.cx, cy: box.cy, rx: r, ry: r}).forEach(([k, v]) => iris.setAttribute(k, v));
        iris.style.filter = soft;
    } else {
        // The way it runs, on the stage (y down): along x, or down y, turned by its angle.
        const a = ((mask.angle || 0) + (mask.axis === "x" ? 0 : -90)) * Math.PI / 180;
        const dx = Math.cos(a), dy = -Math.sin(a);
        const along = Math.abs(box.w * dx) + Math.abs(box.h * dy) + 2 * edge;
        const across = Math.abs(box.w * dy) + Math.abs(box.h * dx) + 2 * edge;
        const turn = Math.atan2(dy, dx) * 180 / Math.PI;
        const put = (r, cx, cy, len) => {
            Object.entries({x: -len / 2, y: -across / 2, width: len, height: across}).forEach(([k, v]) => r.setAttribute(k, v));
            r.setAttribute("transform", `translate(${cx} ${cy}) rotate(${turn})`);
        };
        const all = rects();
        if (mask.shape === "blinds") {
            const n = mask.count, inner = along - 2 * edge;
            for (let i = 0; i < n; i++) {
                const off = ((i + 0.5) / n - 0.5) * inner;
                put(all[i], box.cx + off * dx, box.cy + off * dy, open * (inner / n + 2 * edge / n));
            }
        } else if (mask.shape === "split") {
            put(all[0], box.cx, box.cy, along * open);
        } else {
            put(all[0], box.cx + m * along * dx, box.cy + m * along * dy, along);
        }
    }
    if (!holder.__masked) { holder.setAttribute("mask", `url(#${own.id})`); holder.__masked = true; }
}

/* Trails: copies of the shape a moment behind it, fainter each, under it
   (motion_resolve._echo). They're made when first wanted. */
function trails(t, at, a) {
    const spec = t.p.echoNow;
    const ghosts = t.ghosts || [];
    const now = typeof at === "function" ? at(0) : at;
    const strength = spec ? Math.max(0, Math.min(1, a.e(now))) : 0;
    if (!spec || strength < 0.005) {
        for (const g of ghosts) g.style.display = "none";
        return;
    }
    if (!t.ghosts) {
        t.ghosts = [];
        for (let i = 0; i < spec.copies; i++) {
            const g = t.obj.cloneNode(true);
            g.classList.add("pv-ghost");
            t.obj.parentNode.insertBefore(g, t.obj);
            t.ghosts.push(g);
        }
    }
    t.ghosts.forEach((g, i) => {
        const late = (i + 1) * spec.delay;
        const f = typeof at === "function" ? at(late) : Math.max(0, at - late / t.p.dur * LAST);
        g.style.display = "";
        g.setAttribute("transform", t.obj.getAttribute("transform") ? placed(a, f) : "");
        g.setAttribute("opacity", Math.max(0, Math.min(1, a.o(f))) * strength * spec.fade ** (i + 1));
    });
}

// The transform pose() gives a shape at frame f, flip aside.
const placed = (a, f) => {
    const size = Math.max(0.001, a.s(f));
    return `translate(${a.x(f) * 2} ${a.y(f) * 2}) rotate(${a.r(f)}) `
        + `scale(${Math.max(0.001, size * a.w(f))} ${Math.max(0.001, size * a.h(f))})`;
};

/* A sweep of light across the shape (motion_resolve._shine): a slanted white
   band, only where the shape is - its own outline as the band's mask. */
function sweep(t, l) {
    const spec = t.p.shineNow;
    if (!spec || l <= 0.002 || l >= 0.998) {
        if (t.band) t.band.style.display = "none";
        return;
    }
    if (!t.band) {
        const id = `pv-sweep-${++stages}`;
        const cut = svg("mask", {id, maskUnits: "userSpaceOnUse", maskContentUnits: "userSpaceOnUse",
                                 x: -400, y: -300, width: 800, height: 600},
            [...t.obj.cloneNode(true).childNodes].map(n => { n.style.fill = "#fff"; n.style.stroke = "#fff"; return n; }));
        const rect = svg("rect", {fill: "#fff"});
        t.band = svg("g", {class: "pv-sweep", mask: `url(#${id})`}, [svg("defs", {}, [cut]), rect]);
        t.band.__rect = rect;
        t.obj.appendChild(t.band);
    }
    const box = t.box || outline(t.obj) || {cx: 0, cy: 0, w: 34, h: 22};
    const width = spec.width * box.w;
    const reach = box.w / 2 + width / 2 + box.h * Math.abs(Math.tan(spec.angle * Math.PI / 180)) / 2 + spec.soft * 128;
    const x = box.cx - reach + l * 2 * reach;
    t.band.style.display = "";
    t.band.setAttribute("opacity", spec.strength);
    const rect = t.band.__rect;
    Object.entries({x: -width / 2, y: -box.h * 1.5, width, height: box.h * 3}).forEach(([k, v]) => rect.setAttribute(k, v));
    rect.setAttribute("transform", `translate(${x} ${box.cy}) rotate(${spec.angle})`);
    rect.style.filter = `blur(${(spec.soft * 64).toFixed(2)}px)`;
}

/* The flip (motion.flip_corners): the shape's corners turned in depth and
   seen in perspective - drawn as the closest flat slant through three of
   them, on a group of its own inside the shape. */
function flipCorners(f, g, aspect, pivot, depth) {
    const a = f * Math.PI / 180, b = g * Math.PI / 180;
    const px = ({left: -0.5, right: 0.5}[pivot] || 0) * aspect, py = {bottom: -0.5, top: 0.5}[pivot] || 0;
    const out = {};
    for (const [name, cx, cy] of [["TopLeft", 0, 1], ["TopRight", 1, 1], ["BottomLeft", 0, 0], ["BottomRight", 1, 0]]) {
        let x = (cx - 0.5) * aspect - px, y = (cy - 0.5) - py, z;
        [x, z] = [x * Math.cos(a), x * Math.sin(a)];
        [y, z] = [y * Math.cos(b) - z * Math.sin(b), y * Math.sin(b) + z * Math.cos(b)];
        x += px; y += py;
        const k = depth / Math.max(0.05, depth + z);
        out[name] = [0.5 + x * k / aspect, 0.5 + y * k];
    }
    return out;
}

function flipped(t, f, g) {
    const spec = t.p.flipNow;
    if (!spec || (Math.abs(f) < 0.05 && Math.abs(g) < 0.05)) {
        if (t.turned) { t.turned.removeAttribute("transform"); }
        return;
    }
    if (!t.turned) {
        t.turned = svg("g", {class: "pv-flip"}, [...t.obj.childNodes]);
        t.obj.appendChild(t.turned);
    }
    const box = t.box || outline(t.turned) || {cx: 0, cy: 0, w: 34, h: 22};
    const [bx, by] = [box.cx - box.w / 2, box.cy - box.h / 2];
    const c = flipCorners(f, g, box.w / box.h, spec.pivot, spec.depth);
    // Shares of the box, y up, to the box's own units, y down.
    const pt = ([u, v]) => [bx + u * box.w, by + (1 - v) * box.h];
    const [tl, tr, bl] = [pt(c.TopLeft), pt(c.TopRight), pt(c.BottomLeft)];
    const ma = (tr[0] - tl[0]) / box.w, mb = (tr[1] - tl[1]) / box.w;
    const mc = (bl[0] - tl[0]) / box.h, md = (bl[1] - tl[1]) / box.h;
    const me = tl[0] - ma * bx - mc * by, mf = tl[1] - mb * bx - md * by;
    t.turned.setAttribute("transform", `matrix(${[ma, mb, mc, md, me, mf].map(v => v.toFixed(4)).join(" ")})`);
}

/* The outline of a viewer's parts as they rest (each part's own box at its place). */
function partsOutline(parts) {
    let lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
    for (const p of parts) {
        try {
            const b = p.node.getBBox();
            lo = [Math.min(lo[0], p.x + b.x), Math.min(lo[1], p.y + b.y)];
            hi = [Math.max(hi[0], p.x + b.x + b.width), Math.max(hi[1], p.y + b.y + b.height)];
        } catch (_e) { /* not drawn yet */ }
    }
    return hi[0] > lo[0] ? {cx: (lo[0] + hi[0]) / 2, cy: (lo[1] + hi[1]) / 2, w: hi[0] - lo[0], h: hi[1] - lo[1]} : null;
}

/* A shape's outline at rest, in its stage's units around the middle. */
function outline(obj) {
    try {
        const b = obj.getBBox();
        return b.width > 0 ? {cx: b.x + b.width / 2, cy: b.y + b.height / 2, w: b.width, h: b.height} : null;
    } catch (_e) {
        return null;
    }
}

// Animate by: the tiles play a line of text unit by unit, as a Text+ will
// (units.py). Each part is drawn around its own middle, where it turns and
// scales, at (x, y) on the stage.
const SAMPLE = {lines: ["First line", "and the next"], words: ["Every", "word", "moves"], letters: "Letters"};

const byUnit = () => (state.unit && state.unit !== "clip" ? state.unit : null);
const stagger = () => Number(state.stagger) || 0;
const staggerOf = t => (t.stagger == null ? stagger() : t.stagger);
// The unit a viewer plays by: a Text pack tile's own, else Animate by's.
const byUnitOf = t => (t.parts ? (t.stagger == null ? byUnit() : t.p.unit) : null);

function unitParts(unit) {
    const size = unit === "letters" ? 15 : 11, advance = size * 0.58;
    const part = (text, x, y) => {
        const t = svg("text", {class: "pv-obj pv-word", x: 0, y: size * 0.36, "font-size": size, "text-anchor": "middle"});
        t.textContent = text;
        // Sample words, split up before they're drawn: never translated piecemeal.
        return {node: svg("g", {translate: "no"}, [t]), x, y, rank: 0};
    };
    if (unit === "lines") return SAMPLE.lines.map((line, i) => part(line, 0, (i - 0.5) * size * 1.35));
    const items = unit === "words" ? SAMPLE.words : [...SAMPLE.letters];
    const gap = unit === "words" ? advance : 0;
    const widths = items.map(s => s.length * advance);
    let x = -(widths.reduce((a, b) => a + b, 0) + gap * (items.length - 1)) / 2;
    return items.map((s, i) => {
        const p = part(s, x + widths[i] / 2, 0);
        x += widths[i] + gap;
        return p;
    });
}

/* When each of n units starts, in steps from the first (units.ranks). The
   random order here is only a stand-in for the one Resolve gets. */
function unitRanks(n, order) {
    const all = [...Array(n).keys()];
    if (order === "reverse") return all.map(i => n - 1 - i);
    if (order === "middle" || order === "edges") {
        const distance = all.map(i => Math.abs(i - (n - 1) / 2));
        const steps = [...new Set(distance)].sort((a, b) => (order === "edges" ? b - a : a - b));
        return distance.map(d => steps.indexOf(d));
    }
    if (order === "random") {
        const shuffled = [...all];
        let seed = 7;
        for (let i = n - 1; i > 0; i--) {
            seed = (seed * 9301 + 49297) % 233280;
            const j = Math.floor(seed / 233280 * (i + 1));
            [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
        }
        return all.map(i => shuffled.indexOf(i));
    }
    return all;
}

function reshape() {
    const make = SHAPES[state.shape] || SHAPES.card;
    for (const t of [...tiles, big].filter(Boolean)) {
        t.box = null;                   // its outline, for the reveal: measured again
        // Its trails, sweep and flip group are made again for the new shape.
        for (const g of t.ghosts || []) g.remove();
        t.ghosts = t.band = t.turned = null;
        // A Text pack tile plays the way its preset is made (by letter, by
        // word); the chosen one's viewer, as Animate by is set.
        const own = t !== big && t.p.unit && t.p.unit !== "clip";
        const unit = own ? t.p.unit : byUnit();
        const shaped = t.p.shape && SHAPES[t.p.shape];     // letters to spread: a word, whatever is selected
        t.stagger = own ? Number(t.p.stagger) || 0 : null;
        if (unit) {
            const parts = unitParts(unit), when = unitRanks(parts.length, state.order);
            parts.forEach((p, i) => { p.rank = when[i]; });
            t.parts = parts;
            t.obj.removeAttribute("transform");
            t.obj.removeAttribute("opacity");
            t.obj.replaceChildren(...parts.map(p => p.node));
        } else {
            t.parts = null;
            t.obj.replaceChildren(...(shaped || make)());
        }
    }
    // The Editor draws the curves themselves: always the whole thing.
    if (ed.viewer) {
        for (const g of ed.viewer.ghosts || []) g.remove();
        ed.viewer.obj.replaceChildren(...make());
        Object.assign(ed.viewer, {box: null, ghosts: null, band: null, turned: null});
    }
}

// ----------------------------------------------------------------- motion

const channel = arr => f => {
    const i = Math.min(LAST - 1, Math.floor(f)), k = f - i;
    return arr[i] + (arr[i + 1] - arr[i]) * k;
};

function prepare(p) {
    aim(p);
    // How long its first move lasts, for the tile: the In, the Emphasis or the
    // Out - a Loop's whole cycle.
    const [a, b] = p.kind === "Loop" ? [0, 1] : p.segs[0] || [0, 1];
    p.moveSeconds = (b - a) * p.dur;
    p.span = playSpan(p.kind, p.segs.map(([s, e]) => [s * LAST, e * LAST]), !!p.keys).map(f => f / LAST);
    return p;
}

/* Plays the fitted Fusion curves, including for built-in presets, turned
   the way Direction is set. The raw samples remain available for editing
   and for the timing bar. */
function aim(p) {
    const keys = p.previewKeys && turnedKeys(p, state.direction);
    p.at = keys ? atKeys(keys)
        : Object.fromEntries(CHANNELS.map(([c]) => [c, p[c] ? channel(p[c]) : () => REST[c]]));
    p.maskNow = (keys && keys.mask) || p.mask;
    [p.echoNow, p.shineNow, p.flipNow] = [p.echo, p.shine, p.flip];
}

// motion.turned: a preset that moves across the frame (its "heading") sent
// another way - mirrored back the other way, its turns too, or turned a
// quarter with the whole move. A move reaching half the frame or more keeps
// its share of the frame, so what came in from off screen still does.
const WAYS = {left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1]};
const HALF = {x: 64, y: 36};

function turnedKeys(p, direction) {
    const keys = p.previewKeys, own = p.heading;
    if (!keys || !WAYS[own] || !WAYS[direction] || own === direction) return keys;
    const [hx, hy] = WAYS[own], [dx, dy] = WAYS[direction];
    let mix, reverse = false;
    if (hx === -dx && hy === -dy) {
        mix = {x: ["x", hx ? -1 : 1], y: ["y", hy ? -1 : 1]};
        reverse = true;
    } else if (-hy === dx && hx === dy) {
        mix = {x: ["y", -1], y: ["x", 1]};          // a quarter turn clockwise
    } else {
        mix = {x: ["y", 1], y: ["x", -1]};          // ...or anticlockwise
    }
    const across = (src, dst) => (src === dst ? 1
        : Math.max(0, ...p[src].map(Math.abs)) / HALF[src] >= 0.5 ? HALF[dst] / HALF[src] : 1);
    const scaled = (list, k) => list.map(([t, v, h, g]) => [t, v * k, h && [h[0], h[1] * k], g && [g[0], g[1] * k]]);
    const out = {...keys};
    for (const [dst, [src, sign]] of Object.entries(mix)) out[dst] = scaled(keys[src], sign * across(src, dst));
    // Flips turn about the axes the move turns (motion.turned).
    const byAxis = {x: "f", y: "g"};
    for (const [dst, [src, sign]] of Object.entries(mix)) if (keys[byAxis[src]]) out[byAxis[dst]] = scaled(keys[byAxis[src]], sign);
    // A wipe or split turns with the move (motion.turned).
    if (p.mask && p.mask.shape !== "iris" && keys.m) {
        const [axis, [, sign]] = Object.entries(mix).find(([, [src]]) => src === p.mask.axis);
        out.m = scaled(keys.m, sign);
        out.mask = {...p.mask, axis};
    }
    if (reverse) out.r = scaled(keys.r, -1);
    else if (keys.w && keys.h) [out.w, out.h] = [keys.h, keys.w];   // a quarter turn: squash turns with it
    return out;
}

/* The part of a preset that plays, in samples: [first, last]. An In or an
   Out saved from the Editor still holds the whole curve it was drawn from,
   so it plays only the part that goes on clips; built-in ones play whole,
   as they always have. `runs` are where it moves (motion.segments). */
function playSpan(kind, runs, drawn) {
    const first = runs[0], last = runs[runs.length - 1];
    if (kind === "In") return first && first[0] === 0 ? [0, first[1]] : [0, 0];
    if (kind === "Out" && drawn) return last && last[1] === LAST ? [last[0], LAST] : [LAST, LAST];
    return [0, LAST];
}

/* Draws a viewer at `at`: a frame (in samples), or frameFor's function of
   how many seconds late a unit starts - each of a tile's parts plays its own
   rank of staggers behind the first. */
function pose(t, at) {
    const frame = typeof at === "function" ? at : () => at;
    const a = t.p.at;
    // Width and height squash it on its own axes, before it turns - as the
    // Transform in front of Fusion's Merge does.
    const place = (node, f, x = 0, y = 0) => {
        const size = Math.max(0.001, a.s(f));
        node.setAttribute("transform", `translate(${x + a.x(f) * 2} ${y + a.y(f) * 2}) rotate(${a.r(f)}) `
            + `scale(${Math.max(0.001, size * a.w(f))} ${Math.max(0.001, size * a.h(f))})`);
        node.setAttribute("opacity", Math.max(0, Math.min(1, a.o(f))));
    };
    // Tracking spreads the letters: each letter part out from the middle, a
    // word's own letters by letter-spacing (about 1.4 em per 1, as Text+).
    const track = a.k(frame(0));
    if (t.parts) {
        const n = t.parts.length, letters = byUnitOf(t) === "letters";
        t.parts.forEach((p, i) => {
            const spread = letters ? (i - (n - 1) / 2) * track * 1.4 * 15 : 0;
            place(p.node, frame(p.rank * staggerOf(t)), p.x + spread, p.y);
        });
    } else {
        place(t.obj, frame(0));
    }
    // (As a style: Chromium draws SVG text's letter-spacing attribute as if it weren't there.)
    for (const word of t.obj.querySelectorAll("text")) {
        const spacing = track ? `${(track * 1.4 * Number(word.getAttribute("font-size") || 15)).toFixed(2)}px` : "";
        if (word.style.letterSpacing !== spacing) word.style.letterSpacing = spacing;
    }
    // Blur: on the stage around it, so it's as much however big it's drawn
    // (Fusion's Blur comes after the Merge). Not by unit: a Text+ letter
    // can't be blurred on its own.
    const holder = t.obj.parentNode;
    // Its outline at rest, for the reveal, sweep and flip - before any flip turns it.
    if ((t.p.maskNow || t.p.shineNow || t.p.flipNow) && !t.box) t.box = t.parts ? partsOutline(t.parts) : outline(t.obj);
    if (!t.parts) {
        flipped(t, a.f(frame(0)), a.g(frame(0)));
        trails(t, at, a);
        sweep(t, a.l(frame(0)));
    }
    // Blur, and the colours split - on the stage around it, so they're as
    // much however big it's drawn (Fusion's come after the Merge). Not by
    // unit: a Text+ letter can't be blurred on its own.
    const blur = t.parts ? 0 : Math.max(0, a.b(frame(0))) * 2;
    const shift = t.parts ? 0 : a.c(frame(0)) * 2;
    const look = `${blur.toFixed(2)}|${shift.toFixed(2)}`;
    if (holder && holder.__look !== look) {
        holder.__look = look;
        if (holder.__split) for (const [i, node] of holder.__split.offsets.entries()) node.setAttribute("dx", i ? -shift : shift);
        holder.style.filter = [Math.abs(shift) > 0.02 && holder.__split ? `url(#${holder.__split.id})` : "",
                               blur > 0.01 ? `blur(${blur.toFixed(2)}px)` : ""].join(" ").trim();
    }
    // The reveal: over the whole frame, as Fusion's mask on the Merge is.
    // By unit too: over the whole text, as on a Text+ (motion_resolve._build_units).
    reveal(holder, t.p.maskNow, a.m(frame(0)), t.box);
    if (t.head) t.head.style.left = `${(frame(0) / LAST) * 100}%`;
}

function chosenSpan(p) {
    const first = p.segs[0], last = p.segs[p.segs.length - 1];
    if (!first || !last) return p.span;
    if (p.kind === "In · Out") {
        if (state.way === "in") return [0, first[1]];
        if (state.way === "out") return [last[0], 1];
    } else if (p.kind === "In") return [0, first[1]];
    else if (p.kind === "Out") return [last[0], 1];
    else if (p.kind === "Emphasis") return [first[0], last[1]];
    return p.span;
}

/* Where a viewer is now, as a function: seconds a unit starts late -> its
   frame, in samples. With Animate by, the loop runs until the last unit's
   move is over too. */
function frameFor(t, now, always) {
    const [a, b] = always ? chosenSpan(t.p) : (t.p.span || [0, 1]);
    const loop = t.p.kind === "Loop";
    let elapsed;
    if (!always && state.play === "hover") {
        // At rest: mid-hold - or where a lone In ends, where a lone Out starts,
        // where a Loop starts.
        if (t.hoverAt === null) {
            const rest = (loop ? 0 : b - a >= 1 ? 0.5 : t.p.kind === "Out" ? a : b) * LAST;
            return () => rest;
        }
        elapsed = (now - t.hoverAt) / 1000;
    } else {
        elapsed = now / 1000 + t.offset;
    }
    const speed = t.p.kind === "Whole clip" ? 1 : Number(state.speed) || 1;     // a Whole clip move: as made
    const fps = Number(state.selection?.fps) || 24;
    const length = Math.max(0.01, (b - a) * t.p.dur / speed);
    const lag = t.parts ? Math.max(0, ...t.parts.map(p => p.rank)) * staggerOf(t) : 0;
    // A Loop goes round and round, each part its stagger behind; the rest
    // play, wait until the last part is done, rest a moment and start again.
    const cycle = loop ? length : length + lag + PAUSE;
    const local = ((elapsed % cycle) + cycle) % cycle;
    if (loop) {
        return late => {
            const into = (((local - late) % length) + length) % length;
            return Math.min(b, a + Math.floor(into * fps) / fps * speed / t.p.dur) * LAST;
        };
    }
    return late => {
        // Resolve shows whole timeline frames. Sampling at the same rate and
        // selected speed exposes the jumps a fast move makes at 24 fps.
        const shown = Math.min(length, Math.floor(Math.max(0, local - late) * fps) / fps);
        return Math.min(b, a + shown * speed / t.p.dur) * LAST;
    };
}

function tick(now) {
    // The next frame first: one frame's error mustn't stop every preview for good.
    requestAnimationFrame(tick);
    if (!document.hidden && !$("panel-previews").hidden) {
        for (const t of tiles) if (t.visible) pose(t, frameFor(t, now, false));
        if (big) pose(big, frameFor(big, now, true));
    }
    if (!document.hidden && !$("panel-editor").hidden) editorTick(now);
    if (!document.hidden) hlTick(now);
    if (!document.hidden) zmTick(now);
    if (!document.hidden && typeof trTick === "function") trTick(now);
}

const seen = new IntersectionObserver(entries => {
    for (const e of entries) {
        const t = tiles.find(x => x.node === e.target);
        if (t) t.visible = e.isIntersecting;
    }
});

// ---------------------------------------------------------- Bezier curves
// The same as motion.py's: a channel is [[t, value, left handle, right
// handle], ...], t in samples, a handle an absolute [t, value] or null.

const bez = (a, b, c, d, u) => { const w = 1 - u; return w * w * w * a + 3 * w * w * u * b + 3 * w * u * u * c + u * u * u * d; };

function controls(k0, k1) {
    return [[k0[0], k0[1]], k0[3] || [k0[0], k0[1]], k1[2] || [k1[0], k1[1]], [k1[0], k1[1]]];
}

function solve(p, t) {
    let lo = 0, hi = 1;
    for (let i = 0; i < 32; i++) {
        const u = (lo + hi) / 2;
        if (bez(p[0][0], p[1][0], p[2][0], p[3][0], u) < t) lo = u; else hi = u;
    }
    return (lo + hi) / 2;
}

function evalKeys(keys, t) {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 0; i + 1 < keys.length; i++) {
        const k0 = keys[i], k1 = keys[i + 1];
        if (t <= k1[0]) {
            if (k1[0] - k0[0] <= 1e-9) return k1[1];
            const p = controls(k0, k1);
            return bez(p[0][1], p[1][1], p[2][1], p[3][1], solve(p, t));
        }
    }
    return keys[keys.length - 1][1];
}

// Every channel; one a preset has no keys for (saved before blur and
// squash were) holds at rest.
const atKeys = keys => Object.fromEntries(CHANNELS.map(([c]) => [c, keys[c] ? t => evalKeys(keys[c], t) : () => REST[c]]));

/* One more key at t, the curve's shape unchanged (motion.split_at). Its index. */
function splitAt(keys, t) {
    for (let i = 0; i + 1 < keys.length; i++) {
        const k0 = keys[i], k1 = keys[i + 1];
        if (Math.abs(k0[0] - t) < 1e-6) return i;
        if (Math.abs(k1[0] - t) < 1e-6) return i + 1;
        if (k0[0] < t && t < k1[0]) {
            const p = controls(k0, k1), u = solve(p, t);
            const lerp = (a, b) => [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
            const q0 = lerp(p[0], p[1]), q1 = lerp(p[1], p[2]), q2 = lerp(p[2], p[3]);
            const r0 = lerp(q0, q1), r1 = lerp(q1, q2), s = lerp(r0, r1);
            k0[3] = k0[3] ? q0 : null;
            k1[2] = k1[2] ? q2 : null;
            keys.splice(i + 1, 0, [t, s[1], r0, r1]);
            return i + 1;
        }
    }
    return -1;
}

// ------------------------------------------------------------------ tiles

const seconds = s => `${s.toFixed(1)}s`;
// A tile's length at the chosen speed; a Whole clip move takes the clip's.
const tileSeconds = p => (p.kind === "Whole clip" ? "" : seconds(p.moveSeconds / (Number(state.speed) || 1)));

function tile(p, index) {
    const obj = svg("g");
    const head = el("div.pv-head");
    const choose = () => send("choose", {id: p.id});
    const hearted = favorites.has(p.id);
    const node = el("div.card.pv-tile", {
        role: "button", tabindex: "0", "aria-pressed": String(p.id === state.chosen),
        title: `${p.label} · ${p.pack}`, onclick: choose,
        onkeydown: e => {
            if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); choose(); }
        },
    }, [
        el("div.pv-stage", {}, [stage(obj)]),
        el("div.pv-track", {}, [
            ...p.segs.map(([a, b]) => el("div.pv-seg", {style: `left:${a * 100}%;width:${(b - a) * 100}%`})),
            head,
        ]),
        el("div.pv-meta", {}, [
            el("span.pv-name", {text: p.label, translate: "no"}),
            el("span.pv-sub.muted.small", {}, [el("span", {text: p.kind}), el("span.pv-dur", {text: tileSeconds(p)})]),
        ]),
        el("div.pv-acts", {}, [
            el("button.pv-act", {type: "button", title: "Open in the Editor",
                                 onclick: e => { e.stopPropagation(); openInEditor(p.id); }}, [glyph("edit")]),
            el("button.pv-act.heart", {
                type: "button", "aria-pressed": String(hearted), dataset: {id: p.id},
                title: hearted ? "Take it out of Favorites" : "Add to Favorites",
                onclick: e => { e.stopPropagation(); send("favorite", {id: p.id, on: !favorites.has(p.id)}); },
            }, [glyph("heart")]),
        ]),
    ]);
    const t = {p, node, obj, head, offset: index * 0.37, hoverAt: null, visible: true};
    node.addEventListener("mouseenter", () => { t.hoverAt = performance.now(); });
    node.addEventListener("mouseleave", () => { t.hoverAt = null; });
    tiles.push(t);
    return node;
}

function segmented(host, items, key) {
    host.replaceChildren(...items.map(([id, label, n]) => el("button", {
        type: "button", "aria-pressed": String(view[key] === id),
        onclick: () => { view[key] = id; drawGroups(); },
    }, [label, n == null ? null : el("span.n", {text: String(n)})])));
}

const hearts = () => state.tab === "favorites";

/* The packs open on Previews (page.py keeps them): null until one is opened
   or closed, when only the top one is. Searching, one pack picked or
   Favorites shows them all - what's being looked for is never folded away. */
let openPacks = null;
const showingAll = () => hearts() || Boolean(view.q) || view.pack !== "all";
const isOpen = id => showingAll() || (openPacks ? openPacks.includes(id) : packs[0]?.id === id);

function toggleOpen(id) {
    const open = packs.map(k => k.id).filter(k => openPacks ? openPacks.includes(k) : k === packs[0]?.id);
    openPacks = open.includes(id) ? open.filter(k => k !== id) : [...open, id];
    send("open_packs", {packs: openPacks});
    drawGroups();
}

const matches = p => (hearts() || view.pack === "all" || p.pack === view.pack)
    && (view.kind === "all" || p.kind === view.kind)
    && (!view.q || p.label.toLowerCase().includes(view.q));

function drawGroups() {
    const pool = hearts() ? presets.filter(p => favorites.has(p.id)) : presets;
    const count = f => pool.filter(f).length;
    $("packs").hidden = hearts();
    segmented($("packs"), [["all", "All", presets.length],
        ...packs.map(k => [k.id, k.id, count(p => p.pack === k.id)])], "pack");
    // Only the kinds there are: an In is only ever made in the Editor.
    segmented($("kinds"), [["all", "Any kind"], ...kinds.filter(k => presets.some(p => p.kind === k)).map(k => [k, k])], "kind");

    for (const t of tiles) seen.unobserve(t.node);
    tiles.length = 0;
    let index = 0;
    const groups = packs.map(pack => {
        const list = pool.filter(p => p.pack === pack.id && matches(p));
        if (!list.length) return null;
        // A folded pack has no tiles at all: nothing to draw while it's shut.
        const open = isOpen(pack.id);
        const fixed = showingAll();
        return el(`section.pv-group${open ? "" : ".folded"}`, {style: `--pack: var(--pack-${pack.id.toLowerCase()})`}, [
            el("button.pv-group-head", {
                type: "button", "aria-expanded": String(open), disabled: fixed || null,
                title: fixed ? "" : open ? "Fold this pack away" : "Show this pack",
                onclick: () => toggleOpen(pack.id),
            }, [
                fixed ? null : el("span.pv-fold", {}, [glyph("chevron")]),
                el("span.pv-dot"),
                el("h2.section-title", {text: pack.id}),
                el("span.chip", {text: String(list.length)}),
            ]),
            open ? el("div.pv-grid", {}, list.map(p => tile(p, index++))) : null,
        ]);
    }).filter(Boolean);
    let empty;
    if (hearts() && !favorites.size) {
        empty = el("div.empty", {}, [
            el("span.strong", {text: "No favorites yet"}),
            el("span", {text: "Heart a preset on Previews and it's kept here."}),
        ]);
    } else {
        empty = el("div.empty", {}, [
            el("span.strong", {text: "No presets match"}),
            el("span", {text: "Try another name, pack or kind."}),
        ]);
    }
    $("groups").replaceChildren(...(groups.length ? groups : [empty]));
    for (const t of tiles) seen.observe(t.node);
    reshape();
}

$("q").addEventListener("input", e => { view.q = e.target.value.trim().toLowerCase(); drawGroups(); });

// ------------------------------------------------------------ apply card

for (const b of $("way").querySelectorAll("button")) b.onclick = () => send("way", {way: b.dataset.way});
for (const b of $("speed").querySelectorAll("button")) b.onclick = () => send("speed", {speed: Number(b.dataset.speed)});
for (const b of $("direction").querySelectorAll("button")) b.onclick = () => send("direction", {direction: b.dataset.direction});
for (const b of $("unit").querySelectorAll("button")) b.onclick = () => send("unit", {unit: b.dataset.unit});
for (const b of $("stagger").querySelectorAll("button")) b.onclick = () => send("stagger", {stagger: Number(b.dataset.stagger)});
$("order").onchange = e => send("order", {order: e.target.value});
for (const b of $("play").querySelectorAll("button")) b.onclick = () => send("play", {play: b.dataset.play});
$("apply").onclick = () => send("apply");
$("remove").onclick = () => send("remove");
$("update").onclick = () => send("update");

function drawChosen() {
    const p = presets.find(x => x.id === state.chosen) || presets[0];
    if (!p) return;
    if (!big || big.p !== p) {
        const obj = svg("g");
        $("chosen-stage").replaceChildren(stage(obj));
        big = {p, obj, head: null, offset: 0, hoverAt: null};
        $("apply").closest(".pv-apply").style.setProperty("--pack", `var(--pack-${p.pack.toLowerCase()})`);
        reshape();
    }
    $("chosen-name").textContent = p.label;
    $("chosen-sub").textContent = `${p.pack} · ${p.kind} · ${Number(state.selection?.fps) || 24} fps preview`;
    const inOut = p.kind === "In · Out";
    $("way-row").hidden = !inOut;
    // Only for a preset that moves across the frame.
    $("direction-row").hidden = !p.heading;
    // Stagger and Order stay where they are with Clip, only switched off, so
    // nothing around Animate by moves while it's being clicked through.
    const unit = byUnit();
    for (const row of [$("stagger-row"), $("order-row")]) {
        row.classList.toggle("pv-off", !unit);
        for (const control of row.querySelectorAll("button, select")) control.disabled = !unit;
    }
    for (const t of tiles) t.node.setAttribute("aria-pressed", String(t.p.id === p.id));
}

function selectionChips() {
    const s = state.selection;
    if (!state.connected) {
        return [el("span.muted.small", {text: "Not connected to Resolve"}),
                el("button.btn.ghost", {type: "button", text: "Connect", onclick: () => send("refresh")})];
    }
    if (state.problem) return [el("span.warn", {text: state.problem})];
    if (state.resolve_busy) return [el("span.warn", {text: "Resolve is busy – Buddy carries on once playback stops"})];
    if (!s) return [el("span.muted.small", {text: "Reading the selection…"})];
    if (!s.total) return [el("span.muted.small", {text: "Nothing selected in the timeline"})];
    return s.parts.map(part => el("span.chip", {text: part.text}));
}

function applyLabel(working, can) {
    return working ? "Applying…" : !can ? "Apply to selected" : can === 1 ? "Apply to 1 clip" : `Apply to ${can} clips`;
}

function drawSelection() {
    const s = state.selection;
    $("selection").replaceChildren(...selectionChips());
    $("ed-selection").replaceChildren(...selectionChips());

    const can = s && s.animatable;
    const apply = $("apply"), remove = $("remove");
    apply.disabled = !!state.working || !can;
    remove.disabled = !!state.working || !(s && s.animated);
    apply.textContent = applyLabel(state.working === "apply" && state.tab !== "editor", can);
    remove.textContent = state.working === "remove" ? "Removing…" : "Remove";
    $("ed-apply").disabled = !!state.working || !can || !ed.keys;
    $("ed-apply").textContent = applyLabel(state.working === "apply" && state.tab === "editor", can);
    // Re-framed in the Inspector since the preset went on: the move doesn't
    // follow until Update framing takes the new framing in.
    const reframed = (s && s.reframed) || 0;
    $("reframed").hidden = !reframed;
    $("reframed-text").textContent = reframed === 1 ? "Framing changed on 1 clip"
        : `Framing changed on ${reframed} clips`;
    $("update").disabled = !!state.working;
    $("update").textContent = state.working === "update" ? "Updating…" : "Update framing";
    remove.title = s && s.animated ? "Take Buddy's preset off the selected clips" : "None of the selected clips has a Buddy preset";
}

function pressed(host, attr, value) {
    for (const b of host.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset[attr] === String(value)));
}

// ================================================================= Editor

const CHANNELS = [["x", "Position X"], ["y", "Position Y"], ["r", "Rotation"], ["s", "Scale"], ["o", "Opacity"],
                  ["b", "Blur"], ["w", "Width"], ["h", "Height"], ["m", "Reveal"], ["k", "Tracking"],
                  ["e", "Trails"], ["l", "Sweep"], ["c", "Colour split"], ["f", "Flip Y"], ["g", "Flip X"]];
const REST = {x: 0, y: 0, r: 0, s: 1, o: 1, b: 0, w: 1, h: 1, m: 0, k: 0, e: 0, l: 0, c: 0, f: 0, g: 0};
// motion.py's _EPS and _GAP: how far from rest counts as moving, and how
// short a pass through rest still counts as one move.
const EPS = {x: 0.05, y: 0.05, r: 0.05, s: 0.005, o: 0.005, b: 0.02, w: 0.005, h: 0.005, m: 0.002, k: 0.002,
             e: 0.005, l: 0.002, c: 0.02, f: 0.05, g: 0.05};
const GAP = 3;
// The least a channel's graph spans, so a tiny move isn't blown up to fill it.
const MIN_SPAN = {x: 16, y: 12, r: 30, s: 0.5, o: 1, b: 4, w: 0.5, h: 0.5, m: 2, k: 0.5,
                  e: 1, l: 1, c: 8, f: 90, g: 90};
// Channels measured alike, drawn on one range (fitRange).
const SAME_UNITS = ["s", "w", "h"];
// A channel's value as it reads: x and y as a share of the frame (motion.py:
// x/64 of its width, y/36 of its height), scale and opacity as percentages.
const SHOW = {
    x: v => `${Math.round(v / 64 * 100)}%`, y: v => `${Math.round(v / 36 * 100)}%`,
    r: v => `${Math.round(v)}°`, s: v => `${Math.round(v * 100)}%`, o: v => `${Math.round(v * 100)}%`,
    // Blur as pixels on a 1920-wide frame (a half-unit is 30 px); width and height as percentages.
    b: v => `${Math.round(v * 30)} px`, w: v => `${Math.round(v * 100)}%`, h: v => `${Math.round(v * 100)}%`,
    // Reveal: how far the mask has closed, -100% to 100% (0: all shown).
    m: v => `${Math.round(v * 100)}%`,
    // Tracking: what's added to a Text+'s Character Spacing.
    k: v => `${v > 0 ? "+" : ""}${v.toFixed(2)}`,
    e: v => `${Math.round(v * 100)}%`, l: v => `${Math.round(v * 100)}%`,
    c: v => `${Math.round(v * 30)} px`, f: v => `${Math.round(v)}°`, g: v => `${Math.round(v)}°`,
};
// The kinds the Editor makes; an Emphasis it was opened on stays one.
const EDIT_KINDS = ["In", "Out", "In · Out", "Loop", "Whole clip"];
// The graph's margins, px. The time bar - where the playhead is moved - runs
// along the top; the curves are drawn below it.
const PAD = {l: 48, r: 14, t: 34, b: 12};
const BAR = {top: 4, height: 22};

const ed = {
    id: "",             // the preset it started from
    saved: false,       // ...one of the person's own: Save changes and Delete
    label: "", kind: "In · Out", dur: 1,
    keys: null,         // {x, y, r, s, o}, or null while they're on their way
    waiting: "",        // the id asked for on "keys"
    on: ["s"],          // the channels shown on the graph, in colour, to edit
    ch: "s",            // ...the one being edited: its handles shown, its units up the side
    sels: {},           // channel -> its selected keys' indexes: keys of several shown channels move together
                        // (ed.sel is the edited channel's)
    span: [0, LAST],    // the part that plays for the kind (playSpan)
    ease: null,         // a key's ease copied from the menu (copyEase), to paste
    menuAt: null,       // [left, top] the key menu was dragged to by its grip, or null: beside the keys
    view: "clip",       // "clip": with one clip selected, laid out at its length; "preset": the preset's own time
    layout: null,       // clipLayout(), or null on the preset's own time
    dirty: false,
    playing: true, started: 0,
    head: 0,            // the playhead, in seconds along the graph's time (the clip's or the preset's)
    undo: [], redo: [],
    range: [0, 1],      // the edited channel's values, bottom to top
    ranges: {},         // ...every channel's: each shown one fills the height
    snap: null,         // the time a dragged key snapped to, a key of another channel: drawn as a guide
    viewer: null,
    drag: null,
};
const keyCache = {};    // id -> keys page.py sent
// An edit not yet applied or saved lives only here: kept while it's there (buddy.js keepAlive).
Buddy.holdWhile(() => ed.dirty);

const clone = v => JSON.parse(JSON.stringify(v));
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
// The edited channel's selected keys, read and set as one list.
Object.defineProperty(ed, "sel", {get: () => ed.sels[ed.ch] || [], set: v => { ed.sels[ed.ch] = v; }});
// The channels with keys selected, and how many keys in all.
const selChannels = () => CHANNELS.map(([c]) => c).filter(c => (ed.sels[c] || []).length);
const selCount = () => selChannels().reduce((n, c) => n + ed.sels[c].length, 0);
const clearSel = () => { ed.sels = {}; };
// The one key selected, of the edited channel - its handles shown - or -1.
const single = () => selCount() === 1 && ed.sel.length === 1 ? ed.sel[0] : -1;

function editorShown() {
    if (!ed.id || !presets.some(p => p.id === ed.id)) loadPreset(state.chosen || (presets[0] && presets[0].id), true);
    else drawEditor();
}

function openInEditor(id) {
    goTab("editor");
    if (id !== ed.id) loadPreset(id);
}

async function loadPreset(id, force) {
    const p = presets.find(x => x.id === id);
    if (!p) return;
    if (ed.dirty && !force && !(await Buddy.confirm({
        title: "Discard this edit?", text: "The changes to the curves haven't been saved.", ok: "Discard", danger: true}))) {
        $("ed-base").value = ed.id;
        return;
    }
    Object.assign(ed, {id, saved: p.pack === SAVED, label: p.pack === SAVED ? p.label : `${p.label} edit`,
                       kind: p.kind, dur: p.dur, mask: p.mask || null, echo: p.echo || null, shine: p.shine || null,
                       flip: p.flip || null, sels: {}, dirty: false, undo: [], redo: []});
    if (p.keys) return setKeys(p.keys);
    if (keyCache[id]) return setKeys(keyCache[id]);
    ed.keys = null;
    ed.waiting = id;
    send("keys", {id});
    drawEditor();
}

function setKeys(keys) {
    ed.keys = clone(keys);
    ed.waiting = "";
    // Start on the channel that moves the most.
    const swing = c => {
        const v = ed.keys[c].map(k => k[1]);
        return (Math.max(...v) - Math.min(...v)) / MIN_SPAN[c];
    };
    // Several shown stay shown, from one preset to the next; one shown is the
    // one that moves the most.
    const pool = ed.on.length > 1 ? ed.on : CHANNELS.map(([c]) => c);
    ed.ch = pool.reduce((a, b) => swing(b) > swing(a) ? b : a, pool.includes("s") ? "s" : pool[0]);
    if (ed.on.length <= 1) ed.on = [ed.ch];
    fitRange();
    restart();
    drawEditor();
}

function restart() {
    ed.started = performance.now();
    ed.playing = true;
}

function snapshot() {
    ed.undo.push(JSON.stringify({keys: ed.keys, kind: ed.kind, dur: ed.dur}));
    if (ed.undo.length > 200) ed.undo.shift();
    ed.redo = [];
}

function changed() {
    ed.dirty = true;
    drawEditor();
}

function step(from, to) {
    if (!from.length) return;
    to.push(JSON.stringify({keys: ed.keys, kind: ed.kind, dur: ed.dur}));
    Object.assign(ed, JSON.parse(from.pop()));
    clearSel();
    changed();
}

/* Every channel's range, each fitted to its own keys and handles: the shown
   ones are drawn over each other, each filling the graph's height. */
function fitRange() {
    for (const [c] of CHANNELS) {
        const vals = [REST[c]];
        for (const k of ed.keys[c]) {
            vals.push(k[1]);
            if (k[2]) vals.push(k[2][1]);
            if (k[3]) vals.push(k[3][1]);
        }
        let lo = Math.min(...vals), hi = Math.max(...vals);
        const span = Math.max(hi - lo, MIN_SPAN[c]);
        const mid = (lo + hi) / 2;
        ed.ranges[c] = [mid - span / 2 * 1.15, mid + span / 2 * 1.15];
    }
    // Scale, width and height are all sizes: one shared range, so they read
    // against each other - a 6% squash looks as big as a 6% grow.
    const sizes = SAME_UNITS.map(c => ed.ranges[c]);
    const shared = [Math.min(...sizes.map(r => r[0])), Math.max(...sizes.map(r => r[1]))];
    for (const c of SAME_UNITS) ed.ranges[c] = shared;
    ed.range = ed.ranges[ed.ch];
}

/* Edits c: its handles shown, its units up the side. What is selected stays. */
function activate(c) {
    if (c === ed.ch) return;
    ed.ch = c;
    ed.range = ed.ranges[c];
}

/* Where the keys move, in whole samples - motion.segments for a preset drawn
   as keys (_keyed_runs, then short rests merged), so the preview plays the
   same part that goes on clips. */
function runsOf(keys) {
    const times = [...new Set(CHANNELS.flatMap(([c]) => keys[c].map(k => +k[0].toFixed(6))))].sort((a, b) => a - b);
    const runs = [];
    for (let i = 0; i + 1 < times.length; i++) {
        const t0 = times[i], t1 = times[i + 1];
        let moves = false;
        for (let f = 0; f <= 6 && !moves; f++) {
            const t = t0 + (t1 - t0) * f / 6;
            moves = CHANNELS.some(([c]) => Math.abs(evalKeys(keys[c], t) - REST[c]) > EPS[c]);
        }
        if (!moves) continue;
        const end = runs[runs.length - 1];
        if (end && Math.abs(end[1] - t0) < 1e-6) end[1] = t1;
        else runs.push([t0, t1]);
    }
    const merged = [];
    for (const [a, b] of runs.map(([a, b]) => [Math.floor(a), Math.min(LAST, Math.ceil(b))])) {
        const end = merged[merged.length - 1];
        if (end && a - end[1] <= GAP) end[1] = b;
        else merged.push([a, b]);
    }
    return merged;
}

/* The preset laid on the one selected clip, as motion.plan() lays it: the In
   from the clip's first frame, the Out ending on its last, an Emphasis in the
   middle, each at the chosen speed - squeezed to fit a clip too short for
   them - and the rest held in between. {total: seconds, parts: [{t0, t1: the
   move's samples, s0, s1: its seconds on the clip}]}, or null: no clip, or
   nothing of this kind to put on it. */
function clipLayout() {
    const clip = state.selection && state.selection.clip;
    // A Loop repeats over the clip: shown on its own time, one cycle.
    if (ed.view !== "clip" || !clip || !(clip.frames > 1) || !ed.keys || ed.kind === "Loop") return null;
    const fps = Number(state.selection.fps) || 24, speed = Number(state.speed) || 1;
    const total = (clip.frames - 1) / fps;          // its first frame to its last
    // A Whole clip move: stretched over all of it, whatever the speed.
    if (ed.kind === "Whole clip") return {total, parts: [{t0: 0, t1: LAST, s0: 0, s1: total}]};
    const found = {};
    for (const [a, b] of runsOf(ed.keys)) {         // motion.moves
        if (a === 0 && !found.in && (ed.kind === "In · Out" || ed.kind === "In")) found.in = [a, b];
        else if (b === LAST && (ed.kind === "In · Out" || ed.kind === "Out")) found.out = [a, b];
        else if (ed.kind === "Emphasis") {
            found.emphasis = found.emphasis ? [Math.min(found.emphasis[0], a), Math.max(found.emphasis[1], b)] : [a, b];
        }
    }
    const way = state.way || "both";
    const wanted = ed.kind === "In · Out" ? ["in", "out"].filter(m => found[m] && (way === "both" || way === m))
        : ["in", "out", "emphasis"].filter(m => found[m]);
    if (!wanted.length) return null;
    const seconds = m => (found[m][1] - found[m][0]) / LAST * ed.dur / speed;
    const sum = wanted.reduce((acc, m) => acc + seconds(m), 0);
    const squeeze = sum > total ? total / sum : 1;
    const parts = wanted.map(m => {
        const length = seconds(m) * squeeze;
        const first = m === "in" ? 0 : m === "out" ? total - length : (total - length) / 2;
        return {t0: found[m][0], t1: found[m][1], s0: first, s1: first + length};
    }).sort((p, q) => p.s0 - q.s0);
    return {total, parts};
}

/* A preset sample's seconds on the clip, or null where the clip doesn't play
   it (a move the kind leaves out, a stretch of rest squeezed away). */
function onClip(L, t) {
    for (const p of L.parts) {
        if (t >= p.t0 - 1e-9 && t <= p.t1 + 1e-9) {
            return p.s0 + (t - p.t0) / Math.max(1e-9, p.t1 - p.t0) * (p.s1 - p.s0);
        }
    }
    return null;
}

/* The preset sample the clip shows at s seconds: along a move, or - between
   them - held where the last one ended (before the first, where it starts). */
function fromClip(L, s) {
    const parts = L.parts;
    if (s <= parts[0].s0) return parts[0].t0;
    for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        if (s <= p.s1) return p.s1 - p.s0 > 1e-9 ? p.t0 + (s - p.s0) / (p.s1 - p.s0) * (p.t1 - p.t0) : p.t0;
        if (!parts[i + 1] || s < parts[i + 1].s0) return p.t1;
    }
    return parts[parts.length - 1].t1;
}

function respan() {
    ed.span = ed.keys ? playSpan(ed.kind, runsOf(ed.keys), true) : [0, LAST];
    ed.layout = clipLayout();
    const none = ed.span[1] - ed.span[0] <= 0;
    $("ed-kind-note").textContent = !ed.keys || !none ? ""
        : ed.kind === "In" ? "Nothing moves at the start of this curve, so there's no In to play."
        : "Nothing moves at the end of this curve, so there's no Out to play.";
}

// ----------------------------------------------------------- the head card

function drawBase() {
    const select = $("ed-base");
    // Only when the presets change: rebuilt on every state, an open list closed.
    const sig = presets.map(p => `${p.id}\t${p.label}`).join("\n");
    if (select.dataset.sig === sig) {
        select.value = ed.id;
        return;
    }
    select.dataset.sig = sig;
    select.replaceChildren(...packs.map(pack => {
        const list = presets.filter(p => p.pack === pack.id);
        if (!list.length) return null;
        const group = el("optgroup", {label: pack.id});
        group.append(...list.map(p => el("option", {value: p.id, text: p.label, translate: "no"})));
        return group;
    }).filter(Boolean));
    select.value = ed.id;
}

$("ed-base").addEventListener("change", e => loadPreset(e.target.value));
$("ed-name").addEventListener("input", e => { ed.label = e.target.value; ed.dirty = true; drawButtons(); });
$("ed-dur").addEventListener("change", e => {
    const dur = clamp(Number(e.target.value) || ed.dur, 0.2, 10);
    if (dur !== ed.dur) { snapshot(); ed.dur = dur; changed(); }
    e.target.value = ed.dur;
});

function drawKinds() {
    const list = EDIT_KINDS.includes(ed.kind) ? EDIT_KINDS : [...EDIT_KINDS, ed.kind];
    $("ed-kind").replaceChildren(...list.map(k => el("button", {
        type: "button", "aria-pressed": String(ed.kind === k),
        onclick: () => {
            if (ed.kind === k) return;
            snapshot();
            ed.kind = k;
            restart();          // from the start of the part that now plays
            changed();
        },
    }, [k])));
}

function payload() {
    return {label: ($("ed-name").value || "").trim() || "Untitled", kind: ed.kind, dur: ed.dur, keys: ed.keys,
            mask: ed.mask, echo: ed.echo, shine: ed.shine, flip: ed.flip};
}

$("ed-saveas").onclick = () => send("save", payload());
$("ed-update").onclick = () => send("save", {...payload(), id: ed.id});
$("ed-apply").onclick = () => send("apply_draft", payload());
$("ed-revert").onclick = () => loadPreset(ed.id);
$("ed-delete").onclick = async () => {
    const p = presets.find(x => x.id === ed.id);
    if (!p || !(await Buddy.confirm({title: `Delete ${p.label}?`, text: "It goes from Previews and Favorites. Clips it's already on keep their move.",
                                     ok: "Delete", danger: true}))) return;
    ed.dirty = false;
    send("delete", {id: ed.id});
};

function drawButtons() {
    $("ed-delete").hidden = !ed.saved;
    $("ed-update").hidden = !ed.saved;
    $("ed-update").disabled = !ed.dirty || !ed.keys;
    $("ed-saveas").disabled = !ed.keys;
    $("ed-revert").disabled = !ed.dirty;
    $("ed-undo").disabled = !ed.undo.length;
    $("ed-redo").disabled = !ed.redo.length;
    const keys = ed.keys && ed.keys[ed.ch];
}

// ---------------------------------------------------------------- viewer

function drawViewer() {
    if (!ed.viewer) {
        const obj = svg("g");
        $("ed-stage").replaceChildren(stage(obj));
        ed.viewer = {p: {at: null, dur: 1}, obj, head: null};
        reshape();
    }
    $("ed-play").replaceChildren(Buddy.icon(ed.playing ? "pause" : "play"));
}

function editorTick(now) {
    if (!ed.keys) return;
    if (ed.playing) {
        // Laid on a clip: its whole length, as it'll play there. On the
        // preset's own time: only the part the kind puts on clips.
        const speed = Number(state.speed) || 1;
        const fps = Number(state.selection?.fps) || 24;
        const [from, length, rate] = ed.layout ? [0, ed.layout.total, 1]
            : [ed.span[0] / LAST * ed.dur, (ed.span[1] - ed.span[0]) / LAST * ed.dur / speed, speed];
        const local = length > 0 ? ((now - ed.started) / 1000) % (length + (ed.kind === "Loop" ? 0 : PAUSE)) : 0;
        const shown = Math.min(length, Math.floor(local * fps) / fps);
        ed.head = from + shown * rate;
    }
    ed.head = clamp(ed.head, 0, axisLength());
    ed.viewer.p.at = atKeys(ed.keys);
    // A reveal drawn without a preset's mask is a wipe from the left (motion.mask_of).
    ed.viewer.p.maskNow = ed.mask || {shape: "wipe", axis: "x", soft: 0.04};
    // The rest as their presets have them - or, drawn new, as motion.py's defaults.
    ed.viewer.p.echoNow = ed.echo || {copies: 3, delay: 0.05, fade: 0.55};
    ed.viewer.p.shineNow = ed.shine || {width: 0.18, angle: 20, strength: 0.7, soft: 0.04};
    ed.viewer.p.flipNow = ed.flip || {pivot: "center", depth: 3};
    ed.viewer.p.dur = ed.dur;
    pose(ed.viewer, sampleAt(ed.head));
    $("ed-time").textContent = `${ed.head.toFixed(2)}s / ${axisLength().toFixed(2)}s`;
    // The playhead's frame, as the time bar counts them, of the last one.
    const fps = Number(state.selection?.fps) || 24;
    const frame = `Frame ${Math.round(ed.head * fps)} / ${Math.round(axisLength() * fps)}`;
    if ($("ed-frame").textContent !== frame) $("ed-frame").textContent = frame;
    placeHead();
}

$("ed-play").onclick = () => {
    ed.playing = !ed.playing;
    if (ed.playing) {
        let into = ed.head;                         // seconds into the loop
        if (!ed.layout) {
            const a = ed.span[0] / LAST * ed.dur, b = ed.span[1] / LAST * ed.dur;
            into = ed.head >= a && ed.head <= b ? (ed.head - a) / (Number(state.speed) || 1) : 0;
        }
        ed.started = performance.now() - into * 1000;
    }
    drawViewer();
};

// ----------------------------------------------------------------- graph

const graph = $("ed-graph");
let W = 600, H = 340;

// The time across the graph: the preset's own seconds, or the selected
// clip's with the preset laid on it (clipLayout). X takes a preset sample -
// null where the clip doesn't show it - and T gives one back.
const axisLength = () => ed.layout ? ed.layout.total : ed.dur;
const XS = s => PAD.l + s / axisLength() * (W - PAD.l - PAD.r);
const SX = x => (x - PAD.l) / (W - PAD.l - PAD.r) * axisLength();
const secondsOf = t => ed.layout ? onClip(ed.layout, t) : t / LAST * ed.dur;
const sampleAt = sec => ed.layout ? fromClip(ed.layout, sec) : sec / ed.dur * LAST;
const X = t => { const sec = secondsOf(t); return sec === null ? null : XS(sec); };
const T = x => sampleAt(clamp(SX(x), 0, axisLength()));
// Value: the edited channel's, or c's - each shown channel on its own range.
const rangeOf = c => c === ed.ch ? ed.range : ed.ranges[c];
const Y = (v, c = ed.ch) => { const [lo, hi] = rangeOf(c); return PAD.t + (hi - v) / (hi - lo) * (H - PAD.t - PAD.b); };
const V = (y, c = ed.ch) => { const [lo, hi] = rangeOf(c); return hi - (y - PAD.t) / (H - PAD.t - PAD.b) * (hi - lo); };

function niceStep(span, count) {
    const raw = span / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    return [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
}

// A whole number of frames between the time bar's marks, about count of them.
function frameStep(frames, count) {
    const raw = frames / count, mag = Math.pow(10, Math.max(0, Math.floor(Math.log10(Math.max(raw, 1)))));
    return [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
}

function pathOf(keys, y) {
    if (ed.layout) return clipPath(keys, y);
    let d = `M${X(keys[0][0])} ${y(keys[0][1])}`;
    for (let i = 0; i + 1 < keys.length; i++) {
        const p = controls(keys[i], keys[i + 1]);
        d += ` C${X(p[1][0])} ${y(p[1][1])} ${X(p[2][0])} ${y(p[2][1])} ${X(p[3][0])} ${y(p[3][1])}`;
    }
    return d;
}

/* A channel laid on the clip: each move traced along its seconds, the
   holds between them straight, as Fusion draws a spline with no keys there. */
function clipPath(keys, y) {
    const L = ed.layout, points = [];
    const first = L.parts[0], last = L.parts[L.parts.length - 1];
    if (first.s0 > 0) points.push([0, evalKeys(keys, first.t0)]);
    for (const p of L.parts) {
        for (let i = 0; i <= 48; i++) {
            points.push([p.s0 + (p.s1 - p.s0) * i / 48, evalKeys(keys, p.t0 + (p.t1 - p.t0) * i / 48)]);
        }
    }
    if (last.s1 < L.total) points.push([L.total, evalKeys(keys, last.t1)]);
    return "M" + points.map(([sec, v]) => `${XS(sec)} ${y(v)}`).join(" L");
}

const still = c => ed.keys[c].every(k => Math.abs(k[1] - REST[c]) < 1e-6 && (!k[2] || Math.abs(k[2][1] - REST[c]) < 1e-6)
                                       && (!k[3] || Math.abs(k[3][1] - REST[c]) < 1e-6));

function placeHead() {
    const x = XS(ed.head);
    const line = graph.querySelector(".ed-head-line"), knob = graph.querySelector(".ed-head-knob");
    if (line) { line.setAttribute("x1", x); line.setAttribute("x2", x); }
    if (knob) knob.setAttribute("transform", `translate(${x} 0)`);
}

function drawGraph() {
    W = graph.clientWidth || W;
    H = graph.clientHeight || H;
    graph.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const kids = [];
    if (!ed.keys) {
        const t = svg("text", {class: "ed-axis", x: W / 2, y: H / 2, "text-anchor": "middle"});
        t.textContent = "Reading the curves…";
        graph.replaceChildren(t);
        $("ed-keymenu").hidden = true;
        return;
    }
    graph.style.setProperty("--ch", `var(--ch-${ed.ch})`);
    // The time bar: seconds, and the playhead's knob. Dragging here - and
    // only here - moves the playhead.
    kids.push(svg("rect", {class: "ed-bar", x: PAD.l, y: BAR.top, width: W - PAD.l - PAD.r, height: BAR.height,
                           rx: 4, "data-bar": "1"}));
    // Keys the clip doesn't show (a part the kind leaves out) aren't drawn, or selectable.
    for (const c of Object.keys(ed.sels)) {
        const keep = ed.on.includes(c) ? ed.sels[c].filter(i => ed.keys[c][i] && X(ed.keys[c][i][0]) !== null) : [];
        if (keep.length) ed.sels[c] = keep;
        else delete ed.sels[c];
    }
    if (!ed.sel.length && selChannels().length) { activate(selChannels()[0]); drawChannels(); }
    // Time in frames, at the timeline's frame rate: on a clip, its first frame to its last.
    const fps = Number(state.selection?.fps) || 24;
    const frames = axisLength() * fps;
    const fStep = frameStep(frames, 8);
    for (let f = 0; f <= frames + 1e-6; f += fStep) {
        const x = XS(f / fps);
        kids.push(svg("line", {class: "ed-grid", x1: x, y1: PAD.t, x2: x, y2: H - PAD.b}));
        kids.push(svg("line", {class: "ed-tick", x1: x, y1: BAR.top + BAR.height - 5, x2: x, y2: BAR.top + BAR.height}));
        const label = svg("text", {class: "ed-axis ed-bar-label", x: x + 3, y: BAR.top + 14});
        label.textContent = String(f);
        kids.push(label);
    }
    // Value: the channel's own units up the side.
    const vStep = niceStep(ed.range[1] - ed.range[0], 6);
    for (let v = Math.ceil(ed.range[0] / vStep) * vStep; v <= ed.range[1]; v += vStep) {
        const y = Y(v);
        kids.push(svg("line", {class: "ed-grid", x1: PAD.l, y1: y, x2: W - PAD.r, y2: y}));
        const label = svg("text", {class: `ed-axis${ed.on.length > 1 ? " ed-value" : ""}`, x: PAD.l - 6, y: y + 3, "text-anchor": "end"});
        label.textContent = SHOW[ed.ch](Math.abs(v) < 1e-9 ? 0 : v);
        kids.push(label);
    }
    kids.push(svg("line", {class: "ed-rest", x1: PAD.l, y1: Y(REST[ed.ch]), x2: W - PAD.r, y2: Y(REST[ed.ch])}));
    // The channels not shown that move: muted and faint behind, in their
    // colours, each on its own range as it would be shown - never stretched
    // to fill the height, which made Jelly's 6% height wobble look like 25%
    // behind its width.
    for (const [c] of CHANNELS) {
        if (ed.on.includes(c) || still(c)) continue;
        kids.push(svg("path", {class: "ed-ghost", d: pathOf(ed.keys[c], v => Y(v, c)), style: `--ch: var(--ch-${c})`}));
    }
    // The other channels shown, in their colours, on their own ranges: a
    // press on one of their keys or curves edits that channel.
    for (const c of ed.on) {
        if (c === ed.ch) continue;
        const y = v => Y(v, c), style = `--ch: var(--ch-${c})`, d = pathOf(ed.keys[c], y);
        kids.push(svg("path", {class: "ed-curve other", d, style}));
        kids.push(svg("path", {class: "ed-hit", d, "data-ch": c}));
        ed.keys[c].forEach((key, i) => X(key[0]) !== null && kids.push(svg("circle", {
            class: `ed-key other${(ed.sels[c] || []).includes(i) ? " sel" : ""}`, cx: X(key[0]), cy: y(key[1]), r: 4.5,
            "data-key": i, "data-ch": c, style,
        })));
    }
    const keys = ed.keys[ed.ch];
    kids.push(svg("path", {class: "ed-curve", d: pathOf(keys, Y)}));
    if (ed.snap !== null && X(ed.snap) !== null) {
        kids.push(svg("line", {class: "ed-snap", x1: X(ed.snap), y1: PAD.t, x2: X(ed.snap), y2: H - PAD.b}));
    }
    // What the kind leaves out - an In's Out, an Out's In - dimmed.
    const [a, b] = ed.layout ? [0, LAST] : ed.span;      // on a clip, what it leaves out isn't drawn at all
    for (const [from, to] of [[0, a], [b, LAST]]) {
        if (to - from <= 0) continue;
        kids.push(svg("rect", {class: "ed-unused", x: X(from), y: PAD.t, width: X(to) - X(from), height: H - PAD.t - PAD.b}));
    }
    kids.push(svg("line", {class: "ed-head-line", x1: XS(ed.head), y1: BAR.top, x2: XS(ed.head), y2: H - PAD.b}));
    kids.push(svg("g", {class: "ed-head-knob", transform: `translate(${XS(ed.head)} 0)`}, [
        svg("path", {d: `M-6 ${BAR.top + 1} h12 v9 l-6 6 l-6 -6 z`}),
    ]));
    const one = single(), k = keys[one];
    if (k) {
        for (const [side, h] of [["lh", k[2]], ["rh", k[3]]]) {
            if (!h || X(h[0]) === null) continue;
            kids.push(svg("line", {class: "ed-arm", x1: X(k[0]), y1: Y(k[1]), x2: X(h[0]), y2: Y(h[1])}));
            kids.push(svg("circle", {class: "ed-hand", cx: X(h[0]), cy: Y(h[1]), r: 4.5, "data-hand": side, "data-i": one}));
        }
    }
    keys.forEach((key, i) => X(key[0]) !== null && kids.push(svg("circle", {
        class: `ed-key${ed.sel.includes(i) ? " sel" : ""}`, cx: X(key[0]), cy: Y(key[1]), r: 5.5, "data-key": i, "data-ch": ed.ch,
    })));
    const box = ed.drag && ed.drag.kind === "box" && ed.drag.rect;
    if (box) kids.push(svg("rect", {class: "ed-box", ...box}));
    graph.replaceChildren(...kids);
    drawKeyMenu();
}

/* Clip or Preset: shown while exactly one clip that takes a preset is selected. */
function drawView() {
    const clip = state.selection && state.selection.clip;
    const host = $("ed-view");
    host.hidden = !(clip && clip.frames > 1);
    if (host.hidden) return;
    const fps = Number(state.selection.fps) || 24;
    const items = [
        ["clip", `Clip ${(clip.frames / fps).toFixed(1)}s`, `Laid out at ${clip.name}'s length, as it'll play there`],
        ["preset", `Preset ${ed.dur.toFixed(1)}s`, "The preset's own timing"],
    ];
    host.replaceChildren(...items.map(([view, label, title]) => el("button", {
        type: "button", "aria-pressed": String(ed.view === view), title,
        onclick: () => {
            if (ed.view === view) return;
            ed.view = view;
            clearSel();
            ed.head = 0;
            restart();
            drawEditor();
        },
    }, [label])));
}

/* The channel buttons: each shows its channel or hides it, any number at
   once, the one turned on edited. Ctrl-click shows that one alone; a
   right-click picks its colour. */
function drawChannels() {
    $("ed-channels").replaceChildren(...CHANNELS.map(([c, label]) => el(
        `button${ed.keys && still(c) ? ".ed-still" : ""}${ed.on.length > 1 && ed.ch === c ? ".ed-editing" : ""}`, {
            type: "button", "aria-pressed": String(ed.on.includes(c)), style: `--ch: var(--ch-${c})`,
            title: ed.keys && still(c)
                ? `${label} doesn't move – drag its line to make it. Click to show or hide it, Ctrl-click to show it alone, right-click for its colour`
                : `${label}. Click to show or hide it, Ctrl-click to show it alone, right-click for its colour`,
            onclick: e => showChannel(c, e.ctrlKey || e.metaKey),
            oncontextmenu: e => { e.preventDefault(); colourMenu(c, label, e.clientX, e.clientY); },
        }, [el("span.ed-dot"), label])));
}

function showChannel(c, alone) {
    if (!ed.keys) return;
    if (alone) {
        ed.on = [c];
        ed.sels = {[c]: ed.sels[c] || []};          // a hidden channel's keys aren't selected
    } else if (!ed.on.includes(c)) {
        ed.on = CHANNELS.map(([k]) => k).filter(k => k === c || ed.on.includes(k));
    } else {
        if (ed.on.length === 1) return;             // one stays shown
        ed.on = ed.on.filter(k => k !== c);
        delete ed.sels[c];
        if (ed.ch === c) activate(selChannels()[0] || ed.on[0]);
        return drawEditor();
    }
    activate(c);                                    // turned on: it's the one edited
    drawEditor();
}

// The colours on offer for a channel, besides its own and one picked freely.
const SWATCHES = [["Red", "#ff5a5f"], ["Orange", "#ff8a3d"], ["Amber", "#ffb21e"], ["Lime", "#a3e635"],
                  ["Green", "#3ddc84"], ["Teal", "#2dd4bf"], ["Sky", "#38bdf8"], ["Blue", "#3ea6ff"],
                  ["Purple", "#d07bff"], ["Pink", "#ff6fb5"]];

function colourMenu(c, label, x, y) {
    const now = (state.colors || {})[c];
    Buddy.menu({x, y, items: [
        {heading: `${label} colour`},
        ...SWATCHES.map(([name, hex]) => ({label: name, swatch: hex, checked: now === hex, onClick: () => setColour(c, hex)})),
        {sep: true},
        {label: "Custom colour…", onClick: () => pickColour(c)},
        {label: "Default colour", disabled: !now, onClick: () => setColour(c, null)},
    ]});
}

// The system's colour picker, through an unseen colour field: a picker left
// open from before is taken away first.
function pickColour(c) {
    for (const old of document.querySelectorAll("input.ed-picker")) old.remove();
    const input = el("input.ed-picker", {type: "color", value: (state.colors || {})[c] ||
        getComputedStyle($("panel-editor")).getPropertyValue(`--ch-${c}`).trim() || "#ffffff"});
    input.style.cssText = "position:fixed;opacity:0;pointer-events:none;left:50%;top:50%;width:1px;height:1px";
    input.oninput = () => paintColour(c, input.value);
    input.onchange = () => { setColour(c, input.value); input.remove(); };
    document.body.append(input);
    input.click();
}

function paintColour(c, hex) {
    const panel = $("panel-editor");
    if (hex) panel.style.setProperty(`--ch-${c}`, hex);
    else panel.style.removeProperty(`--ch-${c}`);
}

function setColour(c, hex) {
    state.colors = {...(state.colors || {}), [c]: hex};
    if (!hex) delete state.colors[c];
    paintColours();
    send("color", {channel: c, color: hex});
}

/* The colours picked for channels, over the defaults (animation.css). */
function paintColours() {
    for (const [c] of CHANNELS) paintColour(c, (state.colors || {})[c]);
}

function drawEditor() {
    if ($("panel-editor").hidden) return;
    drawBase();
    drawKinds();
    if (document.activeElement !== $("ed-name")) $("ed-name").value = ed.label;
    if (document.activeElement !== $("ed-dur")) $("ed-dur").value = ed.dur;
    respan();
    drawViewer();
    drawView();
    drawChannels();
    drawGraph();
    drawButtons();
    drawSelection();
}

new ResizeObserver(() => { if (!$("panel-editor").hidden) drawGraph(); }).observe($("ed-graph-wrap"));

// ------------------------------------------------------------- dragging

function point(e) {
    const r = graph.getBoundingClientRect();
    return [(e.clientX - r.left) * W / r.width, (e.clientY - r.top) * H / r.height];
}

// A double-click is told apart here, not by "dblclick": the graph is drawn
// afresh on every press, so the two clicks never land on the same element.
let lastPress = {at: 0, x: -99, y: -99};

function scrub(x) {
    ed.playing = false;
    ed.head = clamp(SX(x), 0, axisLength());
    drawViewer();
    placeHead();
}

graph.addEventListener("pointerdown", e => {
    if (!ed.keys || e.button !== 0) return;
    graph.focus();
    const hit = e.target.dataset || {};
    const [px, py] = point(e);
    graph.setPointerCapture(e.pointerId);
    if (py < PAD.t - 4) {                               // the time bar
        ed.drag = {kind: "head"};
        scrub(px);
        return;
    }
    // A key or the curve of another channel shown: that one's edited now.
    if (hit.ch && hit.ch !== ed.ch) {
        activate(hit.ch);
        drawChannels();
    }
    const double = e.timeStamp - lastPress.at < 400 && Math.hypot(px - lastPress.x, py - lastPress.y) < 6;
    lastPress = {at: double ? 0 : e.timeStamp, x: px, y: py};
    if (double && hit.key === undefined && !hit.hand) {
        ed.drag = null;
        addKey(T(px));
        return;
    }
    // Ctrl (Cmd on a Mac) or Shift: a click adds a key or takes it out, a box adds -
    // keys of any channel shown, to move together. Shift on a key also drags
    // it straight away, in time only: a Shift-click that never moves takes an
    // already selected key out when it lets go.
    const adding = e.shiftKey || e.ctrlKey || e.metaKey;
    const keysDrag = extra => ({kind: "keys", x0: px, y0: py, orig: clone(ed.keys), moved: false, grab: Number(hit.key), ...extra});
    if (hit.key !== undefined) {
        const i = Number(hit.key);
        if (e.shiftKey && !e.ctrlKey && !e.metaKey) {
            const was = ed.sel.includes(i);
            if (!was) ed.sel = [...ed.sel, i].sort((a, b) => a - b);
            ed.drag = keysDrag({untick: was ? i : null});
        } else if (adding) {
            ed.sel = ed.sel.includes(i) ? ed.sel.filter(j => j !== i) : [...ed.sel, i].sort((a, b) => a - b);
            // Its last key taken out: the channel edited is one still with keys selected.
            if (!ed.sel.length && selChannels().length) { activate(selChannels()[0]); drawChannels(); }
            ed.drag = null;
        } else {
            if (!ed.sel.includes(i)) { clearSel(); ed.sel = [i]; }
            ed.drag = keysDrag({});
        }
    } else if (hit.hand) {
        ed.drag = {kind: "hand", i: Number(hit.i), side: hit.hand, moved: false};
    } else {                                            // a box around the keys to select
        ed.drag = {kind: "box", x0: px, y0: py, add: adding, before: clone(ed.sels), rect: null};
        if (!adding) clearSel();
    }
    drawGraph();
    drawButtons();
});

graph.addEventListener("pointermove", e => {
    const d = ed.drag;
    if (!d) return;
    const [x, y] = point(e);
    if (d.kind === "head") return scrub(x);
    if (d.kind === "box") {
        const x0 = Math.min(d.x0, x), x1 = Math.max(d.x0, x), y0 = Math.min(d.y0, y), y1 = Math.max(d.y0, y);
        d.rect = {x: x0, y: y0, width: x1 - x0, height: y1 - y0};
        const within = c => ed.keys[c].map((k, i) => [X(k[0]), Y(k[1], c), i])
            .filter(([kx, ky]) => kx !== null && kx >= x0 && kx <= x1 && ky >= y0 && ky <= y1).map(([, , i]) => i);
        // The keys in the box, of every channel shown.
        ed.sels = {};
        for (const c of ed.on) {
            const keys = [...new Set([...(d.add ? d.before[c] || [] : []), ...within(c)])].sort((a, b) => a - b);
            if (keys.length) ed.sels[c] = keys;
        }
        // The edited channel is one with keys in it, if any are.
        if (!ed.sel.length && selChannels().length) { activate(selChannels()[0]); drawChannels(); }
        drawGraph();
        drawButtons();
        return;
    }
    // A click's wobble isn't a drag: keys move once the pointer has come 3 px.
    if (!d.moved && d.kind === "keys" && Math.hypot(x - d.x0, y - d.y0) < 3) return;
    if (!d.moved) { snapshot(); d.moved = true; ed.dirty = true; }
    if (d.kind === "keys") moveKeys(d, x, y, e.shiftKey, e.ctrlKey || e.metaKey);
    else moveHandle(d, x, y, e.altKey);
    drawGraph();
});

/* The selected keys, together, by how far the pointer has come - from where
   they were when the drag began, so nothing drifts. None passes a key that
   isn't moving, the first and last stay on their frames. Shift keeps them
   all at their values: they slide along in time only. Keys of several
   channels move by the same time, and up or down by the same distance on
   the graph - each in its own channel's units. The key dragged snaps to the
   time of another shown channel's key, one not moving, it comes within 6 px
   of - unless Ctrl is held. */
function moveKeys(d, x, y, lockValue, noSnap) {
    const chans = selChannels();
    let pinnedEnd = false, lo = -Infinity, hi = Infinity;
    for (const c of chans) {
        const orig = d.orig[c], n = orig.length, picked = new Set(ed.sels[c]);
        if (picked.has(0) || picked.has(n - 1)) pinnedEnd = true;
        for (const i of picked) {
            if (i > 0 && !picked.has(i - 1)) lo = Math.max(lo, orig[i - 1][0] + 0.05 - orig[i][0]);
            if (i < n - 1 && !picked.has(i + 1)) hi = Math.min(hi, orig[i + 1][0] - 0.05 - orig[i][0]);
        }
    }
    let dt = pinnedEnd ? 0 : T(x) - T(d.x0);
    dt = clamp(dt, Math.min(lo, 0), Math.max(hi, 0));
    ed.snap = null;
    const grabbed = d.orig[ed.ch][d.grab];
    const at = grabbed ? X(grabbed[0] + dt) : null;
    if (!noSnap && !pinnedEnd && at !== null) {
        let best = null;
        for (const c of ed.on) {
            if (c === ed.ch) continue;
            const moving = new Set(ed.sels[c] || []);
            ed.keys[c].forEach((k, i) => {
                if (moving.has(i)) return;
                const kx = X(k[0]), off = kx === null ? Infinity : Math.abs(kx - at);
                const shift = k[0] - grabbed[0];
                if (off < 6 && shift >= Math.min(lo, 0) && shift <= Math.max(hi, 0) && (!best || off < best[0])) best = [off, k[0], shift];
            });
        }
        if (best) { dt = best[2]; ed.snap = best[1]; }
    }
    for (const c of chans) {
        const keys = clone(d.orig[c]);
        const dv = lockValue ? 0 : V(y, c) - V(d.y0, c);
        for (const i of ed.sels[c]) {
            const k = keys[i];
            k[0] += dt;
            k[1] += dv;
            for (const h of [k[2], k[3]]) if (h) { h[0] += dt; h[1] += dv; }
        }
        // Every handle stays on its own side, inside its neighbours.
        keys.forEach((k, i) => {
            const prev = keys[i - 1], next = keys[i + 1];
            if (k[2]) k[2][0] = clamp(k[2][0], prev ? prev[0] : k[0], k[0]);
            if (k[3]) k[3][0] = clamp(k[3][0], k[0], next ? next[0] : k[0]);
        });
        ed.keys[c] = keys;
    }
}

function moveHandle(d, x, y, broken) {
    const keys = ed.keys[ed.ch], k = keys[d.i];
    const prev = keys[d.i - 1], next = keys[d.i + 1];
    const own = d.side === "lh" ? 2 : 3, other = 5 - own;
    const lo = own === 2 ? (prev ? prev[0] : k[0]) : k[0];
    const hi = own === 2 ? k[0] : (next ? next[0] : k[0]);
    const h = [clamp(T(x), lo, hi), V(y)];
    k[own] = h;
    // Its partner turns with it - smooth through the key - unless Alt breaks
    // them. Lengths compared as the graph shows them.
    if (!broken && k[other]) {
        const sx = LAST, sy = ed.range[1] - ed.range[0];
        const dx = (h[0] - k[0]) / sx, dy = (h[1] - k[1]) / sy, len = Math.hypot(dx, dy);
        const o = k[other], olen = Math.hypot((o[0] - k[0]) / sx, (o[1] - k[1]) / sy);
        if (len > 1e-9) {
            const olo = other === 2 ? (prev ? prev[0] : k[0]) : k[0];
            const ohi = other === 2 ? k[0] : (next ? next[0] : k[0]);
            k[other] = [clamp(k[0] - dx / len * olen * sx, olo, ohi), k[1] - dy / len * olen * sy];
        }
    }
}

function endDrag() {
    const d = ed.drag;
    ed.drag = null;
    ed.snap = null;
    if (!d) return;
    // A Shift-click on a selected key, never dragged: it comes out.
    if (!d.moved && d.untick != null) {
        ed.sel = ed.sel.filter(j => j !== d.untick);
        if (!ed.sel.length && selChannels().length) { activate(selChannels()[0]); drawChannels(); }
        drawButtons();
    }
    // A click without a drag still ends one: the menu, hidden while the
    // pointer was down, comes back for what's selected now.
    if (d.kind === "box" || !d.moved) return drawGraph();
    // Dragged past the graph's edge: refit it to show the whole curve.
    const moved = d.kind === "keys" ? selChannels().map(c => [c, ed.sels[c]]) : [[ed.ch, [d.i]]];
    const outside = moved.some(([c, sel]) => sel.some(i => {
        const k = ed.keys[c][i], [lo, hi] = rangeOf(c);
        return [k[1], k[2] && k[2][1], k[3] && k[3][1]].some(v => v != null && (v < lo || v > hi));
    }));
    if (outside) fitRange();
    changed();
}
graph.addEventListener("pointerup", endDrag);
graph.addEventListener("pointercancel", endDrag);

function addKey(t) {
    t = clamp(t, 0, LAST);
    if (t <= 0.05 || t >= LAST - 0.05) return;
    snapshot();
    const at = splitAt(ed.keys[ed.ch], t);
    clearSel();
    ed.sel = [at];
    changed();
}

/* The selected keys, channel by channel: [keys, their selected indexes]. */
const eachSelected = () => selChannels().map(c => [ed.keys[c], ed.sels[c]]);

/* Takes out the selected keys, of every channel - never a curve's end keys. */
function removeKeys() {
    if (!ed.keys) return;
    const gone = eachSelected().map(([keys, sel]) => [keys, sel.filter(i => i > 0 && i < keys.length - 1)]);
    if (!gone.some(([, sel]) => sel.length)) return;
    snapshot();
    for (const [keys, sel] of gone) for (const i of [...sel].sort((a, b) => b - a)) keys.splice(i, 1);
    clearSel();
    changed();
}

function ease(kind) {
    if (!ed.keys || !selCount()) return;
    snapshot();
    for (const [keys, sel] of eachSelected()) for (const i of sel) {
        const k = keys[i], prev = keys[i - 1], next = keys[i + 1];
        const left = prev ? (k[0] - prev[0]) / 3 : 0, right = next ? (next[0] - k[0]) / 3 : 0;
        if (kind === "linear") {
            k[2] = prev ? [k[0] - left, k[1] + (prev[1] - k[1]) / 3] : null;
            k[3] = next ? [k[0] + right, k[1] + (next[1] - k[1]) / 3] : null;
        } else {
            const slope = kind === "smooth" && prev && next ? (next[1] - prev[1]) / (next[0] - prev[0]) : 0;
            k[2] = prev ? [k[0] - left, k[1] - slope * left] : null;
            k[3] = next ? [k[0] + right, k[1] + slope * right] : null;
        }
    }
    changed();
}

// --------------------------------------------------------------- key menu
// A small bar beside the selected keys: one key gets its own ease, moving to
// the playhead and copying its ease; several also loop and reverse.

const MENU_GLYPHS = {
    smooth: '<path d="M3 18C9 18 15 6 21 6"/><path d="M6.5 16l11-8" opacity=".55"/><circle cx="12" cy="12" r="1.8"/>',
    flat: '<path d="M3 18c5 0 5-8 9-8s4 8 9 8"/><path d="M5.5 10h13" opacity=".55"/><circle cx="12" cy="10" r="1.8"/>',
    linear: '<path d="M3 18l9-10 9 10"/><circle cx="12" cy="8" r="1.8"/>',
    toHead: '<path d="M16 4v16"/><path d="M3 12h9M9 9l3 3-3 3"/>',
    endToHead: '<path d="M8 4v16"/><path d="M21 12h-9M15 9l-3 3 3 3"/>',
    paste: '<rect x="5.5" y="5" width="13" height="16" rx="2"/><path d="M9 5V3.5h6V5"/><path d="M8.5 15c2.2 0 2.2-4 3.5-4s1.3 4 3.5 4"/>',
    unease: '<path d="M3 18l9-10 9 10"/><path d="M4 4l16 16"/>',
    grip: '<circle cx="9" cy="6" r="1.3"/><circle cx="15" cy="6" r="1.3"/><circle cx="9" cy="12" r="1.3"/>' +
          '<circle cx="15" cy="12" r="1.3"/><circle cx="9" cy="18" r="1.3"/><circle cx="15" cy="18" r="1.3"/>',
    loopEven: '<path d="M17 3l3 3-3 3"/><path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H20"/><path d="M7 21l-3-3 3-3"/>' +
              '<path d="M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H4"/><path d="M9.5 10.5h5M9.5 13.5h5"/>',
    loop: '<path d="M17 3l3 3-3 3"/><path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H20"/><path d="M7 21l-3-3 3-3"/><path d="M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H4"/>',
};

function menuIcon(name) {
    if (!MENU_GLYPHS[name]) return Buddy.icon(name);
    const node = svg("svg", {viewBox: "0 0 24 24", class: "icon-svg", "aria-hidden": "true"});
    node.innerHTML = MENU_GLYPHS[name];
    return node;
}

/* The selected keys' first and last index, and whether either end key of the
   curve (which never leaves its frame) is among them. */
function picked() {
    const keys = ed.keys[ed.ch];
    const lo = Math.min(...ed.sel), hi = Math.max(...ed.sel);
    return {keys, lo, hi, ends: lo === 0 || hi === keys.length - 1};
}

const pinned = "The first and last keys stay on their frames";

function drawKeyMenu() {
    const menu = $("ed-keymenu");
    if (!ed.keys || !ed.sel.length || (ed.drag && ed.drag.kind !== "head")) {
        menu.hidden = true;
        return;
    }
    const {keys, lo, hi} = picked();
    const one = selCount() === 1, cross = selChannels().length > 1;
    const ends = eachSelected().some(([k, sel]) => sel.includes(0) || sel.includes(k.length - 1));
    const inner = eachSelected().some(([k, sel]) => sel.some(i => i > 0 && i < k.length - 1));
    const oneChannel = "Loop, Loop evenly and Reverse work on one channel's keys at a time";
    const button = (icon, title, act, disabled) => el("button.ed-km-btn", {
        type: "button", title, "aria-label": title, disabled: !!disabled,
        onclick: () => act(),
    }, [menuIcon(icon)]);
    const gap = () => el("span.ed-km-sep");
    const shapes = [         // not "ease": that would hide ease(), which these call
        button("smooth", "Smooth", () => ease("smooth")),
        button("flat", "Flat", () => ease("flat")),
        button("linear", "Linear", () => ease("linear")),
    ];
    const parts = one ? [
        ...shapes, gap(),
        button("toHead", ends ? pinned : "Move to playhead", () => toHead("start"), ends),
        gap(),
        button("copy", "Copy ease", copyEase),
        button("paste", ed.ease ? "Paste ease" : "Copy a key's ease first", pasteEase, !ed.ease),
        button("unease", "Delete ease", deleteEase),
        gap(),
        button("trash", inner ? "Delete key" : pinned, removeKeys, !inner),
    ] : [
        ...shapes, gap(),
        button("toHead", ends ? pinned : "Move start to playhead", () => toHead("start"), ends),
        button("endToHead", ends ? pinned : "Move end to playhead", () => toHead("end"), ends),
        gap(),
        button("paste", ed.ease ? "Paste ease" : "Copy a key's ease first", pasteEase, !ed.ease),
        button("unease", "Delete ease", deleteEase),
        gap(),
        ...(cross ? [
            button("loop", oneChannel, loopKeys, true),
            button("loopEven", oneChannel, loopEvenly, true),
            button("swap", oneChannel, reverseKeys, true),
        ] : [
            button("loop", loopTimes() ? "Loop: repeat these keys to fill the space after them" : "No room after these keys to repeat them",
                   loopKeys, !loopTimes()),
            button("loopEven", evenPasses() ? "Loop evenly: repeat these keys a whole number of times, evened out to fill the space to the next key exactly"
                                            : "No key after these to loop up to", loopEvenly, !evenPasses()),
            button("swap", "Reverse order", reverseKeys, keys[hi][0] - keys[lo][0] <= 0),
        ]),
        gap(),
        button("trash", inner ? "Delete keys" : pinned, removeKeys, !inner),
    ];
    const grip = el("span.ed-km-grip", {title: "Drag to move this menu – double-click to put it back by the keys"},
                    [menuIcon("grip")]);
    menu.replaceChildren(grip, ...parts);
    menu.hidden = false;
    placeKeyMenu(menu);
}

/* Beside the selection, never over it: above, below, to the right or left of
   the selected keys (and the handles shown), else a corner of the graph -
   the first of those that covers none of them, and of those the one over the
   fewest other keys. Pushed back inside the graph, "above" can land on the
   very keys it's for, so every spot is checked where it really ends up. */
function placeKeyMenu(menu) {
    const w = menu.offsetWidth, h = menu.offsetHeight, near = 10, gap = 14;
    if (ed.menuAt) {                    // where it was dragged to, kept until a double-click on the grip
        menu.style.left = `${clamp(ed.menuAt[0], 0, W - w)}px`;
        menu.style.top = `${clamp(ed.menuAt[1], 0, H - h)}px`;
        return;
    }
    // The selected keys (and the handles shown) of every channel, and the
    // other keys of the channels shown, where they're drawn.
    const shown = p => p[0] !== null;
    const mine = [], others = [];
    for (const c of ed.on) {
        const at = p => [X(p[0]), Y(p[1], c)], sel = ed.sels[c] || [];
        ed.keys[c].forEach((k, i) => {
            if (!sel.includes(i)) return others.push(at(k));
            for (const p of c === ed.ch && single() === i ? [k, k[2], k[3]] : [k]) if (p) mine.push(at(p));
        });
    }
    for (const list of [mine, others]) list.splice(0, list.length, ...list.filter(shown));
    const xs = mine.map(p => p[0]), ys = mine.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const cx = (x0 + x1) / 2 - w / 2, cy = (y0 + y1) / 2 - h / 2;
    const spots = [
        [cx, y0 - h - gap], [cx, y1 + gap], [x1 + gap, cy], [x0 - w - gap, cy],
        [x1 + gap, y0 - h - gap], [x0 - w - gap, y0 - h - gap], [x1 + gap, y1 + gap], [x0 - w - gap, y1 + gap],
        [4, PAD.t], [W - w - 4, PAD.t], [4, H - h - 4], [W - w - 4, H - h - 4],
    ].map(([l, t]) => [clamp(l, 4, W - w - 4), clamp(t, PAD.t, H - h - 4)]);
    const under = ([l, t], points) => points.filter(([x, y]) =>
        x > l - near && x < l + w + near && y > t - near && y < t + h + near).length;
    const score = (spot, order) => under(spot, mine) * 1000 + under(spot, others) + order * 0.01;
    const best = spots.reduce((a, s, i) => score(s, i) < score(a.s, a.i) ? {s, i} : a, {s: spots[0], i: 0}).s;
    menu.style.left = `${best[0]}px`;
    menu.style.top = `${best[1]}px`;
}

// The grip: drag the menu anywhere over the graph and it stays there for
// every selection after; a double-click puts it back beside the keys. On the
// menu itself, not the grip - its buttons are drawn afresh, the menu isn't.
const keyMenu = $("ed-keymenu");
let menuDrag = null, gripPressed = 0;

keyMenu.addEventListener("pointerdown", e => {
    if (e.button !== 0 || !e.target.closest(".ed-km-grip")) return;
    e.preventDefault();
    // A double-click told apart here: the drag's pointer capture sends the
    // second click to the menu, not the grip, so "dblclick" can't name it.
    if (e.timeStamp - gripPressed < 400) {
        gripPressed = 0;
        ed.menuAt = null;
        return drawKeyMenu();
    }
    gripPressed = e.timeStamp;
    menuDrag = {dx: e.clientX - keyMenu.offsetLeft, dy: e.clientY - keyMenu.offsetTop};
    keyMenu.setPointerCapture(e.pointerId);
    keyMenu.classList.add("moving");
});
keyMenu.addEventListener("pointermove", e => {
    if (!menuDrag) return;
    ed.menuAt = [clamp(e.clientX - menuDrag.dx, 0, W - keyMenu.offsetWidth),
                 clamp(e.clientY - menuDrag.dy, 0, H - keyMenu.offsetHeight)];
    keyMenu.style.left = `${ed.menuAt[0]}px`;
    keyMenu.style.top = `${ed.menuAt[1]}px`;
});
const dropMenu = () => { menuDrag = null; keyMenu.classList.remove("moving"); };
keyMenu.addEventListener("pointerup", dropMenu);
keyMenu.addEventListener("pointercancel", dropMenu);

/* Shifts the selected keys in time by dt: never past a key that isn't
   selected, never the curve's end keys. True when they moved all the way. */
function shiftKeys(dt) {
    const all = eachSelected();
    if (all.some(([keys, sel]) => sel.includes(0) || sel.includes(keys.length - 1))) return false;
    let lo = -Infinity, hi = Infinity;
    for (const [keys, sel] of all) {
        const chosen = new Set(sel);
        for (const i of chosen) {
            if (!chosen.has(i - 1)) lo = Math.max(lo, keys[i - 1][0] + 0.05 - keys[i][0]);
            if (!chosen.has(i + 1)) hi = Math.min(hi, keys[i + 1][0] - 0.05 - keys[i][0]);
        }
    }
    const moved = clamp(dt, Math.min(lo, 0), Math.max(hi, 0));
    if (Math.abs(moved) < 1e-9) return Math.abs(dt) < 1e-9;
    snapshot();
    for (const [keys, sel] of all) {
        for (const i of sel) {
            const k = keys[i];
            k[0] += moved;
            for (const h of [k[2], k[3]]) if (h) h[0] += moved;
        }
        tidyHandles(keys);
    }
    changed();
    return Math.abs(moved - dt) < 1e-6;
}

/* The selected keys - of every channel - moved so the first (or the last)
   of them lands on the playhead. */
function toHead(which) {
    const times = eachSelected().flatMap(([keys, sel]) => sel.map(i => keys[i][0]));
    const from = which === "end" ? Math.max(...times) : Math.min(...times);
    if (!shiftKeys(sampleAt(ed.head) - from)) Buddy.toast("They stop at the next key.");
}

/* Every handle on its own side of its key, inside its neighbours. */
function tidyHandles(keys) {
    keys.forEach((k, i) => {
        const prev = keys[i - 1], next = keys[i + 1];
        k[2] = prev && k[2] ? [clamp(k[2][0], prev[0], k[0]), k[2][1]] : null;
        k[3] = next && k[3] ? [clamp(k[3][0], k[0], next[0]), k[3][1]] : null;
    });
}

/* A key's ease as shares of the stretch to each neighbour - how far along
   in time its handle reaches, and how far towards the neighbour's value -
   so it fits any key it's pasted on, in any channel. */
function copyEase() {
    const keys = ed.keys[ed.ch], i = ed.sel[0], k = keys[i];
    const share = (h, n) => {
        if (!h || !n || Math.abs(n[0] - k[0]) < 1e-9) return null;
        const dv = n[1] - k[1];
        return [Math.abs((h[0] - k[0]) / (n[0] - k[0])), Math.abs(dv) > 1e-9 ? (h[1] - k[1]) / dv : 0];
    };
    ed.ease = {l: share(k[2], keys[i - 1]), r: share(k[3], keys[i + 1])};
    Buddy.toast("Copied the ease.");
    drawKeyMenu();
}

function pasteEase() {
    if (!ed.ease) return;
    snapshot();
    for (const [keys, sel] of eachSelected()) {
        for (const i of sel) {
            const k = keys[i], prev = keys[i - 1], next = keys[i + 1];
            const put = (s, n) => s && n ? [k[0] + (n[0] - k[0]) * s[0], k[1] + (n[1] - k[1]) * s[1]] : null;
            k[2] = put(ed.ease.l, prev);
            k[3] = put(ed.ease.r, next);
        }
        tidyHandles(keys);
    }
    changed();
}

function deleteEase() {
    snapshot();
    for (const [keys, sel] of eachSelected()) for (const i of sel) keys[i][2] = keys[i][3] = null;
    changed();
}

/* How many times the selected stretch (first to last selected key) fits in
   the room after it - up to the next key, or the curve's end. */
function loopTimes() {
    const {keys, lo, hi} = picked();
    const length = keys[hi][0] - keys[lo][0];
    if (length < 0.1 || hi === keys.length - 1) return 0;
    const room = keys[hi + 1][0] - 0.05 - keys[hi][0];
    return Math.max(0, Math.floor(room / length + 1e-9));
}

/* The selected stretch, repeated after itself as many times as it fits. Each
   copy starts where the last one ended, so the loop runs on unbroken. */
function loopKeys() {
    const times = loopTimes();
    if (!times) return;
    const {keys, lo, hi} = picked();
    const pattern = clone(keys.slice(lo, hi + 1));
    const length = pattern[pattern.length - 1][0] - pattern[0][0];
    const at = (h, off) => h ? [h[0] + off, h[1]] : null;
    snapshot();
    const copies = [];
    let joint = keys[hi];                       // where one pass ends and the next begins
    for (let j = 1; j <= times; j++) {
        const off = length * j;
        joint[3] = at(pattern[0][3], off);      // leaves the joint the way the stretch began
        for (let m = 1; m < pattern.length; m++) {
            const k = pattern[m];
            const copy = [k[0] + off, k[1], at(k[2], off), at(k[3], off)];
            copies.push(copy);
            joint = copy;
        }
    }
    joint[3] = at(pattern[pattern.length - 1][3], length * times);
    keys.splice(hi + 1, 0, ...copies);
    tidyHandles(keys);
    changed();
}

/* Loop evenly: how many passes of the selected stretch - itself the first -
   come nearest to filling it and the room after it, up to the next key.
   At least two; 0 with no key after the selection to loop up to. */
function evenPasses() {
    const {keys, lo, hi} = picked();
    const length = keys[hi][0] - keys[lo][0];
    if (length < 0.1 || hi === keys.length - 1) return 0;
    return Math.max(2, Math.round((keys[hi + 1][0] - keys[lo][0]) / length));
}

/* The selected stretch repeated a whole number of times, every pass - the
   first too - stretched or squeezed alike so the last ends exactly on the
   next key: no pass cut short, no leftover gap. Each starts where the one
   before ended; the last flows into the next key, arriving the way the
   stretch arrives at its own end. */
function loopEvenly() {
    const passes = evenPasses();
    if (!passes) return;
    const {keys, lo, hi} = picked();
    const t0 = keys[lo][0], next = keys[hi + 1];
    const period = (next[0] - t0) / passes, scale = period / (keys[hi][0] - t0);
    const fit = h => h ? [t0 + (h[0] - t0) * scale, h[1]] : null;
    const pattern = clone(keys.slice(lo, hi + 1)).map(k => [t0 + (k[0] - t0) * scale, k[1], fit(k[2]), fit(k[3])]);
    const at = (h, off) => h ? [h[0] + off, h[1]] : null;
    snapshot();
    const out = clone(pattern);
    for (let j = 1; j < passes; j++) {
        const off = period * j;
        out[out.length - 1][3] = at(pattern[0][3], off);       // leaves the joint the way the stretch began
        for (let m = 1; m < pattern.length; m++) {
            const k = pattern[m];
            out.push([k[0] + off, k[1], at(k[2], off), at(k[3], off)]);
        }
    }
    const end = out.pop();                                      // lands on the next key, which stays
    next[2] = end[2] ? [end[2][0], next[1] + (end[2][1] - end[1])] : null;
    keys.splice(lo, hi - lo + 1, ...out);
    ed.sel = ed.sel.filter(i => i <= hi);
    tidyHandles(keys);
    changed();
}

/* The selected stretch, played backwards: each key mirrored in time across
   it, its two handles swapped. */
function reverseKeys() {
    const {keys, lo, hi} = picked();
    const t0 = keys[lo][0], t1 = keys[hi][0];
    const mirror = h => h ? [t0 + t1 - h[0], h[1]] : null;
    snapshot();
    const part = keys.slice(lo, hi + 1).map(k => [t0 + t1 - k[0], k[1], mirror(k[3]), mirror(k[2])]).reverse();
    keys.splice(lo, hi - lo + 1, ...part);
    ed.sel = ed.sel.map(i => lo + hi - i).sort((a, b) => a - b);
    tidyHandles(keys);
    changed();
}

$("ed-fit").onclick = () => { if (ed.keys) { fitRange(); drawGraph(); } };
$("ed-undo").replaceChildren(Buddy.icon("undo"));
$("ed-redo").replaceChildren(Buddy.icon("undo"));
$("ed-redo").firstChild.style.transform = "scaleX(-1)";
$("ed-undo").onclick = () => step(ed.undo, ed.redo);
$("ed-redo").onclick = () => step(ed.redo, ed.undo);

document.addEventListener("keydown", e => {
    if ($("panel-editor").hidden || typing(e)) return;
    // Delete or Backspace takes the selected keys out wherever the focus is -
    // not only on the graph: a press on Smooth or the stage moved it off.
    if ((e.key === "Delete" || e.key === "Backspace") && ed.keys && selCount()) {
        e.preventDefault();
        return removeKeys();
    }
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); step(...(e.shiftKey ? [ed.redo, ed.undo] : [ed.undo, ed.redo])); }
    else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); step(ed.redo, ed.undo); }
    else if (mod && e.key.toLowerCase() === "a" && e.target === graph && ed.keys) {
        e.preventDefault();
        ed.sels = Object.fromEntries(ed.on.map(c => [c, ed.keys[c].map((_k, i) => i)]));       // every shown channel's
        drawGraph();
        drawButtons();
    }
    // Space plays or pauses wherever the focus is on the tab, never also
    // pressing the button that has it.
    else if (e.key === " " && !typing(e)) { e.preventDefault(); if (!e.repeat) $("ed-play").click(); }
});
document.addEventListener("keyup", e => {
    if (e.key === " " && !$("panel-editor").hidden && !typing(e)) e.preventDefault();
});
// Where a key is text being typed: a field, a list, or anything in a dialog.
function typing(e) {
    return !!e.target.closest?.("textarea, select, .modal, [contenteditable], " +
        "input:not([type=range]):not([type=checkbox]):not([type=radio]):not([type=button])");
}

// ================================================================ Presets

/* Highlight: a caption whose words light up as they're said, each one's box
   sliding (or jumping) over and popping, as highlight.py keys it in Resolve
   - SLIDE and SETTLE seconds, the box padded by BOX_PAD of the line height.
   The preview's words are said at made-up but uneven times, as speech is. */
const HL = {
    words: [["Every", 0], ["word", .32], ["lights", .52], ["up", .9], ["the", 1.3], ["moment", 1.42], ["it's", 1.86], ["said", 2.02]],
    loop: 3.2, slide: .1, settle: .16, pad: [.16, .08],
    pops: {off: 1, small: 1.08, big: 1.18}, corners: {square: 0, rounded: .3, pill: 1},
    spans: [], rects: [],
    playing: true, t: 0, last: null,        // the preview's own clock: seconds into the loop
};
const HL_SWATCHES = [["Yellow", "#ffd60a"], ["Lime", "#a3e635"], ["Green", "#22c55e"], ["Cyan", "#22d3ee"],
                     ["Blue", "#3b82f6"], ["Purple", "#a855f7"], ["Pink", "#ec4899"], ["Red", "#ef4444"],
                     ["Orange", "#f97316"], ["White", "#ffffff"], ["Black", "#111111"]];
const hlOptions = () => state.hl || {look: "both", box_color: "#ffd60a", word_color: "#111111",
                                     pop: "small", move: "slide", shape: "rounded"};

/* The preview's caption: the selected one's text (its lines as it has them),
   else the sample - sized so its longest line fits the stage. */
function hlBuild(text) {
    const lines = text ? text.split("\n").map(l => l.split(/\s+/).filter(Boolean)).filter(l => l.length)
        : [HL.words.slice(0, 4).map(([w]) => w), HL.words.slice(4).map(([w]) => w)];
    const kids = [$("hl-box")];
    HL.spans = [];
    lines.forEach((words, n) => {
        if (n) kids.push(el("br"));
        words.forEach((word, i) => {
            if (i) kids.push(document.createTextNode(" "));
            const span = el("span.hl-word", {text: word});
            if (text) span.setAttribute("translate", "no");
            HL.spans.push(span);
            kids.push(span);
        });
    });
    const longest = Math.max(1, ...lines.map(l => l.join(" ").length));
    $("hl-caption").style.fontSize = `${clamp(200 / (longest * .55), 7, 15).toFixed(1)}px`;
    $("hl-caption").replaceChildren(...kids);
    HL.rects = [];
}

// Where each word sits in the caption, measured once it's drawn (and again
// when the pane changes size).
function hlRects() {
    if (HL.rects.length && HL.rects.every(r => r.h)) return HL.rects;
    HL.rects = HL.spans.map(s => ({x: s.offsetLeft + s.offsetWidth / 2, y: s.offsetTop + s.offsetHeight / 2,
                                   w: s.offsetWidth, h: s.offsetHeight * .8}));
    return HL.rects;
}
new ResizeObserver(() => { HL.rects = []; }).observe($("hl-stage"));

const easeInOut = u => u < .5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;

function hlTick(now) {
    if ($("panel-textplus").hidden || !HL.spans.length) {
        HL.last = null;
        return;
    }
    const o = hlOptions();
    // The selected caption at its own timing, else the sample.
    const words = TM.clip ? TM.clip.words.map((w, k) => [w, TM.starts[k]]) : HL.words;
    const loop = TM.clip ? TM.clip.duration : HL.loop;
    // Its own clock, so it can pause: a rest of PAUSE after each pass.
    if (HL.playing && HL.last != null) HL.t = (HL.t + (now - HL.last) / 1000) % (loop + PAUSE);
    HL.last = now;
    const t = HL.t;
    $("hl-time").textContent = `${Math.min(t, loop).toFixed(2)}s / ${loop.toFixed(2)}s`;
    let i = -1;
    for (let k = 0; k < words.length; k++) if (words[k][1] <= t) i = k;
    const rects = hlRects();
    const box = $("hl-box");
    const lit = i >= 0 && t < loop && i < HL.spans.length;
    tmTick(t, lit ? i : -1);
    const pop = HL.pops[o.pop] || 1;
    let scale = 1, at = rects[Math.max(0, i)];
    if (lit) {
        const since = t - words[i][1];
        const move = o.move === "slide" ? HL.slide : 0;
        if (i > 0 && since < move) {
            const u = easeInOut(since / move), a = rects[i - 1], b = rects[i];
            at = {x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u, w: a.w + (b.w - a.w) * u, h: a.h + (b.h - a.h) * u};
        }
        const up = Math.max(move, 1 / 30);
        scale = since < up ? 1 + (pop - 1) * easeInOut(since / up)
            : since < up + HL.settle ? pop - (pop - 1) * easeInOut((since - up) / HL.settle) : 1;
    }
    HL.spans.forEach((span, k) => {
        const on = lit && k === i;
        span.classList.toggle("lit", on && o.look !== "box");
        span.style.color = on && o.look !== "box" ? (o.look === "both" ? o.word_color : o.box_color) : "";
        span.style.transform = on && scale !== 1 ? `scale(${scale.toFixed(3)})` : "";
    });
    if (!lit || o.look === "color" || !at) {
        box.style.opacity = "0";
        return;
    }
    const w = (at.w + 2 * HL.pad[0] * at.h) * scale, h = at.h * (1 + 2 * HL.pad[1]) * scale;
    Object.assign(box.style, {
        opacity: "1", background: o.box_color,
        width: `${w}px`, height: `${h}px`, left: `${at.x - w / 2}px`, top: `${at.y - h / 2}px`,
        borderRadius: `${(HL.corners[o.shape] ?? .3) * Math.min(w, h) / 2}px`,
    });
}

function hlColourName(hex) {
    return (HL_SWATCHES.find(([, h]) => h === hex) || ["Custom"])[0];
}

function hlColourButton(button) {
    const hex = hlOptions()[button.dataset.key];
    const swatch = el("i.hl-swatch");
    swatch.style.background = hex;
    button.replaceChildren(swatch, el("span", {text: hlColourName(hex)}));
}

function hlSet(key, value) {
    state.hl = {...hlOptions(), [key]: value};
    drawHighlight();
    send("hl_option", {key, value});
}

function hlColourMenu(button) {
    const key = button.dataset.key, now = hlOptions()[key];
    const r = button.getBoundingClientRect();
    Buddy.menu({x: r.left, y: r.bottom + 4, items: [
        {heading: key === "box_color" ? "Highlight colour" : "Word colour"},
        ...HL_SWATCHES.map(([name, hex]) => ({label: name, swatch: hex, checked: now === hex, onClick: () => hlSet(key, hex)})),
        {sep: true},
        {label: "Custom colour…", onClick: () => hlPick(key, button)},
    ]});
}

// Any colour: Buddy's own picker, as Font Styling's colours use.
function hlPick(key, button) {
    Buddy.pickColor({
        hex: hlOptions()[key], title: key === "box_color" ? "Highlight colour" : "Word colour",
        at: button.getBoundingClientRect(),
        onPick: hex => hlSet(key, String(hex).toLowerCase()),
    });
}

function hlChips() {
    const s = state.hl_selection;
    if (!state.connected) {
        return [el("span.muted.small", {text: "Not connected to Resolve"}),
                el("button.btn.ghost", {type: "button", text: "Connect", onclick: () => send("refresh")})];
    }
    if (state.problem) return [el("span.warn", {text: state.problem})];
    if (state.resolve_busy) return [el("span.warn", {text: "Resolve is busy – Buddy carries on once playback stops"})];
    if (!s) return [el("span.muted.small", {text: "Reading the selection…"})];
    if (!s.total) return [el("span.muted.small", {text: "Nothing selected in the timeline"})];
    const chips = [];
    if (s.titles) chips.push(el("span.chip", {text: s.titles === 1 ? "1 Text+ clip" : `${s.titles} Text+ clips`}));
    if (s.highlighted) chips.push(el("span.chip", {text: s.highlighted === 1 ? "1 highlighted" : `${s.highlighted} highlighted`}));
    if (s.other) chips.push(el("span.chip", {text: s.other === 1 ? "1 other clip" : `${s.other} other clips`,
                                             title: "Only Text+ clips take a highlight"}));
    if (s.stale) {
        // Measured for other words, size or position: the boxes may not line up.
        chips.push(el("span.chip.warn", {text: s.stale === 1 ? "1 measured for other text" : `${s.stale} measured for other text`,
                                         title: "Its words, size or position changed since the highlight went on – the boxes may not line up. Refresh measures them again."}),
                   el("button.btn.ghost.small", {type: "button", disabled: !!state.working,
                                                 text: state.working === "highlight" ? "Refreshing…" : "Refresh",
                                                 title: "Measure the highlight again, keeping each clip's options and timing",
                                                 onclick: () => send("hl_refresh")}));
    }
    return chips;
}

function drawHighlight() {
    const o = hlOptions(), s = state.hl_selection;
    pressed($("hl-look"), "look", o.look);
    pressed($("hl-shape"), "shape", o.shape);
    pressed($("hl-move"), "move", o.move);
    pressed($("hl-pop"), "pop", o.pop);
    // One highlight colour: the box's, or the word's with no box; the word
    // takes a colour of its own only on the box.
    $("hl-shape-row").hidden = o.look === "color";
    $("hl-word-row").hidden = o.look !== "both";
    for (const b of document.querySelectorAll(".hl-color")) hlColourButton(b);
    $("hl-selection").replaceChildren(...hlChips());
    const can = (s && s.titles) || 0;
    const apply = $("hl-apply"), remove = $("hl-remove");
    apply.disabled = !!state.working || !can;
    remove.disabled = !!state.working || !(s && s.highlighted);
    apply.textContent = state.working === "highlight" ? "Highlighting…"
        : !can ? "Highlight selected" : can === 1 ? "Highlight 1 clip" : `Highlight ${can} clips`;
    remove.textContent = state.working === "unhighlight" ? "Removing…" : "Remove";
    remove.title = s && s.highlighted ? "Take the highlight off the selected clips" : "None of the selected clips has a highlight";
    tmSync();
}

for (const [host, key] of [["hl-look", "look"], ["hl-shape", "shape"], ["hl-move", "move"], ["hl-pop", "pop"]]) {
    for (const b of $(host).querySelectorAll("button")) b.onclick = () => hlSet(key, b.dataset[key]);
}
for (const b of document.querySelectorAll(".hl-color")) b.onclick = () => hlColourMenu(b);

function hlPlay(on) {
    HL.playing = on;
    $("hl-play").replaceChildren(Buddy.icon(on ? "pause" : "play"));
    $("hl-play").setAttribute("aria-label", on ? "Pause" : "Play");
}
$("hl-play").onclick = () => hlPlay(!HL.playing);
hlPlay(true);
// Space plays or pauses the preview wherever the focus is on the tab, never
// also pressing the button that has it (as the Editor's).
document.addEventListener("keydown", e => {
    if ($("panel-textplus").hidden || e.key !== " " || typing(e) || document.querySelector(".menu-pop")) return;
    e.preventDefault();
    if (!e.repeat) hlPlay(!HL.playing);
});
document.addEventListener("keyup", e => {
    if (e.key === " " && !$("panel-textplus").hidden && !typing(e)) e.preventDefault();
});
$("hl-apply").onclick = () => send("hl_apply");
$("hl-remove").onclick = () => send("hl_remove");
hlBuild();

// ------------------------------------------------------------ word timing

/* One caption selected: its words along its length (page.py's
   hl_selection.clip), each a chip from its start to the next word's start.
   A drag moves a word's start between its neighbours (Shift: the words
   after it go with it); arrows nudge the selected word a frame; Start at
   playhead puts it where Resolve's playhead is and selects the next one.
   The preview plays this caption with these starts. Apply timing sends
   them; until a read of the clip has them back, the edit is ours ("dirty")
   and the poll doesn't overwrite it. */
const TM = {clip: null, starts: [], undo: [], redo: [], sel: -1, dirty: false};
const tmFrame = () => 1 / (Number(TM.clip?.fps) || 24);
const tmSnap = t => Math.round(t / tmFrame()) * tmFrame();
const tmSame = (a, b) => a.length === b.length && a.every((t, i) => Math.abs(t - b[i]) < 0.002);

function tmSync() {
    const c = state.hl_selection?.clip || null;
    $("hl-timing").hidden = !c;
    if (!c) {
        if (TM.clip) {
            TM.clip = null;
            hlBuild();
        }
        return;
    }
    const same = TM.clip && TM.clip.id === c.id && TM.clip.text === c.text;
    if (!same) {
        Object.assign(TM, {undo: [], redo: [], sel: 0, dirty: false});
        HL.t = 0;                                   // another caption: its preview from the start
    }
    // Applied: the clip has them now - each on its frame (highlight_resolve.clean_starts).
    if (TM.dirty && TM.starts.length === c.starts.length
        && TM.starts.every((t, i) => Math.abs(t - c.starts[i]) <= .5 / (Number(c.fps) || 24) + 1e-6)) TM.dirty = false;
    if (!same || !TM.dirty) TM.starts = c.starts.slice();
    TM.clip = c;
    if (!same) hlBuild(c.text);
    drawStrip();
}

function tmSnapshot() {
    TM.undo.push(TM.starts.slice());
    if (TM.undo.length > 200) TM.undo.shift();
    TM.redo = [];
}

function tmChanged() {
    TM.dirty = !tmSame(TM.starts, TM.clip.starts);
    drawStrip();
}

/* Word i to start at t: between its neighbours, or (ripple) with every word
   after it moving as much, from the starts as they were (`base`). */
function tmMove(i, t, ripple, base) {
    const f = tmFrame(), n = base.length, end = TM.clip.duration - f;
    if (ripple) {
        const lo = i > 0 ? base[i - 1] + f : 0, hi = end - (base[n - 1] - base[i]);
        const d = clamp(tmSnap(t), lo, Math.max(lo, hi)) - base[i];
        TM.starts = base.map((s, k) => k >= i ? s + d : s);
    } else {
        const lo = i > 0 ? base[i - 1] + f : 0, hi = i < n - 1 ? base[i + 1] - f : end;
        TM.starts = base.slice();
        TM.starts[i] = clamp(tmSnap(t), lo, Math.max(lo, hi));
    }
    tmChanged();
}

/* Word i to start exactly at t: the words around it make room. */
function tmPlace(i, t) {
    const f = tmFrame(), n = TM.starts.length;
    const s = TM.starts.slice();
    s[i] = clamp(tmSnap(t), i * f, TM.clip.duration - (n - i) * f);
    for (let k = i + 1; k < n; k++) s[k] = Math.max(s[k], s[k - 1] + f);
    for (let k = i - 1; k >= 0; k--) s[k] = Math.min(s[k], s[k + 1] - f);
    TM.starts = s;
    tmChanged();
}

function tmStep(from, to) {
    if (!from.length) return;
    to.push(TM.starts.slice());
    TM.starts = from.pop();
    tmChanged();
}

const SOURCES = {manual: "Your timing", transcript: "From the transcript", even: "Spread evenly – no transcript matched"};

function drawStrip() {
    const c = TM.clip;
    if (!c) return;
    const lane = $("hl-lane"), w = lane.clientWidth || 1, dur = c.duration || 1;
    const x = t => t / dur * w;
    $("hl-clip-name").textContent = c.name;
    $("hl-timing-source").textContent = TM.dirty ? "Your timing – not applied yet" : SOURCES[c.timing] || "";
    // The ruler: a tick every half second to five seconds, as the clip's length allows.
    const step = dur <= 3 ? .5 : dur <= 8 ? 1 : dur <= 20 ? 2 : 5;
    const ticks = [];
    for (let t = 0; t < dur - 1e-6; t += step) {
        const tick = el("span.hl-tick", {text: `${+t.toFixed(1)}s`});
        tick.style.left = `${x(t)}px`;
        ticks.push(tick);
    }
    $("hl-ruler").replaceChildren(...ticks);
    if (lane.children.length !== c.words.length) {
        lane.replaceChildren(...c.words.map((word, i) => {
            const chip = el("div.hl-chip", {text: word, title: word});
            chip.dataset.i = i;
            chip.setAttribute("translate", "no");
            return chip;
        }));
    }
    [...lane.children].forEach((chip, i) => {
        const a = x(TM.starts[i]), b = i + 1 < c.words.length ? x(TM.starts[i + 1]) : w;
        chip.style.left = `${a}px`;
        chip.style.width = `${Math.max(4, b - a - 2)}px`;
        chip.classList.toggle("sel", i === TM.sel);
    });
    const head = $("hl-resolve-head");
    head.hidden = c.playhead == null;
    if (c.playhead != null) head.style.left = `${x(c.playhead)}px`;
    const busy = !!state.working;
    $("hl-undo").disabled = !TM.undo.length;
    $("hl-redo").disabled = !TM.redo.length;
    $("hl-reset").disabled = busy || tmSame(TM.starts, c.auto);
    $("hl-at-playhead").disabled = busy || TM.sel < 0;
    $("hl-set").disabled = busy || (!TM.dirty && c.timing === "manual");
    $("hl-set").textContent = state.working === "timing" ? "Applying…" : "Apply timing";
}

// The preview's time on the strip, and the word it has lit.
function tmTick(t, lit) {
    if (!TM.clip) return;
    const w = $("hl-lane").clientWidth || 1;
    $("hl-head").style.left = `${Math.min(t, TM.clip.duration) / (TM.clip.duration || 1) * w}px`;
    [...$("hl-lane").children].forEach((chip, i) => chip.classList.toggle("now", i === lit));
}

$("hl-lane").addEventListener("pointerdown", e => {
    const chip = e.target.closest(".hl-chip");
    if (!chip || !TM.clip || e.button !== 0) return;
    e.preventDefault();
    $("hl-strip").focus();
    const i = Number(chip.dataset.i), x0 = e.clientX, base = TM.starts.slice();
    const w = $("hl-lane").clientWidth || 1;
    TM.sel = i;
    tmSnapshot();
    chip.setPointerCapture(e.pointerId);
    const move = m => tmMove(i, base[i] + (m.clientX - x0) / w * TM.clip.duration, m.shiftKey, base);
    const up = () => {
        chip.removeEventListener("pointermove", move);
        chip.removeEventListener("pointerup", up);
        chip.removeEventListener("pointercancel", up);
        if (tmSame(TM.undo[TM.undo.length - 1] || [], TM.starts)) TM.undo.pop();    // only a click
        drawStrip();
    };
    chip.addEventListener("pointermove", move);
    chip.addEventListener("pointerup", up);
    chip.addEventListener("pointercancel", up);
    drawStrip();
});

// The ruler moves the preview to the time clicked (and follows a drag).
$("hl-ruler").addEventListener("pointerdown", e => {
    if (!TM.clip || e.button !== 0) return;
    e.preventDefault();
    const ruler = $("hl-ruler");
    const seek = m => {
        const r = ruler.getBoundingClientRect();
        HL.t = clamp((m.clientX - r.left) / (r.width || 1), 0, 1) * TM.clip.duration;
    };
    seek(e);
    ruler.setPointerCapture(e.pointerId);
    const up = () => {
        ruler.removeEventListener("pointermove", seek);
        ruler.removeEventListener("pointerup", up);
        ruler.removeEventListener("pointercancel", up);
    };
    ruler.addEventListener("pointermove", seek);
    ruler.addEventListener("pointerup", up);
    ruler.addEventListener("pointercancel", up);
});

$("hl-strip").addEventListener("keydown", e => {
    if (!TM.clip || TM.sel < 0) return;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        tmSnapshot();
        const by = (e.key === "ArrowLeft" ? -1 : 1) * tmFrame();
        tmMove(TM.sel, TM.starts[TM.sel] + by, e.shiftKey, TM.starts.slice());
    } else if (e.key === "Tab" && !e.ctrlKey) {
        const next = TM.sel + (e.shiftKey ? -1 : 1);
        if (next >= 0 && next < TM.starts.length) {
            e.preventDefault();
            TM.sel = next;
            drawStrip();
        }
    }
});

document.addEventListener("keydown", e => {
    if ($("hl-timing").hidden || $("panel-textplus").hidden || typing(e)) return;
    const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
    if (mod && key === "z") { e.preventDefault(); e.shiftKey ? tmStep(TM.redo, TM.undo) : tmStep(TM.undo, TM.redo); }
    else if (mod && key === "y") { e.preventDefault(); tmStep(TM.redo, TM.undo); }
});

$("hl-undo").replaceChildren(Buddy.icon("undo"));
$("hl-redo").replaceChildren(Buddy.icon("undo"));
$("hl-redo").firstChild.style.transform = "scaleX(-1)";
$("hl-undo").onclick = () => tmStep(TM.undo, TM.redo);
$("hl-redo").onclick = () => tmStep(TM.redo, TM.undo);
$("hl-reset").onclick = () => { tmSnapshot(); TM.starts = TM.clip.auto.slice(); tmChanged(); };
$("hl-at-playhead").onclick = () => TM.clip && TM.sel >= 0 && send("hl_playhead", {id: TM.clip.id});
$("hl-set").onclick = () => TM.clip && send("hl_timing", {id: TM.clip.id, starts: TM.starts});
new ResizeObserver(() => drawStrip()).observe($("hl-strip"));

Buddy.on("hl_playhead", data => {
    if (!TM.clip || data.id !== TM.clip.id || TM.sel < 0) return;
    tmSnapshot();
    TM.clip.playhead = data.at;
    tmPlace(TM.sel, data.at);
    if (TM.sel + 1 < TM.starts.length) TM.sel += 1;
    drawStrip();
});

// =================================================================== Zoom

/* The one clip selected in Resolve (page.py's zm_selection.clip), playing
   here (zm_frame / zm_time from zoom_player.py; a still plays on a clock of
   our own), drawn zoomed exactly as zoom.py keys it for Resolve. Its zooms
   are blocks on a track: dragged whole, by their ends, or by the dots that
   say how long they take to get in and out; a pan's or follow's framings
   are marks on its block, moved in time by dragging. Framing shows the
   whole picture with the selected zoom's box to drag (a pan's or follow's
   every framing, the selected one to drag). Apply sends them; until the clip
   reads back with them on, the edit is ours ("dirty") and the poll leaves
   it be. */
const ZOOM_KINDS = {        // zoom.py KINDS: len / in / out in seconds, null: all of it; points: [share of len, scale, x, y]
    punch: {label: "Punch in", len: 1.5, in: 0, out: 0, scale: 1.3, ease: "smooth"},
    push: {label: "Push in", len: 2.5, in: 0.6, out: 0, scale: 1.4, ease: "smooth"},
    inout: {label: "Zoom in & out", len: 2.5, in: 0.5, out: 0.5, scale: 1.4, ease: "smooth"},
    pullout: {label: "Pull out", len: 1.5, in: 0, out: null, scale: 1.5, ease: "smooth"},
    creep: {label: "Slow creep", len: null, in: null, out: 0, scale: 1.12, ease: "gentle"},
    crash: {label: "Crash zoom", len: 1.2, in: 0.12, out: 0, scale: 1.8, ease: "rush", blur: true},
    bounce: {label: "Bounce", len: 1.5, in: 0.5, out: 0, scale: 1.4, ease: "smooth", bounce: 0.5},
    step: {label: "Step zoom", len: 2.0, in: 0, out: 0, scale: 1.6, ease: "smooth", steps: 2},
    impact: {label: "Impact", len: 1.0, in: 0.06, out: 0, scale: 1.35, ease: "rush", shake: 0.6},
    pan: {label: "Pan & zoom", len: 3.0, in: 0, out: 0, scale: 1.3, ease: "smooth",
          points: [[0, 1.3, .38, .5], [1, 1.3, .62, .5]]},
    follow: {label: "Follow pan", len: 4.0, in: 0, out: 0, scale: 1.4, ease: "smooth",
             points: [[0, 1.4, .36, .5], [.5, 1.4, .5, .45], [1, 1.4, .64, .5]]},
    pulse: {label: "Pulse", len: 2.0, in: 0, out: 0, scale: 1.08, ease: "smooth", period: 0.5},
    tilt: {label: "Tilt zoom", len: 2.5, in: 0.6, out: 0.6, scale: 1.3, ease: "smooth", tilt: 4},
};
const ZOOM_ORDER = ["punch", "push", "crash", "bounce", "step", "impact", "inout", "pulse", "tilt",
                    "pullout", "creep", "pan", "follow"];
const ZM_MOVING = ["pan", "follow"];
const ZM_EXTRAS = ["tilt", "shake", "blur", "steps", "period", "bounce"];
const ZM_MIN = {len: 0.1, scale: 1.02}, ZM_MAX_SCALE = 4, ZM_SHAKE_FOR = 0.6, ZM_MAX_POINTS = 12;
const ZM_STAGE_H = 340;     // px the picture is at most tall
const ZM = {clip: null, zooms: [], sel: -1, pt: 0, undo: [], redo: [], dirty: false,
            t: 0, at: 0, playing: false, ready: false, failed: "", view: "result", sent: 0, seekTimer: 0, icon: null,
            range: null,       // [from, to] seconds of the clip the track shows; null: all of it
            graded: false, lut: null};      // Graded: on, and the clip's grade ({id, size, data}) from Resolve
const zmClone = zs => JSON.parse(JSON.stringify(zs));
const zmFps = () => Number(ZM.clip?.fps) || 24;
const zmAspect = () => (ZM.clip?.width || 16) / (ZM.clip?.height || 9);
const zmSnap = t => Math.round(t * zmFps()) / zmFps();
const zmInside = (v, s) => clamp(v, .5 / s, 1 - .5 / s);
const zmMoving = z => ZM_MOVING.includes(z?.kind);
// What the framing box edits: a moving zoom's selected point, else the zoom.
const zmFramed = z => zmMoving(z) ? z.points[clamp(ZM.pt, 0, z.points.length - 1)] : z;

// ---- zoom.py's maths, line for line.
function zmEase(u, how) {
    u = clamp(u, 0, 1);
    if (how === "gentle") return .5 - .5 * Math.cos(Math.PI * u);
    if (how === "rush") return 1 - Math.pow(1 - u, 4);
    return u < .5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
}

function zmOvershoot(u, strength) {
    u = clamp(u, 0, 1);
    const c1 = 1.70158 * 2 * strength, c3 = c1 + 1;
    return 1 + c3 * Math.pow(u - 1, 3) + c1 * Math.pow(u - 1, 2);
}

const zmBeat = u => u < .12 ? zmEase(u / .12, "smooth") : Math.pow(1 - (u - .12) / .88, 2);
const zmRunning = (z, t, dur) => z.start <= t && (t < z.end || z.end >= dur - 1e-6);

function zmHeld(z, since, len) {
    if (z.kind === "step") {
        const steps = z.steps || 2, span = Math.max(1e-6, (len - z.out) / steps);
        return Math.min(1, (Math.floor(since / span) + 1) / steps);
    }
    if (z.kind === "pulse") {
        const period = z.period || .5;
        return zmBeat((since % period) / period);
    }
    return 1;
}

function zmAmount(z, t, dur) {
    if (!zmRunning(z, t, dur)) return 0;
    const how = (ZOOM_KINDS[z.kind] || ZOOM_KINDS.push).ease, since = t - z.start, len = z.end - z.start;
    if (z.out > 0 && t >= z.end - z.out) {
        const level = 1 - zmEase((t - (z.end - z.out)) / z.out, how);
        return level * zmHeld(z, Math.max(0, z.end - z.out - z.start - 1e-6), len);
    }
    if (z.in > 0 && since < z.in) {
        const u = since / z.in;
        return z.kind === "bounce" ? zmOvershoot(u, z.bounce ?? .5) : zmEase(u, how);
    }
    return zmHeld(z, since, len);
}

function zmShake(z, t) {
    const strength = z.shake || 0, tau = t - (z.start + z.in);
    if (!strength || tau < 0 || tau >= ZM_SHAKE_FOR) return [0, 0, 0];
    const fade = Math.exp(-7 * tau) * (1 - tau / ZM_SHAKE_FOR), amp = .02 * strength * fade;
    return [amp * Math.sin(2 * Math.PI * 13 * tau + .3), .8 * amp * Math.sin(2 * Math.PI * 17 * tau + 1.7),
            1.5 * strength * fade * Math.sin(2 * Math.PI * 11 * tau + .9)];
}

function zmBlend(a, b, f) {
    const wa = 1 / a[0], wb = 1 / b[0];
    const w = Math.exp(Math.log(wa) + (Math.log(wb) - Math.log(wa)) * f);
    const lam = Math.abs(wb - wa) > 1e-9 ? (w - wa) / (wb - wa) : f;
    return [1 / w, a[1] + (b[1] - a[1]) * lam, a[2] + (b[2] - a[2]) * lam];
}

function zmTarget(z, t) {
    const points = zmMoving(z) ? z.points : null;
    if (!points || !points.length) return [z.scale, z.x, z.y];
    const since = t - z.start;
    if (since <= points[0].at) return [points[0].scale, points[0].x, points[0].y];
    for (let i = 0; i + 1 < points.length; i++) {
        const a = points[i], b = points[i + 1];
        if (since < b.at) {
            const f = zmEase((since - a.at) / Math.max(1e-6, b.at - a.at), "smooth");
            return zmBlend([a.scale, a.x, a.y], [b.scale, b.x, b.y], f);
        }
    }
    const p = points[points.length - 1];
    return [p.scale, p.x, p.y];
}

function zmMaxTurn(s, aspect) {
    const a = Math.max(aspect, 1 / aspect), r = Math.hypot(1, a);
    if (s >= r) return 90;
    return Math.max(0, (Math.atan(a) - Math.acos(Math.min(1, s / r))) * 180 / Math.PI);
}

function zmPlace(s, qx, qy, degrees, aspect) {
    const limit = zmMaxTurn(s, aspect);
    degrees = clamp(degrees, -limit, limit);
    const th = degrees * Math.PI / 180, c = Math.cos(th), sn = Math.abs(Math.sin(th));
    const hx = (c + sn / aspect) / (2 * s), hy = (c + sn * aspect) / (2 * s);
    qx = hx < .5 ? clamp(qx, hx, 1 - hx) : .5;
    qy = hy < .5 ? clamp(qy, hy, 1 - hy) : .5;
    const vx = (qx - .5) * aspect, vy = .5 - qy;
    const rx = vx * Math.cos(th) - vy * Math.sin(th), ry = vx * Math.sin(th) + vy * Math.cos(th);
    return [s, .5 - s * rx / aspect, .5 + s * ry, degrees];
}

// (scale, centre x, centre y (down), degrees) at t: zoom.at.
function zmAt(t) {
    const dur = ZM.clip.duration;
    for (const z of ZM.zooms) {
        if (!zmRunning(z, t, dur)) continue;
        const f = zmAmount(z, t, dur), [dx, dy, turn] = zmShake(z, t);
        if (f <= 0 && !(dx || dy || turn)) break;
        const [s, qx, qy] = f > 0 ? zmBlend([1, .5, .5], zmTarget(z, t), f) : [1, .5, .5];
        return zmPlace(s, qx + dx, qy + dy, (z.tilt || 0) * f + turn, zmAspect());
    }
    return [1, .5, .5, 0];
}

// zoom.new: a zoom of `kind` at t, cut to fit before the next one.
function zmNew(kind, t) {
    const spec = ZOOM_KINDS[kind], dur = ZM.clip.duration;
    const holding = ZM.zooms.find(z => z.start <= t && t < z.end);
    let start = holding ? holding.end : t;
    const next = ZM.zooms.filter(z => z.start >= start).sort((a, b) => a.start - b.start)[0];
    const room = next ? next.start : dur;
    if (kind === "creep" && !ZM.zooms.length && start < .5) start = 0;
    start = zmSnap(start);
    const len = spec.len == null ? room - start : Math.min(spec.len, room - start);
    if (len < ZM_MIN.len) return null;
    const zin = spec.in == null ? len : Math.min(spec.in, len);
    const zout = spec.out == null ? len - zin : Math.min(spec.out, len - zin);
    const z = {kind, start, end: Math.min(dur, start + len), in: zin, out: zout, scale: spec.scale, x: .5, y: .5};
    for (const k of ZM_EXTRAS) if (k in spec) z[k] = spec[k];
    if (spec.points) z.points = spec.points.map(([share, s, x, y]) => ({at: share * len, scale: s, x, y}));
    return z;
}

// Two lists of zooms the same - as zoom.py keeps them (no tilt is 0, a
// kind's own settings only on it), numbers to within .002.
function zmCanon(z) {
    const c = [z.kind, z.start, z.end, z.in, z.out, z.scale, z.x, z.y, z.tilt || 0, z.shake || 0, z.blur ? 1 : 0,
               z.kind === "step" ? z.steps || 2 : 0, z.kind === "pulse" ? z.period || .5 : 0,
               z.kind === "bounce" ? z.bounce ?? .5 : 0];
    if (zmMoving(z)) for (const p of z.points || []) c.push(p.at, p.scale, p.x, p.y);
    return c;
}
function zmSame(a, b) {
    return a.length === b.length && a.every((z, i) => {
        const x = zmCanon(z), y = zmCanon(b[i]);
        return x.length === y.length && x.every((v, k) => typeof v === "number" ? Math.abs(v - y[k]) < .002 : v === y[k]);
    });
}

function zmSnapshot() {
    ZM.undo.push(zmClone(ZM.zooms));
    if (ZM.undo.length > 200) ZM.undo.shift();
    ZM.redo = [];
}

function zmChanged() {
    ZM.dirty = !zmSame(ZM.zooms, ZM.clip.zooms || []);
    drawZoom();
}

function zmStep(from, to) {
    if (!from.length) return;
    to.push(zmClone(ZM.zooms));
    ZM.zooms = from.pop();
    if (ZM.sel >= ZM.zooms.length) ZM.sel = ZM.zooms.length - 1;
    zmChanged();
}

const ZM_EMPTY = {
    none: ["Select a clip in Resolve's timeline", "One video clip or still – or an adjustment clip, to zoom everything under it. It plays here, and its zooms go on a track under it."],
    many: ["Select just one clip", "Zooms are set on one clip at a time."],
    other: ["Zoom works on video clips, stills and adjustment clips", "Select one of those in Resolve's timeline."],
    missing: ["Buddy can't find this clip's file", ""],
    error: ["Couldn't read the clip", ""],
};

function zmSync() {
    const s = state.zm_selection, c = s?.clip || null;
    let empty = null;
    if (!state.connected) empty = ["Not connected to Resolve", "Connect to Resolve to zoom its clips."];
    else if (state.problem) empty = [state.problem, ""];
    else if (!s) empty = state.resolve_busy ? ["Resolve is busy", "Buddy carries on once playback stops."]
        : ["Reading the selection…", ""];
    else if (!c) empty = ZM_EMPTY[s.reason] || ZM_EMPTY.none;
    else if (!c.exists) empty = [ZM_EMPTY.missing[0], c.path || ""];
    if (s?.reason === "error" && s.error) empty = [ZM_EMPTY.error[0], s.error];
    $("zm-empty").hidden = !empty;
    $("zm-card").hidden = !!empty;
    // Several clips, zooms copied: paste onto them all.
    const copied = state.zm_clipboard, many = s?.reason === "many" && (s.usable || 0) > 1;
    $("zm-paste-many").hidden = !(empty && many && copied);
    if (many && copied) {
        const n = copied.zooms.length;
        $("zm-paste-many-text").textContent = n === 1
            ? `Paste the copied zoom onto all ${s.usable} clips, in place of theirs?`
            : `Paste the ${n} copied zooms onto all ${s.usable} clips, in place of theirs?`;
        for (const id of ["zm-paste-same", "zm-paste-stretch"]) $(id).disabled = !!state.working;
        const pasting = state.working === "paste";
        $("zm-paste-same").textContent = pasting && ZM.pasting === "same" ? "Pasting…" : "Paste at the same times";
        $("zm-paste-stretch").textContent = pasting && ZM.pasting === "stretch" ? "Pasting…" : "Paste stretched to each clip";
    }
    if (empty) {
        $("zm-empty-title").textContent = empty[0];
        $("zm-empty-text").textContent = empty[1];
        if (!c || !c.exists) ZM.clip = null;
        return;
    }
    const same = ZM.clip && ZM.clip.id === c.id && ZM.clip.path === c.path && ZM.clip.duration === c.duration;
    if (!same) {
        Object.assign(ZM, {zooms: zmClone(c.zooms || []), sel: -1, pt: 0, undo: [], redo: [], dirty: false,
                           t: 0, at: performance.now(), playing: false, ready: false, failed: "", range: null,
                           graded: false, lut: null});
        $("zm-img").removeAttribute("src");
        const aspect = (c.width || 16) / (c.height || 9);
        $("zm-stage").style.aspectRatio = `${c.width || 16} / ${c.height || 9}`;
        // Never taller than ZM_STAGE_H: a portrait clip is narrow, not huge.
        $("zm-stage").style.maxWidth = `${Math.round(ZM_STAGE_H * aspect)}px`;
        $("zm-lane").replaceChildren();
    } else if (ZM.dirty && zmSame(ZM.zooms, c.zooms || [])) {
        ZM.dirty = false;                                       // applied: the clip has them now
    } else if (!ZM.dirty && !zmSame(ZM.zooms, c.zooms || [])) {
        ZM.zooms = zmClone(c.zooms || []);                      // changed on the clip, not here
        if (ZM.sel >= ZM.zooms.length) ZM.sel = -1;
    }
    ZM.clip = c;
    drawZoom();
}

function zmTimes(z) {
    const hold = Math.max(0, z.end - z.start - z.in - z.out);
    return `${z.start.toFixed(2)}s – ${z.end.toFixed(2)}s · in ${z.in.toFixed(2)}s · hold ${hold.toFixed(2)}s · out ${z.out.toFixed(2)}s`;
}

function drawZoom() {
    const c = ZM.clip;
    if (!c) return;
    $("zm-clip").textContent = c.name;
    $("zm-clip").title = c.name;
    $("zm-status").textContent = state.resolve_busy ? "Resolve is busy – Buddy carries on once playback stops"
        : ZM.dirty ? "Not applied yet" : (c.zooms || []).length ? "On the clip" : "";
    const busy = !!state.working;
    $("zm-apply").disabled = busy || !ZM.dirty;
    $("zm-apply").textContent = state.working === "zoom" ? "Applying…" : "Apply zooms";
    $("zm-remove").disabled = busy || !(c.zooms || []).length;
    $("zm-copy").disabled = !ZM.zooms.length;
    $("zm-paste").disabled = !state.zm_clipboard;
    $("zm-remove").textContent = state.working === "unzoom" ? "Removing…" : "Remove zooms";
    $("zm-undo").disabled = !ZM.undo.length;
    $("zm-redo").disabled = !ZM.redo.length;
    pressed($("zm-view"), "view", ZM.view);
    $("zm-graded").setAttribute("aria-pressed", String(ZM.graded));
    $("zm-graded").disabled = busy;
    $("zm-graded").textContent = state.working === "grade" ? "Getting the grade…" : "Graded";
    $("zm-regrade").hidden = !ZM.graded;
    $("zm-regrade").disabled = busy;
    $("zm-stage").classList.toggle("graded", ZM.graded && !!ZM.lut);
    // An adjustment clip: the clips under it play here (each its own grade, so no Graded).
    const adjust = c.kind === "adjust";
    $("zm-stage").classList.toggle("adjust", adjust);
    $("zm-graded-box").hidden = adjust;
    $("zm-under").hidden = !adjust;
    if (adjust) {
        $("zm-under").textContent = (c.parts || []).length
            ? "An adjustment clip: its zooms move everything under it. Shown here: the clips under it, as they are – without their own effects."
            : "An adjustment clip with nothing under it to show – its zooms work on whatever goes under it.";
    }
    // The kinds to add.
    const kinds = $("zm-kinds");
    if (!kinds.children.length) {
        kinds.replaceChildren(...ZOOM_ORDER.map(k =>
            el("button.btn", {type: "button", text: ZOOM_KINDS[k].label, onclick: () => zmAdd(k)})));
    }
    // The selected zoom.
    const z = ZM.zooms[ZM.sel];
    $("zm-edit").hidden = !z;
    if (z) {
        $("zm-kind-name").textContent = ZOOM_KINDS[z.kind]?.label || "";
        const framed = zmFramed(z);
        const show = (id, value, text) => {
            if (document.activeElement !== $(id)) $(id).value = value;
            $(`${id}-text`).textContent = text;
        };
        show("zm-scale", framed.scale, `${framed.scale.toFixed(2)}×`);
        show("zm-tilt", z.tilt || 0, `${(z.tilt || 0).toFixed(1)}°`);
        show("zm-shake", z.shake || 0, `${Math.round((z.shake || 0) * 100)}%`);
        $("zm-bounce-row").hidden = z.kind !== "bounce";
        if (z.kind === "bounce") show("zm-bounce", z.bounce ?? .5, `${Math.round((z.bounce ?? .5) * 100)}%`);
        $("zm-period-row").hidden = z.kind !== "pulse";
        if (z.kind === "pulse") show("zm-period", z.period || .5, `${(z.period || .5).toFixed(2)}s`);
        $("zm-steps-row").hidden = z.kind !== "step";
        if (z.kind === "step") pressed($("zm-steps"), "steps", z.steps || 2);
        $("zm-blur").checked = !!z.blur;
        $("zm-points-row").hidden = !zmMoving(z);
        if (zmMoving(z)) {
            $("zm-point-name").textContent = `${clamp(ZM.pt, 0, z.points.length - 1) + 1} / ${z.points.length}`;
            $("zm-point-del").disabled = z.points.length <= 2;
            $("zm-point-add").disabled = z.points.length >= ZM_MAX_POINTS;
        }
        $("zm-times").textContent = zmTimes(z);
    }
    drawTrack();
}

// The stretch of the clip the track shows, and where a time sits on it.
const zmShown = () => ZM.range || [0, ZM.clip.duration || 1];
function zmX(t) {
    const [from, to] = zmShown();
    return (t - from) / (to - from) * ($("zm-lane").clientWidth || 1);
}
function zmTimeAt(clientX) {
    const r = $("zm-lane").getBoundingClientRect(), [from, to] = zmShown();
    return clamp(from + (clientX - r.left) / (r.width || 1) * (to - from), 0, ZM.clip.duration);
}

/* Alt+scroll zooms the track in time around the pointer (as Dailies'
   strip); zoomed in, scrolling pans it. All of it shown, a plain scroll is
   the page's. */
function zmZoomTrack(factor, around) {
    const dur = ZM.clip.duration, [from, to] = zmShown(), width = to - from;
    const next = clamp(width * factor, Math.min(dur, Math.max(.25, 8 / zmFps())), dur);
    if (next >= dur - 1e-6) ZM.range = null;
    else {
        const begin = clamp(around - (around - from) * next / width, 0, dur - next);
        ZM.range = [begin, begin + next];
    }
    drawTrack();
}

// Playing past the shown stretch: the track moves on with it.
function zmFollow(t) {
    if (!ZM.range) return;
    const [from, to] = ZM.range, width = to - from;
    if (t < from || t > to) {
        const begin = clamp(t - width * .1, 0, ZM.clip.duration - width);
        ZM.range = [begin, begin + width];
        drawTrack();
    }
}

function drawTrack() {
    const c = ZM.clip, lane = $("zm-lane"), [from, to] = zmShown(), width = to - from;
    const steps = [.1, .25, .5, 1, 2, 5, 10, 15, 30, 60];
    const step = steps.find(st => width / st <= 10) || 60;
    const ticks = [];
    for (let t = Math.ceil(from / step - 1e-6) * step; t < to - 1e-6; t += step) {
        const tick = el("span.hl-tick", {text: `${+t.toFixed(2)}s`});
        tick.style.left = `${zmX(t)}px`;
        ticks.push(tick);
    }
    $("zm-ruler").replaceChildren(...ticks);
    while (lane.children.length > ZM.zooms.length) lane.lastChild.remove();
    while (lane.children.length < ZM.zooms.length) {
        const block = el("div.zm-block", {}, [
            el("div.zm-ramps", {}, [el("div.zm-ramp.in"), el("div.zm-ramp.out")]),
            el("span.zm-label", {}, [el("span.zm-label-kind"), el("span", {translate: "no"})]),
            el("span.zm-edge.l", {title: "When it starts"}), el("span.zm-edge.r", {title: "When it ends"}),
            el("span.zm-knob.kin", {title: "How long it takes to zoom in"}),
            el("span.zm-knob.kout", {title: "How long it takes to zoom out"}),
        ]);
        lane.append(block);
    }
    ZM.zooms.forEach((z, i) => {
        const block = lane.children[i], len = Math.max(1e-6, z.end - z.start);
        block.dataset.i = i;
        block.style.left = `${zmX(z.start)}px`;
        block.style.width = `${zmX(z.end) - zmX(z.start)}px`;
        block.classList.toggle("sel", i === ZM.sel);
        block.querySelector(".zm-ramp.in").style.width = `${z.in / len * 100}%`;
        block.querySelector(".zm-ramp.out").style.width = `${z.out / len * 100}%`;
        // The kind, translated, and its size, not: one text, it would be neither.
        block.querySelector(".zm-label-kind").textContent = ZOOM_KINDS[z.kind]?.label || "";
        block.querySelector(".zm-label").lastChild.textContent = ` · ${z.scale.toFixed(2)}×`;
        block.querySelector(".kin").style.left = `${z.in / len * 100}%`;
        block.querySelector(".kout").style.left = `${(1 - z.out / len) * 100}%`;
        const marks = zmMoving(z) ? z.points : [];
        const have = [...block.querySelectorAll(".zm-pt")];
        have.slice(marks.length).forEach(m => m.remove());
        for (let k = have.length; k < marks.length; k++) {
            block.append(el("span.zm-pt", {title: "A framing it moves through – drag to change when"}));
        }
        [...block.querySelectorAll(".zm-pt")].forEach((m, k) => {
            m.dataset.k = k;
            m.style.left = `${marks[k].at / len * 100}%`;
            m.textContent = String(k + 1);
            m.classList.toggle("on", i === ZM.sel && k === ZM.pt);
        });
    });
}

// The time on screen: the player's, moved on by the clock while it plays.
function zmTime(now) {
    if (!ZM.playing) return ZM.t;
    const dur = ZM.clip.duration;
    const t = ZM.t + (now - ZM.at) / 1000;
    if (ZM.clip.kind === "image") {
        if (t >= dur) { ZM.t = 0; ZM.at = now; return 0; }        // a still loops on our clock
        return t;
    }
    return Math.min(t, dur);
}

function zmTick(now) {
    if ($("panel-zoom").hidden || !ZM.clip) return;
    const dur = ZM.clip.duration;
    const t = zmTime(now);
    $("zm-time").textContent = `${t.toFixed(2)}s / ${dur.toFixed(2)}s`;
    if (ZM.playing) zmFollow(t);
    const lx = zmX(t);
    $("zm-line").style.left = `${lx}px`;
    $("zm-line").hidden = lx < -1 || lx > ($("zm-lane").clientWidth || 0) + 1;
    if (ZM.icon !== ZM.playing) {
        ZM.icon = ZM.playing;
        $("zm-play").replaceChildren(Buddy.icon(ZM.playing ? "pause" : "play"));
    }
    const img = $("zm-img"), stage = $("zm-stage"), W = stage.clientWidth, H = stage.clientHeight;
    const z = ZM.zooms[ZM.sel];
    const framing = ZM.view === "framing";
    let blur = 0;
    if (framing) {
        img.style.transform = "";
    } else {
        const [s, cx, cy, deg] = zmAt(t);
        img.style.transform = `translate(${(cx - .5) * W}px, ${(cy - .5) * H}px) rotate(${-deg}deg) scale(${s})`;
        // Motion blur, as the Transform's: how far the picture moves in a frame.
        const live = ZM.zooms.find(q => q.blur && zmRunning(q, t, dur));
        if (live) {
            const [s2, cx2, cy2, deg2] = zmAt(t + 1 / zmFps());
            const moved = Math.abs(Math.log(s2 / s)) * .5 * W + Math.hypot((cx2 - cx) * W, (cy2 - cy) * H)
                + Math.abs(deg2 - deg) * W / 120;
            blur = Math.min(14, moved * .35);
        }
    }
    img.style.filter = blur > .3 ? `blur(${blur.toFixed(1)}px)` : "";
    const lut = $("zm-lut");
    lut.style.transform = img.style.transform;
    lut.style.filter = img.style.filter;
    const box = $("zm-frame");
    const target = z && zmFramed(z);
    box.hidden = !framing || !z;
    if (framing && z) {
        const w = W / target.scale, h = H / target.scale;
        Object.assign(box.style, {left: `${target.x * W - w / 2}px`, top: `${target.y * H - h / 2}px`,
                                  width: `${w}px`, height: `${h}px`});
    }
    // A moving zoom's other framings: numbered outlines, a click selects one.
    const ghosts = framing && zmMoving(z) ? z.points.map((p, k) => [p, k]).filter(([, k]) => k !== ZM.pt) : [];
    const layer = $("zm-ghosts");
    while (layer.children.length > ghosts.length) layer.lastChild.remove();
    while (layer.children.length < ghosts.length) layer.append(el("div.zm-ghost", {}, [el("span")]));
    ghosts.forEach(([p, k], n) => {
        const g = layer.children[n], w = W / p.scale, h = H / p.scale;
        g.dataset.k = k;
        g.firstChild.textContent = String(k + 1);
        Object.assign(g.style, {left: `${p.x * W - w / 2}px`, top: `${p.y * H - h / 2}px`, width: `${w}px`, height: `${h}px`});
    });
    $("zm-frame-num").textContent = framing && zmMoving(z) ? String(clamp(ZM.pt, 0, z.points.length - 1) + 1) : "";
    $("zm-note").textContent = ZM.failed || (!ZM.ready ? "Loading the clip…"
        : framing && !z ? "Select a zoom on the track to set where it zooms to" : "");
}

// ------------------------------------------------------------- controls

function zmSeek(t) {
    if (!ZM.clip) return;
    ZM.t = clamp(t, 0, ZM.clip.duration);
    ZM.at = performance.now();
    if (ZM.clip.kind === "image") return;           // a still plays on the page's own clock
    // At most one seek in flight every 40 ms; the last always goes.
    clearTimeout(ZM.seekTimer);
    const go = () => { ZM.sent = performance.now(); send("zm_seek", {t: ZM.t}); };
    if (performance.now() - ZM.sent > 40) go();
    else ZM.seekTimer = setTimeout(go, 40);
}

function zmToggle() {
    if (!ZM.clip || !ZM.ready) return;
    if (!ZM.playing) ZM.view = "result";                // playing shows the result
    if (ZM.clip.kind === "image") {
        ZM.t = zmTime(performance.now());
        ZM.at = performance.now();
        ZM.playing = !ZM.playing;
        if (ZM.playing && ZM.t >= ZM.clip.duration - .02) ZM.t = 0;
        drawZoom();
        return;
    }
    send("zm_toggle");
    drawZoom();
}

function zmAdd(kind) {
    if (!ZM.clip) return;
    const z = zmNew(kind, zmTime(performance.now()));
    if (!z) return Buddy.toast("There's no room for a zoom there – move the playhead or another zoom.");
    zmSnapshot();
    ZM.zooms.push(z);
    ZM.zooms.sort((a, b) => a.start - b.start);
    ZM.sel = ZM.zooms.indexOf(z);
    ZM.view = "framing";                                // next: where it zooms to
    zmChanged();
}

// A moving zoom's framings keep their places in it as it's made longer or shorter.
function zmStretch(z, z0) {
    if (!zmMoving(z)) return;
    const k = (z.end - z.start) / Math.max(1e-6, z0.end - z0.start);
    z.points = z0.points.map(p => ({...p, at: p.at * k}));
}

// A framing's mark, dragged along its block: between its neighbours.
function zmDragPoint(e, i, k, perPx) {
    const z = ZM.zooms[i], x0 = e.clientX, at0 = z.points[k].at, len = z.end - z.start;
    ZM.sel = i;
    ZM.pt = k;
    zmSeek(z.start + at0);
    zmSnapshot();
    const mark = e.target;
    mark.setPointerCapture(e.pointerId);
    const lo = k > 0 ? z.points[k - 1].at + 1 / zmFps() : 0;
    const hi = k + 1 < z.points.length ? z.points[k + 1].at - 1 / zmFps() : len;
    const move = m => {
        z.points[k].at = clamp(at0 + (m.clientX - x0) * perPx, lo, Math.max(lo, hi));
        zmSeek(z.start + z.points[k].at);
        zmChanged();
    };
    const up = () => {
        mark.removeEventListener("pointermove", move);
        mark.removeEventListener("pointerup", up);
        mark.removeEventListener("pointercancel", up);
        if (zmSame(ZM.undo[ZM.undo.length - 1] || [], ZM.zooms)) ZM.undo.pop();
        drawZoom();
    };
    mark.addEventListener("pointermove", move);
    mark.addEventListener("pointerup", up);
    mark.addEventListener("pointercancel", up);
    drawZoom();
}

// A framing added where the playhead is, as the path has it there.
function zmAddPoint() {
    const z = ZM.zooms[ZM.sel];
    if (!zmMoving(z) || z.points.length >= ZM_MAX_POINTS) return;
    const t = zmTime(performance.now()), at = t - z.start;
    if (at <= 0 || at >= z.end - z.start) return Buddy.toast("Move the playhead into the zoom first.");
    const [scale, x, y] = zmTarget(z, t);
    zmSnapshot();
    z.points.push({at, scale, x, y});
    z.points.sort((a, b) => a.at - b.at);
    ZM.pt = z.points.findIndex(p => p.at === at);
    ZM.view = "framing";
    zmChanged();
}

function zmDeletePoint() {
    const z = ZM.zooms[ZM.sel];
    if (!zmMoving(z) || z.points.length <= 2) return;
    zmSnapshot();
    z.points.splice(clamp(ZM.pt, 0, z.points.length - 1), 1);
    ZM.pt = Math.min(ZM.pt, z.points.length - 1);
    zmChanged();
}

function zmDelete() {
    if (ZM.sel < 0) return;
    zmSnapshot();
    ZM.zooms.splice(ZM.sel, 1);
    ZM.sel = -1;
    zmChanged();
}

/* A drag on the track: a block's body, ends or dots. Every change stays
   between its neighbours and inside the clip, a frame at a time. */
$("zm-lane").addEventListener("pointerdown", e => {
    if (!ZM.clip || e.button !== 0) return;
    const block = e.target.closest(".zm-block"), lane = $("zm-lane");
    const w = lane.clientWidth || 1, dur = ZM.clip.duration;
    const [shownFrom, shownTo] = zmShown(), perPx = (shownTo - shownFrom) / w;
    if (!block) {                                        // empty track: the playhead goes there
        ZM.sel = -1;
        zmSeek(zmTimeAt(e.clientX));
        return drawZoom();
    }
    e.preventDefault();
    $("zm-track").focus();
    const mark = e.target.closest(".zm-pt");
    if (mark) return zmDragPoint(e, Number(block.dataset.i), Number(mark.dataset.k), perPx);
    const i = Number(block.dataset.i), z0 = zmClone([ZM.zooms[i]])[0], x0 = e.clientX;
    const part = e.target.classList.contains("l") ? "start" : e.target.classList.contains("r") ? "end"
        : e.target.classList.contains("kin") ? "in" : e.target.classList.contains("kout") ? "out" : "move";
    const lo = i > 0 ? ZM.zooms[i - 1].end : 0, hi = i + 1 < ZM.zooms.length ? ZM.zooms[i + 1].start : dur;
    ZM.sel = i;
    zmSnapshot();
    block.classList.add("dragging");
    block.setPointerCapture(e.pointerId);
    const move = m => {
        const dt = (m.clientX - x0) * perPx, z = ZM.zooms[i], len0 = z0.end - z0.start;
        const least = Math.max(ZM_MIN.len, z0.in + z0.out);
        if (part === "move") {
            z.start = clamp(zmSnap(z0.start + dt), lo, hi - len0);
            z.end = z.start + len0;
        } else if (part === "start") {
            z.start = clamp(zmSnap(z0.start + dt), lo, z0.end - least);
            zmStretch(z, z0);
        } else if (part === "end") {
            z.end = clamp(zmSnap(z0.end + dt), z0.start + least, hi);
            zmStretch(z, z0);
        } else if (part === "in") {
            z.in = clamp(z0.in + dt, 0, len0 - z0.out);
        } else {
            z.out = clamp(z0.out - dt, 0, len0 - z0.in);
        }
        zmChanged();
    };
    const up = () => {
        block.classList.remove("dragging");
        block.removeEventListener("pointermove", move);
        block.removeEventListener("pointerup", up);
        block.removeEventListener("pointercancel", up);
        if (zmSame(ZM.undo[ZM.undo.length - 1] || [], ZM.zooms)) ZM.undo.pop();    // only a click
        drawZoom();
    };
    block.addEventListener("pointermove", move);
    block.addEventListener("pointerup", up);
    block.addEventListener("pointercancel", up);
    drawZoom();
});

// The ruler moves the playhead (and follows a drag).
$("zm-ruler").addEventListener("pointerdown", e => {
    if (!ZM.clip || e.button !== 0) return;
    e.preventDefault();
    const ruler = $("zm-ruler");
    const seek = m => zmSeek(zmTimeAt(m.clientX));
    seek(e);
    ruler.setPointerCapture(e.pointerId);
    const up = () => {
        ruler.removeEventListener("pointermove", seek);
        ruler.removeEventListener("pointerup", up);
        ruler.removeEventListener("pointercancel", up);
    };
    ruler.addEventListener("pointermove", seek);
    ruler.addEventListener("pointerup", up);
    ruler.addEventListener("pointercancel", up);
});

/* Framing: the selected zoom's box on the whole picture. Drag it to move
   where it zooms to, a corner for how far in; a click elsewhere puts its
   middle there. Always inside the picture. The wheel is left alone: it
   scrolls the page (the user's ask), and Zoom's slider and corners size it. */
$("zm-stage").addEventListener("pointerdown", e => {
    const zoom = ZM.zooms[ZM.sel];
    if (ZM.view !== "framing" || !zoom || e.button !== 0) return;
    e.preventDefault();
    const ghost = e.target.closest(".zm-ghost");
    if (ghost) {
        ZM.pt = Number(ghost.dataset.k);
        zmSeek(zoom.start + zoom.points[ZM.pt].at);
        drawZoom();
    }
    const z = zmFramed(zoom);
    const stage = $("zm-stage"), r = stage.getBoundingClientRect(), W = r.width, H = r.height;
    const corner = e.target.dataset.corner;
    const z0 = {...z}, x0 = e.clientX, y0 = e.clientY;
    zmSnapshot();
    if (!corner && !e.target.closest(".zm-frame") && !ghost) {
        z.x = zmInside((e.clientX - r.left) / W, z.scale);
        z.y = zmInside((e.clientY - r.top) / H, z.scale);
        Object.assign(z0, {x: z.x, y: z.y});
        zmChanged();
    }
    stage.setPointerCapture(e.pointerId);
    const move = m => {
        if (corner) {
            // The box keeps its middle and the frame's shape; the corner sets its size.
            const dx = Math.abs(m.clientX - (r.left + z0.x * W)), dy = Math.abs(m.clientY - (r.top + z0.y * H));
            z.scale = clamp(Math.min(W / (2 * Math.max(dx, 1)), H / (2 * Math.max(dy, 1))), ZM_MIN.scale, ZM_MAX_SCALE);
            z.x = zmInside(z0.x, z.scale);
            z.y = zmInside(z0.y, z.scale);
        } else {
            z.x = zmInside(z0.x + (m.clientX - x0) / W, z.scale);
            z.y = zmInside(z0.y + (m.clientY - y0) / H, z.scale);
        }
        zmChanged();
    };
    const up = () => {
        stage.removeEventListener("pointermove", move);
        stage.removeEventListener("pointerup", up);
        stage.removeEventListener("pointercancel", up);
        if (zmSame(ZM.undo[ZM.undo.length - 1] || [], ZM.zooms)) ZM.undo.pop();
    };
    stage.addEventListener("pointermove", move);
    stage.addEventListener("pointerup", up);
    stage.addEventListener("pointercancel", up);
});

$("zm-track").addEventListener("wheel", e => {
    if (!ZM.clip) return;
    if (e.altKey) {
        e.preventDefault();
        // Alt turns a vertical wheel into a horizontal one on some systems.
        return zmZoomTrack(Math.exp((e.deltaY || e.deltaX) * .0015), zmTimeAt(e.clientX));
    }
    if (!ZM.range) return;
    e.preventDefault();
    const [from, to] = ZM.range, width = to - from;
    const pan = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    const begin = clamp(from + pan / ($("zm-lane").clientWidth || 1) * width, 0, ZM.clip.duration - width);
    ZM.range = [begin, begin + width];
    drawTrack();
}, {passive: false});

// A slider: one undo step a drag (or a key), the zoom changed as it moves.
function zmSlider(id, set) {
    $(id).addEventListener("pointerdown", () => zmSnapshot());
    $(id).addEventListener("keydown", () => zmSnapshot());
    $(id).addEventListener("input", () => {
        const z = ZM.zooms[ZM.sel];
        if (!z) return;
        set(z, Number($(id).value));
        zmChanged();
    });
}
zmSlider("zm-scale", (z, v) => {
    const framed = zmFramed(z);
    framed.scale = clamp(v, ZM_MIN.scale, ZM_MAX_SCALE);
    framed.x = zmInside(framed.x, framed.scale);
    framed.y = zmInside(framed.y, framed.scale);
});
zmSlider("zm-tilt", (z, v) => { z.tilt = clamp(v, -20, 20); });
zmSlider("zm-shake", (z, v) => { z.shake = clamp(v, 0, 1); });
zmSlider("zm-bounce", (z, v) => { z.bounce = clamp(v, 0, 1); });
zmSlider("zm-period", (z, v) => { z.period = clamp(v, .15, 4); });
for (const b of $("zm-steps").querySelectorAll("button")) {
    b.onclick = () => {
        const z = ZM.zooms[ZM.sel];
        if (!z) return;
        zmSnapshot();
        z.steps = Number(b.dataset.steps);
        zmChanged();
    };
}
$("zm-blur").addEventListener("change", () => {
    const z = ZM.zooms[ZM.sel];
    if (!z) return;
    zmSnapshot();
    z.blur = $("zm-blur").checked;
    zmChanged();
});
$("zm-point-add").onclick = zmAddPoint;

// Copy / paste: the copied zooms are page.py's (kept with the settings).
$("zm-copy").onclick = () => ZM.clip && send("zm_copy", {zooms: ZM.zooms, duration: ZM.clip.duration, name: ZM.clip.name});
$("zm-paste").onclick = () => {
    const copied = state.zm_clipboard;
    if (!ZM.clip || !copied) return;
    const r = $("zm-paste").getBoundingClientRect();
    const paste = mode => ZM.clip && send("zm_paste", {id: ZM.clip.id, mode, duration: ZM.clip.duration});
    Buddy.menu({x: r.left, y: r.bottom + 4, items: [
        {heading: copied.name ? `Zooms from ${copied.name}` : "Copied zooms"},
        {label: "At the same times", onClick: () => paste("same")},
        {label: "Stretched to this clip", onClick: () => paste("stretch")},
    ]});
};
for (const mode of ["same", "stretch"]) {
    $(`zm-paste-${mode}`).onclick = () => { ZM.pasting = mode; send("zm_paste_many", {mode}); };
}
Buddy.on("zm_pasted", data => {
    if (!ZM.clip || data.id !== ZM.clip.id) return;
    zmSnapshot();
    ZM.zooms = zmClone(data.zooms);
    ZM.sel = ZM.zooms.length ? 0 : -1;
    ZM.pt = 0;
    zmChanged();
});
$("zm-point-del").onclick = zmDeletePoint;

for (const b of $("zm-view").querySelectorAll("button")) b.onclick = () => { ZM.view = b.dataset.view; drawZoom(); };
$("zm-play").onclick = zmToggle;
$("zm-back").onclick = () => zmSeek(zmTime(performance.now()) - 1 / zmFps());
$("zm-fwd").onclick = () => zmSeek(zmTime(performance.now()) + 1 / zmFps());
$("zm-delete").onclick = zmDelete;
$("zm-undo").replaceChildren(Buddy.icon("undo"));
$("zm-redo").replaceChildren(Buddy.icon("undo"));
$("zm-redo").firstChild.style.transform = "scaleX(-1)";
$("zm-undo").onclick = () => zmStep(ZM.undo, ZM.redo);
$("zm-redo").onclick = () => zmStep(ZM.redo, ZM.undo);
$("zm-apply").onclick = () => ZM.clip && send("zm_apply", {id: ZM.clip.id, zooms: ZM.zooms});
$("zm-remove").onclick = () => {
    if (!ZM.clip) return;
    zmSnapshot();
    ZM.zooms = [];
    ZM.sel = -1;
    send("zm_apply", {id: ZM.clip.id, zooms: []});
    zmChanged();
};
new ResizeObserver(() => ZM.clip && drawTrack()).observe($("zm-track"));

document.addEventListener("keydown", e => {
    if ($("panel-zoom").hidden || $("zm-card").hidden || typing(e) || document.querySelector(".menu-pop")) return;
    const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
    if (e.key === " ") { e.preventDefault(); if (!e.repeat) zmToggle(); }
    else if (mod && key === "z") { e.preventDefault(); e.shiftKey ? zmStep(ZM.redo, ZM.undo) : zmStep(ZM.undo, ZM.redo); }
    else if (mod && key === "y") { e.preventDefault(); zmStep(ZM.redo, ZM.undo); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        zmSeek(zmTime(performance.now()) + (e.key === "ArrowLeft" ? -1 : 1) / zmFps());
    } else if ((e.key === "Delete" || e.key === "Backspace") && ZM.sel >= 0) { e.preventDefault(); zmDelete(); }
});
document.addEventListener("keyup", e => {
    if (e.key === " " && !$("panel-zoom").hidden && !typing(e)) e.preventDefault();
});

// ---- Graded: the picture drawn through the clip's grade, a 3D LUT Resolve
// exports (zoom_resolve.export_grade), on a WebGL2 canvas over the picture -
// every frame, playing too. Colour only, and close to Resolve's, not exact.
const ZG = {gl: null, failed: false, pic: null, lut: null, n: null};

function zgSetup() {
    if (ZG.gl || ZG.failed) return ZG.gl;
    const gl = $("zm-lut").getContext("webgl2", {alpha: false, antialias: false});
    const shader = (type, src) => {
        const sh = gl.createShader(type);
        gl.shaderSource(sh, src);
        gl.compileShader(sh);
        return sh;
    };
    const prog = gl && gl.createProgram();
    if (prog) {
        gl.attachShader(prog, shader(gl.VERTEX_SHADER, `#version 300 es
in vec2 p; out vec2 uv;
void main() { uv = vec2(p.x * .5 + .5, .5 - p.y * .5); gl_Position = vec4(p, 0., 1.); }`));
        gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, `#version 300 es
precision highp float; precision highp sampler3D;
uniform sampler2D pic; uniform sampler3D lut; uniform float n;
in vec2 uv; out vec4 o;
// A cube's n points span 0-1: sample between the first and last texels' middles.
void main() { o = vec4(texture(lut, texture(pic, uv).rgb * ((n - 1.) / n) + .5 / n).rgb, 1.); }`));
        gl.linkProgram(prog);
    }
    if (!prog || !gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        ZG.failed = true;
        return null;
    }
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const at = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(at);
    gl.vertexAttribPointer(at, 2, gl.FLOAT, false, 0, 0);
    const texture = (unit, target) => {
        const t = gl.createTexture();
        gl.activeTexture(gl.TEXTURE0 + unit);
        gl.bindTexture(target, t);
        for (const w of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T, gl.TEXTURE_WRAP_R]) gl.texParameteri(target, w, gl.CLAMP_TO_EDGE);
        gl.texParameteri(target, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(target, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        return t;
    };
    ZG.pic = texture(0, gl.TEXTURE_2D);
    ZG.lut = texture(1, gl.TEXTURE_3D);
    gl.uniform1i(gl.getUniformLocation(prog, "pic"), 0);
    gl.uniform1i(gl.getUniformLocation(prog, "lut"), 1);
    ZG.n = gl.getUniformLocation(prog, "n");
    ZG.gl = gl;
    return gl;
}

// The clip's grade onto the GPU (once per grade).
function zgLoad(lut) {
    const gl = zgSetup();
    if (!gl) return false;
    gl.activeTexture(gl.TEXTURE1);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);         // rows of n * 3 bytes
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB8, lut.size, lut.size, lut.size, 0, gl.RGB, gl.UNSIGNED_BYTE, lut.data);
    gl.uniform1f(ZG.n, lut.size);
    return true;
}

// The picture as it is now, through the grade.
function zgDraw() {
    const img = $("zm-img"), gl = ZG.gl;
    if (!gl || !ZM.graded || !ZM.lut || !img.naturalWidth) return;
    const canvas = $("zm-lut");
    if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
    }
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.activeTexture(gl.TEXTURE0);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}
$("zm-img").addEventListener("load", zgDraw);

function zmGrade(again) {
    if (!ZM.clip || state.working) return;
    if (ZM.graded && !again) {
        ZM.graded = false;
        return drawZoom();
    }
    if (!zgSetup()) return Buddy.toast("This computer's graphics can't show the grade here.");
    ZM.graded = true;
    if (ZM.lut && ZM.lut.id === ZM.clip.id && !again) {
        drawZoom();
        return zgDraw();
    }
    send("zm_grade", {id: ZM.clip.id});
    drawZoom();
}
$("zm-graded").onclick = () => zmGrade(false);
$("zm-regrade").replaceChildren(Buddy.icon("refresh"));
$("zm-regrade").onclick = () => zmGrade(true);
Buddy.on("zm_grade", data => {
    if (!ZM.clip || data.id !== ZM.clip.id) return;
    if (!data.lut) {
        ZM.graded = false;
        return drawZoom();
    }
    const raw = atob(data.lut), bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    ZM.lut = {id: data.id, size: data.size, data: bytes};
    if (!zgLoad(ZM.lut)) ZM.graded = false;
    drawZoom();
    zgDraw();
});

Buddy.on("zm_frame", data => {
    if (ZM.clip && data.id === ZM.clip.id) $("zm-img").src = data.src;
});
Buddy.on("zm_ready", data => {
    if (!ZM.clip || data.id !== ZM.clip.id) return;
    ZM.ready = true;
    drawZoom();
});
Buddy.on("zm_time", data => {
    if (!ZM.clip || data.id !== ZM.clip.id) return;
    // While seeking, the player's late answers would pull the playhead back.
    if (!data.playing && performance.now() - ZM.sent < 250) return;
    ZM.t = data.t;
    ZM.at = performance.now();
    ZM.playing = data.playing;
});
Buddy.on("zm_failed", data => {
    if (!ZM.clip || data.id !== ZM.clip.id) return;
    ZM.failed = data.text;
    ZM.ready = false;
});

// ------------------------------------------------------------------ events

Buddy.on("presets", data => {
    packs = data.packs;
    kinds = data.kinds;
    presets = data.presets.map(prepare);
    big = null;
    drawGroups();
    drawChosen();
    if (!$("panel-editor").hidden) editorShown();
});

Buddy.on("keys", data => {
    keyCache[data.id] = data.keys;
    if (ed.waiting === data.id) setKeys(data.keys);
});

Buddy.on("saved", data => {
    const p = presets.find(x => x.id === data.id);
    if (!p) return;
    Object.assign(ed, {id: p.id, saved: true, label: p.label, dirty: false});
    drawEditor();
});

Buddy.on("state", data => {
    const shapeChanged = data.shape !== state.shape || data.unit !== state.unit || data.order !== state.order;
    const before = JSON.stringify([state.tab === "favorites", [...favorites]]);
    state = data;
    favorites = new Set(state.favorites || []);
    const opened = JSON.stringify(state.open_packs ?? null) !== JSON.stringify(openPacks);
    openPacks = state.open_packs ?? null;
    showTab(state.tab);
    pressed($("way"), "way", state.way);
    pressed($("speed"), "speed", state.speed);
    pressed($("direction"), "direction", state.direction || "own");
    if (state.direction !== aimed) {
        aimed = state.direction;
        for (const p of presets) aim(p);
    }
    pressed($("unit"), "unit", state.unit);
    pressed($("stagger"), "stagger", state.stagger);
    $("order").value = state.order;
    pressed($("play"), "play", state.play);
    paintColours();
    if (opened || JSON.stringify([state.tab === "favorites", [...favorites]]) !== before) drawGroups();
    for (const b of document.querySelectorAll(".pv-act.heart")) {
        const on = favorites.has(b.dataset.id);
        b.setAttribute("aria-pressed", String(on));
        b.title = on ? "Take it out of Favorites" : "Add to Favorites";
    }
    for (const t of tiles) t.node.querySelector(".pv-dur").textContent = tileSeconds(t.p);
    drawChosen();
    drawSelection();
    drawHighlight();
    zmSync();
    if (typeof trSync === "function") trSync();
    if (state.tab === "editor") editorShown();
    if (shapeChanged) reshape();
});

Buddy.on("toast", data => Buddy.toast(data.text));
Buddy.on("alert", data => Buddy.modal({
    title: data.title,
    body: el("p.modal-text", {text: data.text}),
    buttons: [{label: "Got it", kind: "accent"}],
}));

requestAnimationFrame(tick);
