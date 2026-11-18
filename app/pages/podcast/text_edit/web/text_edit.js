"use strict";

/* Text Edit's view: draws the transcript Python sends ("doc") and reports
   what the person does to it. Token ids are positions in doc.tokens; every
   edit carries doc.version, so a transcript that changed underneath is
   refused rather than cut in the wrong place. */

const $ = id => document.getElementById(id);
const docEl = $("te-doc");
let S = null;               // "state"
let D = null;               // "doc"
let els = [];               // token id -> its span
let sel = null;             // [first, last] token ids, or null
let anchor = null;
let dragging = false;
let hits = [], hitAt = -1;
let nowId = null;
let userScrolledAt = 0;
let lastDown = {id: null, at: 0};
let editing = null;

const send = (name, data) => Buddy.send(name, data);
const isMod = e => e.ctrlKey || e.metaKey;

/* --------------------------------------------------------------- state -- */

Buddy.on("state", state => {
    S = state;
    $("te-timeline").textContent = state.timeline ? state.timeline.name : "";
    $("te-dot").classList.toggle("on", !!state.connected);
    if (!state.job.running) {
        // Connected: the timeline's name says so. Otherwise, what's the matter.
        $("te-conn").textContent = state.waiting ? "Waiting for Resolve…" : state.connected ? "" : "Not connected";
    }
    fillSelect($("te-model"), state.models, state.model);
    fillSelect($("te-language"), state.languages, state.language);
    $("te-font").value = state.text_font;
    $("te-size").value = state.text_size;
    docEl.classList.remove("font-sans", "font-serif", "font-mono", "size-small", "size-medium", "size-large", "size-xlarge");
    docEl.classList.add(`font-${state.text_font}`, `size-${state.text_size}`);
    pressed($("te-back"), state.back_to_resolve);
    pressed($("te-follow"), state.follow);
    pressed($("te-inout"), state.set_inout);
    if (document.activeElement !== $("te-pause-over")) $("te-pause-over").value = Number(state.pause_over).toFixed(1);
    $("te-undo").disabled = !state.can_undo;
    $("te-redo").disabled = !state.can_redo;
    $("te-transcribe").disabled = state.job.running || !state.connected;
    $("te-subs").disabled = state.job.running || !state.connected;
    $("te-cancel").hidden = !state.job.running;
    showJob(state.job);
    drawFoot();
    drawNotices();
});

function pressed(button, on) { button.setAttribute("aria-pressed", String(!!on)); }

function fillSelect(select, options, value) {
    const sig = JSON.stringify(options);
    if (select.dataset.sig !== sig) {
        select.replaceChildren(...options.map(o => Buddy.el("option", {value: o.value, text: o.label})));
        select.dataset.sig = sig;
    }
    select.value = value;
}

Buddy.on("job", job => showJob({running: true, ...job}));

function showJob(job) {
    const bar = $("te-progress-bar");
    if (!job || !job.running || !job.total) {
        bar.style.width = "0";
        return;
    }
    const pct = Math.round(100 * Math.min(1, (job.done + (job.fraction || 0)) / job.total));
    bar.style.width = `${pct}%`;
    if (job.file) {
        $("te-conn").textContent = Buddy.t(`Transcribing {name} ({n} of {total}) · {pct}%`)
            .replace("{name}", job.file).replace("{n}", job.done + 1).replace("{total}", job.total).replace("{pct}", pct);
    }
}

Buddy.on("toast", data => Buddy.toast(data.text, data.kind === "error" ? 7000 : 3500));
Buddy.on("alert", data => Buddy.modal({title: data.title, body: Buddy.el("p.modal-text", {text: data.text}),
                                       buttons: [{label: "OK", kind: "accent"}]}));

/* ----------------------------------------------------------------- doc -- */

Buddy.on("doc", doc => {
    const keepSel = D && doc.version === D.version && doc.tokens.length === D.tokens.length;
    D = doc;
    if (!keepSel) { sel = null; anchor = null; }
    drawTracks();
    drawDoc();
    drawFoot();
    drawNotices();
    runFind(false);
});

