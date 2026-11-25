"use strict";

// Panels' view (../page.py says what each event carries, ../CONTRACT.md what a panel
// is). Python owns the panel: where every box sits (solve.py), how it is drawn
// (styles.py: a list of prims), and what happens on every frame (clock.py). This
// draws what it is sent and reports what was done:
//
//   + Add       the shelf, over the stage: a whole page (a layout) or a piece (a component)
//   Layers      the boxes of the page in view as a tree: picked, renamed, removed, dragged
//   Edit        a click picks a box; the window is dragged to move it, its edge to size it
//   Try it      the mouse is the cursor: the boxes answer it, a link changes the page
//   Record      what the mouse does on the stage is kept - sent once, when stopped
//   the dock    the clock played back, frame for frame, under it a lane per thing that moves
//   the right   the window's settings when nothing is picked, else the picked layer's
//
// How a frame looks is clock.pose()'s rule, the one the comp's keys are made from.
// Python can't be asked on every frame, so pose() below is that rule again, line for
// line - a test holds the two together. Trying it, the same pose() is fed a clock made
// up of the mouse's last few frames.

(() => {
    const $ = id => document.getElementById(id);
    const el = Buddy.el;
    const TYPING_MS = 250;
    const HANDLE = 8;             // screen px either side of the window's right edge that size it
    const MOST_LANES = 8;         // other layers' lanes in the dock
    const WINDOW_ROOM = 28;       // screen px round the window when the stage shows it alone
    const DOCK_KEY = "buddy.panels.timeline";

    let state = null;
    let mode = "edit";            // "edit" | "try"
    let view = "edit";            // "edit": the page in view, still | "frame": the clock's frame
    let fit = "window";           // the stage: "window" (as large as it holds) | "frame" (as on the timeline)
    let forced = false;           // fit was switched to "frame" by playing, trying or recording
    let look = "rest";            // how Edit shows the boxes: "rest" | "hover" | "press"
    let picked = "";
    let frame = 0;
    let playing = false;
    let startedAt = 0;
    let seen = false;
    let addMode = "layouts";      // what + Add shows: "layouts" | "insert"
    let shelfFor = "";            // what the tiles were last drawn for
    let renaming = "";            // the page whose chip is being renamed
    let hold = null;              // a drag on the stage or a lane
    let rec = null;               // recording: {t0, samples, clicks, downAt}
    let index = {boxes: new Map(), placed: {}};
    let dockOpen = true;
    // Which containers are open in Layers, page by page, for the session: {page: Map(id -> open)}.
    // One never opened or closed is open only at the top level (the page's own box's children).
    const folds = new Map();

    try {
        dockOpen = sessionStorage.getItem(DOCK_KEY) !== "hidden";
    } catch (_err) {
        // no storage: just for now
    }

    // ------------------------------------------------------- the rule --
    // clock.py's pose(), mirrored: the same arithmetic, rounded where it rounds.

    const CURVES = {ease_out: [1 / 3, 1.0, 2 / 3, 1.0], ease_in_out: [0.65, 0.0, 0.35, 1.0],
                    overshoot: [0.3, 1.2, 0.5, 1.25]};      // data_graphics/easing.py
    const ARRIVES = CURVES.ease_out, MOVES = CURVES.ease_in_out, POPS = CURVES.overshoot;
    const RISE = 8.0, SLIDE = 0.2, BUILD_MOST = 1.5;
    const POP = 0.85;             // a box that pops in starts this share of its size
    const REST = {alpha: 1.0, dx: 0.0, dy: 0.0, scale: 1.0, sx: 1.0, sy: 1.0, glow: 0.0, own: 1.0};
    const POSE_KEYS = Object.keys(REST);

    // Python's round(x, n): to the nearest, an exact tie to the even one.
    function pyRound(x, n) {
        if (!Number.isFinite(x)) return x;
        const k = 10 ** n, y = Math.abs(x) * k, low = Math.floor(y);
        if (Math.abs(y - low - 0.5) > 1e-7) return Math.sign(x) * Math.round(y) / k || 0;
        const s = Math.abs(x).toFixed(Math.min(100, n + 40)), dot = s.indexOf(".");
        const rest = s.slice(dot + 1 + n);
        let v = Number(Math.abs(x).toFixed(n));
        if (/^50*$/.test(rest) && Number(s[dot + n]) % 2 === 0) v = Number(s.slice(0, dot + 1 + n));
        return x < 0 ? -v : v;
    }

    function clamp01(p) {
        return p <= 0 ? 0.0 : p >= 1 ? 1.0 : p;
    }

    function ease(curve, p) {
        p = clamp01(p);
        if (p === 0 || p === 1) return p;
        const [x1, y1, x2, y2] = curve;
        let low = 0.0, high = 1.0;
        for (let i = 0; i < 40; i++) {
            const s = (low + high) / 2;
            const x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s ** 2 * x2 + s ** 3;
            if (x < p) low = s;
            else high = s;
        }
        const s = (low + high) / 2;
        return pyRound(3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s ** 2 * y2 + s ** 3, 9);
    }

    function eased(share, curve) {
        share = clamp01(share);
        if (share === 0 || share === 1) return share;
        return pyRound(clamp01(ease(curve || MOVES, share)), 6);
    }

    // A popping box's size, share of the way through its arrival: POP to 1, past it a little (clock._popped).
    function popped(share) {
        share = clamp01(share);
        if (share === 0 || share === 1) return POP + (1.0 - POP) * share;
        return pyRound(POP + (1.0 - POP) * ease(POPS, share), 6);
    }

    const factsMemo = new WeakMap();

    // What pose() needs of a panel, once per state: each box's page, its own states and
    // its own way of arriving, the pairs a "match" glides.
    function facts(st) {
        let known = factsMemo.get(st);
        if (known) return known;
        const home = {}, states = {}, arrive = {};
        for (const [pid, page] of Object.entries(st.solved)) page.boxes.forEach(b => { home[b.id] = pid; });
        const walk = box => {
            states[box.id] = box.states;
            if (box.arrive && box.arrive.kind) arrive[box.id] = box.arrive.kind;
            box.children.forEach(walk);
        };
        st.spec.pages.forEach(page => walk(page.root));
        known = {home, states, arrive, pairs: {}};
        factsMemo.set(st, known);
        return known;
    }

    function matched(st, from, to) {
        const known = facts(st), key = from + ">" + to;
        if (!known.pairs[key]) {
            const waiting = new Map(), pairs = {};
            const name = b => JSON.stringify([b.role, b.name, b.text]);
            st.solved[from].boxes.forEach(b => {
                if (!waiting.has(name(b))) waiting.set(name(b), []);
                waiting.get(name(b)).push(b);
            });
            st.solved[to].boxes.forEach(b => {
                const same = waiting.get(name(b));
                if (same && same.length) pairs[b.id] = same.shift();
            });
            known.pairs[key] = pairs;
        }
        return known.pairs[key];
    }

    function span(ck, pageId) {
        let most = 0;
        for (const [start, n] of Object.values(ck.build[pageId] || {})) most = Math.max(most, start + n);
        return most;
    }

    // A move and a size on the page as one map, x -> a * x + t on each side: [ax, ay, tx, ty] (clock._about, _then, _undo).
    const SAME = [1.0, 1.0, 0.0, 0.0];
    const about = (mx, my, kx, ky, dx, dy) => [kx, ky, mx - kx * mx + dx, my - ky * my + dy];
    const then = (f, g) => [f[0] * g[0], f[1] * g[1], f[0] * g[2] + f[2], f[1] * g[3] + f[3]];
    const undo = f => [1.0 / f[0], 1.0 / f[1], -f[2] / f[0], -f[3] / f[1]];

    // Each box's LOCAL pose: what it adds to what it's in (clock._page_pose; carried() puts them together).
    // A box's own "arrive" changes how it builds on: "fade" doesn't rise, "pop" grows from POP.
    function pagePose(st, ck, known, pageId, f, since, fade, glides, gone) {
        const building = {};
        if (since < span(ck, pageId)) {
            for (const [id, [start, n]] of Object.entries(ck.build[pageId])) {
                if (since < start + n && !(id in glides)) {
                    const there = eased((since - start) / n, ARRIVES), kind = known.arrive[id] || "rise";
                    building[id] = [there, kind === "rise" ? pyRound((1.0 - there) * RISE, 4) : 0.0,
                                    kind === "pop" ? popped((since - start) / n) : 1.0];
                }
            }
        }
        const answering = {};
        for (const [id, rows] of Object.entries(ck.amounts)) {
            const hover = rows.hover[f], press = rows.press[f];
            if (known.home[id] !== pageId || !(hover || press)) continue;
            const answers = known.states[id] || st.spec.answers;
            answering[id] = [pyRound(1.0 + (answers.hover.grow * hover + answers.press.grow * press) / 100.0, 4),
                             answers.hover.lift * hover + answers.press.lift * press,
                             pyRound(Math.min(1.0, Math.max(0.0, (answers.hover.glow * hover + answers.press.glow * press) / 100.0)), 4)];
        }
        const hidden = new Set(gone), out = {}, seen = {}, base = {};
        for (const b of st.solved[pageId].boxes) {
            const id = b.id, parent = b.parent;
            const mx = b.x + b.w / 2, my = b.y + b.h / 2;
            const [there, rise, size] = building[id] || [1.0, 0.0, 1.0];
            const arrival = rise || size !== 1.0 ? about(mx, my, size, size, 0.0, rise) : SAME;
            const [upAlpha, upArrival] = seen[parent] || [1.0, SAME];
            const upBase = base[parent] || SAME;
            let alpha = upAlpha > 0 ? Math.min(1.0, there / upAlpha) : 0.0;
            if (fade < 1.0 && !(id in glides) && (!(parent in seen) || parent in glides)) alpha *= fade;
            seen[id] = [there, arrival];
            const glide = glides[id];
            let local = glide ? then(undo(upBase), about(mx, my, glide.sx, glide.sy, glide.dx, glide.dy))
                : upArrival === SAME ? arrival
                : then(undo(upArrival), arrival);           // its own arrival, less what it's in already makes
            base[id] = then(upBase, local);
            const [scale, lift, glow] = answering[id] || [1.0, 0.0, 0.0];
            if (id in answering) local = then(about(mx, my, scale, scale, 0.0, -lift), local);
            const look = {alpha: pyRound(alpha, 4), dx: pyRound(local[0] * mx + local[2] - mx, 4),
                          dy: pyRound(local[1] * my + local[3] - my, 4), scale,
                          sx: pyRound(local[0] / scale, 4), sy: pyRound(local[1] / scale, 4), glow,
                          own: hidden.has(id) ? 0.0 : 1.0};
            if (POSE_KEYS.some(k => look[k] !== REST[k])) out[id] = look;
        }
        return out;
    }

    // How a box is drawn, everything it is in put together (clock.carried): alpha, dx, dy,
    // scale, sx, sy about its own middle; glow and own its own.
    function carried(page, boxes, id) {
        const placed = new Map(page.boxes.map(b => [b.id, b]));
        const chain = [];
        for (let at = id; placed.has(at); at = placed.get(at).parent) chain.push(at);
        let move = SAME, alpha = 1.0, scale = 1.0;
        for (const at of chain.reverse()) {
            const b = placed.get(at), look = boxes[at] || REST;
            move = then(move, about(b.x + b.w / 2, b.y + b.h / 2, look.scale * look.sx, look.scale * look.sy, look.dx, look.dy));
            alpha *= look.alpha;
            scale *= look.scale;
        }
        const b = placed.get(id), look = boxes[id] || REST, mx = b.x + b.w / 2, my = b.y + b.h / 2;
        return {alpha, dx: move[0] * mx + move[2] - mx, dy: move[1] * my + move[3] - my, scale,
                sx: move[0] / scale, sy: move[1] / scale, glow: look.glow, own: look.own == null ? 1.0 : look.own};
    }

    // How frame f looks: {pages: [{page, alpha, dx, boxes: {id: {alpha, dx, dy, scale,
    // sx, sy, glow}}}], cursor, down, draw, typed} - clock.pose(spec, clock, frame). `ck`
    // is the state's clock unless another (trying it) is given.
    function pose(st, frameNo, ck) {
        ck = ck || st.clock;
        const f = Math.min(Math.max(0, Math.trunc(frameNo)), ck.total - 1);
        const known = facts(st), changes = ck.pages;
        let i = 0;
        changes.forEach((c, k) => { if (c.at <= f) i = k; });
        const now = changes[i], since = f - now.at;
        const along = now.frames <= 0 ? 1.0 : eased(since / now.frames);
        const wide = st.solved[now.page].w;
        const pages = [];
        if (now.from && along < 1.0) {
            const how = now.with, before = f - changes[i - 1].at;
            if (how === "match") {
                const pairs = matched(st, now.from, now.page), glides = {};
                st.solved[now.page].boxes.forEach(b => {
                    const old = pairs[b.id];
                    if (!old) return;
                    const w = old.w + (b.w - old.w) * along, h = old.h + (b.h - old.h) * along;
                    glides[b.id] = {
                        alpha: 1.0,
                        dx: pyRound((old.x + old.w / 2) * (1 - along) + (b.x + b.w / 2) * along - b.x - b.w / 2, 4),
                        dy: pyRound((old.y + old.h / 2) * (1 - along) + (b.y + b.h / 2) * along - b.y - b.h / 2, 4),
                        sx: b.w > 0 ? pyRound(w / b.w, 4) : 1.0, sy: b.h > 0 ? pyRound(h / b.h, 4) : 1.0};
                });
                pages.push({page: now.from, alpha: 1.0, dx: 0.0,
                            boxes: pagePose(st, ck, known, now.from, f, before, 1.0 - along, {}, Object.values(pairs).map(o => o.id))});
                pages.push({page: now.page, alpha: 1.0, dx: 0.0,
                            boxes: pagePose(st, ck, known, now.page, f, since, along, glides, [])});
            } else {
                const slide = how === "slide" ? SLIDE * wide : 0.0;
                pages.push({page: now.from, alpha: pyRound(1.0 - along, 4), dx: pyRound(-slide * along, 4),
                            boxes: pagePose(st, ck, known, now.from, f, before, 1.0, {}, [])});
                pages.push({page: now.page, alpha: pyRound(along, 4), dx: pyRound(slide * (1.0 - along), 4),
                            boxes: pagePose(st, ck, known, now.page, f, since, 1.0, {}, [])});
            }
        } else {
            pages.push({page: now.page, alpha: 1.0, dx: 0.0, boxes: pagePose(st, ck, known, now.page, f, since, 1.0, {}, [])});
        }
        const showing = new Set(pages.map(p => p.page)), draw = {};
        for (const [id, row] of Object.entries(ck.draw)) if (showing.has(known.home[id])) draw[id] = row[f];
        // The letters each box that types shows on the frame, and the carets that show.
        const typed = {}, caret = {};
        for (const [id, row] of Object.entries(ck.typed || {})) if (showing.has(known.home[id])) typed[id] = row[f];
        for (const [id, row] of Object.entries(ck.caret || {})) {
            if (row[f] !== null && row[f] !== undefined && showing.has(known.home[id])) caret[id] = row[f];
        }
        return {pages, cursor: [...ck.cursor[f]], down: ck.down[f], draw, typed, caret};
    }

    // ------------------------------------------------------- lookups --

    function reindex() {
        const boxes = new Map();
        const walk = (box, page, parent) => {
            boxes.set(box.id, {box, page, parent});
            box.children.forEach(c => walk(c, page, box.id));
        };
        state.spec.pages.forEach(p => walk(p.root, p.id, ""));
        const placed = {};
        for (const [pid, page] of Object.entries(state.solved)) placed[pid] = new Map(page.boxes.map(b => [b.id, b]));
        index = {boxes, placed};
    }

    function boxOf(id) {
        const found = index.boxes.get(id);
        return found ? found.box : null;
    }

    function pageOf(id) {
        return state ? state.spec.pages.find(p => p.id === id) || null : null;
    }

    function chainOf(id) {
        const out = [];
        for (let at = index.boxes.get(id); at; at = index.boxes.get(at.parent)) out.unshift(at.box);
        return out;
    }

    function within(id, of) {
        for (let at = index.boxes.get(id); at; at = index.boxes.get(at.parent)) if (at.box.id === of) return true;
        return false;
    }

    function hasText(box) {
        return ["text", "button", "field", "badge"].includes(box.role);
    }

    function shownName(box) {
        return box.name || box.role;
    }

    // What a box is called in a list (the lanes, what changes the page): its own name,
    // or - for one still called by its role (model.ROLE_NAMES) - its words, or the name
    // of what it's in.
    const ROLE_NAMES = new Set(["Text", "Button", "Field", "Image", "Icon", "Divider", "Badge", "Progress", "Chart",
                                "Card", "Row", "Column"]);

    function label(box) {
        if (!ROLE_NAMES.has(box.name)) return shownName(box);
        if (hasText(box) && box.text) return box.text;
        const named = chainOf(box.id).reverse().slice(1).find(b => !ROLE_NAMES.has(b.name));
        return named ? `${named.name} · ${box.name}` : box.name;
    }

    // A layer as Layers and the right column call it: what it is (Buddy's word) and, the
    // person's, its own name - or its words, or its picture's file.
    const ROLE_WORDS = {text: "Text", button: "Button", field: "Field", image: "Image", icon: "Icon", divider: "Divider",
                        badge: "Badge", progress: "Progress", chart: "Chart"};

    function roleWord(box) {
        if (box.role === "text") return box.variant === "h1" || box.variant === "h2" ? "Heading" : "Text";
        if (box.role === "section") return box.variant === "card" ? "Card" : box.dir === "row" ? "Row" : "Column";
        return ROLE_WORDS[box.role] || box.role;
    }

    function ownWords(box) {
        if (box.role === "image" && box.image && box.image.path) return String(box.image.path).split(/[\\/]/).pop();
        if (box.name && !ROLE_NAMES.has(box.name)) return box.name;
        if (hasText(box) && box.text) return box.text;
        return "";
    }

    // The words of a container's first box with words in it ("" for none).
    function firstWords(box) {
        for (const kid of box.children) {
            if (hasText(kid) && kid.text) return kid.text;
            const deeper = firstWords(kid);
            if (deeper) return deeper;
        }
        return "";
    }

    // What a container is called: its component's name ("Sidebar", "Stat tiles", "Card"),
    // else Row or Column - [the name, whether it's Buddy's word].
    function sectionName(box) {
        if (box.name && box.name !== "Row" && box.name !== "Column") return [box.name, ROLE_NAMES.has(box.name)];
        return [roleWord(box), true];
    }

    // A layer's name as spans: what it is (Buddy's word) and, the person's, its own words.
    // A container is called by its component's name; its first words are added only when a
    // box beside it has the same name, to tell them apart.
    function layerName(box, cls) {
        const role = cls || ".pn-layer-role";
        if (box.role === "section") {
            const [name, ours] = sectionName(box), found = index.boxes.get(box.id), parent = found && boxOf(found.parent);
            const twins = parent ? parent.children.filter(c => c.role === "section" && sectionName(c)[0] === name).length : 0;
            const words = twins > 1 ? firstWords(box) : "";
            const parts = [ours ? el("span" + role, {text: name}) : el("span", {translate: "no", text: name})];
            if (words) parts.push(el("span", {translate: "no", text: ` · ${words}`}));
            return parts;
        }
        const words = ownWords(box), parts = [el("span" + role, {text: roleWord(box)})];
        if (words) parts.push(el("span", {translate: "no", text: ` · ${words}`}));
        return parts;
    }

    const WITH = {fade: "Fade", slide: "Slide", match: "Match", cut: "At once"};

    // The ways a box can move (model.animations, sent as state.animations) - worked out
    // here from the box when the state has none for it.
    function animsOf(id) {
        const given = state.animations && state.animations[id];
        if (Array.isArray(given)) return given;
        const box = boxOf(id);
        if (!box) return [];
        const out = [{id: "arrive", on: !(box.arrive && box.arrive.kind === "none")}];
        if (box.role === "text" || box.role === "field") out.push({id: "type", on: !!box.type});
        out.push({id: "answer", on: !!box.reacts});
        if (box.role === "chart" || box.role === "progress") out.push({id: "draw", on: true});
        return out;
    }

    // Whether the person gave a box something of its own beyond the defaults: it types, has
    // its own way of arriving, its own states, a link, or a rule watches it. (Answering the
    // cursor with the panel's answers is every button's, and isn't marked.)
    function animated(id) {
        const box = boxOf(id);
        if (!box) return false;
        return !!box.type || ownArrival(box) || !!box.states || !!box.go || state.spec.rules.some(r => r.box === id);
    }

    function ownArrival(box) {
        return !!(box && box.arrive && box.arrive.kind && box.arrive.kind !== "none");
    }

    // ------------------------------------------------------ placement --
    // The window's middle is at spec.x, spec.y (% of the frame); a panel px is
    // zoom * frame width / 1920 frame px. On the stage it is drawn either so (In the
    // frame) or as large as the stage holds it (Window).

    function place(st) {
        const [fw, fh] = st.frame, spec = st.spec, solved = st.solved[st.showing] || Object.values(st.solved)[0];
        const s = spec.zoom * fw / 1920;
        return {fw, fh, s, w: solved.w, h: solved.h, ox: spec.x / 100 * fw - solved.w * s / 2,
                oy: spec.y / 100 * fh - solved.h * s / 2};
    }

    // Where the panel is on a canvas w x h screen px: a panel px is `s` screen px, its
    // top left corner at (ox, oy). A width being dragged keeps the map it started with.
    function mapFor(w, h) {
        if (hold && hold.kind === "width" && hold.map) return hold.map;
        const p = place(state);
        if (fit === "frame") {
            const c = w / p.fw;
            return {s: c * p.s, ox: c * p.ox, oy: c * p.oy};
        }
        const s = Math.max(0.05, Math.min((w - 2 * WINDOW_ROOM) / p.w, (h - 2 * WINDOW_ROOM) / p.h));
        return {s, ox: (w - p.w * s) / 2, oy: (h - p.h * s) / 2};
    }

    // A pointer event as a place in panel px and on the frame (%), and a screen px in panel px.
    function at(e) {
        const canvas = $("stage"), box = canvas.getBoundingClientRect(), p = place(state);
        const m = mapFor(box.width, box.height);
        const x = (e.clientX - box.left - m.ox) / m.s, y = (e.clientY - box.top - m.oy) / m.s;
        return {x, y, fx: (p.ox + x * p.s) / p.fw * 100, fy: (p.oy + y * p.s) / p.fh * 100, px: 1 / m.s};
    }

    // The deepest box of a page under a place in panel px (the last hit, back to front).
    function hit(x, y, pageId) {
        if (!state) return "";
        const page = state.solved[pageId || state.showing];
        let found = "";
        if (page) page.boxes.forEach(b => { if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) found = b.id; });
        return found;
    }

    // The topmost reacting box under the hot spot - the one the clock answers.
    function hotBox(x, y, pageId) {
        const page = state.solved[pageId];
        if (!page) return null;
        for (let i = page.boxes.length - 1; i >= 0; i--) {
            const b = page.boxes[i];
            if (b.reacts && b.x <= x && x < b.x + b.w && b.y <= y && y < b.y + b.h) return b;
        }
        return null;
    }

    // The topmost box a click lands on: one that answers, or one with a link.
    function clickBox(x, y, pageId) {
        const page = state.solved[pageId];
        if (!page) return null;
        for (let i = page.boxes.length - 1; i >= 0; i--) {
            const b = page.boxes[i], box = boxOf(b.id);
            if ((b.reacts || (box && box.go)) && b.x <= x && x < b.x + b.w && b.y <= y && y < b.y + b.h) return b;
        }
        return null;
    }

    function placedAnywhere(id) {
        for (const map of Object.values(index.placed)) if (map.has(id)) return map.get(id);
        return null;
    }

    // ------------------------------------------------------- drawing --

    function fontOf(face, size) {
        const f = face || {family: "Arial", weight: 400, italic: false};
        return `${f.italic ? "italic " : ""}${f.weight} ${size}px "${String(f.family).replace(/"/g, "")}", sans-serif`;
    }

    function rad(deg) {
        return (deg - 90) * Math.PI / 180;
    }

    // The pictures of image boxes, each read once: {img, ok, bad} by file url.
    const pictures = new Map();

    function pictureOf(url) {
        let got = pictures.get(url);
        if (!got) {
            const img = new Image();
            got = {img, ok: false, bad: false};
            img.onload = () => {
                got.ok = true;
                fillImage();
                if (state && !playing && !live()) paint();
            };
            img.onerror = () => { got.bad = true; };
            img.src = url;
            pictures.set(url, got);
        }
        return got;
    }

    function polyPath(ctx, points, share) {
        // Drawn on by length: share of the whole line, from its first point.
        let total = 0;
        for (let i = 1; i < points.length; i++) total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
        let left = total * share;
        ctx.moveTo(points[0][0], points[0][1]);
        for (let i = 1; i < points.length; i++) {
            const [ax, ay] = points[i - 1], [bx, by] = points[i], d = Math.hypot(bx - ax, by - ay);
            if (left >= d) {
                ctx.lineTo(bx, by);
                left -= d;
            } else {
                if (d > 0 && left > 0) ctx.lineTo(ax + (bx - ax) * left / d, ay + (by - ay) * left / d);
                break;
            }
        }
    }

    // One prim, in panel px. share: how far a chart or a progress bar is drawn on (1: all
    // of it). shown: the letters its box shows as it types (undefined: all of them).
    function drawPrim(ctx, p, share, scaleNow, shown) {
        const fill = p.fill || "", stroke = p.stroke || "";
        switch (p.kind) {
        case "rect": {
            let {x, y, w, h} = p;
            if (p.draw === "up") { const hh = h * share; y += h - hh; h = hh; }
            else if (p.draw === "across") w *= share;
            if (w <= 0 || h <= 0) return;
            const r = Math.max(0, Math.min(p.radius || 0, w / 2, h / 2));
            ctx.beginPath();
            ctx.roundRect(x, y, w, h, r);
            if (fill) {
                ctx.save();
                if (p.shadow > 0) {
                    ctx.shadowColor = "#00000059";                         // comp.py's SHADOW_COLOUR, SHADOW_DROP
                    ctx.shadowBlur = p.shadow * scaleNow;             // a shadow's blur isn't scaled by the transform
                    ctx.shadowOffsetY = p.shadow * 0.25 * scaleNow;
                }
                ctx.fillStyle = fill;
                ctx.fill();
                ctx.restore();
            }
            if (stroke && p.stroke_w > 0) {
                ctx.lineWidth = p.stroke_w;
                ctx.strokeStyle = stroke;
                ctx.stroke();
            }
            return;
        }
        case "ellipse":
            ctx.beginPath();
            ctx.ellipse(p.x + p.w / 2, p.y + p.h / 2, Math.max(0, p.w / 2), Math.max(0, p.h / 2), 0, 0, Math.PI * 2);
            if (fill) { ctx.fillStyle = fill; ctx.fill(); }
            if (stroke && p.stroke_w > 0) { ctx.lineWidth = p.stroke_w; ctx.strokeStyle = stroke; ctx.stroke(); }
            return;
        case "line":
            if (!stroke) return;
            ctx.beginPath();
            ctx.moveTo(p.x1, p.y1);
            ctx.lineTo(p.x2, p.y2);
            ctx.lineWidth = p.stroke_w || 1;
            ctx.strokeStyle = stroke;
            ctx.stroke();
            return;
        case "text": {
            // A line that types is laid out from its start (solve.typing), so its first
            // letters are drawn in place: nothing moves as it types. A field's placeholder
            // ("placeholder") shows only until its first letter does - not at all in Edit,
            // where what it types shows whole.
            let text = p.text;
            if (p.placeholder && shown !== 0) return;
            if (Array.isArray(p.type) && shown !== undefined) {
                const n = Math.max(0, Math.min(p.type[1] - p.type[0], shown - p.type[0]));
                if (n <= 0) return;
                text = text.slice(0, n);
            }
            ctx.font = fontOf(p.bold ? state.bold_face : state.face, p.size);
            ctx.textAlign = p.align || "left";
            ctx.textBaseline = "alphabetic";
            ctx.fillStyle = fill || "#ffffff";
            ctx.fillText(text, p.x, p.y);
            return;
        }
        case "image": {
            // The picture fills its box, cropped to it (crop: the part of it, in its own px).
            const r = Math.max(0, Math.min(p.radius || 0, p.w / 2, p.h / 2));
            const url = state && state.images ? state.images[p.box] : "";
            const got = url ? pictureOf(url) : null;
            ctx.save();
            ctx.beginPath();
            ctx.roundRect(p.x, p.y, p.w, p.h, r);
            ctx.clip();
            if (got && got.ok && got.img.naturalWidth) {
                const nw = got.img.naturalWidth, nh = got.img.naturalHeight;
                const [pw, ph] = p.px && p.px[0] > 0 && p.px[1] > 0 ? p.px : [nw, nh];
                let [sx, sy, sw, sh] = Array.isArray(p.crop) && p.crop[2] > 0 ? p.crop : [0, 0, pw, ph];
                if (!Array.isArray(p.crop)) {
                    const k = Math.max(p.w / pw, p.h / ph);
                    sw = p.w / k; sh = p.h / k; sx = (pw - sw) / 2; sy = (ph - sh) / 2;
                }
                ctx.drawImage(got.img, sx * nw / pw, sy * nh / ph, sw * nw / pw, sh * nh / ph, p.x, p.y, p.w, p.h);
            } else {
                // Loading, or a picture that can't be read: the placeholder's look.
                ctx.fillStyle = "#7f879459";
                ctx.fillRect(p.x, p.y, p.w, p.h);
                const s = Math.min(p.w, p.h) * 0.3, cx = p.x + p.w / 2, cy = p.y + p.h / 2;
                ctx.strokeStyle = "#ffffff80";
                ctx.lineWidth = Math.max(1, s * 0.08);
                ctx.lineJoin = "round";
                ctx.beginPath();
                ctx.moveTo(cx - s, cy + s * 0.6);
                ctx.lineTo(cx - s * 0.3, cy - s * 0.2);
                ctx.lineTo(cx + s * 0.15, cy + s * 0.25);
                ctx.lineTo(cx + s * 0.5, cy - s * 0.05);
                ctx.lineTo(cx + s, cy + s * 0.6);
                ctx.stroke();
            }
            ctx.restore();
            return;
        }
        case "poly": {
            const points = p.points || [];
            if (points.length < 2) return;
            ctx.beginPath();
            if (p.draw === true && share < 1) {
                if (share <= 0) return;
                polyPath(ctx, points, share);
            } else {
                points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
                if (p.closed) ctx.closePath();
            }
            if (fill && p.closed) {
                ctx.fillStyle = fill;
                if (p.draw === "across" && share < 1) {
                    // The room under a chart's line wipes on with it, left to right (comp.py's wipe).
                    if (share <= 0) return;
                    const xs = points.map(q => q[0]), ys = points.map(q => q[1]);
                    const left = Math.min(...xs), top = Math.min(...ys);
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(left, top - 1, (Math.max(...xs) - left) * share, Math.max(...ys) - top + 2);
                    ctx.clip();
                    ctx.beginPath();
                    points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
                    ctx.closePath();
                    ctx.fill();
                    ctx.restore();
                } else {
                    ctx.fill();
                }
            }
            if (stroke && p.stroke_w > 0) {
                ctx.lineWidth = p.stroke_w;
                ctx.lineJoin = "round";
                ctx.lineCap = "round";
                ctx.strokeStyle = stroke;
                ctx.stroke();
            }
            return;
        }
        case "wedge": {
            // A pie draws on round from the top: each slice up to the share of the whole turn.
            const limit = p.draw ? 360 * share : 360;
            if (p.from >= limit) return;
            const to = Math.min(p.to, limit);
            ctx.beginPath();
            ctx.arc(p.cx, p.cy, p.r, rad(p.from), rad(to));
            if (p.hole > 0) ctx.arc(p.cx, p.cy, p.hole, rad(to), rad(p.from), true);
            else ctx.lineTo(p.cx, p.cy);
            ctx.closePath();
            if (fill) { ctx.fillStyle = fill; ctx.fill(); }
            return;
        }
        default:
        }
    }

    // A box's pose as a transform about its middle; its glow behind it.
    function boxFrame(ctx, placed, bp) {
        const cx = placed.x + placed.w / 2, cy = placed.y + placed.h / 2;
        ctx.translate(cx + bp.dx, cy + bp.dy);
        ctx.scale(bp.scale * (bp.sx == null ? 1 : bp.sx), bp.scale * (bp.sy == null ? 1 : bp.sy));
        ctx.translate(-cx, -cy);
    }

    // A glow as the comp draws one (comp.py, Interactions' GLOW_*): the box's own shape
    // (its background, else its place), grown, softened, in its colour lifted towards white.
    const GLOW = {reach: 0.012, soft: 0.015, lift: 0.4, zoom: 1.5};

    function lifted(hex, by) {
        const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
        return `rgb(${c.map(v => Math.round(v + (255 - v) * by)).join(", ")})`;
    }

    function glowShape(prims, placed) {
        const p = prims.find(q => q.box === placed.id && (q.kind === "rect" || q.kind === "ellipse")
                             && Math.abs(q.x - placed.x) < 0.6 && Math.abs(q.y - placed.y) < 0.6);
        return p ? {kind: p.kind, x: p.x, y: p.y, w: p.w, h: p.h, radius: p.radius || 0,
                    colour: (p.fill || p.stroke || state.spec.accent).slice(0, 7)}
            : null;         // drawn as nothing of its own (a table's row): nothing to glow, as in comp.py
    }

    function glow(ctx, prims, placed, amount) {
        const look = {...GLOW, ...(state.glow || {})}, [fw] = state.frame, spec = state.spec;
        const s = spec.zoom * fw / 1920, by = spec.zoom / look.zoom;
        const shape = glowShape(prims, placed), grow = look.reach * fw / s * by;     // panel px
        if (!shape) return;
        ctx.save();
        ctx.globalAlpha *= Math.min(1, Math.max(0, amount));
        ctx.filter = `blur(${look.soft * by * fw * 0.45 * ctx.getTransform().a / s}px)`;
        ctx.fillStyle = lifted(shape.colour, look.lift);
        ctx.beginPath();
        if (shape.kind === "ellipse") {
            ctx.ellipse(shape.x + shape.w / 2, shape.y + shape.h / 2, shape.w / 2 + grow, shape.h / 2 + grow, 0, 0, Math.PI * 2);
        } else {
            ctx.roundRect(shape.x - grow, shape.y - grow, shape.w + 2 * grow, shape.h + 2 * grow,
                          shape.radius > 0 ? Math.min(shape.radius + grow, shape.w / 2 + grow, shape.h / 2 + grow) : 0);
        }
        ctx.fill();
        ctx.restore();
    }

    // A page's prims (not the window's own) with its pose: each box's prims moved, grown
    // and faded by its pose and those of everything it is in (carried).
    // A page part-way through a change fades as one picture, as the comp's page canvas
    // does (measured in Resolve, 2026-11-25: faded prim by prim, a Cartoon box's hard
    // shadow showed through its fill and the page went dark mid-change).
    let fading = null;

    function drawPage(ctx, layer, ps) {
        if (layer.alpha >= 0.999) return drawPageOn(ctx, layer, ps, 1);
        if (layer.alpha <= 0) return;
        const target = ctx.canvas;
        if (!fading) fading = document.createElement("canvas");
        if (fading.width !== target.width || fading.height !== target.height) {
            fading.width = target.width;
            fading.height = target.height;
        }
        const on = fading.getContext("2d");
        on.setTransform(1, 0, 0, 1, 0, 0);
        on.clearRect(0, 0, fading.width, fading.height);
        on.setTransform(ctx.getTransform());
        drawPageOn(on, layer, ps, 1);
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = layer.alpha;
        ctx.drawImage(fading, 0, 0);
        ctx.restore();
    }

    function drawPageOn(ctx, layer, ps, pageAlpha) {
        const prims = state.drawn[layer.page] || [], placed = index.placed[layer.page] || new Map();
        const looks = new Map(), page = state.solved[layer.page];
        const drawShares = ps.draw || {}, typed = ps.typed, carets = ps.caret || {};
        const lookOf = id => {
            if (!looks.has(id)) looks.set(id, carried(page, layer.boxes, id));
            return looks.get(id);
        };
        ctx.save();
        ctx.globalAlpha = pageAlpha;
        if (layer.dx) {
            const area = state.solved[layer.page].area;
            if (area) {
                ctx.beginPath();
                ctx.rect(area[0], area[1], area[2], area[3]);
                ctx.clip();
            }
            ctx.translate(layer.dx, 0);
        }
        // Box by box, each with what is in it: a box part-way to showing (arriving, or
        // fading in a match) fades as one picture with everything in it - the comp puts
        // it on its own canvas and blends that (comp.py group) - not prim by prim.
        const mine = new Map(), kids = new Map(), loose = [];
        for (const p of prims) {
            if (!p.box) continue;
            if (!placed.has(p.box)) loose.push(p);
            else if (mine.has(p.box)) mine.get(p.box).push(p);
            else mine.set(p.box, [p]);
        }
        for (const b of page.boxes) {
            if (!kids.has(b.parent)) kids.set(b.parent, []);
            kids.get(b.parent).push(b.id);
        }
        const base = ctx.getTransform();
        const box = (on, id, depth) => {
            const local = layer.boxes[id] || REST, alpha = local.alpha == null ? 1 : local.alpha;
            if (alpha <= 0) return;
            const own = local.own == null ? 1 : local.own, where = placed.get(id), ps_ = mine.get(id) || [];
            const inside = kids.get(id) || [];
            const grouped = alpha < 0.999 && (ps_.length + inside.length > 1);
            const to = grouped ? layerCanvas(depth, on.canvas, base) : on;
            const outer = on.globalAlpha;
            to.globalAlpha = grouped ? 1 : outer * alpha;
            if (ps_.length && own > 0) {
                const bp = lookOf(id), was = to.globalAlpha;
                const shown = typed && id in typed ? typed[id] : undefined;
                to.save();
                to.globalAlpha = was * own;
                boxFrame(to, where, bp);
                if (bp.glow > 0.001) glow(to, prims, where, bp.glow);
                for (const p of ps_) drawPrim(to, p, id in drawShares ? drawShares[id] : 1, to.getTransform().a, shown);
                if (carets[id]) {
                    // The typing caret (clock.caret): a thin upright bar the letters' colour.
                    const [cx, cy, cw, ch] = carets[id], words = ps_.find(p => p.kind === "text" && Array.isArray(p.type));
                    to.fillStyle = (words && words.fill) || "#ffffff";
                    to.fillRect(cx, cy, cw, ch);
                }
                to.restore();
            }
            for (const kid of inside) box(to, kid, depth + 1);
            to.globalAlpha = outer;
            if (grouped) {
                on.save();
                on.setTransform(1, 0, 0, 1, 0, 0);
                on.globalAlpha = outer * alpha;
                on.drawImage(to.canvas, 0, 0);
                on.restore();
            }
        };
        for (const id of kids.get("") || []) box(ctx, id, 0);
        for (const p of loose) drawPrim(ctx, p, p.box in drawShares ? drawShares[p.box] : 1, ctx.getTransform().a);
        ctx.restore();
    }

    // A canvas to put a fading box together on, one a depth: the size of the one it
    // will be laid over, cleared, drawing in the same panel px.
    const layerCanvases = [];

    function layerCanvas(depth, over, base) {
        if (!layerCanvases[depth]) layerCanvases[depth] = document.createElement("canvas");
        const c = layerCanvases[depth];
        if (c.width !== over.width || c.height !== over.height) {
            c.width = over.width;
            c.height = over.height;
        }
        const on = c.getContext("2d");
        on.setTransform(1, 0, 0, 1, 0, 0);
        on.globalAlpha = 1;
        on.clearRect(0, 0, c.width, c.height);
        on.setTransform(base);
        return on;
    }

    function drawCursor(ctx, where, down, s, fh) {
        const spec = state.spec;
        if (spec.cursor === "none" || !where) return;
        const look = (state.cursors || {})[spec.cursor];
        if (!look) return;
        const size = spec.cursor_size / 100 * fh / s;          // panel px
        ctx.save();
        ctx.translate(where[0], where[1]);
        ctx.fillStyle = look.fill;
        ctx.strokeStyle = look.edge;
        ctx.lineJoin = "round";
        if (spec.cursor === "dot") {
            const r = look.width * size / 2;
            ctx.beginPath();
            ctx.arc(0, 0, r * 1.3, 0, Math.PI * 2);
            ctx.fillStyle = look.edge;
            ctx.fill();
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.fillStyle = look.fill;
            ctx.fill();
        } else {
            const k = size * (1 - (state.cursor_dip || 0) * (down ? 1 : 0));
            ctx.beginPath();
            look.points.forEach(([ax, ay], i) => i ? ctx.lineTo(ax * k, ay * k) : ctx.moveTo(ax * k, ay * k));
            ctx.closePath();
            ctx.lineWidth = look.edge_w * k;
            ctx.stroke();
            ctx.fill();
        }
        ctx.restore();
    }

    // The whole window as a pose says, the cursor on top. ctx is already in panel px.
    function render(ctx, ps, cursor, s, fh) {
        const top = ps.pages[ps.pages.length - 1];
        for (const p of state.drawn[top.page] || state.chrome || []) if (!p.box) drawPrim(ctx, p, 1, ctx.getTransform().a);
        ps.pages.forEach(layer => drawPage(ctx, layer, ps));
        if (cursor) drawCursor(ctx, ps.cursor, ps.down, s, fh);
    }

    // How a box looks in a state, by the same arithmetic as pose() (hover and press 0..1).
    function stateLook(box, hover, press) {
        const answers = (box && box.states) || state.spec.answers;
        return {...REST,
                scale: pyRound(1.0 + (answers.hover.grow * hover + answers.press.grow * press) / 100.0, 4),
                dy: pyRound(-answers.hover.lift * hover - answers.press.lift * press, 4),
                glow: pyRound(Math.min(1, Math.max(0, (answers.hover.glow * hover + answers.press.glow * press) / 100.0)), 4)};
    }

    // Edit: the page in view, still - the picked box (or every one that answers) in the look chosen.
    function editPose() {
        const boxes = {};
        if (look !== "rest") {
            const amounts = look === "hover" ? [1, 0] : [1, 1];
            const ids = picked ? [picked] : (state.solved[state.showing] || {boxes: []}).boxes.filter(b => b.reacts).map(b => b.id);
            ids.forEach(id => { boxes[id] = stateLook(boxOf(id), ...amounts); });
        }
        return {pages: [{page: state.showing, alpha: 1, dx: 0, boxes}], cursor: null, down: 0, draw: {}};
    }

    function sizeCanvas(canvas, width, height) {
        const ratio = window.devicePixelRatio || 1;
        if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(height * ratio);
        }
        canvas.style.width = width + "px";
        canvas.style.height = height + "px";
    }

    function paint() {
        const canvas = $("stage");
        if (!state) return;
        const p = place(state), box = canvas.parentElement;
        const roomW = Math.max(160, box.clientWidth || 640), roomH = Math.max(120, box.clientHeight || 360);
        let width = roomW, height = roomH;
        if (fit === "frame") {
            width = Math.floor(Math.min(roomW, roomH * p.fw / p.fh));
            height = Math.round(width * p.fh / p.fw);
        }
        sizeCanvas(canvas, width, height);
        canvas.style.left = Math.round((roomW - width) / 2) + "px";
        canvas.style.top = Math.round((roomH - height) / 2) + "px";
        const ctx = canvas.getContext("2d");
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.fillStyle = fit === "frame" ? "#000000" : "#0c0d0f";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        const k = canvas.width / width, m = mapFor(width, height);
        ctx.setTransform(k * m.s, 0, 0, k * m.s, k * m.ox, k * m.oy);       // panel px from here
        let ps, cursor = true;
        if (live()) ps = livePose();
        else if (view === "frame" || playing) ps = pose(state, frame);
        else { ps = editPose(); cursor = false; }
        render(ctx, ps, cursor, p.s, p.fh);
        if (!live() && !playing && view === "edit" && mode === "edit") drawEditing(ctx, p);
        drawTime();
    }

    function drawEditing(ctx, p) {
        const one = 1 / ctx.getTransform().a;          // a device px, in panel px
        const ratio = window.devicePixelRatio || 1;
        ctx.save();
        const it = picked && index.placed[state.showing] && index.placed[state.showing].get(picked);
        if (it) {
            ctx.strokeStyle = "#ffffff";
            ctx.lineWidth = 1.5 * ratio * one;
            ctx.setLineDash([5 * ratio * one, 4 * ratio * one]);
            ctx.strokeRect(it.x, it.y, it.w, it.h);
            ctx.setLineDash([]);
        }
        // The handle on the window's right edge that sizes it.
        const w = hold && hold.kind === "width" ? hold.width : p.w;
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.roundRect(p.w / 2 + w / 2 - 2.5 * ratio * one, p.h / 2 - 14 * ratio * one, 5 * ratio * one, 28 * ratio * one, 2.5 * ratio * one);
        ctx.fill();
        if (hold && hold.kind === "width") {
            ctx.strokeStyle = "#ffffffaa";
            ctx.lineWidth = ratio * one;
            ctx.setLineDash([4 * ratio * one, 4 * ratio * one]);
            ctx.strokeRect(p.w / 2 - w / 2, 0, w, p.h);
        }
        ctx.restore();
    }

    function drawTime() {
        const ck = state.clock, fps = state.fps;
        $("scrub").max = String(ck.total - 1);
        $("scrub").value = String(frame);
        $("time").textContent = `${(frame / fps).toFixed(1)} / ${((ck.total - 1) / fps).toFixed(1)}`;
        $("lanes").style.setProperty("--at", `${frame / Math.max(1, ck.total - 1) * 100}%`);
    }

    // A small picture of some prims, fitted to a canvas (the shelf's tiles, the states).
    function picture(canvas, prims, bounds, width, height, poseOf) {
        sizeCanvas(canvas, width, height);
        const ctx = canvas.getContext("2d");
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const [bx, by, bw, bh] = bounds;
        const k = Math.min(canvas.width / bw, canvas.height / bh) * 0.92;
        ctx.setTransform(k, 0, 0, k, (canvas.width - bw * k) / 2 - bx * k, (canvas.height - bh * k) / 2 - by * k);
        for (const p of prims) {
            ctx.save();
            if (poseOf) poseOf(ctx, p);
            drawPrim(ctx, p, 1, ctx.getTransform().a);
            ctx.restore();
        }
        // The canvas is as wide as its place in the page (CSS), its own shape kept.
        canvas.style.width = "";
        canvas.style.height = "";
    }

    // ------------------------------------------------------ the shelf --
    // + Add: a panel over the stage with every layout (in its groups) or every component,
    // each a small live picture; a field finds them by name.

    function shelfKey(st) {
        const spec = st.spec, shelf = st.shelf || {layouts: [], components: []};
        return JSON.stringify([spec.kind, spec.style, spec.accent, spec.dark, shelf.layouts.map(t => t.id),
                               shelf.components.map(t => t.id), st.face, st.bold_face]);
    }

    function tileButton(item, onClick) {
        const canvas = el("canvas", {width: 180, height: 64, "aria-hidden": "true"});
        const button = el("button.pn-tile", {type: "button", title: item.name, dataset: {id: item.id, name: item.name.toLowerCase()},
                                             onclick: onClick}, [canvas, el("span", {text: item.name})]);
        const tile = item.tile;
        if (tile && tile.prims) picture(canvas, tile.prims, [0, 0, tile.w || 1, tile.h || 1], 180, 64);
        return button;
    }

    function drawShelf() {
        const key = shelfKey(state);
        if (key === shelfFor) return markShelf();
        shelfFor = key;
        const shelf = state.shelf || {layouts: [], components: []};
        const groups = new Map();
        shelf.layouts.forEach(item => {
            if (!groups.has(item.group)) groups.set(item.group, []);
            groups.get(item.group).push(item);
        });
        const layouts = el("div.pn-shelf-part", {dataset: {part: "layouts"}});
        for (const [group, items] of groups) {
            layouts.append(el("div.pn-shelf-set", {}, [
                el("h3.dg-group-title.pn-shelf-group", {text: group}),
                el("div.pn-tiles", {}, items.map(item => tileButton(item, () => {
                    if (!state || state.adding) return;
                    Buddy.send("layout", {page: state.showing, layout: item.id});
                    addPanel.show(false);
                })))]));
        }
        const components = el("div.pn-shelf-part", {dataset: {part: "insert"}},
                              [el("div.pn-shelf-set", {}, [el("div.pn-tiles", {}, shelf.components.map(item => tileButton(item, () => {
                                  if (!state || state.adding) return;
                                  Buddy.send("insert", {component: item.id, into: picked || "", at: -1});
                                  addPanel.show(false);
                              })))])]);
        $("shelf").replaceChildren(layouts, components);
        markShelf();
    }

    function markShelf() {
        if (!state) return;
        const find = $("add-find").value.trim().toLowerCase();
        let any = false;
        $("shelf").querySelectorAll(".pn-shelf-part").forEach(part => {
            part.hidden = part.dataset.part !== addMode;
            part.querySelectorAll(".pn-shelf-set").forEach(set => {
                let some = false;
                set.querySelectorAll(".pn-tile").forEach(b => {
                    b.hidden = !!find && !b.dataset.name.includes(find);
                    some = some || !b.hidden;
                });
                set.hidden = !some;
                if (!part.hidden) any = any || some;
            });
        });
        $("no-tiles").hidden = any;
        pressed($("add-mode"), addMode);
        const page = pageOf(state.showing);
        $("shelf").querySelectorAll('[data-part="layouts"] .pn-tile').forEach(b =>
            b.setAttribute("aria-current", String(!!page && b.dataset.id === page.layout)));
        $("shelf").querySelectorAll(".pn-tile").forEach(b => { b.disabled = !!state.adding; });
        const into = picked ? boxOf(picked) : null;
        $("shelf-hint").textContent = addMode === "layouts" ? "Click one to make the page in view that layout."
            : into ? `Goes into ${label(into)}.` : "Goes onto the page in view. Pick a box first to put it in there.";
    }

    // -------------------------------------------------- the page chips --

    function drawPages() {
        if (renaming) return;                       // the field being typed in stays
        const pages = state.spec.pages, one = pages.length < 2;
        const chips = pages.map((page, i) => {
            const chosen = page.id === state.showing;
            const name = el("button.pn-chip-name", {type: "button", role: "tab", "aria-selected": String(chosen),
                                                    title: "Double-click or press F2 to rename it",
                                                    onclick: () => showPage(page.id), ondblclick: () => startRename(page.id)},
                            [`${i + 1} · `, el("span", {translate: "no", text: page.name})]);
            const parts = [name];
            if (chosen && !one) {
                const x = el("button.pn-chip-x", {type: "button", "aria-label": "Remove this page", title: "Remove this page",
                                                  onclick: () => Buddy.send("page_remove", {id: page.id})});
                x.append(Buddy.icon("close"));
                x.disabled = !!state.adding;
                parts.push(x);
            }
            return el("span.pn-chip", {dataset: {page: page.id}, "aria-selected": String(chosen)}, parts);
        });
        const add = el("button.btn.ghost", {type: "button", id: "page-add", text: "+ Page", title: "Add an empty page",
                                            onclick: () => Buddy.send("page_add", {})});
        add.disabled = !!state.adding || pages.length >= ((state.limits || {}).pages || 8);
        $("pages").replaceChildren(...chips, add);
    }

    function showPage(id) {
        if (!state) return;
        view = "edit";
        if (id !== state.showing) Buddy.send("page_show", {id});
        else if (!playing) paint();
    }

    function startRename(id) {
        const page = pageOf(id);
        if (!page || state.adding) return;
        renaming = id;
        const chip = $("pages").querySelector(`.pn-chip[data-page="${id}"]`);
        if (!chip) { renaming = ""; return; }
        const input = el("input.field", {type: "text", value: page.name, maxlength: "40", "aria-label": "Page name",
                                         autocomplete: "off", spellcheck: "false"});
        let done = false;
        const finish = keep => {
            if (done) return;
            done = true;
            renaming = "";
            const name = input.value.trim();
            if (keep && name && name !== page.name) Buddy.send("page_rename", {id, name});
            drawPages();
        };
        input.addEventListener("keydown", e => {
            if (e.key === "Enter") { e.preventDefault(); finish(true); }
            else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
        });
        input.addEventListener("blur", () => finish(true));
        chip.replaceChildren(input);
        input.focus();
        input.select();
    }

    // ------------------------------------------------------- the layers --
    // The page in view's boxes as a tree (its own box, the page, isn't a row: a row at
    // the left edge is in it). A row is picked by a click, renamed by a double-click or
    // F2, taken out by Delete, and dragged to a new place: between two rows (a line at
    // the depth it goes in, the pointer's distance from the left choosing where there's
    // a choice) or into a container (its row lit). Pointer events, not HTML drag and
    // drop; it starts once the pointer has moved a few px, so a click still picks.

    const ROW_DRAG = 4;           // how far the pointer moves before a row is dragged, px
    const ROW_INDENT = 14;        // a row's indent for each level it's in, px
    let layerRows = [];           // the rows shown: [{id, parent, depth, index, kids, open}]
    let layersFor = "";
    let rowDrag = null;           // a row pressed: {id, x, y, cx, cy, pointer, on, at, line, ghost}
    let rowClickGone = false;     // the click that ends a drag picks nothing

    function rootOfShowing() {
        const page = pageOf(state.showing);
        return page ? page.root : null;
    }

    function foldsOf(pageId) {
        if (!folds.has(pageId)) folds.set(pageId, new Map());
        return folds.get(pageId);
    }

    function isOpen(id, depth) {
        const mine = foldsOf(state.showing);
        return mine.has(id) ? mine.get(id) : depth === 0;
    }

    function flatRows() {
        const rows = [], root = rootOfShowing();
        if (!root) return rows;
        const walk = (box, depth) => box.children.forEach((c, i) => {
            const open = isOpen(c.id, depth);
            rows.push({id: c.id, parent: box.id, depth, index: i, kids: c.role === "section" ? c.children.length : -1, open});
            if (c.role === "section" && open) walk(c, depth + 1);
        });
        walk(root, 0);
        return rows;
    }

    function layerRow(info) {
        const box = boxOf(info.id), selected = info.id === picked;
        const parts = [];
        if (info.kids > 0) {
            const twist = el("button.pn-layer-twist", {type: "button", tabindex: "-1",
                                                       "aria-label": info.open ? "Collapse" : "Expand",
                                                       title: info.open ? "Collapse" : "Expand",
                                                       onclick: e => { e.stopPropagation(); fold(info.id, info.open); }});
            twist.append(Buddy.icon("right"));
            parts.push(twist);
        } else {
            parts.push(el("span.pn-layer-gap", {"aria-hidden": "true"}));
        }
        parts.push(el("span.pn-layer-text", {}, layerName(box)));
        if (animated(info.id)) parts.push(el("span.pn-layer-dot", {title: "It has an animation of its own", "aria-label": "It has an animation of its own"}));
        const row = el("div.pn-layer", {role: "treeitem", "aria-level": String(info.depth + 1), "aria-selected": String(selected),
                                        tabindex: "-1", dataset: {id: info.id},
                                        onclick: () => { pick(info.id); },
                                        ondblclick: () => renameLayer(info.id)}, parts);
        if (info.kids > 0) row.setAttribute("aria-expanded", String(info.open));
        row.style.paddingLeft = 4 + info.depth * ROW_INDENT + "px";
        return row;
    }

    function fillLayers() {
        if (!state) return;
        if ($("layers").querySelector("input.field")) return;          // a name being typed stays
        layerRows = flatRows();
        const key = JSON.stringify([state.showing, layerRows, layerRows.map(r => {
            const box = boxOf(r.id);
            return [layerName(box).map(s => s.textContent).join(""), animated(r.id)];
        })]);
        if (key !== layersFor) {
            layersFor = key;
            $("layers").replaceChildren(...layerRows.map(layerRow));
        }
        markLayers();
        $("no-layers").hidden = layerRows.length > 0;
    }

    function markLayers() {
        const rows = [...$("layers").querySelectorAll(".pn-layer")];
        const focusable = rows.find(r => r.dataset.id === picked) || rows[0];
        rows.forEach(r => {
            r.setAttribute("aria-selected", String(r.dataset.id === picked));
            r.tabIndex = r === focusable ? 0 : -1;
        });
    }

    function layerRowOf(id) {
        return [...$("layers").querySelectorAll(".pn-layer")].find(r => r.dataset.id === id) || null;
    }

    // The picked box's row in sight, every container it's in opened.
    function showLayer(id) {
        if (!id) return;
        let opened = false;
        const mine = foldsOf(state.showing), rows = new Map(layerRows.map(r => [r.id, r]));
        chainOf(id).slice(1, -1).forEach(b => {
            if (!rows.has(b.id) || !rows.get(b.id).open) opened = true;
            mine.set(b.id, true);
        });
        if (opened) fillLayers();
        const row = layerRowOf(id);
        if (row && row.scrollIntoView) row.scrollIntoView({block: "nearest"});
    }

    function fold(id, shut) {
        foldsOf(state.showing).set(id, !shut);
        if (shut && picked && within(picked, id) && picked !== id) pick(id);
        fillLayers();
    }

    function renameLayer(id) {
        const box = boxOf(id), row = layerRowOf(id);
        if (!box || !row || state.adding || $("layers").querySelector("input.field")) return;
        const text = row.querySelector(".pn-layer-text");
        const field = el("input.field", {type: "text", value: box.name, maxLength: 40, spellcheck: false, autocomplete: "off",
                                         "aria-label": "Layer name"});
        let finished = false;
        const done = keep => {
            if (finished || !field.isConnected) return;
            finished = true;
            const name = field.value.trim();
            field.replaceWith(text);
            if (keep && name && name !== box.name) Buddy.send("box", {id, key: "name", value: name});
            layersFor = "";
            fillLayers();
        };
        field.addEventListener("keydown", e => {
            e.stopPropagation();
            if (e.key === "Enter") { e.preventDefault(); done(true); }
            if (e.key === "Escape") { e.preventDefault(); done(false); }
        });
        field.addEventListener("blur", () => done(true));
        field.addEventListener("click", e => e.stopPropagation());
        field.addEventListener("pointerdown", e => e.stopPropagation());
        text.replaceWith(field);
        field.focus();
        field.select();
    }

    // Where the dragged box would go with the pointer at (x, y): {into, at, depth, line
    // (the y of the line between rows) or row (the container lit)}, or null. "at" counts
    // among into's children as they are with the dragged box taken out.
    function rowDropAt(x, y) {
        const rows = [...$("layers").querySelectorAll(".pn-layer")], box = $("layers").getBoundingClientRect();
        if (!rows.length || !rowDrag) return null;
        const id = rowDrag.id, root = rootOfShowing();
        const ok = into => into !== id && !within(into, id);
        const kidsOf = into => (boxOf(into) || {children: []}).children.filter(c => c.id !== id).length;
        const placeAt = (into, at) => {
            const found = index.boxes.get(id);
            const from = found ? boxOf(found.parent).children.findIndex(c => c.id === id) : -1;
            return found && found.parent === into && from >= 0 && from < at ? at - 1 : at;
        };
        y = Math.max(box.top + 1, Math.min(box.bottom - 1, y));
        const i = rows.findIndex(r => r.getBoundingClientRect().bottom > y);
        let gap = rows.length;
        if (i >= 0) {
            const r = rows[i].getBoundingClientRect(), info = layerRows[i];
            const share = (y - r.top) / r.height;
            if (info.kids >= 0 && share >= 0.3 && share <= 0.7 && ok(info.id)) {
                return {into: info.id, at: kidsOf(info.id), depth: info.depth + 1, row: rows[i]};
            }
            gap = share < 0.5 ? i : i + 1;
        }
        const above = gap > 0 ? layerRows[gap - 1] : null, below = gap < rows.length ? layerRows[gap] : null;
        const ways = [];
        if (!above) {
            ways.push({into: root.id, at: 0, depth: 0});
        } else if (above.kids > 0 && above.open && below && below.parent === above.id) {
            ways.push({into: above.id, at: 0, depth: above.depth + 1});       // just under a container's row: first in it
        } else {
            // After a row, or after the containers it's the last one in.
            for (let a = above; a;) {
                ways.push({into: a.parent, at: placeAt(a.parent, a.index + 1), depth: a.depth});
                const parent = boxOf(a.parent);
                if (!parent || a.parent === root.id || a.index !== parent.children.length - 1) break;
                a = layerRows.find(r => r.id === a.parent) || null;
            }
        }
        const fitting = ways.filter(w => ok(w.into));
        if (!fitting.length) return null;
        const want = Math.floor((x - box.left - 4) / ROW_INDENT);
        const to = fitting.reduce((best, w) => Math.abs(w.depth - want) < Math.abs(best.depth - want) ? w : best);
        const edge = gap < rows.length ? rows[gap].getBoundingClientRect().top - 1 : rows[gap - 1].getBoundingClientRect().bottom + 1;
        return {...to, line: edge};
    }

    function rowDragShow() {
        const d = rowDrag;
        d.at = rowDropAt(d.cx, d.cy);
        const box = $("layers").getBoundingClientRect();
        [...$("layers").querySelectorAll(".pn-layer")].forEach(r => {
            r.classList.toggle("pn-dragged", r.dataset.id === d.id || within(r.dataset.id, d.id));
            r.classList.toggle("pn-drop-into", !!(d.at && d.at.row === r));
        });
        const shown = !!(d.at && !d.at.row);
        d.line.hidden = !shown;
        if (shown) {
            const left = box.left + 4 + d.at.depth * ROW_INDENT + 16;
            Object.assign(d.line.style, {left: left + "px", top: d.at.line - 1 + "px", width: Math.max(8, box.right - 4 - left) + "px"});
        }
        Object.assign(d.ghost.style, {left: d.cx + 12 + "px", top: d.cy + 8 + "px"});
    }

    function startRowDrag() {
        const d = rowDrag;
        if (picked !== d.id) pick(d.id);
        d.on = true;
        try { $("layers").setPointerCapture(d.pointer); } catch (_gone) { /* a pointer that has already left */ }
        const box = boxOf(d.id);
        d.line = el("div.pn-drop-line", {hidden: true});
        d.ghost = el("div.pn-drop-ghost", {}, layerName(box, ".pn-ghost-role"));
        document.body.append(d.line, d.ghost);
        document.body.classList.add("pn-rows-dragging");
    }

    function endRowDrag(drop) {
        const d = rowDrag;
        rowDrag = null;
        if (!d || !d.on) return;
        d.line.remove();
        d.ghost.remove();
        document.body.classList.remove("pn-rows-dragging");
        try { $("layers").releasePointerCapture(d.pointer); } catch (_gone) { /* never held */ }
        rowClickGone = true;
        [...$("layers").querySelectorAll(".pn-layer")].forEach(r => r.classList.remove("pn-dragged", "pn-drop-into"));
        const to = drop ? d.at : null, found = index.boxes.get(d.id);
        if (!to || !found || state.adding) return;
        const from = boxOf(found.parent).children.findIndex(c => c.id === d.id);
        if (to.into === found.parent && to.at === from) return;                // where it was
        if (to.row) foldsOf(state.showing).set(to.into, true);
        Buddy.send("move", {id: d.id, into: to.into, at: to.at});
    }

    function bindLayers() {
        const list = $("layers");
        list.addEventListener("pointerdown", e => {
            rowClickGone = false;
            const row = e.target.closest(".pn-layer");
            if (!row || !state || e.button !== 0 || rowDrag || state.adding || e.target.closest("button, input")) return;
            rowDrag = {id: row.dataset.id, x: e.clientX, y: e.clientY, cx: e.clientX, cy: e.clientY, pointer: e.pointerId, on: false};
        });
        window.addEventListener("pointermove", e => {
            const d = rowDrag;
            if (!d || e.pointerId !== d.pointer) return;
            d.cx = e.clientX;
            d.cy = e.clientY;
            if (!d.on) {
                if (Math.hypot(d.cx - d.x, d.cy - d.y) < ROW_DRAG || !boxOf(d.id)) return;
                startRowDrag();
            }
            e.preventDefault();
            rowDragShow();
        });
        window.addEventListener("pointerup", e => { if (rowDrag && e.pointerId === rowDrag.pointer) endRowDrag(true); });
        window.addEventListener("pointercancel", e => { if (rowDrag && e.pointerId === rowDrag.pointer) endRowDrag(false); });
        window.addEventListener("keydown", e => {
            if (!rowDrag || e.key !== "Escape") return;
            if (rowDrag.on) {
                e.preventDefault();
                e.stopPropagation();
            }
            endRowDrag(false);
        }, true);
        list.addEventListener("click", e => {
            if (!rowClickGone) return;
            rowClickGone = false;
            e.stopPropagation();
            e.preventDefault();
        }, true);
        list.addEventListener("scroll", () => { if (rowDrag && rowDrag.on) rowDragShow(); });
        // The arrows walk the tree: up and down the rows, right opens, left closes or goes out.
        list.addEventListener("keydown", e => {
            if (e.target.closest("input")) return;
            const i = layerRows.findIndex(r => r.id === picked), info = layerRows[i];
            let next = null;
            if (e.key === "ArrowDown") next = layerRows[Math.min(layerRows.length - 1, i + 1)];
            else if (e.key === "ArrowUp") next = layerRows[Math.max(0, i - 1)];
            else if (e.key === "ArrowRight" && info && info.kids > 0) {
                if (!info.open) fold(info.id, false);
                else next = layerRows[i + 1];
            } else if (e.key === "ArrowLeft" && info) {
                if (info.kids > 0 && info.open) fold(info.id, true);
                else next = layerRows.find(r => r.id === info.parent);
            } else return;
            e.preventDefault();
            if (next) {
                pick(next.id);
                const row = layerRowOf(next.id);
                if (row) row.focus();
            }
        });
    }

    // ----------------------------------------------------- the controls --

    const timers = new Map();

    function later(key, send, wait) {
        clearTimeout(timers.get(key));
        if (!wait) return send();
        timers.set(key, setTimeout(send, wait));
    }

    function setField(key, value, wait) {
        later("set:" + key, () => Buddy.send("set", {key, value}), wait);
    }

    function setBox(key, value, wait) {
        const id = picked;
        if (id) later("box:" + id + ":" + key, () => Buddy.send("box", {id, key, value}), wait);
    }

    function plain(value) {
        return typeof value === "number" ? String(+value.toFixed(6)) : String(value == null ? "" : value);
    }

    function put(node, value) {
        // What's being typed is left alone: Python has it, tidied, when the field is left.
        if (node !== document.activeElement && node.value !== plain(value)) node.value = plain(value);
    }

    function pressed(group, value) {
        group.querySelectorAll("button[data-value]").forEach(b =>
            b.setAttribute("aria-pressed", String(b.dataset.value === String(value))));
    }

    function bind() {
        document.querySelectorAll("[data-key]").forEach(node => {
            const key = node.dataset.key;
            if (node.classList.contains("segmented")) {
                node.addEventListener("click", e => {
                    const button = e.target.closest("button[data-value]");
                    if (button) setField(key, button.dataset.value);
                });
            } else if (node.tagName === "SELECT") {
                node.addEventListener("change", () => setField(key, node.value));
            } else if (node.type === "range" || node.type === "color") {
                node.addEventListener("input", () => setField(key, Number(node.value), 30));
            } else {
                node.addEventListener("input", () => setField(key, node.value, TYPING_MS));
                node.addEventListener("change", () => setField(key, node.value));
            }
        });
        $("accent").addEventListener("input", e => setField("accent", e.target.value, 30));
        $("accents").addEventListener("click", e => {
            const b = e.target.closest("button[data-colour]");
            if (b) setField("accent", b.dataset.colour);
        });
        $("dark").addEventListener("click", e => {
            const b = e.target.closest("button[data-value]");
            if (b) setField("dark", b.dataset.value === "true");
        });
        $("build-on").addEventListener("change", e => setField("build", {...state.spec.build, on: e.target.checked}));
        const buildField = (id, key) => {
            const send = wait => setField("build", {...state.spec.build, [key]: $(id).value}, wait);
            $(id).addEventListener("input", () => send(TYPING_MS));
            $(id).addEventListener("change", () => send(0));
        };
        buildField("build-stagger", "stagger");
        buildField("build-seconds", "seconds");
        $("width").addEventListener("input", e => setField("width", Number(e.target.value), 40));

        // The picked layer.
        $("box-text").addEventListener("input", e => setBox("text", e.target.value, TYPING_MS));
        $("box-text").addEventListener("change", e => setBox("text", e.target.value));
        $("box-w").addEventListener("click", e => {
            const b = e.target.closest("button[data-value]"), box = boxOf(picked);
            if (!b || !box) return;
            if (b.dataset.value === "fixed") {
                const it = placedAnywhere(picked);
                if (typeof box.w !== "number") setBox("w", Math.round(it ? it.w : 120));
            } else {
                setBox("w", b.dataset.value);
            }
        });
        const px = wait => {
            const v = Number(String($("box-px").value).replace(",", "."));
            if (Number.isFinite(v) && v > 0) setBox("w", v, wait);
        };
        $("box-px").addEventListener("input", () => px(TYPING_MS));
        $("box-px").addEventListener("change", () => px(0));
        $("box-go").addEventListener("change", e => {
            const box = boxOf(picked);
            const how = box && box.go ? box.go.with : "";
            setBox("go", e.target.value ? {page: e.target.value, with: how} : null);
        });
        $("box-with").addEventListener("change", e => {
            const box = boxOf(picked);
            if (box && box.go) setBox("go", {page: box.go.page, with: e.target.value});
        });
        $("box-p").addEventListener("input", e => setBox("p", Number(e.target.value), 30));
        $("box-remove").addEventListener("click", removePicked);
        $("image-choose").addEventListener("click", () => { if (picked && !state.adding) Buddy.send("image", {id: picked}); });
        $("image-clear").addEventListener("click", () => { if (picked && !state.adding) Buddy.send("image_clear", {id: picked}); });
        $("chart-shape").addEventListener("click", e => {
            const b = e.target.closest("button[data-value]"), box = boxOf(picked);
            if (b && box && box.chart) setBox("chart", {shape: b.dataset.value, rows: box.chart.rows});
        });
        const rows = wait => {
            const box = boxOf(picked);
            const read = parseRows($("chart-rows").value);
            if (box && box.chart && read.length) setBox("chart", {shape: box.chart.shape, rows: read}, wait);
        };
        $("chart-rows").addEventListener("input", () => rows(600));
        $("chart-rows").addEventListener("change", () => rows(0));

        // Its animations: each a check and its main control.
        // On: as the page builds - or, on a page that doesn't build on, rising in by itself.
        $("an-arrive").addEventListener("change", e => setBox("arrive", !e.target.checked ? {kind: "none"}
            : state.spec.build.on ? null : {kind: "rise"}));
        $("an-arrive-kind").addEventListener("change", e => setBox("arrive", e.target.value ? {kind: e.target.value} : null));
        $("an-type").addEventListener("change", e => {
            const box = boxOf(picked);
            if (!box) return;
            setBox("type", e.target.checked ? {speed: 18.0, start: "page", caret: true, text: box.role === "field" ? box.text || "" : ""} : null);
        });
        const typing = (key, read, wait) => {
            const box = boxOf(picked);
            if (box && box.type) setBox("type", {...box.type, [key]: read()}, wait);
        };
        const speed = () => {
            const v = Number(String($("an-type-speed").value).replace(",", "."));
            return Number.isFinite(v) && v > 0 ? v : null;
        };
        $("an-type-speed").addEventListener("input", () => { if (speed() !== null) typing("speed", speed, TYPING_MS); });
        $("an-type-speed").addEventListener("change", () => { if (speed() !== null) typing("speed", speed, 0); });
        $("an-type-start").addEventListener("change", e => typing("start", () => e.target.value, 0));
        $("an-type-caret").addEventListener("change", e => typing("caret", () => e.target.checked, 0));
        $("an-type-text").addEventListener("input", e => typing("text", () => e.target.value, TYPING_MS));
        $("an-type-text").addEventListener("change", e => typing("text", () => e.target.value, 0));
        $("box-reacts").addEventListener("change", e => setBox("reacts", e.target.checked));

        // How boxes answer (the States panel).
        $("look").addEventListener("click", e => {
            const b = e.target.closest("button[data-value]");
            if (!b) return;
            look = b.dataset.value;
            pressed($("look"), look);
            view = "edit";
            if (!playing && !live()) paint();
        });
        document.querySelectorAll("[data-state] [data-answer]").forEach(node => {
            const stateName = node.closest("[data-state]").dataset.state, key = node.dataset.answer;
            node.addEventListener("input", () => {
                const id = ownStates() ? picked : "";
                later(`answers:${id}:${stateName}:${key}`,
                      () => Buddy.send("answers", {id, state: stateName, key, value: Number(node.value)}), 30);
            });
        });
        $("own-states").addEventListener("change", e => {
            const box = boxOf(picked);
            if (!box) return;
            if (e.target.checked) {
                // Its own states start as the panel's.
                Buddy.send("answers", {id: picked, state: "hover", key: "grow", value: state.spec.answers.hover.grow});
            } else {
                Buddy.send("answers", {id: picked, state: "hover", key: "reset", value: null});
            }
        });
        const seconds = (id, which) => {
            const send = wait => later("answers::" + which, () =>
                Buddy.send("answers", {id: "", state: which, key: "seconds", value: $(id).value}), wait);
            $(id).addEventListener("input", () => send(TYPING_MS));
            $(id).addEventListener("change", () => send(0));
        };
        seconds("hover-seconds", "hover");
        seconds("press-seconds", "press");

        // Rules, the shelf.
        $("add-rule").addEventListener("click", () => Buddy.send("rule", {id: "", key: "", value: null}));
        $("add-mode").addEventListener("click", e => {
            const b = e.target.closest("button[data-value]");
            if (!b) return;
            addMode = b.dataset.value;
            markShelf();
        });
        $("add-find").addEventListener("input", () => markShelf());

        // Window | In the frame; the timeline shown or not.
        $("fit").addEventListener("click", e => {
            const b = e.target.closest("button[data-value]");
            if (b) setFit(b.dataset.value, false);
        });
        $("dock-toggle").addEventListener("click", () => {
            dockOpen = !dockOpen;
            try {
                sessionStorage.setItem(DOCK_KEY, dockOpen ? "shown" : "hidden");
            } catch (_err) {
                // just for now
            }
            drawDock();
        });
    }

    function drawDock() {
        $("dock-body").hidden = !dockOpen;
        $("dock-toggle").textContent = dockOpen ? "Hide timeline" : "Show timeline";
        $("dock-toggle").setAttribute("aria-expanded", String(dockOpen));
        if (state && !playing && !live()) paint();
    }

    function setFit(next, auto) {
        forced = !!auto && next === "frame" && fit !== "frame" ? true : auto ? forced : false;
        fit = next;
        pressed($("fit"), fit);
        $("black-hint").hidden = fit !== "frame";
        drawHint();
        if (state && !playing && !live()) paint();
    }

    function parseRows(text) {
        const out = [];
        for (const line of String(text).split(/\r?\n/)) {
            if (!line.trim()) continue;
            const cells = line.includes("\t") ? line.split("\t") : line.split(/[,;]/);
            if (cells.length < 2) continue;
            const value = Number(cells[cells.length - 1].trim().replace(/[\s%$€£]/g, ""));
            if (!Number.isFinite(value)) continue;
            out.push({label: cells.slice(0, -1).join(" ").trim(), value});
        }
        return out;
    }

    function rowsText(rows) {
        return (rows || []).map(r => `${r.label}\t${plain(r.value)}`).join("\n");
    }

    function ownStates() {
        const box = boxOf(picked);
        return !!(box && box.states);
    }

    function fill(spec) {
        document.querySelectorAll("[data-key]").forEach(node => {
            const value = spec[node.dataset.key];
            if (node.classList.contains("segmented")) pressed(node, value);
            else put(node, value);
        });
        pressed($("dark"), spec.dark);
        $("build-on").checked = !!spec.build.on;
        put($("build-stagger"), spec.build.stagger);
        put($("build-seconds"), spec.build.seconds);
        put($("accent"), spec.accent);
        const accents = state.accents || [];
        if ($("accents").dataset.of !== accents.join()) {
            $("accents").dataset.of = accents.join();
            $("accents").replaceChildren(...accents.map(c => el("button.pn-swatch", {
                type: "button", dataset: {colour: c}, style: `background:${c}`, "aria-label": c, title: c, translate: "no"})));
        }
        $("accents").querySelectorAll("button").forEach(b =>
            b.setAttribute("aria-pressed", String(b.dataset.colour.toLowerCase() === spec.accent.toLowerCase())));
        const [low, high] = (state.limits || {}).width || [320, 1600];
        $("width").min = String(low);
        $("width").max = String(high);
        if (!(hold && hold.kind === "width")) put($("width"), spec.width);
        put($("hover-seconds"), spec.answers.hover_seconds);
        put($("press-seconds"), spec.answers.press_seconds);
        fillPicked();
        fillStates();
        fillChanges();
        fillRules();
    }

    // The right column: the window's settings, or - a layer picked - the layer's.
    function fillPicked() {
        const box = picked ? boxOf(picked) : null;
        if (!box) picked = "";
        $("window-side").hidden = !!box;
        $("layer-side").hidden = !box;
        drawTrail();
        if (!box) {
            chartPop.show(false);
            typingPop.show(false);
            return;
        }
        const root = index.boxes.get(picked).parent === "";
        const busy = !!state.adding;
        $("picked-name").replaceChildren(...layerName(box));
        $("box-text-row").hidden = !hasText(box);
        if (hasText(box)) put($("box-text"), box.text);
        const how = typeof box.w === "number" ? "fixed" : box.w;
        pressed($("box-w"), how);
        $("box-px-box").hidden = how !== "fixed";
        if (how === "fixed") put($("box-px"), box.w);
        const here = index.boxes.get(picked).page;
        const go = $("box-go");
        const options = [["", "Nothing"], ...state.spec.pages.filter(p => p.id !== here).map(p => [p.id, p.name])];
        go.replaceChildren(...options.map(([value, text], i) =>
            el("option", {value, text, translate: i ? "no" : null})));
        go.value = box.go ? box.go.page : "";
        $("box-with-box").hidden = !box.go;
        $("box-with").value = box.go ? (box.go.with || state.spec.go_with) : state.spec.go_with;
        $("box-p-box").hidden = box.role !== "progress";
        if (box.role === "progress") put($("box-p"), box.p);
        $("chart-row").hidden = box.role !== "chart";
        if (box.role !== "chart") chartPop.show(false);
        else {
            pressed($("chart-shape"), box.chart.shape);
            if (document.activeElement !== $("chart-rows")) $("chart-rows").value = rowsText(box.chart.rows);
        }
        $("image-row").hidden = box.role !== "image";
        if (box.role === "image") fillImage();
        fillAnims(box);
        $("box-remove").disabled = busy || root;
        $("box-remove").title = root ? "A page's own box can't be removed" : "";
    }

    function fillImage() {
        const box = picked ? boxOf(picked) : null;
        if (!box || box.role !== "image") return;
        const url = (state.images || {})[box.id] || "", img = $("image-preview");
        const got = url ? pictureOf(url) : null;
        if (url && img.getAttribute("src") !== url) img.src = url;
        img.hidden = !(got && got.ok);
        const look = $("image-look");
        let mark = look.querySelector("svg");
        if (!mark) {
            mark = Buddy.icon("image");
            look.append(mark);
        }
        mark.style.display = img.hidden ? "" : "none";
        $("image-clear").hidden = !box.image;
        $("image-choose").disabled = $("image-clear").disabled = !!state.adding;
    }

    // One row per way the box can move (state.animations): a check and its one main control.
    function fillAnims(box) {
        const anims = new Map(animsOf(box.id).map(a => [a.id, a]));
        const busy = !!state.adding;
        document.querySelectorAll("#anims [data-anim]").forEach(row => { row.hidden = !anims.has(row.dataset.anim); });
        const kind = box.arrive && box.arrive.kind ? box.arrive.kind : "";
        const arrive = anims.get("arrive");
        $("an-arrive").checked = arrive && "on" in arrive ? !!arrive.on : kind !== "none";
        $("an-arrive-kind").value = kind === "none" ? "" : kind;
        $("an-arrive-kind").disabled = busy || kind === "none";
        $("an-arrive").disabled = busy;
        const typing = box.type || null;
        $("an-type").checked = !!typing;
        $("an-type").disabled = busy;
        document.querySelectorAll('#anims [data-anim="type"] .pn-anim-more').forEach(node => { node.hidden = !typing; });
        $("an-type-text-box").hidden = !typing || box.role !== "field";
        if (typing) {
            put($("an-type-speed"), typing.speed);
            put($("an-type-text"), typing.text || "");
            $("an-type-start").value = typing.start || "page";
            $("an-type-caret").checked = !!typing.caret;
        } else {
            typingPop.show(false);
        }
        $("box-reacts").checked = !!box.reacts;
        $("box-reacts").disabled = busy;
    }

    function drawTrail() {
        const crumbs = [el("button", {type: "button", text: "Window", "aria-current": String(!picked),
                                      onclick: () => pick("")})];
        if (picked) {
            chainOf(picked).slice(1).forEach(box => {
                crumbs.push(el("span.pn-trail-sep", {text: "›", "aria-hidden": "true"}),
                            el("button", {type: "button", translate: "no", text: shownName(box), dataset: {id: box.id},
                                          "aria-current": String(box.id === picked), onclick: () => pick(box.id)}));
            });
        }
        $("trail").replaceChildren(...crumbs);
    }

    function pick(id) {
        const was = picked;
        picked = id || "";
        if (picked !== was) Buddy.send("pick", {id: picked});     // where a dropped picture goes
        view = "edit";
        fillPicked();
        fillStates();
        fillLayers();
        showLayer(picked);
        markShelf();
        drawLanes();
        if (!playing && !live()) paint();
    }

    function removePicked() {
        if (!picked || state.adding) return;
        const found = index.boxes.get(picked);
        if (!found || !found.parent) return;
        Buddy.send("remove_box", {id: picked});
        picked = "";
    }

    function fillStates() {
        const box = picked ? boxOf(picked) : null;
        const answers = (box && box.states) || state.spec.answers;
        $("own-states").checked = !!(box && box.states);
        $("own-states").disabled = !box || !!state.adding;
        document.querySelectorAll("[data-state]").forEach(group => {
            const values = answers[group.dataset.state];
            group.querySelectorAll("[data-answer]").forEach(node => put(node, values[node.dataset.answer]));
        });
        if (!$("states-pop").hidden) drawStates();
    }

    // The three states side by side: the picked box (or a sample button) in each.
    function drawStates() {
        const box = picked ? boxOf(picked) : null;
        let prims, bounds, placed = null;
        if (box) {
            const page = index.boxes.get(picked).page;
            const inside = new Set();
            const walk = b => { inside.add(b.id); b.children.forEach(walk); };
            walk(box);
            prims = (state.drawn[page] || []).filter(p => inside.has(p.box));
            placed = index.placed[page] && index.placed[page].get(picked);
        } else {
            const sample = ((state.shelf || {}).components || []).find(c => c.id === "button");
            prims = sample && sample.tile ? sample.tile.prims.filter(p => p.box) : [];
            placed = primBounds(prims);
        }
        if (!placed) placed = {x: 0, y: 0, w: 100, h: 40};
        const room = Math.max(placed.w, placed.h) * 0.25 + 8;
        bounds = [placed.x - room, placed.y - room, placed.w + 2 * room, placed.h + 2 * room];
        [["rest", 0, 0], ["hover", 1, 0], ["press", 1, 1]].forEach(([name, hover, press]) => {
            const bp = stateLook(box, hover, press);
            picture($("state-" + name), prims, bounds, 120, 72, (ctx, p) => {
                boxFrame(ctx, placed, bp);
                if (bp.glow > 0.001 && p === prims[0]) glow(ctx, prims, placed, bp.glow);
            });
        });
    }

    function primBounds(prims) {
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        prims.forEach(p => {
            if (p.kind === "rect" || p.kind === "ellipse") {
                x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x + p.w); y1 = Math.max(y1, p.y + p.h);
            }
        });
        return Number.isFinite(x0) ? {x: x0, y: y0, w: x1 - x0, h: y1 - y0} : null;
    }

    // What changes the page: each link, each rule, each key.
    function fillChanges() {
        const spec = state.spec, items = [];
        const pageName = id => (pageOf(id) || {name: "?"}).name;
        const named = text => el("b", {translate: "no", text});
        spec.pages.forEach(page => {
            const walk = box => {
                if (box.go) {
                    items.push(el("li", {}, [el("span", {}, ["Click on ", named(label(box)), " → ", named(pageName(box.go.page)),
                                                             ", ", WITH[box.go.with || spec.go_with]])]));
                }
                box.children.forEach(walk);
            };
            walk(page.root);
        });
        spec.rules.forEach(rule => {
            const box = boxOf(rule.box);
            const x = el("button.btn.ghost.icon", {type: "button", "aria-label": "Remove this rule", title: "Remove this rule",
                                                   onclick: () => Buddy.send("rule_remove", {id: rule.id})});
            x.append(Buddy.icon("close"));
            items.push(el("li", {}, [el("span", {}, ["When ", named(box ? label(box) : "?"), ` is drawn past ${plain(rule.over)} % → `,
                                                     named(pageName(rule.page)), ", ", WITH[rule.with]]), x]));
        });
        spec.page_keys.forEach(([t, page, how]) => {
            items.push(el("li", {}, [el("span", {}, [`At ${plain(t)} s → `, named(pageName(page)), ", ", WITH[how]])]));
        });
        $("changes").replaceChildren(...items);
        $("no-changes").hidden = items.length > 0;
    }

    function watchable() {
        const out = [];
        state.spec.pages.forEach(page => {
            const walk = box => {
                if (box.role === "chart" || box.role === "progress") out.push({box, page});
                box.children.forEach(walk);
            };
            walk(page.root);
        });
        return out;
    }

    function fillRules() {
        const spec = state.spec, watched = watchable();
        if ($("rules-pop").contains(document.activeElement) && document.activeElement.tagName !== "BUTTON") return;
        const select = (options, value, onChange, label_) => {
            const node = el("select.field", {"aria-label": label_, translate: "no"},
                            options.map(([v, text]) => el("option", {value: v, text})));
            node.value = value;
            node.addEventListener("change", () => onChange(node.value));
            return node;
        };
        const rows = spec.rules.map(rule => {
            const change = (key, value) => Buddy.send("rule", {id: rule.id, key, value});
            const over = el("input.field", {type: "text", inputmode: "decimal", value: plain(rule.over), "aria-label": "Percent",
                                            autocomplete: "off", spellcheck: "false"});
            over.addEventListener("change", () => change("over", over.value));
            const x = el("button.btn.ghost.icon", {type: "button", "aria-label": "Remove this rule", title: "Remove this rule",
                                                   onclick: () => Buddy.send("rule_remove", {id: rule.id})});
            x.append(Buddy.icon("close"));
            const how = el("select.field", {"aria-label": "With"}, ["fade", "slide", "match"].map(v => el("option", {value: v, text: WITH[v]})));
            how.value = rule.with;
            how.addEventListener("change", () => change("with", how.value));
            return el("div.pn-rule", {dataset: {rule: rule.id}}, [
                el("span", {text: "The next time"}),
                select(watched.map(w => [w.box.id, `${label(w.box)} (${w.page.name})`]), rule.box, v => change("box", v), "Chart or progress bar"),
                el("span", {text: "is drawn past"}), over, el("span", {text: "%, go to"}),
                select(spec.pages.map(p => [p.id, p.name]), rule.page, v => change("page", v), "Page"),
                el("span", {text: "with"}), how, x]);
        });
        $("rules").replaceChildren(...rows);
        $("no-rules").hidden = rows.length > 0;
        const most = (state.limits || {}).rules || 8;
        const why = !watched.length ? "Put a chart or a progress bar on a page to make a rule for it."
            : spec.pages.length < 2 ? "A rule needs a second page to go to."
            : spec.rules.length >= most ? `A panel holds up to ${most} rules.` : "";
        $("add-rule").disabled = !!why || !!state.adding;
        $("rule-why").textContent = why;
    }

    // The pointer stays with what it went down on, even off it (where it can be: a
    // pointer that isn't down any more can't be held).
    function capture(node, e) {
        try {
            node.setPointerCapture(e.pointerId);
        } catch (_err) {
            // let go already
        }
    }

    // ---------------------------------------------------- the pop-ups --
    // The detailed options live in small panels over the page, one at a time: opened
    // by a button, moved by the head, closed by the X or Escape.

    const panels = [];

    function panel(popId, buttonIds, onShow) {
        const pop = $(popId), buttons = buttonIds.map($), head = pop.querySelector(".dg-pop-head");
        const me = {
            pop,
            show(on) {
                if (pop.hidden === !on) return;
                if (on) panels.forEach(other => { if (other !== me) other.show(false); });
                pop.hidden = !on;
                buttons.forEach(b => b.setAttribute("aria-expanded", String(on)));
                if (on && onShow) onShow();
            },
        };
        panels.push(me);
        const close = pop.querySelector(".dg-pop-close");
        close.append(Buddy.icon("close"));
        close.addEventListener("click", () => me.show(false));
        buttons.forEach(b => b.addEventListener("click", () => me.show(pop.hidden)));
        let grip = null;
        head.addEventListener("pointerdown", e => {
            if (e.target.closest("button")) return;
            const box = pop.getBoundingClientRect();
            grip = [e.clientX - box.left, e.clientY - box.top];
            capture(head, e);
        });
        head.addEventListener("pointermove", e => {
            if (!grip) return;
            const box = pop.getBoundingClientRect();
            const left = Math.min(window.innerWidth - box.width - 4, Math.max(4, e.clientX - grip[0]));
            const top = Math.min(window.innerHeight - 40, Math.max(4, e.clientY - grip[1]));
            Object.assign(pop.style, {left: left + "px", top: top + "px", right: "auto", bottom: "auto"});
        });
        const drop = () => { grip = null; };
        head.addEventListener("pointerup", drop);
        head.addEventListener("pointercancel", drop);
        return me;
    }

    const addPanel = panel("add-pop", ["add-open"], () => {
        if (state) markShelf();
        $("add-find").focus();
    });
    const statesPop = panel("states-pop", ["edit-states"], () => state && drawStates());
    const chartPop = panel("chart-pop", ["edit-chart"]);
    const typingPop = panel("typing-pop", ["edit-typing"]);
    panel("rules-pop", ["edit-rules"], () => state && fillRules());
    panel("timing-pop", ["edit-timing"]);
    void statesPop;

    // + Add closes when anything else is clicked.
    document.addEventListener("pointerdown", e => {
        if (addPanel.pop.hidden || addPanel.pop.contains(e.target) || e.target.closest("#add-open")) return;
        addPanel.show(false);
    }, true);

    // -------------------------------------------------------- the lanes --
    // Cursor and Page; then the picked layer's lanes, one for each way it moves; then the
    // page's other layers that move of their own accord. A lane's name picks its layer.

    const ANIM_LANES = {arrive: "Arrives", type: "Types on", answer: "Hover / Pressed", draw: "Draws on"};

    function pos(f) {
        return `${f / Math.max(1, state.clock.total - 1) * 100}%`;
    }

    // A lane: its name (Buddy's word, or - own - a box's name, the person's, never
    // translated), and when `id` is given a button that picks that layer.
    function lane(name, marks, opts) {
        const o = opts || {}, said = o.own ? {translate: "no"} : {};
        const title = o.id ? null : name;
        const parts = [el("span", {text: name, ...said}), o.sub ? el("span.dg-lane-sub", {text: o.sub, translate: "no"}) : null];
        const head = o.id
            ? el("button.dg-lane-name.pn-lane-pick", {type: "button", dataset: {id: o.id}, title: "Pick this layer",
                                                      onclick: () => pick(o.id)}, parts)
            : el("span.dg-lane-name", {title, ...said}, parts);
        return el("div.dg-lane" + (o.mine ? ".pn-mine" : ""), o.mine ? {"aria-selected": "true"} : {},
                  [head, el("div.dg-lane-track", {}, marks)]);
    }

    function bands(row, cls, test) {
        const out = [];
        const on = test || (v => v > 0.5);
        let from = -1;
        for (let f = 0; f <= row.length; f++) {
            const now = f < row.length && on(row[f]);
            if (now && from < 0) from = f;
            if (!now && from >= 0) {
                out.push(el("span.pn-band." + cls, {style: `left:${pos(from)};width:calc(${pos(f)} - ${pos(from)})`}));
                from = -1;
            }
        }
        return out;
    }

    function key(f, cls, more) {
        return el("span.pn-key" + (cls ? "." + cls : ""), {style: `left:${pos(f)}`, ...(more || {})});
    }

    function boxName(id) {
        const box = boxOf(id);
        return box ? label(box) : id;
    }

    function ramp(row) {
        const n = Math.max(1, row.length - 1);
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", "pn-ramp");
        svg.setAttribute("viewBox", "0 0 100 100");
        svg.setAttribute("preserveAspectRatio", "none");
        svg.setAttribute("aria-hidden", "true");
        const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        const step = Math.max(1, Math.floor(row.length / 200));
        const top = Math.max(1e-9, ...row);
        const points = [];
        for (let f = 0; f < row.length; f += step) points.push(`${(f / n * 100).toFixed(2)},${((1 - row[f] / top) * 100).toFixed(2)}`);
        points.push(`100,${((1 - row[row.length - 1] / top) * 100).toFixed(2)}`);
        line.setAttribute("points", points.join(" "));
        svg.append(line);
        return svg;
    }

    // What one way a box moves looks like on its lane.
    function animMarks(id, which) {
        const ck = state.clock, home = (index.boxes.get(id) || {}).page;
        if (which === "arrive") {
            const out = [];
            ck.pages.forEach(change => {
                const plan = change.page === home && ck.build[home] ? ck.build[home][id] : null;
                if (plan) out.push(el("span.pn-band.arrive", {style: `left:${pos(change.at + plan[0])};width:calc(${pos(change.at + plan[0] + plan[1])} - ${pos(change.at + plan[0])})`}));
            });
            return out;
        }
        if (which === "type") {
            const row = ck.typed && ck.typed[id];
            if (!row) return [];
            const total = Math.max(...row);
            return [...bands(row, "type", v => v > 0 && v < total),
                    ...ck.events.filter(e => e.box === id && e.kind === "type").map(e => key(e.frame, "", {title: "Starts typing"}))];
        }
        if (which === "answer") {
            const rows = ck.amounts[id];
            const happened = ck.events.filter(e => e.box === id && ["over", "out", "press"].includes(e.kind))
                .map(e => key(e.frame, e.kind === "press" ? "round" : "", {title: e.kind === "press" ? "Pressed" : e.kind === "over" ? "Hovered" : "Moved off"}));
            return rows ? [...bands(rows.hover, "hover"), ...bands(rows.press, "press"), ...happened] : happened;
        }
        if (which === "draw") return ck.draw[id] ? [ramp(ck.draw[id])] : [];
        return [];
    }

    // Which of the panel's page_keys a "key" change in the clock is: the first one for
    // that page at or before it not taken by an earlier change.
    function keyIndexes() {
        const fps = state.fps, used = new Set(), out = new Map();
        state.clock.pages.forEach((change, i) => {
            if (change.why !== "key") return;
            const k = state.spec.page_keys.findIndex(([t, page], j) =>
                !used.has(j) && page === change.page && Math.round(t * fps) <= change.at);
            if (k >= 0) { used.add(k); out.set(i, k); }
        });
        return out;
    }

    // What of a box shows on its lane on this clip: ["answer", "type", "draw", "arrive"].
    function happens(id) {
        const ck = state.clock, out = [], rows = ck.amounts[id];
        if (rows && (rows.hover.some(v => v > 0.5) || rows.press.some(v => v > 0.5))) out.push("answer");
        if (ck.typed && ck.typed[id] && ck.typed[id].some(v => v > 0)) out.push("type");
        if (ck.draw[id]) out.push("draw");
        if (ownArrival(boxOf(id))) out.push("arrive");
        return out;
    }

    function drawLanes() {
        if (!state) return;
        if (hold && hold.kind === "key") return;            // the key in the hand stays where it is
        const ck = state.clock, fps = state.fps, spec = state.spec, lanes = [];
        // The cursor: a dot per key of its path, a round mark per click.
        lanes.push(lane("Cursor", [
            ...spec.path.map(([t]) => key(Math.round(t * fps), "dot")),
            ...spec.clicks.map(([down]) => key(Math.round(down * fps), "round", {title: "A click"})),
        ]));
        // The page: a band per page as it shows, a key at each change.
        const keys = keyIndexes(), marks = [];
        ck.pages.forEach((change, i) => {
            const end = i + 1 < ck.pages.length ? ck.pages[i + 1].at : ck.total;
            marks.push(el("span.pn-band" + (i % 2 ? ".alt" : ""), {translate: "no",
                          style: `left:${pos(change.at)};width:calc(${pos(end)} - ${pos(change.at)})`,
                          text: (pageOf(change.page) || {name: ""}).name}));
        });
        ck.pages.forEach((change, i) => {
            if (change.why === "start") return;
            const solid = change.why === "key" && keys.has(i);
            const title = change.why === "click" ? `A click on ${boxName(change.box)}` : change.why === "rule" ? "A rule" : "A key you set";
            marks.push(key(change.at, solid ? "solid" : "", {title, dataset: solid ? {key: String(keys.get(i))} : {}}));
        });
        lanes.push(lane("Page", marks));
        // The picked layer: a lane for each way it moves, first and lit.
        const box = picked ? boxOf(picked) : null;
        if (box) {
            animsOf(picked).filter(a => a.on && ANIM_LANES[a.id]).forEach(a => {
                lanes.push(lane(ANIM_LANES[a.id], animMarks(picked, a.id), {id: picked, mine: true, sub: label(box)}));
            });
        }
        // The page's other layers with something on this clip: a hover or a press, typing,
        // a draw-on, or an arrival of their own.
        const others = [];
        for (const b of (state.solved[state.showing] || {boxes: []}).boxes) {
            if (!b.parent || b.id === picked) continue;
            const ways = happens(b.id);
            if (ways.length) others.push([b.id, ways]);
        }
        others.slice(0, MOST_LANES).forEach(([id, ways]) => {
            lanes.push(lane(boxName(id), ways.flatMap(way => animMarks(id, way)), {id, own: true}));
        });
        if (others.length > MOST_LANES) lanes.push(el("div.pn-lane-more", {text: `+ ${others.length - MOST_LANES} more`}));
        $("lanes").replaceChildren(...lanes);
        drawTime();
    }

    function bindLanes() {
        const lanesBox = $("lanes");
        const frameAt = (e, track) => {
            const box = track.getBoundingClientRect();
            const share = Math.min(1, Math.max(0, (e.clientX - box.left) / Math.max(1, box.width)));
            return Math.round(share * (state.clock.total - 1));
        };
        lanesBox.addEventListener("pointerdown", e => {
            const track = e.target.closest(".dg-lane-track");
            if (!state || !track || e.button !== 0) return;
            if (rec) stopRecording();
            const solid = e.target.closest(".pn-key.solid");
            capture(lanesBox, e);
            if (solid && !state.adding) {
                hold = {kind: "key", track, node: solid, index: Number(solid.dataset.key), frame: frameAt(e, track)};
                solid.classList.add("held");
                return;
            }
            hold = {kind: "scrub", track};
            scrubTo(frameAt(e, track));
        });
        lanesBox.addEventListener("pointermove", e => {
            if (!hold || (hold.kind !== "scrub" && hold.kind !== "key")) return;
            const f = frameAt(e, hold.track);
            if (hold.kind === "scrub") return scrubTo(f);
            hold.frame = f;
            hold.node.style.left = pos(f);
        });
        const drop = e => {
            if (!hold || (hold.kind !== "scrub" && hold.kind !== "key")) return;
            const was = hold;
            hold = null;
            if (was.kind === "key" && e.type === "pointerup") {
                const keys = state.spec.page_keys.map(k => [...k]);
                if (keys[was.index]) {
                    keys[was.index][0] = +(was.frame / state.fps).toFixed(3);
                    Buddy.send("set", {key: "page_keys", value: keys});
                }
            }
            if (was.kind === "key") drawLanes();
        };
        lanesBox.addEventListener("pointerup", drop);
        lanesBox.addEventListener("pointercancel", drop);
    }

    function scrubTo(f) {
        playing = false;
        drawPlay();
        if (mode === "try") setMode("edit");
        view = "frame";
        frame = Math.max(0, Math.min(state.clock.total - 1, f));
        paint();
    }

    // --------------------------------------------------------- player --

    function drawPlay() {
        $("play").replaceChildren(Buddy.icon(playing ? "pause" : "play"));
        $("play").setAttribute("aria-label", playing ? "Pause" : "Play");
        $("play").title = playing ? "Pause" : "Play";
    }

    function tick(now) {
        if (!playing || !state) return;
        const last = state.clock.total - 1;
        frame = Math.max(0, Math.min(last, Math.floor((now - startedAt) * state.fps / 1000)));
        paint();
        if (frame >= last) pause();
        else requestAnimationFrame(tick);
    }

    function play(from) {
        if (!state || rec) return;
        if (mode === "try") setMode("edit");
        if (fit !== "frame") setFit("frame", true);
        view = "frame";
        frame = from >= state.clock.total - 1 ? 0 : from;
        startedAt = performance.now() - frame * 1000 / state.fps;
        playing = true;
        drawPlay();
        requestAnimationFrame(tick);
    }

    function pause() {
        if (!playing) return;
        playing = false;
        drawPlay();
        paint();
    }

    // ------------------------------------------------- trying, recording --
    // While the mouse is the cursor, a frame's worth of where it is goes on the trail
    // every 1/fps s - the past the boxes answer to - and, recording, into the samples.

    let mouse = {x: 0, y: 0, fx: 50, fy: 50, down: false};
    let trail = [];               // newest first: {x, y, down, over, pressed}
    let grabbed = null;           // the box the button went down on
    let livePage = "";
    let liveChanges = [];         // [{at (ms), page, from, with, ms}] - the pages trying it has been to
    let lastTick = 0;
    let trying = false;

    function live() {
        return mode === "try" && !playing;
    }

    function startTrying(fromPage) {
        trail = [];
        lastTick = 0;
        grabbed = null;
        livePage = fromPage || livePage || state.showing;
        liveChanges = [{at: performance.now() - 1e6, page: livePage, from: "", with: "cut", ms: 0}];
        if (trying) return;
        trying = true;
        requestAnimationFrame(tryTick);
    }

    function inside(b, x, y) {
        return b && b.x <= x && x < b.x + b.w && b.y <= y && y < b.y + b.h;
    }

    function sample() {
        const under = hotBox(mouse.x, mouse.y, livePage);
        const downNow = mouse.down && !(trail[0] && trail[0].down);
        if (downNow || !mouse.down) grabbed = downNow ? under : null;
        const pressedNow = grabbed && inside(grabbed, mouse.x, mouse.y) ? grabbed.id : "";
        trail.unshift({x: mouse.x, y: mouse.y, down: mouse.down, over: under ? under.id : "", pressed: pressedNow});
        if (downNow) {
            // A click lands on the topmost box that answers or has a link (clock.clock's
            // "clickable"): a linked box changes the page even when it doesn't answer.
            const target = clickBox(mouse.x, mouse.y, livePage);
            const box = target ? boxOf(target.id) : null;
            if (box && box.go) goLive(box.go.page, box.go.with || state.spec.go_with);
        }
    }

    function goLive(page, how) {
        const now = performance.now(), last = liveChanges[liveChanges.length - 1];
        if (page === livePage || now < last.at + last.ms) return;          // one at a time, as the clock has it
        liveChanges.push({at: now, page, from: livePage, with: how, ms: Math.round(state.spec.go_seconds * state.fps) * 1000 / state.fps});
        livePage = page;
        grabbed = null;
    }

    function tryTick(now) {
        if (!live() || !state) {
            trying = false;
            return;
        }
        const step = 1000 / state.fps;
        if (!lastTick || now - lastTick > step * 8) lastTick = now - step;       // back after a pause: one frame, not a burst
        const keep = Math.max(lookBack(state.spec.answers.hover_seconds), lookBack(state.spec.answers.press_seconds)) + 1;
        while (now - lastTick >= step) {
            lastTick += step;
            sample();
            if (trail.length > keep) trail.length = keep;
            if (rec && rec.t0 !== null) {
                const t = (lastTick - rec.t0) / 1000, most = ((state.limits || {}).seconds || [1, 60])[1];
                rec.samples.push([+t.toFixed(3), +mouse.fx.toFixed(2), +mouse.fy.toFixed(2)]);
                $("rec-time").textContent = `${t.toFixed(1)} / ${most.toFixed(0)}`;
                if (t >= most) {
                    trying = false;
                    stopRecording();
                    return;
                }
            }
        }
        paint();
        requestAnimationFrame(tryTick);
    }

    function lookBack(seconds) {
        return Math.max(1, Math.round(seconds * state.fps));
    }

    function windowShare(test, n) {
        let on = 0;
        for (let k = 0; k < n; k++) {
            const s = trail[Math.min(k, trail.length - 1)];
            if (s && test(s)) on++;
        }
        return on / n;
    }

    // The mouse's last few frames as a clock of one frame, for pose(). Words show whole.
    function livePose() {
        const now = performance.now(), fps = state.fps, ck = state.clock;
        const nH = lookBack(state.spec.answers.hover_seconds), nP = lookBack(state.spec.answers.press_seconds);
        const amounts = {};
        const ids = new Set(trail.flatMap(s => [s.over, s.pressed]).filter(Boolean));
        ids.forEach(id => {
            amounts[id] = {hover: [eased(windowShare(s => s.over === id, nH))], press: [eased(windowShare(s => s.pressed === id, nP))]};
        });
        const ago = ms => Math.floor((now - ms) / 1000 * fps);
        const changes = liveChanges.slice(-2).map((c, i, all) => ({
            at: -ago(c.at), page: c.page, from: i ? all[0].page : "", with: c.with,
            frames: Math.round(c.ms * fps / 1000), why: "click", box: ""}));
        if (changes.length === 1) changes[0].from = "";
        const draw = {};
        const since = ago(liveChanges[liveChanges.length - 1].at);
        state.solved[livePage].boxes.forEach(b => {
            if (b.role === "chart" || b.role === "progress") draw[b.id] = [liveShare(livePage, b.id, since)];
        });
        const one = {total: 1, pages: changes, amounts, build: ck.build, draw,
                     cursor: [[mouse.x, mouse.y]], down: [mouse.down ? 1 : 0], events: []};
        return pose(state, 0, one);
    }

    // How far a chart or a progress bar is drawn on `since` frames after its page arrived (clock._plan, _share).
    function liveShare(pageId, id, since) {
        const build = state.spec.build, starts = state.clock.build[pageId] || {};
        if (!build.on || !(id in starts)) return 1;
        const length = Math.max(1, Math.round(build.seconds * state.fps));
        const most = Math.max(...Object.values(starts).map(([s]) => s)) + length;
        const start = starts[id][0], frames = Math.max(2 * length, most - start);
        return frames <= 0 ? 1 : eased((since - start) / frames, ARRIVES);
    }

    function recTime() {
        return rec && rec.t0 !== null ? Math.max(0, (performance.now() - rec.t0) / 1000) : null;
    }

    function startRecording() {
        if (!state || state.adding) return;
        playing = false;
        drawPlay();
        rec = {t0: null, samples: [], clicks: [], downAt: null};
        setMode("try", state.spec.start);                  // the clip starts on its start page
        drawRecord();
    }

    function stopRecording() {
        if (!rec) return;
        const done = rec, t = recTime();
        rec = null;
        if (done.downAt !== null && t !== null) done.clicks.push([done.downAt, +t.toFixed(3)]);
        drawRecord();
        if (done.samples.length < 2) {
            Buddy.toast("Nothing was recorded.");
        } else {
            const end = done.samples[done.samples.length - 1][0];
            const least = ((state.limits || {}).seconds || [1, 60])[0];
            Buddy.send("recorded", {samples: done.samples, clicks: done.clicks, seconds: Math.max(least, end)});
            frame = 0;
        }
        if (live()) startTrying(state.showing);
    }

    function drawRecord() {
        const on = !!rec;
        $("record").textContent = on ? "Stop" : "Record the cursor";
        $("record").classList.toggle("danger", on);
        $("stage-box").classList.toggle("recording", on);
        $("rec-time").textContent = "";
        drawHint();
    }

    function drawHint() {
        const said = rec ? "Recording starts when you move onto the stage. Move and click as it should play, then press Stop."
            : mode === "try" ? "Move the mouse over the boxes and click them. Nothing is kept until you record."
            : fit === "frame" ? "Click a box to pick it. Drag the window to move it, its right edge to make it wider."
            : "Click a box to pick it. Drag the window's right edge to make it wider. Switch to In the frame to move it.";
        const parts = [el("span", {text: said})];
        if (forced && fit === "frame") parts.push(" ", el("span", {text: "The stage shows the window in the frame now, where the cursor moves."}));
        $("hint").replaceChildren(...parts);
    }

    function setMode(next, fromPage) {
        mode = next;
        pressed($("mode"), mode);
        $("stage-box").classList.toggle("try", mode === "try");
        if (mode === "try" && fit !== "frame") setFit("frame", true);
        drawHint();
        if (live()) startTrying(fromPage || state && state.showing);
        else if (!playing && state) paint();
    }

    // ------------------------------------------------------- the stage --

    function bindStage() {
        const canvas = $("stage");
        canvas.addEventListener("pointerdown", e => {
            if (!state || e.button !== 0) return;
            const p = at(e);
            capture(canvas, e);
            if (mode === "try") {
                if (playing) pause();
                mouse = {...mouse, x: p.x, y: p.y, fx: clampPc(p.fx), fy: clampPc(p.fy), down: true};
                const t = recTime();
                if (rec && t !== null) rec.downAt = +t.toFixed(3);
                return;
            }
            if (playing) pause();
            view = "edit";
            if (state.adding) return paint();
            const place_ = place(state);
            if (Math.abs(p.x - place_.w) <= HANDLE * p.px && p.y >= 0 && p.y <= place_.h) {
                const box = canvas.getBoundingClientRect();
                hold = {kind: "width", x0: p.x, start: place_.w, width: place_.w, map: null};
                hold.map = mapFor(box.width, box.height);
                return paint();
            }
            const id = hit(p.x, p.y);
            const found = index.boxes.get(id);
            if (found && found.parent) {
                pick(id);
                return;
            }
            if (fit === "frame" && p.x >= 0 && p.x <= place_.w && p.y >= 0 && p.y <= place_.h) {
                // The window's body (its bar, or its page's own box): it moves.
                hold = {kind: "move", fx: p.fx, fy: p.fy, x: state.spec.x, y: state.spec.y};
            }
            pick("");
        });
        canvas.addEventListener("pointermove", e => {
            if (!state) return;
            const p = at(e);
            if (mode === "try") {
                mouse = {...mouse, x: p.x, y: p.y, fx: clampPc(p.fx), fy: clampPc(p.fy)};
                if (rec && rec.t0 === null) {
                    rec.t0 = performance.now();
                    lastTick = rec.t0;
                    rec.samples.push([0, +mouse.fx.toFixed(2), +mouse.fy.toFixed(2)]);
                }
                return;
            }
            if (!hold) {
                const place_ = place(state);
                const edge = Math.abs(p.x - place_.w) <= HANDLE * p.px && p.y >= 0 && p.y <= place_.h;
                const id = hit(p.x, p.y), found = index.boxes.get(id);
                canvas.style.cursor = edge ? "ew-resize" : found && found.parent ? "pointer" : id && fit === "frame" ? "move" : "";
                return;
            }
            if (hold.kind === "move") {
                const spec = state.spec;
                spec.x = +(hold.x + p.fx - hold.fx).toFixed(2);
                spec.y = +(hold.y + p.fy - hold.fy).toFixed(2);
                setField("x", spec.x, 40);
                setField("y", spec.y, 40);
            } else if (hold.kind === "width") {
                const [low, high] = (state.limits || {}).width || [320, 1600];
                // The window keeps its middle: its edge follows the mouse.
                hold.width = Math.round(Math.min(high, Math.max(low, hold.start + 2 * (p.x - hold.x0))));
                state.spec.width = hold.width;
                put($("width"), hold.width);
                setField("width", hold.width, 60);
            }
            paint();
        });
        const drop = e => {
            if (mode === "try") {
                mouse = {...mouse, down: false};
                const t = recTime();
                if (rec && rec.downAt !== null && t !== null) {
                    rec.clicks.push([rec.downAt, +Math.max(t, rec.downAt + 0.02).toFixed(3)]);
                    rec.downAt = null;
                }
                return;
            }
            const was = hold;
            if (!was || (was.kind !== "move" && was.kind !== "width")) return;
            hold = null;
            if (e.type === "pointerup") {
                if (was.kind === "move" && (state.spec.x !== was.x || state.spec.y !== was.y)) {
                    setField("x", state.spec.x);
                    setField("y", state.spec.y);
                } else if (was.kind === "width" && was.width !== was.start) {
                    setField("width", was.width);
                }
            }
            paint();
        };
        canvas.addEventListener("pointerup", drop);
        canvas.addEventListener("pointercancel", drop);
        // A double-click on an image box asks for its picture.
        canvas.addEventListener("dblclick", e => {
            if (!state || mode !== "edit" || state.adding) return;
            const p = at(e), id = hit(p.x, p.y), box = boxOf(id);
            if (box && box.role === "image") Buddy.send("image", {id});
        });
    }

    function clampPc(v) {
        return Math.min(100, Math.max(0, v));
    }

    // -------------------------------------------------------- history --
    // Python keeps the steps: Undo, Redo and Reset only ask. In a text field Ctrl+Z is
    // the field's own; anywhere else it's the panel's.

    function typing(target) {
        const field = target && target.closest && target.closest("input, textarea, select, [contenteditable]");
        return !!field && !(field.tagName === "INPUT" && /^(checkbox|radio|range|color|button)$/.test(field.type));
    }

    function bindKeys() {
        document.addEventListener("keydown", e => {
            const blocked = document.querySelector(".modal-backdrop") || document.getElementById("picker");
            if (e.key === "Escape" && !blocked) {
                if (rec) stopRecording();
                panels.forEach(p => p.show(false));
                return;
            }
            if (typing(e.target) || blocked || !state) return;
            const key = e.key.toLowerCase();
            if ((e.ctrlKey || e.metaKey) && !e.altKey && (key === "z" || key === "y")) {
                e.preventDefault();
                Buddy.send(key === "y" || e.shiftKey ? "redo" : "undo");
            } else if ((key === "delete" || key === "backspace") && mode === "edit" && picked) {
                e.preventDefault();
                removePicked();
            } else if (e.key === "F2") {
                e.preventDefault();
                const chip = e.target.closest && e.target.closest(".pn-chip");
                if (chip) startRename(chip.dataset.page);
                else if (picked) renameLayer(picked);
                else startRename(state.showing);
            } else if (e.key === " " && !e.ctrlKey && !e.metaKey && !(e.target.closest && e.target.closest("button, a"))) {
                e.preventDefault();
                if (rec) stopRecording();
                playing ? pause() : play(frame);
            }
        });
    }

    // ------------------------------------------------------------ state --

    Buddy.on("state", s => {
        const moving = hold && (hold.kind === "move" || hold.kind === "width") && state ? state.spec : null;
        state = s;
        if (moving) {
            // The window in the hand stays where the hand has it until it's let go.
            if (hold.kind === "move") Object.assign(s.spec, {x: moving.x, y: moving.y});
            else s.spec.width = moving.width;
        }
        reindex();
        if (picked && (!index.boxes.has(picked) || index.boxes.get(picked).page !== s.showing)) picked = "";
        if (livePage && !s.solved[livePage]) livePage = s.showing;
        $("timeline").textContent = s.timeline || "";
        $("dot").classList.toggle("on", !!s.connected);
        $("problem").textContent = s.problem || "";
        $("replace").checked = !!s.replace;
        $("add").disabled = !!s.adding;
        $("record").disabled = !!s.adding;
        $("add-open").disabled = !!s.adding;
        $("undo").disabled = !s.can_undo || !!s.adding;
        $("redo").disabled = !s.can_redo || !!s.adding;
        $("reset").disabled = !!s.is_template || !!s.adding;
        fill(s.spec);
        drawShelf();
        drawPages();
        fillLayers();
        // A change never plays the preview by itself: it's redrawn on the frame it was on.
        frame = seen ? Math.min(frame, s.clock.total - 1) : 0;
        seen = true;
        drawLanes();
        if (!playing && !live()) paint();
    });

    Buddy.on("picked", data => {
        picked = (data && data.id) || "";
        if (state) pick(picked);
    });
    // The page has no font to choose yet: the panel's faces come in its state.
    Buddy.on("fonts", () => {});
    Buddy.on("toast", t => Buddy.toast(t.text, 3200));
    Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}),
                                        buttons: [{label: "OK", kind: "accent"}]}));

    bind();
    bindStage();
    bindLanes();
    bindLayers();
    bindKeys();
    $("undo").append(Buddy.icon("undo"));
    $("redo").append(Buddy.icon("undo"));          // turned round in graphics.css
    $("undo").addEventListener("click", () => Buddy.send("undo"));
    $("redo").addEventListener("click", () => Buddy.send("redo"));
    $("reset").addEventListener("click", () => {
        Buddy.send("reset");
        Buddy.toast("Back to the template.", 5000, {label: "Undo", onClick: () => Buddy.send("undo")});
    });
    $("refresh").append(Buddy.icon("refresh"));
    $("refresh").addEventListener("click", () => Buddy.send("refresh"));
    $("replace").addEventListener("change", e => Buddy.send("option", {key: "replace", value: e.target.checked}));
    $("add").addEventListener("click", () => {
        if (rec) stopRecording();
        Buddy.send("add");
    });
    $("mode").addEventListener("click", e => {
        const button = e.target.closest("button[data-value]");
        if (!button || button.dataset.value === mode) return;
        if (rec) stopRecording();
        setMode(button.dataset.value);
    });
    $("record").addEventListener("click", () => rec ? stopRecording() : startRecording());
    $("play").addEventListener("click", () => {
        if (rec) stopRecording();
        playing ? pause() : play(frame);
    });
    $("scrub").addEventListener("input", e => {
        if (rec) stopRecording();
        scrubTo(Number(e.target.value) || 0);
    });
    drawPlay();
    pressed($("look"), look);
    pressed($("add-mode"), addMode);
    setFit(fit, false);
    drawDock();
    setMode("edit");
    new ResizeObserver(() => { if (state && !playing && !live()) paint(); }).observe($("stage").parentElement);
    if (document.fonts && document.fonts.addEventListener) {
        document.fonts.addEventListener("loadingdone", () => { if (state && !playing && !live()) paint(); });
    }

    // For the tests: the rule, a hit test in panel px, the stage, what was last sent.
    window.panelsView = {
        pose: (st, f) => pose(st || state, f),
        carried: (pageId, boxes, id) => carried(state.solved[pageId], boxes, id),
        // One prim drawn alone on a fresh canvas of w x h panel px: what is lit at each of `spots`.
        drawn: (p, share, w, h, spots, shown) => {
            const c = document.createElement("canvas");
            c.width = w;
            c.height = h;
            const ctx = c.getContext("2d");
            drawPrim(ctx, p, share, 1, shown === null ? undefined : shown);
            return spots.map(([x, y]) => ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data[3]);
        },
        pick: id => pick(id),
        hit: (x, y, pageId) => hit(x, y, pageId),
        picture: () => $("stage"),
        state: () => state,
        info: () => ({mode, view, fit, look, picked, frame, playing, recording: !!rec, livePage, dock: dockOpen}),
        live: () => (live() ? livePose() : null),
        toPage: (x, y) => {                     // panel px to client px on the stage, for synthetic events
            const box = $("stage").getBoundingClientRect(), m = mapFor(box.width, box.height);
            return [box.left + m.ox + x * m.s, box.top + m.oy + y * m.s];
        },
    };
})();
