/*
 * Time Tracker's view. Draws what pages/time_tracker/page.py sends - the
 * live "tick" every second and the "data" snapshot (lists, reports) when
 * entries change - and reports what the user did. Which rows are ticked
 * and which tab is open are the only state kept here.
 */
"use strict";

const $ = id => document.getElementById(id);
const {el, icon, send} = Buddy;

let data = null;
let tick = null;

// ------------------------------------------------------------------ tabs

function showTab(name) {
    for (const tab of document.querySelectorAll("#tabs [data-tab]")) {
        tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
    }
    for (const panel of document.querySelectorAll(".panel")) {
        panel.hidden = panel.id !== `panel-${name}`;
    }
}
for (const tab of document.querySelectorAll("#tabs [data-tab]")) {
    tab.onclick = () => showTab(tab.dataset.tab);
}
showTab("tracker");

// --------------------------------------------------------------- tracker

Buddy.on("tick", t => {
    tick = t;
    const away = t.label === "Away";
    $("pill").dataset.state = away ? "away" : t.state;
    $("pill-text").textContent = t.label;
    $("hero").dataset.state = t.state;
    $("project").translate = !t.project;   // a project's name is the user's
    $("project").textContent = t.project || "No project detected";
    $("project").classList.toggle("none", !t.project);
    $("timer").textContent = t.elapsed;
    $("hint").textContent = t.hint;
    $("today").textContent = t.today;
    $("week").textContent = t.week;

    $("start").hidden = !(t.manual && t.state === "stopped");
    $("start").disabled = !t.can_start;
    $("start").title = t.can_start ? "" : "Open a project in DaVinci Resolve first";
    const idleManual = t.manual && t.state === "stopped";
    $("pause").hidden = idleManual;
    $("stop").hidden = idleManual;
    $("pause").disabled = !t.can_pause;
    $("pause").replaceChildren(icon(t.paused ? "play" : "pause"), t.paused ? "Resume" : "Pause");
    $("pause").classList.toggle("accent", t.paused);
    $("stop").disabled = !t.can_stop;
    $("stop").replaceChildren(icon("stop"), "Stop");

    $("week-goal").hidden = !t.week_goal;
    if (t.week_goal) {
        $("week-goal-fill").style.width = `${Math.round(t.week_goal.fraction * 100)}%`;
        $("week-goal-text").textContent = `${t.week} of ${t.week_goal.hours}h weekly goal (${Math.round(t.week_goal.fraction * 100)}%)`;
    }
    drawProjectTotal(t.project_total);
    if (data) drawCard();
});

