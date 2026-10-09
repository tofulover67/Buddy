/*
 * Project Setup's view. The bin list is typed here and reported to Python
 * as it changes; everything else - the tree preview, the folder's
 * contents, what's open in Resolve, jobs and their logs - comes from
 * page.py, which makes every decision.
 */
"use strict";

const $ = id => document.getElementById(id);
const {el, icon, send} = Buddy;

let state = {tab: "bins", connected: false, busy: false};
let help = {};
let sync = null;
let imp = null;
let pop = null;
let proxy = null;
let binRows = [];
let job = null;
let metadata = null;
const metadataInputs = new Map();

const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || word + "s")}`;

// ----------------------------------------------------------------- tabs

function showTab(tab) {
    for (const b of document.querySelectorAll(".tabs [data-tab]")) {
        b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    }
    for (const panel of document.querySelectorAll(".panel")) {
        panel.hidden = panel.id !== `panel-${tab}`;
    }
}

for (const b of document.querySelectorAll(".tabs [data-tab]")) {
    b.onclick = () => { state.tab = b.dataset.tab; showTab(state.tab); send("tab", {tab: state.tab}); };
}

// Buttons that act on Resolve say so with data-action; all of them are off
// while something is running.
for (const b of document.querySelectorAll("[data-action]")) {
    b.addEventListener("click", () => send(b.dataset.action));
}
for (const b of document.querySelectorAll("[data-help]")) {
    b.onclick = () => Buddy.modal({
        title: b.dataset.help === "import" ? "Import folder" : b.textContent.trim(),
        body: el("p.modal-text", {text: help[b.dataset.help] || ""}),
        buttons: [{label: "Got it", kind: "accent"}],
        wide: true,
    });
}

function applyEnabled() {
    // Each tab's own rules, then "nothing while busy" over the top.
    $("create-bins").disabled = !binRows.length;
    $("import-go").disabled = !(imp && imp.folder);
    $("pop-go").disabled = !(pop && pop.connected && pop.bin && pop.count);
    const noTimeline = !(sync && sync.connected && sync.timeline && !sync.error);
    for (const b of document.querySelectorAll("[data-needs-timeline]")) {
        b.disabled = noTimeline || (b.hasAttribute("data-needs-ffmpeg") && !sync.ffmpeg);
    }
    $("remove-gaps").disabled = noTimeline || !sync.can_remove_gaps;
    applyMetadataEnabled();
    // The Proxy tab's controls follow the proxy run (applyProxyEnabled); a
    // Sync job leaves them free, so proxies can start while it runs.
    if (state.busy) {
        for (const b of document.querySelectorAll("[data-action], #choose-folder, #rescan, #bins-reset")) {
            if (!b.closest("#panel-proxy")) b.disabled = true;
        }
    } else {
        $("choose-folder").disabled = $("rescan").disabled = $("bins-reset").disabled = false;
        for (const b of document.querySelectorAll("[data-action='connect'], [data-action='refresh']")) b.disabled = false;
    }
    const proxyHeld = !!(state.overlay || state.proxy_busy);
    for (const b of document.querySelectorAll("[data-scope], [data-resolution], #proxy-format")) b.disabled = proxyHeld;
    $("proxy-recursive").disabled = proxyHeld;
}

Buddy.on("state", s => {
    state = s;
    showTab(s.tab);
    applyEnabled();
    applyProxyEnabled();
});

Buddy.on("help", h => { help = h; });
Buddy.on("alert", a => Buddy.modal({
    title: a.title, body: el("p.modal-text", {text: a.text}), buttons: [{label: "OK", kind: "accent"}],
}));
Buddy.on("toast", t => Buddy.toast(t.text, 3000));

// ----------------------------------------------------------------- bins

const binText = $("bin-text");
let binTimer = 0;
binText.addEventListener("input", () => {
    clearTimeout(binTimer);
    binTimer = setTimeout(() => send("bins_text", {text: binText.value}), 120);
});
binText.addEventListener("keydown", e => {
    // Tab indents with '>' rather than leaving the box: that's what nesting is.
    if (e.key !== "Tab") return;
    e.preventDefault();
    const start = binText.value.lastIndexOf("\n", binText.selectionStart - 1) + 1;
    const pos = binText.selectionStart;
    if (e.shiftKey) {
        if (binText.value[start] === ">") {
            binText.setRangeText("", start, start + 1, "preserve");
            binText.selectionStart = binText.selectionEnd = Math.max(start, pos - 1);
        }
    } else {
        binText.setRangeText(">", start, start, "preserve");
        binText.selectionStart = binText.selectionEnd = pos + 1;
    }
    binText.dispatchEvent(new Event("input"));
});
$("bins-reset").onclick = async () => {
    if (await Buddy.confirm({title: "Reset the bin list?", text: "This puts back the starter list and replaces what you've typed.", ok: "Reset"})) {
        send("bins_reset");
    }
};

Buddy.on("bins_text", d => { binText.value = d.text; });

Buddy.on("bins", d => {
    binRows = d.rows;
    const tree = $("bin-tree");
    if (!d.rows.length) {
        tree.replaceChildren(el("div.empty", {}, [
            el("div.strong", {text: "No bins yet"}),
            el("div.small", {text: "Type a name on each line to see the bins here."}),
        ]));
    } else {
        tree.replaceChildren(...d.rows.map(r => el(
            `div.tree-row${r.depth ? ".nested" : ".top"}${r.adjusted ? ".adjusted" : ""}`,
            {style: `--depth: ${r.depth}`, title: !r.adjusted ? undefined : r.depth
                ? `Written with ${r.typed} '>' but there's no bin that deep above it, so it goes inside the bin above.`
                : `Written with ${r.typed} '>' but there's no bin that deep above it, so it goes at the top.`},
            [icon("folder"), el("span.name", {text: r.name, translate: "no"}),
             r.adjusted ? el("span.chip.small", {text: "moved up"}) : null],
        )));
    }
    const n = d.rows.length;
    $("bins-summary").replaceChildren(...(n ? [el("span", {text: plural(n, "bin")}),
        ...(d.adjusted ? [" · ", el("span", {text: `${d.adjusted} moved up a level`})] : [])] : []));
    $("create-bins").textContent = n ? `Create ${plural(n, "bin")}` : "Create bins";
    applyEnabled();
});

