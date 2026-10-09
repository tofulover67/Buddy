/* The Web tab's bar (pages/web/page.py): its tabs, back and forward, the
   address (with Python's suggestions under it), downloads, a menu and the
   find bar. Python keeps the tabs and pages; this draws them and says what
   was clicked. A tab's right-click menu and the ... menu are Python's own. */
"use strict";

const {el, icon, send} = Buddy;
const $ = id => document.getElementById(id);
const DRAG_START = 5;

let state = {tabs: [], downloads: {recent: []}};
let editing = false;            // the address is being typed in: leave it alone
let shownTab = null;            // whose address it shows
let toastTimer = 0;
let keys = {};                  // the platform's names for the bar's keys (Python's key_names)
let lastDrawn = "";             // the state last drawn: the same again isn't redrawn
let pressing = false;           // a button is down: a redraw now would lose the click
let pending = null;             // a state that came meanwhile, drawn once it's up

$("new").append(icon("plus"));
$("back").append(icon("left"));
$("forward").append(icon("right"));
$("reload").append(icon("refresh"));
$("downloads").prepend(icon("download"));
$("more").append(icon("more"));

$("new").onclick = () => send("new_tab");
$("new").oncontextmenu = e => { e.preventDefault(); send("new_tab", {private: true}); };
$("back").onclick = () => send("back");
$("forward").onclick = () => send("forward");
$("reload").onclick = () => send(activeTab()?.loading ? "stop" : "reload");
$("downloads").onclick = () => send("downloads");
$("profile").onclick = () => send("profiles");
$("more").onclick = () => send("more");
$("zoom").onclick = () => send("zoom_reset");

$("address-form").onsubmit = e => {
    e.preventDefault();
    editing = false;
    send("go", {text: $("address").value});
};
$("address").addEventListener("focus", () => {
    editing = true;
    send("sites");
    requestAnimationFrame(() => $("address").select());
});

/* Suggestions: Python lists them under the address (its own list over the
   page) as it's typed; the arrow keys move through them and put the one
   picked in the address, Enter goes to it, Esc closes them. */
let suggesting = false;
function suggest() {
    const r = $("address").getBoundingClientRect();
    suggesting = true;
    send("suggest", {text: $("address").value, rect: {x: r.left, y: r.top, w: r.width, h: r.height}});
}
function unsuggest() {
    if (suggesting) send("suggest_close");
    suggesting = false;
}
Buddy.on("suggest_pick", p => {
    if (p.url) { $("address").value = p.url; $("address").setSelectionRange(p.url.length, p.url.length); }
});
Buddy.on("address_text", a => {
    $("address").value = a.text || "";
    $("address").setSelectionRange($("address").value.length, $("address").value.length);
    suggest();
});

/* Finishing what's typed: the best site starting with it (Python ranks
   them - browser.py ranked_sites - and sends them on focus), the rest
   selected so typing on replaces it and Backspace takes it away. The same
   rule as browser.py autofill - change one, change both. */
let sites = [];
Buddy.on("sites", s => { sites = s.sites || []; });

function autofill(typed) {
    if (!typed || typed.includes(" ") || typed.split("://").pop().includes("/")) return null;
    let lower = typed.toLowerCase();
    for (const prefix of ["https://", "http://"]) {
        if (lower.startsWith(prefix)) lower = lower.slice(prefix.length);
    }
    if (!lower) return null;
    const www = lower.startsWith("www.");
    for (const site of sites) {
        const candidate = www ? "www." + site : site;
        if (candidate.startsWith(lower) && candidate !== lower) return typed + candidate.slice(lower.length);
    }
    return null;
}

$("address").addEventListener("input", e => {
    // Only while adding to the end: deleting, or typing in the middle, is left alone.
    const input = $("address");
    if (!(e.inputType || "").startsWith("insert") || input.selectionStart !== input.value.length) return;
    const typed = input.value;
    const full = autofill(typed);
    if (full) {
        input.value = full;
        input.setSelectionRange(typed.length, full.length);
    }
});
$("address").addEventListener("input", () => {
    if ($("address").value.trim()) suggest(); else unsuggest();
});
$("address").addEventListener("blur", () => { editing = false; unsuggest(); $("address").value = state.url || ""; });
$("address").addEventListener("keydown", e => {
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && suggesting) {
        e.preventDefault();
        send("suggest_move", {step: e.key === "ArrowDown" ? 1 : -1});
    } else if (e.key === "Escape") {
        if (suggesting) { unsuggest(); return; }
        $("address").value = state.url || "";
        $("address").blur();
    } else if (e.key === "Enter") {
        suggesting = false;
    }
});