// "22h 54m" - as Python's format_duration_words.
function words(seconds) {
    const s = Math.max(0, Math.floor(seconds)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
    return h && m ? `${h}h ${m}m` : h ? `${h}h` : `${m}m`;
}

// The project on screen's time so far under the timer: one bar - before today, earlier today,
// this session - redrawn when a figure it shows changes (once a minute, not every tick).
let projectTotalKey = "";
let colorKey = "";              // the graph colours drawn with (applyColors)
function drawProjectTotal(p) {
    const parts = p ? [["before", "Before today", p.before], ["today", "Earlier today", p.today], ["now", "This session", p.session]]
        .filter(([, , s]) => s >= 60) : [];
    const total = p ? p.before + p.today + p.session : 0;
    const key = JSON.stringify([p && p.project, words(total), parts.map(([k, , s]) => [k, words(s)]), colorKey]);
    if (key === projectTotalKey) return;
    projectTotalKey = key;
    $("proj-total").hidden = !parts.length;
    if (!parts.length) return;
    paint($("proj-total"), p.project);
    $("proj-total-value").textContent = words(total);
    $("proj-stack").setAttribute("aria-label", parts.map(([, label, s]) => `${label} ${words(s)}`).join(", "));
    // Each part at least 1% wide, 2px apart: painted as one gradient (tracker.css says why).
    const widths = parts.map(([, , s]) => Math.max(1, s / total * 100)), sum = widths.reduce((a, b) => a + b, 0);
    let at = 0;
    const stops = parts.map(([kind], i) => {
        const from = at, to = at += widths[i] / sum * 100;
        const a = i ? `calc(${from}% + 1px)` : "0%", b = i < parts.length - 1 ? `calc(${to}% - 1px)` : "100%";
        return `var(--part-${kind}) ${a} ${b}` + (i < parts.length - 1 ? `, transparent ${b} calc(${to}% + 1px)` : "");
    });
    $("proj-stack").style.background = `linear-gradient(to right, ${stops.join(", ")})`;
    $("proj-stack").title = parts.map(([, label, s]) => `${label} · ${words(s)}`).join("\n");
    $("proj-legend").replaceChildren(...parts.map(([kind, label, s]) =>
        el("span", {}, [el(`i.${kind}`), `${label} `, el("b", {text: words(s)})])));
}

// This Project's card: the total, big, and a bar per week - with the session running now
// counted in, so it matches the timer. Redrawn when a figure it shows changes.
let cardKey = "";
function drawCard() {
    const card = data.this_project.card, box = $("project-card");
    $("this_project-summary").hidden = !!card;
    box.hidden = !card;
    if (!card) return;
    const live = tick && tick.project_total && tick.project_total.project === card.project ? tick.project_total.session : 0;
    const weeks = card.weeks.map(w => ({...w, seconds: w.seconds + (w.current ? live : 0)}));
    if (live && !weeks.length) weeks.push({label: card.this_week, seconds: live, current: true});
    const total = card.seconds + live;
    const key = JSON.stringify([card, words(total), weeks.map(w => words(w.seconds)), colorKey]);
    if (key === cardKey) return;
    cardKey = key;
    paint(box, card.project);
    const longest = Math.max(1, ...weeks.map(w => w.seconds));
    const facts = card.sessions ? el("div.facts", {}, [
        el("span", {}, [el("b", {text: card.sessions.toLocaleString()}), card.sessions === 1 ? " session" : " sessions"]),
        el("span", {}, [el("b", {text: card.days.toLocaleString()}), card.days === 1 ? " day worked" : " days worked"]),
        el("span", {}, ["Since ", el("b", {text: card.since})]),
    ]) : el("div.muted.small", {text: live ? "The first session on this project – running now."
                                           : "No time tracked yet – it adds up here as you work."});
    box.replaceChildren(...[
        el("div.pc-top", {}, [
            el("div", {}, [el("div.kicker", {text: "Total on this project"}), el("div.pc-total", {text: words(total)})]),
            facts,
        ]),
        weeks.length ? el("div.kicker", {text: card.weeks_title}) : null,
        weeks.length ? el("div.weeks", {}, weeks.map(w => el("div.week" + (w.current ? ".now" : ""), {}, [
            el("span.wk", {}, [w.label, w.current ? el("small", {text: "this week"}) : null]),
            el("div.track", {title: `${w.label} · ${w.seconds >= 60 ? words(w.seconds) : "nothing"}`,
                             style: `--filled: ${w.seconds / longest * 100}%`}),
            el("span.val", {text: w.seconds >= 60 ? words(w.seconds) : "–"}),
        ]))) : null,
    ].filter(Boolean));
}

$("start").append(icon("play"), "Start tracking");
$("start").onclick = () => send("start");
$("pause").onclick = () => send("pause", {paused: !(tick && tick.paused)});
$("stop").onclick = async () => {
    const ok = await Buddy.confirm({
        title: "Stop tracking?",
        text: "The current session is saved to History and the timer resets to zero.",
        ok: "Stop",
    });
    if (ok) send("stop");
};

Buddy.on("undo_offer", o => Buddy.toast(o.text, 7000, {label: "Undo", onClick: () => send("undo_stop", {entry_id: o.entry_id})}));
Buddy.on("toast", t => Buddy.toast(t.text, 3000));
Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}), buttons: [{label: "OK", kind: "accent"}]}));

// ---------------------------------------------------------- entry lists

