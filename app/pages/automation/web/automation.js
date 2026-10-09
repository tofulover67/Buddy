/* Automation (page.py): the automations, the one picked - its steps and
   their options - and its run. Python keeps and checks everything; this
   draws it and says what was changed. The run redraws as it goes; the
   steps redraw only when they change, so typing in one isn't cut short. */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);

let S = {automations: [], selected: "", tools: [], run: null};
let drawnEditor = "";       // what the steps were drawn from, to skip redrawing the same

$("new").append(icon("plus"), el("span", {text: "New"}));
$("new").onclick = () => send("new");

const current = () => S.automations.find(a => a.id === S.selected);
const running = () => S.run && S.run.running;

// --------------------------------------------------------------- list --

function drawList() {
    if (!S.automations.length) {
        $("list").replaceChildren(el("div.au-empty", {text: "No automations yet. New makes one – then add its steps."}));
        return;
    }
    $("list").replaceChildren(...S.automations.map(a => {
        const isRunning = running() && S.run.automation === a.id;
        const last = a.last ? (a.last.ok ? `Last run ${a.last.when} · ${a.last.took}` : `Stopped ${a.last.when}`) : "";
        return el("button.au-item", {type: "button", role: "listitem", "aria-current": String(a.id === S.selected),
                                     onclick: () => send("select", {id: a.id})}, [
            el("span.au-item-name", {}, [el("span", {text: a.name, translate: "no"}),
                                         isRunning ? el("span.dots", {}, [el("i"), el("i"), el("i")]) : null]),
            el("span.au-item-sub", {text: `${a.steps.length} ${a.steps.length === 1 ? "step" : "steps"}${last ? " · " + last : ""}`}),
        ]);
    }));
}

// ---------------------------------------------------------------- run --

const MARKS = {done: "check", failed: "close", running: null, waiting: null, not_run: "minus", skipped: "minus"};

function drawRun() {
    const r = S.run;
    if (!r) return null;
    const title = r.running ? (r.stopping ? `Stopping ${r.name}…` : `Running ${r.name}`)
        : r.ok ? `${r.name} finished` : `${r.name} stopped`;
    const buttons = r.running
        ? [el("button.btn.danger.small", {type: "button", text: "Stop", disabled: r.stopping, onclick: () => send("stop")})]
        : [r.ok ? null : el("button.btn.accent.small", {type: "button", text: "Resume from the step that stopped",
                                                        onclick: () => send("resume")}),
           el("button.btn.small", {type: "button", text: "Close", onclick: () => send("dismiss")})];
    const steps = r.steps.map((s, i) => {
        const mark = MARKS[s.status] ? icon(MARKS[s.status]) : el("span", {text: String(i + 1)});
        return el(`div.au-run-step.${s.status}`, {}, [
            el("span.au-mark", {}, [s.status === "running" ? el("i.au-spin") : mark]),
            el("span.au-run-label", {text: s.label}),
            el("span.au-took", {text: s.took || ""}),
            s.text ? el("span.au-run-detail", {text: s.text}) : null,
            s.status === "running"
                ? el(`div.au-bar${s.pct === null || s.pct === undefined ? ".indeterminate" : ""}`, {},
                     [el("i", {style: `width:${s.pct || 0}%`})]) : null,
        ]);
    });
    const open = document.querySelector(".au-log")?.open;
    const log = el("details.au-log", {open: !!open}, [
        el("summary", {text: "What happened"}),
        el("div.au-log-lines", {}, r.log.map(l => el(`div.au-log-line.${l.kind}`, {}, [
            el("span.au-log-time", {text: l.time}), el("span", {text: l.text})]))),
    ]);
    return el(`section.card.au-run${r.running ? "" : r.ok ? ".ok" : ".bad"}`, {}, [
        el("div.au-run-head", {}, [el("h2.au-run-title", {text: title}),
                                   el("span.au-took", {text: r.took}), ...buttons]),
        r.text ? el(`p.au-run-text${r.ok ? ".ok" : ".bad"}`, {text: r.text}) : null,
        el("div.au-run-steps", {}, steps),
        log,
    ]);
}

// ------------------------------------------------------------- editor --