function drawTracks() {
    for (const kind of ["audio", "video"]) {
        const box = $(kind === "audio" ? "te-audio" : "te-video");
        const tracks = D && D.tracks ? D.tracks[kind] : [];
        if (!tracks.length) {
            box.replaceChildren(Buddy.el("span.muted.small", {text: "None"}));
            continue;
        }
        box.replaceChildren(...tracks.map(t => {
            // Shown by its file - what tells the tracks apart; the track's own
            // name, if it has one, in the tooltip.
            const b = Buddy.el("button.btn.ghost.te-track", {
                type: "button", "aria-pressed": String(t.on),
                title: t.on ? `Click to leave ${t.short} out` : `Click to include ${t.short}`,
                onclick: () => send("toggle_track", {kind, index: t.index}),
            }, [Buddy.el("span", {text: t.short, translate: "no"})]);
            if (t.file || t.name) {
                b.append(Buddy.el("span.te-track-name", {text: t.file || t.name, translate: "no",
                                                         title: t.file && t.name ? t.name : undefined}));
            }
            if (t.empty) b.classList.add("empty");
            return b;
        }));
    }
}

function clock(t) {
    t = Math.max(0, Math.floor(t));
    const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function empty(title, text, ...buttons) {
    return Buddy.el("div.empty", {}, [Buddy.el("div.strong", {text: title}), text ? Buddy.el("div", {text}) : null,
                                      buttons.length ? Buddy.el("div.te-empty-act", {}, buttons) : null].filter(Boolean));
}

function transcribeButton(kind) {
    return Buddy.el(`button.btn${kind || ""}`, {type: "button", text: "Transcribe", disabled: !!(S && S.job && S.job.running),
                                                onclick: () => send("transcribe", {force: false})});
}

/* Subtitles already on the timeline, used as the transcript: the one track
   that has some, or the one picked from a menu when several do. */
function useSubtitles(e) {
    const tracks = (D && D.subtitles) || [];
    if (!tracks.length) return;
    const go = async index => {
        if (D.tokens && D.tokens.some(t => t.k === "w")) {
            const ok = await Buddy.confirm({title: "Use the subtitles as the transcript?", ok: "Use subtitles",
                text: "The text of the clips under the subtitles is replaced by the subtitles' own, and words you fixed by hand there are lost."});
            if (!ok) return;
        }
        send("use_subtitles", {index});
    };
    if (tracks.length === 1) return go(tracks[0].index);
    const r = e.currentTarget.getBoundingClientRect();
    Buddy.menu({x: r.left, y: r.bottom + 2, items: tracks.map(t => ({
        label: Buddy.t("Subtitle track {n} ({count})").replace("{n}", t.index).replace("{count}", t.count),
        raw: true, onClick: () => go(t.index)}))});
}

function subsButton() {
    return Buddy.el("button.btn.accent", {type: "button", text: "Use subtitles", onclick: useSubtitles});
}

function drawDoc() {
    const top = docEl.scrollTop;
    els = [];
    nowId = null;
    if (!D || !D.timeline) {
        docEl.replaceChildren(empty("Not connected to Resolve", D && D.problem ? D.problem : ""));
        return;
    }
    if (!D.tokens.length || D.tokens.every(t => t.k === "p")) {
        const subs = D.subtitles && D.subtitles.length && !(S && S.job && S.job.running);
        docEl.replaceChildren(!(D.missing && D.missing.length)
            ? empty("No dialogue clips", "Put the recording on the timeline, and pick its tracks in Options.")
            : subs ? empty("This timeline already has subtitles",
                           "Use them as the transcript straight away, or press Transcribe for exact word timings.",
                           subsButton(), transcribeButton())
            : empty("Transcribe to see the text", "Press Transcribe: each dialogue clip is transcribed once, on this computer.",
                    transcribeButton(".accent")));
        return;
    }
    const ticks = new Set(D.ticks || []);
    const frag = document.createDocumentFragment();
    const speakerIndex = {};
    Object.keys(D.speakers || {}).sort((a, b) => a - b).forEach((t, i) => { speakerIndex[t] = (i % 4) + 1; });
    for (const [first, last, track] of D.paras) {
        const gutter = Buddy.el("div.te-gutter");
        if (D.multi && track != null) {
            gutter.append(Buddy.el(`div.te-who.s${speakerIndex[track] || 1}`,
                {text: (D.speakers || {})[track] || `A${track}`, title: `A${track}`, translate: "no"}));
        }
        const firstWord = D.tokens.slice(first, last + 1).find(t => t.k === "w") || D.tokens[first];
        gutter.append(Buddy.el("button.te-ts", {type: "button", title: "Jump here", translate: "no",
            text: clock(firstWord.s), dataset: {i: String(first)}}));
        const p = Buddy.el("p.te-text", {translate: "no"});
        for (let i = first; i <= last; i++) {
            const tok = D.tokens[i];
            if (ticks.has(i) && i !== first) p.append(Buddy.el("span.te-cut", {"aria-hidden": "true"}));
            const span = document.createElement("span");
            span.className = "tk";
            span.dataset.i = String(i);
            if (tok.k === "p") {
                span.classList.add("pz");
                span.textContent = "[…]";
                span.title = Buddy.t("Pause {s} s").replace("{s}", (tok.e - tok.s).toFixed(1));
            } else {
                span.textContent = tok.t;
                if (tok.f) span.classList.add("f");
                if (tok.lo) { span.classList.add("lo"); span.title = Buddy.t("Low confidence – double-click to fix"); }
            }
            els[i] = span;
            p.append(span, " ");
        }
        frag.append(Buddy.el("div.te-para", {}, [gutter, p]));
    }
    docEl.replaceChildren(frag);
    docEl.scrollTop = top;
    paintSel();
    paintHits();
}

function drawFoot() {
    $("te-clean").disabled = !(D && D.tokens && D.tokens.length);
    $("te-retranscribe").hidden = !(D && D.tokens && D.tokens.some(t => t.k === "w"));
    $("te-subs").hidden = !(D && D.subtitles && D.subtitles.length);
}

function notice(text, kind, button) {
    const node = Buddy.el(`div.te-notice${kind === "warn" ? ".warn-note" : ""}`, {role: "status"},
                          [Buddy.el("span.te-notice-text", {text})]);
    if (button) node.append(button);
    return node;
}

function drawNotices() {
    const box = $("te-notices");
    const out = [];
    if (S && S.keys_problem) out.push(notice(S.keys_problem, "warn"));
    // Text taken from subtitles sits on one track's clips: the other mics aren't "missing".
    if (D && !D.estimated && D.missing && D.missing.length && D.tokens && D.tokens.some(t => t.k === "w")) {
        const text = D.missing.length === 1
            ? Buddy.t("“{name}” isn't transcribed yet.").replace("{name}", D.missing[0])
            : Buddy.t("{n} clips aren't transcribed yet.").replace("{n}", D.missing.length);
        out.push(notice(text, "info", S && S.job && S.job.running ? null
            : Buddy.el("button.btn", {type: "button", text: "Transcribe", onclick: () => send("transcribe", {force: false})})));
    }
    if (D && D.estimated) {
        out.push(notice("This text comes from the timeline's subtitles, so when each word is said is estimated – cuts can land a little early or late. Transcribe for exact word timings.",
            "info", S && S.job && S.job.running ? null
            : Buddy.el("button.btn", {type: "button", text: "Transcribe", onclick: () => send("transcribe", {force: false})})));
    }
    for (const why of (D && D.failed) || []) out.push(notice(why, "warn"));
    if (D && D.locked && D.locked.length) {
        out.push(notice(Buddy.t("Some clips on {tracks} aren't media files – titles, generators or compound clips – so they aren't transcribed.")
            .replace("{tracks}", D.locked.join(", ")), "warn"));
    }
    box.replaceChildren(...out);
}

/* ----------------------------------------------------------- selection -- */

function range() {
    if (!sel) return [];
    const out = [];
    for (let i = Math.min(...sel); i <= Math.max(...sel); i++) out.push(i);
    return out;
}

function paintSel() {
    for (const e of docEl.querySelectorAll(".tk.sel")) e.classList.remove("sel");
    for (const i of range()) if (els[i]) els[i].classList.add("sel");
    placeSelbar();
}

/* The bar on the selection - Cut, Play, Fix word - just above its first
   word (below it when there's no room), inside the transcript's card. Not
   while the selection is still being dragged out, or a word is being fixed. */
const selbar = $("te-selbar");

function placeSelbar() {
    const first = sel ? els[Math.min(...sel)] : null;
    if (!first || dragging || editing || !D) { selbar.hidden = true; return; }
    const word = first.getBoundingClientRect(), card = selbar.parentElement.getBoundingClientRect();
    if (word.bottom < card.top || word.top > card.bottom) { selbar.hidden = true; return; }
    $("te-sel-fix").hidden = !(sel[0] === sel[1] && D.tokens[sel[0]] && D.tokens[sel[0]].k === "w");
    selbar.hidden = false;
    let top = word.top - card.top - selbar.offsetHeight - 6;
    if (top < 4) top = word.bottom - card.top + 6;
    selbar.style.top = `${top}px`;
    selbar.style.left = `${Math.max(6, Math.min(card.width - selbar.offsetWidth - 6, word.left - card.left))}px`;
}

selbar.addEventListener("mousedown", e => e.preventDefault());      // the transcript keeps the keyboard
$("te-sel-cut").addEventListener("click", () => { if (sel && D) send("strike", {ids: range(), version: D.version}); });
$("te-sel-play").addEventListener("click", () => { if (sel) { seek(Math.min(...sel), true); send("play"); } });
$("te-sel-fix").addEventListener("click", () => { if (sel) startEdit(sel[0]); });
docEl.addEventListener("scroll", placeSelbar, {passive: true});
addEventListener("resize", placeSelbar);

function setSel(a, b) {
    sel = a == null ? null : [a, b == null ? a : b];
    paintSel();
}

function markSel() {
    if (!sel || !D) return;
    const [a, b] = [Math.min(...sel), Math.max(...sel)];
    if (b > a) send("select", {first: a, last: b, version: D.version});
}

function seek(i, keepFocus) {
    if (D && D.tokens[i]) send("seek", {id: i, version: D.version, keep_focus: !!keepFocus});
}

/* A click beside the words - the space between two, the end of a line, the
   paragraph's edge - is a click on the word nearest to it. */
function nearestToken(e) {
    const p = e.target.closest(".te-text");
    if (!p) return null;
    let best = null, bestD = Infinity;
    for (const tk of p.querySelectorAll(".tk")) {
        for (const r of tk.getClientRects()) {
            const dy = e.clientY < r.top ? r.top - e.clientY : e.clientY > r.bottom ? e.clientY - r.bottom : 0;
            const dx = e.clientX < r.left ? r.left - e.clientX : e.clientX > r.right ? e.clientX - r.right : 0;
            const d = dy * 10000 + dx;          // the line first, then along it
            if (d < bestD) { bestD = d; best = tk; }
        }
    }
    return best;
}

/* The token above or below `from` (step -1 / 1): on the next line of text,
   the one nearest to straight up or down. */
function lineStep(from, step) {
    const box = i => els[i].getBoundingClientRect();
    const x = box(from).left, y = box(from).top;
    let i = from + step;
    while (els[i] && Math.abs(box(i).top - y) < 3) i += step;
    if (!els[i]) return from;
    const line = box(i).top;
    let best = i;
    for (; els[i] && Math.abs(box(i).top - line) < 3; i += step) {
        if (Math.abs(box(i).left - x) < Math.abs(box(best).left - x)) best = i;
    }
    return best;
}

docEl.addEventListener("mousedown", e => {
    if (editing) return;
    const ts = e.target.closest(".te-ts");
    if (ts) return;
    const tk = e.target.closest(".tk") || nearestToken(e);
    if (!tk) { setSel(null); return; }
    e.preventDefault();
    docEl.focus({preventScroll: true});
    const i = Number(tk.dataset.i);
    const now = Date.now();
    // Its own double-click: Resolve can take the focus between two clicks.
    if (lastDown.id === i && now - lastDown.at < 450 && D.tokens[i].k === "w") {
        lastDown = {id: null, at: 0};
        return startEdit(i);
    }
    lastDown = {id: i, at: now};
    if (e.shiftKey && anchor != null) {
        setSel(anchor, i);
        markSel();
        return;
    }
    anchor = i;
    dragging = true;
    setSel(i);
    seek(i, true);
});

docEl.addEventListener("mouseover", e => {
    if (!dragging) return;
    const tk = e.target.closest(".tk");
    if (tk) setSel(anchor, Number(tk.dataset.i));
});

addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    placeSelbar();
    if (sel && sel[0] !== sel[1]) markSel();
    else if (S && S.back_to_resolve && sel) seek(sel[0]);
});