// Per list: which rows are ticked, and the last one clicked (for Shift).
const lists = {
    this_project: {selected: new Set(), anchor: null, rows: []},
    history: {selected: new Set(), anchor: null, rows: []},
};

function renderList(name) {
    const state = lists[name];
    const view = data[name];
    state.rows = view.rows;
    const ids = new Set(view.rows.map(r => r.id));
    for (const id of [...state.selected]) if (!ids.has(id)) state.selected.delete(id);
    $(`${name}-summary`).textContent = view.summary;

    const box = $(`${name}-list`);
    if (!view.rows.length) {
        const noProject = name === "this_project" && !data.current_project;
        box.replaceChildren(el("div.empty", {}, [
            el("div.strong", {text: noProject ? "No project open in Resolve" : "No entries yet"}),
            el("div.small", {text: noProject
                ? "Open a project in DaVinci Resolve to see its sessions here."
                : "Sessions appear here as you work – or add one by hand."}),
        ]));
    } else {
        const all = el("input", {type: "checkbox", title: "Select all", "aria-label": "Select all"});
        all.checked = state.selected.size === view.rows.length;
        all.indeterminate = state.selected.size > 0 && !all.checked;
        all.onchange = () => {
            state.selected = all.checked ? new Set(view.rows.map(r => r.id)) : new Set();
            renderList(name);
        };
        const showProject = name === "history";
        box.replaceChildren(el("table.table", {}, [
            el("thead", {}, el("tr", {}, [
                el("th.sel", {}, all),
                el("th.date", {text: "Date"}),
                showProject ? el("th", {text: "Project"}) : null,
                el("th.time", {text: "Start"}),
                el("th.time", {text: "End"}),
                el("th.dur", {text: "Duration"}),
                el("th", {text: "Notes"}),
            ])),
            el("tbody", {}, view.rows.map((r, index) => rowNode(name, r, index, showProject))),
        ]));
    }
    renderBar(name);
}

function rowNode(name, r, index, showProject) {
    const state = lists[name];
    const box = el("input", {type: "checkbox", "aria-label": "Select"});
    box.checked = state.selected.has(r.id);
    box.onclick = e => { e.stopPropagation(); toggle(name, r.id, index, e.shiftKey); };
    const tr = el(`tr${state.selected.has(r.id) ? ".selected" : ""}${r.hidden ? ".hidden-entry" : ""}`, {
        title: r.hidden ? "Hidden – left out of totals, reports and exports" : "Double-click to edit",
        onclick: e => {
            if (e.ctrlKey || e.metaKey) toggle(name, r.id, index, false);
            else if (e.shiftKey) toggle(name, r.id, index, true);
            else { state.selected = new Set([r.id]); state.anchor = index; renderList(name); }
        },
        ondblclick: () => editEntry(r),
    }, [
        el("td.sel", {}, box),
        el("td", {}, [el("span.day", {text: r.weekday}), r.date]),
        showProject ? el("td", {text: r.project, title: r.project, translate: "no"}) : null,
        el("td.time", {text: r.start}),
        el("td.time", {text: r.end}),
        el("td.dur", {text: r.duration}),
        el("td.notes", {text: r.notes, title: r.notes, translate: "no"}),
    ]);
    return tr;
}

function toggle(name, id, index, range) {
    const state = lists[name];
    if (range && state.anchor !== null) {
        const [lo, hi] = [Math.min(state.anchor, index), Math.max(state.anchor, index)];
        for (const r of state.rows.slice(lo, hi + 1)) state.selected.add(r.id);
    } else {
        if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
        state.anchor = index;
    }
    renderList(name);
}

