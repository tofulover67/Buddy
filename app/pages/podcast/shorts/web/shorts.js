"use strict";

// Shorts' view (page.py says what each event carries): the sections selected
// on Resolve's timeline, the short's name and shape, the shorts made so far,
// and Make. Python owns the state; this draws it and says what was clicked.

const $ = id => document.getElementById(id);
const el = Buddy.el;

let state = {sections: [], formats: []};

function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function clips(n) {
    return n === 1 ? "1 clip" : `${n} clips`;
}

function several() {
    return state.separate && state.sections.length > 1;
}

function drawBuild() {
    const build = $("build");
    build.disabled = state.building || !state.clips;
    build.textContent = several() ? `Make ${state.sections.length} shorts` : "Make short";
}

Buddy.on("state", s => {
    state = s;
    $("timeline").textContent = s.timeline || "";
    $("dot").classList.toggle("on", !!s.connected);
    $("problem").textContent = s.problem || "";

    $("sections").replaceChildren(...s.sections.map((part, i) => el("div.sh-section", {}, [
        el("span.sh-n", {text: String(i + 1)}),
        el("span.sh-when", {text: `${clock(part.start)} – ${clock(part.end)}`}),
        el("span.sh-detail", {text: clips(part.clips)}),
        el("span.sh-detail", {text: part.tracks, translate: "no"}),
        el("span.sh-detail.sh-len", {text: clock(part.end - part.start)}),
    ])));
    $("empty").hidden = s.sections.length > 0 || !s.connected;
    $("total").textContent = !s.sections.length ? ""
        : several() || s.sections.length === 1 ? clock(s.seconds) : `${clock(s.seconds)} together`;
    $("over").hidden = !(several() ? s.longest_over : s.over);

    const format = $("format");
    format.replaceChildren(...s.formats.map(f => el("option", {
        value: f.value, text: f.size ? `${f.label} (${f.size[0]} × ${f.size[1]})` : f.label})));
    format.value = s.format;
    $("separate-box").hidden = s.sections.length < 2;
    $("separate").checked = !!s.separate;
    $("open-new").checked = !!s.open_new;
    $("name").placeholder = s.default_name || "";
    drawBuild();
});

let newest = "";

Buddy.on("made", m => {
    // A short was made: the name typed was for it.
    const first = m.shorts.length ? m.shorts[0].name : "";
    if (first !== newest) $("name").value = "";
    newest = first;
    $("made-part").hidden = !m.shorts.length;
    $("made").replaceChildren(...m.shorts.map(short => el("div.sh-section", {}, [
        el("span.sh-name-made", {text: short.name, translate: "no"}),
        el("span.sh-detail", {text: clips(short.clips)}),
        el("span.sh-detail.sh-len", {text: clock(short.seconds)}),
    ])));
    const back = $("back");
    back.hidden = !m.can_go_back;
    back.replaceChildren(el("span", {text: "Back to"}), " ", el("span", {text: `“${m.source}”`, translate: "no"}));
});

$("refresh").append(Buddy.icon("refresh"));
$("refresh").addEventListener("click", () => Buddy.send("refresh"));
$("format").addEventListener("change", e => Buddy.send("option", {key: "format", value: e.target.value}));
$("separate").addEventListener("change", e => Buddy.send("option", {key: "separate", value: e.target.checked}));
$("open-new").addEventListener("change", e => Buddy.send("option", {key: "open_new", value: e.target.checked}));
$("back").addEventListener("click", () => Buddy.send("back"));
$("build").addEventListener("click", () => Buddy.send("build", {name: $("name").value}));
$("name").addEventListener("keydown", e => {
    if (e.key === "Enter" && !$("build").disabled) $("build").click();
});

Buddy.on("toast", t => Buddy.toast(t.text));
Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}),
                                    buttons: [{label: "OK", kind: "accent"}]}));
