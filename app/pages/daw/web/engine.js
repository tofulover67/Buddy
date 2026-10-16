/*
 * The DAW's engine: the one project every layout shows and edits, its sound
 * and its transport. The layouts (arrange.js, console.js, perform.js) only
 * draw it and ask for changes - so switching between them changes nothing
 * here, and what's playing keeps playing.
 *
 *   The project   Daw.P - see page.py / project.py for its shape. Changed
 *                 through Daw.edit(label, fn) (Undo and Redo, kept for next
 *                 time) or, for a drag, Daw.checkpoint() once and then
 *                 Daw.changed() as it moves
 *   The key       the song's (when following it and it's been heard) or the
 *                 project's own: Daw.key(), Daw.bpm(), Daw.inScale(midi),
 *                 Daw.chordsInKey()
 *   Sound         each track: its instrument (instruments.js) or kit
 *                 (drums.js) into its chain (drive, room, echo, width), its
 *                 pan, fader and mute, a meter, into the master. The same
 *                 graph is built offline to render - what's placed is what
 *                 played
 *   Transport     play, record (a count-in, notes into the selected track's
 *                 clip under the playhead, or a new one), the loop, the
 *                 metronome, swing. One timer looks a moment ahead and
 *                 schedules on the audio clock what falls in it
 *   The song      heard by page.py: its key, tempo and beat grid, and its
 *                 audio played along on the same clock, beat 0 on its first bar
 *
 * Events (Daw.on): "change" {what}, "transport", "song", "notes" (what's
 * sounding live, to light the keys), "select".
 */
"use strict";