// --------------------------------------------------------------- import

$("import-badge").append(icon("folder"));
$("rescan").append(icon("refresh"));
$("choose-folder").onclick = () => send("choose_folder");
$("rescan").onclick = () => send("rescan");
for (const b of document.querySelectorAll("#dest [data-master]")) {
    b.onclick = () => send("to_master", {on: b.dataset.master === "true"});
}

function stat(n, label) {
    return el(`div.stat${n ? "" : ".zero"}`, {}, [el("b", {text: n.toLocaleString()}), el("span.muted.small", {text: label})]);
}

Buddy.on("import", d => {
    imp = d;
    const name = $("import-name");
    name.translate = !d.folder;   // a folder's name is the user's
    name.textContent = d.folder ? d.name : "No folder chosen";
    name.classList.toggle("none", !d.folder);
    const path = $("import-path");
    path.replaceChildren(d.folder ? el("bdi", {text: d.folder, translate: "no"}) : "Choose the folder you want to import.");
    path.title = d.folder || "";
    $("rescan").hidden = !d.folder;
    $("choose-folder").textContent = d.folder ? "Change…" : "Choose folder…";

    const stats = $("import-stats");
    const s = d.summary;
    if (!s) {
        stats.replaceChildren();
    } else {
        const parts = [stat(s.video, "video"), stat(s.audio, "audio")];
        if (s.sequence) parts.push(stat(s.sequence, s.sequence === 1 ? "image sequence" : "image sequences"));
        parts.push(stat(s.image, s.image === 1 ? "still" : "stills"), stat(s.folders, s.folders === 1 ? "subfolder" : "subfolders"));
        const notes = [];
        if (s.skipped) notes.push(`${plural(s.skipped, "other file")} will be skipped`);
        if (s.partial) notes.push("big folder – counted the first part only");
        if (notes.length) {
            parts.push(el("span.muted.small.stats-note", {},
                          notes.flatMap((t, i) => i ? [" · ", el("span", {text: t})] : [el("span", {text: t})])));
        }
        stats.replaceChildren(...parts);
    }

    for (const b of document.querySelectorAll("#dest [data-master]")) {
        b.setAttribute("aria-pressed", String((b.dataset.master === "true") === d.to_master));
    }
    // One sentence per case, each a whole text node, so each translates.
    const binName = d.folder ? `"${d.name}"` : "The folder's bin";
    const dest = $("dest-text");
    if (d.to_master) {
        dest.textContent = `${binName} is created at the top of the Media Pool.`;
    } else if (d.destination === "Master") {
        dest.textContent = `${binName} is created inside Master (the top of the Media Pool).`;
    } else if (d.destination) {
        dest.textContent = `${binName} is created inside "${d.destination}", the bin open in Resolve.`;
    } else {
        dest.textContent = `${binName} is created inside whichever bin is open in Resolve's Media Pool.`;
    }

    const go = $("import-go");
    go.textContent = s && !s.partial && s.items ? `Import ${plural(s.items, "clip")}` : "Import";
    applyEnabled();
});

