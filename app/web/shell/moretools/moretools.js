/* More tools (core/more_tools.py): the tools hidden from the sidebar, each
   with a line on what it does. Add puts one back; Open then goes to it. */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);

$("organize").onclick = () => send("organize");
$("close").onclick = () => send("close");
document.addEventListener("keydown", e => { if (e.key === "Escape") send("close"); });

let justAdded = null;      // focus follows the row Add was pressed on

function actions(row) {
    if (row.placeholder) return [el("span.muted.small", {text: "Not on a Mac yet"})];
    if (!row.added) {
        return [el("button.btn.accent.small", {type: "button", text: "Add", "aria-label": `Add ${row.name}`,
                                               onclick: () => { justAdded = row.id; send("add", {id: row.id}); }})];
    }
    return [el("span.more-added", {}, [icon("check"), el("span", {text: "Added"})]),
            el("button.btn.small", {type: "button", text: "Open", "aria-label": `Open ${row.name}`,
                                    dataset: {open: row.id}, onclick: () => send("open", {id: row.id})})];
}

Buddy.on("tools", t => {
    $("list").replaceChildren(...(t.rows.length ? t.rows.map(row => el(`div.more-row${row.added ? ".added" : ""}`, {}, [
        el("div.more-text", {}, [el("div.more-name", {text: row.name}),
                                 row.blurb ? el("div.more-blurb", {text: row.blurb}) : null]),
        el("div.more-act", {}, actions(row)),
    ])) : [el("p.muted", {text: "Every tool is in the sidebar already."})]));
    const open = justAdded && document.querySelector(`[data-open="${justAdded}"]`);
    (open || $("close")).focus();
    justAdded = null;
});
