/* The shell header's view: title, the news orb, the Resolve connection, the
   bug report, dual view and Settings. The shell (core/shell_window.py) decides everything. */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);

$("split").append(icon("split"));
$("settings").append(icon("gear"));
$("bug").append(icon("bug"));
$("bug").onclick = () => send("bug");
for (const b of document.querySelectorAll("[data-action]")) b.onclick = () => send(b.dataset.action);
$("settings").onclick = () => send("settings");
$("orb").onclick = () => send("orb");
$("update").onclick = () => send("update");
$("timer-open").onclick = () => send("timer", {action: "open"});
$("timer-toggle").onclick = () => send("timer", {action: "toggle"});

// A tool's running timer (Time Tracker's). The shell sends it when it starts,
// pauses or changes project; between those the clock counts here.
let timer = null, timerAt = 0, timerTick = 0;
function clock(seconds) {
    const two = n => String(n).padStart(2, "0");
    return `${two(Math.floor(seconds / 3600))}:${two(Math.floor(seconds / 60) % 60)}:${two(seconds % 60)}`;
}
function drawClock() {
    if (!timer) return;
    const more = timer.running ? Math.floor((Date.now() - timerAt) / 1000) : 0;
    $("timer-clock").textContent = clock(timer.seconds + more);
}
function showTimer(t) {
    timer = t || null;
    timerAt = Date.now();
    clearInterval(timerTick);
    $("timer").hidden = !timer;
    if (!timer) return;
    $("timer").classList.toggle("paused", !timer.running);
    $("timer-project").textContent = timer.project;
    const toggle = $("timer-toggle");
    toggle.replaceChildren(icon(timer.running ? "pause" : "play"));
    toggle.title = timer.running ? "Pause the timer"
        : timer.away ? "Paused while you were away – resume the timer" : "Resume the timer";
    toggle.setAttribute("aria-label", timer.running ? "Pause the timer" : "Resume the timer");
    drawClock();
    if (timer.running) timerTick = setInterval(drawClock, 1000);
}
$("split").onclick = () => send("split", {on: $("split").getAttribute("aria-pressed") !== "true"});
$("side").onchange = e => send("side", {id: e.target.value});

function reportSize() { send("size", {height: $("header").getBoundingClientRect().height}); }
new ResizeObserver(reportSize).observe($("header"));

// The view is only as tall as the header: while a dropdown is open, the
// shell grows it down over the panes to make room for the list.
Buddy.dropdownRoom = 420;
let shrinkTimer = 0;
document.addEventListener("buddy:dropdown", e => {
    clearTimeout(shrinkTimer);
    if (e.detail.open) send("overlay", {height: Math.ceil(e.detail.bottom + 24)});
    else shrinkTimer = setTimeout(() => send("overlay", {height: 0}), 180);   // after the closing animation
});

Buddy.on("header", h => {
    // The one place Buddy says whether it can reach Resolve; pages only say
    // what they need from it (a timeline, a bin) once it can.
    // Connected, with a project open: its name, the timeline and that
    // timeline's format - what a tool is about to work on. Theirs, so untranslated.
    const w = h.connected && h.where && h.where.project ? h.where : null;
    const status = $("status");
    if (w) {
        const format = [w.fps ? `${w.fps} fps` : "", w.width && w.height ? `${w.width}×${w.height}` : ""]
            .filter(Boolean).join(", ");
        status.replaceChildren(
            el("span.where-name", {text: w.project, translate: "no"}),
            el("span.where-dot", {text: "·", translate: "no"}),
            w.timeline ? el("span.where-name", {text: w.timeline, translate: "no"})
                       : el("span.where-none", {text: "No timeline open"}),
            format ? el("span.where-format", {text: `(${format})`, translate: "no"}) : "");
    } else {
        status.replaceChildren(el("span", {text: h.connected ? "Connected to Resolve" : "Not connected to Resolve"}));
    }
    status.title = w ? "Connected to Resolve – this is the project and timeline Buddy's tools work on" : "";
    $("connect").textContent = h.connected ? "Reconnect" : "Connect";
    $("connect").title = h.connected ? "Connect to Resolve again – after restarting it, or opening another project"
                                     : "Connect to DaVinci Resolve";
    $("status").className = `status ${h.connected ? "ok" : "bad"}`;
    showTimer(h.timer);
    $("orb").hidden = !h.orb;
    // A newer Buddy (core/updater.py): to install, or installed and waiting for a restart.
    const u = h.update;
    $("update").hidden = !u;
    if (u) {
        $("update").textContent = u.kind === "restart" ? "Restart to update" : "Update";
        $("update").title = u.kind === "restart" ? `Buddy ${u.version} is installed – restart Buddy to start using it`
                                                 : `Buddy ${u.version} is ready to install`;
    }
    // Buddy is the window in use (Off-world's cursor blinks only then).
    document.documentElement.classList.toggle("buddy-active", !!h.active);
    $("split").setAttribute("aria-pressed", String(h.split));
    const side = $("side");
    side.hidden = !h.split;
    side.replaceChildren(...h.choices.map(c => el("option", {value: c.id, text: c.label})));
    if (h.side) side.value = h.side;
});