// ------------------------------------------------------------- populate

$("flow").querySelector(".flow-arrow").append(icon("arrow"));
$("pop-recursive").onchange = e => send("populate_recursive", {on: e.target.checked});

Buddy.on("populate", d => {
    pop = d;
    $("pop-recursive").checked = d.recursive;
    const bin = $("pop-bin");
    const tl = $("pop-timeline");
    const note = $("pop-timeline-note");
    if (!d.connected) {
        bin.textContent = "-"; tl.textContent = "-";
        $("pop-count").textContent = "Connect to Resolve to see the open bin.";
        note.textContent = "";
    } else {
        bin.translate = !d.bin;
        bin.textContent = d.bin || "No bin open";
        bin.classList.toggle("none", !d.bin);
        $("pop-count").textContent = d.count === null || d.count === undefined ? "" :
            d.count ? `${plural(d.count, "clip")} will be added` : "No media clips in this bin";
        tl.translate = !d.timeline;
        if (d.timeline) {
            tl.textContent = d.timeline;
            tl.classList.remove("none");
            note.textContent = "Added to the end of the open timeline.";
        } else {
            tl.textContent = d.bin ? `New timeline "${d.bin}"` : "A new timeline";
            tl.classList.add("none");
            note.textContent = "No timeline is open, so one is made from the clips.";
        }
    }
    $("pop-error").textContent = d.error || "";
    $("pop-go").textContent = d.count ? `Add ${plural(d.count, "clip")} to the timeline` : "Add to timeline";
    applyEnabled();
});

// ----------------------------------------------------------------- sync

$("tl-refresh").append(icon("refresh"));
for (const b of document.querySelectorAll("#method [data-method]")) {
    b.onclick = () => send("sync_option", {key: "method", value: b.dataset.method});
}
$("sync-audio").onchange = e => send("sync_option", {key: "sync_audio", value: e.target.checked});
$("delete-silent").onchange = e => send("sync_option", {key: "delete_silent", value: e.target.checked});
$("close-gaps").onchange = e => send("sync_option", {key: "close_gaps", value: e.target.checked});
$("steps-toggle").onclick = () => send("sync_option", {key: "steps_open", value: $("steps").hidden});
$("job-cancel").onclick = () => send("cancel_job", {slot: "main"});

