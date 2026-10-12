/*
 * Footage Search's view. Python owns the Media Pool's clips, the index and
 * what's found; this draws where things stand, the moments found for a
 * description, and a transcript's lines with the moments suggested for each.
 * Frames arrive one by one after the rows they belong to ("thumb").
 */
"use strict";

const $ = id => document.getElementById(id);
const {el, icon, send} = Buddy;

let state = null;
let results = {query: "", finding: false, rows: null};
let broll = {lines: null, suggesting: false, name: "", timeline: ""};

for (const b of document.querySelectorAll(".views [data-tab]")) b.onclick = () => send("tab", {tab: b.dataset.tab});
$("reload").onclick = () => send("load_transcript");

let queryTimer = 0;
$("query").addEventListener("input", () => {
    clearTimeout(queryTimer);
    queryTimer = setTimeout(() => send("find", {query: $("query").value}), 450);   // looked up by a model: not at every letter
});
$("query").addEventListener("keydown", e => {
    if (e.key === "Enter") { clearTimeout(queryTimer); send("find", {query: $("query").value}); }
});

const clips = n => n === 1 ? "1 clip" : `${n} clips`;
const button = (text, message, kind, payload) =>
    el(`button.btn${kind || ".ghost"}`, {type: "button", text, onclick: () => send(message, payload)});

// ----------------------------------------------------------------- status

