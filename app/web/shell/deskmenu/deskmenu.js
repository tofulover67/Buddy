/* The taskbar's popup (core/desk_web.py DeskMenu): Programs - every tool,
   grouped as in the rail, searchable, each with a pin - or one taskbar
   button's actions. It reports its size; the shell fits the window to it. */
"use strict";

const {el, icon, send} = Buddy;
const menu = document.getElementById("menu");

function reportSize() {
    const r = menu.getBoundingClientRect();
    // Room for the hard shadow past the card's right and bottom.
    send("size", {width: Math.ceil(r.width + 8), height: Math.ceil(r.height + 8)});
}
new ResizeObserver(reportSize).observe(menu);

document.addEventListener("keydown", e => {
    if (e.key === "Escape") send("act", {action: "close"});
});

// Programs: every tool in the sidebar, grouped - or, with something typed
// in its search (or in the taskbar's Search box), what the tool search
// finds (tool_search.js): every tool that works, by name or by what it
// does. ↑/↓ choose, Enter opens.
let search = null;

function pinButton(item) {
    return el("button.menu-pin", {
        type: "button", "aria-pressed": String(item.pinned),
        title: item.pinned ? "Unpin from the taskbar" : "Pin to the taskbar",
        "aria-label": item.pinned ? `Unpin ${item.label} from the taskbar` : `Pin ${item.label} to the taskbar`,
        onclick: () => send("pin", {id: item.id, on: !item.pinned}),
    }, [icon("pin")]);
}

function programs(m) {
    const typed = m.query !== undefined ? m.query : (search ? search.value : "");
    search = el("input.field.menu-search", {type: "search", placeholder: "Search tools", "aria-label": "Search tools",
                                            autocomplete: "off", spellcheck: "false", value: typed});
    const list = el("div.menu-list");
    const byId = new Map(m.groups.flatMap(g => g.items).map(item => [item.id, item]));
    let active = 0;

    const drawGroups = () => {
        list.replaceChildren();
        for (const group of m.groups) {
            if (group.heading) list.append(el("div.menu-group", {text: group.heading}));
            list.append(...group.items.map(item => el("div.menu-row", {"data-state": item.state}, [
                el("button.menu-open", {type: "button", role: "menuitem", text: item.label,
                                        onclick: () => send("open", {id: item.id})}),
                pinButton(item),
            ])));
        }
    };
    const markActive = () => {
        const rows = [...list.querySelectorAll(".menu-row")];
        rows.forEach((row, i) => row.classList.toggle("active", i === active));
        if (rows[active]) rows[active].scrollIntoView({block: "nearest"});
    };
    const drawResults = () => {
        const query = search.value;
        const found = ToolSearch.match(m.tools, query);
        active = Math.max(0, Math.min(active, found.length - 1));
        if (!found.length) {
            list.replaceChildren(el("div.menu-empty", {text: "No tools match that"}));
            return;
        }
        list.replaceChildren(...found.map(f => {
            const item = byId.get(f.tool.id);        // in the sidebar: it can be pinned
            return el("div.menu-row.menu-result", {"data-state": item ? item.state : "closed", dataset: {id: f.tool.id}}, [
                el("button.menu-open", {type: "button", role: "menuitem", onclick: () => send("open", {id: f.tool.id})}, [
                    el("span.menu-result-name", {translate: "no"}, ToolSearch.marked(f.name, query)),
                    f.note ? el("span.menu-result-note", f.fromAbout ? {translate: "no", title: f.note} : {text: f.note},
                                f.fromAbout ? ToolSearch.marked(f.note, query) : []) : null,
                ]),
                item ? pinButton(item) : null,
            ]);
        }));
        markActive();
    };
    const draw = () => (search.value.trim() ? drawResults() : drawGroups());

    search.oninput = () => { active = 0; draw(); };
    search.onkeydown = e => {
        const rows = [...list.querySelectorAll(".menu-result")];
        if ((e.key === "ArrowDown" || e.key === "ArrowUp") && rows.length) {
            e.preventDefault();
            active = (active + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length;
            markActive();
        } else if (e.key === "Enter") {
            const row = rows[active] || list.querySelector(".menu-row");
            if (row) row.querySelector(".menu-open").click();
        } else if (e.key === "Escape" && search.value) {
            e.stopPropagation();       // first Esc empties the search; the next one closes
            search.value = "";
            draw();
        }
    };
    const foot = el("div.menu-foot", {}, [
        el("button.btn.ghost", {type: "button", onclick: () => send("settings")}, [icon("gear"), el("span", {text: "Settings"})]),
    ]);
    menu.className = "desk-menu programs";
    menu.replaceChildren(el("div.menu-title", {text: "Programs"}), search, list, foot);
    draw();
    requestAnimationFrame(() => { search.focus(); search.setSelectionRange(search.value.length, search.value.length); });
}

// The taskbar's Search box, typed in while this is open.
Buddy.on("query", q => {
    if (!search || !menu.classList.contains("programs")) return;
    search.value = q.q || "";
    search.dispatchEvent(new Event("input"));
    search.focus();
});

function actions(m) {
    menu.className = "desk-menu actions";
    menu.replaceChildren(el("div.menu-title", {text: m.title}), ...m.items.map(item =>
        el(`button.menu-act${item.danger ? ".danger" : ""}`, {
            type: "button", role: "menuitem", text: item.label,
            onclick: () => send("act", {action: item.action, id: m.id}),
        })));
    requestAnimationFrame(() => { const first = menu.querySelector("button"); if (first) first.focus(); });
}

Buddy.on("menu", m => {
    if (m.mode === "programs") programs(m);
    else actions(m);
    requestAnimationFrame(reportSize);
});
