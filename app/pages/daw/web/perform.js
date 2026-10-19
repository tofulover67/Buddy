/*
 * Perform: for getting an idea down fast without music theory - chord pads
 * already in the key (no wrong chord to play), four macro knobs and an XY
 * pad that each move several settings at once, a drum step sequencer and
 * the keyboard. What's played can be recorded like anything else.
 *
 *   Tracks   across the top: the track the keyboard, the pads (an instrument
 *            track), the macros and the steps (a drum track) work on - a click
 *            to go from the drums to the keys and back
 *   Pads     click, or the number keys 1-8; Triad, 7th or Spread voicing.
 *            They play the selected track - or, with a drum track selected,
 *            the first instrument track - and its sound is picked beside them.
 *            Into the mix: Record (the pads' track, from the playhead, a count-in
 *            first), or Write - each pad puts its chord in at the playhead, as
 *            long as chosen, and the playhead moves on to the next
 *   Macros   Brightness, Ambience, Motion, Punch on the selected track; the XY
 *            pad is Brightness across and Motion up
 *   Steps    the drum track's beat at the cursor, a bar at a time: click a
 *            step, drag across to paint, Shift + click for an accent
 */
"use strict";

window.DawViews = window.DawViews || {};

DawViews.perform = (() => {
    const {el, icon} = Buddy;
    let root = null, UI = null;
    const SWINGS = [[0, "Straight"], [0.08, "Swing 8%"], [0.16, "Swing 16%"], [0.25, "Swing 25%"]];

    function build(into) {
        root = into;
        UI = {};
        UI.top = el("div.perf-top");
        UI.padsHead = el("div.ch");
        UI.padsFoot = el("div.ch.pads-foot");
        UI.pads = el("div.pads");
        UI.macrosHead = el("div.ch");
        UI.macros = el("div.macros");
        UI.xy = el("div.xy", {role: "slider", tabindex: "0", "aria-label": "Brightness and motion"}, [
            el("span.ax.ax-x", {text: "Brightness →"}), el("span.ax.ax-y", {text: "↑ Motion"}), el("div.dot")]);
        UI.drumsHead = el("div.ch");
        UI.seq = el("div.seq");
        UI.keyboard = Parts.keyboard(3);
        UI.chords = Parts.chordControl();
        root.append(UI.top, el("div.perf", {}, [
            el("section.pcard", {}, [UI.padsHead, UI.pads, UI.padsFoot]),
            el("section.pcard", {}, [UI.macrosHead, UI.macros, UI.xy]),
            el("section.pcard.wide", {}, [UI.drumsHead, UI.seq]),
            el("section.pcard.wide.pkeys", {}, [UI.chords.node, UI.keyboard.node]),
        ]));
        xyEvents();
        window.addEventListener("pointerup", () => { painting = null; });
        refresh("all");
    }

    // ------------------------------------------------------------ pads --

    const padTrack = () => {
        const t = Daw.selTrack();
        return t && t.kind === "inst" ? t : Daw.P.tracks.find(x => x.kind === "inst") || null;
    };

    // ------------------------------------------------------------ tracks --

    function drawTop() {
        const P = Daw.P;
        Parts.fill(UI.top,
            el("div.seg.con-seg", {role: "group", "aria-label": "Track"}, P.tracks.map(t =>
                el("button", {type: "button", "aria-pressed": String(t.id === P.sel.track), translate: "no",
                    title: t.kind === "drums" ? Buddy.t("Drums: the keyboard plays the kit, the steps are its beat") : Parts.soundInfo(t).label,
                    style: `--track:${Parts.color(t)}`, onclick: () => { Daw.allOff(); Daw.select(t.id); }},
                [el("i.dot"), el("span", {text: t.name})]))));
    }

    function drawPads() {
        const t = padTrack();
        Parts.fill(UI.padsHead,
            el("span.daw-label", {translate: "no", text: `${Buddy.t("Chord pads")} · ${Daw.keyName()}`}),
            el("span.spacer"),
            t ? el("span.muted.small", {translate: "no", text: t.name}) : null,
            t ? Parts.soundSelect(t) : null,
            Ctl.seg([["triad", "Triad"], ["seventh", "7th"], ["spread", "Spread"]], Daw.P.voicing, v => {
                Daw.P.voicing = v;
                Daw.changed("pads");
            }, "Voicing").node);
        // Into the mix: recorded as played, or written in a chord at a time.
        const lenPick = el("select.field", {"aria-label": "How long each chord is", title: "How long each chord written is"},
            WRITE_LENS.map(([v, text]) => el("option", {value: String(v), text, selected: v === writeLen})));
        lenPick.addEventListener("change", () => { writeLen = Number(lenPick.value); });
        Parts.fill(UI.padsFoot,
            el("button.btn.ghost.pads-rec", {type: "button", disabled: !t, onclick: () => recordPads(),
                title: "Record the pads into their track from the playhead (R), a bar of clicks first when Count in is on"},
                [el("i.rec-dot"), el("span", {text: "Record"})]),
            el("button.tbtn.toggle.pads-write", {type: "button", text: "Write", disabled: !t, "aria-pressed": String(writing),
                title: "Each pad puts its chord in at the playhead, then moves the playhead on - a progression a click at a time",
                onclick: () => { writing = !writing; drawPads(); }}),
            writing ? lenPick : null,
            el("span.muted.small.pads-hint", {text: writing ? "Click the chords in order" : ""}));
        UI.pads.replaceChildren(...Daw.chordsInKey().map((ch, i) => {
            const pad = el("button.pad", {type: "button", dataset: {pad: i}, title: `${Buddy.t("Key")} ${i + 1}`}, [
                el("span", {text: ch.name, translate: "no"}), el("small", {text: ch.numeral, translate: "no"})]);
            pad.addEventListener("pointerdown", e => { if (e.button === 0) { e.preventDefault(); Ctl.capture(pad, e); padOn(i); } });
            pad.addEventListener("pointerup", () => padOff(i));
            pad.addEventListener("pointercancel", () => padOff(i));
            return pad;
        }));
        if (!t) UI.pads.append(el("p.muted.small", {text: "Add an instrument track to play chords."}));
    }

    function padOn(i) {
        const t = padTrack();
        if (!t) return;
        if (writing && !Daw.recording()) writeChord(t, i);
        Daw.noteOn(`pad${i}`, Daw.chordsInKey()[i].notes, Daw.P.velocity, t.id, true);
        const node = UI.pads.querySelector(`[data-pad="${i}"]`);
        if (node) node.classList.add("on");
    }

    function padOff(i) {
        Daw.noteOff(`pad${i}`);
        const node = UI.pads.querySelector(`[data-pad="${i}"]`);
        if (node) node.classList.remove("on");
    }

    /* Record from the playhead into the pads' track (selected first, so what's played goes in). */
    function recordPads() {
        const t = padTrack();
        if (!t) return;
        if (Daw.recording()) { Daw.stop(); DawApp.syncBar(); return drawPads(); }
        Daw.select(t.id);
        Daw.start(true);
        DawApp.syncBar();
        drawPads();
    }

    const WRITE_LENS = [[2, "½ bar"], [4, "1 bar"], [8, "2 bars"]];
    let writing = false, writeLen = 4;

    /* Pad i's chord into track t at the playhead (stopped) or the stretch playing now, writeLen
       beats long: into the clip there (or the one ending there, grown), else a new clip from
       its bar - what was in that stretch replaced. The playhead moves on to the next. */
    function writeChord(t, i) {
        const P = Daw.P, len = writeLen;
        const at = Daw.playing() ? Math.floor(Daw.position() / len + 1e-6) * len : Math.round(P.cursor * 4) / 4;
        const notes = Daw.chordsInKey()[i].notes;
        Daw.edit("Write a chord", () => {
            let c = t.clips.find(x => at >= x.start && at < x.start + x.len) || t.clips.find(x => Math.abs(x.start + x.len - at) < 1e-6);
            if (!c) {
                const before = Math.max(0, ...t.clips.filter(x => x.start + x.len <= at).map(x => x.start + x.len));
                c = {id: Daw.uid(), name: "Chords", start: Math.max(before, Math.floor(at / 4) * 4), len: 0, notes: []};
                t.clips.push(c);
                t.clips.sort((a, b) => a.start - b.start);
            }
            const next = Math.min(Infinity, ...t.clips.filter(x => x !== c && x.start >= at).map(x => x.start));
            const end = Math.min(next, at + len);
            c.len = Math.max(c.len, Math.min(next - c.start, Math.ceil((end - c.start) / 4 - 1e-6) * 4));
            const s = at - c.start, d = end - at;
            c.notes = c.notes.filter(n => n[1] + n[2] <= s + 1e-6 || n[1] >= s + d - 1e-6);
            for (const m of notes) c.notes.push([m, +s.toFixed(4), +d.toFixed(4), P.velocity]);
            c.notes.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
            P.sel.track = t.id;
            P.sel.clip = c.id;
            if (!Daw.playing()) P.cursor = at + len;
        }, "clips");
    }

    // ------------------------------------------------------------ macros --

    function drawMacros() {
        const t = Daw.selTrack();
        Parts.fill(UI.macrosHead, el("span.daw-label", {text: "Macros"}), el("span.spacer"),
            t ? el("span.muted.small", {translate: "no", text: `${t.name} · ${Parts.soundInfo(t).label}`}) : null);
        UI.knobs = [];
        UI.macros.replaceChildren(...Daw.MACROS.map((m, i) => {
            const k = Ctl.knob({label: m.label, hint: m.hint, min: 0, max: 1, value: t ? t.macros[i] : 0.5, def: 0.5, size: 78,
                format: v => `${Math.round(v * 100)}%`,
                onStart: () => Daw.checkpoint("Turn a macro"),
                onInput: x => { if (t) { Daw.applyMacro(t, i, x); Daw.changed("values"); syncXY(); } }});
            UI.knobs.push(k);
            return k.node;
        }));
        syncXY();
    }

    function syncXY() {
        const t = Daw.selTrack();
        UI.xy.style.setProperty("--x", `${(t ? t.macros[0] : 0.5) * 100}%`);
        UI.xy.style.setProperty("--y", `${(1 - (t ? t.macros[2] : 0.5)) * 100}%`);
    }

    function xyEvents() {
        let down = false;
        const move = e => {
            const t = Daw.selTrack();
            if (!t) return;
            const r = UI.xy.getBoundingClientRect();
            const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
            const y = Math.min(1, Math.max(0, 1 - (e.clientY - r.top) / r.height));
            Daw.applyMacro(t, 0, +x.toFixed(3));
            Daw.applyMacro(t, 2, +y.toFixed(3));
            UI.knobs[0].set(x);
            UI.knobs[2].set(y);
            syncXY();
            Daw.changed("values");
        };
        UI.xy.addEventListener("pointerdown", e => {
            if (e.button !== 0) return;
            down = true;
            UI.xy.setPointerCapture(e.pointerId);
            Daw.checkpoint("Move the XY pad");
            move(e);
        });
        UI.xy.addEventListener("pointermove", e => { if (down) move(e); });
        UI.xy.addEventListener("pointerup", () => { down = false; });
    }

    // ------------------------------------------------------------ steps --

    let painting = null;          // while dragging across steps: true (switching on) or false
    let page = 0;                 // the bar of the clip shown

    const drumTrack = () => {
        const t = Daw.selTrack();
        return t && t.kind === "drums" ? t : Daw.P.tracks.find(x => x.kind === "drums") || null;
    };
    const drumClip = t => {
        if (!t) return null;
        const sel = Daw.clipOf(t, Daw.P.sel.clip);
        return sel || t.clips.find(c => Daw.P.cursor >= c.start && Daw.P.cursor < c.start + c.len) || t.clips[0] || null;
    };

    function drawSteps() {
        const t = drumTrack(), c = drumClip(t), P = Daw.P;
        const bars = c ? Math.max(1, Math.ceil(c.len / 4)) : 1;
        page = Math.min(page, bars - 1);
        Parts.fill(UI.drumsHead,
            el("span.daw-label", {translate: "no", text: t ? `${t.name} · ${Parts.soundInfo(t).label}` : Buddy.t("Drums")}),
            t ? Parts.soundSelect(t) : null,
            bars > 1 ? Ctl.seg([...Array(bars).keys()].map(b => [b, `${Buddy.t("Bar")} ${b + 1}`]), page, v => { page = v; drawSteps(); }, "Bar").node : null,
            el("span.spacer"),
            Ctl.seg(SWINGS.map(([v, label]) => [v, label]), SWINGS.reduce((a, b) => (Math.abs(b[0] - P.swing) < Math.abs(a[0] - P.swing) ? b : a))[0],
                v => Daw.edit("Change the swing", () => { P.swing = v; }, "swing"), "Swing").node);
        if (!t || !c) {
            UI.seq.replaceChildren(el("div.seq-empty", {}, [
                el("p.muted", {text: t ? "This drum track has no beat yet." : "There's no drum track yet."}),
                el("button.btn.accent", {type: "button", onclick: () => startBeat(t)}, [icon("plus"), el("span", {text: "Start a beat"})])]));
            return;
        }
        const rows = [];
        Drums.ROWS.forEach((row, r) => {
            rows.push(el("button.rn", {type: "button", title: "Hear it", onclick: () => {
                Daw.noteOn("step", [row.m], 0.9, t.id, true);
                setTimeout(() => Daw.noteOff("step"), 120);
            }}, [el("i"), el("span", {text: row.name})]));
            for (let s = 0; s < 16; s++) {
                const at = page * 4 + s / 4;
                const n = c.notes.find(x => x[0] === row.m && Math.abs(x[1] - at) < 1e-3);
                const step = el(`button.step${s % 4 === 0 ? ".beat" : ""}${n ? ".on" : ""}${n && n[3] >= 0.95 ? ".accent" : ""}`, {type: "button",
                    "aria-label": `${Buddy.t(row.name)} ${s + 1}`, "aria-pressed": String(!!n), dataset: {s}});
                if (at >= c.len) step.disabled = true;
                step.addEventListener("pointerdown", e => {
                    if (e.button !== 0 || step.disabled) return;
                    e.preventDefault();
                    const has = c.notes.find(x => x[0] === row.m && Math.abs(x[1] - at) < 1e-3);
                    if (e.shiftKey && has) {
                        Daw.edit("Accent a step", () => { has[3] = has[3] >= 0.95 ? 0.75 : 1; }, "notes");
                        return drawSteps();
                    }
                    painting = !has;
                    Daw.checkpoint(painting ? "Add steps" : "Clear steps");
                    paint(c, row.m, at, painting, step, e.shiftKey);
                    if (painting) { Daw.noteOn("step", [row.m], 0.85, t.id, true); setTimeout(() => Daw.noteOff("step"), 100); }
                });
                step.addEventListener("pointerenter", () => { if (painting !== null && !step.disabled) paint(c, row.m, at, painting, step, false); });
                rows.push(step);
            }
        });
        UI.seq.replaceChildren(...rows);
    }

    function paint(c, m, at, on, node, accent) {
        const i = c.notes.findIndex(x => x[0] === m && Math.abs(x[1] - at) < 1e-3);
        if (on && i < 0) c.notes.push([m, +at.toFixed(4), 0.25, accent ? 1 : 0.8]);
        if (!on && i >= 0) c.notes.splice(i, 1);
        node.classList.toggle("on", on);
        node.setAttribute("aria-pressed", String(on));
        Daw.changed("steps");
    }

    /* A one-bar beat at the cursor's bar - on a new drum track if there's none. */
    function startBeat(t) {
        const P = Daw.P;
        Daw.edit("Start a beat", () => {
            if (!t) {
                t = Daw.newTrack("drums", "tight", "Drums", 2);
                P.tracks.push(t);
            }
            const at = Math.floor(P.cursor / 4) * 4;
            const c = {id: Daw.uid(), name: "Beat", start: at, len: 4, notes: [[36, 0, 0.25, 0.95], [36, 2, 0.25, 0.85], [38, 1, 0.25, 0.85], [38, 3, 0.25, 0.85]]};
            t.clips = t.clips.filter(x => x.start + x.len <= at || x.start >= at + 4);
            t.clips.push(c);
            t.clips.sort((a, b) => a.start - b.start);
            P.sel.track = t.id;
            P.sel.clip = c.id;
        }, "clips");
    }

    // ------------------------------------------------------------ view --

    function refresh(what) {
        if (!UI) return;
        if (what === "values" || what === "mixer" || what === "cursor" || what === "loop" || what === "picture" || what === "tempo" || what === "audio") return;
        if (what === "steps") return;
        if (what === "keys") { UI.chords.sync(); return UI.keyboard.draw(); }
        if (what === "pads") return drawPads();
        drawTop();
        drawPads();
        drawMacros();
        drawSteps();
        UI.keyboard.draw();
        UI.chords.sync();
    }

    function frame(pos) {
        if (!UI) return;
        UI.keyboard.light(Daw.sounding());
        const t = drumTrack(), c = drumClip(t);
        let now = -1;
        if (c && pos !== null && Daw.playing() && pos >= c.start && pos < c.start + c.len) {
            const into = pos - c.start;
            if (Math.floor(into / 4) === page) now = Math.floor((into - page * 4) * 4);
        }
        if (now !== UI.now) {
            UI.now = now;
            for (const s of UI.seq.querySelectorAll(".step")) s.classList.toggle("now", Number(s.dataset.s) === now);
        }
    }

    function shown() { if (UI) refresh("all"); }

    /* The number keys play the pads. */
    function keydown(e) {
        const i = "12345678".indexOf(e.key);
        if (i < 0 || e.ctrlKey || e.metaKey || e.altKey) return false;
        if (!e.repeat) padOn(i);
        return true;
    }
    function keyup(e) {
        const i = "12345678".indexOf(e.key);
        if (i < 0) return false;
        padOff(i);
        return true;
    }

    return {id: "perform", label: "Perform", build, refresh, frame, shown, keydown, keyup};
})();