Buddy.on("sync", d => {
    sync = d;
    const name = $("tl-name");
    const summary = $("tl-summary");
    name.translate = !(d.connected && d.timeline);   // a timeline's name is the user's
    if (!d.connected) {
        name.textContent = "Not connected";
        summary.textContent = "Connect to Resolve to sync the open timeline.";
    } else if (d.error) {
        name.textContent = d.timeline || "No timeline open";
        summary.textContent = d.error;
    } else if (d.timeline) {
        name.textContent = d.timeline;
        const s = d.summary;
        summary.replaceChildren(...(s ? [el("span", {text: plural(s.clips, "clip")}), " · ", el("span", {text: `${s.video} with picture`}),
                                          " · ", el("span", {text: `${s.audio_only} audio only`})] : []));
    } else {
        name.textContent = "No timeline open";
        summary.textContent = "";
    }
    name.classList.toggle("none", !(d.connected && d.timeline && !d.error));

    for (const b of document.querySelectorAll("#method [data-method]")) {
        b.setAttribute("aria-pressed", String(b.dataset.method === d.method));
    }
    $("sync-audio").checked = d.sync_audio;
    $("sync-audio-box").hidden = d.method === "waveform";   // Waveform already places every clip by audio
    $("delete-silent").checked = d.delete_silent;
    $("close-gaps").checked = d.close_gaps;
    $("steps").hidden = !d.steps_open;
    $("steps-toggle").setAttribute("aria-expanded", String(!!d.steps_open));
    $("remove-gaps").hidden = !d.can_remove_gaps;

    const needsFfmpeg = d.method === "waveform" || d.sync_audio;
    const note = $("ffmpeg-note");
    note.hidden = d.ffmpeg || !needsFfmpeg;
    note.textContent = d.method === "waveform"
        ? "Waveform sync needs ffmpeg, and Buddy can't find it. Install it, or set its path in Settings."
        : "Buddy can't find ffmpeg, so loose audio won't be placed. Install it, or set its path in Settings.";
    applyEnabled();
});

/* The jobs: one of the Sync tab's ("main") and one proxy run ("proxy") can
   run at once, each in its own box with its own Cancel and clock. */
const JOB_BOXES = {main: "", proxy: "proxy-"};
let jobs = {main: null, proxy: null};
// Per slot, the counted stretch now running - for "about 12 min left":
// {total, done, from, since} - from: how many were done when it was first
// seen (a view opened mid-run), since: when, in seconds.
const phases = {main: null, proxy: null};

const nowSeconds = () => Date.now() / 1000;
function span(seconds) {
    const s = Math.max(0, Math.floor(seconds)), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
             : `${m}:${String(s % 60).padStart(2, "0")}`;
}
function roughly(seconds) {
    if (seconds < 60) return "under a minute";
    const m = Math.round(seconds / 60);
    return m < 90 ? `about ${m} min` : `about ${(seconds / 3600).toFixed(1)} h`;
}

function drawClocks() {
    for (const [slot, prefix] of Object.entries(JOB_BOXES)) {
        const j = jobs[slot], clock = $(`${prefix}job-clock`);
        if (!j || !j.started_at) { clock.textContent = ""; continue; }
        const now = nowSeconds(), phase = phases[slot];
        let text = span(now - j.started_at);
        const seen = phase ? phase.done - phase.from : 0;
        if (phase && phase.total > 1 && seen >= 1 && now - phase.since >= 5) {
            text += ` · ${roughly((now - phase.since) / seen * (phase.total - phase.done))} left`;
        }
        clock.textContent = text;
    }
}
setInterval(() => { if (jobs.main || jobs.proxy) drawClocks(); }, 1000);

Buddy.on("jobs", all => {
    jobs = {main: all.main || null, proxy: all.proxy || null};
    job = jobs.main;
    for (const [slot, prefix] of Object.entries(JOB_BOXES)) {
        const j = jobs[slot];
        $(`${prefix}job`).hidden = !j;
        const phase = phases[slot];
        if (!j) { phases[slot] = null; continue; }
        // A new counted stretch (another total, or counting from the top
        // again) restarts what the estimate is measured from.
        if (!phase || phase.total !== j.total || j.done < phase.done) phases[slot] = {total: j.total, done: j.done, from: j.done, since: nowSeconds()};
        else phase.done = j.done;
        $(`${prefix}job-label`).textContent = j.label;
        const counted = j.total > 1;
        const step = Math.min(j.done + 1, j.total);
        const bar = $(`${prefix}job-bar`);
        bar.style.width = counted ? `${(step / j.total) * 100}%` : "";
        bar.parentElement.classList.toggle("indeterminate", !counted);
        $(`${prefix}job-stage`).textContent = j.stage ? (counted ? `${j.stage} (${step} of ${j.total})` : `${j.stage}…`) : "";
        $(`${prefix}job-cancel`).disabled = j.stage === "Stopping";
    }
    drawClocks();
});