function drawStatus() {
    const s = state, d = s.search, i = d.index;
    for (const b of document.querySelectorAll(".views [data-tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === s.tab));
    $("pane-search").hidden = s.tab !== "search";
    $("pane-broll").hidden = s.tab !== "broll";

    const project = $("project"), line = $("index-line"), actions = [];
    project.classList.toggle("bad", false);
    if (s.problem) project.replaceChildren(el("span", {text: s.problem}));
    else if (s.loading) project.replaceChildren(el("span", {text: "Reading the Media Pool…"}));
    else project.replaceChildren(el("span", {text: s.project, translate: "no"}), " – ", el("span", {text: clips(s.clips)}));

    // Each sentence a node of its own: that's what gets translated.
    let said = [], bad = false;
    if (s.index_trouble) { said = [s.index_trouble]; bad = true; }
    else if (!d.supported) { said = [d.why]; bad = true; }
    else if (d.setting_up) { said = [d.setup_text || "Setting up…"]; actions.push(button("Cancel", "cancel_setup")); }
    else if (!d.ready) {
        said = [d.setup_error || `Finding footage by describing it needs a model that runs on this computer – a one-time download of ${d.download_mb} MB.`];
        bad = !!d.setup_error;
        actions.push(button("Set up…", "setup", ".accent"));
    } else if (!d.ffmpeg) {
        said = ["Reading footage needs ffmpeg installed on this computer – Buddy doesn't come with it."];
        bad = true;
    } else if (i.running) {
        const at = Math.min(i.done + 1, i.total);
        said = [i.part > 0 ? `Indexing ${at} of ${i.total} – ${i.current} (${Math.round(i.part * 100)}%)`
                           : `Indexing ${at} of ${i.total} – ${i.current}`];
        actions.push(button("Stop", "stop"));
    } else if (i.error) { said = [i.error]; bad = true; actions.push(button("Try again", "index")); }
    else if (!s.problem && !s.loading) {
        said = [s.clips ? `${s.indexed} of ${s.clips} indexed.` : "No footage or stills in this project's Media Pool yet."];
        if (s.failed) said.push(`${s.failed} couldn't be read.`);
        if (s.offline) said.push(`${s.offline} offline.`);
        if (s.other) said.push(`${s.other} more are in a format Buddy can't read.`);
        if (s.index_folder) said.push(`Index kept in ${s.index_folder}.`);
        if (d.gpu) said.push(`Running on ${d.gpu}.`);
        else if (d.gpu_note) said.push("The graphics card couldn't run it, so the processor is doing the work.");
        if (s.waiting) actions.push(button(s.waiting === 1 ? "Index 1 clip" : `Index ${s.waiting} clips`, "index", ".accent"));
        if (d.faster_mb) actions.push(button(`Use the graphics card (${d.faster_mb} MB)`, "setup"));
    }
    line.replaceChildren(...said.flatMap((text, n) => n ? [" ", el("span", {text})] : [el("span", {text})]));
    line.classList.toggle("bad", bad);
    // Refresh first: it's always there, so it stays put while the button beside it comes and goes.
    const place = el("button.btn.ghost.icon", {type: "button", title: "Where this project's index is kept", disabled: !s.project || i.running,
        onclick: () => {
            const at = place.getBoundingClientRect();
            Buddy.menu({x: at.left, y: at.bottom + 4, items: [
                {heading: "Keep this project's index"},
                {label: "In Buddy's folder", checked: !s.index_folder, onClick: () => send("index_place", {where: "buddy"})},
                {label: s.index_folder ? "In another folder…" : "In a folder you choose…", checked: !!s.index_folder,
                 onClick: () => send("index_place", {where: "folder"})},
            ]});
        }}, icon("folder"));
    actions.unshift(el("button.btn.ghost.icon", {type: "button", title: "Read the Media Pool again", onclick: () => send("refresh")}, icon("refresh")), place);
    $("status-actions").replaceChildren(...actions);
}

// ---------------------------------------------------------------- moments

function shot(row, line) {
    const frame = el("div.frame", {}, row.thumb ? el("img", {src: row.thumb, alt: ""}) : icon(row.still ? "image" : "film"));
    const place = line === undefined ? {clip: row.clip, at: row.at} : {clip: row.clip, at: row.at, line};
    return el("div.shot", {dataset: {key: row.key}}, [
        frame,
        el("div.meta", {}, [
            el("div.name", {text: row.name, translate: "no", title: row.name}),
            el("div.where", {title: row.bin}, [row.time ? el("span", {text: row.time, translate: "no"}) : null, row.time ? " · " : null,
                                               el("span", {text: row.bin, translate: "no"})]),
        ]),
        el("div.acts", {}, [
            el("button.btn.ghost", {type: "button", text: "Find in Media Pool", onclick: () => send("reveal", {clip: row.clip})}),
            el("button.btn", {type: "button", text: "Place",
                              title: line === undefined ? "Puts a few seconds from here on the timeline, at the playhead, on a track above V1"
                                                        : "Puts it on the timeline over this line, on a track above V1",
                              onclick: () => send("place", place)}),
        ]),
    ]);
}

const empty = (title, text) => el("div.empty", {}, [el("div.strong", {text: title}), text ? el("div.small", {text}) : null]);
const waiting = () => el("div.empty", {}, el("span.dots", {}, [el("i"), el("i"), el("i")]));

function drawResults() {
    const box = $("results"), r = results;
    if (document.activeElement !== $("query") && r.query !== undefined) $("query").value = r.query;
    if (r.finding) box.replaceChildren(waiting());
    else if (r.rows === null) {
        box.replaceChildren(empty("Describe a shot to find it",
            "“A drone shot over water”, “two people shaking hands”, “a close-up of hands typing”. Only indexed clips can be found."));
    } else if (!r.rows.length) box.replaceChildren(empty("Nothing like that in the indexed clips", "Try describing what's in the picture another way."));
    else box.replaceChildren(el("div.shots", {}, r.rows.map(row => shot(row))));
}

function drawBroll() {
    const box = $("lines"), b = broll;
    const note = $("broll-note");
    if (b.lines === null) {
        note.textContent = "";
        box.replaceChildren(empty("No transcript yet",
            "Transcribe the timeline in Subtitles first. Buddy then suggests footage for what's said, line by line."));
        return;
    }
    note.replaceChildren(el("span", {text: "Transcript:"}), " ", el("span", {text: b.name, translate: "no"}));
    box.replaceChildren(...b.lines.map(line => el("div.line", {}, [
        el("div.said", {}, [el("span.time", {text: line.time, translate: "no"}), el("span.text", {text: line.text, translate: "no"})]),
        line.picks ? (line.picks.length ? el("div.shots", {}, line.picks.map(row => shot(row, line.i)))
                                        : el("div.muted.small", {text: "Nothing indexed to suggest."}))
                   : b.suggesting ? el("span.dots", {}, [el("i"), el("i"), el("i")])
                                  : el("div.muted.small", {text: "Index the Media Pool's clips above, then read the transcript again."}),
    ])));
}

Buddy.on("state", s => { state = s; drawStatus(); });
Buddy.on("results", r => { results = r; drawResults(); });
Buddy.on("broll", b => { broll = b; drawBroll(); });
Buddy.on("thumb", t => {
    for (const row of results.rows || []) if (row.key === t.key) row.thumb = t.src;
    for (const line of broll.lines || []) for (const row of line.picks || []) if (row.key === t.key) row.thumb = t.src;
    for (const node of document.querySelectorAll(".shot")) {
        if (node.dataset.key === t.key) node.querySelector(".frame").replaceChildren(el("img", {src: t.src, alt: ""}));
    }
});
Buddy.on("toast", t => Buddy.toast(t.text, 3500));

drawResults();