/* Find on page: Enter the next, Shift+Enter the one before, Esc closes. */
$("find-prev").append(icon("left"));
$("find-next").append(icon("right"));
$("find-close").append(icon("close"));
let findTimer = 0;
const find = back => send("find", {text: $("find-text").value, back: !!back, case: $("find-case").checked});
$("find-text").addEventListener("input", () => { clearTimeout(findTimer); findTimer = setTimeout(() => find(false), 120); });
$("find-text").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); find(e.shiftKey); }
    else if (e.key === "Escape") { e.preventDefault(); closeFind(); }
});
$("find-case").onchange = () => find(false);
$("find-prev").onclick = () => find(true);
$("find-next").onclick = () => find(false);
$("find-close").onclick = () => closeFind();
function closeFind() {
    $("find").hidden = true;
    send("find_close");
}
Buddy.on("find_open", f => {
    $("find").hidden = false;
    if (f.text && !$("find-text").value) $("find-text").value = f.text;
    $("find-text").focus();
    $("find-text").select();
    if ($("find-text").value) find(false);
});
Buddy.on("find_close", () => { $("find").hidden = true; $("find-count").textContent = ""; });
Buddy.on("find_result", r => {
    const none = !!$("find-text").value && !r.total;
    $("find-count").textContent = !$("find-text").value ? "" : none ? "No matches" : `${r.active} of ${r.total}`;
    $("find-count").classList.toggle("none", none);
});

// The wheel scrolls the tabs sideways when there are more than fit.
$("tabs").addEventListener("wheel", e => {
    if (!e.deltaY) return;
    e.preventDefault();
    $("tabs").scrollLeft += e.deltaY;
}, {passive: false});

new ResizeObserver(() => send("size", {height: $("bar").getBoundingClientRect().height})).observe($("bar"));

const activeTab = () => state.tabs.find(t => t.id === state.active);

function tabNode(tab) {
    const marks = [];
    if ((tab.audible && !tab.muted) || tab.paused) {
        // Pause and play whatever the page is playing, without opening it -
        // before the speaker, so it stays put when the speaker goes.
        const label = tab.paused ? "Play" : "Pause";
        const media = el("button.wb-mark.wb-media", {type: "button", title: label, "aria-label": label});
        media.append(icon(tab.paused ? "play" : "pause"));
        media.onclick = e => { e.stopPropagation(); send("media", {id: tab.id}); };
        marks.push(media);
    }
    if (tab.audible || tab.muted) {
        const title = tab.muted ? "Unmute tab" : state.ducked ? "Lowered while another app plays sound - click to mute"
            : "Mute tab";
        const sound = el(`button.wb-mark.wb-sound${state.ducked && !tab.muted ? ".ducked" : ""}`,
                         {type: "button", title, "aria-label": tab.muted ? "Unmute tab" : "Mute tab"});
        sound.append(icon(tab.muted ? "mute" : "volume"));
        sound.onclick = e => { e.stopPropagation(); send("mute", {id: tab.id}); };
        marks.push(sound);
    }
    if (tab.crashed) marks.unshift(el("span.wb-mark.wb-crashed", {title: "This page stopped working - reload it"},
                                      [icon("warning")]));
    if (tab.capture) {
        const what = {screen: "Sharing your screen", camera: "Using your camera", mic: "Using your microphone"}[tab.capture];
        marks.unshift(el("span.wb-mark.wb-capture", {title: what}, [icon(tab.capture === "screen" ? "tile" : tab.capture)]));
    }
    if (tab.asleep) marks.push(el("span.wb-mark", {title: "Asleep - it reloads when you open it"}, [icon("moon")]));
    else if (tab.awake || tab.never) marks.push(el("span.wb-mark", {title: tab.why || "Kept awake"}, [icon("eye")]));
    const close = el("button.wb-close", {type: "button", title: `Close tab (${keys.close || "Ctrl+W"})`,
                                         "aria-label": "Close tab", tabindex: "-1"});
    close.append(icon("close"));
    close.onclick = e => { e.stopPropagation(); send("close", {id: tab.id}); };
    const face = tab.loading ? el("span.wb-spin")
        : tab.icon ? el("img.wb-icon", {src: tab.icon, alt: "", draggable: "false"})
        : icon(tab.private ? "private" : "globe");
    if (tab.private && tab.icon && !tab.loading) marks.unshift(el("span.wb-mark.wb-private", {title: "Private tab"}, [icon("private")]));
    // translate="no": a page's title is the site's, not Buddy's.
    const node = el("div.wb-tab", {role: "tab", "aria-selected": String(tab.id === state.active),
                                   title: tab.why ? `${tab.title}\n${tab.why}` : tab.title, tabindex: "0",
                                   "data-id": String(tab.id), "data-pinned": String(!!tab.pinned),
                                   "data-asleep": String(!!tab.asleep), "data-private": String(!!tab.private)}, [
        face, el("span.wb-title", {text: tab.title, translate: "no"}), ...marks, close,
    ]);
    node.onclick = () => { if (!tabDrag.justDropped) send("select", {id: tab.id}); };
    node.addEventListener("pointerdown", e => startTabDrag(e, node, tab.id));
    // Middle-click closes: handled on the row, so the whole row's height counts (below).
    node.oncontextmenu = e => { e.preventDefault(); send("menu", {id: tab.id}); };
    // From the keyboard: Enter or Space shows it, Delete closes it, the arrows move along the row.
    node.onkeydown = e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); send("select", {id: tab.id}); }
        else if (e.key === "Delete") { e.preventDefault(); send("close", {id: tab.id}); }
        else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            const next = e.key === "ArrowRight" ? node.nextElementSibling : node.previousElementSibling;
            if (next) next.focus();
        } else if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
            e.preventDefault();
            send("menu", {id: tab.id});
        }
    };
    return node;
}