// ----------------------------------------------------------------- proxy

$("proxy-job-cancel").onclick = () => send("cancel_job", {slot: "proxy"});

for (const b of document.querySelectorAll("#proxy-scope [data-scope]")) {
    b.onclick = () => send("proxy_option", {key: "scope", value: b.dataset.scope});
}
for (const b of document.querySelectorAll("#proxy-resolution [data-resolution]")) {
    b.onclick = () => send("proxy_option", {key: "resolution", value: b.dataset.resolution});
}
$("proxy-format").onchange = e => send("proxy_option", {key: "codec", value: e.target.value});
$("proxy-recursive").onchange = e => send("proxy_option", {key: "recursive", value: e.target.checked});
$("proxy-check").onclick = () => send("proxy_status");
$("proxy-relink").onclick = () => send("proxy_relink");
for (const b of document.querySelectorAll("#proxy-mode [data-mode]")) {
    b.onclick = () => send("proxy_mode", {mode: b.dataset.mode});
}

/* Proxy management's list: the clips the last List clips read, filtered by
   type (a chip per codec), ticked or not. What the buttons act on: the
   ticked clips that are shown - or, with none ticked, every clip shown. */
const PROXY_STATES = {linked: "Proxy", offline: "Proxy offline", original: "Camera original (proxy ready)",
                      none: "Camera original (no proxy)"};
let proxyRows = [];
let hiddenTypes = new Set();
let ticked = new Set();

const shownRows = () => proxyRows.filter(r => !hiddenTypes.has(r.type));
function chosenIds() {
    const shown = shownRows();
    const picked = shown.filter(r => ticked.has(r.id));
    return (picked.length ? picked : shown).map(r => r.id);
}

function drawProxyList() {
    const types = new Map();
    for (const r of proxyRows) types.set(r.type, (types.get(r.type) || 0) + 1);
    const chip = (label, count, on, onclick) => el("button.chip", {type: "button", "aria-pressed": String(on), onclick},
                                                   [label, count === null ? null : el("b", {text: String(count)})]);
    $("proxy-types-row").hidden = types.size < 2;
    $("proxy-types").replaceChildren(
        chip("All", null, !hiddenTypes.size, () => { hiddenTypes = new Set(); drawProxyList(); }),
        ...[...types].sort((a, b) => b[1] - a[1]).map(([type, count]) => chip(type, count, !hiddenTypes.has(type), () => {
            // From "All", a click shows just that type; after that, each click adds or drops one.
            if (!hiddenTypes.size) hiddenTypes = new Set([...types.keys()].filter(t => t !== type));
            else if (hiddenTypes.has(type)) hiddenTypes.delete(type);
            else hiddenTypes.add(type);
            if (hiddenTypes.size === types.size) hiddenTypes = new Set();
            drawProxyList();
        })));
    const shown = shownRows();
    $("proxy-status-rows").replaceChildren(...shown.map(r => {
        const box = el("input", {type: "checkbox", checked: ticked.has(r.id), "aria-label": `Tick ${r.name}`});
        const row = el("tr", {"data-state": r.state, class: ticked.has(r.id) ? "ticked" : undefined}, [
            el("td.proxy-tick", {}, [box]),
            el("td", {text: r.name, translate: "no"}),
            el("td.proxy-type", {text: r.type, translate: "no"}),
            el("td.proxy-state", {text: PROXY_STATES[r.state] || r.state}),
            el("td.muted.small.proxy-path", {text: r.detail, translate: "no"}),
        ]);
        box.onchange = () => { if (box.checked) ticked.add(r.id); else ticked.delete(r.id); row.classList.toggle("ticked", box.checked); drawChosen(); };
        return row;
    }));
    drawChosen();
}

