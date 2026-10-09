/* The Web tab's Clear browsing data window (pages/web/clear_window.py):
   what to forget, from when. Python clears it. */
"use strict";

const {send} = Buddy;
const $ = id => document.getElementById(id);
const boxes = () => [...document.querySelectorAll(".cd-item input")];

function update() {
    $("go").disabled = !boxes().some(b => b.checked);
}
boxes().forEach(b => { b.onchange = update; });
update();

$("cancel").onclick = () => send("close");
$("go").onclick = () => {
    const what = boxes().filter(b => b.checked).map(b => b.value);
    if (what.length) send("clear", {span: $("span").value, what});
};
document.addEventListener("keydown", e => { if (e.key === "Escape") send("close"); });

Buddy.on("clear", c => {
    $("note").textContent = c.many ? `In the profile ${c.profile} only.` : "";
});
Buddy.on("cleared", c => {
    $("note").textContent = c.cookies ? "Cleared. Restart Buddy to finish clearing what sites stored."
        : "Cleared.";
    $("go").disabled = true;
    setTimeout(() => send("close"), c.cookies ? 2600 : 900);
});
