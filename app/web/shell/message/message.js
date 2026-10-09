/* alert() / confirm() / prompt() (core/message_dialog.py). The text is set
   with textContent - it can hold anything an error message says. */
"use strict";

const {send} = Buddy;
const $ = id => document.getElementById(id);

$("ok").onclick = () => send("answer", {ok: true, text: $("field").value});
$("field").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); $("ok").click(); }
});
$("cancel").onclick = () => send("answer", {ok: false});
document.addEventListener("keydown", e => {
    if (e.key === "Escape") send("answer", {ok: false});
});

Buddy.on("message", m => {
    $("title").textContent = m.title;
    $("text").textContent = m.text;
    $("ok").textContent = m.ok;
    $("ok").className = `btn ${m.danger ? "danger" : "accent"}`;
    $("cancel").hidden = !m.cancel;
    $("cancel").textContent = m.cancel || "";
    const field = $("field");
    field.hidden = !m.field;
    if (m.field) {
        field.value = m.field.value || "";
        field.placeholder = m.field.placeholder || "";
        requestAnimationFrame(() => { field.focus(); field.select(); });
        return;
    }
    requestAnimationFrame(() => (m.cancel && m.danger ? $("cancel") : $("ok")).focus());
});
