"use strict";

// Auto Switch's view (page.py says what each event carries): three steps -
// pair audio with the cameras, name the voices, the extra shots and Build.
// Python owns the state; this draws it and says what was clicked.

const $ = id => document.getElementById(id);
const el = Buddy.el;
const COLORS = ["#4e9be0", "#e0884e", "#5bbf7a", "#c66bd1", "#d6c14e", "#5ec9c9", "#e06a7a", "#9a8be0"];
const PEOPLE = [["1", "1 person"], ["2", "2 people"], ["3", "3 people"], ["4", "4 people"],
                ["5", "5 people"], ["6", "6 people"], ["auto", "Find out"]];
const NEXT = {1: "Next: name voices", 2: "Next: extra shots", 3: "Build timeline"};

let state = {}, setup = {audio: [], cameras: [], setups: [], setup: ""}, voices = {mics: [], voices: []},
    plan = {shots: [], rows: []}, cut = {}, step = 1;

function onTimeline() {
    return setup.cameras.filter(c => c.track != null);
}

function cameraOnTrack(track) {
    return setup.cameras.find(c => c.track === track);
}

function cameraColor(track) {
    const at = onTimeline().findIndex(c => c.track === track);
    return COLORS[(at < 0 ? track : at) % COLORS.length];
}

function cameraName(cam) {
    return cam ? (cam.role === "speaker" && cam.person ? cam.person : cam.file || cam.label) : "";
}

function busy() {
    return ["reading", "measuring", "listening", "grouping", "building"].includes(state.stage);
}

function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function thumb(cam) {
    return cam.still ? el("img.sw-thumb", {src: cam.still, alt: ""})
                     : el("span.sw-thumb.sw-thumb-empty", {}, [Buddy.icon("film")]);
}

function cameraBox(cam) {
    return el("div.sw-camera", {}, [
        thumb(cam),
        el("span.sw-dot", {style: `background:${cameraColor(cam.track)}`}),
        el("span.sw-file", {text: cam.file || cam.label, title: cam.label, translate: "no"}),
        el("span.muted.small", {text: `V${cam.track}`}),
    ]);
}

// ------------------------------------------------------------------ steps --

function showStep(n) {
    step = n;
    for (const panel of document.querySelectorAll("[data-panel]")) panel.hidden = Number(panel.dataset.panel) !== n;
    for (const b of document.querySelectorAll("[data-step]")) b.setAttribute("aria-current", Number(b.dataset.step) === n ? "step" : "false");
    $("back").hidden = n === 1;
    $("next").textContent = NEXT[n];
    $("next").disabled = n === 3 && (busy() || !plan.shots.length);
}

for (const b of document.querySelectorAll("[data-step]")) b.addEventListener("click", () => showStep(Number(b.dataset.step)));
$("back").addEventListener("click", () => showStep(Math.max(1, step - 1)));
$("next").addEventListener("click", () => step < 3 ? showStep(step + 1) : Buddy.send("build"));

// ------------------------------------------------------------------ state --

Buddy.on("state", s => {
    state = s;
    $("timeline").textContent = s.timeline || "";
    $("problem").textContent = s.problem || "";
    $("analyse").disabled = busy();
    $("cancel").hidden = !["measuring", "listening"].includes(s.stage);
    $("status").hidden = !s.stage;
    $("status-text").textContent = s.text || "";
    const bar = $("status-bar");
    bar.classList.toggle("indeterminate", !(s.progress >= 0));
    bar.style.width = s.progress >= 0 ? `${Math.round(s.progress * 100)}%` : "";
    $("engine-notice").hidden = !s.needs_voices && !s.downloading;
    $("engine").hidden = s.engine;
    $("download").hidden = !s.engine || s.model;
    $("download").disabled = s.downloading;
    $("download").textContent = s.downloading ? "Downloading…" : "Download the voice model";
    $("download-text").textContent = s.downloading ? (s.download_text || "") : "";
    showStep(step);
});

// ---------------------------------------------------- step 1: pair audio --

function sendCam(cam, key, value) {
    Buddy.send("cam", {id: cam.id, key, value});
}

Buddy.on("setup", s => {
    setup = s;
    const pick = $("setup");
    pick.replaceChildren(...s.setups.map(x => el("option", {value: x.id, text: x.name, translate: "no"})));
    pick.value = s.setup;
    $("pairs").replaceChildren(...onTimeline().map(cam => {
        const audio = el("select.field", {"aria-label": "Synced audio",
                                          onchange: e => sendCam(cam, "audio", e.target.value === "" ? [] : [Number(e.target.value)])},
            [el("option", {value: "", text: "No audio"}),
             ...s.audio.map(a => el("option", {value: String(a.index), text: a.label, translate: "no",
                                               selected: cam.audio[0] === a.index}))]);
        if (!cam.audio.length) audio.value = "";
        return el("div.sw-pair", {}, [cameraBox(cam), el("span.sw-link-icon", {}, [Buddy.icon(cam.audio.length ? "check" : "minus")]), audio]);
    }));
    drawVoices();
    drawExtras();
});