docEl.addEventListener("click", e => {
    const ts = e.target.closest(".te-ts");
    if (ts) {
        const i = Number(ts.dataset.i);
        setSel(null);
        seek(i);
    }
});

docEl.addEventListener("dblclick", e => {
    const tk = e.target.closest(".tk");
    if (tk && !editing && D.tokens[Number(tk.dataset.i)].k === "w") startEdit(Number(tk.dataset.i));
});

docEl.addEventListener("wheel", () => { userScrolledAt = Date.now(); }, {passive: true});

function startEdit(i) {
    const span = els[i];
    if (!span) return;
    editing = {i, before: span.textContent};
    placeSelbar();
    span.classList.add("editing");
    span.contentEditable = "true";
    span.focus();
    const r = document.createRange();
    r.selectNodeContents(span);
    const s = getSelection();
    s.removeAllRanges();
    s.addRange(r);
    const finish = commit => {
        if (!editing || editing.i !== i) return;
        const text = span.textContent.trim();
        const before = editing.before;
        editing = null;
        span.contentEditable = "false";
        span.classList.remove("editing");
        span.removeEventListener("keydown", onKey);
        span.removeEventListener("blur", onBlur);
        if (commit && text && text !== before) send("correct", {id: i, text, version: D.version});
        else span.textContent = before;
        docEl.focus({preventScroll: true});
        placeSelbar();
    };
    const onKey = ev => {
        if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
        else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
        ev.stopPropagation();
    };
    const onBlur = () => finish(true);
    span.addEventListener("keydown", onKey);
    span.addEventListener("blur", onBlur);
}

