/*
 * Buddy's shared web runtime - every web tool page loads this after
 * qrc:///qtwebchannel/qwebchannel.js and before its own script.
 *
 *   Buddy.on("event", data => ...)   Python's self.emit("event", data)
 *   Buddy.send("action", data)       calls the page's on_action(data)
 *   Buddy.el("div.card", {...}, [children])   small DOM builder
 *   Buddy.toast("Copied")
 *   Buddy.modal / confirm / menu     dialogs and popup menus
 *   Buddy.pickColor({hex, onPick})   the colour picker
 *   Buddy.t("Saved")                 text in the chosen language - rarely
 *                                    needed: pages are translated as drawn
 *
 * Python owns the state (core/web_page.py); a page's script only draws
 * what it is sent and reports what the user did. The "theme" event is
 * handled here for every page: its variables go on :root.
 *
 * Opened outside Buddy (no Qt transport, e.g. in a normal browser while
 * working on the CSS), sends are logged and Buddy.receive(name, data)
 * plays Python's part by hand.
 */
"use strict";

const Buddy = (() => {
    const handlers = {};
    let backend = null;
    const outbox = [];

    function on(name, fn) {
        (handlers[name] = handlers[name] || []).push(fn);
    }

    function receive(name, data) {
        for (const fn of handlers[name] || []) {
            try {
                fn(data);
            } catch (err) {
                console.error(`handler for "${name}" failed:`, err);
            }
        }
    }

    function send(name, data) {
        const payload = JSON.stringify(data === undefined ? null : data);
        if (backend) backend.send(name, payload);
        else if (window.qt) outbox.push([name, payload]);
        else console.log("[Buddy.send]", name, data);
    }

    // The last theme's variables, so one the new theme doesn't set (Modern's
    // page glow) doesn't stay behind.
    let themeVars = [];
    on("theme", theme => {
        const root = document.documentElement;
        for (const key of themeVars) {
            if (!(key in theme.vars)) root.style.removeProperty(`--${key}`);
        }
        themeVars = Object.keys(theme.vars);
        for (const [key, value] of Object.entries(theme.vars)) {
            root.style.setProperty(`--${key}`, value);
        }
        root.dataset.family = theme.family;
        root.dataset.light = String(theme.light);
    });

    /* Languages. Python sends the chosen language's strings ("i18n":
       {language, lang, strings: {english: translation} | null for English},
       core/i18n.py) and the page is translated here as it's drawn - its
       text, tooltips, placeholders and labels - so a page's script keeps
       writing English. Never touched: what people type (inputs, textareas,
       contenteditable) and anything inside translate="no", which a view
       puts on text people named themselves (palettes, clips, messages).
       The matching is core/i18n.py's (_Catalog.lookup) - change one,
       change both: runs of whitespace are one space, "..." is "…", "Name:"
       comes from "Name", and {placeholders} stand for values that change,
       which are kept as they are - except a {t_name}'s, one of Buddy's own
       words (a marker colour), which is translated too. */
    const i18n = (() => {
        const ATTRS = ["title", "placeholder", "aria-label", "alt"];
        // Not translated at all, text or attributes.
        const TREE_SKIP = 'script, style, [translate="no"], .notranslate';
        // Their text is the user's (or code); their placeholder and title aren't.
        const TEXT_SKIP = "textarea, code, kbd";
        const PH = /\{(\w+)\}/g;
        const seen = new WeakMap();   // text node -> {src, shown}; element -> {attr: {src, shown}}
        let exact = null;             // null: English, nothing to do
        let templates = [];
        let observer = null;

        const norm = s => s.replace(/\s+/g, " ").trim();
        const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

        function load(strings) {
            exact = strings ? new Map(Object.entries(strings)) : null;
            templates = [];
            for (const [key, text] of exact || []) {
                if (!key.includes("{")) continue;
                const literal = key.replace(PH, "");
                if ((literal.match(/[\p{L}\p{M}]/gu) || []).length < 2) continue;   // core/i18n.py is_template
                const names = [];
                const parts = key.split(/(\{\w+\})/);
                const source = parts.map(part => {
                    const m = /^\{(\w+)\}$/.exec(part);
                    if (!m) return escape(part);
                    names.push(m[1]);
                    return "([\\s\\S]+?)";
                }).join("");
                if (!names.length) continue;
                // The longest fixed piece: a cheap test before the regex.
                const hint = parts.filter(p => !/^\{\w+\}$/.test(p)).sort((a, b) => b.length - a.length)[0];
                templates.push({re: new RegExp(`^${source}$`), names, text, hint, weight: literal.length});
            }
            templates.sort((a, b) => b.weight - a.weight);
        }

        function exactHit(key) {
            let hit = exact.get(key);
            if (hit !== undefined) return hit;
            if (key.includes("...") || key.includes("…")) {
                hit = exact.get(key.replaceAll("...", "…")) ?? exact.get(key.replaceAll("…", "..."));
                if (hit !== undefined) return hit;
            }
            for (const tail of [":", "…"]) {
                if (key.length > tail.length && key.endsWith(tail)) {
                    hit = exact.get(key.slice(0, -tail.length).trimEnd());
                    if (hit !== undefined) return hit + tail;
                }
            }
            hit = exact.get(key + ":");
            return hit === undefined ? undefined : hit.replace(/[:：]+$/, "").trimEnd();
        }

        function lookUp(key) {
            if (!key || key.length > 4000 || !/\p{L}/u.test(key)) return undefined;
            const hit = exactHit(key);
            if (hit !== undefined) return hit;
            for (const tp of templates) {
                if (tp.hint && !key.includes(tp.hint)) continue;
                const m = tp.re.exec(key);
                if (!m) continue;
                const values = {};
                tp.names.forEach((name, i) => {
                    values[name] = name.startsWith("t_") ? exactHit(norm(m[i + 1])) ?? m[i + 1] : m[i + 1];
                });
                return tp.text.replace(PH, (all, name) => (name in values ? values[name] : all));
            }
            return undefined;
        }

        /* text in the chosen language, or text itself. */
        /* On macOS a shortcut is respelled ("⌘Z", "Option") by the time it's
           drawn, while the strings are keyed by the Windows spelling
           ("Ctrl+Z", "Alt"): it's looked up in that spelling, and the
           translation respelled back - in its own language's key names too
           (see keys() below). Failing that, as it's drawn: "Option" can be
           an ordinary word. */
        function t(text) {
            if (!exact || typeof text !== "string" || !text) return text;
            const key = norm(unkeys(text)), drawn = norm(text);
            let hit = lookUp(key);
            if (hit === undefined && drawn !== key) hit = lookUp(drawn);
            if (hit === undefined) return text;
            return /^\s*/.exec(text)[0] + keys(hit) + /\s*$/.exec(text)[0];
        }

        function doText(node) {
            const rec = seen.get(node);
            // Unchanged since it was translated: translate its English again
            // (the language may have changed); otherwise the page wrote new text.
            const src = rec && node.data === rec.shown ? rec.src : node.data;
            const out = t(src);
            if (out === src && !rec) return;
            seen.set(node, {src, shown: out});
            if (node.data !== out) node.data = out;
        }

        function doAttrs(elm) {
            let recs = seen.get(elm);
            for (const name of ATTRS) {
                const value = elm.getAttribute(name);
                if (value === null) continue;
                const rec = recs && recs[name];
                const src = rec && value === rec.shown ? rec.src : value;
                const out = t(src);
                if (out === src && !rec) continue;
                if (!recs) seen.set(elm, recs = {});
                recs[name] = {src, shown: out};
                if (value !== out) elm.setAttribute(name, out);
            }
        }

        const textSkipped = elm => !elm || elm.isContentEditable || !!elm.closest(`${TREE_SKIP}, ${TEXT_SKIP}`);

        function walk(root) {
            if (root.nodeType === Node.TEXT_NODE) {
                if (!textSkipped(root.parentElement)) doText(root);
                return;
            }
            if (root.nodeType !== Node.ELEMENT_NODE || root.closest(TREE_SKIP)) return;
            if (root.isContentEditable || root.closest(TEXT_SKIP)) { doAttrs(root); return; }
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
                acceptNode: n => {
                    if (n.nodeType !== Node.ELEMENT_NODE) return NodeFilter.FILTER_ACCEPT;
                    if (n.matches(TREE_SKIP)) return NodeFilter.FILTER_REJECT;
                    doAttrs(n);
                    return n.isContentEditable || n.matches(TEXT_SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP;
                },
            });
            doAttrs(root);
            for (let n = walker.nextNode(); n; n = walker.nextNode()) doText(n);
        }

        function onMutations(records) {
            for (const r of records) {
                if (r.type === "characterData") {
                    if (!textSkipped(r.target.parentElement)) doText(r.target);
                } else if (r.type === "attributes") {
                    if (!r.target.closest(TREE_SKIP)) doAttrs(r.target);
                } else {
                    for (const n of r.addedNodes) walk(n);
                }
            }
        }

        function apply(data) {
            load(data && data.strings);
            document.documentElement.lang = (data && data.lang) || "en";
            const root = document.body || document.documentElement;
            if (observer) observer.takeRecords();
            walk(root);   // for English, puts back what was translated
            if (exact && !observer) {
                observer = new MutationObserver(onMutations);
                observer.observe(root, {subtree: true, childList: true, characterData: true,
                                        attributes: true, attributeFilter: ATTRS});
            } else if (!exact && observer) {
                observer.disconnect();
                observer = null;
            }
        }

        return {apply, t};
    })();

    on("i18n", data => i18n.apply(data));

    /* el("button.btn.accent#send", {title: "Send", onclick: fn}, ["Send"]) */
    function el(spec, props, children) {
        const match = /^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i.exec(spec) || [];
        const node = document.createElement(match[1] || "div");
        for (const part of (match[2] || "").match(/[.#][\w-]+/g) || []) {
            if (part[0] === ".") node.classList.add(part.slice(1));
            else node.id = part.slice(1);
        }
        for (const [key, value] of Object.entries(props || {})) {
            if (value === undefined || value === null || value === false) continue;
            if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
            else if (key === "text") node.textContent = value;
            else if (key === "html") node.innerHTML = value;   // only ever Python-rendered HTML
            else if (key === "dataset") Object.assign(node.dataset, value);
            else if (key in node && typeof value !== "string") node[key] = value;
            else node.setAttribute(key, value === true ? "" : value);
        }
        for (const child of [].concat(children || [])) {
            if (child === null || child === undefined || child === false) continue;
            node.append(child instanceof Node ? child : document.createTextNode(String(child)));
        }
        return node;
    }

    /* Inline SVG icons (stroke style, 24px grid), so pages need no files. */
    const ICONS = {
        send: '<path d="M4 12l16-8-6 16-3-6.5z"/><path d="M11 13.5l9-9.5"/>',
        copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
        check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
        plus: '<path d="M12 5v14M5 12h14"/>',
        minus: '<path d="M5 12h14"/>',
        left: '<path d="M15 5l-7 7 7 7"/>',
        right: '<path d="M9 5l7 7-7 7"/>',
        trash: '<path d="M4 7h16M9 7V4.5h6V7"/><path d="M6.5 7l.9 12a2 2 0 0 0 2 1.8h5.2a2 2 0 0 0 2-1.8l.9-12"/><path d="M10 11v6M14 11v6"/>',
        download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
        warning: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
        tool: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>',
        refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/>',
        undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
        play: '<path d="M7 4.5v15l12.5-7.5z"/>',
        pause: '<path d="M8 5v14M16 5v14"/>',
        stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
        spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 17l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
        folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
        globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
        arrow: '<path d="M4 12h15M13 6l6 6-6 6"/>',
        image: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>',
        music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
        film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
        file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
        search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4.2-4.2"/>',
        chat: '<path d="M4 5h16v11H9l-5 4z"/>',
        dropper: '<path d="M14.5 5.5l4 4"/><path d="M17.5 3.5a2.1 2.1 0 0 1 3 3l-2 2-3-3z"/><path d="M15.5 8.5L7 17l-3 1 1-3 8.5-8.5"/>',
        more: '<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>',
        swap: '<path d="M7 7h12l-3-3M17 17H5l3 3"/>',
        pin: '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
        gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
        split: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M12 2.5v19"/>',
        programs: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="3.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="3.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
        cascade: '<rect x="3" y="3" width="12" height="10" rx="2"/><path d="M7 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-2"/>',
        tile: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
        popout: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
        palette: '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.7-.8 1.7-1.7 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.7 1.7-1.7H16a5 5 0 0 0 5-5c0-4-4-7.2-9-7.2z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
        eye: '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.8"/>',
        sliders: '<path d="M6 4v4M6 12v8M12 4v10M12 18v2M18 4v2M18 10v10"/><circle cx="6" cy="10" r="2"/><circle cx="12" cy="16" r="2"/><circle cx="18" cy="8" r="2"/>',
        info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
        // The Web tab's (pages/web/web/browser.js).
        close: '<path d="M6 6l12 12M18 6L6 18"/>',
        volume: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
        mute: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
        moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
        private: '<path d="M3 11.5h18M6.5 11.5l1.6-6h7.8l1.6 6"/><circle cx="7.5" cy="16.5" r="2.5"/><circle cx="16.5" cy="16.5" r="2.5"/><path d="M10 16.5c1.3-.8 2.7-.8 4 0"/>',
        // The link bar's icons (core/link_bar.py ICONS), with globe, folder,
        // file, film, play, music, image, palette, chat and tool above.
        mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>',
        camera: '<path d="M4 8h3.5L9 5.5h6L16.5 8H20a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>',
        cloud: '<path d="M7 18.5a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 9.5a4.5 4.5 0 0 1-.5 9z"/>',
        mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3.5 6.5l8.5 6.5 8.5-6.5"/>',
        calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
        book: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3"/>',
        code: '<path d="M8.5 7L3.5 12l5 5M15.5 7l5 5-5 5M13.5 4.5l-3 15"/>',
        cart: '<path d="M3 4h2.5l2.2 10.5h10.6L20.5 7H6.6"/><circle cx="9" cy="19" r="1.5"/><circle cx="17" cy="19" r="1.5"/>',
        users: '<circle cx="9" cy="8.5" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M15.5 5.2a3.5 3.5 0 0 1 0 6.6M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>',
        home: '<path d="M4 11l8-7 8 7"/><path d="M6 9.5V20h12V9.5"/><path d="M10 20v-5h4v5"/>',
        star: '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.8z"/>',
        heart: '<path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.3 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z"/>',
        bookmark: '<path d="M6.5 3.5h11v17L12 16l-5.5 4.5z"/>',
        map: '<path d="M3 6.5l6-2.5 6 2.5 6-2.5v13.5l-6 2.5-6-2.5-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
        bug: '<path d="M8 2l1.9 1.9M16 2l-1.9 1.9"/><path d="M9 7.1V6a3 3 0 0 1 6 0v1.1"/><path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6z"/><path d="M12 20v-9"/><path d="M6.5 9C4.6 8.8 3 7.1 3 5M6 13H2M3 21c0-2.1 1.7-3.9 3.8-4M21 5c0 2.1-1.6 3.8-3.5 4M22 13h-4M17.2 17c2.1.1 3.8 1.9 3.8 4"/>',
    };

    function icon(name) {
        const span = document.createElement("span");
        span.innerHTML = `<svg class="icon-svg" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
        return span.firstChild;
    }

    let toastNode = null;
    let toastTimer = 0;

    /* toast("Stopped", 6000, {label: "Undo", onClick: fn}) - the action
       button is optional; the toast goes when it's pressed or times out. */
    function toast(text, ms, action) {
        if (!toastNode) {
            toastNode = el("div.toast", {role: "status", "aria-live": "polite"});
            document.body.append(toastNode);
        }
        const hide = () => toastNode.classList.remove("show", "has-action");
        toastNode.replaceChildren(el("span", {text}));
        if (action) {
            toastNode.append(el("button.toast-action", {text: action.label, onclick: () => {
                clearTimeout(toastTimer);
                hide();
                action.onClick();
            }}));
        }
        toastNode.classList.toggle("has-action", !!action);
        toastNode.classList.add("show");
        clearTimeout(toastTimer);
        toastTimer = setTimeout(hide, ms || 2200);
    }

    /* A modal over the page. modal({title, body: node|[nodes], buttons:
       [{label, kind: "accent"|"danger"|"", onClick: close => ..., id}],
       onClose}). Escape and a click outside close it - the top one only,
       when one opens over another. Returns {close, node}. */
    const modals = [];
    const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
                      'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    function modal({title, body, buttons, onClose, wide}) {
        const previous = document.activeElement;
        const dialog = el(`div.modal${wide ? ".wide" : ""}`, {role: "dialog", "aria-modal": "true", "aria-label": title});
        const backdrop = el("div.modal-backdrop", {}, dialog);
        let closed = false;
        const close = () => {
            if (closed) return;
            closed = true;
            modals.splice(modals.indexOf(dialog), 1);
            backdrop.remove();
            document.removeEventListener("keydown", onKey, true);
            if (onClose) onClose();
            if (previous && previous.focus) previous.focus();
        };
        const onKey = e => {
            if (modals[modals.length - 1] !== dialog) return;
            if (e.key === "Escape") { e.stopImmediatePropagation(); close(); }
            // Tab stays inside the dialog: the page behind is inert while it's up.
            if (e.key === "Tab") {
                const stops = [...dialog.querySelectorAll(FOCUSABLE)].filter(n => n.offsetParent !== null);
                if (!stops.length) { e.preventDefault(); return; }
                const first = stops[0], last = stops[stops.length - 1];
                const inside = dialog.contains(document.activeElement);
                if (e.shiftKey && (!inside || document.activeElement === first)) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && (!inside || document.activeElement === last)) { e.preventDefault(); first.focus(); }
            }
        };
        dialog.append(
            el("h2.modal-title", {text: title}),
            el("div.modal-body", {}, [].concat(body || [])),
            el("div.modal-buttons", {}, (buttons || []).map(b => el(
                `button.btn${b.kind ? "." + b.kind : ""}`,
                {type: "button", id: b.id, text: b.label, onclick: () => b.onClick ? b.onClick(close) : close()},
            ))),
        );
        backdrop.addEventListener("mousedown", e => { if (e.target === backdrop) close(); });
        document.addEventListener("keydown", onKey, true);
        document.body.append(backdrop);
        modals.push(dialog);
        requestAnimationFrame(() => {
            // A field if there is one; otherwise the main button - but never
            // a destructive one, so Enter can't delete by accident: a Delete
            // dialog starts on Cancel.
            const danger = dialog.querySelector(".modal-buttons .btn.danger");
            const first = dialog.querySelector("input, select, textarea") ||
                          (danger ? dialog.querySelector(".modal-buttons .btn:not(.danger)")
                                  : dialog.querySelector(".modal-buttons .btn:last-child"));
            if (first) first.focus();
        });
        return {close, node: dialog};
    }

    /* await confirm({title, text, ok: "Delete", danger: true}) -> true/false */
    function confirm({title, text, ok, cancel, danger}) {
        return new Promise(resolve => {
            let answer = false;
            modal({
                title,
                body: el("p.modal-text", {text}),
                buttons: [
                    {label: cancel || "Cancel"},
                    {label: ok || "OK", kind: danger ? "danger" : "accent",
                     onClick: close => { answer = true; close(); }},
                ],
                onClose: () => resolve(answer),
            });
        });
    }

    let menuNode = null;

    /* A popup menu at (x, y) - a right-click or a "more" button:
       menu({x, y, items: [{label, onClick, danger, disabled, swatch: "#hex",
       raw: true for a label people named - a folder, a palette - so it isn't
       translated, checked: true/false for a choice of which one is current -
       a tick, and room for one on the rest} | {sep: true} | {heading: "text"}]}).
       Escape, a click elsewhere or a pick closes it; Up/Down/Enter work.
       Returns close. */
    function menu({x, y, items}) {
        closeMenu();
        const node = el("div.menu-pop", {role: "menu"});
        const ticks = items.some(item => item && "checked" in item);
        for (const item of items) {
            if (item.sep) { node.append(el("hr")); continue; }
            if (item.heading) { node.append(el("div.menu-heading", {text: item.heading})); continue; }
            node.append(el(`button${item.danger ? ".danger" : ""}`, {
                type: "button", role: ticks && "checked" in item ? "menuitemradio" : "menuitem",
                "aria-checked": ticks && "checked" in item ? String(!!item.checked) : undefined,
                disabled: !!item.disabled,
                onclick: () => { closeMenu(); if (item.onClick) item.onClick(); },
            }, [ticks ? el("span.menu-tick", {}, item.checked ? [icon("check")] : []) : null,
                item.swatch ? el("i.menu-swatch", {style: `background:${item.swatch}`}) : null,
                el("span", {text: keys(item.label), translate: item.raw ? "no" : undefined})]));
        }
        document.body.append(node);
        menuNode = node;
        const w = node.offsetWidth, h = node.offsetHeight;
        node.style.left = `${Math.max(6, Math.min(x, innerWidth - w - 6))}px`;
        node.style.top = `${Math.max(6, y + h > innerHeight - 6 ? y - h : y)}px`;
        const first = node.querySelector("button:not(:disabled)");
        if (first) first.focus();
        return closeMenu;
    }

    function closeMenu() {
        if (menuNode) { menuNode.remove(); menuNode = null; }
    }

    document.addEventListener("mousedown", e => { if (menuNode && !menuNode.contains(e.target)) closeMenu(); }, true);
    document.addEventListener("keydown", e => {
        if (!menuNode) return;
        if (e.key === "Escape") { e.stopPropagation(); closeMenu(); return; }
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            const buttons = [...menuNode.querySelectorAll("button:not(:disabled)")];
            const at = buttons.indexOf(document.activeElement);
            const next = buttons[(at + (e.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length];
            if (next) next.focus();
        }
    }, true);
    addEventListener("blur", closeMenu);
    addEventListener("resize", closeMenu);

    /* A colour picker popover: saturation/value square, hue bar, hex box,
       before/after chips and (when the page has one) a screen dropper.
       pickColor({hex, title, at: Element|DOMRect|{x, y}, onPick(hex), okLabel,
       dropper(set) - set(hex) feeds the sampled colour back}). */
    const picker = (() => {
        let node = null, state = null;

        const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
        function hexToRgb(hex) {
            const n = parseInt(hex.slice(1), 16);
            return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
        }
        function rgbToHex(r, g, b) {
            return "#" + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, "0")).join("").toUpperCase();
        }
        function rgbToHsv(r, g, b) {
            r /= 255; g /= 255; b /= 255;
            const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
            let h = 0;
            if (d) {
                if (max === r) h = ((g - b) / d) % 6;
                else if (max === g) h = (b - r) / d + 2;
                else h = (r - g) / d + 4;
                h *= 60;
                if (h < 0) h += 360;
            }
            return [h, max ? d / max : 0, max];
        }
        function hsvToHex(h, s, v) {
            const f = n => {
                const k = (n + h / 60) % 6;
                return v - v * s * Math.max(0, Math.min(k, 4 - k, 1));
            };
            return rgbToHex(f(5) * 255, f(3) * 255, f(1) * 255);
        }
        function parseHex(text) {
            const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(text).trim());
            if (!m) return null;
            const d = m[1].length === 3 ? [...m[1]].map(c => c + c).join("") : m[1];
            return "#" + d.toUpperCase();
        }

        function build() {
            node = el("div.picker#picker", {role: "dialog"});
            node.innerHTML = `
                <div class="pk-title"></div>
                <div class="pk-sv" tabindex="0" role="slider" aria-label="Saturation and brightness"><i class="pk-white"></i><i class="pk-black"></i><b class="pk-knob"></b></div>
                <div class="pk-hue" tabindex="0" role="slider" aria-label="Hue" aria-valuemin="0" aria-valuemax="360"><b class="pk-knob"></b></div>
                <div class="pk-row">
                    <span class="pk-chips"><i class="pk-old" title="Before"></i><i class="pk-new" title="After"></i></span>
                    <input class="field pk-hex" spellcheck="false" maxlength="7" aria-label="Hex colour">
                    <button type="button" class="btn icon pk-drop"></button>
                </div>
                <div class="pk-buttons"><button type="button" class="btn pk-cancel"></button><button type="button" class="btn accent pk-ok"></button></div>`;
            node.querySelector(".pk-drop").append(icon("dropper"));
            // The spectrum and the white/black ramps are the picker's content,
            // not the theme's colours.
            node.querySelector(".pk-hue").style.background =
                "linear-gradient(to right,#f00,#ff0 17%,#0f0 33%,#0ff 50%,#00f 67%,#f0f 83%,#f00)";
            node.querySelector(".pk-white").style.background = "linear-gradient(to right,#fff,rgba(255,255,255,0))";
            node.querySelector(".pk-black").style.background = "linear-gradient(to top,#000,rgba(0,0,0,0))";
            drag(node.querySelector(".pk-sv"), (fx, fy) => { state.s = fx; state.v = 1 - fy; update(); });
            drag(node.querySelector(".pk-hue"), fx => { state.h = fx * 360; update(); });
            // The keyboard does what the pointer does: arrows nudge (Shift for
            // bigger steps), Home/End jump to the ends.
            keys(node.querySelector(".pk-sv"), (key, big) => {
                const step = big ? 0.1 : 0.01;
                if (key === "ArrowLeft") state.s = clamp(state.s - step, 0, 1);
                else if (key === "ArrowRight") state.s = clamp(state.s + step, 0, 1);
                else if (key === "ArrowUp") state.v = clamp(state.v + step, 0, 1);
                else if (key === "ArrowDown") state.v = clamp(state.v - step, 0, 1);
                else if (key === "Home") state.s = 0;
                else if (key === "End") state.s = 1;
                else return false;
                return true;
            });
            keys(node.querySelector(".pk-hue"), (key, big) => {
                const step = big ? 10 : 1;
                if (key === "ArrowLeft" || key === "ArrowDown") state.h = clamp(state.h - step, 0, 360);
                else if (key === "ArrowRight" || key === "ArrowUp") state.h = clamp(state.h + step, 0, 360);
                else if (key === "Home") state.h = 0;
                else if (key === "End") state.h = 360;
                else return false;
                return true;
            });
            const hexField = node.querySelector(".pk-hex");
            hexField.addEventListener("input", () => {
                const hex = parseHex(hexField.value);
                hexField.classList.toggle("invalid", !hex);
                if (hex) setHex(hex, false);
            });
            hexField.addEventListener("keydown", e => { if (e.key === "Enter") ok(); });
            node.querySelector(".pk-old").onclick = () => setHex(state.old);
            node.querySelector(".pk-drop").onclick = () => {
                if (state && state.dropper) state.dropper(hex => { if (state) setHex(hex); });
            };
            node.querySelector(".pk-cancel").onclick = close;
            node.querySelector(".pk-ok").onclick = ok;
            node.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
            document.addEventListener("mousedown", e => {
                if (state && !node.contains(e.target) && !e.target.closest(".menu-pop")) close();
            }, true);
            document.body.append(node);
        }

        function drag(area, onMove) {
            area.addEventListener("pointerdown", e => {
                area.setPointerCapture(e.pointerId);
                const move = ev => {
                    const r = area.getBoundingClientRect();
                    onMove(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1));
                };
                move(e);
                area.onpointermove = move;
                area.onpointerup = () => { area.onpointermove = null; };
            });
        }

        function keys(area, onKey) {
            area.addEventListener("keydown", e => {
                if (!state || !onKey(e.key, e.shiftKey)) return;
                e.preventDefault();
                update();
            });
        }

        function setHex(hex, updateField = true) {
            const [h, s, v] = rgbToHsv(...hexToRgb(hex));
            // Greys have no hue - keep the one the bar is on.
            state.h = s ? h : state.h;
            state.s = s;
            state.v = v;
            update(updateField, hex);
        }

        function update(updateField = true, exact) {
            const hex = exact || hsvToHex(state.h, state.s, state.v);
            state.hex = hex;
            node.querySelector(".pk-sv").style.backgroundColor = hsvToHex(state.h, 1, 1);
            const sv = node.querySelector(".pk-sv .pk-knob");
            sv.style.left = `${state.s * 100}%`;
            sv.style.top = `${(1 - state.v) * 100}%`;
            sv.style.background = hex;
            const hue = node.querySelector(".pk-hue .pk-knob");
            hue.style.left = `${state.h / 360 * 100}%`;
            hue.style.background = hsvToHex(state.h, 1, 1);
            const svArea = node.querySelector(".pk-sv"), hueArea = node.querySelector(".pk-hue");
            svArea.setAttribute("aria-valuetext",
                `Saturation ${Math.round(state.s * 100)}%, brightness ${Math.round(state.v * 100)}%`);
            hueArea.setAttribute("aria-valuenow", String(Math.round(state.h)));
            hueArea.setAttribute("aria-valuetext", `${Math.round(state.h)} degrees`);
            node.querySelector(".pk-new").style.background = hex;
            if (updateField) {
                const field = node.querySelector(".pk-hex");
                field.value = hex;
                field.classList.remove("invalid");
            }
        }

        function open({hex, title, at, onPick, okLabel, dropper, dropperLabel}) {
            if (!node) build();
            closeMenu();
            state = {h: 0, s: 0, v: 0, old: hex || "#FFFFFF", onPick, dropper};
            node.querySelector(".pk-title").textContent = title || "Choose a colour";
            node.querySelector(".pk-cancel").textContent = "Cancel";
            node.querySelector(".pk-ok").textContent = okLabel || "OK";
            const drop = node.querySelector(".pk-drop");
            drop.hidden = !dropper;
            drop.title = dropperLabel || "Pick a colour from anywhere on screen";
            node.querySelector(".pk-old").style.background = state.old;
            setHex(state.old);
            node.hidden = false;
            const w = node.offsetWidth, h = node.offsetHeight;
            // Beside an element (its box), a DOMRect or a point; centred if none.
            if (at && typeof at.getBoundingClientRect === "function") at = at.getBoundingClientRect();
            if (!at) at = {x: (innerWidth - w) / 2, y: Math.max(8, (innerHeight - h) / 2)};
            const x = at.left !== undefined ? at.left : at.x, y = at.bottom !== undefined ? at.bottom + 6 : at.y;
            node.style.left = `${clamp(x, 8, innerWidth - w - 8)}px`;
            node.style.top = `${y + h > innerHeight - 8 ? Math.max(8, (at.top !== undefined ? at.top : y) - h - 6) : y}px`;
            const field = node.querySelector(".pk-hex");
            field.focus();
            field.select();
        }

        function ok() {
            if (!state) return;
            const pick = state.onPick, hex = state.hex;
            close();
            if (pick) pick(hex);
        }

        function close() {
            if (node) node.hidden = true;
            state = null;
        }

        return {open, close, parseHex};
    })();

    function connect() {
        if (!(window.qt && qt.webChannelTransport)) return;
        new QWebChannel(qt.webChannelTransport, channel => {
            backend = channel.objects.backend;
            backend.event.connect((name, json) => receive(name, JSON.parse(json)));
            for (const [name, payload] of outbox.splice(0)) backend.send(name, payload);
            backend.ready();
        });
    }

    // After the page's own script has registered its handlers.
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", connect);
    else setTimeout(connect, 0);

    // ---------------------------------------------------------- dropdowns --
    /* Every <select class="field"> opens Buddy's own list rather than
       Chromium's native popup, which ignores the theme (a grey Windows
       list, whatever the page looks like). The <select> stays the real
       control: pages still set .value, fill its options and listen for
       "change" as usual - this only draws the list and sets the
       value. Keyboard: Enter/Space/Alt+Down open it, arrows and typing move,
       Enter picks, Escape closes; arrows on a closed one still step through
       the options natively.

       A view too small for the list (the shell's header) can set
       Buddy.dropdownRoom to the room it can make below itself; it hears
       "buddy:dropdown" ({open, bottom}) to grow while the list is open. */
    let dropdown = null;   // {select, node, items, index, above}

    function dropdownOptions(select) {
        const out = [];
        for (const child of select.children) {
            if (child.tagName === "OPTGROUP") {
                out.push({heading: child.label});
                for (const o of child.children) out.push({option: o});
            } else if (child.tagName === "OPTION") {
                out.push({option: child});
            }
        }
        return out;
    }

    function openDropdown(select) {
        closeDropdown(true);
        const rows = dropdownOptions(select);
        if (!rows.some(r => r.option)) return;
        const node = el("div.dropdown-pop", {role: "listbox", "aria-label": select.getAttribute("aria-label") || select.title || ""});
        const items = [];
        for (const row of rows) {
            if (row.heading !== undefined) { node.append(el("div.dropdown-heading", {text: row.heading})); continue; }
            const o = row.option;
            const selected = o.value === select.value;
            const item = el("div.dropdown-opt", {role: "option", "aria-selected": String(selected), "aria-disabled": o.disabled ? "true" : undefined},
                            [el("span.dropdown-check", {}, selected ? icon("check") : null), el("span.dropdown-label", {text: o.textContent,
                                translate: o.closest('[translate="no"]') ? "no" : null})]);
            const index = items.length;
            item.addEventListener("mousemove", () => highlight(index, false));
            item.addEventListener("mouseup", e => { if (e.button === 0) pickDropdown(index); });
            items.push({node: item, option: o});
            node.append(item);
        }
        document.body.append(node);
        const r = select.getBoundingClientRect();
        node.style.minWidth = `${r.width}px`;
        // scrollHeight leaves out the border, which max-height (border-box) counts.
        const needed = node.scrollHeight + node.offsetHeight - node.clientHeight;
        const extra = typeof Buddy.dropdownRoom === "number" ? Buddy.dropdownRoom : 0;
        const below = Math.max(innerHeight, r.bottom + extra) - r.bottom - 8;
        const above = r.top - 8;
        const up = needed > below && above > below;
        const room = Math.max(80, up ? above : below);
        node.style.maxHeight = `${Math.min(needed, room, 420)}px`;
        const width = node.offsetWidth;
        node.style.left = `${Math.max(6, Math.min(r.left, innerWidth - width - 6))}px`;
        if (up) { node.style.bottom = `${innerHeight - r.top + 4}px`; node.classList.add("up"); }
        else node.style.top = `${r.bottom + 4}px`;
        select.classList.add("open");
        select.setAttribute("aria-expanded", "true");
        const start = Math.max(0, items.findIndex(i => i.option.value === select.value));
        dropdown = {select, node, items, index: -1, typed: "", typedAt: 0};
        highlight(start, true);
        if (!up) document.dispatchEvent(new CustomEvent("buddy:dropdown",
                                                        {detail: {open: true, bottom: r.bottom + 4 + node.offsetHeight}}));
        requestAnimationFrame(() => node.classList.add("show"));
    }

    function highlight(index, scroll) {
        if (!dropdown) return;
        const items = dropdown.items;
        if (index < 0 || index >= items.length || items[index].option.disabled) return;
        if (dropdown.index >= 0) items[dropdown.index].node.classList.remove("active");
        dropdown.index = index;
        items[index].node.classList.add("active");
        if (scroll) items[index].node.scrollIntoView({block: "nearest"});
    }

    function stepDropdown(delta) {
        const items = dropdown.items;
        let i = dropdown.index;
        for (let n = 0; n < items.length; n++) {
            i = Math.max(0, Math.min(items.length - 1, i + delta));
            if (!items[i].option.disabled) break;
        }
        highlight(i, true);
    }

    function pickDropdown(index) {
        if (!dropdown) return;
        const {select, items} = dropdown;
        const item = items[index];
        if (!item || item.option.disabled) return;
        const changed = select.value !== item.option.value;
        closeDropdown();
        select.focus();
        if (changed) {
            select.value = item.option.value;
            select.dispatchEvent(new Event("input", {bubbles: true}));
            select.dispatchEvent(new Event("change", {bubbles: true}));
        }
    }

    function closeDropdown(instant) {
        if (!dropdown) return;
        const {select, node} = dropdown;
        dropdown = null;
        select.classList.remove("open");
        select.setAttribute("aria-expanded", "false");
        document.dispatchEvent(new CustomEvent("buddy:dropdown", {detail: {open: false}}));
        if (instant) { node.remove(); return; }
        node.classList.remove("show");
        node.classList.add("closing");
        node.addEventListener("transitionend", () => node.remove(), {once: true});
        setTimeout(() => node.remove(), 250);
    }

    const isDropdown = t => t && t.matches && t.matches("select.field") && !t.multiple && !(t.size > 1);

    document.addEventListener("mousedown", e => {
        if (dropdown && dropdown.node.contains(e.target)) { e.preventDefault(); return; }
        const select = e.target.closest ? e.target.closest("select.field") : null;
        if (select && isDropdown(select)) {
            e.preventDefault();          // no native popup
            if (select.disabled) return;
            const same = dropdown && dropdown.select === select;
            select.focus();
            if (same) closeDropdown(); else openDropdown(select);
            return;
        }
        if (dropdown) closeDropdown();
    }, true);

    document.addEventListener("keydown", e => {
        if (dropdown) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); stepDropdown(e.key === "ArrowDown" ? 1 : -1); }
            else if (e.key === "Home" || e.key === "End") { e.preventDefault(); highlight(e.key === "Home" ? 0 : dropdown.items.length - 1, true); }
            else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickDropdown(dropdown.index); }
            else if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closeDropdown(); }
            else if (e.key === "Tab") closeDropdown();
            else if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
                // Type-ahead: the first option starting with what's typed.
                const now = Date.now();
                dropdown.typed = (now - dropdown.typedAt < 700 ? dropdown.typed : "") + e.key.toLowerCase();
                dropdown.typedAt = now;
                const i = dropdown.items.findIndex(it => it.option.textContent.trim().toLowerCase().startsWith(dropdown.typed));
                if (i >= 0) highlight(i, true);
            }
            e.stopPropagation();
            return;
        }
        if (isDropdown(e.target) && !e.target.disabled &&
                (e.key === "Enter" || e.key === " " || e.key === "F4" || (e.altKey && e.key === "ArrowDown"))) {
            e.preventDefault();
            openDropdown(e.target);
        }
    }, true);

    // Anything that moves the page from under it closes it.
    document.addEventListener("scroll", e => {
        if (dropdown && !dropdown.node.contains(e.target)) closeDropdown(true);
    }, true);
    // Only a change of width: a view that grows downward to show the list
    // (the shell's header) mustn't close it by doing so.
    let dropdownWidth = innerWidth;
    addEventListener("resize", () => {
        if (innerWidth !== dropdownWidth) closeDropdown(true);
        dropdownWidth = innerWidth;
    });
    addEventListener("blur", () => closeDropdown(true));

    // ------------------------------------------------------ key names --
    /* Pages spell shortcuts the Windows way ("Ctrl+Z", <kbd>Ctrl</kbd>).
       On macOS the same shortcuts are Command ones, so there the page's
       own text and tooltips are respelled once it has loaded, and
       Buddy.keys(text) respells anything a script writes later:
       "Ctrl+Z" -> "⌘Z", "Ctrl+Shift+Z" -> "⇧⌘Z", a lone "Ctrl" -> "⌘",
       and "Alt" -> "Option" (the key's name on a Mac keyboard).
       Everywhere else Buddy.keys returns the text unchanged.

       Buddy.IS_MAC is for the rest - wording that isn't a key name ("the
       Trash", not "the Recycle Bin") or a behaviour: a script picks with
       it, a style sheet with html.mac, and a page's own HTML gives the Mac
       wording beside the Windows one (data-mac-text, data-mac-title,
       data-mac-placeholder - see macWording below). */
    const IS_MAC = /Mac/.test(navigator.platform);

    // Control and Shift as the translations write them - German Strg and
    // Umschalt, Spanish Mayús, French Maj - so a translated shortcut is
    // respelled like an English one ("Strg+Umschalt+Z" -> "⇧⌘Z").
    const CTRL = "(?:Ctrl|Strg)";
    const SHIFT = "(?:Shift|Umschalt|Mayús|Maj)";
    const CTRL_SHIFT = new RegExp(`${CTRL}\\+${SHIFT}\\+`, "g");
    const CTRL_ALT = new RegExp(`${CTRL}\\+Alt\\+`, "g");
    const CTRL_PLUS = new RegExp(`${CTRL}\\+`, "g");
    const CTRL_ALONE = new RegExp(`\\b${CTRL}\\b`, "g");
    const SPELLED = /Ctrl|Strg|\bAlt\b/;

    function keys(text) {
        if (!IS_MAC || typeof text !== "string" || !SPELLED.test(text)) return text;
        return text.replace(CTRL_SHIFT, "⇧⌘").replace(CTRL_ALT, "⌥⌘")
                   .replace(CTRL_PLUS, "⌘").replace(CTRL_ALONE, "⌘").replace(/\bAlt\b/g, "Option");
    }

    /* keys() undone, back to the Windows spelling the translations are
       keyed by: "⌘Z" -> "Ctrl+Z", a lone "⌘" -> "Ctrl", "Option" -> "Alt".
       Buddy's shortcuts are a capital letter or a digit after the modifiers. */
    function unkeys(text) {
        if (!IS_MAC || typeof text !== "string" || !/⌘|\bOption\b/.test(text)) return text;
        return text.replace(/⇧⌘/g, "Ctrl+Shift+").replace(/⌥⌘/g, "Ctrl+Alt+")
                   .replace(/⌘(?=[A-Z0-9])/g, "Ctrl+").replace(/⌘/g, "Ctrl").replace(/\bOption\b/g, "Alt");
    }

    function respellKeys(root) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            if (SPELLED.test(node.nodeValue)) node.nodeValue = keys(node.nodeValue);
        }
        for (const node of root.querySelectorAll("[title*='Ctrl'], [placeholder*='Ctrl'], [title*='Alt'], [placeholder*='Alt']")) {
            for (const attr of ["title", "placeholder"]) {
                if (node.hasAttribute(attr)) node.setAttribute(attr, keys(node.getAttribute(attr)));
            }
        }
    }

    /* A page's HTML is written once, in the Windows wording. Where the Mac
       wording differs by more than a key's name, the element carries it:
       <button title="Choose a folder on this PC" data-mac-title="Choose a folder">. */
    function macWording(root) {
        for (const node of root.querySelectorAll("[data-mac-text], [data-mac-title], [data-mac-placeholder]")) {
            if (node.dataset.macText !== undefined) node.textContent = node.dataset.macText;
            if (node.dataset.macTitle !== undefined) node.setAttribute("title", node.dataset.macTitle);
            if (node.dataset.macPlaceholder !== undefined) node.setAttribute("placeholder", node.dataset.macPlaceholder);
        }
    }

    if (IS_MAC) {
        document.documentElement.classList.add("mac");
        const reword = () => { macWording(document.body); respellKeys(document.body); };
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", reword);
        } else {
            reword();
        }
    }

    // ------------------------------------------------- overscroll bounce --
    /* macOS: a scrolled list stretches past its top or bottom and springs
       back, like every Mac app. Chromium only does that for the page's own
       scroll, and every Buddy page scrolls inner boxes instead (body is
       overflow: hidden), so it's done here, redrawn every frame:

       - dragging past the end, the box's children follow the finger along
         Apple's rubber-band curve (harder to pull the further it goes),
         moved as each event arrives - not smoothed, and not left for the
         next frame, either of which trails the finger noticeably;
       - let go, and a damped spring takes the stretch back to rest,
         starting at the speed the content was moving - so a flick carries
         on outwards before it comes back (the overshoot), and a slow
         release just settles;
       - scrolling back while stretched undoes the stretch first, with the
         list held at its end meanwhile.

       The page never learns when fingers touch or leave the trackpad, so a
       release is a pause in the events - or deltas shrinking event after
       event, which is a flick's momentum rather than a finger (when that
       reaches the page at all). The rest of such a run is ignored, so it
       can't hold the stretch out; the ignoring ends at the next gesture's
       start marker (a zero-delta event), after any pause, or as soon as a
       delta grows again. Off for mouse wheels (no Mac app bounces those),
       pinch zoom, and with Reduce Motion on. */
    const BOUNCE = {
        quietMs: 50,       // no scroll events this long: the fingers stopped
        spring: 0.02,      // spring rate per ms: back at rest in about a third of a second
        falling: 4,        // this many shrinking deltas in a row: momentum
        maxSpeed: 4,       // px/ms the spring may start at
        sampleMs: 32,      // the last movement before release its speed is taken from
    };
    const bounce = {
        box: null, edge: 0,        // edge: 1 at the top (pulled down), -1 at the bottom
        mode: "idle",              // "idle" | "drag" | "spring"
        pull: 0,                   // distance scrolled past the end
        target: 0, shown: 0,       // px the content should be / is moved
        samples: [],               // [time, target] while dragging
        springAt: 0, springFrom: 0, springSpeed: 0, timer: 0, frame: 0,
        room: false, roomFrom: 0,  // stretching into room past the bottom (paintBounce)
        barHidden: false, barWas: "",   // a box that fits, its scrollbar kept away (startDrag)
        lastSize: 0, falling: 0,   // the run of shrinking deltas so far
        ignoreSize: 0, ignoreAt: 0, ignoreSign: 0,   // a momentum run being ignored
    };

    /* The box a wheel event at `target` stretches, or null when the
       browser scrolls one instead (the innermost box that can still move
       that way). A box at its end stretches. When nothing overflows, the
       outermost scroll area does - the page itself - as a Mac scroll view
       bounces even with little in it: not an inner strip (overflow-x: auto
       makes a tab strip a vertical scroller too), nor a pop-up that fits. */
    function stretchBox(target, dy) {
        let atEnd = null, outermost = null;
        for (let node = target; node && node !== document.body && node.nodeType === 1; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (!/(auto|scroll)/.test(style.overflowY)) continue;
            if (node.scrollHeight > node.clientHeight + 1) {
                if (canScroll(node, dy)) return null;
                atEnd = atEnd || node;
            } else if (style.position !== "fixed" && style.position !== "absolute") {
                outermost = node;
            }
        }
        return atEnd || outermost;
    }

    function canScroll(node, dy) {
        return dy < 0 ? node.scrollTop > 0 : node.scrollTop + node.clientHeight < node.scrollHeight - 1;
    }

    // Apple's rubber band: pulled x past the end shows as band(x); unband
    // is its inverse, to pick a stretch up where it's currently showing.
    const RUBBER = 0.55;
    const band = (x, size) => Math.sign(x) * (1 - 1 / (Math.abs(x) * RUBBER / size + 1)) * size;
    const unband = (y, size) => {
        const f = Math.min(Math.abs(y) / size, 0.95);
        return Math.sign(y) * (1 / (1 - f) - 1) * size / RUBBER;
    };

    /* Moves the content y px (down past the top, up past the bottom).
       Past the top of any box, and in a box whose content fits, its
       children are moved. Past the bottom of a box that scrolls they can't
       be: moving them up shrinks how far the box scrolls by as much, and
       the browser pulls the scroll back to match - nothing would move. So
       there the box gets that much room after its content (buddy.css's
       .buddy-overscroll-end) and is scrolled into it. */
    function paintBounce(box, y) {
        if (bounce.room) {
            box.style.setProperty("--buddy-overscroll", `${Math.max(0, -y).toFixed(2)}px`);
            box.scrollTop = bounce.roomFrom - Math.min(0, y);
            return;
        }
        const transform = Math.abs(y) < 0.05 ? "" : `translateY(${y.toFixed(2)}px)`;
        for (const child of box.children) child.style.transform = transform;
    }

    function endBounce() {
        const box = bounce.box;
        if (box) {
            paintBounce(box, 0);
            if (bounce.room) {
                box.classList.remove("buddy-overscroll-end");
                box.style.removeProperty("--buddy-overscroll");
                box.style.removeProperty("--buddy-overscroll-top");
                box.scrollTop = bounce.roomFrom;
            }
            if (bounce.barHidden) box.style.overflowY = bounce.barWas;
        }
        clearTimeout(bounce.timer);
        Object.assign(bounce, {box: null, edge: 0, mode: "idle", pull: 0, target: 0, shown: 0, samples: [],
                               room: false, roomFrom: 0, barHidden: false, barWas: ""});
    }

    function bounceFrame(now) {
        bounce.frame = 0;
        const box = bounce.box;
        if (!box || !box.isConnected) return endBounce();
        if (bounce.mode === "drag") {
            // Drawn by onWheel as each event comes; here the box is only
            // held at its end while stretched - the browser mustn't scroll
            // it at the same time (scrolling back undoes the stretch first).
            if (!bounce.shown) return endBounce();
            const end = bounce.edge > 0 ? 0
                : bounce.room ? bounce.roomFrom - bounce.shown : box.scrollHeight - box.clientHeight;
            if (Math.abs(box.scrollTop - end) > 0.5) box.scrollTop = end;
            bounce.frame = requestAnimationFrame(bounceFrame);
            return;
        } else {
            // A critically damped spring from where the release left it.
            const t = now - bounce.springAt, w = BOUNCE.spring;
            const x0 = bounce.springFrom, v0 = bounce.springSpeed;
            const x = (x0 + (v0 + w * x0) * t) * Math.exp(-w * t);
            // Never past rest: a spring back from the top stops at the top.
            if (t > 30 && (Math.abs(x) < 0.4 || Math.sign(x) !== bounce.edge)) return endBounce();
            bounce.shown = x;
        }
        paintBounce(box, bounce.shown);
        bounce.frame = requestAnimationFrame(bounceFrame);
    }

    function runBounce() {
        if (!bounce.frame) bounce.frame = requestAnimationFrame(bounceFrame);
    }

    function releaseBounce() {
        clearTimeout(bounce.timer);
        if (bounce.mode !== "drag") return;
        const now = performance.now();
        // The finger's speed as it last moved - not over the pause that
        // told us it had stopped, which would always come out as zero.
        const lastAt = bounce.samples.length ? bounce.samples[bounce.samples.length - 1][0] : now;
        const recent = bounce.samples.filter(([t]) => lastAt - t <= BOUNCE.sampleMs);
        let speed = 0;
        if (recent.length >= 2) {
            // Over at least a frame: two events a moment apart would make
            // the speed spike.
            const [t0, y0] = recent[0], [t1, y1] = recent[recent.length - 1];
            speed = (y1 - y0) / Math.max(t1 - t0, 16);
        }
        // Still heading outwards when released: the spring carries it on.
        if (Math.sign(speed) !== bounce.edge) speed = 0;
        Object.assign(bounce, {
            mode: "spring", springAt: now, springFrom: bounce.shown,
            springSpeed: Math.max(-BOUNCE.maxSpeed, Math.min(BOUNCE.maxSpeed, speed)), samples: [],
        });
        runBounce();
    }

    function isMouseWheel(e) {
        // A mouse's notches come in whole multiples of 120, and big.
        return e.deltaMode !== 0 || (e.wheelDeltaY && e.wheelDeltaY % 120 === 0 && Math.abs(e.deltaY) >= 40);
    }

    function startDrag(box, edge) {
        // Caught mid-spring at this same end: carry on from where it is
        // now, not from rest. Anywhere else starts afresh.
        if (bounce.box && (bounce.box !== box || bounce.edge !== edge)) endBounce();
        if (!bounce.box && edge < 0 && box.scrollHeight > box.clientHeight + 1) {
            // Past the bottom of a box that scrolls: stretched by scrolling
            // into room made after its content (see paintBounce and
            // buddy.css). Where its marker sits on its own is measured by
            // moving it far down, then it's placed at the content's end.
            const height = box.scrollHeight, probe = 100000;
            Object.assign(bounce, {room: true, roomFrom: box.scrollTop});
            box.classList.add("buddy-overscroll-end");
            box.style.setProperty("--buddy-overscroll-top", `${probe}px`);
            const natural = box.scrollHeight - probe;
            box.style.setProperty("--buddy-overscroll-top", `${height - natural}px`);
            box.scrollTop = bounce.roomFrom;
        } else if (!bounce.box && box.scrollHeight <= box.clientHeight + 1) {
            // A box whose content fits has no scrollbar - and mustn't grow
            // one because the stretch pushes its content past the end for a
            // moment: a scrollbar takes room, and everything would shift
            // over to make it.
            Object.assign(bounce, {barHidden: true, barWas: box.style.overflowY});
            box.style.overflowY = "hidden";
        }
        const size = box.clientHeight;
        Object.assign(bounce, {
            box, edge, mode: "drag", pull: bounce.shown ? unband(bounce.shown, size) : 0,
            target: bounce.shown, samples: [], lastSize: 0, falling: 0,
        });
    }

    function onWheel(e) {
        // Zero-delta events are Chromium marking a gesture's start or end:
        // whatever momentum was being ignored is over.
        if (!e.deltaY) { bounce.ignoreSize = 0; return; }
        if (e.defaultPrevented || e.ctrlKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
        if (isMouseWheel(e)) return;
        const now = performance.now(), dy = e.deltaY, size = Math.abs(dy), outwards = -Math.sign(dy);
        if (bounce.ignoreSize) {
            if (Math.sign(dy) === bounce.ignoreSign && size <= bounce.ignoreSize * 1.1
                    && now - bounce.ignoreAt < BOUNCE.quietMs * 2) {
                bounce.ignoreSize = size;
                bounce.ignoreAt = now;
                return;
            }
            bounce.ignoreSize = 0;
        }
        let box = bounce.box;

        if (box && bounce.mode === "drag" && outwards !== bounce.edge) {
            // Scrolling back while stretched: the stretch goes first.
            bounce.pull -= dy;
            if (Math.sign(bounce.pull) !== bounce.edge) bounce.pull = 0;
        } else {
            if (!(box && bounce.mode === "drag")) {
                const found = stretchBox(e.target, dy);
                if (!found) return;
                startDrag(found, outwards);
                box = found;
            }
            bounce.falling = size < bounce.lastSize ? bounce.falling + 1 : 0;
            bounce.lastSize = size;
            if (bounce.falling >= BOUNCE.falling) {
                // Momentum: let go now, and ignore the rest of the run.
                Object.assign(bounce, {ignoreSize: size, ignoreAt: now, ignoreSign: Math.sign(dy)});
                releaseBounce();
                return;
            }
            bounce.pull -= dy;
        }
        bounce.target = bounce.shown = band(bounce.pull, box.clientHeight);
        paintBounce(box, bounce.shown);
        bounce.samples.push([now, bounce.target]);
        if (bounce.samples.length > 12) bounce.samples.shift();
        runBounce();
        clearTimeout(bounce.timer);
        bounce.timer = setTimeout(releaseBounce, BOUNCE.quietMs);
    }

    if (IS_MAC && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
        window.addEventListener("wheel", onWheel, {passive: true});
    }

    // ------------------------------------------------------ letting go --
    /* Buddy lets a tool it hasn't shown for a while go, to give its memory
       back (core/tool_memory.py): the page is discarded, and reloaded - and
       redrawn from what Python keeps - the next time it's shown. First it
       asks keepAlive(): true keeps the page. Text typed into a field and
       still there always keeps it; a page holding anything else only it has
       (a game in progress, an unsaved edit) adds a check:
       Buddy.holdWhile(() => inProgress). */
    const holds = [];
    const typed = new WeakSet();
    const TEXT_FIELD = "textarea, input:not([type]), input[type=text], input[type=search], input[type=url], " +
                       "input[type=email], input[type=number], input[type=tel], input[type=password]";
    document.addEventListener("input", e => {
        const t = e.target;
        if (e.isTrusted && t instanceof Element && (t.matches(TEXT_FIELD) || t.isContentEditable)) typed.add(t);
    }, true);

    function holdWhile(check) {
        holds.push(check);
    }

    /* A list of languages to tick, in a modal: {title, hint, entries: [{code, name}],
       selected: [codes], min, single}. Resolves to the codes, or null when
       cancelled. The Subtitles tool's "Mixed languages" and Dailies' transcript box. */
    function pickLanguages({title, hint, entries, selected, min, single}) {
        return new Promise(resolve => {
            const chosen = new Set(selected || []);
            const search = el("input.field", {type: "search", placeholder: "Search languages…", autocomplete: "off", spellcheck: "false"});
            const list = el("div.lang-list", {role: single ? "listbox" : "group"});
            const count = el("span.muted.small");
            const error = el("div.field-error");
            const ordered = [...entries.filter(e => chosen.has(e.code)), ...entries.filter(e => !chosen.has(e.code))];
            const rows = ordered.map(e => {
                const input = el("input", {type: single ? "radio" : "checkbox", name: "lang", checked: chosen.has(e.code)});
                input.addEventListener("change", () => {
                    if (single) { chosen.clear(); chosen.add(e.code); }
                    else if (input.checked) chosen.add(e.code);
                    else chosen.delete(e.code);
                    update();
                });
                const row = el("label.lang-row", {}, [input, el("span", {text: e.name}), el("span.muted.small.code", {text: e.code})]);
                row.dataset.find = `${e.name} ${e.code}`.toLowerCase();
                return row;
            });
            list.append(...rows);
            const update = () => {
                count.textContent = single ? "" : chosen.size ? `${chosen.size} selected` : "None selected";
                error.textContent = "";
            };
            search.addEventListener("input", () => {
                const q = search.value.trim().toLowerCase();
                for (const r of rows) r.hidden = !!q && !r.dataset.find.includes(q);
            });
            let answer = null;
            const buttons = [{label: "Cancel"}, {label: "OK", kind: "accent", onClick: close => {
                if (chosen.size < (min || 0)) { error.textContent = `Tick at least ${min}.`; return; }
                if (single && !chosen.size) { error.textContent = "Choose one."; return; }
                answer = [...chosen];
                close();
            }}];
            if (!single) buttons.unshift({label: "Clear all", onClick: () => {
                chosen.clear();
                for (const r of rows) r.querySelector("input").checked = false;
                update();
            }});
            modal({title, body: [el("p.modal-text.muted", {text: hint}), search, list, el("div.row", {}, [count]), error],
                         buttons, onClose: () => resolve(answer)});
            update();
            requestAnimationFrame(() => {
                search.focus();
                const first = list.querySelector("input:checked");
                if (first && single) first.closest("label").scrollIntoView({block: "center"});
            });
        });
    }

    function keepAlive() {
        for (const check of holds) {
            try {
                if (check()) return true;
            } catch (err) {
                return true;                  // can't tell: keep it
            }
        }
        for (const field of document.querySelectorAll(`${TEXT_FIELD}, [contenteditable]`)) {
            if (!typed.has(field)) continue;
            const text = field.isContentEditable ? field.textContent : field.value;
            if (String(text || "").trim()) return true;
        }
        return false;
    }

    return {on, send, receive, el, icon, toast, modal, confirm, menu, closeMenu, closeDropdown, t: i18n.t, pickLanguages,
            pickColor: picker.open, closePicker: picker.close, parseHex: picker.parseHex,
            keys, IS_MAC, _bounce: bounce, holdWhile, keepAlive};
})();