// --------------------------------------------------- step 2: name voices --

Buddy.on("voices", v => {
    voices = v;
    drawVoices();
});

function drawVoices() {
    const cams = onTimeline();
    $("voices").replaceChildren(...voices.mics.map(mic => {
        const people = el("select.field", {"aria-label": "People on this mic",
                                           onchange: e => Buddy.send("people", {track: mic.index, value: e.target.value})},
            PEOPLE.map(([value, label]) => el("option", {value, text: label, selected: value === mic.people})));
        const rows = voices.voices.filter(x => x.track === mic.index).map(voice => {
            const camera = el("select.field", {"aria-label": "Camera",
                                               onchange: e => Buddy.send("map", {voice: voice.id, camera: e.target.value || null})},
                [el("option", {value: "", text: "No camera"}),
                 ...cams.map(c => el("option", {value: c.id, text: c.file || c.label, translate: "no",
                                                selected: c.id === voice.camera}))]);
            if (!cams.some(c => c.id === voice.camera)) camera.value = "";
            const cam = cams.find(c => c.id === voice.camera);
            return el("div.sw-voice", {}, [
                el("button.btn.ghost.icon", {type: "button", title: "Hear it in Resolve", "aria-label": "Hear it",
                                             disabled: voice.sample == null,
                                             onclick: () => Buddy.send("seek", {frame: voice.sample})}, [Buddy.icon("play")]),
                el("input.field.sw-name", {type: "text", value: voice.name, maxlength: 60, translate: "no",
                                           placeholder: voice.which || "Name", "aria-label": "Name",
                                           onchange: e => Buddy.send("name", {voice: voice.id, name: e.target.value})}),
                el("span.muted.small", {text: `${Math.round(voice.share * 100)}% of the talk`}),
                el("span.spacer"),
                el("span.sw-dot", {style: cam ? `background:${cameraColor(cam.track)}` : ""}),
                camera,
            ]);
        });
        return el("div.sw-mic", {}, [
            el("div.row.sw-mic-head", {}, [
                el("span.sw-file", {text: mic.label, translate: "no"}),
                el("span.spacer"),
                people,
            ]),
            ...rows,
        ]);
    }));
}

// ----------------------------------------- step 3: extra shots and build --

function useOf(cam) {
    return cam.role === "angle" ? `angle:${cam.of || ""}` : cam.role;
}

function drawExtras() {
    const cams = onTimeline();
    const speakers = cams.filter(c => c.role === "speaker");
    const extras = cams.filter(c => c.role !== "speaker");
    $("extras").replaceChildren(...extras.map(cam => {
        const use = el("select.field", {"aria-label": "Used for", onchange: e => {
            const [role, of] = e.target.value.split(":");
            sendCam(cam, "role", role);
            if (role === "angle") sendCam(cam, "of", of || null);
        }}, [
            el("option", {value: "wide", text: "Wide shot"}),
            ...speakers.map(s => el("option", {value: `angle:${s.id}`, text: `Second angle of ${cameraName(s)}`})),
            el("option", {value: "cutaway", text: "Random cutaway"}),
            el("option", {value: "off", text: "Not used"}),
        ]);
        use.value = useOf(cam);
        if (use.selectedIndex < 0) use.value = "off";
        return el("div.sw-pair", {}, [cameraBox(cam), el("span.sw-link-icon"), use]);
    }));
    if (!extras.length) $("extras").append(el("p.muted.small", {text: "Every camera shows a person."}));
}

function place(node, a, b, span) {
    const total = Math.max(1, span[1] - span[0]);
    node.style.left = `${((a - span[0]) / total) * 100}%`;
    node.style.width = `${Math.max(0.05, ((b - a) / total) * 100)}%`;
    return node;
}

Buddy.on("plan", p => {
    plan = p;
    $("preview").hidden = !p.shots.length;
    showStep(step);
    if (!p.shots.length) return;
    const span = p.span;
    $("strip").replaceChildren(...p.shots.map(s => place(el("div.sw-shot", {
        style: `background:${cameraColor(s.camera)}`,
        title: `${cameraName(cameraOnTrack(s.camera)) || `V${s.camera}`} · ${clock((s.start - p.start) / p.fps)}–${clock((s.end - p.start) / p.fps)}`,
    }), s.start, s.end, span)));
    const per = p.summary.cameras;
    $("legend").replaceChildren(...Object.keys(per).map(Number).sort((a, b) => a - b).map(track =>
        el("span.sw-key", {}, [el("span.sw-dot", {style: `background:${cameraColor(track)}`}),
                               el("span", {text: cameraName(cameraOnTrack(track)) || `V${track}`, translate: "no"}),
                               el("span.muted.small", {text: ` ${per[track].shots} · ${clock(per[track].seconds)}`})])));
    $("summary").textContent = `${p.summary.cuts} cuts`;
});

