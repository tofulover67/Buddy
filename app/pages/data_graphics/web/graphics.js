"use strict";

// What Data Graphics' pages share (counters/, graphs/; ../graphic_page.py says what
// each event carries): the controls - any element with data-key is one field of the
// graphic, sent to Python as it changes and filled in from the state - the fonts, the
// preview's player, the timing dock under it (a lane per part: its bar dragged to
// retime it, its curve reshaped), pop-up panels for a page's detailed options, and
// the header and foot. Python owns the graphic; a page's own script only draws a
// frame of it: Graphics.start(draw), draw(ctx, frame, state).

const Graphics = (() => {
    const $ = id => document.getElementById(id);
    const el = Buddy.el;
    const TYPING_MS = 250;

    let state = null;
    let draw = null;
    let fonts = {};
    let frame = 0;            // the preview frame showing
    let playing = false;
    let startedAt = 0;        // performance.now() of frame 0, while playing
    let seen = false;         // a state has been drawn: the first one opens on the finished graphic

    // ------------------------------------------------------- controls --

    const timers = new Map();

    function later(key, send, wait) {
        clearTimeout(timers.get(key));
        if (!wait) return send();
        timers.set(key, setTimeout(send, wait));
    }

    function setField(key, value, wait) {
        later(key, () => Buddy.send("set", {key, value}), wait);
    }

    function bind() {
        document.querySelectorAll("[data-key]").forEach(node => {
            const key = node.dataset.key;
            if (node.classList.contains("segmented")) {
                node.addEventListener("click", e => {
                    const button = e.target.closest("button[data-value]");
                    if (button) setField(key, button.dataset.value);
                });
            } else if (node.type === "checkbox") {
                node.addEventListener("change", () => setField(key, node.checked));
            } else if (node.tagName === "SELECT") {
                node.addEventListener("change", () => setField(key, node.value));
            } else if (node.type === "range" || node.type === "color") {
                node.addEventListener("input", () => setField(key, node.value, 30));
            } else {
                node.addEventListener("input", () => setField(key, node.value, TYPING_MS));
                node.addEventListener("change", () => setField(key, node.value));
            }
        });
    }

    function plain(value) {
        return typeof value === "number" ? String(+value.toFixed(6)) : String(value == null ? "" : value);
    }

    function fill(spec) {
        document.querySelectorAll("[data-key]").forEach(node => {
            const value = spec[node.dataset.key];
            if (node.classList.contains("segmented")) {
                node.querySelectorAll("button[data-value]").forEach(b =>
                    b.setAttribute("aria-pressed", String(b.dataset.value === String(value))));
            } else if (node.type === "checkbox") {
                node.checked = !!value;
            } else if (node !== document.activeElement || node.tagName === "SELECT") {
                // What's being typed is left alone: Python has it, tidied, when the field is left.
                if (node.value !== plain(value)) node.value = plain(value);
            }
        });
        document.querySelectorAll("[data-needs]").forEach(node => { node.disabled = !spec[node.dataset.needs]; });
        $("easing").value = spec.easing;
        fillStyles(spec);
    }

    function fillStyles(spec) {
        const font = $("font"), style = $("style");
        if (!font || !style) return;
        if (font.value !== spec.font) font.value = spec.font;
        const styles = fonts[spec.font] || [spec.style];
        if (style.dataset.of !== spec.font || style.options.length !== styles.length) {
            style.replaceChildren(...styles.map(s => el("option", {value: s, text: s})));
            style.dataset.of = spec.font;
        }
        style.value = spec.style;
    }

    Buddy.on("fonts", data => {
        fonts = data.fonts || {};
        $("font").replaceChildren(...Object.keys(fonts).map(f => el("option", {value: f, text: f})));
        if (state) fillStyles(state.spec);
    });

    // ------------------------------------------------------ the dock --
    // A lane per part of the graphic (state.lanes: {id, label, sub, plain, start,
    // length, easing, curve, kind, kinds, own}). A lane's bar is when the part moves:
    // drag it to move its start, its right edge to stretch it. The lane clicked is the
    // one the fields and the curve under the lanes are about. A part that is simply
    // there (kind "none") has a faint bar the whole way along until it's given a way
    // to arrive. Everything goes to Python as "part" {id, key, value}.

    const EDGE = 9;           // px of a bar's right end that stretch it
    let picked = "";          // the lane the inspector is about
    let hold = null;          // a bar held: {id, mode: "move" | "stretch", x, start, length, bar}

    function seconds() {
        return state.preview.total / state.preview.fps;
    }

    function lane(id) {
        return state.lanes.find(l => l.id === id) || null;
    }

    function sendPart(id, key, value, wait) {
        later("part:" + key, () => Buddy.send("part", {id, key, value}), wait);
    }

    function clock(value) {
        return String(+Number(value).toFixed(2));
    }

    function place(bar, l) {
        const total = seconds();
        const still = l.kind === "none";
        bar.style.left = (still ? 0 : Math.min(100, l.start / total * 100)) + "%";
        bar.style.width = (still ? 100 : Math.max(0.8, Math.min(100 - l.start / total * 100, l.length / total * 100))) + "%";
        bar.classList.toggle("still", still);
        bar.classList.toggle("own", !!l.own && !still);
    }

    function laneNode(l) {
        const bar = el("div.dg-lane-bar", {dataset: {id: l.id}});
        place(bar, l);
        return el("div.dg-lane", {dataset: {id: l.id}, "aria-selected": String(l.id === picked)}, [
            el("span.dg-lane-name", {title: l.plain ? l.label : undefined}, [
                el("span", {text: l.label, translate: l.plain ? "no" : undefined}),
                l.sub ? el("span.dg-lane-sub", {text: l.sub === "name" ? "Name" : "Number"}) : null]),
            el("div.dg-lane-track", {}, bar)]);
    }

    function drawLanes() {
        if (hold) return;                     // the bar in the hand isn't redrawn under it
        if (!lane(picked)) picked = state.lanes.length ? state.lanes[0].id : "";
        const box = $("lanes"), scroll = box.scrollTop;
        box.replaceChildren(...state.lanes.map(laneNode));
        box.scrollTop = scroll;
        drawInspector();
    }

    function drawInspector() {
        const l = lane(picked);
        $("inspect").hidden = !l;
        if (!l) return;
        const still = l.kind === "none";
        $("part-kind-box").hidden = !l.kinds || l.kinds.length < 2;
        if (l.kinds) {
            [...$("part-kind").options].forEach(o => { o.hidden = o.disabled = !l.kinds.includes(o.value); });
            $("part-kind").value = l.kind;
        }
        for (const [id, value] of [["part-start", l.start], ["part-length", l.length]]) {
            const field = $(id);
            field.disabled = still;
            if (field !== document.activeElement && field.value !== clock(value)) field.value = clock(value);
        }
        $("part-easing").disabled = still;
        $("part-easing").value = l.easing;
        $("part-reset").hidden = !(l.own && l.id !== "count" && l.id !== "arrive");
        $("curve").classList.toggle("off", still);
        if (dragging < 0) curve = l.curve || curve;       // a held handle is where the hand is
        drawCurve();
    }

    function pick(id) {
        picked = id;
        $("lanes").querySelectorAll(".dg-lane").forEach(n => n.setAttribute("aria-selected", String(n.dataset.id === id)));
        drawInspector();
    }

    function bindDock() {
        const lanes = $("lanes");
        lanes.addEventListener("pointerdown", e => {
            const row = e.target.closest(".dg-lane");
            if (!row) return;
            pick(row.dataset.id);
            const bar = e.target.closest(".dg-lane-bar"), l = lane(row.dataset.id);
            if (!bar || !l || l.kind === "none") return;
            const box = bar.getBoundingClientRect();
            hold = {id: l.id, mode: box.right - e.clientX <= EDGE ? "stretch" : "move", x: e.clientX,
                    start: l.start, length: l.length, bar, track: bar.parentElement.getBoundingClientRect().width};
            bar.setPointerCapture(e.pointerId);
            bar.classList.add("held");
        });
        lanes.addEventListener("pointermove", e => {
            if (!hold) return;
            const total = seconds(), step = 1 / state.preview.fps;
            const moved = Math.round((e.clientX - hold.x) / hold.track * total / step) * step;
            const l = lane(hold.id);
            if (hold.mode === "move") {
                l.start = Math.min(Math.max(0, hold.start + moved), Math.max(0, total - state.min_length));
                sendPart(hold.id, "start", +l.start.toFixed(4), 40);
            } else {
                l.length = Math.max(state.min_length, hold.length + moved);
                sendPart(hold.id, "length", +l.length.toFixed(4), 40);
            }
            place(hold.bar, l);
            drawInspector();
        });
        const drop = () => {
            if (!hold) return;
            const l = lane(hold.id), key = hold.mode === "move" ? "start" : "length";
            hold.bar.classList.remove("held");
            if (l[key] !== hold[key]) sendPart(hold.id, key, +l[key].toFixed(4));
            hold = null;
        };
        lanes.addEventListener("pointerup", drop);
        lanes.addEventListener("pointercancel", drop);
        // A bar's right end says it stretches.
        lanes.addEventListener("mousemove", e => {
            const bar = e.target.closest(".dg-lane-bar");
            if (bar && !hold) bar.classList.toggle("edge", bar.getBoundingClientRect().right - e.clientX <= EDGE);
        });

        $("part-kind").addEventListener("change", e => sendPart(picked, "kind", e.target.value));
        $("part-easing").addEventListener("change", e => sendPart(picked, "easing", e.target.value));
        for (const [id, key] of [["part-start", "start"], ["part-length", "length"]]) {
            $(id).addEventListener("input", e => sendPart(picked, key, e.target.value, TYPING_MS));
            $(id).addEventListener("change", e => sendPart(picked, key, e.target.value));
        }
        $("part-reset").addEventListener("click", () => sendPart(picked, "reset", null));
        $("dock-fold").addEventListener("click", () => Buddy.send("option", {key: "dock", value: $("dock-body").hidden}));
    }

    // --------------------------------------------------- timing curve --
    // The picked lane's curve: a cubic Bezier from the bottom left (its start) to the
    // top right (its end), its two handles dragged - [x1, y1, x2, y2], x within 0..1, y
    // within the range Python allows (past the lines is an overshoot).

    const PAD = 14, GRAB = 18;
    let curve = [1 / 3, 1, 2 / 3, 1];
    let range = [-0.5, 1.5];
    let dragging = -1;        // 0 or 1: the handle held; -1: none

    function curveBox() {
        const canvas = $("curve");
        const w = canvas.clientWidth || 320, h = canvas.clientHeight || 150;
        const toX = x => PAD + x * (w - 2 * PAD);
        const toY = y => h - PAD - (y - range[0]) / (range[1] - range[0]) * (h - 2 * PAD);
        return {canvas, w, h, toX, toY};
    }

    function drawCurve() {
        const {canvas, w, h, toX, toY} = curveBox();
        const ratio = window.devicePixelRatio || 1;
        if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
            canvas.width = Math.round(w * ratio);
            canvas.height = Math.round(h * ratio);
        }
        const css = getComputedStyle(document.documentElement);
        const dim = css.getPropertyValue("--text-dim").trim() || "#888";
        const accent = css.getPropertyValue("--primary").trim() || "#e5484d";
        const strong = css.getPropertyValue("--text-strong").trim() || "#fff";
        const ctx = canvas.getContext("2d");
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, w, h);
        ctx.lineWidth = 1;
        ctx.strokeStyle = dim;
        ctx.globalAlpha = 0.45;
        ctx.setLineDash([4, 4]);
        for (const y of [0, 1]) {             // where it starts and where it ends
            ctx.beginPath();
            ctx.moveTo(toX(0), toY(y));
            ctx.lineTo(toX(1), toY(y));
            ctx.stroke();
        }
        ctx.setLineDash([]);
        ctx.globalAlpha = 0.8;
        ctx.strokeStyle = accent;
        for (const [hx, hy, ax, ay] of [[curve[0], curve[1], 0, 0], [curve[2], curve[3], 1, 1]]) {
            ctx.beginPath();
            ctx.moveTo(toX(ax), toY(ay));
            ctx.lineTo(toX(hx), toY(hy));
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
        ctx.strokeStyle = strong;
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(toX(0), toY(0));
        ctx.bezierCurveTo(toX(curve[0]), toY(curve[1]), toX(curve[2]), toY(curve[3]), toX(1), toY(1));
        ctx.stroke();
        ctx.fillStyle = accent;
        for (const i of [0, 1]) {
            ctx.beginPath();
            ctx.arc(toX(curve[2 * i]), toY(curve[2 * i + 1]), dragging === i ? 8 : 6.5, 0, 2 * Math.PI);
            ctx.fill();
        }
    }

    function bindCurve() {
        const canvas = $("curve");
        const at = e => {
            const box = canvas.getBoundingClientRect();
            return [e.clientX - box.left, e.clientY - box.top];
        };
        const move = e => {
            const {w, h} = curveBox();
            const [px, py] = at(e);
            const x = Math.min(1, Math.max(0, (px - PAD) / (w - 2 * PAD)));
            const y = range[0] + (h - PAD - py) / (h - 2 * PAD) * (range[1] - range[0]);
            curve = curve.slice();
            curve[2 * dragging] = +x.toFixed(4);
            curve[2 * dragging + 1] = +Math.min(range[1], Math.max(range[0], y)).toFixed(4);
            drawCurve();
        };
        canvas.addEventListener("pointerdown", e => {
            if (canvas.classList.contains("off")) return;
            const {toX, toY} = curveBox();
            const [px, py] = at(e);
            const far = i => Math.hypot(px - toX(curve[2 * i]), py - toY(curve[2 * i + 1]));
            const nearest = far(0) <= far(1) ? 0 : 1;
            if (far(nearest) > GRAB * 2.5) return;
            dragging = nearest;
            canvas.setPointerCapture(e.pointerId);
            canvas.classList.add("dragging");
            move(e);
            sendPart(picked, "curve", curve, 40);
        });
        canvas.addEventListener("pointermove", e => {
            if (dragging < 0) return;
            move(e);
            sendPart(picked, "curve", curve, 40);
        });
        const drop = () => {
            if (dragging < 0) return;
            dragging = -1;
            canvas.classList.remove("dragging");
            sendPart(picked, "curve", curve);
            drawCurve();
        };
        canvas.addEventListener("pointerup", drop);
        canvas.addEventListener("pointercancel", drop);
        new ResizeObserver(drawCurve).observe(canvas);
    }

    // ---------------------------------------------------- the pop-ups --
    // A page's detailed options live in small panels over the page, so the page itself
    // stays simple: each opened by its button, moved by its head, closed by its X or
    // Escape, one at a time. The preview stays in sight behind them. panel(pop id,
    // button id, {shown(), hidden()}) makes an element with class dg-pop one.

    const panels = [];

    function panel(popId, buttonId, hooks) {
        const pop = $(popId), button = $(buttonId), head = pop.querySelector(".dg-pop-head");
        const me = {
            isOpen: () => !pop.hidden,
            show(on) {
                if (pop.hidden === !on) return;
                if (on) panels.forEach(other => { if (other !== me) other.show(false); });
                pop.hidden = !on;
                button.setAttribute("aria-expanded", String(on));
                const hook = hooks && (on ? hooks.shown : hooks.hidden);
                if (hook) hook();
            },
        };
        panels.push(me);
        const close = pop.querySelector(".dg-pop-close");
        close.append(Buddy.icon("close"));
        close.addEventListener("click", () => me.show(false));
        button.addEventListener("click", () => me.show(pop.hidden));
        let grip = null;          // where in the panel the head was taken hold of
        head.addEventListener("pointerdown", e => {
            if (e.target.closest("button")) return;
            const box = pop.getBoundingClientRect();
            grip = [e.clientX - box.left, e.clientY - box.top];
            head.setPointerCapture(e.pointerId);
        });
        head.addEventListener("pointermove", e => {
            if (!grip) return;
            const box = pop.getBoundingClientRect();
            const left = Math.min(window.innerWidth - box.width - 4, Math.max(4, e.clientX - grip[0]));
            const top = Math.min(window.innerHeight - 40, Math.max(4, e.clientY - grip[1]));
            Object.assign(pop.style, {left: left + "px", top: top + "px", right: "auto", bottom: "auto"});
        });
        const drop = () => { grip = null; };
        head.addEventListener("pointerup", drop);
        head.addEventListener("pointercancel", drop);
        return me;
    }

    document.addEventListener("keydown", e => {
        if (e.key !== "Escape" || document.querySelector(".modal-backdrop") || document.getElementById("picker")) return;
        panels.forEach(p => p.show(false));
    });

    // --------------------------------------------------------- player --

    function paint() {
        const canvas = $("preview");
        if (!state || !draw) return;
        const [fw, fh] = state.preview.frame;
        const stage = canvas.parentElement;
        const width = Math.max(120, Math.min(stage.clientWidth, Math.floor(window.innerHeight * 0.42 * fw / fh)));
        const height = Math.round(width * fh / fw);
        const ratio = window.devicePixelRatio || 1;
        if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(height * ratio);
            canvas.style.width = width + "px";
            canvas.style.height = height + "px";
        }
        const ctx = canvas.getContext("2d");
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(canvas.width / fw, 0, 0, canvas.width / fw, 0, 0);      // the frame's own px from here
        draw(ctx, frame, state);
        const total = state.preview.total, fps = state.preview.fps;
        $("scrub").max = String(total - 1);
        $("scrub").value = String(frame);
        $("time").textContent = `${(frame / fps).toFixed(1)} / ${((total - 1) / fps).toFixed(1)}`;
        // Where the preview is, on the lanes.
        $("lanes").style.setProperty("--at", (total > 1 ? frame / (total - 1) * 100 : 0) + "%");
    }

    function drawPlay() {
        $("play").replaceChildren(Buddy.icon(playing ? "pause" : "play"));
        $("play").setAttribute("aria-label", playing ? "Pause" : "Play");
        $("play").title = playing ? "Pause" : "Play";
    }

    function tick(now) {
        if (!playing || !state) return;
        const last = state.preview.total - 1;
        // The first tick's time can be a hair before play()'s: never before frame 0.
        frame = Math.max(0, Math.min(last, Math.floor((now - startedAt) * state.preview.fps / 1000)));
        paint();
        if (frame >= last) {
            playing = false;
            drawPlay();
        } else {
            requestAnimationFrame(tick);
        }
    }

    function play(from) {
        if (!state) return;
        frame = from >= state.preview.total - 1 ? 0 : from;
        startedAt = performance.now() - frame * 1000 / state.preview.fps;
        playing = true;
        drawPlay();
        requestAnimationFrame(tick);
    }

    function pause() {
        playing = false;
        drawPlay();
    }

    // The value of a keyed text at a frame: keys are [[frame, text], ...], in order.
    function textAt(keys, f) {
        let text = "";
        for (const [at, value] of keys) {
            if (at > f) break;
            text = value;
        }
        return text;
    }

    // A part's arriving at a frame - {alpha, dy, scale, pivot}, or null when it has
    // arrived (or simply is there). size: its letter size; pivot: [x, y] it scales about.
    function arriving(s, id, f, size, pivot) {
        const frames = s.preview.arrivals[id];
        if (!frames || f < 0 || f >= frames.length) return null;
        const [alpha, dy, scale] = frames[f];
        return {alpha, dy: dy * size, scale, pivot};
    }

    // What's drawn after this is moved as a part arriving is (inside ctx.save/restore).
    function move(ctx, moved) {
        if (!moved) return;
        ctx.globalAlpha *= moved.alpha;
        ctx.translate(moved.pivot[0], moved.pivot[1] + moved.dy);
        ctx.scale(moved.scale, moved.scale);
        ctx.translate(-moved.pivot[0], -moved.pivot[1]);
    }

    // A line of text as the Text+ will draw it: size and place in the frame's px.
    // moved: arriving()'s, while it is still arriving.
    function text(ctx, look, value, x, baseline, size, align, moved) {
        const face = look.face;
        ctx.save();
        move(ctx, moved);
        ctx.font = `${face.italic ? "italic " : ""}${face.weight} ${size}px "${face.family.replace(/"/g, "")}", sans-serif`;
        ctx.textAlign = align || "center";
        ctx.textBaseline = "alphabetic";
        ctx.lineJoin = "round";
        if (look.shadow) {
            ctx.shadowColor = "rgba(0, 0, 0, 0.7)";
            ctx.shadowOffsetX = ctx.shadowOffsetY = look.shadow_dist * size * ctx.getTransform().a;
            ctx.shadowBlur = 0.05 * size * ctx.getTransform().a;
        }
        if (look.outline) {
            ctx.strokeStyle = look.outline_colour;
            ctx.lineWidth = 2 * look.outline_w * size;
            ctx.strokeText(value, x, baseline);
            ctx.shadowColor = "transparent";
        }
        ctx.fillStyle = look.colour;
        ctx.fillText(value, x, baseline);
        ctx.restore();
    }

    // ------------------------------------------------------------ state --

    Buddy.on("state", s => {
        state = s;
        $("timeline").textContent = s.timeline || "";
        $("dot").classList.toggle("on", !!s.connected);
        $("problem").textContent = s.problem || "";
        $("replace").checked = !!s.replace;
        $("add").disabled = !!s.adding;
        fill(s.spec);
        document.dispatchEvent(new CustomEvent("graphics-state", {detail: s}));
        range = s.curve_range || range;
        $("dock-body").hidden = !s.dock;
        $("dock-fold").setAttribute("aria-expanded", String(!!s.dock));
        drawLanes();
        // A change never plays the preview by itself: it's redrawn on the frame it was
        // on (the last one, if the graphic got shorter) - or keeps playing if it was.
        // Play is the person's to press.
        const last = s.preview.total - 1;
        frame = seen ? Math.min(frame, last) : last;
        seen = true;
        if (!playing) paint();
    });

    function start(drawFrame) {
        draw = drawFrame;
        bind();
        bindDock();
        bindCurve();
        $("easing").addEventListener("change", e => setField("easing", e.target.value));
        $("dock-fold").append(Buddy.icon("sliders"));
        $("refresh").append(Buddy.icon("refresh"));
        $("refresh").addEventListener("click", () => Buddy.send("refresh"));
        $("replace").addEventListener("change", e => Buddy.send("option", {key: "replace", value: e.target.checked}));
        $("add").addEventListener("click", () => Buddy.send("add"));
        $("play").addEventListener("click", () => playing ? pause() : play(frame));
        $("scrub").addEventListener("input", e => {
            pause();
            frame = Number(e.target.value) || 0;
            paint();
        });
        drawPlay();
        new ResizeObserver(() => { if (!playing) paint(); }).observe($("preview").parentElement);
        // Fonts the page hasn't drawn with yet load late: draw again once they're in.
        if (document.fonts && document.fonts.addEventListener) {
            document.fonts.addEventListener("loadingdone", () => { if (!playing) paint(); });
        }
    }

    Buddy.on("toast", t => Buddy.toast(t.text, 3200));
    Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}),
                                        buttons: [{label: "OK", kind: "accent"}]}));

    return {start, text, textAt, arriving, move, panel};
})();