function drawChosen() {
    const shown = shownRows(), picked = shown.filter(r => ticked.has(r.id)).length;
    const all = $("proxy-tick-all");
    all.checked = !!shown.length && picked === shown.length;
    all.indeterminate = picked > 0 && picked < shown.length;
    $("proxy-chosen").textContent = !shown.length ? "No clips shown."
        : picked ? `${plural(picked, "clip")} ticked` : `No clips ticked – the buttons act on all ${plural(shown.length, "clip")} shown`;
    applyProxyEnabled();
}

$("proxy-tick-all").onchange = e => {
    for (const r of shownRows()) { if (e.target.checked) ticked.add(r.id); else ticked.delete(r.id); }
    drawProxyList();
};
$("proxy-use-originals").onclick = () => send("proxy_use", {use: "original", ids: chosenIds()});
$("proxy-use-proxies").onclick = () => send("proxy_use", {use: "proxy", ids: chosenIds()});
$("proxy-make-chosen").onclick = () => send("proxy_go", {ids: chosenIds()});

Buddy.on("proxy_status", s => {
    $("proxy-status").hidden = false;
    const c = s.counts;
    $("proxy-counts").replaceChildren(
        el("span.chip.ok", {text: `${c.linked} on proxies`}),
        el("span.chip", {text: `${c.original || 0} on originals, proxy ready`}),
        el("span.chip", {text: `${c.none} without a proxy`}),
        el("span.chip.bad", {text: `${c.offline} offline`}));
    proxyRows = s.rows;
    const ids = new Set(proxyRows.map(r => r.id));
    ticked = new Set([...ticked].filter(id => ids.has(id)));          // a re-list keeps what's still there ticked
    const types = new Set(proxyRows.map(r => r.type));
    hiddenTypes = new Set([...hiddenTypes].filter(t => types.has(t)));
    if (hiddenTypes.size === types.size) hiddenTypes = new Set();
    drawProxyList();
    $("proxy-status-more").hidden = !s.more;
    $("proxy-status-more").textContent = s.more ? `And ${s.more.toLocaleString()} more, not listed.` : "";
});

Buddy.on("proxy", d => {
    proxy = d;
    const scope = d.scope || "selection";
    for (const b of document.querySelectorAll("#proxy-scope [data-scope]")) {
        b.setAttribute("aria-pressed", String(b.dataset.scope === scope));
    }
    $("proxy-recursive-box").hidden = scope !== "bin";
    $("proxy-recursive").checked = !!d.recursive;
    for (const b of document.querySelectorAll("#proxy-resolution [data-resolution]")) {
        b.setAttribute("aria-pressed", String(b.dataset.resolution === (d.resolution || "half")));
    }
    const format = $("proxy-format"), codecs = d.codecs || [];
    if (format.options.length !== codecs.length) {
        format.replaceChildren(...codecs.map(c => el("option", {value: c.id, text: c.label})));
    }
    format.value = d.codec || "h264";
    const chosen = codecs.find(c => c.id === format.value);
    $("proxy-format-hint").textContent = chosen ? chosen.hint : "";
    const note = $("proxy-ffmpeg-note");
    note.hidden = !!d.ffmpeg;
    note.textContent = "Making proxies needs ffmpeg, and Buddy can't find it. Install it, or set its path in Settings.";
    for (const b of document.querySelectorAll("#proxy-mode [data-mode]")) {
        b.setAttribute("aria-pressed", String(b.dataset.mode === d.mode));
    }
    applyProxyEnabled();
});

function applyProxyEnabled() {
    const go = $("proxy-go");
    if (!proxy) return;
    // Held only by a proxy run of its own (or a step with the overlay up) -
    // not by a Sync job, which runs alongside.
    const held = !!(state.overlay || state.proxy_busy);
    go.disabled = !(proxy.connected && proxy.ffmpeg && !held);
    // Listing, relinking and switching need Resolve, not ffmpeg; listing
    // and the playback switch work mid-render too.
    const resolve = proxy.connected && !held, any = shownRows().length > 0;
    $("proxy-check").disabled = !(proxy.connected && !state.overlay);
    $("proxy-relink").disabled = !resolve;
    $("proxy-use-originals").disabled = $("proxy-use-proxies").disabled = !(resolve && any);
    $("proxy-make-chosen").disabled = !(resolve && any && proxy.ffmpeg);
    for (const b of document.querySelectorAll("#proxy-mode [data-mode]")) b.disabled = !proxy.connected || proxy.mode === null;
}