/* ------------------------------------------------------------ keyboard -- */

function typing(target) {
    return target && (target.matches("input, select, textarea") || target.isContentEditable);
}

document.addEventListener("keydown", e => {
    if (document.querySelector(".modal")) return;
    if (isMod(e) && !e.altKey && e.code === "KeyF") {
        e.preventDefault();
        $("te-find").focus();
        $("te-find").select();
        return;
    }
    if (typing(e.target) || editing || e.repeat && (e.code === "KeyZ" || e.code === "KeyY")) return;
    if (isMod(e) && e.code === "KeyZ") {
        e.preventDefault();
        send(e.shiftKey ? "redo" : "undo");
        return;
    }
    if (isMod(e) && e.code === "KeyY") { e.preventDefault(); send("redo"); return; }
    if (!D || !D.tokens.length) return;
    if (e.key === " " && !isMod(e)) { e.preventDefault(); send("play"); return; }
    if ((e.key === "Delete" || e.key === "Backspace") && sel) {
        e.preventDefault();
        send("strike", {ids: range(), version: D.version});
        return;
    }
    if (e.key === "Escape") { if (!optionsEl.hidden) showOptions(false); else setSel(null); return; }
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
        e.preventDefault();
        const step = e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1;
        const from = sel ? (e.shiftKey ? sel[1] : (step < 0 ? Math.min(...sel) : Math.max(...sel))) : (nowId != null ? nowId : -1);
        const lines = e.key === "ArrowUp" || e.key === "ArrowDown";
        const i = lines && els[from] ? lineStep(from, step) : Math.max(0, Math.min(D.tokens.length - 1, from + step));
        if (e.shiftKey && anchor != null) { setSel(anchor, i); markSel(); }
        else { anchor = i; setSel(i); seek(i, true); }
        if (els[i]) els[i].scrollIntoView({block: "nearest"});
    }
});

