/*
 * The DAW's frame: the title bar every layout shares - which layout (Arrange,
 * Console, Perform: Ctrl+1, 2, 3), the song, the transport and Place - and
 * what comes from and goes to page.py. The layouts are built once and kept:
 * switching shows another, and nothing playing stops.
 *
 * Keys, in every layout (not while typing in a field):
 *   Space        play / pause - and nothing else: never a click on the button with the
 *                focus, never a list opening (only a text box takes it, as a space)
 *   R            record
 *   L            loop on / off        M        metronome
 *   Home         playhead to the start   Escape   stop
 *   Left, Right  playhead back or on a beat (Shift: a bar)
 *   Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y)  undo / redo
 *   A W S E D F T G Y H U J K O L P ; '   play, from C; Z X octave; C V how hard
 */
"use strict";

const DawApp = (() => {
    const {el, icon} = Buddy;
    const send = (name, data) => Buddy.send(name, data);
    const VIEWS = [DawViews.arrange, DawViews.console, DawViews.perform];
    let TL = null, HEAD = null, CONNECTED = false, PROBLEM = "", RESOLVE_UNDO = null;
    let listening = null;             // the clip id being listened to
    let placing = false;
    const PEAKS = new Map();          // a clip's waveform key -> {rate, codes} (page.py "peaks")
    let peaksDrawn = 0;
    const UI = {};
    const dirty = new Set();

    const active = () => VIEWS.find(v => v.id === Daw.P.view) || VIEWS[0];
    const fpsOr = () => (TL ? TL.fps : 24);
    const timecode = frame => {
        const fps = Math.round(fpsOr()), f = Math.max(0, Math.round(frame));
        const pad = n => String(n).padStart(2, "0");
        return `${pad(Math.floor(f / fps / 3600))}:${pad(Math.floor(f / fps / 60) % 60)}:${pad(Math.floor(f / fps) % 60)}:${pad(f % fps)}`;
    };

    // ------------------------------------------------------------ title bar --

    function build() {
        UI.views = Ctl.seg(VIEWS.map((v, i) => [v.id, v.label, `${v.label} (${Buddy.keys(`Ctrl+${i + 1}`)})`]), Daw.P.view, show, "Layout");
        UI.song = el("button.btn.ghost.song-pick", {type: "button", title: "The music the DAW plays along with and follows",
            onclick: e => songMenu(e.currentTarget)});
        UI.pos = el("b", {translate: "no"});
        UI.tempo = el("span", {translate: "no"});
        UI.keyOut = el("span", {translate: "no"});
        UI.lcd = el("button.lcd", {type: "button", title: "The key and tempo", onclick: keyTempo}, [UI.pos, UI.tempo, UI.keyOut, el("span", {text: "4/4", translate: "no"})]);
        UI.rec = el("button.tbtn.rec", {type: "button", title: "Record (R)", "aria-label": "Record", onclick: () => toggle(true)}, [el("i.rec-dot")]);
        UI.play = el("button.tbtn.play", {type: "button", title: "Play or pause (Space)", onclick: () => playOrPause()});
        UI.home = el("button.tbtn", {type: "button", title: "To the start (Home)", "aria-label": "To the start", onclick: () => moveCursor(Daw.loopOn() ? Daw.P.loop.start : 0)},
            [el("i.home-mark")]);
        UI.loop = el("button.tbtn.toggle", {type: "button", title: "Play the chosen bars over and over (L) – choose them by dragging along the loop strip above the bar numbers in Arrange",
            onclick: () => flip("loop")});
        UI.metro = el("button.tbtn.toggle", {type: "button", text: "Click", title: "Metronome (M)", onclick: () => flip("metronome")});
        UI.countIn = el("button.tbtn.toggle", {type: "button", text: "Count in", title: "A bar of clicks before recording starts",
            onclick: () => flip("count_in")});
        UI.preRoll = el("button.tbtn.toggle", {type: "button", text: "Pre-roll",
            title: "Play from a beat before the playhead, so nothing right at its start is cut off", onclick: () => flip("pre_roll")});
        UI.snap = el("select.field.snap", {"aria-label": "Snap", title: "Snap: where notes and clips land"},
            [[0, "Snap off"], [1, "1/4"], [2, "1/8"], [3, "1/8 triplet"], [4, "1/16"], [8, "1/32"]].map(([v, text]) =>
                el("option", {value: String(v), text})));
        UI.snap.addEventListener("change", () => { Daw.P.snap = Number(UI.snap.value); Daw.changed("snap"); });
        UI.undo = el("button.btn.ghost.icon", {type: "button", onclick: () => Daw.undo()}, [icon("undo")]);
        UI.redo = el("button.btn.ghost.icon.redo", {type: "button", onclick: () => Daw.redo()}, [icon("undo")]);
        UI.place = el("button.btn.accent", {type: "button", onclick: e => placeMenu(e.currentTarget)}, [el("span", {text: "Place…"})]);
        UI.status = el("div.daw-status", {hidden: true});
        const bar = el("header.daw-bar", {}, [
            el("h1.daw-name", {text: "DAW"}), UI.views.node, UI.song,
            el("div.transport", {}, [UI.lcd, UI.home, UI.rec, UI.play, UI.loop, UI.metro, UI.countIn, UI.preRoll, UI.snap, UI.undo, UI.redo, UI.place])]);
        UI.panes = VIEWS.map(v => el("section.daw-view", {dataset: {view: v.id}, hidden: true}));
        document.getElementById("daw").append(bar, UI.status, buildTempo(), ...UI.panes);
        VIEWS.forEach((v, i) => v.build(UI.panes[i]));
        show(Daw.P.view, false);
        syncBar();
        requestAnimationFrame(loop);
    }

    function show(id, remember = true) {
        if (!VIEWS.some(v => v.id === id)) id = "arrange";
        const before = Daw.P.view;
        Daw.P.view = id;
        UI.views.set(id);
        VIEWS.forEach((v, i) => { UI.panes[i].hidden = v.id !== id; });
        // Drawn again with what changed in another layout meanwhile.
        if (dirty.has(id)) { dirty.delete(id); active().refresh("all"); }
        if (before !== id) active().shown();
        if (remember) Daw.changed("view");
    }

    /* Console, on a clip. */
    function openClip(trackId, clipId) {
        Daw.select(trackId, clipId);
        const t = Daw.track(trackId), c = Daw.clipOf(t, clipId);
        if (c && !Daw.playing()) Daw.P.cursor = c.start;
        show("console");
    }

    /* Dragging the playhead (a ruler, a lane): playing pauses while it's dragged and
       carries on from where it's let go. */
    const scrub = {
        was: false,
        start() {
            this.was = Daw.playing() && !Daw.recording();
            if (Daw.playing()) { Daw.stop(); syncBar(); }
        },
        to(beat) {
            Daw.P.cursor = Math.max(0, beat);
            drawPosition(Daw.P.cursor);
            Daw.changed("cursor");
        },
        end() {
            if (this.was) Daw.start();
            this.was = false;
            syncBar();
        },
    };

    function moveCursor(beat, restart = true) {
        Daw.P.cursor = Math.max(0, beat);
        if (Daw.playing() && restart) { const rec = Daw.recording(); Daw.stop(); Daw.start(rec); }
        Daw.changed("cursor");
        syncBar();
    }

    /* Space and the Play button: playing pauses where it is; paused plays on from there. */
    function playOrPause() {
        if (Daw.recording()) Daw.stop();
        else if (Daw.playing()) Daw.pause();
        else Daw.start(false);
        syncBar();
    }

    function toggle(record) {
        if (Daw.playing()) {
            const was = Daw.recording();
            Daw.stop();
            if (record && !was) Daw.start(true);
        } else Daw.start(record);
        syncBar();
    }

    function flip(what) {
        const P = Daw.P;
        if (what === "loop") P.loop.on = !P.loop.on;
        if (what === "metronome") P.metronome = !P.metronome;
        if (what === "count_in") P.count_in = !P.count_in;
        if (what === "pre_roll") P.pre_roll = !P.pre_roll;
        Daw.changed(what === "loop" ? "loop" : what);
        syncBar();
    }

    function syncBar() {
        const P = Daw.P, S = Daw.song;
        const playing = Daw.playing(), rec = Daw.recording();
        UI.play.replaceChildren(icon(playing ? "pause" : "play"), el("span", {text: playing ? "Pause" : "Play"}));
        UI.play.classList.toggle("on", playing && !rec);
        UI.rec.classList.toggle("on", rec);
        UI.loop.setAttribute("aria-pressed", String(Daw.loopOn()));
        // Which bars play, said on the button: "Loop 5–8".
        const first = Daw.barOf(P.loop.start), last = Daw.barOf(P.loop.end - 1e-6);
        Parts.fill(UI.loop, el("span", {text: "Loop"}),
            Daw.loopOn() ? el("span.loop-bars", {translate: "no", text: first === last ? `${first}` : `${first}–${last}`}) : null);
        UI.metro.setAttribute("aria-pressed", String(!!P.metronome));
        UI.countIn.setAttribute("aria-pressed", String(!!P.count_in));
        UI.preRoll.setAttribute("aria-pressed", String(!!P.pre_roll));
        UI.snap.value = String(P.snap);
        UI.tempo.textContent = `${Math.round(Daw.bpm() * 10) / 10} BPM`;
        // Following a song Buddy heard no steady beat in: the tempo is the project's own, not
        // the song's - said, so it isn't taken for what was heard.
        const unsure = !!(P.follow && S && !Daw.songBpm());
        UI.tempo.classList.toggle("unsure", unsure);
        UI.lcd.title = Buddy.t(unsure ? "Buddy couldn't hear a steady beat in the song, so this is the project's own tempo – click to set it"
            : "The key and tempo");
        UI.keyOut.textContent = Daw.keyName();
        const u = Daw.undoLabel(), r = Daw.redoLabel();
        UI.undo.disabled = !u;
        UI.undo.title = u ? `${Buddy.t("Undo")}: ${Buddy.t(u)} (${Buddy.keys("Ctrl+Z")})` : Buddy.t("Undo");
        UI.redo.disabled = !r;
        UI.redo.title = r ? `${Buddy.t("Redo")}: ${Buddy.t(r)} (${Buddy.keys("Ctrl+Shift+Z")})` : Buddy.t("Redo");
        const songText = listening ? Buddy.t("Listening to the song…")
            : S ? `${S.name} · ${S.key ? S.key.name : Buddy.t("no key heard")} · ${Daw.songBpm() ? `${Math.round(Daw.songBpm())} BPM` : Buddy.t("no steady beat")}`
                : TL && TL.songs.length ? Buddy.t("Pick the song…") : Buddy.t("No song");
        UI.song.replaceChildren(icon("music"), el("span", {text: songText, translate: S ? "no" : undefined}));
        UI.song.disabled = !TL || !TL.songs.length;
        UI.place.disabled = placing;
        if (!S) UI.tempoBar.hidden = true;
        if (!UI.tempoBar.hidden) syncTempo();
        UI.status.hidden = CONNECTED && !PROBLEM;
        UI.status.textContent = CONNECTED ? PROBLEM
            : Buddy.t(PROBLEM ? `${PROBLEM} – the DAW still plays; placing needs a timeline open in Resolve.`
                : "Open a timeline in Resolve to play along with its music and place what you make. Everything else works without it.");
        drawPosition(Daw.position());
    }

    function drawPosition(beat) {
        const b = Math.max(0, beat);
        UI.pos.textContent = `${Daw.barOf(b)}.${Math.floor(b) % 4 + 1}.${Math.floor(b * 4) % 4 + 1}`;
    }

    // ------------------------------------------------------------ song --

    /* The clips on the timeline the song can be, and whether to follow its key and tempo. */
    function songMenu(anchor) {
        if (!TL) return;
        const r = anchor.getBoundingClientRect(), P = Daw.P;
        const items = TL.songs.slice(0, 30).map(s => ({label: `${s.name} · A${s.track} · ${timecode(s.start)}`, raw: true,
            checked: Daw.song ? Daw.song.id === s.id : false, onClick: () => listen(s.id)}));
        Buddy.menu({x: r.left, y: r.bottom + 4, items: [
            {heading: "Play along with"}, ...items, {sep: true},
            {label: "Follow its key and tempo", checked: !!P.follow, onClick: () => { Daw.edit("Follow the song", () => { P.follow = !P.follow; }, "key"); syncBar(); }},
            {label: "Correct its tempo…", disabled: !Daw.song, onClick: () => openTempo()},
            {label: "No song", disabled: !Daw.song, onClick: () => { P.song = ""; Daw.setSong(null); syncBar(); }},
        ]});
    }

    /* Buddy's guess: the longest clip that looks like music, else the longest. */
    function guessSong() {
        if (!TL || !TL.songs.length) return null;
        const music = TL.songs.filter(s => /music|song|score|bgm|soundtrack|beat/i.test(`${s.track_name} ${s.name}`));
        const pool = music.length ? music : TL.songs;
        return pool.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a)).id;
    }

    function listen(id) {
        listening = id;
        send("listen", {id});
        syncBar();
    }

    // ------------------------------------------------------------ key and tempo --

    function keyTempo() {
        const P = Daw.P, S = Daw.song;
        const follow = el("input", {type: "checkbox", checked: !!P.follow, disabled: !S});
        const tonic = el("select.field", {}, Daw.NAMES.map((n, i) => el("option", {value: String(i), text: n, selected: i === P.tonic})));
        const mode = el("select.field", {}, [["major", "major"], ["minor", "minor"]].map(([v, t]) => el("option", {value: v, text: t, selected: v === P.mode})));
        const bpm = el("input.field.num", {type: "number", min: "40", max: "240", step: "1", value: String(Math.round(P.bpm))});
        const length = el("input.field.num", {type: "number", min: "1", max: "256", step: "1", value: String(P.length)});
        const sync = () => { const on = follow.checked && S; tonic.disabled = mode.disabled = !!(on && S.key); bpm.disabled = !!(on && S.bpm); };
        follow.addEventListener("change", sync);
        sync();
        const dialog = Buddy.modal({title: "Key and tempo", body: [
            el("label.check", {}, [follow, " ", Buddy.t(S ? "Follow the song's key and tempo" : "Follow the song's key and tempo (pick a song first)")]),
            el("div.row.kt-row", {}, [el("span.ctl-label", {text: "Key"}), tonic, mode]),
            el("div.row.kt-row", {}, [el("span.ctl-label", {text: "Tempo"}), bpm, el("span", {text: "BPM"})]),
            el("div.row.kt-row", {}, [el("span.ctl-label", {text: "Length"}), length, el("span", {text: "bars"})]),
            S ? el("div.row.kt-row", {}, [el("span.ctl-label"), el("button.btn.ghost.kt-tempo", {type: "button",
                text: "Correct the song's tempo…", title: "When Buddy heard the song's tempo or its first bar wrong",
                onclick: () => { dialog.close(); openTempo(); }})]) : null,
        ], buttons: [{label: "Cancel"}, {label: "OK", kind: "accent", onClick: close => {
            Daw.edit("Change the key and tempo", () => {
                P.follow = follow.checked;
                P.tonic = Number(tonic.value);
                P.mode = mode.value;
                const b = Math.round(Number(bpm.value));
                if (Number.isFinite(b)) P.bpm = Math.min(240, Math.max(40, b));
                const l = Math.round(Number(length.value));
                if (Number.isFinite(l)) P.length = Math.min(256, Math.max(1, l));
            }, "key");
            syncBar();
            close();
        }}]});
    }

    // ------------------------------------------------------------ the song's tempo --
    //
    // For when Buddy hears a song's tempo wrong: a strip under the title bar that stays open
    // while the song plays, to put it right by ear - the BPM a music library gives, a tempo
    // heard double, half or 4:3 off, where its downbeats are, the grid nudged a few milliseconds
    // against the song. Each change is an edit (Undo), kept for the song's file.

    const SNAP_S = 0.06;       // "Downbeat here" this near a beat of the grid: on that beat
    const NUDGE_S = 0.005;     // a nudge (Shift: 1 ms)
    const TIMES = [
        [0.5, "½", "Half as fast – Buddy heard it double"],
        [0.75, "¾", "Three quarters as fast – Buddy counted the hi-hats as the beat"],
        [4 / 3, "4/3", "A third faster – Buddy heard it three quarters as fast"],
        [2, "2×", "Twice as fast – Buddy heard it half"],
    ];

    /* The grid to correct: the song's - or, with no steady beat heard in it, the project's
       tempo from the song's first moment. */
    const gridNow = () => Daw.songGrid() || {bpm: Daw.P.bpm, first: 0};
    function setGrid(change, label) {
        Daw.setSongGrid(Object.assign(gridNow(), change), label);
        syncBar();
    }

    function buildTempo() {
        const T = UI.t = {};
        const btn = (text, title, fn, cls = "") => el(`button.tbtn${cls}`, {type: "button", text, title, onclick: fn});
        const most = Daw.MAX_SONG_BPM, least = Daw.MIN_SONG_BPM;
        T.bpm = el("input.field.num.tempo-bpm", {type: "number", min: String(least), max: String(most), step: "0.01",
            "aria-label": "The song's tempo", translate: "no"});
        T.bpm.addEventListener("change", () => {
            const b = Number(T.bpm.value);
            if (Number.isFinite(b) && b >= least && b <= most && Math.abs(b - gridNow().bpm) > 1e-6) setGrid({bpm: b}, "Set the song's tempo");
            else syncTempo(true);
        });
        T.times = TIMES.map(([times, mark, title]) => {
            const b = el("button.tbtn.tempo-x", {type: "button", title, translate: "no",
                onclick: () => setGrid({bpm: gridNow().bpm * times}, "Correct the song's tempo")}, [el("b", {text: mark}), el("span")]);
            b.dataset.times = String(times);
            return b;
        });
        const beat = dir => () => { const g = gridNow(); setGrid({first: g.first + dir * 60 / g.bpm}, "Move the downbeat"); };
        T.here = btn("Downbeat here", "A bar starts at the playhead – or click this on a downbeat (a 1) as the song plays", downbeatHere, ".tempo-here");
        T.first = el("span.tempo-first", {translate: "no", title: "Where the song's first bar starts, in seconds into the song"});
        const nudge = dir => e => { const g = gridNow(); setGrid({first: g.first + dir * (e.shiftKey ? 0.001 : NUDGE_S)}, "Nudge the song's grid"); };
        T.state = el("span.tempo-state");
        T.follow = el("button.btn.ghost", {type: "button", text: "Follow the song's tempo",
            onclick: () => { Daw.edit("Follow the song", () => { Daw.P.follow = true; }, "key"); syncBar(); }});
        T.reset = el("button.btn.ghost", {type: "button", text: "Back to what Buddy heard",
            onclick: () => { Daw.setSongGrid(null, "Back to the tempo Buddy heard"); syncBar(); }});
        const done = el("button.btn.ghost.icon", {type: "button", title: "Done", "aria-label": "Done",
            onclick: () => { UI.tempoBar.hidden = true; }}, [icon("close")]);
        UI.tempoBar = el("div.tempo-bar", {hidden: true, role: "group", "aria-label": "The song's tempo"}, [
            el("div.tempo-group", {}, [el("span.daw-label", {text: "Song tempo"}), T.bpm, el("span.tempo-unit", {text: "BPM", translate: "no"})]),
            el("div.tempo-group", {}, [el("span.daw-label", {text: "Heard wrong?"}), ...T.times]),
            el("div.tempo-group", {}, [el("span.daw-label", {text: "Downbeat"}),
                btn("◀", "The downbeat a beat earlier", beat(-1), ".tempo-beat"), T.here, btn("▶", "The downbeat a beat later", beat(1), ".tempo-beat"), T.first]),
            el("div.tempo-group", {}, [el("span.daw-label", {text: "Nudge"}),
                btn("◀ 5 ms", "The grid – and what plays on it – a little earlier against the song (Shift: 1 ms)", nudge(-1), ".tempo-nudge"),
                btn("5 ms ▶", "The grid – and what plays on it – a little later against the song (Shift: 1 ms)", nudge(1), ".tempo-nudge")]),
            el("div.tempo-group.tempo-end", {}, [T.state, T.follow, T.reset, done])]);
        for (const b of UI.tempoBar.querySelectorAll(".tempo-beat, .tempo-nudge")) b.setAttribute("translate", "no");
        return UI.tempoBar;
    }

    function openTempo() {
        if (!Daw.song) return;
        UI.tempoBar.hidden = false;
        syncTempo();
    }

    function syncTempo(typed = false) {
        const T = UI.t, g = gridNow(), hand = Daw.handGrid(), heard = Daw.heardGrid();
        if (typed || document.activeElement !== T.bpm) T.bpm.value = String(+g.bpm.toFixed(2));
        for (const b of T.times) {
            const to = g.bpm * Number(b.dataset.times);
            b.lastChild.textContent = String(Math.round(to * 10) / 10);
            b.disabled = to < Daw.MIN_SONG_BPM || to > Daw.MAX_SONG_BPM;
        }
        T.first.textContent = `${g.first.toFixed(3)} s`;
        T.state.textContent = Buddy.t(hand ? "Set by hand" : heard ? "As Buddy heard it"
            : "Buddy heard no steady beat – type the tempo, then set a downbeat");
        T.reset.hidden = !hand;
        T.reset.title = heard ? `${Buddy.t("Buddy heard")} ${Math.round(heard.bpm * 10) / 10} BPM` : "";
        // Not following the song, the DAW keeps its own tempo: the song's, corrected, isn't heard.
        T.follow.hidden = !!Daw.P.follow;
    }

    /* A downbeat where the song is heard now - on the grid's nearest beat when it's that
       close (the tempo and the beats were right; which beat is the 1 wasn't). The bars run
       on from it both ways, bar 1 the first at the timeline's start. */
    function downbeatHere() {
        const g = Daw.songGrid(), t = Daw.songNow();
        let first = t;
        if (g) {
            const beat = 60 / g.bpm, near = g.first + Math.round((t - g.first) / beat) * beat;
            if (Math.abs(near - t) <= SNAP_S) first = near;
        }
        setGrid({first}, "Put a downbeat here");
    }

    // ------------------------------------------------------------ the picture --
    //
    // Timeline frames and the DAW's beats (engine.js): beat 0 is the timeline's first frame -
    // on the song's grid with a song, so its bars are the song's - so the picture lane shows
    // where things land, and Place puts them there.

    const frameRate = () => (Daw.song ? Daw.song.fps : fpsOr());
    const beatOfFrame = frame => Daw.beatOfFrame(frame);
    const frameOfBeat = beat => Daw.frameOfBeat(beat);

    /* The edit as beats, for the picture lane - or null with no timeline open: its cuts,
       its markers (Buddy's own beat markers left out) and where the picture ends. */
    function picture() {
        if (!TL) return null;
        return {
            cuts: (TL.cuts || []).map(frame => ({frame, beat: beatOfFrame(frame)})),
            markers: (TL.markers || []).map(m => Object.assign({}, m, {beat: beatOfFrame(m.frame),
                end: beatOfFrame(m.frame + Math.max(1, m.duration || 1))})),
            end: TL.video_end ? beatOfFrame(TL.video_end) : null,
            audio: TL.audio || [], fps: TL.fps,
        };
    }

    // ------------------------------------------------------------ place --

    function placeMenu(anchor) {
        const r = anchor.getBoundingClientRect(), t = Daw.selTrack();
        const mix = Daw.usedRange(), one = t ? Daw.usedRange(t.id) : null;
        const bars = range => (range ? ` · ${Buddy.t("bars")} ${Daw.barOf(range.from)}–${Daw.barOf(range.to - 1e-6)}` : "");
        Buddy.menu({x: r.right - 260, y: r.bottom + 4, items: [
            {heading: TL ? (Daw.song ? "Lined up with the song" : "Lined up with the picture") : "Open a timeline in Resolve first"},
            {label: `${Buddy.t("The whole mix")}${bars(mix)}`, disabled: !TL || !mix, onClick: () => place("mix")},
            {label: `${Buddy.t("The selected track")}${t ? ` (${t.name})` : ""}${bars(one)}`, raw: true, disabled: !TL || !one, onClick: () => place("track")},
            {label: `${Buddy.t("The loop")} · ${Buddy.t("bars")} ${Daw.barOf(Daw.P.loop.start)}–${Daw.barOf(Daw.P.loop.end - 1e-6)}`, disabled: !TL, onClick: () => place("loop")},
            {sep: true},
            {label: RESOLVE_UNDO ? `${Buddy.t("Undo")}: ${Buddy.t(RESOLVE_UNDO)}` : "Nothing placed to undo", disabled: !RESOLVE_UNDO, onClick: () => send("undo")},
        ]});
    }

    /* A render of the mix, the selected track or the loop, onto the timeline as a new clip:
       where the song plays that bar - or, with no song, where the picture lane shows it. */
    async function place(what) {
        if (!TL) return Buddy.toast(Buddy.t("Open a timeline in Resolve first."), 3500);
        const P = Daw.P, t = Daw.selTrack(), S = Daw.song;
        const range = what === "loop" ? {from: P.loop.start, to: P.loop.end} : Daw.usedRange(what === "track" && t ? t.id : null);
        if (!range) return Buddy.toast(Buddy.t("There's nothing to place yet – record or write some notes first."), 3500);
        if ((range.to - range.from) * Daw.beatS() > 145) return Buddy.toast(Buddy.t("That's longer than Buddy places at once (2½ minutes) – place a loop or one track."), 4500);
        // Its first beat seldom falls on a frame: the clip starts on the frame before, the
        // render that much silence in - on the beat to the sample.
        // Bar 1 can begin a moment before the timeline's first frame (the song's bars carried
        // back to it): the clip starts on that frame, and what comes before it is left out.
        const exact = frameOfBeat(range.from), frame = Math.max(TL.start, Math.floor(exact + 1e-6));
        const lead = (exact - frame) / frameRate();
        Daw.stop();
        placing = true;
        syncBar();
        Buddy.toast(Buddy.t("Rendering…"), 1500);
        try {
            let buffer = await Daw.render(range.from, range.to, what === "track" && t ? t.id : null, Math.max(0, lead));
            if (lead < 0) buffer = Daw.trim(buffer, -lead);
            const what2 = what === "track" && t ? t.name : what === "loop" ? "loop" : "mix";
            send("place", {what, frame, rate: buffer.sampleRate, channels: Daw.channels(buffer), song: S ? S.id : null,
                label: `DAW ${what2} ${Daw.keyName()} ${Math.round(Daw.bpm())} BPM`,
                recipe: {kind: "daw", what, bpm: Daw.bpm(), key: Daw.keyName(), from: range.from, to: range.to}});
        } catch (err) {
            placing = false;
            syncBar();
            Buddy.toast(String(err), 4000);
        }
    }

    // ------------------------------------------------------------ keys --

    // A list (Chords, Snap...) with the focus keeps only the keys that work it - Up, Down,
    // Enter... The rest play and move as anywhere else: a letter is a note, never a jump
    // to the option it starts.
    const LIST_KEYS = new Set(["ArrowUp", "ArrowDown", "Enter", "F4", "Tab", "PageUp", "PageDown", "Home", "End"]);
    const inList = e => e.target.closest && e.target.closest("select");
    const typing = e => e.target.closest && (e.target.closest("input, textarea, [contenteditable]")
        || e.target.closest(".modal-backdrop, [role=dialog], .menu-pop")
        || (inList(e) && (LIST_KEYS.has(e.key) || e.altKey)));

    // Picked from with the mouse, a list lets go of the focus, so the keys go back to playing
    // at once. (Worked from the keys, it keeps it.)
    let listKeyAt = -Infinity;
    window.addEventListener("keydown", e => { if (inList(e)) listKeyAt = e.timeStamp; }, true);
    document.addEventListener("change", e => {
        if (e.target.matches && e.target.matches("#daw select") && e.timeStamp - listKeyAt > 500) e.target.blur();
    });

    // Space plays or pauses, and does nothing else: caught before anything on the page sees
    // it - a focused button isn't pressed, a list doesn't open, a knob or step isn't changed.
    // Only where text is typed (the search, a name) is it a space; in a dialog it's the dialog's.
    const TEXT = "input:not([type]), input[type=text], input[type=search], input[type=number], textarea, [contenteditable]";
    const spaceIsOurs = e => e.code === "Space" && !e.ctrlKey && !e.metaKey && !e.altKey && e.target.closest
        && !e.target.closest(TEXT) && !e.target.closest(".modal-backdrop, [role=dialog], .menu-pop");
    window.addEventListener("keydown", e => {
        if (!spaceIsOurs(e)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!e.repeat) playOrPause();
    }, true);
    window.addEventListener("keyup", e => {
        if (!spaceIsOurs(e)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
    }, true);

    window.addEventListener("keydown", e => {
        if (typing(e)) return;
        if (inList(e) && e.key.length === 1) e.preventDefault();    // not the list's type-ahead
        const ctrl = e.ctrlKey || e.metaKey, P = Daw.P;
        if (ctrl && !e.altKey) {
            const n = "123".indexOf(e.key);
            if (n >= 0) { e.preventDefault(); return show(VIEWS[n].id); }
            if (e.code === "KeyZ") { e.preventDefault(); return e.shiftKey ? Daw.redo() : Daw.undo(); }
            if (e.code === "KeyY") { e.preventDefault(); return Daw.redo(); }
        }
        if (active().keydown && active().keydown(e)) { e.preventDefault(); return; }
        if (ctrl || e.altKey) return;
        if (e.code === "Escape") { if (Daw.playing()) { Daw.stop(); syncBar(); } return; }
        if (e.code === "Home") { e.preventDefault(); return moveCursor(0); }
        if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
            e.preventDefault();
            const by = e.shiftKey ? 4 : 1, dir = e.code === "ArrowRight" ? 1 : -1;
            const here = Daw.playing() ? Daw.position() : P.cursor;
            return moveCursor(Math.max(0, (dir > 0 ? Math.floor(here / by + 1e-6) + 1 : Math.ceil(here / by - 1e-6) - 1) * by));
        }
        if (e.code in Ctl.CODES) {
            e.preventDefault();
            if (!e.repeat) Daw.noteOn(e.code, [12 * (P.octave + 1) + Ctl.CODES[e.code]], P.velocity);
            return;
        }
        if (e.repeat) return;
        if (e.code === "KeyR") return toggle(true);
        if (e.code === "KeyL") return flip("loop");
        if (e.code === "KeyM") return flip("metronome");
        if (e.code === "KeyZ" || e.code === "KeyX") {
            Daw.allOff();
            P.octave = Math.min(7, Math.max(1, P.octave + (e.code === "KeyX" ? 1 : -1)));
            Buddy.toast(`${Buddy.t("Octave")} C${P.octave}`, 900);
            return Daw.changed("keys");
        }
        if (e.code === "KeyC" || e.code === "KeyV") {
            P.velocity = Math.min(1, Math.max(0.1, +(P.velocity + (e.code === "KeyV" ? 0.1 : -0.1)).toFixed(2)));
            Buddy.toast(`${Buddy.t("How hard")} ${Math.round(P.velocity * 100)}%`, 900);
            return Daw.changed("keys");
        }
    });
    window.addEventListener("keyup", e => {
        if (active().keyup && active().keyup(e)) return;
        if (e.code in Ctl.CODES) Daw.noteOff(e.code);
    });
    window.addEventListener("blur", () => Daw.allOff());

    // ------------------------------------------------------------ the frame --

    let wasPlaying = false;
    function loop() {
        const playing = Daw.playing();
        const pos = playing ? Daw.position() : null;
        if (playing) drawPosition(Math.max(0, pos));
        if (playing !== wasPlaying) { wasPlaying = playing; syncBar(); }
        active().frame(pos);
        requestAnimationFrame(loop);
    }

    // ------------------------------------------------------------ engine --

    Daw.on("change", ({what}) => {
        if (!UI.panes) return;
        for (const v of VIEWS) if (v !== active()) dirty.add(v.id);
        if (what !== "view") active().refresh(what);
        syncBar();
    });
    Daw.on("select", () => {
        for (const v of VIEWS) if (v !== active()) dirty.add(v.id);
        active().refresh("select");
    });
    // Stopped: the playhead goes back to where it'll start from.
    Daw.on("transport", () => { syncBar(); if (UI.panes && !Daw.playing()) active().refresh("cursor"); });
    Daw.on("song", () => syncBar());

    // ------------------------------------------------------------ page.py --

    // The project for the open timeline ({scope, project}; a bare one from before): another
    // timeline's, its song listened to afresh.
    Buddy.on("project", msg => {
        const scoped = !!(msg && typeof msg === "object" && "scope" in msg && !Array.isArray(msg.tracks));
        const scope = scoped ? msg.scope : null, project = scoped ? msg.project : msg;
        const another = scope !== null && Daw.scope !== null && scope !== Daw.scope;
        if (another && (Daw.song || listening)) { listening = null; Daw.setSong(null); }
        Daw.load(project, scope);
        if (UI.panes) show(Daw.P.view, false);
    });
    Buddy.on("state", s => { CONNECTED = !!s.connected; PROBLEM = s.problem || ""; if (UI.panes) syncBar(); });
    Buddy.on("timeline", t => {
        TL = t;
        Daw.setEdit(TL);
        // The song lane's clip: the one used last, else Buddy's guess - listened to once.
        if (TL && !Daw.song && !listening) {
            const want = TL.songs.some(s => s.id === Daw.P.song) ? Daw.P.song : guessSong();
            if (want) listen(want);
        }
        if (!TL && Daw.song) Daw.setSong(null);
        if (UI.panes) {
            // The edit may have changed: the picture lane drawn again.
            for (const v of VIEWS) if (v !== active()) dirty.add(v.id);
            active().refresh("picture");
            syncBar();
        }
    });
    // Moved in Resolve while the DAW is stopped, Resolve's playhead takes the DAW's with it
    // (follow_resolve, the picture lane's Follow) - only when it moves, so the DAW's own
    // playhead, moved here, stays put till Resolve's does.
    let lastHead = null;
    Buddy.on("playhead", p => {
        HEAD = p ? p.frame : null;
        if (HEAD !== null && lastHead !== null && HEAD !== lastHead && TL && Daw.P.follow_resolve && !Daw.playing()) {
            const beat = beatOfFrame(HEAD);
            if (beat >= 0) moveCursor(Math.round(beat * 1000) / 1000, false);
        }
        lastHead = HEAD;
    });
    Buddy.on("listening", () => {});
    Buddy.on("song", found => {
        if (found && found.id !== listening && listening !== null) return;
        listening = null;
        if (found && found.error) { Buddy.toast(found.error, 4000); syncBar(); return; }
        Daw.setSong(found);
        syncBar();
    });
    // The timeline's clips' waveforms, as page.py reads each file: the lanes drawn again
    // once a few have come (not once for each).
    Buddy.on("peaks", p => {
        if (!p || !p.key) return;
        let codes = null;
        if (p.data) {
            const bin = atob(p.data);
            codes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) codes[i] = bin.charCodeAt(i);
        }
        PEAKS.set(p.key, codes ? {rate: p.rate, codes} : null);
        if (peaksDrawn) return;
        peaksDrawn = setTimeout(() => { peaksDrawn = 0; if (UI.panes) active().refresh("audio"); }, 250);
    });
    Buddy.on("placed", () => { placing = false; syncBar(); });
    Buddy.on("undo", u => { RESOLVE_UNDO = u ? u.label : null; });
    Buddy.on("toast", t => Buddy.toast(t.text, 3500));
    Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}), buttons: [{label: "OK", kind: "accent"}]}));

    build();

    // For tests and a look from outside.
    window.DawState = {view: () => Daw.P.view, playing: () => Daw.playing(), project: () => JSON.parse(JSON.stringify(Daw.P))};

    return {show, openClip, moveCursor, syncBar, scrub, picture, beatOfFrame, frameOfBeat, timecode, openTempo,
            peaks: key => (key && PEAKS.get(key)) || null};
})();