/* Dragging a tab left or right, as in Chrome: it follows the pointer along
   the row, the others slide aside to make room, and on release it settles
   into its place and Python hears where that is ("move"). The row's own
   order isn't touched until the drop, so every tab's layout position
   (offsetLeft) stays what it was and the slots are read from that; only
   transforms move. Redraws from Python wait until it's dropped. */
const tabDrag = {id: null, node: null, x: 0, grab: 0, moved: false, justDropped: false,
                 pointer: 0, from: 0, to: 0, others: [], gap: 0, timer: 0};
const SLIDE = "transform .14s ease";

function startTabDrag(e, node, id) {
    if (e.button !== 0 || e.target.closest("button")) return;      // its own buttons click as ever
    Object.assign(tabDrag, {id, node, x: e.clientX, moved: false});
    node.setPointerCapture(e.pointerId);
}

function dragLayout() {
    const d = tabDrag, row = $("tabs"), r = row.getBoundingClientRect();
    // Where the tab's left edge wants to be, in the row's scrolled content.
    const want = d.pointer - r.left + row.scrollLeft - d.grab;
    const left = Math.max(0, Math.min(want, row.scrollWidth - d.node.offsetWidth));
    d.node.style.transform = `translateX(${left - d.node.offsetLeft}px)`;
    const centre = left + d.node.offsetWidth / 2;
    d.to = d.others.filter(n => n.offsetLeft + n.offsetWidth / 2 < centre).length;
    const shift = d.node.offsetWidth + d.gap;
    d.others.forEach((n, i) => {
        // Past the dragged tab's old place and not yet past its new one: out of its way.
        const x = i >= d.from && i < d.to ? -shift : i >= d.to && i < d.from ? shift : 0;
        n.style.transform = x ? `translateX(${x}px)` : "";
    });
}

