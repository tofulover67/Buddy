/*
 * Audit's view. page.py reads the timeline and runs the checks
 * (checks.py); this draws what they found. A row's timecode moves Resolve's
 * playhead there; its fix button (when it has one) sends the row's own fix
 * back - asking first for one that takes something off the timeline.
 */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);

const SEVERITY = {
    problem: {icon: "warning", one: "1 problem", many: n => `${n} problems`},
    warning: {icon: "warning", one: "1 warning", many: n => `${n} warnings`},
    note: {icon: "info", one: "1 note", many: n => `${n} notes`},
};
const counted = (severity, n) => n === 1 ? SEVERITY[severity].one : SEVERITY[severity].many(n);

// A group the user closed stays closed across checks (until Buddy restarts).
const closed = new Set();
let state = null;

$("check").addEventListener("click", () => send("check"));

Buddy.on("state", s => {
    state = s;
    $("check").replaceChildren(icon(s.done ? "refresh" : "search"),
        el("span", {text: s.checking ? "Checking…" : s.done ? "Check again" : "Check timeline"}));
    $("check").disabled = s.checking || s.fixing;

    const t = $("timeline");
    t.hidden = !s.timeline;
    t.replaceChildren(...(s.timeline ? [el("b", {text: s.timeline, translate: "no"}),
        ` · ${Math.round(s.fps * 1000) / 1000} fps`,
        s.checked_at ? " · " : "", s.checked_at ? el("span", {text: `Checked at ${s.checked_at}`}) : ""] : []));

    summary(s);
    sound(s);
    groups(s);
    passed(s);
    choose(s);
});

function summary(s) {
    const box = $("summary");
    box.className = "card summary";
    if (s.checking) {
        box.replaceChildren(el("div.strong", {text: s.waiting ? "Waiting for Resolve…" : "Checking the timeline…"}),
            s.waiting ? el("div.muted", {text: "Resolve answers once playback stops."}) : "");
        return;
    }
    if (s.problem) {
        box.classList.add("bad");
        box.replaceChildren(el("div.strong", {text: "Couldn't check the timeline"}), el("div", {text: s.problem}));
        return;
    }
    if (!s.done) {
        box.replaceChildren(
            el("div.strong", {text: s.connected ? "Check the open timeline before you deliver it" : "Not connected to Resolve"}),
            el("div.muted", {text: s.connected
                ? "Missing media, missing fonts, black gaps, flash frames, things turned off, unfinished titles, to-do markers and hard-to-read subtitles – all in one list."
                : "Open Resolve with a project and a timeline, then press Check timeline."}));
        return;
    }
    const chips = ["problem", "warning", "note"].filter(sev => s.counts[sev])
        .map(sev => el(`span.count.${sev}`, {}, [icon(SEVERITY[sev].icon), el("span", {text: counted(sev, s.counts[sev])})]));
    if (!chips.length) {
        box.classList.add("clear");
        box.replaceChildren(el("div.strong.ok-line", {}, [icon("check"), el("span", {text: "All clear"})]),
            el("div.muted", {text: "Nothing turned up in the checks that are on."}));
        return;
    }
    box.replaceChildren(el("div.counts", {}, chips));
}

// ------------------------------------------------------------------ sound --

const db = v => (v > 0 ? "+" : "") + v.toFixed(1);

function sound(s) {
    const box = $("sound");
    const snd = s.sound;
    box.hidden = !s.done || !snd.on;
    if (box.hidden) return;
    const busy = s.checking || s.fixing;
    const button = snd.running
        ? el("button.btn", {type: "button", onclick: () => send("stop_sound")}, [icon("stop"), el("span", {text: "Stop"})])
        : el("button.btn", {type: "button", disabled: busy, onclick: () => send("check_sound")},
            [icon("volume"), el("span", {text: snd.done ? "Check sound again" : "Check sound"})]);
    const target = el("select.target", {"aria-label": "Target", disabled: snd.running,
        onchange: e => send("target", {id: e.target.value})},
        snd.targets.map(t => el("option", {value: t.id, text: t.label, selected: t.id === snd.target})));

    let body;
    if (snd.running) {
        body = el("div.sound-progress", {}, [
            el("div.muted", {text: snd.stage === "measure" ? "Measuring the mix…"
                : snd.stage === "recordings" ? "Listening to each clip's recording…" : "Rendering the mix in Resolve…"}),
            el("div.bar", {}, el("i", {style: `width:${snd.progress}%`})),
        ]);
    } else if (snd.error) {
        body = el("div.sound-error", {}, [el("div.strong", {text: "Couldn't check the sound"}), el("div", {text: snd.error})]);
    } else if (snd.done) {
        body = el("div", {}, [el("div.readings", {}, [
            snd.mixed ? reading("Loudness", snd.lufs === null ? "–" : `${snd.lufs.toFixed(1)} LUFS`, `Target ${snd.target_lufs} LUFS`) : "",
            snd.mixed ? reading("True peak", snd.peak_db === null ? "–" : `${db(snd.peak_db)} dBTP`, `Ceiling ${snd.ceiling} dBTP`) : "",
            snd.listened ? reading("Recordings", snd.clips === 1 ? "1 clip" : `${snd.clips} clips`, "Listened to") : "",
            el("span.muted.small", {text: `Checked at ${snd.checked_at}`}),
        ]), ...snd.unread.map(u => el("div.muted.small", {}, [
            el("span", {text: "Couldn't listen to"}), " ", el("b", {text: u.name, translate: "no"}), " – ", el("span", {text: u.why})]))]);
    } else {
        body = el("div.muted", {text: "Renders the timeline's mix in Resolve and measures its peaks and loudness, then listens to each clip's recording for clipping, dropouts and other faults. Your Deliver settings stay as they are; the render is deleted afterwards."});
    }
    box.replaceChildren(
        el("div.sound-head", {}, [el("div.section-title", {text: "Sound"}), el("span.spacer"), target, button]),
        body);
}