/* ---------------------------------------------------------------- find -- */

const norm = s => String(s || "").toLowerCase().replace(/[^\p{L}\p{N}_']+/gu, "");

function runFind(jump) {
    const words = String($("te-find").value || "").split(/\s+/).map(norm).filter(Boolean);
    hits = [];
    if (words.length && D && D.tokens.length) {
        const ws = D.tokens.map((t, i) => [i, t]).filter(([, t]) => t.k === "w");
        const ns = ws.map(([, t]) => norm(t.t));
        for (let i = 0; i + words.length <= ws.length; i++) {
            let ok = true;
            for (let j = 0; j < words.length && ok; j++) {
                ok = j === words.length - 1 ? ns[i + j].startsWith(words[j]) : ns[i + j] === words[j];
            }
            if (ok) hits.push([ws[i][0], ws[i + words.length - 1][0]]);
        }
    }
    if (hitAt >= hits.length) hitAt = -1;
    paintHits();
    if (jump && hits.length) goHit(0);
    countText();
}

function countText() {
    const q = $("te-find").value.trim();
    $("te-count").textContent = !q ? "" : !hits.length ? Buddy.t("None")
        : hitAt >= 0 ? Buddy.t("{i} of {n}").replace("{i}", hitAt + 1).replace("{n}", hits.length)
        : Buddy.t("{n} found").replace("{n}", hits.length);
}

function paintHits() {
    for (const e of docEl.querySelectorAll(".tk.hit")) e.classList.remove("hit");
    for (const [a, b] of hits) for (let i = a; i <= b; i++) if (els[i]) els[i].classList.add("hit");
}

function goHit(index) {
    if (!hits.length) return;
    hitAt = (index + hits.length) % hits.length;
    const [a, b] = hits[hitAt];
    anchor = a;
    setSel(a, b);
    seek(a, true);
    if (els[a]) els[a].scrollIntoView({block: "center"});
    countText();
}

$("te-find").addEventListener("input", () => { hitAt = -1; runFind(false); });
$("te-find").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); goHit(hitAt < 0 ? 0 : hitAt + (e.shiftKey ? -1 : 1)); }
    else if (e.key === "Escape") { $("te-find").value = ""; runFind(false); docEl.focus(); }
});