$("strip").addEventListener("click", e => {
    if (!plan.shots.length) return;
    const box = $("strip").getBoundingClientRect();
    const at = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
    Buddy.send("seek", {frame: Math.round(plan.span[0] + at * (plan.span[1] - plan.span[0]))});
});

// ----------------------------------------------------------- cut settings --

Buddy.on("cut", c => { cut = c; });

function cutNumber(key, label, min, max, stepBy) {
    return el("label.lbl", {}, [label, el("input.field", {type: "number", min, max, step: stepBy, value: cut[key],
        onchange: e => { cut[key] = Number(e.target.value); Buddy.send("cut", {key, value: Number(e.target.value)}); }})]);
}

function cutCheck(key, label) {
    return el("label.check", {}, [el("input", {type: "checkbox", checked: !!cut[key],
        onchange: e => { cut[key] = e.target.checked; Buddy.send("cut", {key, value: e.target.checked}); }}), label]);
}

function cutGroup(title, items) {
    return el("div.sw-cut-group", {}, [el("div.section-title", {text: title}), el("div.sw-options", {}, items)]);
}

$("cut-settings").addEventListener("click", () => Buddy.modal({
    title: "Cut settings",
    wide: true,
    body: [
        cutGroup("Every cut", [cutNumber("min_shot", "Shortest shot (s)", 0.5, 20, 0.5),
                               cutNumber("lead", "Cut ahead (s)", 0, 1, 0.05),
                               cutNumber("ignore", "Ignore talk under (s)", 0, 5, 0.1)]),
        cutGroup("Reaction shots", [cutCheck("reaction", "Show the listener in long answers"),
                                    cutNumber("react_after", "After (s)", 3, 600, 1),
                                    cutNumber("react_len", "For (s)", 0.5, 20, 0.5)]),
        cutGroup("Wide shot", [cutCheck("crosstalk", "When people talk at once"),
                               cutCheck("quiet", "During long silences"),
                               cutNumber("ends", "First and last (s)", 0, 60, 1),
                               cutNumber("cutaway", "Cutaway every (s)", 0, 600, 5),
                               cutNumber("cut_len", "For (s)", 0.5, 20, 0.5)]),
        cutGroup("Second angle", [cutNumber("every", "Take turns every (s)", 2, 600, 1)]),
        cutGroup("Random cutaway", [cutNumber("gap_min", "Every (s)", 3, 1800, 1),
                                    cutNumber("gap_max", "To (s)", 3, 1800, 1),
                                    cutNumber("length", "For (s)", 0.5, 20, 0.5)]),
    ],
    buttons: [{label: "Done", kind: "accent"}],
}));

// ----------------------------------------------------------------- setups --

$("setup").addEventListener("change", e => Buddy.send("setup_pick", {id: e.target.value}));
$("setup-more").append(Buddy.icon("more"));
$("analyse").append(Buddy.icon("refresh"));

function askName(title, ok, value, action) {
    const input = el("input.field", {type: "text", value: value || "", maxlength: 60, "aria-label": "Name"});
    let dialog = null;
    const done = close => { Buddy.send(action, {name: input.value}); close(); };
    input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); done(dialog.close); } });
    dialog = Buddy.modal({title, body: input, buttons: [{label: "Cancel"}, {label: ok, kind: "accent", onClick: done}]});
}

$("setup-more").addEventListener("click", e => {
    const box = e.currentTarget.getBoundingClientRect();
    const current = setup.setups.find(x => x.id === setup.setup) || {name: ""};
    Buddy.menu({x: box.left, y: box.bottom + 4, items: [
        {label: "New setup from this timeline…", onClick: () => askName("New setup", "Make", "", "setup_new")},
        {label: "Save a copy…", onClick: () => askName("Save a copy", "Save", current.name, "setup_copy")},
        {label: "Rename…", onClick: () => askName("Rename setup", "Rename", current.name, "setup_rename")},
        {sep: true},
        {label: "Delete this setup", danger: true, disabled: setup.setups.length < 2,
         onClick: async () => {
             if (await Buddy.confirm({title: "Delete this setup?", text: "Timelines that used it use another setup.",
                                      ok: "Delete", danger: true})) Buddy.send("setup_delete");
         }},
    ]});
});

// --------------------------------------------------------------- actions --

$("analyse").addEventListener("click", () => Buddy.send("analyse"));
$("cancel").addEventListener("click", () => Buddy.send("cancel"));
$("engine").addEventListener("click", () => Buddy.send("engine"));
$("download").addEventListener("click", () => Buddy.send("download"));

Buddy.on("toast", t => Buddy.toast(t.text));
Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}),
                                    buttons: [{label: "OK", kind: "accent"}]}));

showStep(1);