function renderBar(name) {
    const state = lists[name];
    const bar = $(`${name}-bar`);
    const chosen = state.rows.filter(r => state.selected.has(r.id));
    bar.hidden = !chosen.length;
    if (!chosen.length) return;
    const allHidden = chosen.every(r => r.hidden);
    const ids = chosen.map(r => r.id);
    bar.replaceChildren(
        el("span.count", {text: `${chosen.length} selected`}),
        chosen.length === 1 ? el("button.btn", {text: "Edit", onclick: () => editEntry(chosen[0])}) : null,
        el("button.btn", {
            text: allHidden ? "Unhide" : "Hide",
            title: "Hidden entries stay listed but are left out of totals, reports and exports",
            onclick: () => send("hide", {ids, hidden: !allHidden}),
        }),
        el("button.btn.danger", {text: "Delete", onclick: async () => {
            const n = ids.length;
            const ok = await Buddy.confirm({
                title: `Delete ${n} ${n === 1 ? "entry" : "entries"}?`,
                text: "This can't be undone.", ok: "Delete", danger: true,
            });
            if (ok) { state.selected.clear(); send("delete", {ids}); }
        }}),
        el("button.btn.ghost", {text: "Clear", onclick: () => { state.selected.clear(); renderList(name); }}),
    );
}

for (const name of Object.keys(lists)) {
    const panel = $(`panel-${name}`);
    panel.querySelector("[data-add]").onclick = () => editEntry(null);
    panel.querySelector("[data-export]").onclick = () =>
        send("export_history", {tab: name, format: panel.querySelector("[data-format]").value});
}

$("history-scope").onchange = e => { lists.history.selected.clear(); send("history_scope", {scope: e.target.value}); };

// ------------------------------------------------------- entry dialog