/* ------------------------------------------------------------ playhead -- */

Buddy.on("playhead", data => {
    if (!D || !D.tokens.length) return;
    const t = data.t;
    let id = null;
    if (t != null) {
        let lo = 0, hi = D.tokens.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (D.tokens[mid].s <= t) lo = mid; else hi = mid - 1;
        }
        if (D.tokens[lo].s <= t && t < D.tokens[lo].e) id = lo;
    }
    if (id === nowId) return;
    if (nowId != null && els[nowId]) els[nowId].classList.remove("now");
    nowId = id;
    if (id == null || !els[id]) return;
    els[id].classList.add("now");
    if (S && S.follow && !dragging && Date.now() - userScrolledAt > 3000) {
        const r = els[id].getBoundingClientRect(), box = docEl.getBoundingClientRect();
        if (r.top < box.top + 40 || r.bottom > box.bottom - 40) {
            const smooth = !matchMedia("(prefers-reduced-motion: reduce)").matches;
            els[id].scrollIntoView({block: "center", behavior: smooth ? "smooth" : "auto"});
        }
    }
});

/* ------------------------------------------------------------- buttons -- */

$("te-refresh").addEventListener("click", () => send("refresh"));
$("te-transcribe").addEventListener("click", () => send("transcribe", {force: false}));
$("te-retranscribe").addEventListener("click", async () => {
    const ok = await Buddy.confirm({title: "Transcribe again?", ok: "Re-transcribe",
        text: "Every dialogue clip is transcribed again, and words you fixed by hand go back to what was heard."});
    if (ok) send("transcribe", {force: true});
});
$("te-cancel").addEventListener("click", () => send("cancel_transcribe"));
$("te-model").addEventListener("change", e => send("option", {key: "model", value: e.target.value}));
$("te-language").addEventListener("change", e => send("option", {key: "language", value: e.target.value}));
$("te-font").addEventListener("change", e => send("option", {key: "text_font", value: e.target.value}));
$("te-size").addEventListener("change", e => send("option", {key: "text_size", value: e.target.value}));
$("te-subs").addEventListener("click", useSubtitles);
for (const [id, key] of [["te-back", "back_to_resolve"], ["te-follow", "follow"], ["te-inout", "set_inout"]]) {
    $(id).addEventListener("click", e => send("option", {key, value: e.currentTarget.getAttribute("aria-pressed") !== "true"}));
}
$("te-undo").addEventListener("click", () => send("undo"));
$("te-redo").addEventListener("click", () => send("redo"));
$("te-pause-over").addEventListener("change", e => send("option", {key: "pause_over", value: Number(e.target.value)}));

