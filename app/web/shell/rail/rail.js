/* The shell's nav rail: the tools in the order Settings > Organize sidebar
   left them, with their headings and lines. Click one to show it. Above
   them, the tool search (tool_search.js): while something's typed, what it
   finds - every tool that works, those hidden from the sidebar too - in
   place of the list. ↑/↓ choose, Enter opens, Esc clears. */
"use strict";

const {el, icon, send} = Buddy;

// A tool's mark beside its name (ToolPage.rail_badge): the Web tab playing
// sound, with a button to pause it - or paused, with one to play it again.
// The button comes first, so it stays put when the speaker comes and goes.
// Or a tool at work (ToolPage.working_on) - a spinner, what it's doing on hover.
function badge(item) {
    if (item.badge === "working") {
        const note = item.working || "Working…";
        return [el("span.nav-working", {title: note, "aria-label": note, translate: item.working ? "no" : undefined})];
    }
    if (item.badge !== "sound" && item.badge !== "paused") return [];
    const paused = item.badge === "paused";
    const label = paused ? "Play" : "Pause";
    // A span, not a button: it sits inside the tool's own button.
    const media = el("span.nav-media", {role: "button", tabindex: "0", title: label, "aria-label": label,
                                        onclick: e => { e.stopPropagation(); send("media", {id: item.id}); },
                                        onkeydown: e => {
                                            if (e.key === "Enter" || e.key === " ") {
                                                e.preventDefault(); e.stopPropagation(); send("media", {id: item.id});
                                            }
                                        }}, [icon(paused ? "play" : "pause")]);
    return paused ? [media]
        : [media, el("span.nav-badge", {title: "Playing sound", "aria-label": "Playing sound"}, [icon("volume")])];
}
const rail = document.getElementById("rail");
const list = document.getElementById("rail-list");
const search = document.getElementById("search");
let last = {items: [], more: 0};
let tools = [];
let active = 0;          // the result Enter opens

/* The width the longest entry needs - so a wider theme font never clips a
   name - plus room for a scrollbar, whose coming and going mustn't change it.
   Measured on the tools, never on search results. */
function reportSize() {
    if (search.value.trim()) return;
    let widest = 0;
    for (const item of [...list.children]) {
        const clone = item.cloneNode(true);
        clone.style.cssText = "position:absolute;visibility:hidden;width:max-content";
        list.append(clone);
        widest = Math.max(widest, clone.getBoundingClientRect().width);
        clone.remove();
    }
    const style = getComputedStyle(rail);
    const chrome = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)
                 + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
    send("size", {width: Math.ceil(widest + chrome + 12)});
}

function drawTools(r) {
    list.replaceChildren(...r.items.map(item => {
        if (item.type === "heading") return el("div.nav-heading", {text: item.label});
        if (item.type === "line") return el("div.nav-line", {role: "separator"});
        // Right-click: the tool's own menu (its Settings).
        return el("button.nav-item", {type: "button", "aria-current": item.active ? "page" : undefined,
                                      dataset: {id: item.id},
                                      onclick: () => send("switch", {id: item.id}),
                                      oncontextmenu: e => { e.preventDefault(); send("menu", {id: item.id}); }},
                  [el("span", {text: item.label}), ...badge(item)]);
    }));
    // Tools hidden from the rail (a new install shows only a few): More
    // tools puts them back (core/more_tools.py).
    if (r.more) {
        list.append(el("div.nav-spacer"),
                    el("button.nav-item.nav-more#more", {type: "button", onclick: () => send("more"),
                                                         title: "Add more of Buddy's tools to the sidebar"},
                       [icon("plus"), el("span", {text: "More tools"})]));
    }
    reportSize();
}

// ----------------------------------------------------------------- search

function open(id) {
    search.value = "";
    draw();
    send("switch", {id});
}

function drawResults() {
    const query = search.value;
    const found = ToolSearch.match(tools, query);
    active = Math.max(0, Math.min(active, found.length - 1));
    if (!found.length) {
        list.replaceChildren(el("div.nav-empty", {text: "No tools match that"}));
        return;
    }
    list.replaceChildren(...found.map((f, i) => el(`button.nav-item.nav-result${i === active ? ".active" : ""}`, {
        type: "button", id: `result-${i}`, dataset: {id: f.tool.id},
        onclick: () => open(f.tool.id),
        onmousemove: () => { if (active !== i) { active = i; markActive(); } },
    }, [
        el("span.nav-result-name", {translate: "no"}, ToolSearch.marked(f.name, query)),
        f.note ? el("span.nav-result-note", f.fromAbout ? {translate: "no", title: f.note}
                                                        : {text: f.note}, f.fromAbout ? ToolSearch.marked(f.note, query) : [])
               : null,
    ])));
    markActive();
}

function markActive() {
    [...list.querySelectorAll(".nav-result")].forEach((b, i) => b.classList.toggle("active", i === active));
    search.setAttribute("aria-activedescendant", list.querySelector(".nav-result") ? `result-${active}` : "");
    const shown = list.querySelector(".nav-result.active");
    if (shown) shown.scrollIntoView({block: "nearest"});
}

function draw() {
    if (search.value.trim()) drawResults();
    else drawTools(last);
}

search.addEventListener("input", () => { active = 0; draw(); });
search.addEventListener("keydown", e => {
    const results = list.querySelectorAll(".nav-result");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (!results.length) return;
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
        markActive();
    } else if (e.key === "Enter") {
        const pick = results[active];
        if (pick) open(pick.dataset.id);
    } else if (e.key === "Escape" && search.value) {
        e.stopPropagation();
        search.value = "";
        draw();
    }
});

Buddy.on("rail", r => {
    last = r;
    if (r.tools) tools = r.tools;
    draw();
});
Buddy.on("theme", () => requestAnimationFrame(reportSize));
document.fonts.ready.then(reportSize);