// ------------------------------------------------------------- activity

function metadataChanges() {
    const changes = {};
    for (const [key, {check, input}] of metadataInputs) {
        if (check && check.checked) changes[key] = input.value;
    }
    return changes;
}

function applyMetadataEnabled() {
    const count = Object.keys(metadataChanges()).length;
    $("metadata-load").disabled = state.busy;
    $("metadata-apply").disabled = state.busy || !metadata || !metadata.count || !count;
    $("metadata-changes").textContent = count ? `${plural(count, "field")} to apply to ${plural(metadata.count, "clip")}` : "No fields chosen";
    for (const {check, input} of metadataInputs.values()) {
        if (check) {
            check.disabled = state.busy;
            input.disabled = state.busy;
        }
    }
}

$("metadata-load").onclick = async () => {
    if (Object.keys(metadataChanges()).length && !await Buddy.confirm({
        title: "Reload selected clips?", text: "This replaces the metadata edits you have not applied yet.", ok: "Reload",
    })) return;
    send("metadata_load");
};
$("metadata-apply").onclick = () => {
    if (!metadata || state.busy) return;
    send("metadata_apply", {revision: metadata.revision, changes: metadataChanges()});
};

Buddy.on("metadata", d => {
    if (metadata && d.revision === metadata.revision) return; // Preserve drafts across tab switches.
    metadata = d;
    metadataInputs.clear();
    $("metadata-count").textContent = d.count ? `${plural(d.count, "clip")} loaded` : "No clips loaded";
    $("metadata-names").textContent = (d.names || []).join(" · ");
    $("metadata-error").textContent = d.error || "";
    $("metadata-fields").replaceChildren(...(d.fields || []).map((field, index) => {
        const id = `metadata-field-${index}`;
        const editable = field.kind !== "readonly";
        const check = editable ? el("input", {type: "checkbox", title: `Apply ${field.label} to all loaded clips`,
            "aria-label": `Apply ${field.label}`, onchange: applyMetadataEnabled}) : null;
        let input;
        if (field.kind === "tag" || field.kind === "color") {
            const options = field.kind === "tag"
                ? [["1", "Good Take"], ["0", "Untagged"], ["-1", "Rejected"]]
                : [["", "No color"], ...(d.colors || []).map(color => [color, color])];
            input = el("select.field", {id}, [
                ...(field.mixed ? [el("option", {value: "", text: "Mixed — choose a value", disabled: true, selected: true})] : []),
                ...options.map(([value, label]) => el("option", {value, text: label, selected: !field.mixed && field.value === value})),
            ]);
            // A mixed placeholder must not become an accidental clearing edit.
            check.onchange = () => {
                if (check.checked && input.selectedOptions[0].disabled) check.checked = false;
                applyMetadataEnabled();
            };
        } else {
            input = el(field.kind === "multiline" ? "textarea.field" : "input.field", {
                id, value: field.value, readOnly: !editable, translate: "no",
                placeholder: field.mixed ? "Mixed" : "", rows: field.kind === "multiline" ? 3 : undefined,
            });
            input.value = field.value;
        }
        if (check) input.oninput = input.onchange = () => { check.checked = true; applyMetadataEnabled(); };
        metadataInputs.set(field.key, {check, input});
        return el("div.metadata-row", {}, [check || el("span"), el("label", {for: id, text: field.label}), input]);
    }));
    applyMetadataEnabled();
});

Buddy.on("log", d => {
    const box = document.querySelector(`[data-log="${d.tab}"]`);
    if (!box) return;
    const had = box.querySelectorAll(".log li").length;
    box.hidden = !d.entries.length;
    box.querySelector(".log").replaceChildren(...d.entries.slice().reverse().map(e =>
        el(`li.k-${e.kind}`, {}, [el("span.time", {text: e.time}), e.text])));
    if (d.entries.length > had) box.open = true;
});

showTab(state.tab);