function optionControl(a, s, o, locked) {
    const set = value => send("option", {id: a.id, step: s.id, key: o.key, value});
    const wide = o.kind === "textarea" || o.kind === "folder";
    let control;
    if (o.asked) {
        control = el("div.au-asked", {text: "Asked when it runs"});
    } else if (o.kind === "check") {
        return el(`label.check.au-opt${wide ? ".wide" : ""}`, {title: o.hint || ""}, [
            el("input", {type: "checkbox", checked: !!o.value, disabled: locked, onchange: e => set(e.target.checked)}),
            el("span", {text: o.label})]);
    } else if (o.kind === "select") {
        control = el("select.field", {disabled: locked, onchange: e => set(e.target.value)},
                     o.choices.map(c => el("option", {value: c.id, text: c.label})));
        control.value = o.value;
    } else if (o.kind === "textarea") {
        control = el("textarea.field", {disabled: locked, spellcheck: "false", onchange: e => set(e.target.value)});
        control.value = o.value || "";
    } else if (o.kind === "folder") {
        const input = el("input.field", {type: "text", disabled: locked, placeholder: "Choose a folder…",
                                         spellcheck: "false", translate: "no", onchange: e => set(e.target.value)});
        input.value = o.value || "";
        control = el("div.au-folder", {}, [input, el("button.btn", {type: "button", text: "Choose…", disabled: locked,
            onclick: () => send("browse", {id: a.id, step: s.id, key: o.key})})]);
    } else {
        control = el("input.field", {type: "text", disabled: locked, spellcheck: "false", onchange: e => set(e.target.value)});
        control.value = o.value || "";
    }
    return el(`div.au-opt${wide ? ".wide" : ""}`, {}, [
        el("span.au-opt-label", {text: o.label + (o.required && !o.asked ? " *" : "")}),
        control,
        o.hint ? el("span.au-hint", {text: o.hint}) : null,
        o.can_ask ? el("label.check.au-ask", {}, [
            el("input", {type: "checkbox", checked: o.asked, disabled: locked,
                         onchange: e => send("ask", {id: a.id, step: s.id, key: o.key, on: e.target.checked})}),
            el("span", {text: "Ask when it runs"})]) : null,
    ]);
}

function stepCard(a, s, i, locked) {
    const move = (to, label, cls, dir) => el(`button.btn.ghost.icon.${cls}`, {
        type: "button", title: label, "aria-label": label, disabled: locked || to < 0 || to >= a.steps.length,
        onclick: () => send("move_step", {id: a.id, step: s.id, to})}, icon(dir));
    return el(`li.au-step${s.missing ? ".missing" : ""}`, {}, [
        el("div.au-step-head", {}, [
            el("span.au-num", {text: String(i + 1)}),
            el("div.au-step-text", {}, [
                el("span.au-step-tool", {text: s.tool_label}),
                el("span.au-step-label", {text: s.label}),
                s.about ? el("span.au-step-about", {text: s.about}) : null,
            ]),
            el("div.au-step-tools", {}, [
                move(i - 1, "Move up", "au-arrow-up", "right"),
                move(i + 1, "Move down", "au-arrow-down", "right"),
                el("button.btn.ghost.icon", {type: "button", title: "Remove this step", "aria-label": "Remove this step",
                                             disabled: locked, onclick: () => send("remove_step", {id: a.id, step: s.id})},
                   icon("trash")),
            ]),
        ]),
        s.options.length ? el("div.au-opts", {}, s.options.map(o => optionControl(a, s, o, locked))) : null,
    ]);
}

function pickStep(a) {
    const body = el("div.au-picker", {}, S.tools.map(t => el("section.au-picker-tool", {}, [
        el("h3.au-step-tool", {text: t.label}),
        ...t.steps.map(step => el("button.au-picker-step", {type: "button", onclick: () => {
            dialog.close();
            send("add_step", {id: a.id, tool: t.tool, step: step.id});
        }}, [el("b", {text: step.label}), el("span", {text: step.about})])),
    ])));
    const dialog = Buddy.modal({title: "Add a step", wide: true, body, buttons: [{label: "Cancel"}]});
}