const Daw = (() => {
    const RATE = 48000;
    const NAMES = ["C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];
    const SCALES = {major: [0, 2, 4, 5, 7, 9, 11], minor: [0, 2, 3, 5, 7, 8, 10]};
    const COLORS = ["#4C9BE8", "#9B6CE8", "#3FAE4B", "#E8B04C", "#E8664C", "#3FBFB8", "#E05C9E", "#A3B547", "#8D9099", "#C98B5A"];
    const INSTRUMENTS = Object.keys(Instruments.KINDS);
    const KITS = Object.keys(Drums.KITS);
    const AHEAD = 0.12;

    /* The knobs, in groups: what each instrument and each kit can be set to. */
    const LOG = {log: true};
    const INST_KNOBS = [
        {group: "Tone", knobs: [
            {key: "tone", label: "Tone", min: 0, max: 100, unit: "%", hint: "Darker or brighter"},
            {key: "drive", label: "Drive", min: 0, max: 100, unit: "%", hint: "Warmth, then grit"},
            {key: "octave", label: "Octave", min: -2, max: 2, step: 1, unit: "oct", bipolar: true}]},
        {group: "Envelope", knobs: [
            Object.assign({key: "attack", label: "Attack", min: 0.002, max: 2, unit: "s", hint: "How long each note takes to come in"}, LOG),
            Object.assign({key: "release", label: "Release", min: 0.02, max: 4, unit: "s", hint: "How long each note rings after it ends"}, LOG)]},
        {group: "Ambience", knobs: [
            {key: "room", label: "Room", min: 0, max: 100, unit: "%"},
            {key: "echo", label: "Echo", min: 0, max: 100, unit: "%", hint: "Repeats, three sixteenths apart"},
            {key: "width", label: "Width", min: 0, max: 100, unit: "%"}]},
    ];
    const DRUM_KNOBS = [
        {group: "Kit", knobs: [
            {key: "tune", label: "Tune", min: -12, max: 12, step: 1, unit: "st", bipolar: true},
            {key: "decay", label: "Decay", min: 25, max: 200, unit: "%", hint: "How long each hit rings"},
            {key: "tone", label: "Tone", min: 0, max: 100, unit: "%"}]},
        {group: "Ambience", knobs: [
            {key: "drive", label: "Drive", min: 0, max: 100, unit: "%"},
            {key: "room", label: "Room", min: 0, max: 100, unit: "%"},
            {key: "echo", label: "Echo", min: 0, max: 100, unit: "%"},
            {key: "width", label: "Width", min: 0, max: 100, unit: "%"}]},
    ];
    const knobsOf = track => (track.kind === "drums" ? DRUM_KNOBS : INST_KNOBS);
    const MACROS = [
        {label: "Brightness", hint: "Opens the tone and adds drive"},
        {label: "Ambience", hint: "Adds room and echo together"},
        {label: "Motion", hint: "Widens it and lets notes ring on"},
        {label: "Punch", hint: "A quicker attack and more drive"},
    ];

    const uid = () => Math.random().toString(36).slice(2, 10);
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    const mod = (a, n) => ((a % n) + n) % n;

    // ------------------------------------------------------------ events --

    const handlers = {};
    const on = (name, fn) => { (handlers[name] = handlers[name] || []).push(fn); };
    function emit(name, data) {
        for (const fn of handlers[name] || []) {
            try { fn(data); } catch (err) { console.error(`DAW "${name}" handler failed:`, err); }
        }
    }

    // ------------------------------------------------------------ project --

    function newTrack(kind, sound, name, color) {
        return {id: uid(), name, kind, color, sound, values: {}, level: 0.8, pan: 0, mute: false, solo: false,
                macros: [0.5, 0.5, 0.5, 0.5], clips: []};
    }

    /* A new project: four tracks, and a beat to start from. */
    function fresh() {
        const drums = newTrack("drums", "tight", "Drums", 2);
        const beat = [];
        for (let s = 0; s < 16; s++) {
            if (s % 4 === 0) beat.push([36, s / 4, 0.25, s % 8 === 0 ? 0.95 : 0.8]);
            if (s % 8 === 4) beat.push([38, s / 4, 0.25, 0.85]);
            if (s % 2 === 0) beat.push([42, s / 4, 0.25, s % 4 === 0 ? 0.7 : 0.5]);
        }
        drums.clips.push({id: uid(), name: "Beat", start: 0, len: 4, notes: beat});
        return {
            bpm: 120, tonic: 9, mode: "minor", follow: true, swing: 0, snap: 4, length: 16, cursor: 0,
            loop: {on: true, start: 0, end: 16}, metronome: false, count_in: true, octave: 4, velocity: 0.8,
            voicing: "triad", view: "arrange", song: "", master: 0.8, song_level: 0.7, song_mute: false, follow_resolve: true,
            key_chord: {type: "off", voicing: "close", inversion: 0},
            sel: {track: "", clip: ""},
            tracks: [newTrack("inst", "pad", "Chords", 0), newTrack("inst", "bass", "Bass", 1), drums,
                     newTrack("inst", "lead", "Lead", 3)],
        };
    }

    let P = fresh();
    P.sel.track = P.tracks[0].id;
    let restored = false;
    const undo = [], redo = [];

    const track = id => P.tracks.find(t => t.id === id) || null;
    const selTrack = () => track(P.sel.track) || P.tracks[0] || null;
    const clipOf = (t, id) => (t ? t.clips.find(c => c.id === id) || null : null);
    const selClip = () => clipOf(selTrack(), P.sel.clip);

    /* A track's settings: its sound's defaults, then what's been set. */
    function values(t) {
        if (t.kind === "drums") return Object.assign({}, Drums.KITS[t.sound].defaults, t.values);
        return Object.assign({drive: 0, echo: 0, octave: 0}, Instruments.values(t.sound), t.values);
    }
    function defaults(t) {
        return t.kind === "drums" ? Object.assign({}, Drums.KITS[t.sound].defaults)
            : Object.assign({drive: 0, echo: 0, octave: 0}, Instruments.values(t.sound));
    }

    let saveTimer = 0;
    function save() {
        if (!restored) return;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => Buddy.send("project", P), 900);
    }

    /* A change from a layout: kept for Undo, drawn everywhere, saved. */
    function edit(label, fn, what = "project") {
        checkpoint(label);
        fn();
        changed(what);
    }
    function checkpoint(label) {
        undo.push({label, state: JSON.stringify(P)});
        if (undo.length > 80) undo.shift();
        redo.length = 0;
    }
    function changed(what = "project") {
        syncGraph();
        emit("change", {what});
        save();
    }
    function step(from, to) {
        if (!from.length) return;
        const was = from.pop();
        to.push({label: was.label, state: JSON.stringify(P)});
        const view = P.view;
        P = JSON.parse(was.state);
        P.view = view;
        changed("all");
        Buddy.toast(`${from === undo ? "Undone" : "Redone"}: ${was.label}`);
    }

    function load(given) {
        restored = true;
        if (!given || !Array.isArray(given.tracks)) return;
        const base = fresh();
        P = Object.assign(base, given, {loop: Object.assign(base.loop, given.loop), sel: Object.assign(base.sel, given.sel),
                                        key_chord: Object.assign(base.key_chord, given.key_chord)});
        for (const t of P.tracks) {
            if (t.kind === "drums" && !Drums.KITS[t.sound]) t.sound = KITS[0];
            if (t.kind !== "drums" && !Instruments.KINDS[t.sound]) t.sound = INSTRUMENTS[0];
        }
        undo.length = redo.length = 0;
        changed("all");
    }

    function select(trackId, clipId = null) {
        P.sel.track = trackId;
        if (clipId !== null) P.sel.clip = clipId;
        else if (!clipOf(track(trackId), P.sel.clip)) P.sel.clip = "";
        emit("select", {});
        save();
    }

    // ------------------------------------------------------------ the key --

    let SONG = null;            // what page.py heard, or null
    const songKey = () => (P.follow && SONG && SONG.key && Number.isFinite(SONG.key.tonic) ? SONG.key : null);
    function key() {
        const k = songKey();
        return k ? {tonic: k.tonic, mode: k.mode === "minor" ? "minor" : "major"} : {tonic: P.tonic, mode: P.mode};
    }
    // The song's tempo: its grid's (song.py - to the thousandth, the steady beat through
    // all of its beats), else the tempo heard.
    const songBpm = () => (SONG ? (SONG.grid && SONG.grid.bpm) || SONG.bpm || null : null);
    const bpm = () => (P.follow && songBpm() ? songBpm() : P.bpm);
    const beatS = () => 60 / bpm();
    const keyName = () => `${NAMES[key().tonic]} ${key().mode}`;
    const inScale = midi => SCALES[key().mode].includes(mod(midi - key().tonic, 12));
    const isRoot = midi => mod(midi - key().tonic, 12) === 0;
    const noteName = midi => `${NAMES[mod(midi, 12)]}${Math.floor(midi / 12) - 1}`;

    /* The scale's notes from `midi` up, `steps` scale steps each. */
    function stack(midi, steps) {
        const k = key(), scale = SCALES[k.mode];
        const rel = mod(midi - k.tonic, 12), degree = scale.indexOf(rel);
        if (degree < 0) return [midi];
        return steps.map(s => { const d = degree + s; return midi - rel + scale[d % 7] + 12 * Math.floor(d / 7); });
    }

    /* The chords in the key, a pad each: the seven degrees and the first an octave up,
       each {name, numeral, notes} - voiced as triads, sevenths or spread wide. */
    function chordsInKey(voicing = P.voicing) {
        const k = key(), scale = SCALES[k.mode];
        const base = 48 + k.tonic - (k.tonic > 6 ? 12 : 0);
        const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII"];
        const out = [];
        for (let d = 0; d < 8; d++) {
            const root = base + scale[d % 7] + 12 * Math.floor(d / 7);
            const triad = stack(root, [0, 2, 4]);
            const third = triad[1] - triad[0], fifth = triad[2] - triad[0];
            const quality = third === 4 ? "" : fifth === 6 ? "°" : "m";
            const numeral = (third === 4 ? ROMAN[d % 7] : ROMAN[d % 7].toLowerCase()) + (fifth === 6 ? "°" : "");
            let notes = voicing === "seventh" ? stack(root, [0, 2, 4, 6]) : triad;
            if (voicing === "spread") notes = [root - 12, triad[0], triad[2], triad[1] + 12];
            out.push({name: NAMES[mod(root, 12)] + quality + (voicing === "seventh" ? "7" : ""), numeral: d === 7 ? `${numeral} · 8va` : numeral, notes});
        }
        return out;
    }

    // ------------------------------------------------------------ sound --

    let ctx = null, master = null;
    const nodes = new Map();           // track id -> its live graph
    const anySolo = () => P.tracks.some(t => t.solo);
    const audible = t => !t.mute && (!anySolo() || t.solo);
    const faderGain = level => Math.pow(level / 0.8, 2);
    const faderDb = level => (level <= 0.001 ? -Infinity : 20 * Math.log10(faderGain(level)));

    function chainParams(t) {
        const v = values(t);
        return {room: v.room, width: v.width, drive: v.drive, echo: v.echo, echoTime: beatS() * 0.75, level: 0};
    }

    /* A track's graph into `dest`: its chain, pan, fader, mute - and on the live
       context an analyser for its meter and the scope. */
    function trackGraph(c, t, dest, live, forceOn = false) {
        const pan = c.createStereoPanner();
        pan.pan.value = t.pan;
        const fader = c.createGain();
        fader.gain.value = faderGain(t.level);
        const mute = c.createGain();
        mute.gain.value = forceOn || audible(t) ? 1 : 0;
        const p = chainParams(t);
        const input = Instruments.chain(c, p, pan);
        pan.connect(fader).connect(mute);
        let analyser = null;
        if (live) {
            analyser = c.createAnalyser();
            analyser.fftSize = 2048;
            mute.connect(analyser);
        }
        mute.connect(dest);
        return {input, pan, fader, mute, analyser, sig: JSON.stringify(p)};
    }

    function masterGraph(c, dest, live) {
        const input = c.createGain();
        input.gain.value = faderGain(P.master ?? 0.8);
        const limit = c.createDynamicsCompressor();
        limit.threshold.value = -3;
        limit.knee.value = 0;
        limit.ratio.value = 20;
        limit.attack.value = 0.002;
        limit.release.value = 0.12;
        input.connect(limit).connect(dest);
        let analyser = null;
        if (live) {
            analyser = c.createAnalyser();
            analyser.fftSize = 2048;
            limit.connect(analyser);
        }
        return {input, analyser};
    }

    function audio() {
        if (!ctx) {
            ctx = new AudioContext({sampleRate: RATE, latencyHint: "interactive"});
            master = masterGraph(ctx, ctx.destination, true);
            syncGraph();
        }
        if (ctx.state === "suspended") ctx.resume();
        return ctx;
    }

    /* The live graph to the project: a track added or gone, its chain built again when its
       space settings changed, its pan, fader and mute set. */
    function syncGraph() {
        if (!ctx) return;
        const now = ctx.currentTime;
        master.input.gain.setTargetAtTime(faderGain(P.master ?? 0.8), now, 0.02);
        for (const [id, n] of nodes) {
            if (!track(id)) {
                n.mute.gain.setTargetAtTime(0, now, 0.02);
                setTimeout(() => { n.mute.disconnect(); n.input.disconnect(); }, 200);
                nodes.delete(id);
            }
        }
        for (const t of P.tracks) {
            let n = nodes.get(t.id);
            if (!n) {
                nodes.set(t.id, trackGraph(ctx, t, master.input, true));
                continue;
            }
            const p = chainParams(t), sig = JSON.stringify(p);
            if (sig !== n.sig) {
                const old = n.input;
                n.input = Instruments.chain(ctx, p, n.pan);
                n.sig = sig;
                setTimeout(() => old.disconnect(), 60);
            }
            n.pan.pan.setTargetAtTime(t.pan, now, 0.02);
            n.fader.gain.setTargetAtTime(faderGain(t.level), now, 0.02);
            n.mute.gain.setTargetAtTime(audible(t) ? 1 : 0, now, 0.01);
        }
        const songVolume = P.song_mute ? 0 : clamp(faderGain(P.song_level ?? 0.7) * 0.8, 0, 1);
        if (songGain) songGain.gain.setTargetAtTime(songVolume, now, 0.02);
        if (songAudio) {
            songAudio.volume = songVolume;
            songAudio.muted = !!P.song_mute;
        }
    }

    /* A note on a track, at `t` - ending at t + dur, or held (a handle to stop) when dur is null. */
    function sound(c, input, t, midi, vel, at, dur) {
        const v = values(t);
        if (t.kind === "drums") {
            const row = Drums.rowOf(midi);
            if (row >= 0) Drums.hit(c, input, t.sound, row, vel, at, v);
            return null;
        }
        const p = Instruments.values(t.sound, v);
        const voice = Instruments.KINDS[t.sound].voice(c, input, clamp(midi + 12 * Math.round(v.octave || 0), 0, 127), vel, at, p);
        if (dur !== null && dur !== undefined) { voice.stop(at + dur); return null; }
        return voice;
    }

    // Live playing: what's held, by who's holding it (a key, the mouse, a pad).
    const held = new Map();            // source -> {track, notes: [midi], voices: [voice]}

    /* Notes on, held by `source` until noteOff(source). On a drum track a key plays its row
       (rowNote); on an instrument track, the chord the keyboard is set to play from one key
       (chords.js, P.key_chord) - unless `asIs`: a pad, a step or a note in the roll, already
       what's to sound. */
    function noteOn(source, midis, vel = P.velocity, trackId = null, asIs = false) {
        const t = trackId ? track(trackId) : selTrack();
        if (!t) return;
        noteOff(source);
        const c = audio(), n = nodes.get(t.id), at = c.currentTime;
        const notes = asIs ? midis : t.kind === "drums" ? midis.map(rowNote).filter(m => m !== null)
            : [...new Set(midis.flatMap(m => Chords.notes(m, P.key_chord, key())))];
        const voices = notes.map(m => sound(c, n.input, t, m, vel, at, null));
        held.set(source, {track: t.id, notes, voices});
        for (const m of notes) recordStart(t, m, vel, at);
        emit("notes", sounding());
    }

    function noteOff(source) {
        const h = held.get(source);
        if (!h) return;
        held.delete(source);
        const at = ctx ? ctx.currentTime : 0;
        for (const v of h.voices) if (v) v.stop(at);
        for (const m of h.notes) recordEnd(h.track, m, at);
        emit("notes", sounding());
    }

    function allOff() {
        for (const source of [...held.keys()]) noteOff(source);
    }

    /* A key played on a drum track: the row it plays (drums.js keyRow), as that row's note. */
    function rowNote(midi) {
        const row = Drums.keyRow(midi, 12 * (P.octave + 1));
        return row < 0 ? null : Drums.ROWS[row].m;
    }

    function sounding() {
        const out = new Set();
        for (const h of held.values()) for (const m of h.notes) out.add(m);
        return out;
    }

    /* A metronome blip, higher on the bar. */
    function click(at, accent) {
        const c = audio(), o = c.createOscillator(), g = c.createGain();
        o.frequency.value = accent ? 1760 : 1175;
        g.gain.setValueAtTime(0, at);
        g.gain.linearRampToValueAtTime(accent ? 0.3 : 0.18, at + 0.002);
        g.gain.setTargetAtTime(0, at + 0.004, 0.015);
        o.connect(g).connect(c.destination);
        o.start(at);
        o.stop(at + 0.12);
    }

    // ------------------------------------------------------------ transport --
    //
    // T.anchors [{time, beat}]: where the transport was at a time on the audio
    // clock - one at the start and one at each turn of the loop - so where it is
    // now is the last anchor before now plus the beats since.

    let T = null;
    let timer = 0;
    const SWING_GRID = 4;

    const playing = () => !!T;
    const recording = () => !!(T && T.record);

    function swung(beat) {
        if (!P.swing) return beat;
        const q = beat * SWING_GRID, r = Math.round(q);
        return Math.abs(q - r) < 1e-4 && mod(r, 2) === 1 ? beat + P.swing / SWING_GRID : beat;
    }

    function position(at = ctx ? ctx.currentTime : 0) {
        if (!T) return P.cursor;
        if (at < T.t0) return T.from;           // the moment before it starts (a count-in): where it starts
        let a = T.anchors[0];
        for (const x of T.anchors) if (x.time <= at) a = x;
        return a.beat + (at - a.time) / beatS();
    }

    const loopOn = () => P.loop.on && P.loop.end > P.loop.start;

    function start(record = false) {
        stop(false);
        const c = audio();
        const B = beatS();
        let from = P.cursor;
        if (loopOn() && (from < P.loop.start || from >= P.loop.end)) from = P.loop.start;
        const lead = record && P.count_in ? 4 * B : 0.06;
        const t0 = c.currentTime + lead + 0.04;
        T = {record, t0, from, nextBeat: from, nextTime: t0, anchors: [{time: t0, beat: from}], countFrom: c.currentTime + 0.04,
             clicked: c.currentTime, recClip: null, rec: new Map()};
        if (record) {
            checkpoint("Record");
            prepareRecording(from);
        }
        songFollow(t0, from);
        tick();
        timer = setInterval(tick, 25);
        emit("transport", {});
    }

    function stop(toCursor = true) {
        if (!T) return;
        const at = ctx.currentTime;
        for (const [m, r] of [...T.rec]) recordEnd(r.track, m, at);
        const wasRecording = T.record;
        const recClip = T.recClip;
        clearInterval(timer);
        timer = 0;
        T = null;
        allOff();
        songStop();
        if (wasRecording) finishRecording(recClip);
        if (!toCursor) return;
        emit("transport", {});
    }

    function tick() {
        if (!T) return;
        const c = ctx, B = beatS(), until = c.currentTime + AHEAD;
        // The count-in's clicks, before the take starts.
        if (T.record && P.count_in) {
            for (let b = 0; b < 4; b++) {
                const t = T.t0 - (4 - b) * B;
                if (t > T.clicked && t < until) { click(t, b === 0); T.clicked = t; }
            }
        }
        while (T && T.nextTime < until) {
            const loopEnd = loopOn() ? P.loop.end : Infinity;
            const endTime = Math.min(until, T.nextTime + (loopEnd - T.nextBeat) * B);
            const b0 = T.nextBeat, b1 = b0 + (endTime - T.nextTime) / B;
            schedule(b0, b1, T.nextTime);
            T.nextTime = endTime;
            T.nextBeat = b1;
            if (loopOn() && b1 >= loopEnd - 1e-9) {
                T.nextBeat = P.loop.start;
                T.anchors.push({time: endTime, beat: P.loop.start});
                if (T.anchors.length > 8) T.anchors.splice(1, 1);
                songFollow(endTime, P.loop.start);
                if (T.record) loopedRecording(endTime);
            } else if (!loopOn() && b1 >= projectBeats() + 4) {
                stopSoon(endTime);
                break;
            }
        }
    }

    /* Paused: stopped, the playhead left where it got to - the next play goes on from there.
       (In the count-in, nothing's been played yet: it stays where it was.) */
    function pause() {
        if (!T) return;
        const at = ctx.currentTime >= T.t0 ? position() : P.cursor;
        P.cursor = Math.max(0, Math.round(at * 1000) / 1000);
        stop();
        changed("cursor");
    }

    function stopSoon(at) {
        setTimeout(() => { if (T) { stop(); } }, Math.max(0, (at - ctx.currentTime) * 1000));
    }

    /* Everything between beats b0 and b1, b0 sounding at time t0. */
    function schedule(b0, b1, t0) {
        const B = beatS(), timeOf = b => t0 + (swung(b) - b0) * B;
        if (P.metronome) {
            for (let b = Math.ceil(b0 - 1e-9); b < b1; b++) {
                const t = t0 + (b - b0) * B;
                if (t > T.clicked + 1e-4) { click(t, mod(b, 4) === 0); T.clicked = t; }
            }
        }
        for (const t of P.tracks) {
            if (!audible(t)) continue;
            const n = nodes.get(t.id);
            for (const clip of t.clips) {
                if (clip.start >= b1 || clip.start + clip.len <= b0) continue;
                for (const [m, s, d, v] of clip.notes) {
                    const at = clip.start + s;
                    if (at < b0 || at >= b1 || s >= clip.len) continue;
                    if (T.record && T.recClip && T.recClip.id === clip.id && T.fresh && T.fresh.has(clipNoteKey(m, s))) continue;
                    sound(ctx, n.input, t, m, v, Math.max(ctx.currentTime, timeOf(at)), Math.min(d, clip.len - s) * B);
                }
            }
        }
    }

    // ------------------------------------------------------------ recording --
    //
    // Notes played while recording go into the selected track's clip under the
    // playhead - or a new clip there, the loop's length (or 4 bars), grown to
    // what was played. Each pass of the loop adds to it; a note played this
    // pass plays back from the next.

    const clipNoteKey = (m, s) => `${m}@${s.toFixed(4)}`;

    function prepareRecording(from) {
        const t = selTrack();
        if (!t) return;
        let clip = t.clips.find(c => from >= c.start && from < c.start + c.len);
        if (!clip) {
            const start = loopOn() ? P.loop.start : Math.floor(from / 4) * 4;
            const len = loopOn() ? P.loop.end - P.loop.start : 16;
            clip = {id: uid(), name: "Take", start, len, notes: [], grow: !loopOn()};
            t.clips.push(clip);
            t.clips.sort((a, b) => a.start - b.start);
        }
        T.recClip = clip;
        T.recTrack = t.id;
        T.fresh = new Set();
        P.sel.track = t.id;
        P.sel.clip = clip.id;
        emit("change", {what: "clips"});
    }

    /* How long after the audio clock a sound is heard: what's played along to reaches the
       ears this late, so a key pressed with it was pressed this late too. */
    const heardLate = () => (ctx ? (ctx.baseLatency || 0) + (ctx.outputLatency || 0) : 0);

    function recordStart(t, m, vel, at) {
        if (!T || !T.record || !T.recClip || t.id !== T.recTrack) return;
        const heard = at - heardLate();
        let beat = position(heard);
        if (heard < T.t0) {
            if (heard < T.t0 - beatS() / 2) return;   // a note well into the count-in isn't the take's
            beat = T.from;                             // ...one a hair early is its first beat's
        }
        // A hair before the loop comes round (it would snap onto its end): its first beat.
        const g = P.snap ? 1 / P.snap : 0.25;
        if (loopOn() && beat >= P.loop.end - g / 2) beat = P.loop.start;
        T.rec.set(m, {track: t.id, beat, vel, at});
    }

    function recordEnd(trackId, m, at) {
        if (!T || !T.rec.has(m)) return;
        const r = T.rec.get(m);
        T.rec.delete(m);
        const clip = T.recClip;
        if (!clip || trackId !== T.recTrack) return;
        const g = P.snap ? 1 / P.snap : 0;
        let s = r.beat - clip.start;
        if (g) s = Math.round(s / g) * g;
        let d = (at - r.at) / beatS();
        if (track(trackId).kind === "drums") d = g || 0.25;
        if (g) d = Math.max(g, Math.round(d / g) * g);
        d = Math.max(1 / 32, d);
        if (clip.grow && s + d > clip.len) clip.len = Math.ceil((s + d) / 4) * 4;
        if (s < 0 || s >= clip.len) return;
        clip.notes.push([m, +s.toFixed(4), +Math.min(d, clip.len - s).toFixed(4), +r.vel.toFixed(3)]);
        T.fresh.add(clipNoteKey(m, +s.toFixed(4)));
        emit("change", {what: "notes"});
    }

    /* The loop came round: what was played last pass plays from now on; held notes carry on. */
    function loopedRecording(at) {
        for (const [m, r] of [...T.rec]) {
            recordEnd(r.track, m, at);
            T.rec.set(m, {track: r.track, beat: P.loop.start, vel: r.vel, at});
        }
        T.fresh = new Set();
    }

    function finishRecording(clip) {
        if (clip) {
            delete clip.grow;
            const t = P.tracks.find(x => x.clips.includes(clip));
            if (t && !clip.notes.length) t.clips = t.clips.filter(c => c !== clip);
        }
        if (undo.length && undo[undo.length - 1].label === "Record" && undo[undo.length - 1].state === JSON.stringify(P)) undo.pop();
        changed("clips");
    }

    // ------------------------------------------------------------ song --

    // The song is played on the audio clock, as the tracks are: decoded once into a
    // buffer and started at the very moment its beat is due - so it and what's played
    // with it stay together to the sample. Until it's decoded (or if it can't be),
    // an <audio> plays it, near enough.
    let songAudio = null, songBuffer = null, songGain = null, songLoading = "";
    const songTimers = [], songSources = [];

    function setSong(found) {
        SONG = found && !found.error ? found : null;
        if (SONG) {
            if (!songAudio) { songAudio = new Audio(); songAudio.preload = "auto"; }
            if (songAudio.src !== SONG.url) songAudio.src = SONG.url;
            loadSong(SONG.url);
            P.song = SONG.id;
        } else {
            songBuffer = null;
            songLoading = "";
        }
        syncGraph();
        emit("song", {});
        changed("song");
    }

    /* The song's file into a buffer at the DAW's rate. (XHR: fetch() won't read a file:// URL.) */
    function loadSong(url) {
        if (!url || songLoading === url) return;
        songLoading = url;
        songBuffer = null;
        const req = new XMLHttpRequest();
        req.open("GET", url);
        req.responseType = "arraybuffer";
        req.onload = () => {
            if (songLoading !== url || !req.response || !req.response.byteLength) return;
            new OfflineAudioContext(2, 1, RATE).decodeAudioData(req.response)
                .then(buffer => { if (songLoading === url) songBuffer = buffer; })
                .catch(() => {});
        };
        req.send();
    }

    /* Seconds into the song at project beat b: beat 0 is the song's first bar, on its grid. */
    function songTime(b) {
        if (!SONG) return 0;
        const first = SONG.grid ? SONG.grid.first : SONG.bars && SONG.bars.length ? SONG.bars[0] : 0;
        return first + b * 60 / (songBpm() || bpm());
    }
    const songBeats = () => (SONG && SONG.length_s ? Math.max(0, (SONG.length_s - songTime(0)) / (60 / (songBpm() || bpm()))) : 0);

    /* The song from beat `beat`, starting at time `at` on the audio clock - the part
       playing until then (the loop's last pass) stopping at that moment. */
    function songFollow(at, beat) {
        if (!SONG) return;
        if (songBuffer) {
            const c = audio();
            if (!songGain) { songGain = c.createGain(); songGain.connect(c.destination); syncGraph(); }
            const late = Math.max(0, c.currentTime - at), from = songTime(beat) + late;
            if (songAudio) songAudio.pause();             // decoded while it played
            // The pass before ends as this one starts - and stays in the list till then, so
            // stopping in between stops it too.
            for (const s of songSources) { try { s.stop(Math.max(c.currentTime, at)); } catch (err) { /* ended */ } }
            songSources.splice(0, Math.max(0, songSources.length - 1));
            if (from >= songBuffer.duration) return;
            const src = c.createBufferSource();
            src.buffer = songBuffer;
            src.connect(songGain);
            src.start(Math.max(c.currentTime, at), Math.max(0, from));
            songSources.push(src);
            return;
        }
        if (!songAudio || !songAudio.src) return;
        const go = () => {
            if (!T) return;
            const late = Math.max(0, ctx.currentTime - at);
            songAudio.currentTime = Math.max(0, songTime(beat) + late);
            songAudio.play().catch(() => {});
        };
        songTimers.push(setTimeout(go, Math.max(0, (at - ctx.currentTime) * 1000 - 10)));
        if (songTimers.length > 6) songTimers.shift();
    }

    function songStop() {
        if (songAudio) songAudio.pause();
        for (const id of songTimers) clearTimeout(id);
        songTimers.length = 0;
        for (const s of songSources) { try { s.stop(); } catch (err) { /* ended */ } }
        songSources.length = 0;
    }

    // ------------------------------------------------------------ length --

    function projectBeats() {
        let end = P.length * 4;
        for (const t of P.tracks) for (const c of t.clips) end = Math.max(end, c.start + c.len);
        return Math.max(end, Math.ceil(songBeats() / 4) * 4);
    }

    function usedRange(onlyTrack = null) {
        let lo = Infinity, hi = 0;
        for (const t of P.tracks) {
            if (onlyTrack ? t.id !== onlyTrack : !audible(t)) continue;
            for (const c of t.clips) if (c.notes.length) { lo = Math.min(lo, c.start); hi = Math.max(hi, c.start + c.len); }
        }
        return hi > 0 ? {from: Math.floor(lo / 4) * 4, to: Math.ceil(hi / 4) * 4} : null;
    }

    // ------------------------------------------------------------ render --

    /* Beats `from`-`to` rendered offline as they play (one track, or every one heard),
       with the last notes' ring after - a buffer, at most -1 dBFS. `lead` seconds of
       silence first: a clip on the timeline starts on a frame, and the beat it's for
       can fall between two. */
    async function render(from, to, onlyTrack = null, lead = 0) {
        const B = beatS(), tail = 4;
        const seconds = lead + (to - from) * B + tail;
        const off = new OfflineAudioContext(2, Math.ceil(seconds * RATE), RATE);
        const m = masterGraph(off, off.destination, false);
        for (const t of P.tracks) {
            if (onlyTrack ? t.id !== onlyTrack : !audible(t)) continue;
            const n = trackGraph(off, t, m.input, false, true);
            for (const clip of t.clips) {
                for (const [mid, s, d, v] of clip.notes) {
                    const at = clip.start + s;
                    if (at < from || at >= to || s >= clip.len) continue;
                    sound(off, n.input, t, mid, v, lead + (swung(at) - from) * B, Math.min(d, clip.len - s) * B);
                }
            }
        }
        const buffer = await off.startRendering();
        let peak = 0, last = 0;
        for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
            const d = buffer.getChannelData(ch);
            for (let i = 0; i < d.length; i++) {
                const a = Math.abs(d[i]);
                if (a > peak) peak = a;
                if (a > 1e-4) last = Math.max(last, i);
            }
        }
        const most = Math.pow(10, -1 / 20);
        if (peak > most) {
            for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
                const d = buffer.getChannelData(ch);
                for (let i = 0; i < d.length; i++) d[i] *= most / peak;
            }
        }
        // The ring after the last note, not the silence after it.
        const keep = Math.min(buffer.length, Math.max(Math.ceil((lead + (to - from) * B) * RATE), last + Math.round(0.05 * RATE)));
        if (keep >= buffer.length) return buffer;
        const out = new AudioBuffer({numberOfChannels: 2, length: keep, sampleRate: RATE});
        for (let ch = 0; ch < 2; ch++) out.copyToChannel(buffer.getChannelData(ch).subarray(0, keep), ch);
        return out;
    }

    /* The buffer's channels as base64 of their float32 samples, for page.py. */
    function channels(buffer) {
        const out = [];
        for (let c = 0; c < buffer.numberOfChannels; c++) {
            const bytes = new Uint8Array(buffer.getChannelData(c).slice().buffer);
            let bin = "";
            for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
            out.push(btoa(bin));
        }
        return out;
    }

    // ------------------------------------------------------------ meters --

    const meterBuf = new Float32Array(2048);
    /* A track's level now (0-1, the peak of the last moment), or the master's with no id. */
    function level(id = null) {
        const n = id ? nodes.get(id) : master;
        if (!n || !n.analyser || !T && !held.size) return 0;
        n.analyser.getFloatTimeDomainData(meterBuf);
        let peak = 0;
        for (let i = 0; i < meterBuf.length; i++) peak = Math.max(peak, Math.abs(meterBuf[i]));
        return peak;
    }
    const analyser = id => (id ? (nodes.get(id) || {}).analyser : master && master.analyser) || null;

    // ------------------------------------------------------------ macros --

    /* A macro knob (0-1) to the settings it moves. */
    function applyMacro(t, i, x) {
        t.macros[i] = x;
        const v = t.values, d = defaults(t);
        if (t.kind === "drums") {
            if (i === 0) { v.tone = Math.round(10 + x * 85); v.drive = Math.round(Math.max(0, x - 0.5) * 120); }
            if (i === 1) { v.room = Math.round(x * 80); v.echo = Math.round(Math.max(0, x - 0.4) * 60); }
            if (i === 2) { v.width = Math.round(x * 100); v.decay = Math.round(60 + x * 110); }
            if (i === 3) { v.drive = Math.round(x * 70); v.tune = Math.round((x - 0.5) * 6); }
            return;
        }
        if (i === 0) { v.tone = Math.round(8 + x * 88); v.drive = Math.round(Math.max(0, x - 0.55) * 110); }
        if (i === 1) { v.room = Math.round(x * 85); v.echo = Math.round(Math.max(0, x - 0.35) * 70); }
        if (i === 2) { v.width = Math.round(x * 100); v.release = +(d.release * Math.pow(3, (x - 0.5) * 2)).toFixed(3); }
        if (i === 3) { v.attack = +(Math.max(0.002, d.attack * Math.pow(0.05, x))).toFixed(4); v.drive = Math.round(x * 55); }
    }

    // ------------------------------------------------------------ public --

    return {
        RATE, NAMES, SCALES, COLORS, INSTRUMENTS, KITS, INST_KNOBS, DRUM_KNOBS, MACROS, knobsOf,
        get P() { return P; }, get song() { return SONG; },
        on, emit, edit, checkpoint, changed, load, fresh, newTrack, uid, select,
        undo: () => step(undo, redo), redo: () => step(redo, undo),
        undoLabel: () => (undo.length ? undo[undo.length - 1].label : null),
        redoLabel: () => (redo.length ? redo[redo.length - 1].label : null),
        track, selTrack, selClip, clipOf, values, defaults, audible, faderDb,
        key, bpm, songBpm, beatS, keyName, inScale, isRoot, noteName, stack, chordsInKey,
        audio, noteOn, noteOff, allOff, sounding, rowNote,
        start, stop, pause, playing, recording, position, loopOn,
        setSong, songTime, songBeats, songReady: () => !!songBuffer, projectBeats, usedRange, render, channels,
        level, analyser, applyMacro,
    };
})();
