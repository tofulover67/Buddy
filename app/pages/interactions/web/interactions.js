"use strict";

// Interactions' view (../page.py says what each event carries). Python owns the
// interaction; this draws it and reports what was done:
//
//   Arrange   the mouse moves and sizes the shapes (a "box" sent as it's dragged)
//   Try it    the mouse is the cursor: the shapes answer it as they will in Resolve
//   Record    what the mouse does on the stage is kept - sent once, when stopped
//   the player  the recorded cursor played back, frame for frame
//
// How a shape answers is ../model.py's rule (amounts()), the one the comp's
// expressions run: its hover is the share of the last few frames the cursor was on it,
// eased; its press the share it was on it with the button down. Here that is answer(),
// fed either the recorded frames (playing) or the mouse's last few (trying). What that
// makes it look like is model.look()'s sums of its own answers (its "states", or the
// interaction's "answers"): here lookOf().
//
// Rest · Hover · Pressed show, in Arrange, the picked shape (or every one that answers)
// in that state, still; the player and the scrubber show the recorded frame again.

(() => {
    const $ = id => document.getElementById(id);
    const el = Buddy.el;
    const TYPING_MS = 250;
    const CAP = 0.7;              // a capital's height in letter sizes (data_graphics/text_look.py)
    const HANDLE = 9;             // px round a picked shape's corner that stretch it

    let state = null;
    let fonts = {};
    let mode = "arrange";         // "arrange" | "try"
    let picked = "";              // the shape the fields are about
    let frame = 0;                // the recorded frame showing
    let playing = false;
    let startedAt = 0;
    let seen = false;
    let mouse = {x: 50, y: 50, down: false};   // where the mouse is on the stage, % - trying
    let trail = [];               // the mouse on each of the last frames, newest first
    let lastTick = 0;
    let rec = null;               // recording: {t0, samples, clicks, downAt}
    let hold = null;              // a shape held in Arrange: {id, size, x, y, box}
    let look = "rest";            // the state the chips show: "rest" | "hover" | "press"
    let view = "frame";           // "frame": the recorded frame | "state": the shapes in `look`, still

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

    function setElement(key, value, wait) {
        const id = picked;
        if (id) later("el:" + key, () => Buddy.send("element", {id, key, value}), wait);
    }

    function bind() {
        document.querySelectorAll("[data-key]").forEach(node => {
            const key = node.dataset.key;
            if (node.classList.contains("segmented")) {
                node.addEventListener("click", e => {
                    const button = e.target.closest("button[data-value]");
                    if (button) setField(key, button.dataset.value);
                });
            } else if (node.tagName === "SELECT") {
                node.addEventListener("change", () => setField(key, node.value));
            } else if (node.type === "range" || node.type === "color") {
                node.addEventListener("input", () => setField(key, node.value, 30));
            } else {
                node.addEventListener("input", () => setField(key, node.value, TYPING_MS));
                node.addEventListener("change", () => setField(key, node.value));
            }
        });
        $("el-label").addEventListener("input", e => setElement("label", e.target.value, TYPING_MS));
        $("el-label").addEventListener("change", e => setElement("label", e.target.value));
        $("el-colour").addEventListener("input", e => setElement("colour", e.target.value, 30));
        $("el-radius").addEventListener("input", e => setElement("radius", e.target.value, 30));
        $("el-reacts").addEventListener("change", e => setElement("reacts", e.target.checked));
        $("el-front").addEventListener("click", () => picked && Buddy.send("front_element", {id: picked}));
        $("el-remove").addEventListener("click", removePicked);
        $("kinds").addEventListener("click", e => {
            const button = e.target.closest("button[data-kind]");
            if (button) Buddy.send("new_element", {kind: button.dataset.kind});
        });

        // How shapes answer: the chips show a state on the stage, States… sets each one.
        $("look").addEventListener("click", e => {
            const button = e.target.closest("button[data-value]");
            if (!button) return;
            look = button.dataset.value;
            view = "state";
            drawChips();
            if (!playing && !live()) paint();
        });
        document.querySelectorAll("[data-state] [data-answer]").forEach(node => {
            const which = node.closest("[data-state]").dataset.state, key = node.dataset.answer;
            node.addEventListener("input", () => {
                const id = ownStates() ? picked : "";
                later(`answers:${id}:${which}:${key}`,
                      () => Buddy.send("answers", {id, state: which, key, value: Number(node.value)}), 30);
            });
        });
        $("own-states").addEventListener("change", e => {
            if (!shape(picked)) return;
            if (e.target.checked) {
                // Its own states start as the interaction's.
                Buddy.send("answers", {id: picked, state: "hover", key: "grow", value: state.spec.answers.hover.grow});
            } else {
                Buddy.send("answers", {id: picked, state: "hover", key: "reset", value: null});
            }
        });
    }

    function drawChips() {
        $("look").querySelectorAll("button[data-value]").forEach(b =>
            b.setAttribute("aria-pressed", String(view === "state" && b.dataset.value === look)));
    }

    // Whether the States pop is about the picked shape's own states.
    function ownStates() {
        const it = shape(picked);
        return !!(it && it.states);
    }

    function plain(value) {
        return typeof value === "number" ? String(+value.toFixed(6)) : String(value == null ? "" : value);
    }

    function put(node, value) {
        // What's being typed is left alone: Python has it, tidied, when the field is left.
        if (node !== document.activeElement && node.value !== plain(value)) node.value = plain(value);
    }

    function fill(spec) {
        document.querySelectorAll("[data-key]").forEach(node => {
            const value = spec[node.dataset.key];
            if (node.classList.contains("segmented")) {
                node.querySelectorAll("button[data-value]").forEach(b =>
                    b.setAttribute("aria-pressed", String(b.dataset.value === String(value))));
            } else if (node.tagName === "SELECT") {
                if (node.value !== plain(value)) node.value = plain(value);
            } else {
                put(node, value);
            }
        });
        fillStyles(spec);
        pickedChanged();
    }

    // The States pop: the picked shape's own states, or the interaction's.
    function fillStates() {
        if (!state) return;
        const it = shape(picked);
        const answers = answersOf(it);
        $("own-states").checked = !!(it && it.states);
        $("own-states").disabled = !it || !!state.adding;
        document.querySelectorAll("[data-state]").forEach(group => {
            const values = answers[group.dataset.state];
            group.querySelectorAll("[data-answer]").forEach(node => put(node, values[node.dataset.answer]));
        });
        if (!$("states-pop").hidden) drawStates();
    }

    function fillStyles(spec) {
        const font = $("font"), style = $("style");
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

    function shape(id) {
        return state ? state.spec.elements.find(e => e.id === id) || null : null;
    }

    function fillPicked() {
        const it = shape(picked);
        if (!it) picked = "";
        $("picked-fields").hidden = !it;
        $("none-picked").hidden = !!it;
        if (!it) return;
        put($("el-label"), it.label);
        put($("el-colour"), it.colour);
        put($("el-radius"), it.radius);
        $("el-radius-box").hidden = it.kind === "circle";
        $("el-colour").title = it.kind === "image" ? "An image's colour is the colour of its glow" : "";
        $("el-reacts").checked = !!it.reacts;
        const busy = !!state.adding;
        $("el-front").disabled = busy || state.spec.elements[state.spec.elements.length - 1].id === it.id;
        $("el-remove").disabled = busy;
    }

    function pickedChanged() {
        fillPicked();
        fillStates();
    }

    function removePicked() {
        if (!picked) return;
        Buddy.send("remove_element", {id: picked});
        picked = "";
    }

    Buddy.on("picked", data => {
        picked = data.id || "";
        pickedChanged();
        if (!playing) paint();
    });

    // ---------------------------------------------------- the pop-ups --
    // The detailed options live in small panels over the page, one at a time: opened
    // by a button, moved by the head, closed by the X or Escape.

    const panels = [];

    function panel(popId, buttonId, shown) {
        const pop = $(popId), button = $(buttonId), head = pop.querySelector(".dg-pop-head");
        const me = {
            show(on) {
                if (pop.hidden === !on) return;
                if (on) panels.forEach(other => { if (other !== me) other.show(false); });
                pop.hidden = !on;
                button.setAttribute("aria-expanded", String(on));
                if (on && shown) shown();
            },
        };
        panels.push(me);
        const close = pop.querySelector(".dg-pop-close");
        close.append(Buddy.icon("close"));
        close.addEventListener("click", () => me.show(false));
        button.addEventListener("click", () => me.show(pop.hidden));
        let grip = null;
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
    }

    // -------------------------------------------------------- the rule --

    function inside(it, x, y) {
        if (it.kind === "circle") {
            const [fw, fh] = state.preview.frame;
            const dx = (x - it.x) / 100 * fw, dy = (y - it.y) / 100 * fh, r = it.w / 100 * fw / 2;
            return dx * dx + dy * dy < r * r;
        }
        return Math.abs(x - it.x) < it.w / 2 && Math.abs(y - it.y) < it.h / 2;
    }

    // Where the cursor was k frames ago: {x, y, down} - before the recorded frame `now`
    // (the one showing, unless said), or the mouse's, trying.
    function past(k, now) {
        if (now === undefined && live()) return trail[Math.min(k, trail.length - 1)] || mouse;
        const p = state.preview, f = Math.max(0, (now === undefined ? frame : now) - k);
        return {x: p.cursor[f][0], y: p.cursor[f][1], down: !!p.down[f]};
    }

    function share(frames, test, now) {
        let n = 0;
        for (let k = 0; k < frames; k++) if (test(past(k, now))) n++;
        return n / frames;
    }

    // How a shape answers now (or on recorded frame `now`): {hover, press}, each 0..1.
    function answer(it, now) {
        if (!it.reacts) return {hover: 0, press: 0};
        const p = state.preview;
        const raw = share(p.hover_frames, at => inside(it, at.x, at.y), now);
        return {hover: raw * raw * (3 - 2 * raw), press: share(p.press_frames, at => at.down && inside(it, at.x, at.y), now)};
    }

    // How a shape answers: its own states, or the interaction's (model.answers_of).
    function answersOf(it) {
        return (it && it.states) || state.spec.answers;
    }

    // What a shape looks like hovered and pressed that much: model.look()'s sums.
    function lookOf(answers, hover, press) {
        const h = answers.hover, p = answers.press;
        return {scale: 1 + h.grow / 100 * hover + p.grow / 100 * press,
                lift: h.lift * hover + p.lift * press,
                glow: Math.min(1, Math.max(0, h.glow / 100 * hover + p.glow / 100 * press)),
                outline: Math.min(1, Math.max(0, h.outline / 100 * hover + p.outline / 100 * press))};
    }

    // How far a shape is hovered and pressed as it's drawn: the chips' state, shown
    // still in Arrange - or the frame (playing, scrubbed) or the mouse (trying).
    function amountsOf(it) {
        if (view === "state" && !playing && !live()) {
            if (!it.reacts || look === "rest" || (picked && it.id !== picked)) return {hover: 0, press: 0};
            return {hover: 1, press: look === "press" ? 1 : 0};
        }
        return answer(it);
    }

    // The mouse is the cursor: trying it (or recording), and not playing the recording back.
    function live() {
        return mode === "try" && !playing;
    }

    // -------------------------------------------------------- drawing --

    // An image shape's picture, once it has loaded (state.images: {id: file URL}).
    const pictures = new Map();

    function picture(it) {
        const url = state.images[it.id];
        if (!url) return null;
        let pic = pictures.get(url);
        if (!pic) {
            pic = new Image();
            pic.onload = () => { if (!playing && !live()) paint(); };
            pic.src = url;
            pictures.set(url, pic);
        }
        return pic.complete && pic.naturalWidth ? pic : null;
    }

    function lifted(hex, by) {
        const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
        return `rgb(${c.map(v => Math.round(v + (255 - v) * by)).join(", ")})`;
    }

    function outline(ctx, it, w, h, grow) {
        ctx.beginPath();
        if (it.kind === "circle") {
            ctx.ellipse(0, 0, w / 2 + grow, w / 2 + grow, 0, 0, Math.PI * 2);
        } else {
            const r = Math.min(w, h) / 2 * it.radius / 100 + grow;
            ctx.roundRect(-w / 2 - grow, -h / 2 - grow, w + 2 * grow, h + 2 * grow, Math.max(0, r));
        }
    }

    function drawShape(ctx, it) {
        const [fw, fh] = state.preview.frame;
        const {hover, press} = amountsOf(it);
        ctx.save();
        ctx.translate(it.x / 100 * fw, it.y / 100 * fh);
        drawShapeHere(ctx, it, lookOf(answersOf(it), hover, press));
        ctx.restore();
    }

    // A shape about where the drawing is, looking as `pose` ({scale, lift, glow,
    // outline}) says: grown about its middle, and lifted - its glow, outline and label with it.
    function drawShapeHere(ctx, it, pose) {
        const [fw, fh] = state.preview.frame, spec = state.spec;
        const w = it.w / 100 * fw, h = (it.kind === "circle" ? it.w / 100 * fw : it.h / 100 * fh);
        ctx.save();
        ctx.translate(0, -pose.lift / 100 * fh);
        ctx.scale(pose.scale, pose.scale);
        const glow = pose.glow;
        if (glow > 0.001) {
            ctx.save();
            ctx.globalAlpha = Math.min(1, glow);
            ctx.filter = `blur(${state.glow.soft * fw * 0.45 * ctx.getTransform().a}px)`;
            ctx.fillStyle = lifted(it.colour, state.glow.lift);
            outline(ctx, it, w, h, state.glow.reach * fw);
            ctx.fill();
            ctx.restore();
        }
        outline(ctx, it, w, h, 0);
        if (it.kind === "image") {
            // Its picture, as large as fits its box and kept to its corners.
            const pic = picture(it);
            ctx.save();
            ctx.clip();
            if (pic) {
                const k = Math.min(w / it.px[0], h / it.px[1]);
                ctx.drawImage(pic, -it.px[0] * k / 2, -it.px[1] * k / 2, it.px[0] * k, it.px[1] * k);
            } else {
                ctx.fillStyle = "#2c2f37";          // not loaded, or not there any more
                ctx.fill();
            }
            ctx.restore();
        } else {
            ctx.fillStyle = it.colour;
            ctx.fill();
        }
        const edge = pose.outline;
        if (edge > 0.001) {
            // The outline: a line along the shape's own edge, half inside it and half out.
            ctx.save();
            ctx.globalAlpha = Math.min(1, edge);
            ctx.strokeStyle = spec.outline_colour;
            ctx.lineWidth = spec.outline_width / 100 * fh;
            outline(ctx, it, w, h, 0);
            ctx.stroke();
            ctx.restore();
        }
        if (it.label) {
            const px = spec.label_size / 100 * fh, face = state.face;
            ctx.font = `${face.italic ? "italic " : ""}${face.weight} ${px}px "${face.family.replace(/"/g, "")}", sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "alphabetic";
            ctx.fillStyle = spec.label_colour;
            ctx.fillText(it.label, 0, CAP * px / 2);
        }
        ctx.restore();
    }

    function drawPicked(ctx, it) {
        const [fw, fh] = state.preview.frame, k = 1 / ctx.getTransform().a;      // a screen px, in the frame's
        const w = it.w / 100 * fw, h = (it.kind === "circle" ? it.w / 100 * fw : it.h / 100 * fh);
        const x = it.x / 100 * fw, y = it.y / 100 * fh;
        ctx.save();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5 * k;
        ctx.setLineDash([5 * k, 4 * k]);
        ctx.strokeRect(x - w / 2, y - h / 2, w, h);
        ctx.setLineDash([]);
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(x + w / 2 - 4 * k, y + h / 2 - 4 * k, 8 * k, 8 * k);
        ctx.restore();
    }

    // A sample button for the States pop's pictures when no shape is picked: a new
    // button's look (model.NEW), following the interaction's answers.
    const SAMPLE = {id: "", kind: "button", x: 50, y: 50, w: 14, h: 9, radius: 50, colour: "#3c82e0", label: "",
                    reacts: true, states: null};

    // The three states side by side: the picked shape (or a sample button) in each, by its answers.
    function drawStates() {
        if (!state) return;
        const it = shape(picked) || SAMPLE, [fw, fh] = state.preview.frame;
        const answers = answersOf(it);
        const w = it.w / 100 * fw, h = (it.kind === "circle" ? it.w / 100 * fw : it.h / 100 * fh);
        const room = Math.max(w, h) * 0.3 + state.glow.reach * fw * 2;
        const lift = Math.max(0, ...["hover", "press"].map(s => Math.abs(answers[s].lift))) / 100 * fh * 2;
        [["rest", 0, 0], ["hover", 1, 0], ["press", 1, 1]].forEach(([name, hover, press]) => {
            const canvas = $("state-" + name), ctx = canvas.getContext("2d");
            const k = Math.min(canvas.width / (w + 2 * room), canvas.height / (h + 2 * room + lift));
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.setTransform(k, 0, 0, k, canvas.width / 2, canvas.height / 2);
            drawShapeHere(ctx, it, lookOf(answers, it.reacts ? hover : 0, it.reacts ? press : 0));
        });
    }

    function drawCursor(ctx) {
        const spec = state.spec;
        if (spec.cursor === "none") return;
        const [, fh] = state.preview.frame, at = past(0);
        const [fw] = state.preview.frame;
        const held = share(state.preview.press_frames, p => p.down);
        const size = spec.cursor_size / 100 * fh;
        ctx.save();
        ctx.translate(at.x / 100 * fw, at.y / 100 * fh);
        const look = state.cursors[spec.cursor];      // a Windows or a Mac pointer, or a dot
        ctx.fillStyle = look.fill;
        ctx.strokeStyle = look.edge;
        ctx.lineJoin = "round";
        if (spec.cursor === "dot") {
            const r = look.width * size / 2;
            ctx.beginPath();
            ctx.arc(0, 0, r * 1.3, 0, Math.PI * 2);
            ctx.fillStyle = look.edge;
            ctx.fill();
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.fillStyle = look.fill;
            ctx.fill();
        } else {
            const s = size * (1 - state.cursor_dip * held);
            ctx.beginPath();
            look.points.forEach(([ax, ay], i) => i ? ctx.lineTo(ax * s, ay * s) : ctx.moveTo(ax * s, ay * s));
            ctx.closePath();
            ctx.lineWidth = look.edge_w * s;
            ctx.stroke();
            ctx.fill();
        }
        ctx.restore();
    }

    function paint() {
        const canvas = $("stage");
        if (!state) return;
        const [fw, fh] = state.preview.frame;
        const box = canvas.parentElement;
        const width = Math.max(120, Math.min(box.clientWidth, Math.floor(window.innerHeight * 0.52 * fw / fh)));
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
        state.spec.elements.forEach(it => drawShape(ctx, it));
        const it = mode === "arrange" && !playing ? shape(picked) : null;
        if (it) drawPicked(ctx, it);
        drawCursor(ctx);
        const total = state.preview.total, fps = state.preview.fps;
        $("scrub").max = String(total - 1);
        $("scrub").value = String(frame);
        $("time").textContent = `${(frame / fps).toFixed(1)} / ${((total - 1) / fps).toFixed(1)}`;
    }

    // --------------------------------------------------------- player --

    function drawPlay() {
        $("play").replaceChildren(Buddy.icon(playing ? "pause" : "play"));
        $("play").setAttribute("aria-label", playing ? "Pause" : "Play");
        $("play").title = playing ? "Pause" : "Play";
    }

    function tick(now) {
        if (!playing || !state) return;
        const last = state.preview.total - 1;
        frame = Math.max(0, Math.min(last, Math.floor((now - startedAt) * state.preview.fps / 1000)));
        paint();
        if (frame >= last) {
            pause();
        } else {
            requestAnimationFrame(tick);
        }
    }

    function play(from) {
        if (!state || rec) return;
        frame = from >= state.preview.total - 1 ? 0 : from;
        startedAt = performance.now() - frame * 1000 / state.preview.fps;
        playing = true;
        view = "frame";             // paused, it stays on the frame it got to
        drawChips();
        drawPlay();
        requestAnimationFrame(tick);
    }

    function pause() {
        if (!playing) return;
        playing = false;
        drawPlay();
        if (mode === "try") startTrying();
    }

    // ------------------------------------------------- trying, recording --
    // While the mouse is the cursor, a frame's worth of where it is goes on the trail
    // every 1/fps s - the past the shapes answer to - and, recording, into the samples.

    let trying = false;           // tryTick is going round

    function startTrying() {
        trail = [];
        lastTick = 0;
        if (trying) return;
        trying = true;
        requestAnimationFrame(tryTick);
    }

    function tryTick(now) {
        if (!live() || !state) {
            trying = false;
            return;
        }
        const step = 1000 / state.preview.fps;
        if (!lastTick || now - lastTick > step * 8) lastTick = now - step;       // back after a pause: one frame, not a burst
        const keep = Math.max(state.preview.hover_frames, state.preview.press_frames) + 1;
        while (now - lastTick >= step) {
            lastTick += step;
            trail.unshift({...mouse});
            if (trail.length > keep) trail.length = keep;
            if (rec && rec.t0 !== null) {
                const t = (lastTick - rec.t0) / 1000;
                rec.samples.push([+t.toFixed(3), +mouse.x.toFixed(2), +mouse.y.toFixed(2)]);
                $("rec-time").textContent = `${t.toFixed(1)} / ${state.max_seconds.toFixed(0)}`;
                if (t >= state.max_seconds) {
                    trying = false;
                    stopRecording();
                    return;
                }
            }
        }
        paint();
        requestAnimationFrame(tryTick);
    }

    function recTime() {
        return rec && rec.t0 !== null ? Math.max(0, (performance.now() - rec.t0) / 1000) : null;
    }

    function startRecording() {
        if (!state || state.adding) return;
        playing = false;
        drawPlay();
        rec = {t0: null, samples: [], clicks: [], downAt: null};
        view = "frame";             // what was recorded is what's shown after
        drawChips();
        setMode("try");
        drawRecord();
    }

    function stopRecording() {
        if (!rec) return;
        const done = rec, t = recTime();
        rec = null;
        if (done.downAt !== null && t !== null) done.clicks.push([done.downAt, +t.toFixed(3)]);
        drawRecord();
        if (done.samples.length < 2) {
            Buddy.toast("Nothing was recorded.");
        } else {
            const end = done.samples[done.samples.length - 1][0];
            Buddy.send("recorded", {samples: done.samples, clicks: done.clicks, seconds: Math.max(state.min_seconds, end)});
            frame = 0;
        }
        if (live()) startTrying();
    }

    function drawRecord() {
        const on = !!rec;
        $("record").textContent = on ? "Stop" : "Record the cursor";
        $("record").classList.toggle("danger", on);
        $("stage-box").classList.toggle("recording", on);
        $("rec-time").textContent = "";
        drawHint();
    }

    function drawHint() {
        $("hint").textContent =
            rec ? "Recording starts when you move onto the stage. Move and click as it should play, then press Stop."
            : mode === "try" ? "Move the mouse over the shapes and click them. Nothing is kept until you record."
            : "Drag a shape to move it, the corner of the picked one to resize it.";
    }

    function setMode(next) {
        mode = next;
        $("mode").querySelectorAll("button[data-value]").forEach(b =>
            b.setAttribute("aria-pressed", String(b.dataset.value === mode)));
        $("stage-box").classList.toggle("try", mode === "try");
        drawHint();
        if (live()) startTrying();
        else if (!playing) paint();
    }

    // ------------------------------------------------------- the stage --

    function at(e) {
        const canvas = $("stage"), box = canvas.getBoundingClientRect();
        return {x: (e.clientX - box.left) / box.width * 100, y: (e.clientY - box.top) / box.height * 100,
                px: 100 / box.width, py: 100 / box.height};
    }

    function clamp(v, low, high) {
        return Math.min(high, Math.max(low, v));
    }

    function topShape(x, y) {
        const all = state.spec.elements;
        for (let i = all.length - 1; i >= 0; i--) {
            const it = all[i];
            if (it.kind === "circle" ? inside(it, x, y) : Math.abs(x - it.x) <= it.w / 2 && Math.abs(y - it.y) <= it.h / 2) return it;
        }
        return null;
    }

    function height(it) {
        const [fw, fh] = state.preview.frame;
        return it.kind === "circle" ? it.w * fw / fh : it.h;
    }

    function onCorner(it, p) {
        return Math.abs(p.x - (it.x + it.w / 2)) <= HANDLE * p.px && Math.abs(p.y - (it.y + height(it) / 2)) <= HANDLE * p.py;
    }

    function bindStage() {
        const canvas = $("stage");
        canvas.addEventListener("pointerdown", e => {
            if (!state || e.button !== 0) return;
            const p = at(e);
            canvas.setPointerCapture(e.pointerId);
            if (mode === "try") {
                if (playing) pause();
                mouse = {x: clamp(p.x, 0, 100), y: clamp(p.y, 0, 100), down: true};
                const t = recTime();
                if (rec && t !== null) rec.downAt = +t.toFixed(3);
                return;
            }
            if (playing) pause();
            if (state.adding) return;
            const mine = shape(picked);
            const it = mine && onCorner(mine, p) ? mine : topShape(p.x, p.y);
            picked = it ? it.id : "";
            pickedChanged();
            if (it) hold = {id: it.id, size: it === mine && onCorner(it, p), x: p.x, y: p.y, box: {x: it.x, y: it.y, w: it.w, h: it.h}};
            paint();
        });
        canvas.addEventListener("pointermove", e => {
            if (!state) return;
            const p = at(e);
            if (mode === "try") {
                mouse = {...mouse, x: clamp(p.x, 0, 100), y: clamp(p.y, 0, 100)};
                if (rec && rec.t0 === null) {
                    rec.t0 = performance.now();
                    lastTick = rec.t0;
                    rec.samples.push([0, +mouse.x.toFixed(2), +mouse.y.toFixed(2)]);
                }
                return;
            }
            const it = hold && shape(hold.id);
            if (!it) {
                const mine = shape(picked);
                canvas.style.cursor = mine && onCorner(mine, p) ? "nwse-resize" : topShape(p.x, p.y) ? "move" : "";
                return;
            }
            const [fw, fh] = state.preview.frame;
            if (hold.size) {
                // Stretched from its middle: the corner follows the mouse.
                it.w = clamp(2 * Math.abs(p.x - it.x), 1, 100);
                if (it.kind === "circle") it.w = clamp(Math.max(it.w, 2 * Math.abs(p.y - it.y) * fh / fw), 1, 100);
                else if (it.kind === "image") it.h = clamp(it.w * fw / fh * it.px[1] / it.px[0], 1, 100);      // its own shape
                else it.h = clamp(2 * Math.abs(p.y - it.y), 1, 100);
            } else {
                it.x = clamp(hold.box.x + p.x - hold.x, 0, 100);
                it.y = clamp(hold.box.y + p.y - hold.y, 0, 100);
            }
            later("box", () => Buddy.send("element", {id: it.id, key: "box", value: {x: it.x, y: it.y, w: it.w, h: it.h}}), 40);
            paint();
        });
        const drop = e => {
            if (mode === "try") {
                mouse = {...mouse, down: false};
                const t = recTime();
                if (rec && rec.downAt !== null && t !== null) {
                    rec.clicks.push([rec.downAt, +Math.max(t, rec.downAt + 0.02).toFixed(3)]);
                    rec.downAt = null;
                }
                return;
            }
            const it = hold && shape(hold.id);
            hold = null;
            if (it && e.type === "pointerup") {
                later("box", () => Buddy.send("element", {id: it.id, key: "box", value: {x: it.x, y: it.y, w: it.w, h: it.h}}));
            }
        };
        canvas.addEventListener("pointerup", drop);
        canvas.addEventListener("pointercancel", drop);
    }

    // -------------------------------------------------------- history --
    // Python keeps the steps: Undo, Redo and Reset only ask. In a text field Ctrl+Z is
    // the field's own; anywhere else it's the interaction's.

    function typing(target) {
        const field = target && target.closest && target.closest("input, textarea, select, [contenteditable]");
        return !!field && !(field.tagName === "INPUT" && /^(checkbox|radio|range|color|button)$/.test(field.type));
    }

    function bindKeys() {
        document.addEventListener("keydown", e => {
            const blocked = document.querySelector(".modal-backdrop") || document.getElementById("picker");
            if (e.key === "Escape" && !blocked) {
                if (rec) stopRecording();
                panels.forEach(p => p.show(false));
                return;
            }
            if (typing(e.target) || blocked) return;
            const key = e.key.toLowerCase();
            if ((e.ctrlKey || e.metaKey) && !e.altKey && (key === "z" || key === "y")) {
                e.preventDefault();
                Buddy.send(key === "y" || e.shiftKey ? "redo" : "undo");
            } else if ((key === "delete" || key === "backspace") && mode === "arrange" && picked) {
                e.preventDefault();
                removePicked();
            }
        });
    }

    // ------------------------------------------------------------ state --

    Buddy.on("state", s => {
        const dragging = hold && shape(hold.id);
        state = s;
        if (dragging) {
            // The shape in the hand stays where the hand has it until it's let go.
            const it = shape(hold.id);
            if (it) Object.assign(it, {x: dragging.x, y: dragging.y, w: dragging.w, h: dragging.h});
        }
        $("timeline").textContent = s.timeline || "";
        $("dot").classList.toggle("on", !!s.connected);
        $("problem").textContent = s.problem || "";
        $("replace").checked = !!s.replace;
        $("add").disabled = !!s.adding;
        $("record").disabled = !!s.adding;
        $("undo").disabled = !s.can_undo || !!s.adding;
        $("redo").disabled = !s.can_redo || !!s.adding;
        $("reset").disabled = !!s.is_template || !!s.adding;
        document.querySelectorAll("#kinds button").forEach(b => { b.disabled = !!s.full || !!s.adding; });
        fill(s.spec);
        // A change never plays the preview by itself: it's redrawn on the frame it was on.
        const last = s.preview.total - 1;
        frame = seen ? Math.min(frame, last) : 0;
        seen = true;
        if (!playing && !live()) paint();
    });

    Buddy.on("drop_hover", on => $("stage-box").classList.toggle("drop", on === true));
    Buddy.on("toast", t => Buddy.toast(t.text, 3200));
    Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}),
                                        buttons: [{label: "OK", kind: "accent"}]}));

    bind();
    bindStage();
    bindKeys();
    panel("states-pop", "edit-states", drawStates);
    panel("timing-pop", "edit-timing");
    panel("outline-pop", "edit-outline");
    panel("labels-pop", "edit-labels");
    $("undo").append(Buddy.icon("undo"));
    $("redo").append(Buddy.icon("undo"));          // turned round in graphics.css
    $("undo").addEventListener("click", () => Buddy.send("undo"));
    $("redo").addEventListener("click", () => Buddy.send("redo"));
    $("reset").addEventListener("click", () => {
        Buddy.send("reset");
        Buddy.toast("Back to the template.", 5000, {label: "Undo", onClick: () => Buddy.send("undo")});
    });
    $("refresh").append(Buddy.icon("refresh"));
    $("refresh").addEventListener("click", () => Buddy.send("refresh"));
    $("replace").addEventListener("change", e => Buddy.send("option", {key: "replace", value: e.target.checked}));
    $("add").addEventListener("click", () => {
        if (rec) stopRecording();
        Buddy.send("add");
    });
    $("mode").addEventListener("click", e => {
        const button = e.target.closest("button[data-value]");
        if (!button || button.dataset.value === mode) return;
        if (rec) stopRecording();
        setMode(button.dataset.value);
    });
    $("record").addEventListener("click", () => rec ? stopRecording() : startRecording());
    $("play").addEventListener("click", () => {
        if (rec) stopRecording();
        playing ? pause() : play(frame);
    });
    $("scrub").addEventListener("input", e => {
        if (rec) stopRecording();
        playing = false;
        drawPlay();
        // Looking through the recording: the mouse lets go of the cursor until it's back on the stage.
        if (mode === "try") setMode("arrange");
        frame = Number(e.target.value) || 0;
        view = "frame";
        drawChips();
        paint();
    });
    drawPlay();
    drawChips();
    setMode("arrange");
    new ResizeObserver(() => { if (!playing && !live()) paint(); }).observe($("stage").parentElement);
    if (document.fonts && document.fonts.addEventListener) {
        document.fonts.addEventListener("loadingdone", () => { if (!playing && !live()) paint(); });
    }

    // For the tests: what the view is doing, and how it draws a shape.
    window.interactionsView = {
        info: () => ({mode, view, look, picked, frame, playing, recording: !!rec}),
        // A shape's look as the stage draws it now, or on recorded frame f.
        lookOf: (id, f) => {
            const it = shape(id);
            if (!it) return null;
            const {hover, press} = f === undefined ? amountsOf(it) : answer(it, f);
            return lookOf(answersOf(it), hover, press);
        },
        // How much of each States picture is drawn on.
        pictures: () => ["rest", "hover", "press"].map(name => {
            const c = $("state-" + name), d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
            let n = 0;
            for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
            return n;
        }),
        picture: () => $("stage"),
    };
})();