$("tabs").addEventListener("pointermove", e => {
    const d = tabDrag;
    if (d.id === null) return;
    d.pointer = e.clientX;
    if (!d.moved) {
        if (Math.abs(e.clientX - d.x) < DRAG_START) return;
        d.moved = true;
        const kids = [...$("tabs").children];
        d.others = kids.filter(n => n !== d.node);
        d.from = d.to = kids.indexOf(d.node);
        d.grab = d.x - d.node.getBoundingClientRect().left;
        d.gap = parseFloat(getComputedStyle($("tabs")).columnGap) || 0;
        d.node.classList.add("dragging");
        d.others.forEach(n => { n.style.transition = SLIDE; });
        // Near either end of a row that scrolls, it scrolls along - even
        // while the pointer holds still.
        d.timer = setInterval(() => {
            const row = $("tabs").getBoundingClientRect();
            if (d.pointer < row.left + 28) $("tabs").scrollLeft -= 10;
            else if (d.pointer > row.right - 28) $("tabs").scrollLeft += 10;
            else return;
            dragLayout();
        }, 16);
    }
    dragLayout();
});

function endTabDrag() {
    const d = tabDrag;
    if (d.id === null) return;
    clearInterval(d.timer);
    if (d.moved) {
        const row = $("tabs"), id = d.id, node = d.node, index = d.to;
        const was = node.getBoundingClientRect().left;
        // The row in its new order; the tab then eases from where it was let go.
        d.others.forEach(n => { n.style.transition = ""; n.style.transform = ""; });
        node.style.transform = "";
        row.insertBefore(node, d.others[index] || null);
        node.classList.remove("dragging");
        node.style.transition = "none";
        node.style.transform = `translateX(${was - node.getBoundingClientRect().left}px)`;
        node.getBoundingClientRect();
        node.style.transition = SLIDE;
        node.style.transform = "";
        setTimeout(() => { node.style.transition = ""; }, 200);
        send("move", {id, index});
        d.justDropped = true;                          // the click that ends a drag isn't a select
        setTimeout(() => { d.justDropped = false; }, 0);
    }
    d.id = d.node = null;
    d.others = [];
    later();
}
$("tabs").addEventListener("pointerup", endTabDrag);
$("tabs").addEventListener("pointercancel", endTabDrag);

/* Middle-click closes the tab under the pointer - anywhere across the row's
   height, its padding and the gaps between tabs included (a 30 px chip in a
   42 px row made a press just above or below it miss). The press itself is
   taken too, or Chromium starts scrolling the row with it. The + button is
   its own. */
const tabsRow = document.querySelector(".wb-tabs-row");
function tabAtX(x) {
    return [...$("tabs").children].find(n => {
        const r = n.getBoundingClientRect();
        return x >= r.left - 1 && x <= r.right + 1;
    }) || null;
}
// In the tab row's band of the bar: the row, and the bar's padding above and below it.
function inTabBand(y) {
    const r = tabsRow.getBoundingClientRect();
    return y >= r.top - 8 && y <= r.bottom + 3;
}
$("bar").addEventListener("mousedown", e => {
    if (e.button === 1 && inTabBand(e.clientY) && !e.target.closest(".wb-new")) e.preventDefault();
});
$("bar").addEventListener("auxclick", e => {
    if (e.button !== 1 || e.target.closest(".wb-new") || !inTabBand(e.clientY)) return;
    const node = e.target.closest(".wb-tab") || tabAtX(e.clientX);
    if (node === null) return;
    e.preventDefault();
    send("close", {id: Number(node.dataset.id)});
});

/* A redraw replaces the tabs' nodes: one between a button going down and
   coming up lost the click (a page loading redraws the bar many times a
   second). So none is drawn while a button's down - nor while a tab is
   dragged - and the latest is drawn once it's up, after its click. Up is
   the mouseup, not the pointerup: Chromium sends the mouseup and click a
   step after the pointerup, so a redraw then still lost the click. */
document.addEventListener("pointerdown", () => { pressing = true; }, true);
const released = () => { pressing = false; setTimeout(later, 0); };
document.addEventListener("mouseup", released, true);
document.addEventListener("pointercancel", released, true);
window.addEventListener("blur", () => { if (pressing) released(); });
function later() {
    if (pending && !pressing && !(tabDrag.moved && tabDrag.id !== null)) {
        const s = pending;
        pending = null;
        drawBrowser(s);
    }
}

Buddy.on("browser", s => {
    if (pressing || (tabDrag.moved && tabDrag.id !== null)) { pending = s; return; }
    drawBrowser(s);
});