function localInput(date) {
    const pad = n => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
           `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

let entryDialog = null;

function editEntry(row) {
    const now = new Date();
    const project = el("input.field", {list: "project-names", value: row ? row.project : (data.current_project || ""),
                                       placeholder: "Project name", autocomplete: "off"});
    const start = el("input.field", {type: "datetime-local", step: "1",
                                     value: row ? row.start_iso : localInput(new Date(now - 30 * 60000))});
    const end = el("input.field", {type: "datetime-local", step: "1", value: row ? row.end_iso : localInput(now)});
    const notes = el("input.field", {value: row ? row.notes : "", placeholder: "What were you working on?"});
    const error = el("div.field-error");
    const fields = {project, start, end, notes};
    const save = () => {
        for (const f of Object.values(fields)) f.classList.remove("invalid");
        error.textContent = "";
        send("save_entry", {id: row ? row.id : null, project: project.value, start: start.value,
                            end: end.value, notes: notes.value});
    };
    for (const f of Object.values(fields)) f.addEventListener("keydown", e => { if (e.key === "Enter") save(); });
    entryDialog = Buddy.modal({
        title: row ? "Edit entry" : "Add entry",
        wide: true,
        body: [
            el("datalist#project-names", {}, data.projects.map(p => el("option", {value: p}))),
            el("div.form-grid", {}, [
                el("label.lbl.full", {}, ["Project", project]),
                el("label.lbl", {}, ["Start", start]),
                el("label.lbl", {}, ["End", end]),
                el("label.lbl.full", {}, ["Notes (optional)", notes]),
            ]),
            error,
        ],
        buttons: [{label: "Cancel"}, {label: "Save", kind: "accent", onClick: save}],
        onClose: () => { entryDialog = null; },
    });
    entryDialog.fields = fields;
    entryDialog.error = error;
}

Buddy.on("entry_result", r => {
    if (!entryDialog) return;
    if (r.ok) return entryDialog.close();
    if (entryDialog.fields[r.field]) entryDialog.fields[r.field].classList.add("invalid");
    entryDialog.error.textContent = r.message;
});

// --------------------------------------------------------------- reports

function tileNode(t) {
    return el(`div.card.tile${t.empty ? ".zero" : ""}`, {}, [
        el("span.muted.small", {text: t.label}),
        el("span.value", {text: t.value}),
        ...t.money.map(m => el("span.muted.small", {text: m})),
    ]);
}

function renderReports() {
    const r = data.reports;
    $("tiles").replaceChildren(...r.tiles.map(tileNode));

    $("goals").hidden = !r.goals.length;
    $("goal-rows").replaceChildren(...r.goals.map(g => el("div.goal-row", {}, [
        el("div.row", {}, [el("span", {text: g.label}),
                           el("span.muted.small", {text: `${g.hours} of ${g.goal}h (${Math.round(g.fraction * 100)}%)`})]),
        el("div.bar", {}, el("i", {style: `width:${Math.round(g.fraction * 100)}%`})),
    ])));

    const max = Math.max(0, ...r.daily.map(d => d.seconds));
    $("chart").replaceChildren(...r.daily.map((d, i) => el(
        `div.day-col${d.seconds ? "" : ".none"}${i === r.daily.length - 1 ? ".today" : ""}`,
        {title: `${d.label}: ${d.text || "nothing tracked"}`},
        [
            el("span.amount", {text: d.text}),
            el("div.track", {}, el("div.fill", {style: `height:${max ? Math.max(2, Math.round(d.seconds / max * 100)) : 2}%`})),
            el("span.day", {text: i === r.daily.length - 1 ? "Today" : d.weekday}),
        ],
    )));

    const top = Math.max(0, ...r.projects.map(p => p.seconds));
    $("projects").replaceChildren(...(r.projects.length ? r.projects.map(p => paint(el("div.project-row", {}, [
        el("div.name-cell", {}, [swatch(p.name), el("span.name", {text: p.name, title: p.name, translate: "no"})]),
        el("div.bar", {}, el("i", {style: `width:${top ? Math.max(1, Math.round(p.seconds / top * 100)) : 0}%`})),
        el("div.figures", {}, [
            el("span", {text: p.text}),
            p.money ? el("span.money", {text: p.money}) : null,
            el("button.btn.ghost", {text: p.money ? "Rate" : "Set rate", title: `Hourly rate for ${p.name}`,
                                    onclick: () => rateDialog(p.name)}),
        ]),
    ]), p.name)) : [el("div.muted", {text: "No projects tracked yet."})]));

    $("custom-result").replaceChildren(...(r.custom ? [tileNode(r.custom)] :
        [document.createTextNode("Pick a range and press Calculate to see its total time and earnings – it's included in Export too.")]));
}

function dateInput(date) { return localInput(date).slice(0, 10); }
const today = new Date();
$("custom-from").value = dateInput(new Date(today.getFullYear(), today.getMonth(), 1));
$("custom-to").value = dateInput(today);
$("custom-go").onclick = () => send("custom_range", {from: $("custom-from").value, to: $("custom-to").value});
Buddy.on("custom_result", r => { $("custom-error").textContent = r.error; });
$("report-export").onclick = () => send("export_report", {format: $("report-format").value});

/* Graph colours (Python's data_manager.chart_colors): the graphs' own - the
   theme's when there's none - and each project's, on its bars. */
function applyColors(colors) {
    colorKey = JSON.stringify(colors || {});
    if (colors && colors.chart) document.body.style.setProperty("--chart", colors.chart);
    else document.body.style.removeProperty("--chart");
}
function projectColor(project) {
    return (data && data.colors && data.colors.projects[project]) || null;
}
function paint(node, project) {
    const color = projectColor(project);
    if (color) node.style.setProperty("--chart", color);
    else node.style.removeProperty("--chart");
    return node;
}
function cssHex(rgb) {
    const [r, g, b] = (rgb.match(/[\d.]+/g) || [0, 0, 0]).map(Number);
    return "#" + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, "0")).join("");
}
function swatch(project) {
    const own = projectColor(project);
    return el("button.swatch", {
        type: "button", "aria-label": `Colour for ${project}`,
        title: own ? "Click to change – right-click for the graphs' colour" : "Click to give it a colour of its own",
        onclick: e => Buddy.pickColor({
            hex: own || cssHex(getComputedStyle(e.currentTarget).backgroundColor), title: project, at: e.currentTarget,
            onPick: hex => send("set_project_color", {project, color: hex}),
        }),
        oncontextmenu: e => { e.preventDefault(); if (own) send("set_project_color", {project, color: null}); },
    });
}

function rateDialog(project) {
    const info = data.rates[project] || {rate: 0, currency: "USD"};
    const rate = el("input.field", {inputmode: "decimal", value: info.rate ? info.rate.toFixed(2) : "",
                                    placeholder: "0.00", autocomplete: "off"});
    const currency = el("select.field", {}, data.currencies.map(c => el("option", {value: c.code, text: c.label})));
    currency.value = info.currency || "USD";
    const save = close => { send("set_rate", {project, rate: rate.value, currency: currency.value}); close(); };
    rate.addEventListener("keydown", e => { if (e.key === "Enter") save(dialog.close); });
    const dialog = Buddy.modal({
        title: `Hourly rate - ${project}`,
        body: [
            el("div.row", {}, [rate, currency, el("span.muted", {text: "/ hour"})]),
            el("p.modal-text.muted.small", {text: "Leave it empty or 0 to hide earnings for this project."}),
        ],
        buttons: [{label: "Cancel"}, {label: "Save", kind: "accent", onClick: save}],
    });
}

let invoiceDialog = null;
$("invoice").onclick = () => {
    if (!data.projects.length) {
        return Buddy.modal({title: "Generate invoice", body: el("p.modal-text", {text: "No tracked projects yet – nothing to invoice."}),
                            buttons: [{label: "OK", kind: "accent"}]});
    }
    const project = el("select.field", {}, data.projects.map(p => el("option", {value: p, text: p, translate: "no"})));
    if (data.projects.includes(data.current_project)) project.value = data.current_project;
    const hint = el("div.muted.small");
    const showRate = () => {
        const info = data.rates[project.value];
        hint.textContent = info && info.text ? `Rate: ${info.text}` : "No rate set for this project – amounts are left blank on the invoice.";
    };
    project.onchange = showRate;
    showRate();
    const from = el("input.field", {type: "date", value: $("custom-from").value});
    const to = el("input.field", {type: "date", value: $("custom-to").value});
    const billTo = el("input.field", {placeholder: "Client or company name"});
    const number = el("input.field", {placeholder: `e.g. ${dateInput(today).slice(0, 7).replace("-", "")}-01`});
    const error = el("div.field-error");
    invoiceDialog = Buddy.modal({
        title: "Generate invoice",
        wide: true,
        body: [el("div.form-grid", {}, [
            el("label.lbl.full", {}, ["Project", project]), el("div.full", {}, hint),
            el("label.lbl", {}, ["From", from]), el("label.lbl", {}, ["To", to]),
            el("label.lbl", {}, ["Bill to (optional)", billTo]), el("label.lbl", {}, ["Invoice # (optional)", number]),
        ]), error],
        buttons: [{label: "Cancel"}, {label: "Generate PDF…", kind: "accent", onClick: () => {
            error.textContent = "";
            send("invoice", {project: project.value, from: from.value, to: to.value, bill_to: billTo.value, number: number.value});
        }}],
        onClose: () => { invoiceDialog = null; },
    });
    invoiceDialog.error = error;
};
Buddy.on("invoice_result", r => {
    if (!invoiceDialog) return;
    if (r.ok) invoiceDialog.close(); else invoiceDialog.error.textContent = r.message;
});

// ------------------------------------------------------------------ data

function fillFormats(select, formats) {
    const keep = select.value;
    select.replaceChildren(...formats.map(f => el("option", {value: f, text: f})));
    select.value = formats.includes(keep) ? keep : (formats.includes("PDF") ? "PDF" : formats[0]);
}

Buddy.on("data", d => {
    data = d;
    applyColors(d.colors);
    $("this_project-title").translate = !d.current_project;
    $("this_project-title").textContent = d.current_project || "This project";
    const scope = $("history-scope");
    scope.replaceChildren(
        el("option", {value: "__current__", text: "Project open in Resolve"}),
        el("option", {value: "__all__", text: "All projects"}),
        ...d.projects.map(p => el("option", {value: p, text: p, translate: "no"})),
    );
    scope.value = d.history.scope;
    for (const select of document.querySelectorAll("[data-format]")) fillFormats(select, d.history_formats);
    fillFormats($("report-format"), d.report_formats);
    renderList("this_project");
    renderList("history");
    renderReports();
    drawCard();
});
