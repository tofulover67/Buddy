/* The Web tab's History window (pages/web/history_window.py). Python keeps
   the history and searches it (newest first, a page at a time); this draws
   it by day. Click opens a page in a new tab, Shift+click in the tab you're
   on; the x takes one off the list. */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);
const DAY = 86400;

let typing = 0;

$("search").oninput = () => {
    clearTimeout(typing);
    typing = setTimeout(() => send("search", {query: $("search").value.trim()}), 150);
};
$("more").onclick = () => send("more");
$("clear").onclick = () => send("clear");
document.addEventListener("keydown", e => {
    // The list from the keyboard: arrows move between pages, Enter opens one.
    const item = e.target.closest && e.target.closest(".hi-item");
    if (item && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        const items = [...document.querySelectorAll(".hi-item")];
        const next = items[items.indexOf(item) + (e.key === "ArrowDown" ? 1 : -1)];
        if (next) next.focus();
        return;
    }
    if (e.target === $("search") && e.key === "ArrowDown") {
        e.preventDefault();
        document.querySelector(".hi-item")?.focus();
        return;
    }
    if (e.key === "Escape") {
        if ($("search").value) { $("search").value = ""; send("search", {query: ""}); }
        else send("close");
    }
});

const dayLabel = seconds => {
    const date = new Date(seconds * 1000), today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((today - new Date(date).setHours(0, 0, 0, 0)) / (DAY * 1000));
    if (days === 0) return "Today";
    if (days === 1) return "Yesterday";
    return date.toLocaleDateString(undefined, {weekday: "long", month: "short", day: "numeric",
                                               year: date.getFullYear() === today.getFullYear() ? undefined : "numeric"});
};
const clock = seconds => new Date(seconds * 1000).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"});

function row(e) {
    const remove = el("button.btn.icon.ghost.hi-remove", {type: "button", title: "Remove from history",
                                                          "aria-label": "Remove from history"}, [icon("close")]);
    remove.onclick = ev => { ev.stopPropagation(); send("remove", {ids: [e.id]}); };
    const item = el("div.hi-item", {role: "listitem", title: e.url, tabindex: "0", "data-id": String(e.id)}, [
        el("span.hi-time", {text: clock(e.t), translate: "no"}),
        el("div.hi-main", {}, [
            el("span.hi-title", {text: e.title || e.site || e.url, translate: "no"}),
            el("span.hi-site", {text: e.site || e.url, translate: "no"}),
        ]),
        remove,
    ]);
    item.onclick = ev => send("open", {url: e.url, here: ev.shiftKey});
    item.onauxclick = ev => { if (ev.button === 1) send("open", {url: e.url}); };
    item.onkeydown = ev => {
        if (ev.key === "Enter") send("open", {url: e.url, here: ev.shiftKey});
        else if (ev.key === "Delete") send("remove", {ids: [e.id]});
    };
    return item;
}

Buddy.on("history", h => {
    $("profile").textContent = h.profile || "";
    $("profile").hidden = !h.profile;
    const nodes = [];
    let day = "";
    for (const e of h.items) {
        const label = dayLabel(e.t);
        if (label !== day) { day = label; nodes.push(el("div.hi-day", {text: label})); }
        nodes.push(row(e));
    }
    if (!h.items.length) {
        nodes.push(el("div.hi-empty", {text: h.query ? "Nothing matches." : "No pages visited yet."}));
    }
    const list = $("list"), top = list.scrollTop;
    const focused = document.activeElement?.dataset?.id;           // kept on its page through a redraw
    list.replaceChildren(...nodes);
    list.scrollTop = top;
    if (focused) list.querySelector(`.hi-item[data-id="${focused}"]`)?.focus();
    $("more").hidden = !h.more;
    const n = h.items.length;
    $("count").textContent = !n ? "" : h.more ? `${n}+ pages` : n === 1 ? "1 page" : `${n} pages`;
});
