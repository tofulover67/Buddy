/*
 * The DAW's controls, shared by the three layouts so each behaves the same
 * everywhere:
 *
 *   Ctl.knob(spec)      drag up or down to turn (Shift: fine), scroll to nudge,
 *                       arrow keys, double-click for its default, Ctrl + click to
 *                       type a value
 *   Ctl.fader(spec)     a track's level: drag, double-click for 0 dB
 *   Ctl.meter()         green, then amber, red only at the top
 *   Ctl.seg(...)        a row of choices, one lit
 *   Ctl.keyboard(spec)  a piano: the scale marked, the computer's letters on the
 *                       keys they play, what's sounding lit
 *   Ctl.roll(spec)      a piano roll for one clip: click to add a note, drag to
 *                       move, drag its right end to lengthen, a velocity lane
 *                       under it; Delete, arrows, Ctrl+A and Ctrl+D on the
 *                       selected notes; a ruler over it to click or drag the
 *                       playhead; Alt + scroll (or Ctrl) zooms along it
 */
"use strict";

const Ctl = (() => {
    const {el} = Buddy;
    const SVG = "http://www.w3.org/2000/svg";
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

    // ------------------------------------------------------------ knob --

    const pt = (c, r, deg) => [c + r * Math.sin(deg * Math.PI / 180), c - r * Math.cos(deg * Math.PI / 180)];
    function arc(c, r, a0, a1) {
        const [x0, y0] = pt(c, r, a0), [x1, y1] = pt(c, r, a1);
        return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${Math.abs(a1 - a0) > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
    }

    /* A value as it's shown: by its unit. */
    function shown(spec, v) {
        if (spec.format) return spec.format(v);
        switch (spec.unit) {
        case "s": return v < 1 ? `${Math.round(v * 1000)} ms` : `${v.toFixed(2)} s`;
        case "oct": return v === 0 ? "0" : `${v > 0 ? "+" : "−"}${Math.abs(v)}`;
        case "st": return v === 0 ? "0" : `${v > 0 ? "+" : "−"}${Math.abs(v)} st`;
        case "pan": return Math.abs(v) < 0.02 ? "C" : `${v < 0 ? "L" : "R"}${Math.round(Math.abs(v) * 100)}`;
        case "%": return `${Math.round(v)}%`;
        default: return String(Math.round(v * 100) / 100);
        }
    }

    /* spec: {label, min, max, value, def, step, log, unit, bipolar, size, hint, onStart, onInput(v), format} */
    function knob(spec) {
        const size = spec.size || 44, c = size / 2, ring = c - 3, body = c - 9;
        const node = el("div.knob", {tabindex: "0", role: "slider", "aria-label": spec.label,
            title: spec.hint ? `${Buddy.t(spec.hint)}. ${Buddy.t("Double-click to reset")}` : Buddy.t("Double-click to reset")});
        node.style.setProperty("--knob", `${size}px`);
        const svg = document.createElementNS(SVG, "svg");
        svg.setAttribute("width", size);
        svg.setAttribute("height", size);
        svg.innerHTML = `<path class="knob-track" d="${arc(c, ring, -135, 135)}"/><path class="knob-val"/>`
            + `<circle class="knob-body" cx="${c}" cy="${c}" r="${body}"/><circle class="knob-rim" cx="${c}" cy="${c}" r="${Math.max(2, body - 4)}"/>`
            + `<line class="knob-ptr" x1="${c}" y1="${c - body + 4}" x2="${c}" y2="${c - body * 0.42}"/>`;
        const valueOut = el("span.kv", {translate: "no"});
        node.append(svg, valueOut, el("span.kl", {text: spec.label}));
        let value = spec.value;
        const toU = v => (spec.log ? Math.log(v / spec.min) / Math.log(spec.max / spec.min) : (v - spec.min) / (spec.max - spec.min));
        const fromU = u => {
            u = clamp(u, 0, 1);
            let v = spec.log ? spec.min * Math.pow(spec.max / spec.min, u) : spec.min + u * (spec.max - spec.min);
            if (spec.step) v = Math.round(v / spec.step) * spec.step;
            return spec.log ? +v.toPrecision(3) : +v.toFixed(4);
        };
        function draw() {
            const u = clamp(toU(value), 0, 1), deg = -135 + u * 270, from = spec.bipolar ? 0 : -135;
            const p = svg.querySelector(".knob-val");
            if (Math.abs(deg - from) < 0.5) p.removeAttribute("d");
            else p.setAttribute("d", deg >= from ? arc(c, ring, from, deg) : arc(c, ring, deg, from));
            svg.querySelector(".knob-ptr").setAttribute("transform", `rotate(${deg} ${c} ${c})`);
            valueOut.textContent = shown(spec, value);
            node.setAttribute("aria-valuetext", valueOut.textContent);
        }
        function set(v, tell = true) {
            v = clamp(v, spec.min, spec.max);
            if (v === value) return;
            value = v;
            draw();
            if (tell && spec.onInput) spec.onInput(value);
        }
        let drag = null;
        node.addEventListener("pointerdown", e => {
            if (e.button !== 0) return;
            e.preventDefault();
            node.focus();
            if (e.ctrlKey || e.metaKey) return type();
            drag = {y: e.clientY, u: toU(value), started: false};
            node.setPointerCapture(e.pointerId);
            node.classList.add("dragging");
        });
        node.addEventListener("pointermove", e => {
            if (!drag) return;
            if (!drag.started) { drag.started = true; if (spec.onStart) spec.onStart(); }
            set(fromU(drag.u + (drag.y - e.clientY) / (e.shiftKey ? 700 : 160)));
        });
        const up = () => { drag = null; node.classList.remove("dragging"); };
        node.addEventListener("pointerup", up);
        node.addEventListener("pointercancel", up);
        node.addEventListener("dblclick", () => {
            if (spec.def === undefined) return;
            if (spec.onStart) spec.onStart();
            set(spec.def);
        });
        let wheelTimer = 0;
        node.addEventListener("wheel", e => {
            e.preventDefault();
            if (!wheelTimer && spec.onStart) spec.onStart();
            clearTimeout(wheelTimer);
            wheelTimer = setTimeout(() => { wheelTimer = 0; }, 600);
            const nudge = spec.step ? spec.step / (spec.max - spec.min) : 0.02;
            set(fromU(toU(value) - Math.sign(e.deltaY) * Math.max(nudge, 0.01) * (e.shiftKey ? 0.25 : 1)));
        }, {passive: false});
        node.addEventListener("keydown", e => {
            const dir = {ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1}[e.key];
            if (!dir) return;
            e.preventDefault();
            e.stopPropagation();
            if (spec.onStart && !e.repeat) spec.onStart();
            const nudge = spec.step ? spec.step / (spec.max - spec.min) : 0.02;
            set(fromU(toU(value) + dir * Math.max(nudge, 0.01) * (e.shiftKey ? 0.25 : 1)));
        });
        /* Ctrl + click: type the value. */
        function type() {
            const input = el("input.field.knob-type", {type: "text", value: String(+value.toFixed(3)), translate: "no"});
            node.append(input);
            input.select();
            const done = ok => {
                if (!input.isConnected) return;
                const v = parseFloat(input.value.replace(",", ".").replace("−", "-"));
                input.remove();
                if (ok && Number.isFinite(v)) {
                    if (spec.onStart) spec.onStart();
                    set(spec.unit === "s" && /ms/i.test(input.value) ? v / 1000 : v);
                }
                node.focus();
            };
            input.addEventListener("keydown", e => {
                e.stopPropagation();
                if (e.key === "Enter") done(true);
                if (e.key === "Escape") done(false);
            });
            input.addEventListener("blur", () => done(true));
            input.addEventListener("pointerdown", e => e.stopPropagation());
        }
        draw();
        return {node, set: v => set(v, false), get: () => value};
    }

    // ------------------------------------------------------------ fader --

    /* spec: {value (0-1.25), def, onStart, onInput(v), height} - 0.8 is 0 dB. */
    function fader(spec) {
        const node = el("div.fader", {tabindex: "0", role: "slider", "aria-label": spec.label || "Level",
            title: Buddy.t("Double-click for 0 dB")});
        const h = spec.height || 120;
        node.style.height = `${h}px`;
        const fill = el("div.fill"), cap = el("div.cap");
        node.append(el("div.slot"), fill, cap);
        let value = spec.value;
        const MAX = 1.25;
        function draw() {
            const u = value / MAX;
            cap.style.top = `${4 + (1 - u) * (h - 8 - 14)}px`;
            fill.style.height = `${u * (h - 8)}px`;
        }
        function set(v, tell = true) {
            value = clamp(v, 0, MAX);
            draw();
            if (tell && spec.onInput) spec.onInput(value);
        }
        let drag = null;
        node.addEventListener("pointerdown", e => {
            if (e.button !== 0) return;
            e.preventDefault();
            node.focus();
            drag = {y: e.clientY, v: value, started: false};
            node.setPointerCapture(e.pointerId);
        });
        node.addEventListener("pointermove", e => {
            if (!drag) return;
            if (!drag.started) { drag.started = true; if (spec.onStart) spec.onStart(); }
            set(drag.v + (drag.y - e.clientY) / (h - 22) * MAX * (e.shiftKey ? 0.25 : 1));
        });
        node.addEventListener("pointerup", () => { drag = null; });
        node.addEventListener("dblclick", () => { if (spec.onStart) spec.onStart(); set(spec.def ?? 0.8); });
        node.addEventListener("keydown", e => {
            const dir = {ArrowUp: 1, ArrowDown: -1}[e.key];
            if (!dir) return;
            e.preventDefault();
            e.stopPropagation();
            if (spec.onStart && !e.repeat) spec.onStart();
            set(value + dir * 0.02);
        });
        node.addEventListener("wheel", e => { e.preventDefault(); set(value - Math.sign(e.deltaY) * 0.02); }, {passive: false});
        draw();
        return {node, set: v => set(v, false), get: () => value};
    }

    function meter(height = 120) {
        const bar = el("i");
        const node = el("div.meter", {}, [bar]);
        node.style.height = `${height}px`;
        bar.style.backgroundSize = `100% ${height}px`;
        let shownLevel = 0;
        return {node, set(peak) {
            // dBFS to the meter: -48 at the bottom, 0 at the top; quick up, slower down.
            const db = peak > 1e-5 ? 20 * Math.log10(peak) : -60;
            const u = clamp((db + 48) / 48, 0, 1);
            shownLevel = u > shownLevel ? u : Math.max(u, shownLevel - 0.04);
            bar.style.height = `${(shownLevel * 100).toFixed(1)}%`;
            node.classList.toggle("clip", peak >= 0.999);
        }};
    }

    /* options [[value, label, title?]] */
    function seg(options, value, onPick, label = "") {
        const node = el("div.seg", {role: "group", "aria-label": label});
        const buttons = options.map(([v, text, title]) => el("button", {type: "button", text, title, "aria-pressed": String(v === value),
            onclick: () => { set(v); onPick(v); }}));
        node.append(...buttons);
        function set(v) { options.forEach(([x], i) => buttons[i].setAttribute("aria-pressed", String(x === v))); }
        return {node, set};
    }

    // ------------------------------------------------------------ keyboard --

    // The computer's keys, by where they are: white keys on the middle row, black above - from C.
    const CODES = {KeyA: 0, KeyW: 1, KeyS: 2, KeyE: 3, KeyD: 4, KeyF: 5, KeyT: 6, KeyG: 7, KeyY: 8, KeyH: 9,
                   KeyU: 10, KeyJ: 11, KeyK: 12, KeyO: 13, KeyL: 14, KeyP: 15, Semicolon: 16, Quote: 17};
    const LETTER = {KeyA: "A", KeyW: "W", KeyS: "S", KeyE: "E", KeyD: "D", KeyF: "F", KeyT: "T", KeyG: "G", KeyY: "Y",
                    KeyH: "H", KeyU: "U", KeyJ: "J", KeyK: "K", KeyO: "O", KeyL: "L", KeyP: "P", Semicolon: ";", Quote: "'"};
    const isBlack = m => [1, 3, 6, 8, 10].includes(((m % 12) + 12) % 12);

    /* spec: {octaves, base() (the C the computer's keys start on), drums(), onDown(source, midi, vel), onUp(source)} */
    function keyboard(spec) {
        const node = el("div.piano", {role: "group", "aria-label": "Keyboard"});
        let first = 0;
        function draw() {
            const base = spec.base();
            first = base - 12;
            const drumRows = spec.drums && spec.drums();
            const keys = [];
            for (let m = first; m <= first + 12 * (spec.octaves || 3); m++) {
                const code = Object.keys(CODES).find(k => CODES[k] === m - base);
                const k = el(`div.pk.${isBlack(m) ? "b" : "w"}`, {dataset: {midi: m}}, [
                    code ? el("span.pk-letter", {text: LETTER[code], translate: "no"}) : null,
                    drumRows && !isBlack(m) ? el("span.pk-name", {text: drumRows(m) || "", translate: "no"})
                        : m % 12 === 0 ? el("span.pk-name", {text: `C${m / 12 - 1}`, translate: "no"}) : null,
                ]);
                if (!drumRows && Daw.inScale(m)) k.classList.add(Daw.isRoot(m) ? "root" : "in");
                keys.push(k);
            }
            node.replaceChildren(...keys);
        }
        function light(sounding) {
            for (const k of node.children) k.classList.toggle("on", sounding.has(Number(k.dataset.midi)));
        }
        let held = null;
        const velAt = (k, e) => { const r = k.getBoundingClientRect(); return clamp(0.3 + 0.7 * (e.clientY - r.top) / r.height, 0.2, 1); };
        node.addEventListener("pointerdown", e => {
            const k = e.target.closest(".pk");
            if (!k || e.button !== 0) return;
            e.preventDefault();
            node.setPointerCapture(e.pointerId);
            held = Number(k.dataset.midi);
            spec.onDown("mouse", held, velAt(k, e));
        });
        node.addEventListener("pointermove", e => {
            if (held === null) return;
            const hit = document.elementFromPoint(e.clientX, e.clientY);
            const k = hit && hit.closest && hit.closest(".pk");
            if (!k || Number(k.dataset.midi) === held || !node.contains(k)) return;
            held = Number(k.dataset.midi);
            spec.onDown("mouse", held, velAt(k, e));
        });
        const up = () => { if (held === null) return; held = null; spec.onUp("mouse"); };
        node.addEventListener("pointerup", up);
        node.addEventListener("pointercancel", up);
        draw();
        return {node, draw, light};
    }

    // ------------------------------------------------------------ roll --

    /* spec: {track(), clip(), onEdit(label, fn), onPreview(midi), position() (the playhead, in
       beats), seek: {start(), to(beat), end()}, room(clip) (beats from its start the roll shows:
       past its end - the loop's bars - shaded, and a note written there grows the clip)} - a
       canvas piano roll for the clip, rows of the scale shaded. Alt + scroll or - Fit + zoom;
       the wheel goes along when the rows all fit (Shift: always). */
    function roll(spec) {
        const keysCol = el("canvas.roll-keys");
        const grid = el("canvas.roll-grid", {tabindex: "0", "aria-label": "Piano roll"});
        const vel = el("canvas.roll-vel", {"aria-label": "Velocity"});
        const ruler = el("canvas.roll-ruler", {title: Buddy.t("Click or drag to move the playhead")});
        const scroller = el("div.roll-scroll", {}, [grid]);
        const zoomBy = by => {
            const atX = scroller.clientWidth / 2, beat = (scroller.scrollLeft + atX) / pxPerBeat;
            pxPerBeat = Math.min(400, Math.max(least(), pxPerBeat * by));
            draw();
            scroller.scrollLeft = beat * pxPerBeat - atX;
        };
        const zoomBar = el("div.zoom.roll-zoom", {}, [
            el("button.btn.ghost.icon", {type: "button", title: "Zoom out (Alt + scroll)", onclick: () => zoomBy(1 / 1.4)}, [Buddy.icon("minus")]),
            el("button.btn.ghost", {type: "button", text: "Fit", title: "The whole clip in view", onclick: () => { fit(); draw(); scroller.scrollLeft = 0; }}),
            el("button.btn.ghost.icon", {type: "button", title: "Zoom in (Alt + scroll)", onclick: () => zoomBy(1.4)}, [Buddy.icon("plus")])]);
        const node = el("div.roll", {}, [el("div.roll-corner"), ruler, keysCol, scroller,
            el("div.roll-vel-label", {text: "Velocity"}), vel, zoomBar]);
        const ROW = 14;
        let lo = 36, hi = 96;              // the rows shown
        let selected = new Set();          // indexes into the clip's notes
        let drag = null;
        let pxPerBeat = 80;

        const clip = () => spec.clip();
        /* Beats from the clip's start the roll reaches: its length, or more (spec.room). */
        const span = (c = clip()) => (c ? Math.max(c.len, spec.room ? spec.room(c) : c.len) : 16);
        const least = () => Math.max(8, (scroller.clientWidth - 2) / Math.max(4, span()));
        /* A note past the clip's end (in its room): the clip grows to the bar it ends in. */
        function grow(c) {
            const end = Math.max(0, ...c.notes.map(n => n[1] + n[2]));
            if (end > c.len + 1e-6) c.len = Math.min(span(c), Math.ceil(end / 4 - 1e-6) * 4);
            for (const n of c.notes) n[2] = +Math.min(n[2], c.len - n[1]).toFixed(4);
            c.notes = c.notes.filter(n => n[1] < c.len && n[2] > 0);
        }
        const isDrums = () => { const t = spec.track(); return t && t.kind === "drums"; };
        function rows() {
            if (isDrums()) return Drums.ROWS.map(r => r.m).slice().reverse();
            const out = [];
            for (let m = hi; m >= lo; m--) out.push(m);
            return out;
        }
        const snap = () => (Daw.P.snap ? 1 / Daw.P.snap : 0);
        const q = b => { const g = snap(); return g ? Math.round(b / g) * g : b; };
        const qDown = b => { const g = snap(); return g ? Math.floor(b / g + 1e-6) * g : b; };

        function fit() {
            const c = clip();
            const w = scroller.clientWidth || 600;
            pxPerBeat = c ? Math.max(24, (w - 2) / Math.max(4, span(c))) : 80;
            if (!isDrums() && c && c.notes.length) {
                const ms = c.notes.map(n => n[0]);
                const mid = Math.round((Math.min(...ms) + Math.max(...ms)) / 2);
                lo = Math.max(0, Math.min(Math.min(...ms) - 4, mid - 24));
                hi = Math.min(127, Math.max(Math.max(...ms) + 4, lo + 36));
            } else if (!isDrums()) {
                const base = 12 * (Daw.P.octave + 1);
                lo = base - 12;
                hi = base + 24;
            }
        }

        function size(canvas, w, h) {
            const dpr = window.devicePixelRatio || 1;
            canvas.style.width = `${w}px`;
            canvas.style.height = `${h}px`;
            if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
                canvas.width = Math.round(w * dpr);
                canvas.height = Math.round(h * dpr);
            }
            const g = canvas.getContext("2d");
            g.setTransform(dpr, 0, 0, dpr, 0, 0);
            return g;
        }

        const css = name => getComputedStyle(node).getPropertyValue(name).trim();

        function layout() {
            const c = clip(), R = rows();
            const w = Math.max(scroller.clientWidth - 2, span(c) * pxPerBeat);
            const rowH = isDrums() ? Math.max(ROW, Math.floor((scroller.clientHeight - 2) / R.length)) : ROW;
            const h = Math.max(scroller.clientHeight - 2, R.length * rowH);
            return {c, R, w, h, rowH, y: m => R.indexOf(m) * rowH, x: b => b * pxPerBeat};
        }

        function draw(pos = spec.position ? spec.position() : null) {
            if (!node.isConnected) return;
            const L = layout(), {c, R, w, h, rowH} = L;
            const g = size(grid, w, h);
            const live = css("--daw-live") || "#f2a33a", line = css("--daw-line") || "#34353c", t = spec.track();
            const color = t ? Daw.COLORS[t.color % Daw.COLORS.length] : "#4C9BE8";
            g.clearRect(0, 0, w, h);
            R.forEach((m, i) => {
                if (isDrums()) { if (i % 2) { g.fillStyle = "rgba(127,127,127,.06)"; g.fillRect(0, i * rowH, w, rowH); } return; }
                if (Daw.inScale(m)) {
                    g.fillStyle = Daw.isRoot(m) ? "rgba(242,163,58,.09)" : "rgba(127,127,127,.07)";
                    g.fillRect(0, i * rowH, w, rowH);
                }
                if (m % 12 === 0) { g.fillStyle = line; g.fillRect(0, (i + 1) * rowH - 1, w, 1); }
            });
            const len = span(c), gstep = snap() || 0.25;
            // Past the clip's end: shaded - write there and the clip grows.
            if (c && len > c.len) {
                g.fillStyle = "rgba(0,0,0,.22)";
                g.fillRect(L.x(c.len), 0, w - L.x(c.len), h);
            }
            for (let b = 0; b <= len + 1e-6; b += gstep) {
                const bar = Math.abs(b / 4 - Math.round(b / 4)) < 1e-6, beat = Math.abs(b - Math.round(b)) < 1e-6;
                g.fillStyle = line;
                g.globalAlpha = bar ? 1 : beat ? 0.55 : 0.22;
                g.fillRect(Math.round(L.x(b)), 0, 1, h);
            }
            g.globalAlpha = 1;
            if (c) {
                c.notes.forEach((n, i) => {
                    const [m, s, d, v] = n;
                    if (!R.includes(m)) return;
                    const x = L.x(s), y = L.y(m), nw = Math.max(4, L.x(d) - 1);
                    g.fillStyle = selected.has(i) ? live : color;
                    g.globalAlpha = 0.55 + 0.45 * v;
                    g.fillRect(x + 0.5, y + 1, nw, rowH - 2);
                    g.globalAlpha = 1;
                    g.fillStyle = "rgba(0,0,0,.35)";
                    g.fillRect(x + nw - 2, y + 4, 2, Math.max(2, rowH - 8));
                });
            }
            if (c && len > c.len) { g.fillStyle = live; g.globalAlpha = 0.6; g.fillRect(Math.round(L.x(c.len)) - 1, 0, 2, h); g.globalAlpha = 1; }
            if (pos !== null && c && pos >= c.start && pos < c.start + len) {
                g.fillStyle = live;
                g.fillRect(Math.round(L.x(pos - c.start)), 0, 2, h);
            }
            // The keys down the side.
            const kh = scroller.clientHeight;
            const k = size(keysCol, 54, kh);
            k.clearRect(0, 0, 54, kh);
            k.font = `600 9px ${getComputedStyle(document.body).fontFamily}`;
            k.textBaseline = "middle";
            const top = scroller.scrollTop;
            R.forEach((m, i) => {
                const y = i * rowH - top;
                if (isDrums()) {
                    k.fillStyle = css("--daw-raised") || "#26272d";
                    k.fillRect(0, y, 54, rowH - 1);
                    k.fillStyle = css("--text") || "#ddd";
                    k.fillText(Buddy.t(Drums.ROWS[Drums.rowOf(m)].name), 5, y + rowH / 2, 46);
                    return;
                }
                const black = isBlack(m);
                k.fillStyle = black ? "#1a1b1f" : "#d9dade";
                k.fillRect(0, y, 54, rowH - 1);
                if (Daw.inScale(m)) { k.fillStyle = Daw.isRoot(m) ? "#f2a33a" : "rgba(242,163,58,.45)"; k.fillRect(50, y, 4, rowH - 1); }
                if (m % 12 === 0 || m === hi) { k.fillStyle = black ? "#888" : "#555"; k.fillText(Daw.noteName(m), 4, y + rowH / 2); }
            });
            drawRuler(L, pos);
            // Velocity.
            const vw = scroller.clientWidth, vh = 54;
            const v = size(vel, vw, vh);
            v.clearRect(0, 0, vw, vh);
            if (c) {
                const sx = scroller.scrollLeft;
                c.notes.forEach((n, i) => {
                    if (!R.includes(n[0])) return;
                    const x = L.x(n[1]) - sx;
                    v.fillStyle = selected.has(i) ? live : color;
                    v.fillRect(x + 1, vh - n[3] * (vh - 6), 5, n[3] * (vh - 6));
                });
            }
        }

        /* The ruler over the notes: the song's bar numbers where the clip sits, its beats,
           and the playhead's handle. It scrolls along with the notes. */
        function drawRuler(L, pos) {
            const rw = scroller.clientWidth, rh = 20, c = L.c;
            const r = size(ruler, rw, rh);
            r.clearRect(0, 0, rw, rh);
            if (!c) return;
            const sx = scroller.scrollLeft, line = css("--daw-line") || "#34353c";
            r.font = `600 10px ${getComputedStyle(document.body).fontFamily}`;
            r.textBaseline = "middle";
            const every = pxPerBeat * 4 < 30 ? 2 : 1;
            for (let b = 0; b <= span(c) + 1e-6; b++) {
                const x = Math.round(L.x(b) - sx);
                if (x < -40 || x > rw + 2) continue;
                const bar = (c.start + b) % 4 === 0;
                r.fillStyle = line;
                r.fillRect(x, bar ? 0 : rh - 6, 1, bar ? rh : 6);
                if (bar && Math.floor((c.start + b) / 4) % every === 0) {
                    r.fillStyle = css("--text-dim") || "#888";
                    r.fillText(String(Daw.barOf(c.start + b)), x + 4, rh / 2);
                }
            }
            if (pos !== null && pos >= c.start && pos <= c.start + span(c)) {
                const x = L.x(pos - c.start) - sx;
                r.fillStyle = css("--daw-live") || "#f2a33a";
                r.beginPath();
                r.moveTo(x - 6, 0);
                r.lineTo(x + 6, 0);
                r.lineTo(x, 9);
                r.closePath();
                r.fill();
                r.fillRect(Math.round(x) - 1, 0, 2, rh);
            }
        }

        // The ruler: click or drag to move the playhead (inside the clip).
        let seeking = false;
        const seekTo = e => {
            const c = clip();
            if (!c || !spec.seek) return;
            const b = (e.clientX - ruler.getBoundingClientRect().left + scroller.scrollLeft) / pxPerBeat;
            const g = snap() || 0.25;
            spec.seek.to(c.start + Math.min(span(c), Math.max(0, Math.round(b / g) * g)));
        };
        ruler.addEventListener("pointerdown", e => {
            if (e.button !== 0 || !clip() || !spec.seek) return;
            e.preventDefault();
            seeking = true;
            capture(ruler, e);
            spec.seek.start();
            seekTo(e);
        });
        ruler.addEventListener("pointermove", e => { if (seeking) seekTo(e); });
        const seekEnd = () => { if (!seeking) return; seeking = false; spec.seek.end(); };
        ruler.addEventListener("pointerup", seekEnd);
        ruler.addEventListener("pointercancel", seekEnd);

        // Alt + scroll zooms along the clip around the mouse (as on Resolve's timeline; Ctrl
        // too, a trackpad's pinch). Qt on Windows turns an Alt + wheel sideways: whichever
        // moved is the step. The rest scrolls as it comes - up and down, Shift along.
        node.addEventListener("wheel", e => {
            const step = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
            if (!step) return;
            const pxStep = e.deltaMode === 1 ? step * 40 : e.deltaMode === 2 ? step * 400 : step;
            if (!(e.altKey || e.ctrlKey || e.metaKey)) {
                // Along when the rows all fit (or with Shift); up and down as it comes otherwise.
                const wide = scroller.scrollWidth > scroller.clientWidth + 1, tall = scroller.scrollHeight > scroller.clientHeight + 1;
                if (wide && Math.abs(e.deltaY) >= Math.abs(e.deltaX) && (e.shiftKey || !tall)) {
                    e.preventDefault();
                    scroller.scrollLeft += pxStep;
                }
                return;
            }
            e.preventDefault();
            const atX = Math.max(0, e.clientX - scroller.getBoundingClientRect().left);
            const beat = (scroller.scrollLeft + atX) / pxPerBeat;
            pxPerBeat = Math.min(400, Math.max(least(), pxPerBeat * Math.pow(1.0015, -pxStep)));
            draw();
            scroller.scrollLeft = beat * pxPerBeat - atX;
        }, {passive: false});

        function noteAt(e) {
            const c = clip();
            if (!c) return null;
            const r = grid.getBoundingClientRect(), L = layout();
            const x = e.clientX - r.left, y = e.clientY - r.top;
            for (let i = c.notes.length - 1; i >= 0; i--) {
                const [m, s, d] = c.notes[i];
                const ny = L.y(m);
                if (ny < 0) continue;
                if (x >= L.x(s) && x <= L.x(s) + Math.max(4, L.x(d)) && y >= ny && y < ny + L.rowH) {
                    return {i, edge: x > L.x(s) + Math.max(4, L.x(d)) - 6};
                }
            }
            return null;
        }
        function cellAt(e) {
            const r = grid.getBoundingClientRect(), L = layout();
            const row = Math.floor((e.clientY - r.top) / L.rowH);
            return {m: L.R[clamp(row, 0, L.R.length - 1)], b: (e.clientX - r.left) / pxPerBeat};
        }

        grid.addEventListener("pointerdown", e => {
            const c = clip();
            if (!c || e.button !== 0) return;
            grid.focus();
            e.preventDefault();
            const hit = noteAt(e), cell = cellAt(e);
            if (!hit) {
                if (e.shiftKey) { drag = {kind: "box", x: e.clientX, y: e.clientY}; grid.setPointerCapture(e.pointerId); return; }
                // A new note, the snap's length (a step on drums), where it was clicked.
                const s = qDown(cell.b), d = isDrums() ? (snap() || 0.25) : (lastLen || snap() || 0.25);
                if (s >= span(c)) return;
                spec.onEdit("Add a note", () => {
                    c.notes.push([cell.m, +s.toFixed(4), +Math.min(d, span(c) - s).toFixed(4), Daw.P.velocity]);
                    grow(c);
                });
                selected = new Set([c.notes.length - 1]);
                spec.onPreview(cell.m);
                drag = {kind: "move", i: c.notes.length - 1, from: cell, orig: [c.notes[c.notes.length - 1].slice()], idx: [c.notes.length - 1], fresh: true};
                grid.setPointerCapture(e.pointerId);
                draw();
                return;
            }
            if (e.ctrlKey || e.metaKey) {
                selected.has(hit.i) ? selected.delete(hit.i) : selected.add(hit.i);
                draw();
                return;
            }
            if (!selected.has(hit.i)) selected = new Set([hit.i]);
            const idx = [...selected];
            Daw.checkpoint(hit.edge ? "Lengthen notes" : "Move notes");
            drag = {kind: hit.edge ? "size" : "move", i: hit.i, from: cell, orig: idx.map(i => c.notes[i].slice()), idx};
            if (!hit.edge) spec.onPreview(c.notes[hit.i][0]);
            grid.setPointerCapture(e.pointerId);
            draw();
        });
        let lastLen = 0;
        grid.addEventListener("pointermove", e => {
            const c = clip();
            if (!drag || !c) {
                const hit = noteAt(e);
                grid.style.cursor = hit && hit.edge ? "ew-resize" : hit ? "grab" : "crosshair";
                return;
            }
            if (drag.kind === "box") return;
            const cell = cellAt(e), L = layout();
            if (drag.kind === "size") {
                const db = cell.b - drag.from.b;
                drag.idx.forEach((i, k) => {
                    const [, s, d] = drag.orig[k];
                    const nd = Math.max(snap() || 1 / 32, q(d + db));
                    c.notes[i][2] = +Math.min(nd, span(c) - s).toFixed(4);
                });
                lastLen = c.notes[drag.i][2];
            } else {
                const db = q(cell.b - drag.from.b);
                const dr = L.R.indexOf(cell.m) - L.R.indexOf(drag.from.m);
                drag.idx.forEach((i, k) => {
                    const [m, s, d] = drag.orig[k];
                    const row = clamp(L.R.indexOf(m) + dr, 0, L.R.length - 1);
                    const nm = L.R[row];
                    if (nm !== c.notes[i][0] && i === drag.i) spec.onPreview(nm);
                    c.notes[i][0] = nm;
                    c.notes[i][1] = +clamp(s + db, 0, span(c) - Math.min(d, span(c))).toFixed(4);
                });
            }
            Daw.changed("notes");
        });
        grid.addEventListener("pointerup", e => {
            if (drag && drag.kind === "box") {
                const c = clip(), r = grid.getBoundingClientRect(), L = layout();
                const x0 = Math.min(drag.x, e.clientX) - r.left, x1 = Math.max(drag.x, e.clientX) - r.left;
                const y0 = Math.min(drag.y, e.clientY) - r.top, y1 = Math.max(drag.y, e.clientY) - r.top;
                selected = new Set();
                if (c) c.notes.forEach(([m, s, d], i) => {
                    const ny = L.y(m);
                    if (ny >= 0 && L.x(s) < x1 && L.x(s + d) > x0 && ny < y1 && ny + L.rowH > y0) selected.add(i);
                });
                draw();
            }
            if (drag && drag.kind !== "box") { const c = clip(); if (c) grow(c); sortNotes(); }
            drag = null;
        });
        grid.addEventListener("dblclick", e => {
            const c = clip(), hit = noteAt(e);
            if (!c || !hit) return;
            spec.onEdit("Delete a note", () => c.notes.splice(hit.i, 1));
            selected = new Set();
            draw();
        });
        grid.addEventListener("contextmenu", e => {
            const c = clip(), hit = noteAt(e);
            e.preventDefault();
            if (!c || !hit) return;
            spec.onEdit("Delete a note", () => c.notes.splice(hit.i, 1));
            selected = new Set();
            draw();
        });

        /* The notes in time order, the selection kept on the same notes. */
        function sortNotes() {
            const c = clip();
            if (!c) return;
            const keep = c.notes.map((n, i) => ({n, sel: selected.has(i)}));
            keep.sort((a, b) => a.n[1] - b.n[1] || a.n[0] - b.n[0]);
            c.notes = keep.map(k => k.n);
            selected = new Set(keep.map((k, i) => (k.sel ? i : -1)).filter(i => i >= 0));
            Daw.changed("notes");
        }

        // Velocity: drag a bar (the nearest note's) up or down.
        let velDrag = null;
        vel.addEventListener("pointerdown", e => {
            const c = clip();
            if (!c || e.button !== 0) return;
            const r = vel.getBoundingClientRect(), x = e.clientX - r.left + scroller.scrollLeft;
            let best = -1, dist = 12;
            c.notes.forEach((n, i) => { const dx = Math.abs(n[1] * pxPerBeat + 3 - x); if (dx < dist) { dist = dx; best = i; } });
            if (best < 0) return;
            Daw.checkpoint("Change velocity");
            velDrag = {targets: selected.has(best) ? [...selected] : [best], r};
            vel.setPointerCapture(e.pointerId);
            setVel(e);
        });
        function setVel(e) {
            const c = clip(), v = clamp(1 - (e.clientY - velDrag.r.top - 3) / (velDrag.r.height - 6), 0.05, 1);
            for (const i of velDrag.targets) if (c.notes[i]) c.notes[i][3] = +v.toFixed(3);
            Daw.changed("notes");
        }
        vel.addEventListener("pointermove", e => { if (velDrag) setVel(e); });
        vel.addEventListener("pointerup", () => { velDrag = null; });

        grid.addEventListener("keydown", e => {
            const c = clip();
            if (!c) return;
            const ctrl = e.ctrlKey || e.metaKey;
            if (ctrl && e.code === "KeyA") {
                e.preventDefault();
                e.stopPropagation();
                selected = new Set(c.notes.map((_n, i) => i));
                return draw();
            }
            if (!selected.size) return;
            if (e.key === "Delete" || e.key === "Backspace") {
                e.preventDefault();
                e.stopPropagation();
                spec.onEdit("Delete notes", () => { c.notes = c.notes.filter((_n, i) => !selected.has(i)); });
                selected = new Set();
                return draw();
            }
            if (ctrl && e.code === "KeyD") {
                e.preventDefault();
                e.stopPropagation();
                const sel = [...selected].map(i => c.notes[i]);
                const lo2 = Math.min(...sel.map(n => n[1])), hi2 = Math.max(...sel.map(n => n[1] + n[2]));
                const by = Math.ceil((hi2 - lo2) / (snap() || 0.25) - 1e-6) * (snap() || 0.25);
                spec.onEdit("Duplicate notes", () => {
                    const start = c.notes.length;
                    for (const n of sel) if (n[1] + by < c.len) c.notes.push([n[0], +(n[1] + by).toFixed(4), Math.min(n[2], c.len - n[1] - by), n[3]]);
                    selected = new Set(c.notes.slice(start).map((_n, k) => start + k));
                });
                return draw();
            }
            const move = {ArrowUp: [0, 1], ArrowDown: [0, -1], ArrowLeft: [-1, 0], ArrowRight: [1, 0]}[e.key];
            if (!move) return;
            e.preventDefault();
            e.stopPropagation();
            const g = snap() || 0.25;
            spec.onEdit(move[1] ? "Transpose notes" : "Move notes", () => {
                for (const i of selected) {
                    const n = c.notes[i];
                    if (move[1]) {
                        if (isDrums()) {
                            const R = rows(), row = clamp(R.indexOf(n[0]) - move[1], 0, R.length - 1);
                            n[0] = R[row];
                        } else n[0] = clamp(n[0] + move[1] * (e.shiftKey ? 12 : 1), 0, 127);
                    } else n[1] = +clamp(n[1] + move[0] * g * (e.shiftKey ? 4 : 1), 0, c.len - g).toFixed(4);
                }
            });
            draw();
        });
        scroller.addEventListener("scroll", () => draw());

        /* Playing: the playhead kept in sight, scrolled to a quarter in when it goes past. */
        function follow(pos) {
            const c = clip();
            if (!c || pos === null || pos < c.start || pos >= c.start + span(c)) return;
            const x = (pos - c.start) * pxPerBeat, view = scroller;
            if (x > view.scrollLeft + view.clientWidth - 20 || x < view.scrollLeft) view.scrollLeft = Math.max(0, x - view.clientWidth / 4);
        }

        return {node, draw, follow, fit: () => { fit(); selected = new Set(); draw(); }, clearSelection: () => { selected = new Set(); }};
    }

    /* Keeps a drag's pointer while it's held - when there's one to keep (a pointer gone already, or
       made up by a test, can't be held; the drag goes on without it). */
    function capture(node, e) {
        try { node.setPointerCapture(e.pointerId); } catch (_err) { /* no such pointer */ }
    }

    return {knob, fader, meter, seg, keyboard, roll, shown, capture, CODES, isBlack};
})();
