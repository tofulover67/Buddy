"use strict";

// Auto Switch's view (page.py says what each event carries), all on one
// screen: the angles - a card each, with its name, the video it shows and,
// for a person's angle, the audio that says when they talk; what an angle
// with no audio is for, and each angle's own rules behind its button - then
// how it will cut, and Build. Python owns the state; this draws it and says
// what was clicked.

const $ = id => document.getElementById(id);
const el = Buddy.el;
const COLORS = ["#4e9be0", "#e0884e", "#5bbf7a", "#c66bd1", "#d6c14e", "#5ec9c9", "#e06a7a", "#9a8be0"];
const PEOPLE = [["1", "1 person"], ["2", "2 people"], ["3", "3 people"], ["4", "4 people"],
                ["5", "5 people"], ["6", "6 people"], ["auto", "Find out"]];

let state = {}, setup = {video: [], audio: [], mics: [], cameras: [], setups: [], setup: "", most: 16},
    plan = {shots: [], rows: []}, cut = {};

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
    return cam ? (cam.name || cam.file || cam.label) : "";
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

function drawBuild() {
    $("build").disabled = busy() || !plan.shots.length;
}

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
    drawBuild();
});

// ----------------------------------------------------------------- angles --

function sendCam(cam, key, value) {
    Buddy.send("cam", {id: cam.id, key, value});
}

Buddy.on("setup", s => {
    setup = s;
    const pick = $("setup");
    pick.replaceChildren(...s.setups.map(x => el("option", {value: x.id, text: x.name, translate: "no"})));
    pick.value = s.setup;
    drawAngles();
    drawPlan();          // its key shows the angles' names
});

function useOf(cam) {
    return cam.role === "angle" ? `angle:${cam.of || ""}` : cam.role;
}

function micOf(cam) {
    return setup.mics.find(m => m.index === cam.audio[0]);
}

function voiceOf(cam) {
    for (const mic of setup.mics) {
        const voice = mic.voices.find(v => v.id === cam.voice);
        if (voice) return voice;
    }
    return null;
}