/* Clean up: every filler word, or every long pause, cut in one go. */
$("te-clean").addEventListener("click", e => {
    const box = e.currentTarget.getBoundingClientRect();
    const n = D ? D.fillers || 0 : 0;
    const seconds = Number($("te-pause-over").value) || 1;
    Buddy.menu({x: box.left, y: box.bottom + 4, items: [
        {label: n ? Buddy.t("Remove filler words ({n})").replace("{n}", n) : Buddy.t("Remove filler words"), raw: true,
         disabled: !n, onClick: () => send("remove_fillers")},
        {label: Buddy.t("Remove pauses over {s} s").replace("{s}", seconds.toFixed(1)), raw: true,
         onClick: () => send("remove_pauses", {seconds})},
    ]});
});

/* Options: beside the transcript, there until closed. */
const optionsEl = $("te-options");

function showOptions(on) {
    optionsEl.hidden = !on;
    $("te-options-btn").setAttribute("aria-expanded", String(on));
    placeSelbar();
}

$("te-options-btn").addEventListener("click", () => showOptions(optionsEl.hidden));
$("te-options-close").addEventListener("click", () => showOptions(false));
$("te-find-icon").append(Buddy.icon("search"));
$("te-options-btn").prepend(Buddy.icon("sliders"));
$("te-shortcuts").addEventListener("click", openShortcuts);

/* ---------------------------------------------------- Shortcuts dialog -- */

const KEY_NAMES = {Space: "Space", Backspace: "Backspace", Delete: "Delete", Enter: "Enter", Escape: "Esc",
                   ArrowLeft: "Left", ArrowRight: "Right", ArrowUp: "Up", ArrowDown: "Down", Home: "Home",
                   End: "End", PageUp: "PageUp", PageDown: "PageDown", BracketLeft: "[", BracketRight: "]",
                   Semicolon: ";", Quote: "'", Comma: ",", Period: ".", Slash: "/", Backslash: "\\",
                   Minus: "-", Equal: "=", Backquote: "`"};

function comboOf(e) {
    let key = KEY_NAMES[e.code];
    if (!key && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3);
    if (!key && /^Digit\d$/.test(e.code)) key = e.code.slice(5);
    if (!key && /^F\d{1,2}$/.test(e.code)) key = e.code;
    if (!key) return "";
    return [isMod(e) ? "Ctrl" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", key].filter(Boolean).join("+");
}

function openShortcuts() {
    if (!S) return;
    const values = {...S.keys};
    const preset = Buddy.el("select.field", {"aria-label": "Start from"},
        [...S.presets.map(p => Buddy.el("option", {value: p.value, text: p.label})), Buddy.el("option", {value: "", text: "Custom"})]);
    preset.value = S.preset || "";
    const fields = {};
    const grid = Buddy.el("div.te-keys");
    for (const c of S.commands) {
        const input = Buddy.el("input.field", {type: "text", readonly: true, value: Buddy.keys(values[c.id] || ""),
                                               "aria-label": c.label, spellcheck: "false"});
        input.addEventListener("keydown", e => {
            if (e.key === "Tab") return;
            e.preventDefault();
            e.stopPropagation();
            if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
            const combo = comboOf(e);
            if (!combo) return;
            values[c.id] = combo;
            input.value = Buddy.keys(combo);
            preset.value = Object.keys(S.preset_keys).find(k => S.commands.every(x => S.preset_keys[k][x.id] === values[x.id])) || "";
        });
        fields[c.id] = input;
        grid.append(Buddy.el("label.muted.small", {text: c.label}), input);
    }
    preset.addEventListener("change", () => {
        const keys = S.preset_keys[preset.value];
        if (!keys) return;
        Object.assign(values, keys);
        for (const c of S.commands) fields[c.id].value = Buddy.keys(values[c.id]);
    });
    const body = Buddy.el("div", {}, [
        Buddy.el("p.modal-text", {text: "Live cuts press these in Resolve for you – Ripple delete once per cut. They must match your Resolve keyboard (DaVinci Resolve > Keyboard Customization). Click a box and press the shortcut."}),
        Buddy.el("div.row", {}, [Buddy.el("span.muted.small", {text: "Start from"}), preset]),
        grid,
    ]);
    Buddy.modal({title: "Resolve shortcuts", body, buttons: [
        {label: "Cancel"},
        {label: "Save", kind: "accent", onClick: close => {
            send("save_keys", preset.value ? {preset: preset.value} : {keys: values});
            close();
        }},
    ]});
}