function drawBrowser(s) {
    const key = JSON.stringify(s);
    if (key === lastDrawn) return;                 // nothing changed
    lastDrawn = key;
    state = s;
    if (s.keys) setKeys(s.keys);
    // The tab with the keyboard's focus keeps it through the redraw.
    const focusedTab = document.activeElement && document.activeElement.classList.contains("wb-tab")
        ? document.activeElement.dataset.id : null;
    $("tabs").replaceChildren(...s.tabs.map(tabNode));
    if (focusedTab) $("tabs").querySelector(`.wb-tab[data-id="${focusedTab}"]`)?.focus();
    const tab = activeTab();
    $("bar").classList.toggle("private", !!(tab && tab.private));
    $("address").placeholder = tab && tab.private ? "Search or type an address - private" : "Search or type an address";
    // Left alone while it's typed in - unless that was another tab's.
    if (!editing || s.active !== shownTab) $("address").value = s.url || "";
    shownTab = s.active;
    $("back").disabled = !s.back;
    $("forward").disabled = !s.forward;
    const loading = !!(tab && tab.loading);
    $("reload").replaceChildren(icon(loading ? "close" : "refresh"));
    $("reload").title = loading ? "Stop" : `Reload (${keys.reload || "F5"})`;
    $("progress").style.width = loading ? `${Math.max(8, tab.progress)}%` : "0";
    $("progress").classList.toggle("on", loading);
    $("zoom").hidden = s.zoom === 100;
    $("zoom").textContent = `${s.zoom}%`;
    $("memory").hidden = s.memory === null || s.memory === undefined;
    $("memory").textContent = `${s.memory} MB`;
    // The profile in use, once there's more than one to choose from.
    const p = s.profile || {};
    $("profile").hidden = !p.many;
    $("profile-name").textContent = p.name || "";
    $("profile-dot").style.background = p.color || "";
    const d = s.downloads;
    $("badge").hidden = !d.busy;
    $("badge").textContent = String(d.busy);
    $("downloads").title = d.busy ? (d.progress === null ? "Downloading…" : `Downloading… ${Math.round(d.progress * 100)}%`)
        : "Downloads";
    const selected = $("tabs").querySelector('[aria-selected="true"]');
    if (selected) selected.scrollIntoView({block: "nearest", inline: "nearest"});
}

/* Tooltips with this platform's keys (Cmd on a Mac). */
function setKeys(k) {
    if (JSON.stringify(k) === JSON.stringify(keys)) return;
    keys = k;
    $("new").title = `New tab (${k.new}) - right-click for a private tab (${k.private})`;
    $("back").title = `Back (${k.back})`;
    $("forward").title = `Forward (${k.forward})`;
    $("zoom").title = `Zoom - ${k.zoom} for actual size`;
    $("find-next").title = `Next (Enter, ${k.find_next})`;
}

Buddy.on("focus_address", () => {
    send("sites");                      // what it may finish with, fresh
    $("address").focus();
    $("address").select();
});

/* A finished download: its name for a few seconds - drag it straight
   into Resolve (the Downloads bin), or click to see the Downloads window. */
let press = null, dragging = false;
Buddy.on("toast", t => {
    clearTimeout(toastTimer);
    $("toast").hidden = false;
    $("toast").replaceChildren(icon(t.info ? "info" : "check"), el("span", {text: t.text, translate: "no"}));
    $("toast").dataset.id = t.id || "";
    $("toast").dataset.info = t.info ? "1" : "";
    $("toast").classList.toggle("info", !!t.info);
    $("toast").title = t.info ? t.text
        : t.id ? "Drag into Resolve (it goes to the Downloads bin), or click to see your downloads"
        : "Click to see your downloads";
    toastTimer = setTimeout(() => { if (!dragging) $("toast").hidden = true; }, 8000);
});
$("toast").addEventListener("pointerdown", e => { if (e.button === 0) press = {x: e.clientX, y: e.clientY}; });
$("toast").addEventListener("pointermove", e => {
    if (!press || dragging || !(e.buttons & 1) || !$("toast").dataset.id) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) < DRAG_START) return;
    dragging = true;
    press = null;
    send("drag_download", {id: $("toast").dataset.id});
});
$("toast").addEventListener("pointerup", () => {
    if (press && !dragging && !$("toast").dataset.info) send("downloads");
    press = null;
});
document.addEventListener("dragstart", e => e.preventDefault());
Buddy.on("drag_done", () => { dragging = false; press = null; });
