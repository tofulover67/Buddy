/*
 * The pieces more than one layout shows, built the same way wherever they
 * are: a track's knobs, its sound picker, a mixer strip, the keyboard, the
 * progression writer. Each is bound to the engine (engine.js): what it
 * changes goes through Daw.checkpoint() and Daw.changed(), so it can be
 * undone and every layout sees it.
 */
"use strict";

const Parts = (() => {
    const {el} = Buddy;
    const color = t => Daw.COLORS[t.color % Daw.COLORS.length];
    /* node.replaceChildren(), leaving out what isn't there (null, false). */
    const fill = (node, ...kids) => node.replaceChildren(...kids.filter(k => k !== null && k !== undefined && k !== false));

    /* A sound's name and what it's like. */
    function soundInfo(t) {
        const k = t.kind === "drums" ? Drums.KITS[t.sound] : Instruments.KINDS[t.sound];
        return k ? {label: k.label, about: k.about} : {label: t.sound, about: ""};
    }

    /* A track's knobs, in their groups: [{group, node}]. */
    function knobGroups(t, size = 44) {
        const v = Daw.values(t), d = Daw.defaults(t);
        return Daw.knobsOf(t).map(g => {
            const row = el("div.knob-row");
            for (const spec of g.knobs) {
                const k = Ctl.knob(Object.assign({}, spec, {size, value: v[spec.key], def: d[spec.key], label: spec.label,
                    onStart: () => Daw.checkpoint("Turn a knob"),
                    onInput: x => { t.values[spec.key] = x; Daw.changed("values"); }}));
                row.append(k.node);
            }
            return {group: g.group, node: row};
        });
    }

    /* The sounds a track can be: instruments for an instrument track, kits for a drum track. */
    function soundSelect(t) {
        const names = t.kind === "drums" ? Daw.KITS : Daw.INSTRUMENTS;
        const s = el("select.field", {"aria-label": "Sound"}, names.map(n => el("option", {value: n,
            text: (t.kind === "drums" ? Drums.KITS[n] : Instruments.KINDS[n]).label, selected: n === t.sound})));
        s.addEventListener("change", () => setSound(t, s.value));
        return s;
    }

    function setSound(t, name) {
        Daw.allOff();
        Daw.edit("Change the sound", () => {
            t.sound = name;
            t.values = {};
            t.macros = [0.5, 0.5, 0.5, 0.5];
        }, "tracks");
    }

    // ------------------------------------------------------------ mixer --

    function msButtons(t) {
        const mute = el("button.ms-m", {type: "button", text: "M", title: "Mute", "aria-pressed": String(!!t.mute),
            onclick: () => Daw.edit(t.mute ? "Unmute" : "Mute", () => { t.mute = !t.mute; }, "mixer")});
        const solo = el("button.ms-s", {type: "button", text: "S", title: "Solo", "aria-pressed": String(!!t.solo),
            onclick: () => Daw.edit(t.solo ? "Unsolo" : "Solo", () => { t.solo = !t.solo; }, "mixer")});
        return el("div.ms", {translate: "no"}, [mute, solo]);
    }

    const dbText = level => { const db = Daw.faderDb(level); return db === -Infinity ? "−∞" : `${db > 0.05 ? "+" : db < -0.05 ? "−" : ""}${Math.abs(db).toFixed(1)} dB`; };

    /* A track's strip: name, pan, fader and meter, level, mute and solo. */
    function strip(t) {
        const db = el("span.db", {translate: "no", text: dbText(t.level)});
        const pan = Ctl.knob({label: "Pan", min: -1, max: 1, value: t.pan, def: 0, unit: "pan", bipolar: true, size: 34,
            onStart: () => Daw.checkpoint("Pan"), onInput: x => { t.pan = x; Daw.changed("mixer"); }});
        const f = Ctl.fader({value: t.level, def: 0.8, height: 112, label: `${Buddy.t("Level")}: ${t.name}`,
            onStart: () => Daw.checkpoint("Change the level"),
            onInput: x => { t.level = x; db.textContent = dbText(x); Daw.changed("mixer"); }});
        const m = Ctl.meter(112);
        const node = el("div.strip", {dataset: {track: t.id}, onclick: e => {
            if (!e.target.closest("button, .knob, .fader")) Daw.select(t.id);
        }}, [
            el("div.strip-name", {text: t.name, translate: "no", title: t.name}),
            pan.node, el("div.strip-fm", {}, [f.node, m.node]), db, msButtons(t)]);
        node.style.setProperty("--track", color(t));
        node.classList.toggle("sel", Daw.P.sel.track === t.id);
        node.classList.toggle("quiet", !Daw.audible(t));
        return {node, meter: m, id: t.id};
    }

    function masterStrip() {
        const P = Daw.P;
        const db = el("span.db", {translate: "no", text: dbText(P.master ?? 0.8)});
        const f = Ctl.fader({value: P.master ?? 0.8, def: 0.8, height: 112, label: "Master level",
            onStart: () => Daw.checkpoint("Change the master level"),
            onInput: x => { P.master = x; db.textContent = dbText(x); Daw.changed("mixer"); }});
        const m = Ctl.meter(112);
        return {node: el("div.strip.master", {}, [el("div.strip-name", {text: "Master"}), el("div.strip-gap"),
            el("div.strip-fm", {}, [f.node, m.node]), db]), meter: m, id: null};
    }

    /* The song's strip: its level and mute - heard along, never placed. */
    function songStrip() {
        const P = Daw.P;
        const db = el("span.db", {translate: "no", text: dbText(P.song_level ?? 0.7)});
        const f = Ctl.fader({value: P.song_level ?? 0.7, def: 0.7, height: 112, label: "Song level",
            onStart: () => Daw.checkpoint("Change the song's level"),
            onInput: x => { P.song_level = x; db.textContent = dbText(x); Daw.changed("mixer"); }});
        const mute = el("button.ms-m", {type: "button", text: "M", title: "Mute the song", "aria-pressed": String(!!P.song_mute),
            onclick: () => Daw.edit("Mute the song", () => { P.song_mute = !P.song_mute; }, "mixer")});
        const node = el("div.strip.song", {title: "The song plays along – it's never part of what's placed"}, [
            el("div.strip-name", {text: "Song"}), el("div.strip-gap"), el("div.strip-fm", {}, [f.node]), db,
            el("div.ms", {translate: "no"}, [mute])]);
        return {node, meter: null, id: "song"};
    }

    // ------------------------------------------------------------ keyboard --

    /* The keyboard, playing the selected track (its drums' names on the keys of a drum track). */
    function keyboard(octaves = 3) {
        const kb = Ctl.keyboard({
            octaves,
            base: () => 12 * (Daw.P.octave + 1),
            drums: () => {
                const t = Daw.selTrack();
                if (!t || t.kind !== "drums") return null;
                return m => { const r = Drums.keyRow(m, 12 * (Daw.P.octave + 1)); return r < 0 ? "" : Buddy.t(Drums.ROWS[r].name); };
            },
            onDown: (source, midi, vel) => Daw.noteOn(source, [midi], vel),
            onUp: source => Daw.noteOff(source),
        });
        // Lit: a key that's sounding - on a drum track, the key that plays the row sounding.
        const light = sounding => {
            const s = Daw.selTrack();
            if (s && s.kind === "drums") {
                const base = 12 * (Daw.P.octave + 1), lit = new Set();
                for (let m = base - 12; m <= base + 36; m++) {
                    const r = Drums.keyRow(m, base);
                    if (r >= 0 && sounding.has(Drums.ROWS[r].m)) lit.add(m);
                }
                return kb.light(lit);
            }
            kb.light(sounding);
        };
        return {node: kb.node, draw: kb.draw, light};
    }

    /* What one key plays (chords.js): the chord or interval, its voicing and inversion. One
       setting, shown on each keyboard (Console's, Perform's) and kept with the project; on a
       drum track the keys play its drums instead. */
    function chordControl() {
        const set = (key, value) => {
            Daw.allOff();
            Daw.P.key_chord[key] = value;
            Daw.changed("keys");
        };
        const type = el("select.field.chord-type", {"aria-label": "Chords from one key", title: "What one key plays: a chord, or an interval"});
        const voicing = el("select.field", {"aria-label": "Voicing", title: "How the chord's notes are spread"},
            Chords.VOICINGS.map(([v, text]) => el("option", {value: v, text})));
        const inversion = el("select.field", {"aria-label": "Inversion", title: "Which of the chord's notes is at the bottom"},
            Chords.INVERSIONS.map(([v, text]) => el("option", {value: String(v), text})));
        type.addEventListener("change", () => set("type", type.value));
        voicing.addEventListener("change", () => set("voicing", voicing.value));
        inversion.addEventListener("change", () => set("inversion", Number(inversion.value)));
        const node = el("div.chord-ctl", {}, [el("span.daw-label", {text: "Chords"}), type, voicing, inversion]);
        function sync() {
            // The groups' headings are drawn by Buddy's list from the label: put in the language here.
            type.replaceChildren(...Chords.GROUPS.map(g => (g.group === "Off"
                ? el("option", {value: "off", text: g.items[0][1]})
                : el("optgroup", {label: Buddy.t(g.group)}, g.items.map(([v, text]) => el("option", {value: v, text}))))));
            const kc = Daw.P.key_chord, t = Daw.selTrack(), drums = !!(t && t.kind === "drums");
            type.value = kc.type;
            voicing.value = kc.voicing;
            inversion.value = String(kc.inversion);
            type.disabled = drums;
            voicing.disabled = inversion.disabled = drums || kc.type === "off";
            node.title = drums ? Buddy.t("On a drum track the keys play its drums") : "";
        }
        sync();
        return {node, sync};
    }

    // ------------------------------------------------------------ writing --

    const PROGRESSIONS = {
        major: [["I–V–vi–IV", [0, 4, 5, 3]], ["vi–IV–I–V", [5, 3, 0, 4]], ["I–vi–IV–V", [0, 5, 3, 4]], ["ii–V–I–I", [1, 4, 0, 0]]],
        minor: [["i–VI–III–VII", [0, 5, 2, 6]], ["i–iv–v–i", [0, 3, 4, 0]], ["i–VII–VI–VII", [0, 6, 5, 6]], ["i–iv–VI–v", [0, 3, 5, 4]]],
    };

    /* A clip on the track at the cursor's bar, `bars` long, written by fn(notes, bars). */
    function writeClip(t, label, bars, fn) {
        const start = Math.floor(Daw.P.cursor / 4) * 4;
        Daw.edit(label, () => {
            // Whatever it would overlap gives way.
            t.clips = t.clips.filter(c => c.start + c.len <= start || c.start >= start + bars * 4);
            const clip = {id: Daw.uid(), name: label, start, len: bars * 4, notes: []};
            fn(clip.notes, bars);
            t.clips.push(clip);
            t.clips.sort((a, b) => a.start - b.start);
            Daw.P.sel.track = t.id;
            Daw.P.sel.clip = clip.id;
        }, "clips");
    }

    /* Chips that write a progression in the key into the track - chords for a pad, a root
       note a beat for a bass, an arpeggio for anything plucked. */
    function progressions(t) {
        const box = el("div.prog");
        const k = Daw.key();
        box.append(el("span.muted.small", {text: "Write a progression"}),
            ...PROGRESSIONS[k.mode].map(([name, degrees]) => el("button.chip", {type: "button", text: name, translate: "no",
                title: "Four bars of chords in the key, from the cursor's bar",
                onclick: () => writeClip(t, name, 4, notes => {
                    const chords = Daw.chordsInKey(Daw.P.voicing);
                    degrees.forEach((d, bar) => {
                        const ch = chords[d].notes, at = bar * 4;
                        if (t.sound === "bass" || t.sound === "sub") {
                            for (let b = 0; b < 4; b++) notes.push([ch[0] - 12, at + b, 0.9, b === 0 ? 0.9 : 0.7]);
                        } else if (t.sound === "pluck" || t.sound === "bell" || t.sound === "lead") {
                            const seq = [ch[0], ch[1], ch[2], ch[1] + 12 > ch[2] ? ch[1] + 12 : ch[2] + 5].map(m => m + 12);
                            for (let s = 0; s < 8; s++) notes.push([seq[s % seq.length], at + s / 2, 0.45, s % 2 ? 0.65 : 0.85]);
                        } else {
                            for (const m of ch) notes.push([m, at, 4, 0.75]);
                        }
                    });
                })})));
        return box;
    }

    /* "Write a progression": a menu of progressions in the key, written into the track. */
    function progressionMenu(t, anchor) {
        const r = anchor.getBoundingClientRect(), box = progressions(t);
        Buddy.menu({x: r.left, y: r.bottom + 4, items: [{heading: "Four bars of chords in the key, from the cursor's bar"},
            ...[...box.querySelectorAll(".chip")].map(chip => ({label: chip.textContent, raw: true, onClick: () => chip.click()}))]});
    }

    return {color, fill, soundInfo, knobGroups, soundSelect, setSound, strip, masterStrip, songStrip, keyboard, chordControl,
            progressions, progressionMenu, writeClip, dbText};
})();