// A card per angle: a still of its video, its name, the video and the audio
// it's made of, and - with no audio - what it's for. The gear holds its
// rules; the bin takes it out of the setup.
function angleCard(cam) {
    const field = name => `${cam.id}:${name}`;
    const video = el("select.field", {"aria-label": "Video", "data-f": field("track"),
                                      onchange: e => sendCam(cam, "track", e.target.value === "" ? null : Number(e.target.value))},
        [el("option", {value: "", text: "No video"}),
         ...setup.video.map(v => el("option", {value: String(v.index), text: v.label, translate: "no",
                                               selected: cam.track === v.index}))]);
    if (cam.track == null) video.value = "";

    const several = cam.audio.length > 1;      // an older setup's: kept until another is picked
    const audio = el("select.field", {"aria-label": "Audio", "data-f": field("audio"),
                                      onchange: e => {
                                          if (e.target.value === "keep") return;
                                          sendCam(cam, "audio", e.target.value === "" ? [] : [Number(e.target.value)]);
                                      }},
        [el("option", {value: "", text: "No audio"}),
         several ? el("option", {value: "keep", selected: true, translate: "no",
                                 text: cam.audio.map(a => (setup.audio.find(x => x.index === a) || {label: `A${a}`}).label).join(" + ")}) : null,
         ...setup.audio.map(a => el("option", {value: String(a.index), text: a.label, translate: "no",
                                               selected: !several && cam.audio[0] === a.index}))]);
    if (!cam.audio.length) audio.value = "";

    const voice = voiceOf(cam), mic = micOf(cam);
    const hear = el("button.btn.ghost.icon", {type: "button", title: "Hear it in Resolve", "aria-label": "Hear it",
                                              disabled: !voice || voice.sample == null,
                                              onclick: () => Buddy.send("seek", {frame: voice.sample})}, [Buddy.icon("play")]);
    const rows = [
        el("label.lbl", {}, ["Video", video]),
        el("label.lbl", {}, ["Audio", el("div.sw-pair", {}, [audio, cam.audio.length ? hear : null])]),
    ];
    if (cam.audio.length) {
        if (mic && mic.voices.length > 1) {       // several people on this mic: which one this angle shows
            const which = el("select.field", {"aria-label": "Voice", "data-f": field("voice"),
                                              onchange: e => Buddy.send("voice", {id: cam.id, voice: e.target.value})},
                [voice ? null : el("option", {value: "", text: "No voice"}),
                 ...mic.voices.map(v => el("option", {value: v.id, text: v.which || v.id, selected: v.id === cam.voice}))]);
            if (!voice) which.value = "";
            rows.push(el("label.lbl", {}, ["Voice", which]));
        }
        rows.push(el("div.sw-when", {}, [
            el("span", {text: "On screen when they talk"}),
            voice ? el("span.muted.small", {text: `${Math.round(voice.share * 100)}% of the talk`}) : null,
        ]));
    } else {
        const speakers = setup.cameras.filter(c => c.role === "speaker" && c.id !== cam.id);
        const use = el("select.field", {"aria-label": "Used as", "data-f": field("role"), onchange: e => {
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
        rows.push(el("label.lbl", {}, ["Used as", use]));
    }

    const still = thumb(cam);
    if (cam.track != null) still.style.borderBottomColor = cameraColor(cam.track);
    return el("div.sw-cam", {}, [
        still,
        el("div.sw-cam-head", {}, [
            el("input.field.sw-name", {type: "text", value: cam.name, maxlength: 60, translate: "no",
                                       placeholder: "Name", "aria-label": "Name", "data-f": field("name"),
                                       onchange: e => sendCam(cam, "name", e.target.value)}),
            el("button.btn.ghost.icon", {type: "button", title: "When this angle cuts in", "aria-label": "Rules",
                                         "data-f": field("rules"), onclick: () => openRules(cam)}, [Buddy.icon("sliders")]),
            el("button.btn.ghost.icon", {type: "button", title: "Remove this angle", "aria-label": "Remove",
                                         onclick: () => removeAngle(cam)}, [Buddy.icon("trash")]),
        ]),
        ...rows,
    ]);
}

function drawAngles() {
    const box = $("cams");
    // Drawn again after every change: the field being used keeps the focus.
    const active = box.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    box.replaceChildren(...setup.cameras.map(angleCard));
    box.append(el("button.sw-add", {type: "button", disabled: setup.cameras.length >= setup.most,
                                    onclick: () => Buddy.send("angle_add")},
        [Buddy.icon("plus"), el("span", {text: "Add angle"})]));
    if (active) {
        const node = [...box.querySelectorAll("[data-f]")].find(n => n.getAttribute("data-f") === active);
        if (node) node.focus();
    }
}

async function removeAngle(cam) {
    if (await Buddy.confirm({title: "Remove this angle?",
                             text: "It's taken out of this setup. Nothing on your timeline changes.",
                             ok: "Remove", danger: true})) Buddy.send("angle_remove", {id: cam.id});
}

// ------------------------------------------------------------------ rules --

// An angle's own rules - when it cuts in - in a small panel.
function ruleNumber(cam, key, label, min, max, stepBy) {
    return el("label.lbl", {}, [label, el("input.field", {type: "number", min, max, step: stepBy, value: cam.rules[key],
        onchange: e => { cam.rules[key] = Number(e.target.value); sendCam(cam, key, Number(e.target.value)); }})]);
}

function ruleCheck(cam, key, label) {
    return el("label.check", {}, [el("input", {type: "checkbox", checked: !!cam.rules[key],
        onchange: e => { cam.rules[key] = e.target.checked; sendCam(cam, key, e.target.checked); }}), label]);
}

function cutGroup(title, items) {
    return el("div.sw-cut-group", {}, [el("div.section-title", {text: title}), el("div.sw-options", {}, items)]);
}

function openRules(cam) {
    const body = [];
    if (cam.role === "speaker") {
        const mic = micOf(cam);
        body.push(el("p.modal-text", {text: "This angle is on screen whenever its person talks."}));
        if (mic) {
            body.push(cutGroup("Its audio", [el("label.lbl", {}, ["People on this mic",
                el("select.field", {onchange: e => Buddy.send("people", {track: mic.index, value: e.target.value})},
                   PEOPLE.map(([value, label]) => el("option", {value, text: label, selected: value === mic.people})))])]));
        }
        body.push(cutGroup("Reaction shots", [ruleCheck(cam, "reaction", "Cut to them listening in long answers"),
                                              ruleNumber(cam, "react_after", "After (s)", 3, 600, 1),
                                              ruleNumber(cam, "react_len", "For (s)", 0.5, 20, 0.5)]));
    } else if (cam.role === "wide") {
        body.push(cutGroup("Cut to it", [ruleCheck(cam, "crosstalk", "When people talk at once"),
                                         ruleCheck(cam, "quiet", "During long silences")]));
        body.push(cutGroup("At the start and the end", [ruleNumber(cam, "ends", "First and last (s)", 0, 60, 1)]));
        body.push(cutGroup("In long shots", [ruleNumber(cam, "cutaway", "Cutaway every (s)", 0, 600, 5),
                                             ruleNumber(cam, "cut_len", "For (s)", 0.5, 20, 0.5)]));
    } else if (cam.role === "angle") {
        body.push(cutGroup("Second angle", [ruleNumber(cam, "every", "Take turns every (s)", 2, 600, 1)]));
    } else if (cam.role === "cutaway") {
        body.push(cutGroup("Random cutaway", [ruleNumber(cam, "gap_min", "Every (s)", 3, 1800, 1),
                                              ruleNumber(cam, "gap_max", "To (s)", 3, 1800, 1),
                                              ruleNumber(cam, "length", "For (s)", 0.5, 20, 0.5)]));
    } else {
        body.push(el("p.modal-text", {text: "This angle isn't used. Pick what it's used as on its card, or give it audio."}));
    }
    Buddy.modal({title: "When this angle cuts in", body, buttons: [{label: "Done", kind: "accent"}]});
}

// ---------------------------------------------------------------- preview --

function place(node, a, b, span) {
    const total = Math.max(1, span[1] - span[0]);
    node.style.left = `${((a - span[0]) / total) * 100}%`;
    node.style.width = `${Math.max(0.05, ((b - a) / total) * 100)}%`;
    return node;
}

function drawPlan() {
    const p = plan;
    $("preview").hidden = !p.shots.length;
    drawBuild();
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
}

Buddy.on("plan", p => {
    plan = p;
    drawPlan();
});

$("strip").addEventListener("click", e => {
    if (!plan.shots.length) return;
    const box = $("strip").getBoundingClientRect();
    const at = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
    Buddy.send("seek", {frame: Math.round(plan.span[0] + at * (plan.span[1] - plan.span[0]))});
});

// ----------------------------------------------------------------- pacing --

Buddy.on("cut", c => { cut = c; });

function cutNumber(key, label, min, max, stepBy) {
    return el("label.lbl", {}, [label, el("input.field", {type: "number", min, max, step: stepBy, value: cut[key],
        onchange: e => { cut[key] = Number(e.target.value); Buddy.send("cut", {key, value: Number(e.target.value)}); }})]);
}

$("cut-settings").addEventListener("click", () => Buddy.modal({
    title: "Pacing",
    body: [
        cutGroup("Every cut", [cutNumber("min_shot", "Shortest shot (s)", 0.5, 20, 0.5),
                               cutNumber("lead", "Cut ahead (s)", 0, 1, 0.05),
                               cutNumber("ignore", "Ignore talk under (s)", 0, 5, 0.1)]),
        el("p.muted.small", {text: "When each angle cuts in is set on its own card."}),
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

$("build").addEventListener("click", () => Buddy.send("build"));
