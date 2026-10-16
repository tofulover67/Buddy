/*
 * The Sound Design tab (page.py's sd_*): sounds made for the music on the
 * timeline, in its key and tempo, placed on its moments.
 *
 *   Song      a music clip, picked (Buddy guesses one: the selected clip, else
 *             the longest that looks like music), listened to by song.py -
 *             its key, tempo and moments
 *   Sounds    a riser, an impact or a whoosh (synth.js) on a mini timeline of
 *             its own: its waveform on a beat grid counted from its moment,
 *             a playhead to click or drag - dragging scrubs, playing a moment
 *             of the sound under the pointer - Play from there, Loop; it
 *             stays in view while the settings scroll under it. Presets to
 *             start from. The settings, in groups - its timing, then the
 *             layers it's made of (each with an on/off switch that keeps its
 *             settings), then the finishing - each group with a dot when it's
 *             been changed and its own Reset; a setting's name (or slider)
 *             double-clicked goes back to how it started. Saved: sounds kept
 *             by name (page.py keeps them, with the settings last used) - start
 *             from one, change it, then save it as a new one or place it; a
 *             saved one is never written over. Each change makes
 *             it again (and, while it plays, it carries on from where it was
 *             with the new sound - tweak while it loops). A Variation number
 *             gives the same settings other noise. Place puts it so its
 *             moment lands on Resolve's playhead
 *   Moments   the song's drops, new sections, breaks and last hit, on a strip
 *             of its loudness bar by bar - each with the sounds that belong
 *             on it, placed in one click (with the settings set above)
 *
 * Space plays and stops the sound while this tab shows - and does nothing
 * else here (no list opens, no button is pressed). The timeline,
 * playhead and selection are timeline.js's (TL, HEAD, SEL, CLIPS, FXBUSY -
 * read here, never changed); so are timecode() and mmss(). This file's own
 * names stay in its own scope.
 */
"use strict";