function reading(label, value, against) {
    return el("span.reading", {}, [el("span.muted.small", {text: label}), el("b", {text: value}),
        el("span.muted.small", {text: against})]);
}

// ------------------------------------------------------------------ fixes --

function frames(n) {
    return n === 1 ? "1 frame" : `${n} frames`;
}

function question(fix) {
    const n = fix.end - fix.start;
    if (fix.do === "delete_clip") {
        const then = {
            close: n === 1 ? "Nothing else is there, so everything after it moves up 1 frame to close the space."
                : `Nothing else is there, so everything after it moves up ${n} frames to close the space.`,
            black: n === 1 ? "That leaves 1 frame of black – Audit lists it as a black gap."
                : `That leaves ${n} frames of black – Audit lists them as a black gap.`,
            "": "Another clip's picture shows there instead.",
        }[fix.then];
        return {title: "Delete this clip?", ok: "Delete",
                // Two sentences, each translated on its own: the dialog's text is one line.
                text: `${Buddy.t(fix.sound.length ? "The clip and its sound come off the timeline."
                                                  : "The clip comes off the timeline.")} ${Buddy.t(then)}`};
    }
    if (fix.do === "close_gap") {
        const secs = n / (state.fps || 24);
        return {title: "Close this gap?", ok: "Close gap",
                text: secs < 1
                    ? (n === 1 ? "Everything after it, on every track, moves up 1 frame to close it."
                               : `Everything after it, on every track, moves up ${n} frames to close it.`)
                    : `Everything after it, on every track, moves up ${secs.toFixed(1)} s to close it.`};
    }
    if (fix.do === "delete_marker") {
        return {title: "Remove this marker?", ok: "Remove marker", text: "Only the marker goes – the clips stay as they are."};
    }
    return null;
}

async function makeFix(fixes) {
    if (fixes.length === 1 && state.fixes[fixes[0].do].ask) {
        const ask = question(fixes[0]);
        if (!ask || !await Buddy.confirm({...ask, danger: true})) return;
    }
    send("fix", {fixes});
}

function fixButton(fix, s) {
    if (!fix) return el("span");
    return el("button.btn.small.fix", {type: "button", text: s.fixes[fix.do].label,
        disabled: s.fixing || s.checking || s.sound.running, onclick: () => makeFix([fix])});
}

// ----------------------------------------------------------------- groups --

function groups(s) {
    $("groups").replaceChildren(...(s.done && !s.checking ? s.groups : []).map(g => {
        const busy = s.fixing || s.checking || s.sound.running;
        const head = el("summary.group-head", {}, [
            el(`span.sev.${g.severity}`, {}, [icon(SEVERITY[g.severity].icon)]),
            el("span.group-title", {text: g.title}),
            el("span.badge", {text: String(g.count)}),
            el("span.spacer"),
            g.fix_all ? el("button.btn.small", {type: "button", text: g.fix_all.label, disabled: busy,
                onclick: e => { e.preventDefault(); makeFix(g.fix_all.fixes); }}) : "",
            g.fix ? el("button.btn.small", {type: "button", text: g.fix.label,
                onclick: e => { e.preventDefault(); send("open_tool", {tool_id: g.fix.tool_id}); }}) : "",
        ]);
        const fixable = g.rows.some(r => r.fix);
        const rows = g.rows.map(r => el(`div.row-item.${r.severity}${fixable ? ".fixable" : ""}`, {}, [
            el("button.tc", {type: "button", title: "Go there in Resolve", text: r.tc,
                onclick: () => send("goto", {frame: r.frame})}),
            el("span.where", {text: r.where, translate: "no"}),
            el("span.clip", {text: r.clip, translate: r.own ? "yes" : "no", title: r.clip}),
            el("span.detail", {text: r.detail}),
            r.times > 1 ? el("span.times", {text: `×${r.times}`, title: `Used ${r.times} times`}) : el("span"),
            fixable ? fixButton(r.fix, s) : "",
        ]));
        if (g.count > g.rows.length) {
            rows.push(el("div.more.muted", {text: `And ${g.count - g.rows.length} more`}));
        }
        const box = el("details.card.group", {open: !closed.has(g.id)}, [head, el("p.about.muted", {text: g.about}), el("div.rows", {}, rows)]);
        box.addEventListener("toggle", () => box.open ? closed.delete(g.id) : closed.add(g.id));
        return box;
    }));
}

function passed(s) {
    const box = $("passed");
    const show = s.done && !s.checking && (s.passed.length || s.fonts_unchecked);
    box.hidden = !show;
    if (!show) return;
    box.replaceChildren(
        el("div.section-title", {text: "Passed"}),
        el("div.passed-list", {}, s.passed.map(title => el("span.pass", {}, [icon("check"), el("span", {text: title})]))),
        s.fonts_unchecked ? el("div.muted.small", {text: "Fonts couldn't be checked here."}) : "");
}

function choose(s) {
    const item = c => {
        const box = el("input", {type: "checkbox", checked: c.on, onchange: e => send("toggle", {id: c.id, on: e.target.checked})});
        return el("label.choice", {}, [box, el("span.choice-text", {}, [
            el("span.strong", {text: c.title}), el("span.muted.small", {text: c.about})])]);
    };
    $("choose-list").replaceChildren(
        ...s.checks.filter(c => !c.sound).map(item),
        el("div.choose-sub.section-title", {text: "Sound (with Check sound)"}),
        ...s.checks.filter(c => c.sound).map(item));
}

Buddy.on("toast", t => Buddy.toast(t.text, 4000));
