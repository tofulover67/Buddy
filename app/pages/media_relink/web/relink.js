/*
 * Media Relink's view. Python owns the clip lists and every decision; this
 * draws them and keeps which rows are ticked (sent with Relink selected).
 */
"use strict";

const $ = id => document.getElementById(id);
const {el, send} = Buddy;

// Each mode's title, then its text a sentence (or two) at a time - each
// translated on its own.
const INTRO = {
    fix: ["Fix offline media",
          "Scan for clips whose files have gone missing, then search a folder to find them. " +
          "Clips that already work are never touched, even if a file with the same name turns up.",
          "If you don't know which folder they're in, search the whole drive."],
    relocate: ["Move to a new location",
               "Point clips at a copy of their media somewhere else – a new drive, or off a server onto a local SSD - " +
               "even clips that work now. Scan, search the new location, tick the clips to move, then relink."],
};

let state = {mode: "fix", scope: "project", connected: false, busy: false};
const lists = {fix: null, relocate: null};
const selected = {fix: new Set(), relocate: new Set()};
const generation = {fix: -1, relocate: -1};
const selectionAnchor = {fix: null, relocate: null};
const onlyOffline = $("only-offline");

// Per scope: the scan button, and what the empty table says before a scan.
const SCOPES = {
    bin: {scan: "Scan current bin", title: "Scan the current bin to list its clips",
          detail: "Only clips directly in the bin open in Resolve are included; sub-bins are excluded.",
          none: "The current bin has no clips that link to a file."},
    timeline: {scan: "Scan timeline", title: "Scan the current timeline to list its clips",
               detail: "Every clip used on the timeline open in Resolve, once each.",
               none: "The current timeline has no clips that link to a file."},
    selected: {scan: "Scan selected clips", title: "Select clips in Resolve, then scan them",
               detail: "The clips selected in the Media Pool – or on the timeline, when none are selected in the Media Pool.",
               none: "None of the selected clips link to a file."},
    project: {scan: "Scan project", title: "Scan the project to list its clips",
              none: "The Media Pool has no clips that link to a file."},
};
const scopeInfo = () => SCOPES[state.scope] || SCOPES.project;