(() => {
    const {el, icon} = Buddy;
    const send = (name, data) => Buddy.send(name, data);     // looked up each time: a test can listen in
    const $ = id => document.getElementById(id);
    const panel = $("panel-sound-design");

    const KINDS = ["riser", "impact", "whoosh"];
    const MOMENT = {drop: "Drop", change: "New section", break: "Break", end: "Last hit"};
    // The sounds that belong on each kind of moment, the likeliest first.
    const FITS = {drop: ["riser", "impact"], change: ["whoosh", "riser"], break: ["whoosh", "impact"], end: ["impact"]};
    const ON_IT = {riser: "Riser into it", impact: "Impact on it", whoosh: "Whoosh on it"};
    const RULER = 18;                 // px: the mini timeline's ruler
    const GRAIN_MS = 30;              // a scrub plays a moment of sound at most this often

    const defaults = kind => Synth.values(kind);
    const groupsOf = kind => [...new Set(Synth.SOUNDS[kind].params.map(p => p.group))];

    const SD = {
        song: null,              // the music clip's id
        picked: false,           // picked by the user - Buddy's guess doesn't move it then
        heard: {},               // clip id -> page.py's sd_song for it
        listening: null,         // the clip being listened to
        error: "",
        kind: "riser",
        values: Object.fromEntries(KINDS.map(k => [k, defaults(k)])),
        seed: Object.fromEntries(KINDS.map(k => [k, 1])),
        // The groups shown open, per sound: the first three to begin with.
        open: Object.fromEntries(KINDS.map(k => [k, new Set(groupsOf(k).slice(0, 3))])),
        made: null,              // {kind, sig, buffer, sync, peaks: {w, lo, hi}}
        making: 0,               // renders asked for; `made` is the newest finished
        busy: 0,                 // renders running
        playing: false,
        loop: false,
        pos: 0,                  // the mini timeline's playhead, seconds into the sound
        from: 0,                 // where the last Play started (back there when it ends)
        placing: false,
        restart: false,          // a preset was picked: the next rendering plays from its start
        saved: [],               // the library: [{id, name, kind, values, seed, made}] (page.py keeps it)
        loaded: Object.fromEntries(KINDS.map(k => [k, null])),   // the saved sound each sound was last started from
        restored: false,         // the settings last used are put back once, when the library first comes
    };
    let UI = null;
    let undo = null;
    let scrub = null;            // {wasPlaying, last} while the mini timeline is dragged

    // ---------------------------------------------------------------- song --

    const musicClips = () => (TL ? TL.tracks.flatMap(t => t.clips.map(c => ({clip: c, track: t})))
        .filter(({clip}) => !clip.transition && clip.media) : []);
    const looksLikeMusic = ({clip, track}) => /music|song|score|bgm|soundtrack|beat/i.test(`${track.name} ${clip.name}`);

    /* Buddy's guess: the selected clip; else the longest that looks like music; else the longest. */
    function guessSong() {
        const all = musicClips();
        if (!all.length) return null;
        const selected = all.find(({clip}) => SEL.has(clip.id));
        if (selected) return selected.clip.id;
        const longest = list => list.reduce((a, b) => (b.clip.end - b.clip.start > a.clip.end - a.clip.start ? b : a));
        const music = all.filter(looksLikeMusic);
        return longest(music.length ? music : all).clip.id;
    }

    function syncSong() {
        const all = musicClips();
        if (SD.song && !all.some(({clip}) => clip.id === SD.song)) { SD.song = null; SD.picked = false; }
        if (!SD.picked) SD.song = guessSong();
    }

    const heard = () => (SD.song && SD.heard[SD.song] && !SD.heard[SD.song].error ? SD.heard[SD.song] : null);

    /* What the synth is told: the heard song's tempo and key, or NO_SONG's. */
    function songFor() {
        const h = heard();
        if (!h) return Object.assign({}, Synth.NO_SONG);
        return {bpm: h.bpm || Synth.NO_SONG.bpm, tonic: h.key ? h.key.tonic : 0, mode: h.key ? h.key.mode : "major"};
    }

    // --------------------------------------------------------------- sounds --

    const sig = kind => JSON.stringify([kind, SD.values[kind], SD.seed[kind], songFor()]);
    const current = () => (SD.made && SD.made.kind === SD.kind ? SD.made : null);

    async function make(kind = SD.kind) {
        const want = sig(kind);
        if (SD.made && SD.made.sig === want) {
            if (SD.restart) arrived(SD.made);          // the same sound: still from the start, as asked
            return SD.made;
        }
        const ticket = ++SD.making;
        SD.busy++;
        drawReadout();
        try {
            const got = await Synth.render(kind, SD.values[kind], songFor(), SD.seed[kind]);
            const made = {kind, sig: want, buffer: got.buffer, sync: got.sync, peaks: null};
            if (ticket === SD.making) arrived(made);
            return made;
        } finally {
            SD.busy--;
            drawReadout();
        }
    }

    /* A new rendering is the one shown - and, while the old one plays, the
       one heard from the same place on. After a preset (SD.restart), from
       its start instead. */
    function arrived(made) {
        const was = SD.made;
        SD.made = made;
        if (!was || was.kind !== made.kind) SD.pos = 0;
        SD.pos = Math.min(SD.pos, made.buffer.duration);
        if (SD.restart && made.kind === SD.kind) {
            SD.restart = false;
            SD.pos = SD.from = 0;
            if (SD.playing) startPlaying(0);
        } else if (SD.playing && made.kind === SD.kind) {
            const at = Synth.position();
            startPlaying(at === null ? SD.pos : Math.min(at, made.buffer.duration - 0.001), true);
        }
        drawWave();
    }

    let remakeTimer = null;
    function remake() {
        clearTimeout(remakeTimer);
        remakeTimer = setTimeout(() => make().catch(err => console.error(err)), 120);
        remember();
    }

    /* The settings in use, kept by page.py for next time - a moment after the last change. */
    let rememberTimer = null;
    function remember() {
        if (!SD.restored) return;                    // not before what was kept has been put back
        clearTimeout(rememberTimer);
        rememberTimer = setTimeout(() => send("sd_last", {kind: SD.kind, values: SD.values, seed: SD.seed}), 800);
    }

    // ---------------------------------------------------------------- saved --

    const newId = () => `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
    const savedOf = kind => SD.saved.filter(s => s.kind === kind);
    /* Whether a saved sound is what's set now (its settings and Variation). */
    const isNow = (kind, item) => JSON.stringify(Synth.values(kind, item.values)) === JSON.stringify(SD.values[kind])
        && item.seed === SD.seed[kind];

    function load(item) {
        stopPlaying();
        SD.kind = item.kind;
        SD.values[item.kind] = Synth.values(item.kind, item.values);
        SD.seed[item.kind] = item.seed;
        SD.loaded[item.kind] = item.id;
        SD.pos = SD.from = 0;
        SD.restart = true;
        renderParams();
        syncSound();
        remake();
    }

    function nameDialog(title, value, ok, onDone) {
        const input = el("input.field", {value: value || "", maxlength: "60", placeholder: "Name"});
        const submit = close => { const v = input.value.trim(); if (v) { close(); onDone(v); } else input.focus(); };
        const dlg = Buddy.modal({title, body: input, buttons: [{label: "Cancel"}, {label: ok, kind: "accent", onClick: submit}]});
        input.addEventListener("keydown", e => { if (e.key === "Enter") submit(dlg.close); });
    }

    /* Saved as a new sound, by name - never over another: started from "Big build", it
       offers "Big build 2". It's then the one started from. */
    function saveNew() {
        const kind = SD.kind, from = savedOf(kind).find(s => s.id === SD.loaded[kind]);
        const base = from ? from.name : Synth.SOUNDS[kind].label;
        let n = from ? 2 : savedOf(kind).length + 1;
        while (SD.saved.some(s => s.name === `${base} ${n}`)) n++;
        nameDialog("Save as a new sound", `${base} ${n}`, "Save", name => {
            const item = {id: newId(), name, kind, values: SD.values[kind], seed: SD.seed[kind], made: new Date().toISOString().slice(0, 16)};
            SD.loaded[kind] = item.id;
            SD.saved = SD.saved.concat([item]);
            send("sd_saved_put", item);
            renderSaved();
        });
    }

    function savedMenu(e, item) {
        e.preventDefault();
        Buddy.menu({x: e.clientX, y: e.clientY, items: [
            {label: "Rename…", onClick: () => nameDialog("Rename the sound", item.name, "Rename", name => {
                const updated = Object.assign({}, item, {name});
                SD.saved = SD.saved.map(s => (s.id === item.id ? updated : s));
                send("sd_saved_put", updated);
                renderSaved();
            })},
            {sep: true},
            {label: "Delete", danger: true, onClick: async () => {
                if (!await Buddy.confirm({title: `Delete "${item.name}"?`, ok: "Delete", danger: true,
                        text: "The saved sound goes. Sounds already placed on a timeline stay as they are."})) return;
                SD.saved = SD.saved.filter(s => s.id !== item.id);
                if (SD.loaded[item.kind] === item.id) SD.loaded[item.kind] = null;
                send("sd_saved_remove", {id: item.id});
                renderSaved();
            }},
        ]});
    }

    /* The Saved row: this sound's saved ones (click to start from one - change it from there,
       then save it as a new one or place it; right-click to rename or delete), and Save. */
    function renderSaved() {
        if (!UI) return;
        const kind = SD.kind, mine = savedOf(kind);
        const chips = mine.map(item => {
            const chip = el("button.chip.sd-preset.sd-saved-chip", {type: "button", text: item.name, translate: "no",
                title: "Click to start from it – right-click to rename or delete", dataset: {saved: item.id},
                "aria-pressed": String(isNow(kind, item)), onclick: () => load(item)});
            chip.addEventListener("contextmenu", e => savedMenu(e, item));
            return chip;
        });
        UI.saved.replaceChildren(el("span.muted.small", {text: "Saved"}),
            ...(chips.length ? chips : [el("span.muted.small.sd-none", {text: "Nothing saved yet – set a sound up and save it to use again."})]),
            el("button.btn.ghost.small.sd-save", {type: "button", title: "Save what's set now as a new sound", onclick: saveNew},
                [icon("plus"), el("span", {text: "Save…"})]));
    }

    function startPlaying(from, keepFrom = false) {
        const made = current();
        if (!made) return;
        if (!keepFrom) SD.from = from;
        SD.playing = true;
        Synth.play(made.buffer, {from, loop: SD.loop, onEnd: () => {
            SD.playing = false;
            SD.pos = SD.from;                // back to where Play started, as a timeline does
            syncSound();
            drawWave();
        }});
        syncSound();
        animate();
    }

    function stopPlaying() {
        if (SD.playing) {
            const at = Synth.position();
            if (at !== null) SD.pos = at;
        }
        Synth.stop();
        SD.playing = false;
        syncSound();
        drawWave();
    }

    async function playOrStop() {
        if (SD.playing) return stopPlaying();
        const made = await make();
        if (made !== current()) return;
        startPlaying(SD.pos >= made.buffer.duration - 0.01 ? 0 : SD.pos);
    }

    let frame = 0;
    function animate() {
        cancelAnimationFrame(frame);
        const step = () => {
            if (!SD.playing) return;
            const at = Synth.position();
            if (at !== null) SD.pos = at;
            drawWave();
            frame = requestAnimationFrame(step);
        };
        frame = requestAnimationFrame(step);
    }

    const choiceLabel = (kind, key) => {
        const p = Synth.SOUNDS[kind].params.find(q => q.key === key);
        const c = p && p.choices.find(([v]) => v === SD.values[kind][key]);
        return c ? c[1] : "";
    };

    /* What the file is called and the undo says: "Riser 2 bars A minor 124 BPM". */
    function labelOf(kind) {
        const h = heard();
        const length = kind === "riser" ? choiceLabel(kind, "bars") : kind === "whoosh" ? choiceLabel(kind, "beats") : "";
        return [Synth.SOUNDS[kind].label, length, h && h.key ? h.key.name : "", h && h.bpm ? `${h.bpm} BPM` : ""]
            .filter(Boolean).join(" ");
    }

    /* The sound made now, its moment on timeline frame `frame`. */
    async function place(kind, at) {
        if (!TL) return Buddy.toast(Buddy.t("Open a timeline in Resolve first."), 3500);
        if (SD.placing) return;
        stopPlaying();
        SD.placing = true;
        syncSound();
        try {
            const made = await make(kind);
            send("sd_place", {kind, label: labelOf(kind), frame: at, sync: made.sync, rate: made.buffer.sampleRate,
                channels: Synth.channels(made.buffer), song: heard() ? SD.song : null,
                recipe: {kind, values: SD.values[kind], song: songFor(), seed: SD.seed[kind], synth: 2}});
        } catch (err) {
            SD.placing = false;
            syncSound();
            Buddy.toast(String(err), 4000);
        }
    }

    // ------------------------------------------------------------ settings --

    /* A log slider runs 0-1000 over min..max, so each octave gets the same room. */
    const LOG_STEPS = 1000;
    const toSlider = (p, v) => (p.log ? Math.round(Math.log(v / p.min) / Math.log(p.max / p.min) * LOG_STEPS) : v);
    const fromSlider = (p, r) => (p.log ? Math.round(p.min * Math.pow(p.max / p.min, r / LOG_STEPS)) : r);

    function shown(p, v) {
        const signed = n => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)}`;
        switch (p.unit) {
            case "dB": return `${signed(v)} dB`;
            case "s": return `${v.toFixed((p.step || 1) < 0.1 ? 2 : 1)} s`;
            case "ms": return `${Math.round(v)} ms`;
            case "Hz": return v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 1 : 2)} kHz`
                : `${(p.step || 1) < 1 ? v.toFixed(1) : Math.round(v)} Hz`;
            case "st": return `${signed(v)} st`;
            case "oct": return signed(v);
            case "ct": return `${v} ct`;
            case "Q": return v.toFixed(1);
            case "%": return `${Math.round(v)}%`;
            default: return String(v);
        }
    }

    /* A setting changed: the sound made again, and the groups and presets say whether it's their own still. */
    function changed() {
        syncGroups();
        remake();
    }

    /* Each control can be set to a value from outside it (a reset): `put` does that. */
    function control(kind, p) {
        const label = el(p.hint ? "span.ctl-label.has-hint" : "span.ctl-label",
            {text: p.label, title: p.hint ? `${Buddy.t(p.hint)}. ${Buddy.t("Double-click to reset")}` : "Double-click to reset"});
        let input, put, readout = null;
        if (p.toggleRow) {
            input = el("input", {type: "checkbox", checked: SD.values[kind][p.key]});
            input.addEventListener("change", () => { SD.values[kind][p.key] = input.checked; changed(); });
            put = v => { input.checked = v; };
        } else if (p.choices) {
            input = el("select.field", {}, p.choices.map(([v, text]) => el("option", {value: String(v), text,
                selected: SD.values[kind][p.key] === v})));
            input.addEventListener("change", () => {
                const choice = p.choices.find(c => String(c[0]) === input.value);
                SD.values[kind][p.key] = choice ? choice[0] : p.value;
                changed();
            });
            put = v => { input.value = String(v); };
        } else {
            const value = el("span.unit.sd-value", {translate: "no"});
            const range = el("input.range", {type: "range", min: p.log ? 0 : p.min, max: p.log ? LOG_STEPS : p.max,
                step: p.log ? 1 : p.step || 1, value: toSlider(p, SD.values[kind][p.key])});
            const show = () => { value.textContent = shown(p, SD.values[kind][p.key]); };
            range.addEventListener("input", () => { SD.values[kind][p.key] = fromSlider(p, Number(range.value)); show(); changed(); });
            put = v => { range.value = toSlider(p, v); show(); };
            show();
            input = range;
            readout = value;
        }
        const reset = () => {
            if (SD.values[kind][p.key] === p.value) return;
            SD.values[kind][p.key] = p.value;
            put(p.value);
            changed();
        };
        label.addEventListener("dblclick", reset);
        if (input.type === "range") input.addEventListener("dblclick", reset);
        const body = p.toggleRow ? [el("label.check", {}, [input, " ", Buddy.t("On")])] : [input, readout];
        const row = el("div.ctl", {dataset: {key: p.key}}, [label, el("div.ctl-body", {}, body)]);
        row.put = put;
        return row;
    }

    const isDefault = (kind, keys) => keys.every(k => SD.values[kind][k] === Synth.SOUNDS[kind].params.find(p => p.key === k).value);

    /* A group: its heading (open/close, its layer's switch, a dot when it's
       been changed, Reset) and its settings. */
    function group(kind, name) {
        const params = Synth.SOUNDS[kind].params.filter(p => p.group === name);
        const sw = params.find(p => p.toggle);
        const rows = params.filter(p => !p.toggle).map(p => control(kind, p));
        const box = el("section.sd-group", {dataset: {group: name}});
        const head = el("div.sd-group-head", {role: "button", tabindex: 0, "aria-expanded": "false"}, [
            el("i.sd-chevron"),
            el("span.group-title", {text: name}),
            el("i.sd-changed", {title: "Changed from how it started"}),
            el("span.spacer"),
        ]);
        if (sw) {
            const on = el("input", {type: "checkbox", checked: SD.values[kind][sw.key]});
            on.addEventListener("change", () => { SD.values[kind][sw.key] = on.checked; box.classList.toggle("off", !on.checked); changed(); });
            box.sw = {param: sw, input: on};
            box.classList.toggle("off", !SD.values[kind][sw.key]);
            head.append(el("label.check.sd-switch", {title: "Turn this layer on or off – its settings are kept"}, [on, " ", Buddy.t("On")]));
        }
        const reset = el("button.btn.ghost.small.sd-reset", {type: "button", text: "Reset", title: "This group back to how it started",
            onclick: () => {
                for (const p of params) SD.values[kind][p.key] = p.value;
                for (const row of rows) row.put(SD.values[kind][row.dataset.key]);
                if (sw) { box.sw.input.checked = sw.value; box.classList.toggle("off", !sw.value); }
                changed();
            }});
        head.append(reset);
        const body = el("div.sd-group-body", {}, rows);
        const open = want => {
            box.classList.toggle("open", want);
            head.setAttribute("aria-expanded", String(want));
            if (want) SD.open[kind].add(name); else SD.open[kind].delete(name);
        };
        head.addEventListener("click", e => { if (!e.target.closest("button, label, input")) open(!box.classList.contains("open")); });
        head.addEventListener("keydown", e => { if (e.key === "Enter" && e.target === head) { e.preventDefault(); open(!box.classList.contains("open")); } });
        open(SD.open[kind].has(name));
        box.keys = params.map(p => p.key);
        box.resetBtn = reset;
        box.append(head, body);
        return box;
    }

    /* The groups' dots and Reset buttons, and which preset (if any) it still is. */
    function syncGroups() {
        if (!UI) return;
        const kind = SD.kind;
        for (const box of UI.params.querySelectorAll(".sd-group")) {
            const same = isDefault(kind, box.keys);
            box.classList.toggle("changed", !same);
            box.resetBtn.disabled = same;
        }
        const now = JSON.stringify(SD.values[kind]);
        for (const chip of UI.presets.querySelectorAll("button[data-preset]")) {
            chip.setAttribute("aria-pressed", String(JSON.stringify(Synth.preset(kind, chip.dataset.preset)) === now));
        }
        renderSaved();
    }

    function renderParams() {
        const kind = SD.kind, params = Synth.SOUNDS[kind].params;
        // Read top to bottom: its timing, then the layers it's made of, then the finishing.
        const firstLayer = (params.find(p => p.toggle && p.group !== "Echo and reverb") || {}).group;
        const parts = [];
        for (const name of groupsOf(kind)) {
            if (name === firstLayer) parts.push(el("h3.sd-section", {text: "Layers"}));
            if (name === "Colour") parts.push(el("h3.sd-section", {text: "Finishing"}));
            parts.push(group(kind, name));
        }
        UI.params.replaceChildren(...parts);
        UI.presets.replaceChildren(el("span.muted.small", {text: "Start from"}),
            ...Synth.SOUNDS[kind].presets.map(pr => el("button.chip.sd-preset", {type: "button", text: pr.name, dataset: {preset: pr.name},
                onclick: () => {
                    // A new sound to start from: its playhead back at the start at once (and, playing,
                    // the new sound heard from there when it's made).
                    SD.values[kind] = Synth.preset(kind, pr.name);
                    SD.loaded[kind] = null;
                    SD.pos = SD.from = 0;
                    SD.restart = true;
                    renderParams();
                    remake();
                }})));
        UI.about.textContent = Buddy.t(Synth.SOUNDS[kind].about);
        UI.seed.value = SD.seed[kind];
        for (const b of UI.kindPick.children) b.setAttribute("aria-pressed", String(b.dataset.kind === kind));
        syncGroups();
        drawWave();
    }

    function setKind(kind) {
        if (SD.kind === kind) return;
        stopPlaying();
        SD.kind = kind;
        SD.pos = 0;
        renderParams();
        syncSound();
        remake();
    }

    // ------------------------------------------------------------------ view --

    function render() {
        const songPick = el("select.field.sd-song", {translate: "no"});
        songPick.addEventListener("change", () => {
            SD.song = songPick.value || null;
            SD.picked = true;
            SD.error = "";
            sync();
            remake();
        });
        const listen = el("button.btn", {type: "button", onclick: () => {
            if (!SD.song) return;
            SD.listening = SD.song;
            SD.error = "";
            delete SD.heard[SD.song];
            send("sd_listen", {id: SD.song});
            sync();
        }});
        const undoBtn = el("button.btn.ghost", {type: "button", hidden: true, onclick: () => send("undo")}, [icon("undo"), el("span")]);
        const kindPick = el("div.segmented", {role: "group", "aria-label": "Sound"},
            KINDS.map(k => el("button", {type: "button", text: Synth.SOUNDS[k].label, dataset: {kind: k}, onclick: () => setKind(k)})));
        const seed = el("input.field.num.sd-seed", {type: "number", min: 1, max: 9999, step: 1, translate: "no",
            title: "The same settings with other noise – each number is its own take"});
        seed.addEventListener("change", () => {
            const n = Math.round(Number(seed.value));
            SD.seed[SD.kind] = Number.isFinite(n) ? Math.min(9999, Math.max(1, n)) : 1;
            seed.value = SD.seed[SD.kind];
            remake();
        });
        const dice = el("button.btn.ghost.icon", {type: "button", title: "Another variation", onclick: () => {
            SD.seed[SD.kind] = 1 + Math.floor(Math.random() * 9999);
            seed.value = SD.seed[SD.kind];
            remake();
        }}, [icon("spark")]);
        const reset = el("button.btn.ghost", {type: "button", text: "Reset", title: "Every setting of this sound back to how it started",
            onclick: () => {
                SD.values[SD.kind] = defaults(SD.kind);
                SD.seed[SD.kind] = 1;
                renderParams();
                remake();
            }});
        UI = {
            songPick, listen, undoBtn, kindPick, seed,
            bar: el("div.fx-bar", {}, el("i")),
            songText: el("p.muted.small.hint"),
            tiles: el("div.tiles.sd-tiles"),
            about: el("p.muted.small.hint"),
            presets: el("div.row.sd-presets"),
            saved: el("div.row.sd-presets.sd-saved"),
            params: el("div.sd-params"),
            wave: el("canvas.sd-wave", {title: "Click or drag to move through the sound – dragging lets you hear it"}),
            readout: el("span.muted.small.sd-readout", {translate: "no"}),
            tuned: el("span.muted.small"),
            play: el("button.btn", {type: "button", onclick: () => playOrStop().catch(err => console.error(err))}),
            loop: el("button.btn.ghost", {type: "button", text: "Loop", onclick: () => {
                SD.loop = !SD.loop;
                if (SD.playing) startPlaying(Synth.position() || SD.pos, true);
                syncSound();
            }}),
            toStart: el("button.btn.ghost.icon", {type: "button", title: "Back to the start", onclick: () => {
                SD.pos = SD.from = 0;
                if (SD.playing) startPlaying(0); else drawWave();
            }}, [icon("left")]),
            placeAt: el("button.btn.accent", {type: "button", text: "Place at the playhead", onclick: () => {
                if (HEAD === null || HEAD === undefined) return Buddy.toast(Buddy.t("Buddy doesn't know where the playhead is yet."), 3500);
                place(SD.kind, HEAD);
            }}),
            strip: el("canvas.sd-strip"),
            moments: el("div.sd-moments"),
            momentsText: el("p.muted.small.hint"),
        };
        scrubbing(UI.wave);
        UI.strip.addEventListener("click", e => {
            const h = heard();
            if (!h || !h.bars.length || !TL) return;
            const r = UI.strip.getBoundingClientRect();
            const at = h.start + Math.round((e.clientX - r.left) / r.width * (h.end - h.start));
            send("seek", {frame: at, final: true});
        });
        panel.replaceChildren(
            el("section.card.sd-card", {}, [
                el("div.row.sd-head", {}, [el("h2.section-title", {text: "Song"}), el("span.spacer"), undoBtn]),
                el("p.muted.small.hint", {text: "Pick the music on your timeline and Buddy listens to it: its key and tempo, and the moments a sound belongs on."}),
                el("div.row.sd-song-row", {}, [songPick, listen]),
                UI.bar, UI.songText, UI.tiles,
            ]),
            el("section.card.sd-card", {}, [
                el("div.row.sd-head", {}, [el("h2.section-title", {text: "Sounds"}), kindPick, el("span.spacer"),
                    el("span.muted.small", {text: "Variation"}), seed, dice, reset]),
                UI.about,
                UI.presets,
                UI.saved,
                // Stays in view while the settings under it scroll: hear and scrub while changing them.
                el("div.sd-stage", {}, [
                    el("div.sd-scope", {}, [UI.wave]),
                    el("div.row.fx-buttons.sd-transport", {}, [UI.toStart, UI.play, UI.loop, UI.readout, el("span.spacer"), UI.tuned, UI.placeAt]),
                ]),
                UI.params,
            ]),
            el("section.card.sd-card", {}, [
                el("div.row.sd-head", {}, [el("h2.section-title", {text: "Moments"})]),
                UI.momentsText, UI.strip, UI.moments,
            ]),
        );
        renderParams();
        sync();
        drawUndo();
    }

    /* The mini timeline: a click puts its playhead there; a drag scrubs. */
    function scrubbing(canvas) {
        const at = e => {
            const made = current();
            if (!made) return null;
            const r = canvas.getBoundingClientRect();
            return Math.min(made.buffer.duration, Math.max(0, (e.clientX - r.left) / r.width * made.buffer.duration));
        };
        const hear = (t, force) => {
            const now = performance.now();
            if (!force && now - scrub.last < GRAIN_MS) return;
            scrub.last = now;
            Synth.grain(current().buffer, t);
        };
        canvas.addEventListener("pointerdown", e => {
            const t = at(e);
            if (t === null || e.button !== 0) return;
            try { canvas.setPointerCapture(e.pointerId); } catch (_e) { /* a pointer that's already gone */ }
            scrub = {wasPlaying: SD.playing, last: 0};
            canvas.classList.add("scrubbing");
            if (SD.playing) { Synth.stop(); SD.playing = false; }
            SD.pos = SD.from = t;
            hear(t, true);
            drawWave();
        });
        canvas.addEventListener("pointermove", e => {
            if (!scrub) return;
            const t = at(e);
            if (t === null) return;
            SD.pos = SD.from = t;
            hear(t, false);
            drawWave();
        });
        const end = () => {
            if (!scrub) return;
            const resume = scrub.wasPlaying;
            scrub = null;
            canvas.classList.remove("scrubbing");
            if (resume) startPlaying(SD.pos); else syncSound();
        };
        canvas.addEventListener("pointerup", end);
        canvas.addEventListener("pointercancel", end);
    }

    function sync() {
        if (!UI) return;
        syncSong();
        // The song picker: every clip with audio, by track.
        const options = [el("option", {value: "", text: Buddy.t("No song – C major, 120 BPM")})];
        for (const {clip, track} of musicClips()) {
            const length = mmss((clip.end - clip.start) / TL.fps);
            options.push(el("option", {value: clip.id, text: `A${track.index} · ${clip.name} · ${length}`, selected: clip.id === SD.song}));
        }
        UI.songPick.replaceChildren(...options);
        UI.songPick.value = SD.song || "";
        UI.songPick.disabled = !TL;
        const listening = SD.listening && SD.listening === SD.song;
        const busy = FXBUSY.doing === "sd_listen";
        UI.listen.disabled = !SD.song || busy || (!!FXBUSY.doing && FXBUSY.doing !== "preview");
        UI.listen.textContent = Buddy.t(listening || busy ? "Listening…" : heard() ? "Listen again" : "Listen");
        UI.bar.hidden = !busy;
        UI.bar.firstChild.style.width = `${Math.round((FXBUSY.progress || 0) * 100)}%`;
        const h = heard();
        let text = "";
        if (!TL) text = "Open a timeline in Resolve first.";
        else if (SD.error) text = SD.error;
        else if (listening || busy) text = "Listening to the song…";
        else if (!SD.song) text = "There's no audio on this timeline – sounds are made in C major at 120 BPM.";
        else if (!h) text = "Press Listen to find its key, tempo and moments.";
        else if (h.key && !h.key.clear) text = h.key.also ? `Buddy isn't sure of the key – it could be ${h.key.also}.` : "Buddy isn't sure of the key.";
        else if (!h.bpm) text = "Buddy didn't hear a steady beat – sounds keep 120 BPM, in the key it found.";
        UI.songText.textContent = Buddy.t(text);
        UI.songText.classList.toggle("mix-error", !!SD.error);
        UI.songText.hidden = !text;
        UI.tiles.hidden = !h;
        if (h) {
            const tile = (label, value, sub) => el("div.tile", {}, [el("span", {text: label}), el("b", {text: value, translate: "no"}),
                sub ? el("span", {text: sub}) : null]);
            UI.tiles.replaceChildren(
                tile("Musical key", h.key ? h.key.name : "–", h.key ? `${h.key.camelot} · relative ${h.key.relative}` : "No notes heard"),
                tile("Tempo", h.bpm ? `${h.bpm} BPM` : "–", h.bar_s ? `A bar is ${h.bar_s.toFixed(2)} s` : ""),
                tile("Moments", String(h.moments.length), h.moments.length ? "Below – one click each" : "None found"));
        }
        syncSound();
        drawMoments();
    }

    function syncSound() {
        if (!UI) return;
        const h = heard(), s = songFor();
        UI.tuned.textContent = Buddy.t(h ? `In ${h.key ? h.key.name : "C major"} at ${s.bpm} BPM`
            : "In C major at 120 BPM – listen to a song to match it");
        UI.play.replaceChildren(icon(SD.playing ? "stop" : "play"), el("span", {text: SD.playing ? "Stop" : "Play"}));
        UI.loop.setAttribute("aria-pressed", String(SD.loop));
        UI.loop.classList.toggle("on", SD.loop);
        UI.placeAt.disabled = SD.placing || !TL;
        for (const b of UI.moments.querySelectorAll("button.sd-put")) b.disabled = SD.placing || !TL;
        drawReadout();
    }

    /* Where the mini timeline's playhead is: "0.84 s · 1.2 beats before the moment". */
    function drawReadout() {
        if (!UI) return;
        const made = current();
        if (SD.busy && !made) { UI.readout.textContent = Buddy.t("Making the sound…"); return; }
        if (!made) { UI.readout.textContent = ""; return; }
        const beats = (SD.pos - made.sync) / Synth.beatS(songFor());
        const away = Math.abs(beats) < 0.05 ? Buddy.t("on the moment")
            : Buddy.t(beats < 0 ? `${(-beats).toFixed(1)} beats before the moment` : `${beats.toFixed(1)} beats after the moment`);
        UI.readout.textContent = `${SD.pos.toFixed(2)} s · ${away}${SD.busy ? " · " + Buddy.t("Making the sound…") : ""}`;
    }

    function colour(name, fallback) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    }

    function fitCanvas(canvas) {
        const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
        if (!w || !h) return null;
        if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
        }
        const c = canvas.getContext("2d");
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.clearRect(0, 0, w, h);
        return {c, w, h};
    }

    /* Each pixel column's lowest and highest sample, both channels - worked out
       once per rendering and width, so the playhead moving costs nothing. */
    function peaksOf(made, w) {
        if (made.peaks && made.peaks.w === w) return made.peaks;
        const data = [made.buffer.getChannelData(0), made.buffer.getChannelData(1)];
        const lo = new Float32Array(w), hi = new Float32Array(w), per = data[0].length / w;
        for (let x = 0; x < w; x++) {
            const a = Math.floor(x * per), b = Math.min(data[0].length, Math.floor((x + 1) * per) + 1);
            const step = Math.max(1, Math.floor((b - a) / 96));
            for (let i = a; i < b; i += step) {
                for (const d of data) { lo[x] = Math.min(lo[x], d[i]); hi[x] = Math.max(hi[x], d[i]); }
            }
        }
        made.peaks = {w, lo, hi};
        return made.peaks;
    }

    /* The mini timeline: a ruler of beats counted from the moment, the
       waveform, the moment, the playhead. */
    function drawWave() {
        drawReadout();
        if (!UI) return;
        const got = fitCanvas(UI.wave);
        if (!got) return;
        const {c, w, h} = got;
        const made = current();
        if (!made) return;
        const dur = made.buffer.duration, x = t => t / dur * w;
        const font = getComputedStyle(document.body).fontFamily;
        const dim = colour("--text-dim", "#888");
        // The ruler and the beat grid: bars brighter, numbered from the moment.
        c.fillStyle = colour("--card-bg", "#222");
        c.fillRect(0, 0, w, RULER);
        const B = Synth.beatS(songFor());
        const pxBeat = B / dur * w;
        const every = pxBeat >= 30 ? 1 : pxBeat * 4 >= 30 ? 4 : Math.ceil(30 / (pxBeat * 4)) * 4;
        c.font = `600 10px ${font}`;
        c.textBaseline = "middle";
        for (let k = Math.ceil(-made.sync / B); made.sync + k * B <= dur; k++) {
            const gx = Math.round(x(made.sync + k * B));
            const bar = k % 4 === 0;
            c.fillStyle = dim;
            c.globalAlpha = bar ? 0.35 : 0.14;
            c.fillRect(gx, RULER, 1, h - RULER);
            c.globalAlpha = 1;
            c.fillRect(gx, bar ? 6 : 11, 1, RULER - (bar ? 6 : 11));
            if (k !== 0 && k % every === 0) c.fillText(`${k > 0 ? "+" : "−"}${Math.abs(k)}`, gx + 3, RULER / 2);
        }
        // The waveform.
        const {lo, hi} = peaksOf(made, w);
        const mid = RULER + (h - RULER) / 2, half = (h - RULER) / 2 * 0.92;
        c.fillStyle = "#4C9BE8";
        for (let px = 0; px < w; px++) c.fillRect(px, mid - hi[px] * half, 1, Math.max(1, (hi[px] - lo[px]) * half));
        // Played already: dimmed, so where it is reads at a glance.
        c.fillStyle = colour("--page-bg", "#000");
        c.globalAlpha = 0.25;
        c.fillRect(0, RULER, x(SD.pos), h - RULER);
        c.globalAlpha = 1;
        // The moment.
        const mx = x(made.sync);
        c.fillStyle = "#F2A33A";
        c.fillRect(Math.round(mx) - 1, 0, 2, h);
        c.font = `600 11px ${font}`;
        c.textBaseline = "top";
        const label = Buddy.t("The moment");
        const tw = c.measureText(label).width;
        c.fillText(label, mx + 6 + tw > w ? mx - 6 - tw : mx + 6, RULER + 4);
        // The playhead.
        const px = Math.round(x(SD.pos));
        c.fillStyle = colour("--text-strong", "#fff");
        c.fillRect(px, 0, 1.5, h);
        c.beginPath();
        c.moveTo(px - 5, 0);
        c.lineTo(px + 6.5, 0);
        c.lineTo(px + 0.75, 7);
        c.closePath();
        c.fill();
    }

    /* The song bar by bar (each bar's loudness), its moments, the playhead. */
    function drawMoments() {
        if (!UI) return;
        const h = heard();
        UI.strip.hidden = !h || !h.bars.length;
        UI.momentsText.textContent = Buddy.t(!h ? "Listen to a song first – its moments show here."
            : !h.bars.length ? "No moments without a steady beat – place sounds at the playhead instead."
                : h.moments.length ? "Click the strip to move the playhead there." : "Buddy found no moment that stands out in this song.");
        UI.moments.replaceChildren(...(h ? h.moments : []).map(m => {
            const go = el("button.btn.ghost.small.sd-time", {type: "button", text: timecode(m.frame), translate: "no",
                title: "Move the playhead here", onclick: () => send("seek", {frame: m.frame, final: true})});
            const puts = FITS[m.kind].map(kind => el("button.btn.small.sd-put", {type: "button", text: ON_IT[kind],
                disabled: SD.placing || !TL, onclick: () => {
                    setKind(kind);
                    place(kind, m.frame);
                }}));
            return el(`div.sd-moment.${m.kind}`, {}, [go, el("b", {text: MOMENT[m.kind]}),
                el("i.sd-strength", {title: "How strongly it stands out"}, el("i", {style: `width:${Math.round(m.strength * 100)}%`})),
                el("span.spacer"), ...puts]);
        }));
        if (UI.strip.hidden) return;
        const got = fitCanvas(UI.strip);
        if (!got) return;
        const {c, w, h: height} = got;
        const span = h.end - h.start, fps = TL ? TL.fps : 24;
        const x = f => (f - h.start) / span * w;
        const top = Math.max(...h.levels), floor = top - 36;
        c.fillStyle = colour("--text-dim", "#888");
        c.globalAlpha = 0.45;
        h.bars.forEach((s, i) => {
            const a = x(h.start + s * fps), b = x(h.start + (h.bars[i + 1] !== undefined ? h.bars[i + 1] : s + h.bar_s) * fps);
            const k = Math.max(0.04, (h.levels[i] - floor) / (top - floor));
            c.fillRect(a + 0.5, height - k * (height - 14), Math.max(1, b - a - 1), k * (height - 14));
        });
        c.globalAlpha = 1;
        const hue = {drop: "#E5393B", change: "#4C9BE8", break: "#9B6CE8", end: "#F2A33A"};
        for (const m of h.moments) {
            c.fillStyle = hue[m.kind];
            c.fillRect(Math.round(x(m.frame)) - 1, 0, 2, height);
            c.beginPath();
            c.arc(x(m.frame), 6, 4, 0, Math.PI * 2);
            c.fill();
        }
        if (HEAD !== null && HEAD !== undefined && HEAD >= h.start && HEAD <= h.end) {
            c.fillStyle = colour("--text-strong", "#fff");
            c.fillRect(Math.round(x(HEAD)), 0, 1, height);
        }
    }

    function drawUndo() {
        if (!UI) return;
        UI.undoBtn.hidden = !undo;
        if (undo) UI.undoBtn.lastChild.textContent = `Undo: ${undo.label}`;
    }

    // -------------------------------------------------------------- events --

    Buddy.on("timeline", () => { if (UI) sync(); });
    Buddy.on("selection", () => { if (UI && !SD.picked) sync(); });
    Buddy.on("playhead", () => drawMoments());
    Buddy.on("fx_busy", () => sync());
    Buddy.on("undo", u => { undo = u; drawUndo(); });
    Buddy.on("theme", () => { drawWave(); drawMoments(); });
    Buddy.on("i18n", () => { if (UI) { renderParams(); sync(); } });

    Buddy.on("sd_song", s => {
        if (SD.listening === s.id) SD.listening = null;
        SD.heard[s.id] = s;
        if (s.id === SD.song) {
            SD.error = s.error || "";
            remake();
        }
        sync();
    });

    Buddy.on("sd_placed", () => {
        SD.placing = false;
        syncSound();
    });

    /* The library (page.py): the saved sounds - and, the first time, the settings last used. */
    Buddy.on("sd_library", lib => {
        SD.saved = (lib && lib.saved) || [];
        if (!SD.restored) {
            SD.restored = true;
            const last = (lib && lib.last) || {};
            for (const kind of KINDS) {
                if (last.values && last.values[kind]) SD.values[kind] = Synth.values(kind, last.values[kind]);
                if (last.seed && last.seed[kind]) SD.seed[kind] = last.seed[kind];
            }
            if (KINDS.includes(last.kind)) SD.kind = last.kind;
            if (UI) { renderParams(); remake(); }
        }
        renderSaved();
    });

    // Space plays and stops the sound, and does nothing else while this tab shows - whatever has
    // the focus: a list doesn't open, a button isn't pressed (a button goes on the key coming up,
    // so that's taken too). Caught before anything else on the page sees it. Not in a dialog.
    const spaceIsOurs = e => !panel.hidden && UI && (e.key === " " || e.code === "Space")
        && !e.ctrlKey && !e.metaKey && !e.altKey && !(e.target.closest && e.target.closest(".modal-backdrop, [role=dialog]"));
    window.addEventListener("keydown", e => {
        if (!spaceIsOurs(e)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!e.repeat) playOrStop().catch(err => console.error(err));
    }, true);
    window.addEventListener("keyup", e => {
        if (!spaceIsOurs(e)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
    }, true);

    // Drawn when the tab first shows (the panel has no size while it's hidden).
    new MutationObserver(() => {
        if (panel.hidden) return stopPlaying();
        if (!UI) render();
        requestAnimationFrame(() => { drawWave(); drawMoments(); });
        if (!SD.made) remake();
    }).observe(panel, {attributes: true, attributeFilter: ["hidden"]});
    new ResizeObserver(() => { drawWave(); drawMoments(); }).observe(panel);
})();
