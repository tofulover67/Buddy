/*
 * Console: one track at a time, built like a piece of hardware - a faceplate
 * of knobs with a live scope of what the track is playing, a large piano roll
 * of one clip to edit, and the keyboard with an octave wheel and a velocity
 * wheel. For shaping one sound and writing one part carefully.
 *
 *   Roll    click to add a note (the last length used), drag to move,
 *           drag its right end to lengthen, double-click or right-click to
 *           delete, Shift + drag to select several; Delete, the arrows
 *           (Shift: an octave, or a bar), Ctrl+A and Ctrl+D on the selection;
 *           the velocity lane under it
 */
"use strict";

window.DawViews = window.DawViews || {};

DawViews.console = (() => {
    const {el, icon} = Buddy;
    let root = null, UI = null;

    function build(into) {
        root = into;
        UI = {};
        UI.tracks = el("div.con-tracks");
        UI.clipBar = el("div.con-clipbar");
        UI.faceplate = el("div.faceplate");
        UI.scope = el("canvas.scope-canvas");
        UI.roll = Ctl.roll({
            track: () => Daw.selTrack(),
            clip: () => Daw.selClip(),
            onEdit: (label, fn) => Daw.edit(label, fn, "notes"),
            onPreview: midi => {
                Daw.noteOn("roll", [midi], Daw.P.velocity, null, true);
                setTimeout(() => Daw.noteOff("roll"), 180);
            },
            position: () => (Daw.playing() ? Daw.position() : Daw.P.cursor),
            // DawApp is still being made while this is built: looked up when it's used.
            seek: {start: () => DawApp.scrub.start(), to: b => DawApp.scrub.to(b), end: () => DawApp.scrub.end()},
        });
        UI.empty = el("div.roll-empty");
        UI.keyboard = Parts.keyboard(4);
        UI.chords = Parts.chordControl();
        UI.octave = wheel("Octave", () => (Daw.P.octave - 1) / 6, u => {
            const o = Math.round(1 + u * 6);
            if (o !== Daw.P.octave) { Daw.allOff(); Daw.P.octave = o; Daw.changed("keys"); }
        }, () => `C${Daw.P.octave}`);
        UI.velocity = wheel("Velocity", () => (Daw.P.velocity - 0.1) / 0.9, u => {
            Daw.P.velocity = Math.round((0.1 + u * 0.9) * 100) / 100;
            Daw.changed("keys");
        }, () => `${Math.round(Daw.P.velocity * 100)}%`);
        root.append(el("div.con", {}, [
            el("div.con-top", {}, [UI.tracks, el("span.spacer"), UI.clipBar]),
            UI.faceplate,
            el("div.con-roll", {}, [UI.roll.node, UI.empty]),
            el("div.con-bottom", {}, [el("div.wheels", {}, [UI.octave.node, UI.velocity.node]),
                el("div.con-keys", {}, [UI.chords.node, UI.keyboard.node])]),
        ]));
        refresh("all");
    }

    /* A wheel beside the keys: drag up or down. */
    function wheel(label, get, set, text) {
        const thumb = el("i"), out = el("span.wv", {translate: "no"});
        const node = el("div.wheel", {tabindex: "0", role: "slider", "aria-label": label, title: label}, [el("div.wheel-body", {}, [thumb]), out,
            el("span.daw-label", {text: label})]);
        const body = node.firstChild;
        const draw = () => { thumb.style.top = `${(1 - get()) * (body.clientHeight - 18 || 82)}px`; out.textContent = text(); };
        let drag = null;
        body.addEventListener("pointerdown", e => {
            e.preventDefault();
            drag = {y: e.clientY, u: get()};
            body.setPointerCapture(e.pointerId);
        });
        body.addEventListener("pointermove", e => {
            if (!drag) return;
            set(Math.min(1, Math.max(0, drag.u + (drag.y - e.clientY) / 100)));
            draw();
        });
        body.addEventListener("pointerup", () => { drag = null; });
        node.addEventListener("keydown", e => {
            const d = {ArrowUp: 1, ArrowDown: -1}[e.key];
            if (!d) return;
            e.preventDefault();
            e.stopPropagation();
            set(Math.min(1, Math.max(0, get() + d / 6)));
            draw();
        });
        return {node, draw};
    }

    function drawTracks() {
        const P = Daw.P;
        UI.tracks.replaceChildren(el("div.seg.con-seg", {role: "group", "aria-label": "Track"}, P.tracks.map(t =>
            el("button", {type: "button", "aria-pressed": String(t.id === P.sel.track), translate: "no",
                style: `--track:${Parts.color(t)}`, onclick: () => pickTrack(t)}, [el("i.dot"), el("span", {text: t.name})]))));
    }

    function pickTrack(t) {
        Daw.allOff();
        const c = Daw.clipOf(t, Daw.P.sel.clip) || clipAtCursor(t) || t.clips[0];
        Daw.select(t.id, c ? c.id : "");
    }

    const clipAtCursor = t => t.clips.find(c => Daw.P.cursor >= c.start && Daw.P.cursor < c.start + c.len) || null;

    function drawClipBar() {
        const t = Daw.selTrack(), c = Daw.selClip();
        if (!t) return UI.clipBar.replaceChildren();
        const i = c ? t.clips.indexOf(c) : -1;
        const go = d => { const n = t.clips[i + d]; if (n) { Daw.select(t.id, n.id); DawApp.moveCursor(n.start, false); } };
        Parts.fill(UI.clipBar,
            el("button.btn.ghost.icon", {type: "button", title: "The clip before", disabled: i <= 0, onclick: () => go(-1)}, [icon("left")]),
            el("span.con-clipname", {translate: "no", text: c ? `${c.name} · ${Buddy.t("bar")} ${Math.floor(c.start / 4) + 1}` : Buddy.t("No clip")}),
            el("button.btn.ghost.icon", {type: "button", title: "The clip after", disabled: i < 0 || i >= t.clips.length - 1, onclick: () => go(1)}, [icon("right")]),
            c ? clipLength(t, c) : null,
            t.kind === "drums" ? null : el("button.btn.ghost", {type: "button", text: "Write a progression…",
                title: "Four bars of chords in the key, from the cursor's bar", onclick: e => Parts.progressionMenu(t, e.currentTarget)}),
            el("button.btn.ghost", {type: "button", onclick: () => newClip(t)}, [icon("plus"), el("span", {text: "New clip"})]));
    }

    function clipLength(t, c) {
        const s = el("select.field", {"aria-label": "Clip length", title: "How long the clip is"},
            [1, 2, 4, 8, 16].map(b => el("option", {value: String(b * 4), text: Buddy.t(b === 1 ? "1 bar" : `${b} bars`), selected: c.len === b * 4})));
        if (![4, 8, 16, 32, 64].includes(c.len)) s.prepend(el("option", {value: String(c.len), text: `${+(c.len / 4).toFixed(2)} ${Buddy.t("bars")}`, selected: true}));
        s.addEventListener("change", () => Daw.edit("Change a clip's length", () => {
            c.len = Number(s.value);
            c.notes = c.notes.filter(n => n[1] < c.len);
            for (const n of c.notes) n[2] = Math.min(n[2], c.len - n[1]);
        }, "clips"));
        return s;
    }

    /* A new clip at the cursor's bar (or after the track's last), a bar for drums, else 4. */
    function newClip(t) {
        const bars = t.kind === "drums" ? 1 : 4;
        let at = Math.floor(Daw.P.cursor / 4) * 4;
        if (t.clips.some(c => at < c.start + c.len && at + bars * 4 > c.start)) at = Math.max(0, ...t.clips.map(c => c.start + c.len));
        at = Math.ceil(at / 4) * 4;
        Daw.edit("Add a clip", () => {
            const c = {id: Daw.uid(), name: t.kind === "drums" ? "Beat" : "Clip", start: at, len: bars * 4, notes: []};
            t.clips.push(c);
            t.clips.sort((a, b) => a.start - b.start);
            Daw.P.sel.clip = c.id;
        }, "clips");
    }

    function drawFaceplate() {
        const t = Daw.selTrack();
        if (!t) return UI.faceplate.replaceChildren();
        const info = Parts.soundInfo(t);
        const names = t.kind === "drums" ? Daw.KITS : Daw.INSTRUMENTS;
        const n = Daw.P.tracks.indexOf(t) + 1;
        const brand = el("div.fp-brand", {}, [
            el("span.daw-label", {text: t.kind === "drums" ? "Drum kit" : "Instrument"}),
            el("span.model", {translate: "no"}, [info.label.toUpperCase(), el("em", {text: "·"}), String(n).padStart(2, "0")]),
            el("p.fp-about", {text: info.about}),
            el("div.fp-sounds", {role: "group", "aria-label": "Sound"}, names.map(s => el("button", {type: "button",
                text: (t.kind === "drums" ? Drums.KITS[s] : Instruments.KINDS[s]).label, "aria-pressed": String(s === t.sound),
                onclick: () => Parts.setSound(t, s)}))),
        ]);
        const modules = Parts.knobGroups(t, 48).map(g => el("div.module", {}, [el("span.daw-label", {text: g.group}), g.node]));
        UI.faceplate.replaceChildren(brand, ...modules,
            el("div.screen", {}, [el("span.cap", {text: "Scope"}), UI.scope]));
        UI.faceplate.style.setProperty("--track", Parts.color(t));
    }

    function drawScope() {
        const canvas = UI.scope;
        if (!canvas.isConnected) return;
        const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
        if (!w || !h) return;
        if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
        const g = canvas.getContext("2d");
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.clearRect(0, 0, w, h);
        g.strokeStyle = "rgba(255,255,255,.06)";
        for (let i = 1; i < 4; i++) { g.beginPath(); g.moveTo(0, h * i / 4); g.lineTo(w, h * i / 4); g.stroke(); }
        const t = Daw.selTrack(), a = t ? Daw.analyser(t.id) : null;
        const data = new Float32Array(a ? a.fftSize : 2);
        if (a) a.getFloatTimeDomainData(data);
        // From a rising zero crossing, so a steady note stands still.
        let from = 0;
        for (let i = 1; i < data.length / 2; i++) if (data[i - 1] < 0 && data[i] >= 0) { from = i; break; }
        const n = Math.min(data.length - from, 600);
        const live = getComputedStyle(root).getPropertyValue("--daw-live").trim() || "#f2a33a";
        g.beginPath();
        for (let i = 0; i < n; i++) {
            const x = i / Math.max(1, n - 1) * w, y = h / 2 - (data[from + i] || 0) * (h / 2 - 8) * 1.6;
            i ? g.lineTo(x, y) : g.moveTo(x, y);
        }
        g.strokeStyle = live;
        g.lineWidth = 2;
        g.shadowColor = live;
        g.shadowBlur = 8;
        g.stroke();
        g.shadowBlur = 0;
    }

    function drawRoll() {
        const t = Daw.selTrack(), c = Daw.selClip();
        UI.empty.hidden = !!c;
        UI.roll.node.hidden = !c;
        if (!c) {
            UI.empty.replaceChildren(el("p.muted", {text: t && t.clips.length ? "Pick a clip above, or make a new one." : "This track has no clips yet."}),
                el("button.btn.accent", {type: "button", onclick: () => t && newClip(t)}, [icon("plus"), el("span", {text: "New clip"})]));
            return;
        }
        UI.roll.fit();
    }

    function refresh(what) {
        if (!UI) return;
        if (what === "values") return;
        if (what === "notes" || what === "cursor") return UI.roll.draw();
        if (what === "keys") { UI.octave.draw(); UI.velocity.draw(); UI.chords.sync(); return UI.keyboard.draw(); }
        if (what === "mixer" || what === "loop") return;
        // Keep a clip in view: the selected one, else the one under the cursor.
        const t = Daw.selTrack();
        if (t && !Daw.selClip()) {
            const c = clipAtCursor(t) || t.clips[0];
            if (c) Daw.P.sel.clip = c.id;
        }
        drawTracks();
        drawClipBar();
        if (what !== "select" || UI.faceplateFor !== (t && t.id + t.sound)) drawFaceplate();
        UI.faceplateFor = t && t.id + t.sound;
        drawRoll();
        UI.keyboard.draw();
        UI.chords.sync();
        UI.octave.draw();
        UI.velocity.draw();
    }

    function frame(pos) {
        if (!UI) return;
        drawScope();
        if (Daw.playing()) UI.roll.draw(pos);
        UI.keyboard.light(Daw.sounding());
    }

    function shown() {
        if (!UI) return;
        refresh("all");
    }

    return {id: "console", label: "Console", build, refresh, frame, shown, keydown: () => false};
})();
