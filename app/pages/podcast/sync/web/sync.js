"use strict";

// Podcast Sync's view (see ../page.py for the protocol). Python owns the
// state; this draws what it's sent and says what was clicked. Text is
// written in English and translated by buddy.js as it's drawn.

const $ = id => document.getElementById(id);
const el = Buddy.el;
let state = {};
let devices = null;
let job = null;
let jobClock = 0;

function length(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, r = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

function segmented(id, key) {
    for (const b of $(id).querySelectorAll("button[data-value]")) {
        b.onclick = () => Buddy.send("option", {key, value: b.dataset.value});
    }
}

function pressed(id, value) {
    for (const b of $(id).querySelectorAll("button[data-value]")) {
        b.setAttribute("aria-pressed", String(b.dataset.value === value));
    }
}

segmented("source", "source");
segmented("method", "method");
segmented("target", "target");
$("put-back").onclick = () => Buddy.send("put_back");
$("fine").onchange = e => Buddy.send("option", {key: "fine", value: e.target.checked});
$("camera-audio").onchange = e => Buddy.send("option", {key: "camera_audio", value: e.target.value});
$("scan").onclick = () => Buddy.send("scan");
$("sync").onclick = () => Buddy.send("sync");
$("cancel").onclick = () => Buddy.send("cancel");

// --------------------------------------------------------------- state

function drawEnabled() {
    const busy = Boolean(job);
    const needsFfmpeg = state.method === "waveform" && !state.ffmpeg;
    $("scan").disabled = busy;
    $("sync").disabled = busy || needsFfmpeg;
    $("ffmpeg-note").hidden = !needsFfmpeg;
}

Buddy.on("state", s => {
    state = s;
    pressed("source", s.source);
    pressed("method", s.method);
    // Where the sync goes: a new timeline, or the open one synced where it is
    // (which reads the open timeline, whatever was picked above).
    const here = s.target === "here";
    pressed("target", s.target);
    $("target-note").textContent = here ? "Each camera and recorder needs to be on its own track. Their clips are moved into sync along it – nothing is cut or deleted."
        : "You get a new timeline. Nothing you already have is changed.";
    $("sync").textContent = here ? "Sync this timeline" : "Build synced timeline";
    $("put-back").disabled = Boolean(s.busy);
    $("fine").checked = Boolean(s.fine);
    $("fine-box").hidden = s.method !== "timecode";
    $("camera-audio").value = s.camera_audio;
    // How it will sync, in a line: the choices themselves open under "Change".
    const by = s.method === "waveform" ? "Syncing by sound" : s.fine ? "Syncing by timecode, fine-tuned with sound" : "Syncing by timecode";
    const own = {tracks: "cameras' own sound kept", muted: "cameras' own sound kept, turned off", none: "cameras' own sound left out"}[s.camera_audio];
    $("how-summary").replaceChildren(el("span", {text: by}), el("span", {text: " · "}), el("span", {text: own || ""}));
    drawEnabled();
});

// ------------------------------------------------------------- devices

function deviceRow(device, index, all, master) {
    const name = el("input.field.ps-name", {type: "text", value: device.name, maxlength: "60", spellcheck: "false",
                                            "aria-label": "Device name", translate: "no"});
    name.addEventListener("change", () => Buddy.send("rename_device", {device: device.name, name: name.value}));
    name.addEventListener("keydown", e => { if (e.key === "Enter") name.blur(); });

    const facts = [`${device.clips.length === 1 ? "1 clip" : `${device.clips.length} clips`}`, length(device.seconds)];
    if (device.timecode) facts.push(device.timecode === device.clips.length ? "timecode" : `timecode on ${device.timecode}`);
    if (!device.sound) facts.push("no sound");

    const clips = el("details.ps-clips", {}, [
        el("summary", {text: "Clips"}),
        ...device.clips.map(c => {
            const move = el("select.field", {"aria-label": "Device", title: "Move this clip to another device"});
            for (const d of all) move.append(el("option", {value: d.name, text: d.name, translate: "no", selected: d.name === device.name}));
            move.append(el("option", {value: "__new__", text: "A new device"}));
            move.onchange = () => Buddy.send("move_clip", {path: c.path, device: move.value});
            return el("div.ps-clip", {}, [
                el("span.ps-clip-name", {text: c.name, title: c.path, translate: "no"}),
                el("span.muted", {text: length(c.seconds)}),
                move,
            ]);
        }),
    ]);

    // Everything done to a device now and then - its place in the order, the
    // master clock, its clips - is behind one button.
    const isMaster = master === device.name;
    const more = el("button.btn.ghost.icon", {type: "button", title: "Order, master clock and clips", "aria-label": "Device options",
        onclick: e => {
            const box = e.currentTarget.getBoundingClientRect();
            Buddy.menu({x: box.left, y: box.bottom + 4, items: [
                {label: "Move up", disabled: index === 0 || all[index - 1].kind !== device.kind,
                 onClick: () => Buddy.send("move_device", {device: device.name, step: -1})},
                {label: "Move down", disabled: index === all.length - 1 || all[index + 1].kind !== device.kind,
                 onClick: () => Buddy.send("move_device", {device: device.name, step: 1})},
                {sep: true},
                {label: "Make this the master clock", disabled: !device.sound || isMaster,
                 onClick: () => Buddy.send("master", {device: device.name})},
                {label: clips.open ? "Hide its clips" : "Show its clips", onClick: () => { clips.open = !clips.open; }},
            ]});
        }}, [Buddy.icon("more")]);

    return el("div.ps-device", {}, [
        el("div.ps-device-head", {}, [
            el("span.ps-kind", {title: device.kind === "mic" ? "Mic" : "Camera"}, [Buddy.icon(device.kind === "mic" ? "mic" : "camera")]),
            el("div.ps-device-text", {}, [
                name,
                // One span each, so each is translated on its own.
                el("span.muted.small", {}, facts.flatMap((f, i) => i ? [el("span", {text: " · "}), el("span", {text: f})]
                                                                      : [el("span", {text: f})])),
            ]),
            isMaster ? el("span.ps-master", {text: "Master clock", title: "Every other recording is measured against this one"}) : null,
            more,
        ].filter(Boolean)),
        clips,
    ]);
}

Buddy.on("devices", d => {
    devices = d;
    $("devices-card").hidden = !d;
    $("scan-what").textContent = !d ? "" : d.what.kind === "selected" ? "Read from the selected clips."
        : d.what.kind === "bin" ? `Read from the bin "${d.what.name}".` : `Read from the timeline "${d.what.name}".`;
    const box = $("devices");
    box.replaceChildren();
    if (!d) return;
    d.devices.forEach((device, i) => box.append(deviceRow(device, i, d.devices, d.master)));
});

// ----------------------------------------------------------------- job

function drawClock() {
    if (!job) return;
    $("job-clock").textContent = length(job.seconds + (Date.now() - job.at) / 1000);
}

Buddy.on("job", j => {
    job = j ? {...j, at: Date.now()} : null;
    $("job").hidden = !j;
    clearInterval(jobClock);
    drawEnabled();
    if (!j) return;
    $("job-label").textContent = j.label;
    const counted = j.total > 1;
    const step = Math.min(j.done + 1, j.total);
    $("job-bar").style.width = counted ? `${(step / j.total) * 100}%` : "";
    $("job-bar").parentElement.classList.toggle("indeterminate", !counted);
    $("job-stage").textContent = j.stage ? (counted ? `${j.stage} (${step} of ${j.total})` : `${j.stage}…`) : "";
    $("cancel").disabled = j.stage === "Stopping";
    drawClock();
    jobClock = setInterval(drawClock, 1000);
});

// -------------------------------------------------------------- result

function table(head, rows) {
    return el("table.ps-table", {}, [
        el("thead", {}, el("tr", {}, head.map(h => el("th", {text: h})))),
        el("tbody", {}, rows),
    ]);
}

Buddy.on("result", r => {
    $("result").hidden = !r;
    if (!r) return;
    $("result-name").textContent = r.timeline || "";
    const video = r.tracks.filter(t => t.kind === "Video").length;
    const audio = r.tracks.filter(t => t.kind === "Audio").length;
    $("result-summary").textContent =
        (r.in_place ? `${r.placed} of ${r.total} clips in sync on this timeline - ${r.moved} moved.`
                    : `${r.placed} of ${r.total} clips synced, on ${video} video and ${audio} audio tracks.`) +
        (r.master && r.fine ? ` Measured against ${r.master}.` : "");
    $("put-back").hidden = !(r.in_place && r.can_undo);

    const drift = $("result-drift");
    drift.replaceChildren();
    if (r.drift.length) {
        drift.append(el("div.ps-sub", {text: "Drift"}), table(
            ["Clip", "Against", "ms per hour", "Ends out by"],
            r.drift.map(row => el(`tr${row.warn ? ".warn" : ""}`, {}, [
                el("td", {text: row.clip, translate: "no"}),
                el("td", {text: row.against, translate: "no"}),
                el("td.num", {text: Math.abs(row.ms_per_hour).toFixed(0)}),
                el("td.num", {text: row.warn ? `${(row.frames / 2).toFixed(1)} frames` : "under a frame"}),
            ]))));
        if (r.drift.some(row => row.warn)) {
            drift.append(el("p.muted.small", {text: "Each clip is placed by its middle, so a drifting one is " +
                "out by up to half its drift at its start and end. Check those ends, or nudge the clip there."}));
        }
    } else if (r.fine) {
        drift.append(el("p.muted.small", {text: "No drift measured - there wasn't enough shared sound to check."}));
    }

    const unplaced = $("result-unplaced");
    unplaced.replaceChildren();
    if (r.unplaced.length) {
        unplaced.append(el("div.ps-sub", {text: r.in_place ? "Not synced - left where they were" : "Not synced - put after the rest"}), table(
            ["Clip", "Device", "Why"],
            r.unplaced.map(row => el("tr", {}, [
                el("td", {text: row.clip, translate: "no"}),
                el("td", {text: row.device, translate: "no"}),
                el("td", {text: row.why}),
            ]))));
    }
    $("result-warnings").replaceChildren(...r.warnings.map(w => el("li", {text: w})));
});

// ----------------------------------------------------------------- log

Buddy.on("log", lines => {
    $("activity").hidden = !lines.length;
    $("log").replaceChildren(...lines.map(l => el(`li.k-${l.kind}`, {}, [
        el("span.time", {text: l.time}), el("span", {text: l.text}),
    ])));
    const log = $("log");
    log.scrollTop = log.scrollHeight;
});

Buddy.on("alert", a => {
    Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}), buttons: [{label: "OK", kind: "accent"}]});
});