const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || word + "s")}`;
// Whole sentences, one text node each, so each is translated on its own.
const sentences = parts => parts.flatMap((p, i) => i ? [" ", el("span", {text: p})] : [el("span", {text: p})]);
const needsAttention = r => r.status !== "online" || r.new_path;
const visibleRows = list => state.mode === "fix" && onlyOffline.checked
    ? list.rows.filter(needsAttention) : list.rows;

for (const b of document.querySelectorAll("#mode [data-mode]")) {
    b.onclick = () => send("mode", {mode: b.dataset.mode});
}
for (const b of document.querySelectorAll("#scope [data-scope]")) {
    b.onclick = () => send("scope", {scope: b.dataset.scope});
}
for (const b of document.querySelectorAll("[data-action]")) b.addEventListener("click", () => send(b.dataset.action));
$("cancel-search").onclick = () => send("cancel_search");
onlyOffline.onchange = () => { selectionAnchor[state.mode] = null; draw(); };

function selectRow(id, checked, shiftKey, rows) {
    const mode = state.mode;
    const set = selected[mode];
    const anchor = rows.findIndex(r => r.id === selectionAnchor[mode]);
    const end = rows.findIndex(r => r.id === id);
    if (end < 0) return;
    const range = shiftKey && anchor >= 0
        ? rows.slice(Math.min(anchor, end), Math.max(anchor, end) + 1) : [rows[end]];
    for (const r of range) checked ? set.add(r.id) : set.delete(r.id);
    if (!shiftKey || anchor < 0) selectionAnchor[mode] = id;
    draw();
}

$("select-all").onclick = () => {
    const list = lists[state.mode];
    if (!list) return;
    const set = selected[state.mode];
    const rows = visibleRows(list);
    const every = rows.length && rows.every(r => set.has(r.id));
    for (const r of rows) every ? set.delete(r.id) : set.add(r.id);
    selectionAnchor[state.mode] = null;
    draw();
};
$("relink-selected").onclick = () => send("relink_selected", {ids: [...selected[state.mode]]});
$("relink-all").onclick = () => send("relink_all");

// ------------------------------------------------------------------ draw

function statusChip(r) {
    return el(`span.status-chip.st-${r.status}`, {title: `Recorded path: ${r.old_path}`}, [el("i"), r.label]);
}

// The file name is the same on both sides (that's how a match is found),
// so the table shows the folders; the full paths are in the tooltip.
const folderOf = path => path.replace(/[\\/][^\\/]*$/, "") || path;

function pathLine(cls, path, arrow) {
    return el(`div.path${cls}`, {title: path, translate: "no"}, el("bdi", {}, [arrow ? el("span.path-arrow", {text: "→"}) : null, folderOf(path)]));
}

function pathCell(r) {
    if (r.new_path) return el("td.file", {}, [pathLine(".old", r.old_path), pathLine(".new", r.new_path, true)]);
    return el("td.file", {}, pathLine("", r.old_path));
}

function actionCell(r) {
    if (r.status === "ambiguous") {
        return el("td.act", {}, el("button.btn.accent", {type: "button", text: "Pick…", onclick: () => pickDialog(r)}));
    }
    if (r.status === "online" && state.mode === "fix") return el("td.act");
    const label = r.candidates.length > 1 ? "Change…" : "Browse…";
    return el("td.act", {}, el("button.btn.ghost", {
        type: "button", text: label, title: "Choose the file for this clip yourself",
        onclick: () => r.candidates.length > 1 ? pickDialog(r) : send("browse", {id: r.id}),
    }));
}

function emptyState(title, detail, button) {
    return el("div.empty", {}, [el("div.strong", {text: title}), detail ? el("div.small", {text: detail}) : null, button || null]);
}

function drawTable(list) {
    const box = $("results");
    if (!state.connected && !list.scanned) {
        box.replaceChildren(emptyState("Not connected to Resolve", "Open Resolve with a project, then press Connect.",
            el("button.btn.accent", {text: "Connect", onclick: () => send("connect")})));
        return;
    }
    const scope = scopeInfo();
    if (!list.scanned) {
        box.replaceChildren(emptyState(scope.title,
            scope.detail || (state.mode === "fix" ? "Buddy checks every clip's file and lists the ones that are missing." :
                                                    "Buddy lists every clip in the Media Pool with the file it points at."),
            el("button.btn.accent", {text: scope.scan, onclick: () => send("scan")})));
        return;
    }
    const rows = visibleRows(list);
    if (!list.rows.length) {
        box.replaceChildren(emptyState("No clips with files", scope.none));
        return;
    }
    if (!rows.length) {
        box.replaceChildren(emptyState("Nothing is offline",
            list.rows.length === 1 ? "The one clip links to a file that exists." : `All ${list.rows.length} clips link to files that exist.`,
            el("button.btn", {text: "Show all clips", onclick: () => { onlyOffline.checked = false; draw(); }})));
        return;
    }
    const set = selected[state.mode];
    const allTicked = rows.every(r => set.has(r.id));
    const head = el("tr", {}, [
        el("th.sel", {}, el("input", {type: "checkbox", checked: allTicked, title: "Select all shown",
            onchange: e => {
                for (const r of rows) e.target.checked ? set.add(r.id) : set.delete(r.id);
                selectionAnchor[state.mode] = null;
                draw();
            }})),
        el("th.name", {text: "Clip"}), el("th.bin", {text: "Bin"}), el("th.status", {text: "Status"}),
        el("th.file", {text: "File"}), el("th.act"),
    ]);
    const body = rows.map(r => el(`tr${set.has(r.id) ? ".selected" : ""}`, {
        onmousedown: e => { if (e.shiftKey && !e.target.closest("button, input")) e.preventDefault(); },
        onclick: e => {
            if (e.shiftKey && !e.target.closest("button, input")) selectRow(r.id, true, true, rows);
        },
        ondblclick: () => r.status === "ambiguous" ? pickDialog(r) : (r.status !== "online" || state.mode !== "fix") && send("browse", {id: r.id}),
    }, [
        el("td.sel", {}, el("input", {type: "checkbox", checked: set.has(r.id),
            title: "Shift-click to select a range", "aria-label": r.name,
            onclick: e => { e.stopPropagation(); selectRow(r.id, e.target.checked, e.shiftKey, rows); }})),
        el("td.name", {text: r.name, title: r.name, translate: "no"}),
        el("td.bin", {text: r.bin, title: r.bin, translate: "no"}),
        el("td.status", {}, statusChip(r)),
        pathCell(r),
        actionCell(r),
    ]));
    const scroll = box.scrollTop;
    box.replaceChildren(el("table.table", {}, [el("thead", {}, head), el("tbody", {}, body)]));
    box.scrollTop = scroll;
}

function drawCounts(list) {
    const c = list.counts;
    const chip = (n, label, cls) => el(`span.count${cls ? "." + cls : ""}`, {}, [el("b", {text: n.toLocaleString()}), label]);
    $("counts").replaceChildren(...(list.scanned ? [
        chip(c.total, c.total === 1 ? "clip" : "clips"),
        state.mode === "fix" || c.offline ? chip(c.offline, "offline") : null,
        c.matched ? chip(c.matched, c.matched === 1 ? "match" : "matches") : null,
        c.ambiguous ? chip(c.ambiguous, "to pick") : null,
    ].filter(Boolean) : []));
}

function draw() {
    const mode = state.mode;
    for (const b of document.querySelectorAll("#mode [data-mode]")) b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
    for (const b of document.querySelectorAll("#scope [data-scope]")) {
        b.setAttribute("aria-pressed", String(b.dataset.scope === state.scope));
        b.disabled = state.busy;
    }
    const [title, ...text] = INTRO[mode];
    $("intro-title").textContent = title;
    $("intro-text").replaceChildren(...sentences(text));
    $("only-offline-box").hidden = mode !== "fix";
    $("relink-all").hidden = mode !== "fix";

    const list = lists[mode] || {rows: [], counts: {total: 0}, scanned: false, summary: ""};
    drawCounts(list);
    drawTable(list);
    $("summary").replaceChildren(...sentences(list.summary_parts || []));

    const scan = $("scan");
    scan.classList.toggle("accent", !list.scanned);
    scan.textContent = list.scanned ? "Scan again" : scopeInfo().scan;
    const search = $("search");
    search.classList.toggle("accent", list.scanned && !list.counts.matched && !list.counts.ambiguous);
    const toPick = list.counts.ambiguous || 0;
    const pickGroups = $("pick-groups");
    pickGroups.hidden = toPick < 2;
    pickGroups.textContent = `Pick for all ${toPick}…`;
    pickGroups.disabled = state.busy;

    const set = selected[mode];
    const picked = list.rows.filter(r => set.has(r.id));
    const ready = picked.filter(r => r.new_path && r.status !== "online");
    const matches = list.rows.filter(r => r.status === "match_found" && r.new_path).length;
    const shown = visibleRows(list);
    $("select-all").hidden = !shown.length;
    $("select-all").textContent = shown.length && shown.every(r => set.has(r.id)) ? "Select none" : "Select all";
    $("selection").replaceChildren(...(picked.length
        ? [el("span", {text: `${picked.length} selected`}),
           ...(ready.length !== picked.length ? [" · ", el("span", {text: `${ready.length} with a file to go to`})] : [])]
        : [mode === "relocate" && matches ? "Tick the clips to move" : ""]));
    const rs = $("relink-selected");
    rs.textContent = ready.length ? `Relink ${plural(ready.length, "selected clip")}` : "Relink selected";
    rs.classList.toggle("accent", mode === "relocate");
    const ra = $("relink-all");
    ra.textContent = matches ? `Relink all ${plural(matches, "match", "matches")}` : "Relink all matches";

    const busy = state.busy;
    scan.disabled = search.disabled = busy;
    search.disabled = busy || !list.rows.length;
    rs.disabled = busy || !ready.length;
    ra.disabled = busy || !matches;
    $("select-all").disabled = busy;
}

// ---------------------------------------------------------------- picking

const CLOSEST = "Closest to where it was";
const closestTag = () => el("span.pick-tag", {text: CLOSEST, title: "Its folders match the clip's recorded path the most"});

// How many other clips still to pick have a file at the place with this
// key - the ones "same_place" would pick too (relink_rows.pick_location):
// not those whose recorded path points at another of their places.
function othersAt(key, exceptId) {
    const groups = (lists[state.mode] || {}).groups || [];
    return groups.filter(g => g.keys.includes(key) && (!g.closest || g.closest === key))
        .reduce((n, g) => n + g.ids.filter(id => id !== exceptId).length, 0);
}

function pickDialog(r) {
    let choice = r.new_path || r.closest || r.candidates[0];
    const sameBox = el("input", {type: "checkbox", checked: true});
    const sameText = el("span");
    const same = el("label.check.small.pick-same", {}, [sameBox, sameText]);
    const showSame = () => {
        const at = r.candidates.indexOf(choice);
        const n = r.status === "ambiguous" && at >= 0 ? othersAt(r.location_keys[at], r.id) : 0;
        same.hidden = !n;
        if (n) sameText.textContent = n === 1 ? `Also use ${r.locations[at]} for the other clip that has a file there`
            : `Also use ${r.locations[at]} for the ${n} other clips that have a file there`;
    };
    const list = el("div.pick-list", {}, r.candidates.map(path => el("label", {}, [
        el("input", {type: "radio", name: "pick", checked: path === choice, onchange: () => { choice = path; showSame(); }}),
        el("span.pick-option", {text: path, translate: "no"}),
        path === r.closest ? closestTag() : null,
    ])));
    showSame();
    Buddy.modal({
        title: `Which file is "${r.name}"?`,
        body: [el("p.modal-text.muted", {text: `${plural(r.candidates.length, "file")} in that folder share its name.`}), list, same],
        wide: true,
        buttons: [
            {label: "Browse for another…", onClick: close => { close(); send("browse", {id: r.id}); }},
            {label: "Cancel"},
            {label: "Use this file", kind: "accent", onClick: close => {
                close();
                send("pick", {id: r.id, path: choice, same_place: !same.hidden && sameBox.checked});
            }},
        ],
    });
}

// Every clip still to pick, grouped by the places its files are in - one
// choice per group. Picking a place fills it in for the other groups that
// have it and haven't been answered yet.
function pickGroupsDialog() {
    const list = lists[state.mode];
    const groups = (list && list.groups) || [];
    if (!groups.length) return;
    const names = new Map(list.rows.map(r => [r.id, r.name]));
    const choices = groups.map(g => g.closest);
    const radios = groups.map(() => new Map());
    const choose = (gi, key) => {
        choices[gi] = key;
        if (key === null) return;
        groups.forEach((g, gj) => {
            if (choices[gj] !== null || !g.keys.includes(key) || (g.closest && g.closest !== key)) return;
            choices[gj] = key;
            radios[gj].get(key).checked = true;
        });
    };
    const sections = groups.map((g, gi) => {
        const option = (key, content) => {
            const radio = el("input", {type: "radio", name: `group-${gi}`, checked: choices[gi] === key,
                                       onchange: () => choose(gi, key)});
            radios[gi].set(key, radio);
            return el("label", {}, [radio, ...content]);
        };
        const clips = g.ids.map(id => names.get(id));
        return el("section.pick-group", {}, [
            el("details.pick-group-clips", {}, [
                el("summary", {text: g.ids.length === 1 ? "1 clip" : `${g.ids.length} clips`}),
                el("div.small.muted", {text: clips.join(", "), translate: "no"}),
            ]),
            el("div.pick-list.flat", {}, [
                ...g.keys.map((key, i) => option(key, [
                    el("span.pick-option", {translate: "no"}, [
                        el("span.loc", {text: g.locations[i]}),
                        el("span.example", {text: g.examples[i], title: g.examples[i]}),
                    ]),
                    key === g.closest ? closestTag() : null,
                ])),
                option(null, [el("span.pick-option", {text: "Decide later"})]),
            ]),
        ]);
    });
    const clips = groups.reduce((n, g) => n + g.ids.length, 0);
    Buddy.modal({
        title: `Pick files for ${plural(clips, "clip")}`,
        body: [
            el("p.modal-text.muted", {text: "Clips whose files are in the same places are grouped, so one choice does for the whole group. Nothing is relinked yet."}),
            el("div.pick-groups", {}, sections),
        ],
        wide: true,
        buttons: [
            {label: "Cancel"},
            {label: "Use these", kind: "accent", onClick: close => {
                close();
                const picks = groups.map((g, gi) => choices[gi] === null ? null
                    : {ids: g.ids, location: g.locations[g.keys.indexOf(choices[gi])]}).filter(Boolean);
                if (picks.length) send("pick_groups", {picks});
            }},
        ],
    });
}
$("pick-groups").onclick = pickGroupsDialog;

// ---------------------------------------------------------------- events

Buddy.on("state", s => { state = s; draw(); });

Buddy.on("rows", d => {
    if (d.generation !== generation[d.mode]) {
        generation[d.mode] = d.generation;
        selected[d.mode].clear();
        selectionAnchor[d.mode] = null;
    }
    const ids = new Set(d.rows.map(r => r.id));
    for (const id of [...selected[d.mode]]) if (!ids.has(id)) selected[d.mode].delete(id);
    if (!ids.has(selectionAnchor[d.mode])) selectionAnchor[d.mode] = null;
    lists[d.mode] = d;
    if (d.mode === state.mode) draw();
});

Buddy.on("search", s => {
    $("search-card").hidden = !s;
    if (!s) return;
    $("search-title").textContent = s.stopping ? "Stopping…" : `Searching ${s.folder}`;
    $("search-count").textContent = s.seen ? `Looked at ${s.seen.toLocaleString()} files so far` : "Starting…";
    $("cancel-search").disabled = s.stopping;
});

Buddy.on("log", entries => {
    $("activity").hidden = !entries.length;
    $("log").replaceChildren(...entries.slice().reverse().map(e =>
        el(`li.k-${e.kind}`, {}, [el("span.time", {text: e.time}), ...(e.parts ? sentences(e.parts) : [e.text])])));
});

Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}), buttons: [{label: "OK", kind: "accent"}]}));
Buddy.on("toast", t => Buddy.toast(t.text, 3000));

draw();