function drawEditor() {
    const a = current();
    if (!a) {
        return el("section.card.au-editor", {}, [el("p.au-none", {
            text: S.automations.length ? "Pick an automation on the left." : "Make an automation with New – say, " +
                  "Import a folder › Make proxies › Put the bin on a timeline › Sync."})]);
    }
    const locked = running() && S.run.automation === a.id;
    const name = el("input.field.au-name", {type: "text", maxlength: "80", spellcheck: "false", translate: "no",
                                            "aria-label": "Name", disabled: locked,
                                            onchange: e => send("rename", {id: a.id, name: e.target.value})});
    name.value = a.name;
    const run = el("button.btn.accent.au-run-btn", {type: "button", disabled: running() || a.problems.length > 0,
                                                    title: a.problems[0] || "",
                                                    onclick: () => send("run", {id: a.id})},
                   [icon("play"), el("span", {text: "Run"})]);
    const last = a.last ? el(`div.au-last${a.last.ok ? "" : ".bad"}`, {
        text: a.last.ok ? `Last run ${a.last.when}: ${a.last.text}` : `Last run ${a.last.when}: ${a.last.text}`}) : null;
    return el("section.card.au-editor", {}, [
        el("div.au-title-row", {}, [
            name, run,
            el("button.btn", {type: "button", text: "Duplicate", onclick: () => send("duplicate", {id: a.id})}),
            el("button.btn.ghost.icon", {type: "button", title: "Delete this automation", "aria-label": "Delete",
                                         disabled: locked, onclick: async () => {
                if (await Buddy.confirm({title: "Delete this automation?", text: `"${a.name}" and its steps are deleted.`,
                                         ok: "Delete", danger: true})) send("delete", {id: a.id});
            }}, icon("trash")),
        ]),
        last,
        a.problems.length && a.steps.length ? el("ul.au-problems", {}, a.problems.map(p => el("li", {text: p}))) : null,
        a.steps.length ? el("ol.au-steps", {}, a.steps.map((s, i) => stepCard(a, s, i, locked)))
            : el("p.au-none", {text: "No steps yet – add the first one."}),
        el("button.btn.au-add", {type: "button", disabled: locked, onclick: () => pickStep(a)},
           [icon("plus"), el("span", {text: "Add a step"})]),
    ]);
}

// ------------------------------------------------------------- drawing --

// Two fixed slots: the run's panel, redrawn as it goes, and the steps,
// replaced only when they change - a field being typed in stays put.
const runSlot = el("div.au-slot");
const editorSlot = el("div.au-slot");
$("main").append(runSlot, editorSlot);

function draw() {
    drawList();
    // The run shows over the automation it's of - or over any, while it's going.
    const showRun = S.run && (S.run.running || S.run.automation === S.selected);
    const box = runSlot.querySelector(".au-log-lines");
    const follow = !box || box.scrollTop + box.clientHeight >= box.scrollHeight - 4;   // at the end: stay there
    const top = box ? box.scrollTop : 0;
    runSlot.replaceChildren(...(showRun ? [drawRun()] : []));
    const fresh = runSlot.querySelector(".au-log-lines");
    if (fresh) fresh.scrollTop = follow ? fresh.scrollHeight : top;
    const signature = JSON.stringify([current() || null, running() ? S.run.automation : "", S.tools]);
    if (signature !== drawnEditor) {
        drawnEditor = signature;
        editorSlot.replaceChildren(drawEditor());
    }
}

Buddy.on("state", s => {
    S = s;
    draw();
});

// ------------------------------------------------------------ questions --

let answerFields = {};      // the open questions' fields, by name (a folder's Choose… fills one)

Buddy.on("folder", f => {
    const entry = answerFields[f.field];
    if (entry && document.body.contains(entry.control)) entry.control.value = f.value;
});

Buddy.on("questions", q => {
    const fields = answerFields = {};
    const body = el("div.au-picker", {}, [
        el("p.muted", {text: "These are asked each time. Then it runs on its own."}),
        ...q.questions.map((x, n) => {
            const field = `q${n}`;
            let control;
            if (x.kind === "select") {
                control = el("select.field", {}, x.choices.map(c => el("option", {value: c.id, text: c.label})));
                control.value = x.value;
            } else if (x.kind === "check") {
                control = el("input", {type: "checkbox", checked: !!x.value});
            } else if (x.kind === "textarea") {
                control = el("textarea.field", {rows: "5", spellcheck: "false"});
                control.value = x.value || "";
            } else {
                control = el("input.field", {type: "text", spellcheck: "false", translate: x.kind === "folder" ? "no" : null,
                                             placeholder: x.kind === "folder" ? "Choose a folder…" : ""});
                control.value = x.value || "";
            }
            fields[field] = {x, control};
            return el("div.au-question", {}, [
                el("span.au-opt-label", {}, [el("b", {text: x.step_label}), " · ", el("span", {text: x.label})]),
                x.kind === "folder" ? el("div.au-folder", {}, [control, el("button.btn", {type: "button", text: "Choose…",
                    onclick: () => send("browse_answer", {field, value: control.value})})]) : control,
            ]);
        }),
    ]);
    const answers = () => {
        const out = {};
        for (const {x, control} of Object.values(fields)) {
            out[x.id] = out[x.id] || {};
            out[x.id][x.key] = x.kind === "check" ? control.checked : control.value;
        }
        return out;
    };
    Buddy.modal({title: `Run ${q.name}`, wide: true, body, buttons: [
        {label: "Cancel"},
        {label: "Start", kind: "accent", onClick: close => { send("start", {id: q.id, answers: answers()}); close(); }},
    ]});
});

Buddy.on("toast", t => Buddy.toast(t.text, 3200));
