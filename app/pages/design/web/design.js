"use strict";

// Design's view (../page.py says what each event carries). Python keeps the design and
// its history; this draws it and edits a copy of its own, sending the whole design back
// after each change ("design", with a number - rev - so an answer older than what's on
// the canvas is known and passed over).
//
// What a layer is and how it's drawn is ../model.py and ../geometry.py - the same rules
// ../comp.py writes the Fusion comp by: outlineOf() here is geometry.outline() there, a
// blur is a Gaussian of half the number typed, a boolean's children are combined in
// order, the first the base.

(() => {
    const $ = id => document.getElementById(id);
    const el = Buddy.el;
    const TYPING_MS = 250;
    const HANDLE = 5;               // half a handle's side, screen px
    const GRAB = 9;                 // how near a handle the mouse takes it, screen px
    const SNAP = 6;                 // how near a guide a dragged layer jumps to it, screen px
    const KAPPA = 0.5522847498;
    const SHAPES = ["rect", "ellipse", "line", "polygon", "star", "path"];
    const SOLIDS = ["rect", "ellipse", "polygon", "star", "path"];
    const BOXES = ["rect", "image", "frame"];          // what can have each corner rounded its own way
    const TITLES = {rect: "Rectangle", ellipse: "Ellipse", line: "Line", polygon: "Polygon", star: "Star",
                    path: "Path", text: "Text", image: "Image", group: "Group", boolean: "Shape",
                    frame: "Auto layout", component: "Component", instance: "Instance"};
    const NUMBERS = /^(x|y|w|h|rot|radius|radii\.[0-3]|sides|inner|size|opacity|blur|fill\.angle|stroke\.width|shadow\.(dx|dy|blur|opacity)|layout\.(gap|pad|padx|pady))$/;

    let state = null;
    let doc = null;                 // the design on the canvas
    let localRev = 0;               // how many changes this view has made
    let fonts = {};
    let sel = [];                   // ids of the layers picked
    let tool = "select";
    let view = {k: 1, ox: 0, oy: 0, fit: true};
    let drag = null;                // what the mouse holds
    let pen = null;                 // a path being drawn: {points: [{x, y, ix, iy, ox, oy}], at}
    let editing = null;             // a path's points being moved: {id, pts, c, rot, picked}
    let guides = [];                // lines shown while a layer snaps: {x} or {y}
    let clip = [];                  // layers copied
    let space = false;
    let lastFill = null;            // the look of the last shape changed, for the next new one
    let gauge = null;               // what Alt measures to: another layer's bounds, or the canvas's

    // ---------------------------------------------------------- sending --

    const timers = new Map();

    function later(key, send, wait) {
        clearTimeout(timers.get(key));
        timers.delete(key);
        if (!wait) return send();
        timers.set(key, setTimeout(() => { timers.delete(key); send(); }, wait));
    }

    let pending = null;

    // The design changed here: Python is told (after `wait` ms of no more of the same).
    function commit(merge, wait) {
        flowAll();
        settleMains();
        unsent = false;
        localRev++;
        pending = () => { pending = null; Buddy.send("design", {design: doc, rev: localRev, merge: merge || ""}); };
        later("design", () => pending && pending(), wait);
        fillAll();
        paint();
    }

    function flush() {
        clearTimeout(timers.get("design"));
        timers.delete("design");
        if (pending) pending();
    }

    function ask(action, data) {
        flush();
        Buddy.send(action, {...(data || {}), rev: ++localRev});
    }

    // A number typed as a sum - "120+40", "300/2", "(1920-600)/2" - or NaN.
    function calc(text) {
        const src = String(text).replace(/,/g, ".").replace(/\s+/g, "");
        let at = 0;
        const number = () => {
            if (src[at] === "(") {
                at++;
                const inner = sum();
                if (src[at++] !== ")") return NaN;
                return inner;
            }
            if (src[at] === "-") { at++; return -number(); }
            const found = /^\d*\.?\d+/.exec(src.slice(at));
            if (!found) return NaN;
            at += found[0].length;
            return parseFloat(found[0]);
        };
        const product = () => {
            let v = number();
            while (src[at] === "*" || src[at] === "/") v = src[at++] === "*" ? v * number() : v / number();
            return v;
        };
        const sum = () => {
            let v = product();
            while (src[at] === "+" || src[at] === "-") v = src[at++] === "+" ? v + product() : v - product();
            return v;
        };
        const value = src ? sum() : NaN;
        return at === src.length && isFinite(value) ? value : NaN;
    }

    // ------------------------------------------------------- the layers --

    // A container with no box of its own (a group, a boolean, a main component): its box
    // is the one round what is in it. An auto layout frame has its own (x, y, w, h).
    const isBox = l => Array.isArray(l.children) && l.w === undefined;
    const rad = deg => deg * Math.PI / 180;

    function turn(x, y, deg) {
        const c = Math.cos(rad(deg)), s = Math.sin(rad(deg));
        return [x * c - y * s, x * s + y * c];
    }

    function newId() {
        return "n" + Math.random().toString(36).slice(2, 10);
    }

    function walk(layers, fn, owner) {
        layers.forEach((l, i) => {
            fn(l, layers, i, owner || null);
            if (l.children) walk(l.children, fn, l);
        });
    }

    function find(id) {
        let out = null;
        if (doc) walk(doc.layers, (l, list, i, owner) => { if (l.id === id) out = {layer: l, list, index: i, owner}; });
        return out;
    }

    const layer = id => (find(id) || {}).layer || null;

    // A layer's own pieces: itself, or what's in it - and a container with a box of its
    // own (a frame) as well as what's in it, so it moves, sizes and is hit with them.
    function leaves(l, out) {
        out = out || [];
        if (l.children && l.w !== undefined) out.push(l);
        if (l.children) l.children.forEach(c => leaves(c, out));
        else out.push(l);
        return out;
    }

    const picked = () => sel.map(layer).filter(Boolean);

    function pickedLeaves() {
        const out = [];
        picked().forEach(l => leaves(l).forEach(leaf => { if (!out.includes(leaf)) out.push(leaf); }));
        return out;
    }

    function face(l) {
        const known = state && state.faces[l.font + "|" + l.style];
        return known || {css: {family: l.font, weight: /bold|black|heavy/i.test(l.style) ? 700 : 400,
                               italic: /italic|oblique/i.test(l.style)}, em: 0.716, baseline: 0.375};
    }

    function fontOf(l) {
        const css = face(l).css;
        return `${css.italic ? "italic " : ""}${css.weight} ${l.size}px "${String(css.family).replace(/"/g, "")}", sans-serif`;
    }

    const measurer = document.createElement("canvas").getContext("2d");

    // A layer's box on the canvas: {x, y, w, h, rot} - its middle, sides and turn. A
    // text's is round the line it draws; a group's or a boolean's round its children.
    function frameOf(l) {
        if (isBox(l)) {
            const b = boundsOf(leaves(l));
            return {x: (b.l + b.r) / 2, y: (b.t + b.b) / 2, w: b.r - b.l, h: b.b - b.t, rot: 0};
        }
        if (l.kind !== "text") return {x: l.x, y: l.y, w: l.w, h: l.kind === "line" ? 0 : l.h, rot: l.rot};
        measurer.font = fontOf(l);
        const w = Math.max(4, measurer.measureText(l.text).width);
        const ax = l.align === "left" ? w / 2 : l.align === "right" ? -w / 2 : 0;
        const [fx, fy] = flipOf(l);
        const [dx, dy] = turn(fx * ax, fy * (face(l).baseline - 0.25) * l.size, l.rot);
        return {x: l.x + dx, y: l.y + dy, w, h: l.size, rot: l.rot};
    }

    function cornersOf(f) {
        return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => {
            const [dx, dy] = turn(sx * f.w / 2, sy * f.h / 2, f.rot);
            return [f.x + dx, f.y + dy];
        });
    }

    function boundsOf(list) {
        const pts = list.flatMap(l => cornersOf(frameOf(l)));
        const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
        return {l: Math.min(...xs), t: Math.min(...ys), r: Math.max(...xs), b: Math.max(...ys)};
    }

    // The box the handles go round: one layer's own (turned with it), or the straight
    // box round everything picked.
    function pickedFrame() {
        const all = picked();
        if (!all.length) return null;
        if (all.length === 1) return frameOf(all[0]);
        const b = boundsOf(pickedLeaves());
        return {x: (b.l + b.r) / 2, y: (b.t + b.b) / 2, w: b.r - b.l, h: b.b - b.t, rot: 0};
    }

    // ---------------------------------------------------------- drawing --

    // A box's four corner radii as drawn (geometry.radii_of): top left, top right,
    // bottom right, bottom left of its own picture - made smaller together until two on
    // one side fit the side between them.
    function radiiOf(l) {
        const raw = l.radii || [0, 1, 2, 3].map(() => l.radius || 0);
        const [tl, tr, br, bl] = raw.map(r => Math.max(0, r));
        const k = Math.min(1, ...[[l.w, tl + tr], [l.w, bl + br], [l.h, tl + bl], [l.h, tr + br]]
            .filter(([, pair]) => pair > 0).map(([side, pair]) => side / pair));
        return [tl, tr, br, bl].map(r => r * k);
    }

    // A box's outline, its corners rounded (geometry._box), as {x, y, ix, iy, ox, oy}.
    function boxPoints(a, b, radii) {
        const [tl, tr, br, bl] = radii, P = (x, y, ix, iy, ox, oy) => ({x, y, ix, iy, ox, oy});
        const out = [tl > 0 ? P(-a + tl, -b, -tl * KAPPA, 0, 0, 0) : P(-a, -b, 0, 0, 0, 0)];
        [[a, -b, tr, -1, 0, 0, 1], [a, b, br, 0, -1, -1, 0], [-a, b, bl, 1, 0, 0, -1]].forEach(([x, y, r, fx, fy, tx, ty]) => {
            if (r <= 0) return out.push(P(x, y, 0, 0, 0, 0));
            const k = r * KAPPA;
            out.push(P(x + fx * r, y + fy * r, 0, 0, -fx * k, -fy * k), P(x + tx * r, y + ty * r, -tx * k, -ty * k, 0, 0));
        });
        if (tl > 0) out.push(P(-a, -b + tl, 0, 0, 0, -tl * KAPPA));
        return out;
    }

    // Straight-sided corners rounded by r (geometry.rounded): each cut back along its
    // sides and joined by a circle's arc, as far as half the shorter side lets it.
    function roundedPoints(corners, r) {
        const P = (x, y, ix, iy, ox, oy) => ({x, y, ix, iy, ox, oy});
        if (!(r > 0)) return corners.map(([x, y]) => P(x, y, 0, 0, 0, 0));
        const n = corners.length, out = [];
        corners.forEach(([vx, vy], i) => {
            const [px, py] = corners[(i + n - 1) % n], [nx, ny] = corners[(i + 1) % n];
            const l1 = Math.hypot(px - vx, py - vy), l2 = Math.hypot(nx - vx, ny - vy);
            if (l1 < 1e-9 || l2 < 1e-9) return out.push(P(vx, vy, 0, 0, 0, 0));
            const u1x = (px - vx) / l1, u1y = (py - vy) / l1, u2x = (nx - vx) / l2, u2y = (ny - vy) / l2;
            const theta = Math.acos(Math.max(-1, Math.min(1, u1x * u2x + u1y * u2y)));
            if (theta < 1e-6 || theta > Math.PI - 1e-6) return out.push(P(vx, vy, 0, 0, 0, 0));
            const half = Math.tan(theta / 2), cut = Math.min(r / half, l1 / 2, l2 / 2);
            const k = 4 / 3 * Math.tan((Math.PI - theta) / 4) * cut * half;
            out.push(P(vx + u1x * cut, vy + u1y * cut, 0, 0, -u1x * k, -u1y * k),
                     P(vx + u2x * cut, vy + u2y * cut, -u2x * k, -u2y * k, 0, 0));
        });
        return out;
    }

    // A shape's outline about its own middle as points with handles, before it's turned
    // or flipped (geometry._local): what it's drawn from, and what Edit points starts with.
    function shapePoints(l) {
        const a = l.w / 2, b = l.h / 2, P = (x, y, ix, iy, ox, oy) => ({x, y, ix, iy, ox, oy});
        if (l.kind === "rect" || l.kind === "image" || l.layout) return boxPoints(a, b, radiiOf(l));
        if (l.kind === "ellipse") {
            const ka = a * KAPPA, kb = b * KAPPA;
            return [P(0, -b, -ka, 0, ka, 0), P(a, 0, 0, -kb, 0, kb), P(0, b, ka, 0, -ka, 0), P(-a, 0, 0, kb, 0, -kb)];
        }
        if (l.kind === "line") return [P(-a, 0, 0, 0, 0, 0), P(a, 0, 0, 0, 0, 0)];
        if (l.kind === "polygon" || l.kind === "star") {
            const steps = l.kind === "star" ? l.sides * 2 : l.sides, corners = [];
            for (let i = 0; i < steps; i++) {
                const angle = -Math.PI / 2 + 2 * Math.PI * i / steps;
                const reach = l.kind === "star" && i % 2 ? l.inner / 100 : 1;
                corners.push([a * reach * Math.cos(angle), b * reach * Math.sin(angle)]);
            }
            return roundedPoints(corners, l.radius || 0);
        }
        return l.points.map(p => P((p.x - 0.5) * l.w, (p.y - 0.5) * l.h, p.ix * l.w, p.iy * l.h, p.ox * l.w, p.oy * l.h));
    }

    // The outline of a shape about its own middle, before it's turned (geometry._local).
    function outlineOf(ctx, l) {
        ctx.beginPath();
        if (l.kind === "ellipse") return ctx.ellipse(0, 0, l.w / 2, l.h / 2, 0, 0, Math.PI * 2);
        curve(ctx, shapePoints(l), l.kind === "path" ? l.closed : l.kind !== "line");
    }

    function curve(ctx, pts, closed) {
        pts.forEach((p, i) => {
            if (!i) return ctx.moveTo(p.x, p.y);
            const q = pts[i - 1];
            ctx.bezierCurveTo(q.x + q.ox, q.y + q.oy, p.x + p.ix, p.y + p.iy, p.x, p.y);
        });
        if (closed && pts.length > 1) {
            const q = pts[pts.length - 1], p = pts[0];
            ctx.bezierCurveTo(q.x + q.ox, q.y + q.oy, p.x + p.ix, p.y + p.iy, p.x, p.y);
            ctx.closePath();
        }
    }

    // A fill's paint for a box w by h about the middle (geometry.gradient_line).
    function paintOf(ctx, fill, w, h) {
        if (fill.type !== "linear") return fill.colour;
        const a = rad(fill.angle), dx = Math.cos(a), dy = Math.sin(a);
        const half = (Math.abs(w * dx) + Math.abs(h * dy)) / 2;
        const g = ctx.createLinearGradient(-dx * half, -dy * half, dx * half, dy * half);
        fill.stops.forEach(s => g.addColorStop(Math.min(1, Math.max(0, s.at / 100)), s.colour));
        return g;
    }

    const pictures = new Map();

    function picture(l) {
        const url = state && state.images[l.path];
        if (!url) return null;
        let pic = pictures.get(url);
        if (!pic) {
            pic = new Image();
            pic.onload = paint;
            pic.src = url;
            pictures.set(url, pic);
        }
        return pic.complete && pic.naturalWidth ? pic : null;
    }

    // One layer's own picture - no shadow, opacity or blur yet.
    function body(ctx, l) {
        if (l.kind === "boolean") return booleanBody(ctx, l);
        ctx.save();
        ctx.translate(l.x, l.y);
        ctx.rotate(rad(l.rot));
        ctx.scale(...flipOf(l));
        if (l.kind === "text") {
            ctx.font = fontOf(l);
            ctx.textAlign = l.align;
            ctx.textBaseline = "alphabetic";
            ctx.fillStyle = l.colour;
            ctx.fillText(l.text, 0, face(l).baseline * l.size);
        } else if (l.kind === "image") {
            const pic = picture(l);
            outlineOf(ctx, l);
            ctx.clip();
            if (pic) {
                ctx.drawImage(pic, -l.w / 2, -l.h / 2, l.w, l.h);
            } else {
                ctx.fillStyle = "#2c2f37";          // not loaded, or not there any more
                ctx.fill();
            }
        } else {
            outlineOf(ctx, l);
            if (l.fill.type !== "none" && l.kind !== "line") {
                ctx.fillStyle = paintOf(ctx, l.fill, l.w, l.h);
                ctx.fill();
            }
            if (l.stroke.width > 0) {
                ctx.strokeStyle = l.stroke.colour;
                ctx.lineWidth = l.stroke.width;
                ctx.lineJoin = l.stroke.join;
                ctx.lineCap = l.stroke.cap;
                ctx.miterLimit = 10;
                ctx.stroke();
            }
        }
        ctx.restore();
    }

    // A boolean: its children combined in order on a canvas that holds nothing else,
    // then painted with its fill wherever they left something.
    function booleanBody(ctx, l) {
        if (l.fill.type === "none") return;
        const ops = {union: "source-over", subtract: "destination-out", intersect: "destination-in", exclude: "xor"};
        l.children.forEach((c, i) => {
            ctx.save();
            ctx.globalCompositeOperation = i ? ops[l.op] : "source-over";
            ctx.translate(c.x, c.y);
            ctx.rotate(rad(c.rot));
            ctx.scale(...flipOf(c));
            outlineOf(ctx, c);
            ctx.fillStyle = "#000";
            ctx.fill();
            ctx.restore();
        });
        const f = frameOf(l);
        ctx.save();
        ctx.globalCompositeOperation = "source-in";
        ctx.translate(f.x, f.y);
        ctx.fillStyle = paintOf(ctx, l.fill, f.w, f.h);
        ctx.fillRect(-f.w / 2 - 2, -f.h / 2 - 2, f.w + 4, f.h + 4);
        ctx.restore();
    }

    // Spare canvases the size of the stage, for what has to be drawn apart first.
    const spares = [];

    function spare(n, like) {
        while (spares.length <= n) spares.push(document.createElement("canvas"));
        const c = spares[n];
        if (c.width !== like.canvas.width || c.height !== like.canvas.height) {
            c.width = like.canvas.width;
            c.height = like.canvas.height;
        }
        const ctx = c.getContext("2d");
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = "source-over";
        ctx.filter = "none";
        ctx.clearRect(0, 0, c.width, c.height);
        ctx.setTransform(like.getTransform());
        return ctx;
    }

    // One whole canvas onto another, px for px.
    function blit(to, from, alpha, blurPx, dx, dy) {
        to.save();
        to.setTransform(1, 0, 0, 1, 0, 0);
        to.globalAlpha = alpha;
        if (blurPx > 0) to.filter = `blur(${blurPx}px)`;
        to.drawImage(from.canvas, dx || 0, dy || 0);
        to.restore();
    }

    function drawLayer(to, l, depth) {
        if (l.hidden || l.opacity <= 0) return;
        const alpha = l.opacity / 100, scale = to.getTransform().a;      // device px per design px
        if (l.kind === "instance") return drawInstance(to, l, depth);
        if (l.layout) return drawFrame(to, l, depth);
        if (l.kind === "group" || l.kind === "component") {
            if (alpha >= 1) return l.children.forEach(c => drawLayer(to, c, depth));
            const own = spare(depth * 4, to);
            l.children.forEach(c => drawLayer(own, c, depth + 1));
            return blit(to, own, alpha);
        }
        const shadow = l.shadow.on && l.shadow.opacity > 0, blur = l.blur || 0;
        if (!shadow && !blur && alpha >= 1 && l.kind !== "boolean") return body(to, l);
        const mine = spare(depth * 4 + 1, to);
        body(mine, l);
        const apart = blur > 0 || alpha < 1;
        const out = apart ? spare(depth * 4 + 2, to) : to;
        if (shadow) {
            // The shadow: the layer's own shape in the shadow's colour, moved and softened.
            const dark = spare(depth * 4 + 3, to);
            blit(dark, mine, 1);
            dark.setTransform(1, 0, 0, 1, 0, 0);
            dark.globalCompositeOperation = "source-in";
            dark.fillStyle = l.shadow.colour;
            dark.fillRect(0, 0, dark.canvas.width, dark.canvas.height);
            blit(out, dark, l.shadow.opacity / 100, l.shadow.blur / 2 * scale, l.shadow.dx * scale, l.shadow.dy * scale);
        }
        blit(out, mine, 1);
        if (apart) blit(to, out, alpha, blur / 2 * scale);
    }

    let checks = null;

    function checker(ctx) {
        if (!checks) {
            const c = document.createElement("canvas");
            c.width = c.height = 24;
            const g = c.getContext("2d");
            g.fillStyle = "#16181c";
            g.fillRect(0, 0, 24, 24);
            g.fillStyle = "#1c1f24";
            g.fillRect(0, 0, 12, 12);
            g.fillRect(12, 12, 12, 12);
            checks = ctx.createPattern(c, "repeat");
        }
        return checks;
    }

    function paint() {
        const canvas = $("stage");
        if (!doc) return;
        const box = canvas.parentElement, ratio = window.devicePixelRatio || 1;
        const cw = Math.max(80, box.clientWidth), ch = Math.max(80, box.clientHeight);
        if (canvas.width !== Math.round(cw * ratio) || canvas.height !== Math.round(ch * ratio)) {
            canvas.width = Math.round(cw * ratio);
            canvas.height = Math.round(ch * ratio);
        }
        // The canvas is shown at the stage's own size, however many pixels it holds.
        canvas.style.width = cw + "px";
        canvas.style.height = ch + "px";
        const [W, H] = doc.size;
        if (view.fit) {
            const room = rulerRoom();           // the rulers are over the stage's top and left
            view.k = Math.max(0.01, Math.min((cw - room - 40) / W, (ch - room - 40) / H));
            view.ox = room + (cw - room - W * view.k) / 2;
            view.oy = room + (ch - room - H * view.k) / 2;
        }
        const ctx = canvas.getContext("2d");
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        // The canvas itself: see-through in Resolve, so shown on dark squares here.
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.fillStyle = checker(ctx);
        ctx.fillRect(view.ox, view.oy, W * view.k, H * view.k);
        ctx.save();
        ctx.beginPath();
        ctx.rect(view.ox, view.oy, W * view.k, H * view.k);
        ctx.clip();
        ctx.setTransform(view.k * ratio, 0, 0, view.k * ratio, view.ox * ratio, view.oy * ratio);
        doc.layers.forEach(l => drawLayer(ctx, l, 0));
        ctx.restore();
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        over(ctx);
        drawDropper(ctx);
        paintRulers();
        $("zoom").textContent = Math.round(view.k * 100) + "%";
    }

    const toScreen = (x, y) => [view.ox + x * view.k, view.oy + y * view.k];

    // What's drawn over the design, in screen px: the picked layers' boxes and handles,
    // the guides, a path being drawn or its points.
    function over(ctx) {
        ctx.save();
        ctx.lineWidth = 1;
        ctx.strokeStyle = "#ff4fa3";
        guides.forEach(g => {
            ctx.beginPath();
            if (g.x !== undefined) { const [x] = toScreen(g.x, 0); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, 1e5); }
            else { const [, y] = toScreen(0, g.y); ctx.moveTo(0, y + 0.5); ctx.lineTo(1e5, y + 0.5); }
            ctx.stroke();
        });
        ctx.strokeStyle = "#4da3ff";
        ctx.fillStyle = "#ffffff";
        if (editing) {
            drawPoints(ctx, editing.pts.map(p => fromLocal(editing, p)), layer(editing.id).closed, editing.picked);
        } else {
            const all = picked();
            if (all.length > 1) all.forEach(l => ring(ctx, frameOf(l)));
            const f = pickedFrame();
            if (f) {
                ring(ctx, f);
                if (!drag || drag.type === "resize" || drag.type === "rotate") handles(f).forEach(h => {
                    const [x, y] = toScreen(h.x, h.y);
                    ctx.beginPath();
                    if (h.name === "rotate") ctx.arc(x, y, HANDLE, 0, Math.PI * 2);
                    else ctx.rect(x - HANDLE + 0.5, y - HANDLE + 0.5, HANDLE * 2 - 1, HANDLE * 2 - 1);
                    ctx.fill();
                    ctx.stroke();
                });
                if (!drag || drag.type === "radius") cornerDots().forEach(c => {
                    const [x, y] = toScreen(c.x, c.y);
                    ctx.beginPath();
                    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.stroke();
                });
                const held = drag && drag.type === "radius" && drag.shown !== undefined
                    && cornerDots().find(c => c.corner === drag.dot.corner);
                if (held) {
                    const [x, y] = toScreen(held.x, held.y);
                    tag(ctx, `${Buddy.t("Corners")} ${drag.shown}`, x, y - 18, "#2f7fe0");
                }
            }
        }
        if (!editing && !pen) {
            const f = pickedFrame();
            if (f) sizeBadge(ctx, f);
            if (f && gauge && !drag) measure(ctx, boundsOf(pickedLeaves()), gauge);
        }
        if (pen) {
            const pts = pen.points.slice();
            if (pen.at && !drag) pts.push({x: pen.at.x, y: pen.at.y, ix: 0, iy: 0, ox: 0, oy: 0});
            drawPoints(ctx, pts, false, pen.points.length - 1);
        }
        if (drag && drag.type === "marquee" && drag.now) {
            const [x0, y0] = toScreen(drag.start.x, drag.start.y), [x1, y1] = toScreen(drag.now.x, drag.now.y);
            ctx.fillStyle = "rgba(77, 163, 255, 0.12)";
            ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
            ctx.strokeRect(Math.round(x0) + 0.5, Math.round(y0) + 0.5, Math.round(x1 - x0), Math.round(y1 - y0));
        }
        ctx.restore();
    }

    function tag(ctx, text, x, y, colour) {
        ctx.save();
        ctx.font = "11px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const w = ctx.measureText(text).width + 10;
        ctx.fillStyle = colour;
        ctx.beginPath();
        ctx.roundRect(Math.round(x - w / 2), Math.round(y - 9), w, 18, 4);
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.fillText(text, x, y + 0.5);
        ctx.restore();
    }

    const whole = v => String(+v.toFixed(1));

    // How large what's picked is, under its box.
    function sizeBadge(ctx, f) {
        const pts = cornersOf(f).map(([x, y]) => toScreen(x, y));
        const low = Math.max(...pts.map(p => p[1])), mid = (Math.min(...pts.map(p => p[0])) + Math.max(...pts.map(p => p[0]))) / 2;
        tag(ctx, `${whole(f.w)} \u00d7 ${whole(f.h)}`, mid, low + 16, "#2f7fe0");
    }

    // The gaps between what's picked (a) and what Alt is over (b): to it when they're
    // apart, to its sides when one is inside or across the other.
    function measure(ctx, a, b) {
        // Apart along a side: the gap. One inside the other: how far in from each side.
        // Across each other: nothing to say that way.
        const within = (a.l >= b.l && a.r <= b.r && a.t >= b.t && a.b <= b.b) || (b.l >= a.l && b.r <= a.r && b.t >= a.t && b.b <= a.b);
        const spans = (a0, a1, b0, b1) => a1 <= b0 ? [[a1, b0]] : b1 <= a0 ? [[b1, a0]] : within ? [[b0, a0], [a1, b1]] : [];
        ctx.save();
        ctx.strokeStyle = "#ff5a4d";
        ctx.lineWidth = 1;
        const line = (x0, y0, x1, y1, text) => {
            const [sx0, sy0] = toScreen(x0, y0), [sx1, sy1] = toScreen(x1, y1);
            ctx.beginPath();
            ctx.moveTo(Math.round(sx0) + 0.5, Math.round(sy0) + 0.5);
            ctx.lineTo(Math.round(sx1) + 0.5, Math.round(sy1) + 0.5);
            ctx.stroke();
            tag(ctx, text, (sx0 + sx1) / 2, (sy0 + sy1) / 2, "#ff5a4d");
        };
        const cy = (a.t + a.b) / 2, cx = (a.l + a.r) / 2;
        spans(a.l, a.r, b.l, b.r).forEach(([x0, x1]) => { if (Math.abs(x1 - x0) >= 0.5) line(x0, cy, x1, cy, whole(Math.abs(x1 - x0))); });
        spans(a.t, a.b, b.t, b.b).forEach(([y0, y1]) => { if (Math.abs(y1 - y0) >= 0.5) line(cx, y0, cx, y1, whole(Math.abs(y1 - y0))); });
        ctx.strokeRect(...toScreen(b.l, b.t).map(v => Math.round(v) + 0.5), (b.r - b.l) * view.k, (b.b - b.t) * view.k);
        ctx.restore();
    }

    function ring(ctx, f) {
        ctx.beginPath();
        cornersOf(f).forEach(([x, y], i) => {
            const [sx, sy] = toScreen(x, y);
            ctx[i ? "lineTo" : "moveTo"](sx, sy);
        });
        ctx.closePath();
        ctx.stroke();
    }

    // A path's points and handles: design px in, drawn in screen px.
    function drawPoints(ctx, pts, closed, pickedAt) {
        const s = pts.map(p => {
            const [x, y] = toScreen(p.x, p.y);
            return {x, y, ix: p.ix * view.k, iy: p.iy * view.k, ox: p.ox * view.k, oy: p.oy * view.k};
        });
        ctx.beginPath();
        curve(ctx, s, closed);
        ctx.stroke();
        s.forEach((p, i) => {
            if (i === pickedAt) {
                [[p.ix, p.iy], [p.ox, p.oy]].forEach(([hx, hy]) => {
                    if (!hx && !hy) return;
                    ctx.beginPath();
                    ctx.moveTo(p.x, p.y);
                    ctx.lineTo(p.x + hx, p.y + hy);
                    ctx.stroke();
                    ctx.beginPath();
                    ctx.arc(p.x + hx, p.y + hy, 3.5, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.stroke();
                });
            }
            ctx.beginPath();
            ctx.rect(p.x - 3.5, p.y - 3.5, 7, 7);
            ctx.fillStyle = i === pickedAt ? "#4da3ff" : "#ffffff";
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = "#ffffff";
        });
    }

    // Where a picked box's handles are, design px: a line's two ends; a corner each -
    // and a side each for a single shape, which stretches freely; and one to turn it by.
    function handles(f) {
        const all = picked(), one = all.length === 1 ? all[0] : null;
        if (one && one.locked) return [];
        const at = (sx, sy, out) => {
            const [dx, dy] = turn(sx * f.w / 2, sy * f.h / 2 - (out || 0) / view.k, f.rot);
            return {x: f.x + dx, y: f.y + dy};
        };
        if (one && one.kind === "line") return [{name: "end0", ...at(-1, 0)}, {name: "end1", ...at(1, 0)}];
        const out = [["nw", -1, -1], ["ne", 1, -1], ["se", 1, 1], ["sw", -1, 1]].map(([name, sx, sy]) => ({name, sx, sy, ...at(sx, sy)}));
        if (one && (stretchy(one) || one.layout)) {
            [["n", 0, -1], ["e", 1, 0], ["s", 0, 1], ["w", -1, 0]].forEach(([name, sx, sy]) => out.push({name, sx, sy, ...at(sx, sy)}));
        }
        if (!(one && one.layout)) out.push({name: "rotate", ...at(0, -1, 22)});      // a frame is never turned
        return out;
    }

    // A box's round-corner dots (a rectangle's, an image's, a frame's), picked alone and
    // large enough on screen to hold them: one inside each corner, as far in as that
    // corner's radius - never nearer the corner than 12 screen px. Dragged in, the four
    // corners round together; with Alt, only the one held. corner: which of the box's
    // own radii (radiiOf) the dot is, its flips taken into account.
    const ROUND_MIN = 12;

    function cornerDots() {
        if (editing || pen || tool !== "select") return [];
        const all = picked(), l = all.length === 1 ? all[0] : null;
        if (!l || l.locked || !BOXES.includes(l.kind)) return [];
        const f = frameOf(l), a = f.w / 2, b = f.h / 2;
        if (Math.min(f.w, f.h) * view.k < 4 * ROUND_MIN) return [];
        const radii = radiiOf(l), [fx, fy] = flipOf(l);
        return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => {
            const px = sx * fx, py = sy * fy;
            const corner = py < 0 ? (px < 0 ? 0 : 1) : (px > 0 ? 2 : 3);
            const d = Math.min(a, b, Math.max(radii[corner], ROUND_MIN / view.k));
            const [dx, dy] = turn(sx * (a - d), sy * (b - d), f.rot);
            return {x: f.x + dx, y: f.y + dy, sx, sy, corner, l};
        });
    }

    function radiusHeld(p, e) {
        const d = drag, f = d.frame;
        const [qx, qy] = turn(p.x - f.x, p.y - f.y, -f.rot), [sx0, sy0] = turn(d.start.x - f.x, d.start.y - f.y, -f.rot);
        const inward = (d.dot.sx * (sx0 - qx) + d.dot.sy * (sy0 - qy)) / 2;
        const r = Math.round(Math.max(0, Math.min(f.w / 2, f.h / 2, d.was + inward)));
        writeCorner(d.dot.l, e.altKey ? `radii.${d.dot.corner}` : "radius", r);
        d.shown = r;
    }

    // ------------------------------------------------------ the pointer --

    // The cursor over a handle: a two-way arrow along the way that handle moves - up and
    // down on the top and bottom sides, across on the left and right, slanted on the
    // corners - turned with the box, to the nearest of the four arrows there are.
    function handleCursor(h, f) {
        if (h.name === "rotate") return "grab";
        if (h.sx === undefined) return "crosshair";            // a line's end goes anywhere
        const ARROWS = ["ew-resize", "nwse-resize", "ns-resize", "nesw-resize"];
        const angle = Math.atan2(h.sy, h.sx) * 180 / Math.PI + f.rot;
        return ARROWS[((Math.round(angle / 45) % 4) + 4) % 4];
    }

    function at(e) {
        const box = $("stage").getBoundingClientRect();
        const sx = e.clientX - box.left, sy = e.clientY - box.top;
        return {x: (sx - view.ox) / view.k, y: (sy - view.oy) / view.k, sx, sy};
    }

    const near = (p, q, by) => Math.hypot((p.x - q.x) * view.k, (p.y - q.y) * view.k) <= (by || GRAB);

    function inFrame(f, p, pad) {
        const [x, y] = turn(p.x - f.x, p.y - f.y, -f.rot);
        return Math.abs(x) <= f.w / 2 + pad && Math.abs(y) <= f.h / 2 + pad;
    }

    function onLeaf(l, p) {
        if (l.hidden) return false;
        if (l.kind === "instance") return onInstance(l, p);
        if (l.kind === "text" || l.kind === "image" || l.layout) return inFrame(frameOf(l), p, 0);
        measurer.setTransform(1, 0, 0, 1, 0, 0);
        measurer.translate(l.x, l.y);
        measurer.rotate(rad(l.rot));
        measurer.scale(...flipOf(l));
        outlineOf(measurer, l);
        measurer.setTransform(1, 0, 0, 1, 0, 0);
        const filled = l.kind !== "line" && (l.fill.type !== "none" || !l.stroke) && (l.kind !== "path" || l.closed);
        if (filled && measurer.isPointInPath(p.x, p.y)) return true;
        measurer.lineWidth = Math.max(l.stroke ? l.stroke.width : 0, 2 * GRAB / view.k);
        return measurer.isPointInStroke(p.x, p.y);
    }

    // The topmost layer under the mouse: a whole group or boolean, or (deep) the one
    // layer inside it.
    function topAt(p, deep) {
        for (let i = doc.layers.length - 1; i >= 0; i--) {
            const top = doc.layers[i];
            if (top.hidden || top.locked) continue;
            const all = leaves(top);
            for (let j = all.length - 1; j >= 0; j--) {
                if (!all[j].locked && onLeaf(all[j], p)) return deep ? all[j] : top;
            }
        }
        return null;
    }

    function keepLook(leaf) {
        const o = {x: leaf.x, y: leaf.y, w: leaf.w, h: leaf.h, rot: leaf.rot};
        if (leaf.kind === "text") o.size = leaf.size;
        if (leaf.radius !== undefined) o.radius = leaf.radius;
        if (leaf.radii) o.radii = leaf.radii.slice();
        return o;
    }

    function hold(type, p, more) {
        const all = pickedLeaves().filter(l => !l.locked);
        drag = {type, start: p, moved: false, leaves: all, was: all.map(keepLook), frame: pickedFrame(), ...(more || {})};
    }

    function snap(dx, dy) {
        guides = [];
        if (!drag.frame) return [dx, dy];
        const b = drag.bounds, [W, H] = doc.size, reach = SNAP / view.k;
        const xs = [0, W / 2, W], ys = [0, H / 2, H];
        doc.layers.forEach(l => {
            if (l.hidden || sel.includes(l.id) || leaves(l).some(leaf => drag.leaves.includes(leaf))) return;
            const o = boundsOf(leaves(l));
            xs.push(o.l, (o.l + o.r) / 2, o.r);
            ys.push(o.t, (o.t + o.b) / 2, o.b);
        });
        xs.push(...snapGuides("x"));
        ys.push(...snapGuides("y"));
        const best = (mine, theirs) => {
            let found = null;
            mine.forEach(m => theirs.forEach(t => {
                if (Math.abs(t - m) <= reach && (!found || Math.abs(t - m) < Math.abs(found.by))) found = {by: t - m, at: t};
            }));
            return found;
        };
        const fx = best([b.l + dx, (b.l + b.r) / 2 + dx, b.r + dx], xs);
        const fy = best([b.t + dy, (b.t + b.b) / 2 + dy, b.b + dy], ys);
        if (fx) { dx += fx.by; guides.push({x: fx.at}); }
        if (fy) { dy += fy.by; guides.push({y: fy.at}); }
        return [dx, dy];
    }

    function scaleFrom(leaf, was, ax, ay, sx, sy) {
        leaf.x = ax + (was.x - ax) * sx;
        leaf.y = ay + (was.y - ay) * sy;
        if (leaf.kind === "text") {
            leaf.size = Math.max(4, was.size * sx);
        } else {
            leaf.w = Math.max(1, was.w * sx);
            leaf.h = Math.max(1, was.h * sy);
            if (was.radius !== undefined) leaf.radius = was.radius * Math.min(sx, sy);
            if (was.radii) leaf.radii = was.radii.map(r => r * Math.min(sx, sy));
        }
    }

    function moveHeld(p, e) {
        const d = drag;
        if (d.type === "pan") {
            view.ox = d.view.ox + p.sx - d.start.sx;
            view.oy = d.view.oy + p.sy - d.start.sy;
            view.fit = false;
            return;
        }
        if (d.type === "marquee") {
            d.now = p;
            return;
        }
        if (!d.moved && Math.hypot(p.sx - d.start.sx, p.sy - d.start.sy) < 3) return;
        d.moved = true;
        if (d.type === "move") {
            let dx = p.x - d.start.x, dy = p.y - d.start.y;
            if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
            if (!e.ctrlKey && !e.metaKey) [dx, dy] = snap(dx, dy);
            else guides = [];
            d.leaves.forEach((leaf, i) => { leaf.x = d.was[i].x + dx; leaf.y = d.was[i].y + dy; });
        } else if (d.type === "rotate") {
            const f = d.frame;
            let by = (Math.atan2(p.y - f.y, p.x - f.x) - Math.atan2(d.start.y - f.y, d.start.x - f.x)) * 180 / Math.PI;
            if (e.shiftKey) by = Math.round(by / 15) * 15;
            d.leaves.forEach((leaf, i) => {
                const [x, y] = turn(d.was[i].x - f.x, d.was[i].y - f.y, by);
                leaf.x = f.x + x;
                leaf.y = f.y + y;
                leaf.rot = ((d.was[i].rot + by) % 360 + 360) % 360;
            });
        } else if (d.type === "resize") {
            resizeHeld(sideToGuide(p, e), e);
        } else if (d.type === "end") {
            // A line's end follows the mouse; its other end stays.
            const leaf = d.leaves[0], f = d.frame;
            const [ox, oy] = turn((d.name === "end0" ? 1 : -1) * f.w / 2, 0, f.rot);
            const fixed = {x: f.x + ox, y: f.y + oy};
            let angle = Math.atan2(p.y - fixed.y, p.x - fixed.x) * 180 / Math.PI;
            if (e.shiftKey) angle = Math.round(angle / 15) * 15;
            const length = Math.max(1, Math.hypot(p.x - fixed.x, p.y - fixed.y));
            const [ex, ey] = turn(length, 0, angle);
            leaf.x = fixed.x + ex / 2;
            leaf.y = fixed.y + ey / 2;
            leaf.w = length;
            leaf.rot = ((angle + (d.name === "end0" ? 180 : 0)) % 360 + 360) % 360;
        } else if (d.type === "draw") {
            drawHeld(toGuide(p, e), e);
        } else if (d.type === "pen") {
            const pt = pen.points[pen.points.length - 1];
            pt.ox = p.x - pt.x;
            pt.oy = p.y - pt.y;
            pt.ix = -pt.ox;
            pt.iy = -pt.oy;
        } else if (d.type === "point") {
            pointHeld(p, e);
        } else if (d.type === "radius") {
            radiusHeld(p, e);
        } else if (d.type === "guide") {
            guideHeld(p);
        }
    }

    function resizeHeld(p, e) {
        const d = drag, f = d.frame, h = d.handle;
        const one = d.leaves.length === 1 && picked().length === 1 && !isBox(picked()[0]) ? d.leaves[0] : null;
        const [qx, qy] = turn(p.x - f.x, p.y - f.y, -f.rot);       // the mouse, in the box's own axes
        if (one && (stretchy(one) || one.layout) && !e.shiftKey && !one.linked) {
            // One shape stretches freely: the side or corner held follows, the opposite
            // stays - or, with Alt, mirrors it, so the shape grows from its middle.
            let l = -f.w / 2, r = f.w / 2, t = -f.h / 2, b = f.h / 2;
            if (e.altKey) {
                if (h.sx) { r = Math.max(0.5, Math.abs(qx)); l = -r; }
                if (h.sy) { b = Math.max(0.5, Math.abs(qy)); t = -b; }
            } else {
                if (h.sx < 0) l = Math.min(qx, r - 1);
                if (h.sx > 0) r = Math.max(qx, l + 1);
                if (h.sy < 0) t = Math.min(qy, b - 1);
                if (h.sy > 0) b = Math.max(qy, t + 1);
            }
            const [cx, cy] = turn((l + r) / 2, (t + b) / 2, f.rot);
            Object.assign(one, {x: f.x + cx, y: f.y + cy, w: r - l, h: b - t});
            return;
        }
        // Everything else keeps its shape (one whose width and height are linked, or
        // with Shift, too): it grows from the corner or side opposite the one held - with
        // Alt, from its middle.
        const dx = h.sx * f.w, dy = h.sy * f.h, reach = Math.hypot(dx, dy) || 1;
        const from = e.altKey ? [0, 0] : [-h.sx * f.w / 2, -h.sy * f.h / 2];
        const along = ((qx - from[0]) * dx + (qy - from[1]) * dy) / reach;
        const k = Math.max(0.01, along / (e.altKey ? reach / 2 : reach));
        const [ax, ay] = turn(from[0], from[1], f.rot);
        d.leaves.forEach((leaf, i) => scaleFrom(leaf, d.was[i], f.x + ax, f.y + ay, k, k));
    }

    function drawHeld(p, e) {
        const l = drag.layer, s = drag.start;
        if (l.kind === "line") {
            let angle = Math.atan2(p.y - s.y, p.x - s.x) * 180 / Math.PI;
            if (e.shiftKey) angle = Math.round(angle / 15) * 15;
            const length = Math.max(1, Math.hypot(p.x - s.x, p.y - s.y));
            const [ex, ey] = turn(length, 0, angle);
            Object.assign(l, {x: s.x + ex / 2, y: s.y + ey / 2, w: length, rot: (angle % 360 + 360) % 360});
            return;
        }
        let w = p.x - s.x, h = p.y - s.y;
        if (e.shiftKey) {
            const side = Math.max(Math.abs(w), Math.abs(h));
            w = Math.sign(w || 1) * side;
            h = Math.sign(h || 1) * side;
        }
        Object.assign(l, {x: s.x + w / 2, y: s.y + h / 2, w: Math.max(1, Math.abs(w)), h: Math.max(1, Math.abs(h))});
    }

    function freshLook(kind) {
        const fill = lastFill ? JSON.parse(JSON.stringify(lastFill.fill)) : {type: "solid", colour: "#3c82e0", angle: 0,
            stops: [{at: 0, colour: "#3c82e0"}, {at: 100, colour: "#9a5cf0"}]};
        const stroke = lastFill ? {...lastFill.stroke} : {width: 0, colour: "#ffffff", join: "miter", cap: "butt"};
        if (kind === "line") {
            fill.type = "none";
            stroke.width = stroke.width || 4;
        }
        return {fill, stroke};
    }

    function fresh(kind, more) {
        const count = [];
        walk(doc.layers, l => { if (l.kind === kind) count.push(l); });
        return {id: newId(), kind, name: `${Buddy.t(TITLES[kind])} ${count.length + 1}`, hidden: false, locked: false, opacity: 100,
                x: 0, y: 0, w: 1, h: 1, rot: 0, blur: 0,
                shadow: {on: false, dx: 0, dy: 12, blur: 16, colour: "#000000", opacity: 50},
                ...(SHAPES.includes(kind) ? freshLook(kind) : {}), ...(SHAPES.includes(kind) && kind !== "line" ? {linked: false} : {}),
                ...(kind === "rect" ? {radius: 0} : {}), ...(kind === "polygon" ? {sides: 6, radius: 0} : {}),
                ...(kind === "star" ? {sides: 5, inner: 45, radius: 0} : {}), ...(more || {})};
    }

    function full() {
        if (overDrawn(0)) return true;
        let n = 0;
        walk(doc.layers, () => n++);
        if (n < state.max_layers) return false;
        Buddy.toast(`A design holds up to ${state.max_layers} layers.`);
        return true;
    }

    function setTool(next) {
        if (pen && next !== "pen") finishPen(false);
        if (editing && next !== "select") stopEditing();
        tool = next;
        document.querySelectorAll("#tools [data-tool]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.tool === tool)));
        $("stage").style.cursor = tool === "select" ? "" : "crosshair";
        drawHint();
        paint();
    }

    function drawHint() {
        $("hint").textContent =
            editing ? "Drag a point or its handles. Alt-click a point to make it a corner or a curve. Delete removes it. Escape is done."
            : tool === "pen" ? "Click for a corner, drag for a curve. Click the first point to close the shape, or press Enter to stop."
            : tool === "text" ? "Click where the text goes."
            : tool !== "select" ? "Drag on the canvas to draw. Shift keeps it square."
            : "";
    }

    // ---------------------------------------------------------- the pen --

    function finishPen(closed) {
        const pts = pen ? pen.points : [];
        pen = null;
        if (pts.length < 2 || full()) return paint();
        const made = fresh("path", {closed: closed && pts.length > 2});
        if (!made.closed) {
            made.fill.type = "none";
            made.stroke.width = made.stroke.width || 4;
        }
        doc.layers.push(made);
        writePoints(made, {c: {x: 0, y: 0}, rot: 0, pts});
        sel = [made.id];
        commit();
        setTool("select");
    }

    // A path's points, kept in axes of their own while they're moved: the layer's
    // middle and turn when the editing began.
    function startEditing(l) {
        const [fx, fy] = flipOf(l), w = l.w * fx, h = l.h * fy;     // a flip is put into the points
        editing = {id: l.id, c: {x: l.x, y: l.y}, rot: l.rot, picked: -1,
                   pts: l.points.map(p => ({x: (p.x - 0.5) * w, y: (p.y - 0.5) * h, ix: p.ix * w, iy: p.iy * h,
                                            ox: p.ox * w, oy: p.oy * h}))};
        drawHint();
        paint();
    }

    // A shape made a path with the same outline, so its points can be moved one by one:
    // a rectangle's, an ellipse's, a polygon's, a star's or a line's. Its flips go into
    // the points (as startEditing does); its look and name stay.
    function asPath(l) {
        if (l.kind === "path" || !SHAPES.includes(l.kind)) return;
        const [fx, fy] = flipOf(l);
        const pts = shapePoints(l).map(p => ({x: p.x * fx, y: p.y * fy, ix: p.ix * fx, iy: p.iy * fy, ox: p.ox * fx, oy: p.oy * fy}));
        const closed = l.kind !== "line";
        ["radius", "radii", "sides", "inner"].forEach(key => delete l[key]);
        if (l.linked === undefined) l.linked = false;
        l.kind = "path";
        l.closed = closed;
        writePoints(l, {c: {x: l.x, y: l.y}, rot: l.rot, pts});
    }

    // The layers Reset is for: each picked one that was born something (model._born) -
    // a frame alone, not what's in it - or, for a picked group, boolean or main, each
    // layer in it that was.
    function resettable() {
        const out = [];
        const add = l => {
            if (l.born) { if (!l.locked && !out.includes(l)) out.push(l); } else (l.children || []).forEach(add);
        };
        picked().forEach(add);
        return out;
    }

    // Each of them as it was first drawn - its kind (a rectangle made a path is a
    // rectangle again), size, turn, corners, fill, stroke, shadow and the rest - where it
    // is now: its middle stays, as do its name, its place in the layers, a text's words,
    // an image's picture, a frame's layers and the main an instance draws. One step to undo.
    function resetLayers() {
        const all = resettable();
        if (!all.length) return;
        if (editing) stopEditing();
        all.forEach(l => {
            const keep = {id: l.id, name: l.name, hidden: l.hidden, locked: l.locked, x: l.x, y: l.y, born: l.born};
            if (l.children) keep.children = l.children;
            if (l.kind === "text" && l.born.kind === "text") keep.text = l.text;
            if (l.kind === "instance") keep.of = l.of;
            Object.keys(l).forEach(key => delete l[key]);
            Object.assign(l, JSON.parse(JSON.stringify(keep.born)), keep);
        });
        commit();
    }

    function stopEditing() {
        editing = null;
        drawHint();
        paint();
    }

    function fromLocal(ed, p) {
        const [x, y] = turn(p.x, p.y, ed.rot), [ix, iy] = turn(p.ix, p.iy, ed.rot), [ox, oy] = turn(p.ox, p.oy, ed.rot);
        return {x: ed.c.x + x, y: ed.c.y + y, ix, iy, ox, oy};
    }

    function toLocal(ed, p) {
        const [x, y] = turn(p.x - ed.c.x, p.y - ed.c.y, -ed.rot);
        return {x, y};
    }

    // The layer made to fit its points again: its box round them, each point a place in it.
    function writePoints(l, ed) {
        const xs = ed.pts.map(p => p.x), ys = ed.pts.map(p => p.y);
        const left = Math.min(...xs), top = Math.min(...ys);
        const w = Math.max(1, Math.max(...xs) - left), h = Math.max(1, Math.max(...ys) - top);
        const [cx, cy] = turn(left + w / 2, top + h / 2, ed.rot);
        delete l.flipx;                 // the points hold the flip now (startEditing)
        delete l.flipy;
        Object.assign(l, {x: ed.c.x + cx, y: ed.c.y + cy, w, h, rot: ed.rot,
                          points: ed.pts.map(p => ({x: (p.x - left) / w, y: (p.y - top) / h, ix: p.ix / w, iy: p.iy / h,
                                                    ox: p.ox / w, oy: p.oy / h}))});
    }

    function pointDown(p, e) {
        const l = layer(editing.id), q = toLocal(editing, p), reach = GRAB / view.k;
        const sharp = editing.pts[editing.picked];
        if (sharp) {
            for (const side of ["i", "o"]) {
                const hx = sharp[side + "x"], hy = sharp[side + "y"];
                if ((hx || hy) && Math.hypot(q.x - sharp.x - hx, q.y - sharp.y - hy) <= reach) {
                    drag = {type: "point", start: p, moved: false, handle: side};
                    return true;
                }
            }
        }
        const i = editing.pts.findIndex(pt => Math.hypot(q.x - pt.x, q.y - pt.y) <= reach);
        if (i < 0) return false;
        editing.picked = i;
        if (e.altKey) {
            // A corner becomes a curve (handles along the line between its neighbours), a curve a corner.
            const pt = editing.pts[i], n = editing.pts.length;
            if (pt.ix || pt.iy || pt.ox || pt.oy) {
                Object.assign(pt, {ix: 0, iy: 0, ox: 0, oy: 0});
            } else {
                const a = editing.pts[(i + n - 1) % n], b = editing.pts[(i + 1) % n];
                pt.ox = (b.x - a.x) / 6;
                pt.oy = (b.y - a.y) / 6;
                pt.ix = -pt.ox;
                pt.iy = -pt.oy;
            }
            writePoints(l, editing);
            commit();
            return true;
        }
        drag = {type: "point", start: p, moved: false, handle: "", was: {...editing.pts[i]}};
        paint();
        return true;
    }

    function pointHeld(p, e) {
        const l = layer(editing.id), pt = editing.pts[editing.picked], q = toLocal(editing, p);
        if (drag.handle) {
            const side = drag.handle, other = side === "i" ? "o" : "i";
            pt[side + "x"] = q.x - pt.x;
            pt[side + "y"] = q.y - pt.y;
            if (!e.altKey) {        // the other handle stays in line with it, as long as it was
                const long = Math.hypot(pt[other + "x"], pt[other + "y"]), now = Math.hypot(pt[side + "x"], pt[side + "y"]) || 1;
                pt[other + "x"] = -pt[side + "x"] / now * long;
                pt[other + "y"] = -pt[side + "y"] / now * long;
            }
        } else {
            pt.x = q.x;
            pt.y = q.y;
        }
        writePoints(l, editing);
    }

    function removePoint() {
        const l = layer(editing.id);
        if (editing.picked < 0 || editing.pts.length <= 2) return;
        editing.pts.splice(editing.picked, 1);
        editing.picked = -1;
        if (editing.pts.length < 3) l.closed = false;
        writePoints(l, editing);
        commit();
    }

    // ------------------------------------------------------- the canvas --

    function bindStage() {
        const canvas = $("stage");
        canvas.addEventListener("pointerdown", e => {
            if (!doc) return;
            const p = at(e);
            try { canvas.setPointerCapture(e.pointerId); } catch (_gone) { /* a pointer that has already left */ }
            if (e.button === 1 || (e.button === 0 && space)) {
                drag = {type: "pan", start: p, view: {...view}};
                return;
            }
            if (e.button !== 0 || state.adding) return;
            if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
            if (rulerDown(p)) return paint();
            if (tool === "pen") return penDown(p);
            if (tool === "text") return textDown(p);
            if (tool !== "select") {
                if (full()) return;
                const from = toGuide(p, e);
                const made = fresh(tool, {x: from.x, y: from.y});
                doc.layers.push(made);
                sel = [made.id];
                drag = {type: "draw", start: from, moved: false, layer: made};
                return paint();
            }
            if (editing) {
                if (pointDown(p, e)) return;
                stopEditing();
            }
            const f = pickedFrame();
            const h = f && handles(f).find(hd => near(hd, p));
            if (h) {
                hold(h.name === "rotate" ? "rotate" : h.name.startsWith("end") ? "end" : "resize", p, {handle: h, name: h.name});
                holdFrame();
                return;
            }
            const dot = cornerDots().find(c => near(c, p, GRAB - 3));
            if (dot) {
                drag = {type: "radius", start: p, moved: false, dot, frame: frameOf(dot.l), was: radiiOf(dot.l)[dot.corner]};
                return paint();
            }
            const hit = pickAt(p, e.ctrlKey || e.metaKey);
            if (!hit) {
                if (!e.shiftKey) sel = [];
                drag = {type: "marquee", start: p, had: sel.slice()};
                fillAll();
                return paint();
            }
            if (e.shiftKey) sel = sel.includes(hit.id) ? sel.filter(id => id !== hit.id) : [...sel, hit.id];
            else if (!sel.includes(hit.id)) sel = [hit.id];
            const copied = e.altKey && picked().length > 0 && !full();
            if (copied) {
                // Alt-drag: a copy is what's dragged, the layer itself staying where it was.
                sel = picked().map(l => {
                    const where = find(l.id), twin = copyOf(l, 0);
                    where.list.splice(where.index + 1, 0, twin);
                    return twin.id;
                });
            }
            hold("move", p);
            drag.moved = copied;
            if (drag.leaves.length) drag.bounds = boundsOf(drag.leaves);
            fillAll();
            paint();
        });
        canvas.addEventListener("pointermove", e => {
            if (!doc) return;
            const p = at(e), onRulers = rulerMove(p);
            if (drag) {
                moveHeld(p, e);
                flowHeld(p);
                if (drag && drag.moved && drag.type !== "pan" && drag.type !== "marquee") fillHeld();
                return paint();
            }
            if (pen) {
                pen.at = p;
                return paint();
            }
            if (tool !== "select" || onRulers) return;
            const over = e.altKey && !editing && sel.length ? topAt(p, e.ctrlKey || e.metaKey) : null;
            const next = !e.altKey || editing || !sel.length ? null
                : over && !sel.includes(over.id) ? boundsOf(leaves(over)) : {l: 0, t: 0, r: doc.size[0], b: doc.size[1]};
            if (JSON.stringify(next) !== JSON.stringify(gauge)) {
                gauge = next;
                paint();
            }
            const f = !editing && pickedFrame();
            const h = f && handles(f).find(hd => near(hd, p));
            const dot = !h && cornerDots().some(c => near(c, p, GRAB - 3));
            canvas.style.cursor = space ? "grab" : h ? handleCursor(h, f) : dot ? "pointer" : topAt(p) ? "move" : "";
        });
        const drop = e => {
            const d = drag;
            drag = null;
            guides = [];
            if (!d || !doc) return;
            if (d.type === "marquee") {
                if (d.now) {
                    const l = Math.min(d.start.x, d.now.x), r = Math.max(d.start.x, d.now.x);
                    const t = Math.min(d.start.y, d.now.y), b = Math.max(d.start.y, d.now.y);
                    const inside = doc.layers.filter(top => {
                        if (top.hidden || top.locked) return false;
                        const o = boundsOf(leaves(top));
                        return o.l >= l && o.r <= r && o.t >= t && o.b <= b;
                    }).map(top => top.id);
                    sel = [...new Set([...d.had, ...inside])];
                }
                fillAll();
                return paint();
            }
            if (d.type === "pan") return paint();
            if (d.type === "guide") return guideDrop(d, e);
            if (d.type === "draw") {
                if (!d.moved) {
                    // A click, not a drag: a shape of a size to start from, where it was clicked.
                    Object.assign(d.layer, d.layer.kind === "line" ? {w: 240, x: d.start.x + 120} : {w: 240, h: 240});
                }
                commit();
                return setTool("select");
            }
            if (d.type === "pen") return paint();
            if (e.type === "pointerup" && d.moved) return commit();
            paint();
        };
        canvas.addEventListener("pointerup", drop);
        canvas.addEventListener("pointercancel", drop);
        canvas.addEventListener("dblclick", e => {
            if (!doc || state.adding) return;
            if (pen) return finishPen(false);
            if (guideDouble(at(e))) return;
            if (tool === "select" && enterFrame(at(e))) return;
            const hit = tool === "select" && topAt(at(e), true);
            if (hit && hit.kind === "path" && !hit.locked) {
                sel = [hit.id];
                fillAll();
                startEditing(hit);
            } else if (hit && hit.kind === "text") {
                sel = [hit.id];
                fillAll();
                $("text-value").focus();
                $("text-value").select();
            }
        });
        canvas.addEventListener("wheel", e => {
            if (!doc) return;
            e.preventDefault();
            const p = at(e);
            zoomTo(view.k * Math.pow(1.0015, -e.deltaY), p);
        }, {passive: false});
    }

    function zoomTo(k, p) {
        k = Math.min(32, Math.max(0.02, k));
        const box = $("stage").parentElement;
        p = p || {x: (box.clientWidth / 2 - view.ox) / view.k, y: (box.clientHeight / 2 - view.oy) / view.k,
                  sx: box.clientWidth / 2, sy: box.clientHeight / 2};
        view = {k, ox: p.sx - p.x * k, oy: p.sy - p.y * k, fit: false};
        paint();
    }

    function penDown(p) {
        if (!pen) {
            if (full()) return;
            pen = {points: [], at: p};
        }
        const first = pen.points[0];
        if (first && pen.points.length > 1 && near(first, p)) return finishPen(true);
        pen.points.push({x: p.x, y: p.y, ix: 0, iy: 0, ox: 0, oy: 0});
        drag = {type: "pen", start: p, moved: false};
        paint();
    }

    function textDown(p) {
        if (full()) return;
        const families = Object.keys(fonts);
        const family = lastFace ? lastFace.font : fonts.Arial ? "Arial" : families[0] || "Arial";
        const styles = fonts[family] || ["Regular"];
        const style = lastFace && styles.includes(lastFace.style) ? lastFace.style
            : styles.includes("Bold") ? "Bold" : styles.includes("Regular") ? "Regular" : styles[0];
        const made = fresh("text", {x: p.x, y: p.y, text: Buddy.t("Text"), font: family, style,
                                    size: Math.round(doc.size[1] / 11), align: "center", colour: "#ffffff"});
        doc.layers.push(made);
        sel = [made.id];
        commit();
        setTool("select");
        $("text-value").focus();
        $("text-value").select();
    }

    let lastFace = null;

    // ---- rulers --
    // Rulers along the stage's top and left, in the design's own px, and the guides
    // dragged out of them: doc.guides = {x: [the upright ones' places], y: [the level
    // ones']}, part of the design (model.py cleans them, the comp never has them).
    // They're drawn on a canvas of their own over the stage (#rulers) that lets the
    // mouse through, so at() and every sum about the stage are the same with them or
    // without - the stage's own handlers ask rulerDown() and rulerMove() first.
    // Whether the rulers show, and the guides show or are locked, is the page's setting
    // ("option"), not the design's.

    const RULER = 20;               // a ruler's breadth, screen px
    const GUIDE_GRAB = 4;           // how near a guide the mouse takes it, screen px
    const GUIDE = "#2fc4e8";
    const viewOpts = {rulers: true, guides_shown: true, guides_locked: false};
    let mouseAt = null;             // where the mouse is over the stage: at()'s {x, y, sx, sy}, or null
    let guideOver = null;           // the guide the mouse is over: {axis, i}
    let ruled = false;              // whether the stage's cursor is the rulers' own

    const rulerRoom = () => (viewOpts.rulers ? RULER : 0);

    function guideList(axis) {
        return doc && doc.guides && Array.isArray(doc.guides[axis]) ? doc.guides[axis] : [];
    }

    // The design's guides made these: each place once, in order - none, no "guides".
    function putGuides(x, y) {
        const tidy = list => [...new Set(list.filter(isFinite).map(v => Math.round(v * 1000) / 1000))].sort((a, b) => a - b);
        x = tidy(x);
        y = tidy(y);
        if (x.length || y.length) doc.guides = {x, y};
        else delete doc.guides;
    }

    function setOption(key, on) {
        viewOpts[key] = on;
        Buddy.send("option", {key, value: on});
        showRulerOptions();
        paint();
    }

    function showRulerOptions() {
        $("rulers-toggle").setAttribute("aria-pressed", String(viewOpts.rulers));
        $("ruler-corner").hidden = !viewOpts.rulers;
    }

    function rulerState(s) {
        Object.keys(viewOpts).forEach(key => { if (key in s) viewOpts[key] = !!s[key]; });
        showRulerOptions();
    }

    // What a layer moved snaps to as well (snap()): the guides, while they're shown.
    const snapGuides = axis => (viewOpts.guides_shown ? guideList(axis) : []);

    // A point being drawn, onto a guide near it each way asked for (not with Ctrl,
    // as a move) - shown with the snapping lines.
    function toGuide(p, e, ways) {
        guides = [];
        if (e.ctrlKey || e.metaKey) return p;
        const reach = SNAP / view.k, out = {...p};
        (ways === undefined ? "xy" : ways).split("").forEach(axis => {
            let best = null;
            snapGuides(axis).forEach(g => {
                if (Math.abs(g - p[axis]) <= reach && (best === null || Math.abs(g - p[axis]) < Math.abs(best - p[axis]))) best = g;
            });
            if (best !== null) {
                out[axis] = best;
                guides.push({[axis]: best});
            }
        });
        return out;
    }

    // A side or corner being dragged, onto a guide: only on a box that isn't turned,
    // and only the ways that side moves. A corner of a box that keeps its shape follows
    // the mouse along its slant, so it isn't snapped.
    function sideToGuide(p, e) {
        const d = drag, h = d.handle, all = picked();
        if (!d.frame || d.frame.rot || !h || h.sx === undefined) { guides = []; return p; }
        const free = all.length === 1 && SHAPES.includes(all[0].kind) && !e.shiftKey && !all[0].linked;
        if (h.sx && h.sy && !free) { guides = []; return p; }
        return toGuide(p, e, (h.sx ? "x" : "") + (h.sy ? "y" : ""));
    }

    // The guide under the mouse that can be taken: shown, not locked, the Select tool.
    function guideAt(p) {
        if (!viewOpts.guides_shown || viewOpts.guides_locked || tool !== "select" || editing || pen) return null;
        let found = null, by = GUIDE_GRAB + 0.5;
        ["x", "y"].forEach(axis => guideList(axis).forEach((g, i) => {
            const off = Math.abs(axis === "x" ? toScreen(g, 0)[0] - p.sx : toScreen(0, g)[1] - p.sy);
            if (off < by) { by = off; found = {axis, i}; }
        }));
        return found;
    }

    // A press on the stage, before anything else has it: a new guide from a ruler (the
    // top one makes a level guide, the left one an upright), or a guide taken to move.
    // True when it was the rulers'.
    function rulerDown(p) {
        const room = rulerRoom();
        if (room && (p.sx < room || p.sy < room)) {
            if (p.sx < room && p.sy < room) return true;          // the corner: its button's
            const axis = p.sy < room ? "y" : "x";
            if (guideList(axis).length >= state.max_guides) {
                Buddy.toast("There's no room for more guides that way.");
                return true;
            }
            if (!viewOpts.guides_shown) setOption("guides_shown", true);
            drag = {type: "guide", axis, index: -1, at: null, gone: true, start: p, moved: false};
            return true;
        }
        const f = pickedFrame();
        if (f && handles(f).some(hd => near(hd, p))) return false;    // a handle comes first
        const hit = guideAt(p);
        if (!hit) return false;
        drag = {type: "guide", axis: hit.axis, index: hit.i, at: guideList(hit.axis)[hit.i], gone: false, start: p, moved: false};
        return true;
    }

    // A guide held: on whole px, and gone when it's back on its ruler or off the stage.
    function guideHeld(p) {
        const d = drag, box = $("stage").parentElement;
        const s = d.axis === "x" ? p.sx : p.sy, end = d.axis === "x" ? box.clientWidth : box.clientHeight;
        d.gone = s < rulerRoom() || s > end;
        d.at = Math.round(d.axis === "x" ? p.x : p.y);
    }

    function guideDrop(d, e) {
        if (e.type !== "pointerup" || !d.moved || d.at === null || (d.index < 0 && d.gone)) return paint();
        const x = guideList("x").slice(), y = guideList("y").slice(), list = d.axis === "x" ? x : y;
        if (d.index >= 0) list.splice(d.index, 1);
        if (!d.gone) list.push(d.at);
        putGuides(x, y);
        commit();
    }

    // The mouse over the stage: where it is, marked on the rulers, and the cursor over
    // a ruler or a guide. True when it's over one of them (the stage's own cursor and
    // measuring then wait).
    function rulerMove(p) {
        mouseAt = p;
        const room = rulerRoom(), canvas = $("stage");
        const onRuler = !!room && (p.sx < room || p.sy < room);
        guideOver = drag || onRuler ? null : guideAt(p);
        if (!drag) {
            const want = onRuler ? (p.sy < room ? "row-resize" : "col-resize")
                : guideOver ? (guideOver.axis === "x" ? "col-resize" : "row-resize") : "";
            if (want) canvas.style.cursor = want;
            else if (ruled) canvas.style.cursor = tool === "select" ? "" : "crosshair";
            ruled = !!want;
            paintRulers();
        }
        return ruled && !drag;
    }

    function editGuide(hit) {
        const value = guideList(hit.axis)[hit.i], [sx, sy] = toScreen(value, value);
        const field = el("input.field.ds-guide-field", {type: "text", value: plain(value), spellcheck: false, autocomplete: "off",
            inputmode: "decimal", title: "Where the guide is, in px. Leave it empty to remove the guide", "aria-label": "Where the guide is"});
        Object.assign(field.style, hit.axis === "x" ? {left: Math.round(sx - 32) + "px", top: "0px"}
            : {left: "0px", top: Math.round(sy - 11) + "px"});
        let finished = false;
        const done = keep => {
            if (finished) return;
            finished = true;            // (taking the field away blurs it: done once, not again from there)
            const text = field.value.trim(), v = calc(text);
            field.remove();
            if (!keep || (text && !isFinite(v)) || state.adding) return paint();
            const x = guideList("x").slice(), y = guideList("y").slice(), list = hit.axis === "x" ? x : y;
            const i = list.indexOf(value);
            if (i >= 0) list.splice(i, 1);
            if (text) list.push(v);
            putGuides(x, y);
            commit();
        };
        field.addEventListener("keydown", e => {
            e.stopPropagation();
            if (e.key === "Enter") done(true);
            if (e.key === "Escape") done(false);
        });
        field.addEventListener("blur", () => done(true));
        $("stage-box").append(field);
        field.focus();
        field.select();
    }

    // A double-click on a guide: its place, to type.
    function guideDouble(p) {
        const hit = guideAt(p);
        if (hit) editGuide(hit);
        return !!hit;
    }

    function clearGuides() {
        if (state.adding || !doc.guides) return;
        delete doc.guides;
        commit();
    }

    function rulerMenu() {
        const b = $("ruler-corner").getBoundingClientRect(), none = !guideList("x").length && !guideList("y").length;
        Buddy.menu({x: b.left, y: b.bottom + 2, items: [
            {label: "Show guides", checked: viewOpts.guides_shown, onClick: () => setOption("guides_shown", !viewOpts.guides_shown)},
            {label: "Lock guides", checked: viewOpts.guides_locked, onClick: () => setOption("guides_locked", !viewOpts.guides_locked)},
            {label: "Clear guides", disabled: none || !!state.adding, onClick: clearGuides},
            {sep: true},
            {label: "Hide rulers", onClick: () => setOption("rulers", false)},
        ]});
    }

    // Shift+R shows or hides the rulers; Ctrl+; the guides, Ctrl+Alt+; locks them.
    // True when the key was one of these.
    function rulerKey(e, cmd) {
        if (!cmd && !e.altKey && e.shiftKey && e.code === "KeyR") { setOption("rulers", !viewOpts.rulers); return true; }
        if (cmd && !e.shiftKey && e.code === "Semicolon") {
            if (e.altKey) setOption("guides_locked", !viewOpts.guides_locked);
            else setOption("guides_shown", !viewOpts.guides_shown);
            return true;
        }
        return false;
    }

    // The overlay: the guides, then the rulers over them, at the stage's size in screen
    // px (each canvas px a device px, as the stage's own).
    function paintRulers() {
        const canvas = $("rulers");
        if (!doc) return;
        const box = canvas.parentElement, ratio = window.devicePixelRatio || 1;
        const cw = Math.max(80, box.clientWidth), ch = Math.max(80, box.clientHeight);
        if (canvas.width !== Math.round(cw * ratio) || canvas.height !== Math.round(ch * ratio)) {
            canvas.width = Math.round(cw * ratio);
            canvas.height = Math.round(ch * ratio);
        }
        canvas.style.width = cw + "px";
        canvas.style.height = ch + "px";
        const ctx = canvas.getContext("2d");
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        drawGuides(ctx, cw, ch);
        if (viewOpts.rulers) drawRulers(ctx, cw, ch);
        const d = drag && drag.type === "guide" ? drag : null;
        const tagged = d ? (d.moved && !d.gone ? {axis: d.axis, g: d.at} : null)
            : guideOver ? {axis: guideOver.axis, g: guideList(guideOver.axis)[guideOver.i]} : null;
        if (tagged && viewOpts.guides_shown) {
            const room = rulerRoom(), [sx, sy] = toScreen(tagged.g, tagged.g);
            if (tagged.axis === "x") tag(ctx, whole(tagged.g), sx, room + 14, "#1f8fb0");
            else tag(ctx, whole(tagged.g), room + 24, sy, "#1f8fb0");
        }
    }

    function drawGuides(ctx, cw, ch) {
        if (!viewOpts.guides_shown) return;
        const d = drag && drag.type === "guide" ? drag : null, room = rulerRoom();
        ctx.save();
        ["x", "y"].forEach(axis => {
            const snapped = guides.filter(g => g[axis] !== undefined).map(g => g[axis]);
            const rows = guideList(axis).map((g, i) => ({g, i})).filter(r => !(d && d.moved && d.axis === axis && d.index === r.i));
            if (d && d.moved && !d.gone && d.axis === axis) rows.push({g: d.at, i: -2});
            rows.forEach(({g, i}) => {
                const hot = i === -2 || (guideOver && guideOver.axis === axis && guideOver.i === i);
                // One a layer has just snapped to is drawn as the snapping lines are.
                ctx.strokeStyle = snapped.includes(g) ? "#ff4fa3" : GUIDE;
                ctx.globalAlpha = viewOpts.guides_locked ? 0.55 : 1;
                ctx.lineWidth = hot ? 2 : 1;
                ctx.beginPath();
                if (axis === "x") {
                    const x = Math.round(toScreen(g, 0)[0]) + 0.5;
                    ctx.moveTo(x, room);
                    ctx.lineTo(x, ch);
                } else {
                    const y = Math.round(toScreen(0, g)[1]) + 0.5;
                    ctx.moveTo(room, y);
                    ctx.lineTo(cw, y);
                }
                ctx.stroke();
            });
        });
        ctx.restore();
    }

    // The numbered ticks: 1, 2 or 5 times a power of ten design px apart, at least
    // 56 screen px at any zoom, with four or five smaller ones between.
    function rulerStep(k) {
        const want = 56 / k, ten = Math.pow(10, Math.floor(Math.log10(want)));
        const step = Math.max(1, [1, 2, 5, 10].map(m => m * ten).find(s => s >= want - 1e-9));
        return {step, minor: step / (String(step)[0] === "2" ? 4 : 5)};
    }

    function drawRulers(ctx, cw, ch) {
        const R = RULER, {step, minor} = rulerStep(view.k), per = Math.round(step / minor);
        ctx.save();
        ctx.fillStyle = "#17191d";
        ctx.fillRect(0, 0, cw, R);
        ctx.fillRect(0, 0, R, ch);
        // What's picked, along each.
        const f = pickedFrame();
        if (f) {
            const pts = cornersOf(f).map(([x, y]) => toScreen(x, y));
            const l = Math.max(R, Math.min(...pts.map(q => q[0]))), r = Math.max(...pts.map(q => q[0]));
            const t = Math.max(R, Math.min(...pts.map(q => q[1]))), b = Math.max(...pts.map(q => q[1]));
            ctx.fillStyle = "rgba(77, 163, 255, 0.3)";
            if (r > l) ctx.fillRect(l, 0, r - l, R);
            if (b > t) ctx.fillRect(0, t, R, b - t);
        }
        ctx.strokeStyle = "#4a4f59";
        ctx.fillStyle = "#8a909b";
        ctx.lineWidth = 1;
        ctx.font = "9px sans-serif";
        ctx.textBaseline = "top";
        ctx.beginPath();
        const along = (origin, from, to, mark) => {
            for (let i = Math.floor((from - origin) / view.k / minor); ; i++) {
                const s = origin + i * minor * view.k;
                if (s > to) break;
                if (s < from) continue;
                mark(Math.round(s) + 0.5, i % per === 0 ? R : i % (per / 2) === 0 ? 8 : 4, i % per === 0 ? String(Math.round(i * minor)) : "");
            }
        };
        along(view.ox, R, cw, (x, long, label) => {
            ctx.moveTo(x, R - long);
            ctx.lineTo(x, R);
            if (label) ctx.fillText(label, x + 3, 2);
        });
        along(view.oy, R, ch, (y, long, label) => {
            ctx.moveTo(R - long, y);
            ctx.lineTo(R, y);
            if (!label) return;
            ctx.save();
            ctx.translate(2, y + 3);
            ctx.rotate(-Math.PI / 2);
            ctx.textAlign = "right";
            ctx.fillText(label, 0, 0);
            ctx.restore();
        });
        ctx.stroke();
        // Their inner edges, and where the mouse is.
        ctx.strokeStyle = "#2c2f36";
        ctx.beginPath();
        ctx.moveTo(R, R - 0.5);
        ctx.lineTo(cw, R - 0.5);
        ctx.moveTo(R - 0.5, R);
        ctx.lineTo(R - 0.5, ch);
        ctx.stroke();
        if (mouseAt) {
            ctx.strokeStyle = "#e8eaee";
            ctx.beginPath();
            if (mouseAt.sx >= R && mouseAt.sx <= cw) { ctx.moveTo(Math.round(mouseAt.sx) + 0.5, 0); ctx.lineTo(Math.round(mouseAt.sx) + 0.5, R); }
            if (mouseAt.sy >= R && mouseAt.sy <= ch) { ctx.moveTo(0, Math.round(mouseAt.sy) + 0.5); ctx.lineTo(R, Math.round(mouseAt.sy) + 0.5); }
            ctx.stroke();
        }
        ctx.fillStyle = "#17191d";
        ctx.fillRect(0, 0, R, R);
        ctx.restore();
    }

    function bindRulers() {
        $("ruler-corner").addEventListener("click", rulerMenu);
        $("rulers-toggle").addEventListener("click", () => setOption("rulers", !viewOpts.rulers));
        $("stage").addEventListener("pointerleave", () => {
            if (drag) return;
            mouseAt = null;
            guideOver = null;
            paintRulers();
        });
    }

    // ------------------------------------------------- what's picked --

    function props(path) {
        // The layers a field is about: every picked layer that has it (a group's own
        // children aren't changed through it).
        // Each corner ("radii.0" to "radii.3") is any box's that has corners to round.
        const [root] = path.split(".");
        return picked().filter(l => (root === "radii" ? BOXES.includes(l.kind) : l[root] !== undefined)
                                    && !(root === "fill" && l.kind === "line"));
    }

    function read(l, path) {
        if (path.startsWith("radii.") && !l.radii) return l.radius;          // all four the same
        return path.split(".").reduce((o, k) => (o == null ? o : o[k]), l);
    }

    // One corner's radius, or all four (path "radius"): "radii" is kept only while the
    // four differ (model._corners).
    function writeCorner(l, path, value) {
        const r = Math.max(0, value);
        if (path === "radius") {
            delete l.radii;
            l.radius = r;
            return;
        }
        const radii = l.radii ? l.radii.slice() : [0, 1, 2, 3].map(() => l.radius || 0);
        radii[Number(path.split(".")[1])] = r;
        if (radii.every(v => v === radii[0])) {
            delete l.radii;
            l.radius = radii[0];
        } else {
            l.radii = radii;
        }
    }

    function write(l, path, value) {
        if (frameWrite(l, path, value)) return;
        if (path === "radius" || path.startsWith("radii.")) return writeCorner(l, path, value);
        const keys = path.split("."), last = keys.pop();
        const o = keys.reduce((obj, k) => obj[k], l);
        const was = {w: l.w, h: l.h};
        o[last] = value;
        if (l.kind === "image" && path === "w") l.h = l.w * l.px[1] / l.px[0];
        if (l.kind === "image" && path === "h") { l.w = l.h * l.px[0] / l.px[1]; }
        if (l.linked && was.w > 0 && was.h > 0) {
            if (path === "w") l.h = Math.max(1, l.w * was.h / was.w);
            if (path === "h") l.w = Math.max(1, l.h * was.w / was.h);
        }
        if (l.fill && l.stroke && SHAPES.includes(l.kind) && l.kind !== "line") lastFill = {fill: l.fill, stroke: l.stroke};
        if (l.kind === "text") lastFace = {font: l.font, style: l.style};
    }

    function setProp(path, raw, wait) {
        let value = raw;
        if (NUMBERS.test(path)) {
            value = calc(raw);
            if (!isFinite(value)) return;
        }
        const targets = props(path);
        if (!targets.length) return;
        targets.forEach(l => write(l, path, value));
        commit("prop:" + path, wait);
    }

    function plain(value) {
        return typeof value === "number" ? String(+value.toFixed(2)) : String(value == null ? "" : value);
    }

    function put(node, value) {
        // What's being typed is left alone: Python has it, tidied, when the field is left.
        if (node !== document.activeElement && node.value !== plain(value)) node.value = plain(value);
        if (node !== document.activeElement) node.dataset.put = node.value;
    }

    function show(id, on) {
        $(id).hidden = !on;
    }

    // While something is dragged on the canvas, the fields on the right follow it -
    // X, Y, Width, Height, Turn as they change - once a frame at most.
    let fillQueued = false;

    function fillHeld() {
        if (fillQueued) return;
        fillQueued = true;
        requestAnimationFrame(() => {
            fillQueued = false;
            if (doc && state) fillPicked();
        });
    }

    function fillPicked() {
        const all = picked(), one = all.length === 1 ? all[0] : null;
        const leaf = one && !isBox(one) ? one : null;
        show("canvas-group", !all.length);
        show("place-group", !!all.length);
        show("text-group", !!leaf && leaf.kind === "text");
        show("fill-group", props("fill.type").length > 0);
        show("stroke-group", props("stroke.width").length > 0);
        show("look-group", !!all.length);
        show("arrange-group", !!all.length);
        if (!all.length) return;
        $("picked-title").textContent = one ? one.name : Buddy.t("{n} layers").replace("{n}", all.length);
        show("box-fields", !!leaf);
        document.querySelectorAll("#box-fields [data-for]").forEach(node => {
            const key = node.dataset.for;
            node.hidden = !leaf || leaf[key] === undefined || (leaf.kind === "text" && (key === "w" || key === "h"))
                || (leaf.kind === "line" && key === "h");
        });
        document.querySelectorAll("[data-prop]").forEach(node => {
            const path = node.dataset.prop, targets = props(path);
            if (!targets.length) return;
            const value = read(targets[0], path);
            if (node.classList.contains("segmented")) {
                node.querySelectorAll("button[data-value]").forEach(b =>
                    b.setAttribute("aria-pressed", String(b.dataset.value === String(value))));
            } else if (node.type === "checkbox") {
                node.checked = !!value;
            } else {
                put(node, value);
            }
        });
        $("link-size").setAttribute("aria-pressed", String(!!(leaf && leaf.linked)));
        document.querySelectorAll("#align [data-spread]").forEach(b => { b.hidden = all.length < 3; });
        const fill = props("fill.type")[0];
        show("fill-colour", !!fill && fill.fill.type === "solid");
        show("edit-gradient", !!fill && fill.fill.type === "linear");
        if (!fill || fill.fill.type !== "linear") panels.gradient.show(false);
        if (!props("stroke.width").length) panels.stroke.show(false);
        const flat = all.every(l => !l.shadow);          // a group, a main, an instance: no look of their own
        show("edit-effects", !flat);
        if (flat) panels.effects.show(false);
        show("edit-points", !!leaf && SHAPES.includes(leaf.kind) && !leaf.locked);
        show("reset-layer", resettable().length > 0);
        // The Corners field: blank, "Mixed", while the four corners differ.
        const corners = document.querySelector('#box-fields [data-prop="radius"]');
        corners.placeholder = leaf && leaf.radii ? Buddy.t("Mixed") : "";
        if (leaf && leaf.radii && corners !== document.activeElement) corners.value = corners.dataset.put = "";
        show("edit-corners", !!leaf && BOXES.includes(leaf.kind));
        if (!(leaf && BOXES.includes(leaf.kind))) panels.corners.show(false);
        show("ungroup", all.some(l => l.children));
        show("group", all.length > 1 && sameOwner(all));
        const solids = all.length > 1 && all.every(l => SOLIDS.includes(l.kind) && (l.kind !== "path" || l.closed)) && sameOwner(all);
        show("combine", solids);
        if (leaf && leaf.kind === "text") fillFace(leaf);
        fillFrame();
        fillComponentButtons(all);
        fillStops();
        const busy = !!state.adding;
        document.querySelectorAll(".ds-right button, .ds-right input, .ds-right select").forEach(n => { n.disabled = busy; });
    }

    function sameOwner(all) {
        const owners = all.map(l => find(l.id).owner);
        return owners.every(o => o === owners[0]);
    }

    function fillFace(l) {
        const font = $("font"), style = $("style");
        if (font.value !== l.font) font.value = l.font;
        const styles = fonts[l.font] || [l.style];
        if (style.dataset.of !== l.font || style.options.length !== styles.length) {
            style.replaceChildren(...styles.map(s => el("option", {value: s, text: s})));
            style.dataset.of = l.font;
        }
        style.value = l.style;
    }

    function fillStops() {
        const target = props("fill.type")[0], box = $("stops");
        if (!target || target.fill.type !== "linear") return box.replaceChildren();
        const stops = target.fill.stops;
        if (box.children.length !== stops.length) {
            box.replaceChildren(...stops.map((_s, i) => el("div.ds-stop", {}, [
                swatch(el("input.dg-colour", {type: "color", "aria-label": "Colour", oninput: e => setStop(i, "colour", e.target.value)}),
                       () => ({stop: i})),
                el("input", {type: "range", min: "0", max: "100", step: "1", "aria-label": "Where along",
                             oninput: e => setStop(i, "at", Number(e.target.value))}),
                el("button.btn.ghost.icon", {type: "button", title: "Remove this colour", "aria-label": "Remove this colour",
                                             onclick: () => removeStop(i)}, [Buddy.icon("close")]),
            ])));
        }
        stops.forEach((s, i) => {
            const [, range, remove] = box.children[i].children, colour = box.children[i].querySelector("input.dg-colour");
            put(colour, s.colour);
            put(range, s.at);
            remove.disabled = stops.length <= 2;
        });
        $("add-stop").disabled = stops.length >= state.max_stops;
    }

    function setStop(i, key, value) {
        props("fill.type").forEach(l => { if (l.fill.stops[i]) l.fill.stops[i][key] = value; });
        commit("stop:" + i + key, 30);
    }

    function removeStop(i) {
        props("fill.type").forEach(l => { if (l.fill.stops.length > 2) l.fill.stops.splice(i, 1); });
        commit();
    }

    function addStop() {
        props("fill.type").forEach(l => {
            const stops = l.fill.stops;
            if (stops.length >= state.max_stops) return;
            const a = stops[stops.length - 2], b = stops[stops.length - 1];
            stops.splice(stops.length - 1, 0, {at: Math.round((a.at + b.at) / 2), colour: b.colour});
        });
        commit();
    }

    // ---------------------------------------------------- the layers list --

    function fillLayers() {
        const rows = [];
        const add = (list, depth) => {
            for (let i = list.length - 1; i >= 0; i--) {
                const l = list[i];
                rows.push(layerRow(l, depth));
                if (l.children) add(l.children, depth + 1);
            }
        };
        add(doc.layers, 0);
        if (!$("layers").querySelector("input.field")) $("layers").replaceChildren(...rows);
        show("no-layers", !doc.layers.length);
        const one = sel.length === 1 ? find(sel[0]) : null;
        $("order-up").disabled = !one || one.index >= one.list.length - 1 || !!state.adding;
        $("order-down").disabled = !one || one.index <= 0 || !!state.adding;
        if (rowDrag && rowDrag.on) rowDragShow();
    }

    function layerRow(l, depth) {
        const flag = (icon, on, title, toggle) => {
            const b = el("button.ds-layer-flag" + (on ? ".on" : ""), {type: "button", title, "aria-label": title, "aria-pressed": String(on),
                onclick: e => { e.stopPropagation(); toggle(); commit(); }}, [Buddy.icon(icon)]);
            return b;
        };
        const name = el("span.ds-layer-name", {text: l.name, translate: "no", title: l.name});
        const row = el("div.ds-layer" + (l.hidden ? ".off" : "") + (l.kind === "component" ? ".main" : l.kind === "instance" ? ".instance" : ""), {role: "option", "aria-selected": String(sel.includes(l.id)),
            onclick: e => {
                // A second click on the same row soon after: its name to type. Counted here,
                // not left to dblclick - the first click redraws the list, and a dblclick
                // whose two clicks land on different rows never comes.
                const now = Date.now(), plain = !e.shiftKey && !e.ctrlKey && !e.metaKey;
                const again = plain && (e.detail >= 2 || (lastRowClick.id === l.id && now - lastRowClick.at < DOUBLE_MS));
                lastRowClick = again ? {id: null, at: 0} : {id: l.id, at: now};
                if (editing) stopEditing();
                sel = !plain ? (sel.includes(l.id) ? sel.filter(id => id !== l.id) : [...sel, l.id]) : [l.id];
                fillAll();
                paint();
                if (again) renameRow(l.id);
            },
            ondblclick: () => renameRow(l.id)}, [
            name,
            ...(KIND_TAGS[l.kind] ? [el("span.ds-layer-kind", {text: KIND_TAGS[l.kind]})] : []),
            flag("pin", l.locked, l.locked ? "Unlock" : "Lock", () => { l.locked = !l.locked; }),
            flag("eye", !l.hidden, l.hidden ? "Show" : "Hide", () => { l.hidden = !l.hidden; }),
        ]);
        row.style.paddingLeft = 4 + depth * 14 + "px";
        row.dataset.id = l.id;                  // what a drag of the row moves (layers-drag)
        return row;
    }

    const DOUBLE_MS = 500;
    let lastRowClick = {id: null, at: 0};

    // A layer's row as it is now, its name made a field to type a new one in (Enter or
    // leaving it keeps it, Escape doesn't) - from a double-click on the row or F2.
    function renameRow(id) {
        const row = [...$("layers").querySelectorAll(".ds-layer[data-id]")].find(r => r.dataset.id === id);
        const l = layer(id), name = row && row.querySelector(".ds-layer-name");
        if (!l || !name || $("layers").querySelector("input.field")) return;
        row.scrollIntoView({block: "nearest"});
        rename(l, row, name);
    }

    function rename(l, row, name) {
        const field = el("input.field", {type: "text", value: l.name, maxLength: 60, spellcheck: false});
        let finished = false;
        const done = keep => {
            // Once: taking the field away blurs it, and that blur mustn't keep what Escape didn't.
            if (finished || !field.isConnected) return;
            finished = true;
            const text = field.value.trim();
            field.remove();
            if (keep && text && text !== l.name) {
                l.name = text;
                commit();
            } else {
                fillLayers();
            }
        };
        field.addEventListener("keydown", e => {
            e.stopPropagation();
            if (e.key === "Enter") done(true);
            if (e.key === "Escape") done(false);
        });
        field.addEventListener("blur", () => done(true));
        field.addEventListener("click", e => e.stopPropagation());
        name.replaceWith(field);
        field.focus();
        field.select();
    }

    // ---- layers-drag --------------------------------------------------------
    // A row of the layers list dragged up or down - it, or every picked layer together -
    // to a new place in the pile: between two rows (a line at the depth it goes in, which
    // the pointer's distance from the left picks where there's a choice) or into a
    // container (its row lit: what's dragged goes on top of what it holds). Pointer
    // events, not HTML drag and drop. It starts once the pointer has moved a few px, so a
    // click still picks and a double-click still renames; Escape lets go of it, near the
    // list's top or bottom it scrolls, and the whole move is one commit(). Locked and
    // hidden layers move like any other (as in Figma): locking keeps a layer still on the
    // canvas, not in the pile.

    const ROW_DRAG = 4;             // how far the pointer moves before a row is dragged, px
    const ROW_INDENT = 14;          // a row's indent for each level it's in (layerRow), px
    const ROW_EDGE = 24;            // how near the list's top or bottom it scrolls, px

    let rowDrag = null;             // a row pressed: {id, x, y, pointer, on, ids, at, cx, cy, frame}
    let rowClickGone = false;       // the click that ends a drag picks nothing

    const isSolid = l => SOLIDS.includes(l.kind) && (l.kind !== "path" || !!l.closed);

    // The rule on its own (designView.moveLayers - checked from Python): a copy of the
    // pile `layers` with the layers `ids` taken out and put, as they were stacked, in the
    // pile of `parent` (a container's id, or null for the top) at `index`, counted in
    // that pile as it was, bottom first. One whose container moves goes with it. A
    // container left with nothing goes (as model.clean drops it). null when it can't be
    // done: nothing to move, a container into itself or what it holds, or anything but a
    // solid shape into a boolean.
    function moveLayers(layers, ids, parent, index) {
        const all = JSON.parse(JSON.stringify(layers || []));
        const where = new Map();
        const visit = (list, owner) => list.forEach(l => {
            where.set(l.id, {layer: l, list, owner});
            if (l.children) visit(l.children, l);
        });
        visit(all, null);
        const within = (id, of) => {
            for (let at = where.get(id); at; at = at.owner ? where.get(at.owner.id) : null) if (at.layer.id === of) return true;
            return false;
        };
        const wanted = new Set(ids || []), moving = [];
        const take = list => list.forEach(l => {
            if (wanted.has(l.id)) moving.push(l);
            else if (l.children) take(l.children);
        });
        take(all);
        const into = parent === null || parent === undefined ? null : where.get(parent);
        if (!moving.length || !isFinite(index) || (parent != null && !(into && Array.isArray(into.layer.children)))) return null;
        if (into && moving.some(m => within(into.layer.id, m.id))) return null;
        if (into && into.layer.kind === "boolean" && !moving.every(isSolid)) return null;
        // Nor an instance into its own main (or a main it is drawn in): it would draw itself.
        if (into && window.designComponents && moving.some(m => window.designComponents.wouldLoop(into.layer, m))) return null;
        const list = into ? into.layer.children : all;
        let at = Math.max(0, Math.min(list.length, Math.round(index)));
        at -= moving.filter(m => where.get(m.id).list === list && list.indexOf(m) < at).length;
        moving.forEach(m => { const from = where.get(m.id).list; from.splice(from.indexOf(m), 1); });
        list.splice(at, 0, ...moving);
        const prune = rows => rows.filter(l => {
            if (!l.children) return true;
            l.children = prune(l.children);
            return l.children.length > 0;
        });
        return prune(all);
    }

    function depthOf(id) {
        let depth = 0;
        for (let at = find(id); at && at.owner; at = find(at.owner.id)) depth++;
        return depth;
    }

    const rowsNow = () => [...$("layers").querySelectorAll(".ds-layer[data-id]")];
    const canMove = to => !!to && !!moveLayers(doc.layers, rowDrag.ids, to.parent, to.index);

    // Where what's dragged would go with the pointer at (x, y): {parent, index, depth,
    // line (the y of the line between rows) or row (the container lit)}, or null.
    function rowDropAt(x, y) {
        const rows = rowsNow(), box = $("layers").getBoundingClientRect();
        if (!rows.length) return null;
        y = Math.max(box.top + 1, Math.min(box.bottom - 1, y));
        const i = rows.findIndex(r => r.getBoundingClientRect().bottom > y);
        let gap = rows.length;                  // the row below the gap
        if (i >= 0) {
            const r = rows[i].getBoundingClientRect(), l = layer(rows[i].dataset.id);
            const share = (y - r.top) / r.height;
            if (l && l.children && share >= 0.3) {
                const to = {parent: l.id, index: l.children.length, depth: depthOf(l.id) + 1, row: rows[i]};
                if (canMove(to)) return to;
                if (share >= 0.5) return null;  // not into it, nor just under its row (the same place)
                gap = i;
            } else {
                gap = share < 0.5 ? i : i + 1;
            }
        }
        const above = gap > 0 ? find(rows[gap - 1].dataset.id) : null;
        const below = gap < rows.length ? find(rows[gap].dataset.id) : null;
        const ways = [];
        if (!above) {
            ways.push({parent: null, index: doc.layers.length, depth: 0});
        } else if (above.layer.children && below && below.owner === above.layer) {
            // Just under a container's row: on top of what it holds.
            ways.push({parent: above.layer.id, index: above.layer.children.length, depth: depthOf(above.layer.id) + 1});
        } else {
            // Under a layer, or under the containers it's the bottom one of.
            for (let at = above; at; at = at.index === 0 && at.owner ? find(at.owner.id) : null)
                ways.push({parent: at.owner ? at.owner.id : null, index: at.index, depth: depthOf(at.layer.id)});
        }
        const fit = ways.filter(canMove);
        if (!fit.length) return null;
        const want = Math.floor((x - box.left - 4) / ROW_INDENT);
        const to = fit.reduce((best, w) => Math.abs(w.depth - want) < Math.abs(best.depth - want) ? w : best);
        const edge = gap < rows.length ? rows[gap].getBoundingClientRect().top - 1 : rows[gap - 1].getBoundingClientRect().bottom + 1;
        return {...to, line: edge};
    }

    // The drag as it now is, drawn: the line or the lit row, the dragged rows dimmed.
    function rowDragShow() {
        const d = rowDrag;
        d.at = rowDropAt(d.cx, d.cy);
        const box = $("layers").getBoundingClientRect(), line = d.line;
        const moved = new Set();
        d.ids.forEach(id => { const l = layer(id); if (l) walk([l], c => moved.add(c.id)); });
        rowsNow().forEach(r => {
            r.classList.toggle("ds-dragged", moved.has(r.dataset.id));
            r.classList.toggle("ds-drop-into", !!(d.at && d.at.row && d.at.row.dataset.id === r.dataset.id));
        });
        const shown = !!(d.at && !d.at.row && d.at.line >= box.top - 2 && d.at.line <= box.bottom + 2);
        line.hidden = !shown;
        if (shown) {
            const left = box.left + 4 + d.at.depth * ROW_INDENT;
            Object.assign(line.style, {left: left + "px", top: d.at.line - 1 + "px", width: Math.max(8, box.right - 4 - left) + "px"});
        }
        Object.assign(d.ghost.style, {left: d.cx + 12 + "px", top: d.cy + 8 + "px"});
    }

    // Near the list's top or bottom it scrolls, faster the nearer the edge (or past it).
    function rowDragScroll() {
        const d = rowDrag, list = $("layers");
        cancelAnimationFrame(d.frame);
        const box = list.getBoundingClientRect();
        const by = d.cy < box.top + ROW_EDGE ? d.cy - box.top - ROW_EDGE : d.cy > box.bottom - ROW_EDGE ? d.cy - box.bottom + ROW_EDGE : 0;
        if (!by) return;
        d.frame = requestAnimationFrame(() => {
            if (rowDrag !== d) return;
            const was = list.scrollTop;
            list.scrollTop += Math.sign(by) * Math.min(16, 2 + Math.abs(by) / 3);
            if (list.scrollTop === was) return;
            rowDragShow();
            rowDragScroll();
        });
    }

    function startRowDrag() {
        const d = rowDrag;
        if (editing) stopEditing();
        if (!sel.includes(d.id)) {
            sel = [d.id];
            fillAll();
            paint();
        }
        d.ids = sel.slice();
        d.on = true;
        try { $("layers").setPointerCapture(d.pointer); } catch (_gone) { /* a pointer that has already left */ }
        const one = d.ids.length === 1 ? layer(d.ids[0]) : null;
        d.line = el("div.ds-drop-line", {hidden: true});
        d.ghost = el("div.ds-drop-ghost", {text: one ? one.name : Buddy.t("{n} layers").replace("{n}", d.ids.length), translate: "no"});
        document.body.append(d.line, d.ghost);
        document.body.classList.add("ds-rows-dragging");
    }

    function endRowDrag(drop) {
        const d = rowDrag;
        rowDrag = null;
        if (!d || !d.on) return;
        cancelAnimationFrame(d.frame);
        d.line.remove();
        d.ghost.remove();
        document.body.classList.remove("ds-rows-dragging");
        try { $("layers").releasePointerCapture(d.pointer); } catch (_gone) { /* never held */ }
        rowClickGone = true;
        const moved = drop && d.at ? moveLayers(doc.layers, d.ids, d.at.parent, d.at.index) : null;
        if (!moved || JSON.stringify(moved) === JSON.stringify(doc.layers)) return fillLayers();
        doc.layers = moved;
        commit();
    }

    function bindRowDrag() {
        const list = $("layers");
        list.addEventListener("pointerdown", e => {
            rowClickGone = false;
            const row = e.target.closest(".ds-layer[data-id]");
            if (!row || !doc || e.button !== 0 || rowDrag || (state && state.adding) || e.target.closest("button, input")) return;
            rowDrag = {id: row.dataset.id, x: e.clientX, y: e.clientY, cx: e.clientX, cy: e.clientY, pointer: e.pointerId, on: false};
        });
        window.addEventListener("pointermove", e => {
            const d = rowDrag;
            if (!d || e.pointerId !== d.pointer) return;
            d.cx = e.clientX;
            d.cy = e.clientY;
            if (!d.on) {
                if (Math.hypot(d.cx - d.x, d.cy - d.y) < ROW_DRAG || !layer(d.id)) return;
                startRowDrag();
            }
            e.preventDefault();
            rowDragShow();
            rowDragScroll();
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
    }
    // ---- end layers-drag ----------------------------------------------------

    // --------------------------------------------------------- arranging --

    function order(by) {
        const at = sel.length === 1 ? find(sel[0]) : null;
        if (!at) return;
        const to = by === "front" ? at.list.length - 1 : by === "back" ? 0 : at.index + by;
        if (to < 0 || to >= at.list.length || to === at.index) return;
        at.list.splice(to, 0, at.list.splice(at.index, 1)[0]);
        commit();
    }

    // The picked layers out of the pile they're in, bottom first, and where the top one was.
    function lift(all) {
        const where = find(all[0].id), list = where.list;
        const taken = list.filter(l => all.includes(l));
        const top = Math.max(...taken.map(l => list.indexOf(l)));
        const index = top - taken.length + 1;
        taken.forEach(l => list.splice(list.indexOf(l), 1));
        return {list, taken, index};
    }

    function group() {
        const all = picked();
        if (all.length < 2 || !sameOwner(all) || full()) return;
        const {list, taken, index} = lift(all);
        const made = {id: newId(), kind: "group", name: Buddy.t("Group"), hidden: false, locked: false, opacity: 100, children: taken};
        list.splice(index, 0, made);
        sel = [made.id];
        commit();
    }

    function ungroup(sure) {
        const boxes = picked().filter(l => Array.isArray(l.children));     // a frame too: its layers come out
        if (!boxes.length) return;
        if (sure !== true && guardMains(boxes.filter(b => b.kind === "component"), [], () => ungroup(true), "ungroup")) return;
        const freed = [];
        boxes.forEach(b => {
            const where = find(b.id);
            if (where.owner && where.owner.kind === "boolean" && b.kind === "group") return;
            where.list.splice(where.index, 1, ...b.children);
            b.children.forEach(c => freed.push(c.id));
        });
        sel = freed;
        commit();
    }

    function combine(op) {
        const all = picked();
        if (all.length < 2 || !sameOwner(all) || full()) return;
        const {list, taken, index} = lift(all);
        const base = taken[0];
        const made = {id: newId(), kind: "boolean", op, name: Buddy.t(TITLES.boolean), hidden: false, locked: false, opacity: base.opacity,
                      fill: JSON.parse(JSON.stringify(base.fill.type === "none" ? {...base.fill, type: "solid"} : base.fill)),
                      shadow: {...base.shadow}, blur: base.blur, children: taken};
        list.splice(index, 0, made);
        sel = [made.id];
        commit();
    }

    function remove(sure) {
        const all = picked();
        if (!all.length) return;
        if (sure !== true && guardMains(mainsIn(all), all, () => remove(true), "delete")) return;
        all.forEach(l => {
            const where = find(l.id);
            if (where) where.list.splice(where.index, 1);
        });
        // A group or a boolean with nothing left in it goes too (model.clean drops it).
        sel = [];
        commit();
    }

    function copyOf(l, by) {
        const made = JSON.parse(JSON.stringify(twinOf(l)));
        walk([made], c => {
            c.id = newId();
            if (c.x !== undefined) { c.x += by; c.y += by; }
        });
        return made;
    }

    function duplicate() {
        const all = picked();
        if (!all.length || full()) return;
        const made = all.map(l => {
            const where = find(l.id), twin = copyOf(l, 24);
            where.list.splice(where.index + 1, 0, twin);
            return twin.id;
        });
        sel = made;
        commit();
    }

    function copy() {
        clip = picked().map(keptForPaste);
    }

    function paste() {
        if (!clip.length || full()) return;
        const made = clip.map(l => copyOf(l, 24));
        clip = made.map(l => JSON.parse(JSON.stringify(l)));        // the next paste steps on again
        doc.layers.push(...made);
        sel = made.map(l => l.id);
        commit();
    }

    function lineUp(how) {
        const all = picked();
        if (!all.length) return;
        // One layer lines up with the canvas; several, with the box round them all.
        const whole = all.length > 1 ? boundsOf(pickedLeaves()) : {l: 0, t: 0, r: doc.size[0], b: doc.size[1]};
        all.forEach(l => {
            const b = boundsOf(leaves(l));
            const dx = how === "left" ? whole.l - b.l : how === "right" ? whole.r - b.r
                : how === "center" ? (whole.l + whole.r - b.l - b.r) / 2 : 0;
            const dy = how === "top" ? whole.t - b.t : how === "bottom" ? whole.b - b.b
                : how === "middle" ? (whole.t + whole.b - b.t - b.b) / 2 : 0;
            leaves(l).forEach(leaf => { leaf.x += dx; leaf.y += dy; });
        });
        commit();
    }

    // Three or more layers with the same gap between each and the next, the outer two staying put.
    function spread(axis) {
        const [lo, hi] = axis === "x" ? ["l", "r"] : ["t", "b"];
        const rows = picked().map(l => ({l, b: boundsOf(leaves(l))})).sort((p, q) => p.b[lo] + p.b[hi] - q.b[lo] - q.b[hi]);
        if (rows.length < 3) return;
        const taken = rows.reduce((sum, row) => sum + row.b[hi] - row.b[lo], 0);
        const gap = (rows[rows.length - 1].b[hi] - rows[0].b[lo] - taken) / (rows.length - 1);
        let at = rows[0].b[lo];
        rows.forEach(row => {
            const by = at - row.b[lo];
            leaves(row.l).forEach(leaf => { leaf[axis] += by; });
            at += row.b[hi] - row.b[lo] + gap;
        });
        commit();
    }

    // ---- flip ------------------------------------------------------------
    // What's picked mirrored about the middle of the box round it, across (Shift+H) or up
    // and down (Shift+V): each layer's place mirrored, its turn the other way, its own
    // picture flipped - the rule in ../geometry.py's flipped(), which says why.

    function flipOf(l) {
        return [l.flipx ? -1 : 1, l.flipy ? -1 : 1];
    }

    function flipLayer(l, axis, middle) {
        if (l.kind === "instance") {
            // An instance draws its main as it is: it can be moved to its mirrored place
            // but not mirrored itself (flip the main, or detach it first).
            if (!l.locked) l[axis] = 2 * middle - l[axis];
            return;
        }
        if (l.children) {
            // An auto layout frame has a place of its own: it moves to the mirrored one,
            // its children's pictures are mirrored, and its layout puts them in their
            // order again (a row stays first-to-last, left to right).
            if (l.layout && !l.locked) {
                l[axis] = 2 * middle - l[axis];
                // A frame is never flipped itself: its corners change places instead.
                if (l.radii) l.radii = axis === "x" ? [1, 0, 3, 2].map(i => l.radii[i]) : [3, 2, 1, 0].map(i => l.radii[i]);
            }
            if (l.fill && !l.locked) {       // a boolean's gradient, drawn in its straight box
                const a = axis === "x" ? 180 - l.fill.angle : -l.fill.angle;
                l.fill.angle = +((a % 360 + 360) % 360).toFixed(3);
            }
            return l.children.forEach(c => flipLayer(c, axis, middle));
        }
        if (l.locked) return;
        l[axis] = 2 * middle - l[axis];
        l.rot = ((-l.rot % 360) + 360) % 360;
        if (l.kind === "line") return;          // the same mirrored
        const key = "flip" + axis;
        if (l[key]) delete l[key];
        else l[key] = true;
        if (l.flipx && l.flipy) {               // both ways is turned half round
            delete l.flipx;
            delete l.flipy;
            l.rot = (l.rot + 180) % 360;
        }
    }

    function flip(axis) {
        const all = picked();
        if (!all.length || pickedLeaves().every(l => l.locked)) return;
        if (editing) stopEditing();
        const b = boundsOf(pickedLeaves());
        const middle = axis === "x" ? (b.l + b.r) / 2 : (b.t + b.b) / 2;
        all.forEach(l => flipLayer(l, axis, middle));
        commit();
        if (all.some(l => leaves(l).some(c => c.kind === "instance"))) {
            Buddy.toast("An instance moves but isn't mirrored: flip its main component, or detach it first.", 4200);
        }
    }

    function zoomToPicked() {
        const all = pickedLeaves();
        if (!all.length) return;
        const b = boundsOf(all), box = $("stage").parentElement;
        const k = Math.min(32, Math.max(0.02, Math.min((box.clientWidth - 80) / Math.max(1, b.r - b.l), (box.clientHeight - 80) / Math.max(1, b.b - b.t))));
        view = {k, ox: box.clientWidth / 2 - (b.l + b.r) / 2 * k, oy: box.clientHeight / 2 - (b.t + b.b) / 2 * k, fit: false};
        paint();
    }

    function nudge(dx, dy) {
        const all = pickedLeaves().filter(l => !l.locked);
        if (!all.length) return;
        all.forEach(l => { l.x += dx; l.y += dy; });
        commit("nudge", TYPING_MS);
    }

    // ---- auto-layout --
    // An auto layout frame (a layer with a "layout": model.py) puts its children in a
    // line. The rule is written here and nowhere else - text is only measured here
    // (measureText, which matched Resolve's Text+ to the pixel), so this view works the
    // places out and sends them; Python and comp.py keep what they are sent:
    //
    //   Frames are laid out inside first, so a button's size is known before the card
    //   it is in is. A frame's children that are shown go in a line in their order -
    //   children[0] first, at the left of a "row" or the top of a "column" - each by the
    //   straight box round it as the canvas shows it (turned or not; a text's round the
    //   line it draws; a frame's its own box). From each to the next is "gap"; from the
    //   line to the frame's sides, "padx" across and "pady" down. Across the line each
    //   sits at the start (the top of a row, the left of a column), the middle or the
    //   end of the room inside the padding ("align"). Each way ("w", "h") the frame
    //   either "hug"s - is as long as the line, or as wide as its widest child, padding
    //   added - or keeps its "fixed" size. Its top left corner stays put; its x, y (its
    //   middle) follow its size. A child is moved whole, it and everything in it alike.
    //   A frame with direction "none", or with nothing shown in it, places nothing and
    //   keeps its size. A frame is never turned. Places are rounded to 0.001 px, as
    //   model.clean keeps them.

    let unsent = false;             // laid out here since Python last heard: sent with the next change

    const round3 = v => Math.round(v * 1000) / 1000;

    // A layer moved, and everything in it with it.
    function shiftLayer(l, dx, dy) {
        if (!dx && !dy) return;
        walk([l], c => {
            if (c.x === undefined) return;
            c.x = round3(c.x + dx);
            c.y = round3(c.y + dy);
        });
    }

    // The straight box a child takes up in a line: a frame's own, anything else's round
    // what it draws.
    function outerBox(l) {
        if (l.layout) return {l: l.x - l.w / 2, t: l.y - l.h / 2, r: l.x + l.w / 2, b: l.y + l.h / 2};
        return boundsOf(leaves(l));
    }

    // One frame laid out (the rule above). held: a child being dragged - its room is
    // kept, but it stays under the mouse.
    function layOut(f, held) {
        const L = f.layout;
        f.rot = 0;
        if (!L || L.direction === "none") return;
        const row = L.direction === "row";
        const kids = f.children.filter(c => !c.hidden);
        if (!kids.length) return;
        const boxes = kids.map(outerBox);
        const along = boxes.map(b => row ? b.r - b.l : b.b - b.t);
        const across = boxes.map(b => row ? b.b - b.t : b.r - b.l);
        const line = along.reduce((a, b) => a + b, 0) + L.gap * (kids.length - 1);
        const widest = Math.max(...across);
        const left = f.x - f.w / 2, top = f.y - f.h / 2;
        if (L.w === "hug") f.w = round3(Math.max(1, (row ? line : widest) + 2 * L.padx));
        if (L.h === "hug") f.h = round3(Math.max(1, (row ? widest : line) + 2 * L.pady));
        f.x = round3(left + f.w / 2);
        f.y = round3(top + f.h / 2);
        let at = row ? left + L.padx : top + L.pady;
        const start = row ? top + L.pady : left + L.padx;
        const room = row ? f.h - 2 * L.pady : f.w - 2 * L.padx;
        kids.forEach((c, i) => {
            const b = boxes[i];
            const side = L.align === "start" ? start : L.align === "end" ? start + room - across[i] : start + (room - across[i]) / 2;
            if (c !== held) shiftLayer(c, row ? at - b.l : side - b.l, row ? side - b.t : at - b.t);
            at += along[i] + L.gap;
        });
    }

    // Every frame in the design laid out, inside ones first: how far anything moved.
    function flowAll(held) {
        if (!doc) return 0;
        const was = [];
        walk(doc.layers, l => { if (l.x !== undefined) was.push([l, l.x, l.y, l.w, l.h]); });
        const visit = list => list.forEach(l => {
            if (!l.children) return;
            visit(l.children);
            if (l.layout) layOut(l, held);
        });
        visit(doc.layers);
        return Math.max(0, ...was.map(([l, x, y, w, h]) =>
            Math.max(Math.abs(l.x - x), Math.abs(l.y - y), Math.abs((l.w || 0) - (w || 0)), Math.abs((l.h || 0) - (h || 0)))));
    }

    // A frame: its background drawn as a rectangle, then its children - faded as one.
    function drawFrame(to, l, depth) {
        const alpha = l.opacity / 100;
        const own = alpha >= 1 ? to : spare(depth * 4, to), inner = alpha >= 1 ? depth : depth + 1;
        drawLayer(own, {...l, kind: "rect", opacity: 100, blur: 0, layout: undefined, children: undefined}, inner);
        l.children.forEach(c => drawLayer(own, c, inner));
        if (alpha < 1) blit(to, own, alpha);
    }

    const flows = l => !!(l && l.layout && l.layout.direction !== "none");

    // The one of a container's children that holds `leaf` (or is it).
    function childHolding(box, leaf) {
        return (box.children || []).find(c => c === leaf || leaves(c).includes(leaf)) || null;
    }

    // What a click picks: as topAt - but once something in a frame is picked, what's
    // clicked in that frame is picked at the same depth.
    function pickAt(p, deep) {
        const top = topAt(p, deep);
        if (deep || !top) return top;
        const leaf = topAt(p, true);
        for (const id of sel) {
            const where = find(id);
            const kid = where && where.owner && where.owner.layout ? childHolding(where.owner, leaf) : null;
            if (kid) return kid;
        }
        return top;
    }

    // A double-click on a picked frame picks what's under the mouse in it.
    function enterFrame(p) {
        const all = picked();
        if (all.length !== 1 || !all[0].layout) return false;
        const kid = childHolding(all[0], topAt(p, true));
        if (!kid || kid.locked) return false;
        sel = [kid.id];
        fillAll();
        paint();
        return true;
    }

    // A frame's side or corner held: the frame alone is sized (its children are laid
    // out in it again), and that way it is fixed from then on.
    function holdFrame() {
        const all = picked();
        if (drag.type !== "resize" || all.length !== 1 || !all[0].layout || all[0].locked) return;
        drag.leaves = [all[0]];
        drag.was = [keepLook(all[0])];
        drag.frameAlone = true;
    }

    // The topmost frame that lays its children out under p, the deepest of those -
    // leaving out `l` and what's in it.
    function frameUnder(p, l) {
        let found = null;
        const visit = list => list.forEach(c => {
            if (c === l || c.hidden) return;
            if (flows(c) && !c.locked && inFrame(frameOf(c), p, 0)) found = c;
            if (c.children && c.kind !== "boolean") visit(c.children);
        });
        visit(doc.layers);
        return found;
    }

    // Where a layer taken out of a frame goes: just above the outermost frame round it
    // that lays things out, in whatever holds that (a group, or the design).
    function freeSpot(frame) {
        let where = find(frame.id);
        while (where.owner && flows(where.owner)) where = find(where.owner.id);
        return {list: where.list, index: where.index + 1};
    }

    // While something is dragged: a single layer dragged over a frame goes into it,
    // among its children by where the mouse is (as in Figma); dragged out of the frame
    // it was in, it comes out. The frames are laid out round it as it goes.
    function flowHeld(p) {
        const d = drag;
        if (!d || !d.moved) return;
        if (d.type === "resize" && d.frameAlone) {
            const L = d.leaves[0].layout;
            if (d.handle.sx) L.w = "fixed";
            if (d.handle.sy) L.h = "fixed";
        }
        let held = null;
        const l = d.type === "move" && sel.length === 1 ? layer(sel[0]) : null, where = l && find(l.id);
        if (l && !l.locked && !(where.owner && where.owner.kind === "boolean")) {
            held = l;
            const into = frameUnder(p, l);
            if (into) {
                const row = into.layout.direction === "row";
                const others = into.children.filter(c => c !== l);
                let i = 0;
                others.forEach((c, j) => {
                    if (c.hidden) return;
                    const b = outerBox(c);
                    if ((row ? p.x : p.y) > (row ? b.l + b.r : b.t + b.b) / 2) i = j + 1;
                });
                if (where.owner !== into || into.children.indexOf(l) !== i) {
                    where.list.splice(where.index, 1);
                    into.children.splice(i, 0, l);
                }
            } else if (flows(where.owner)) {
                where.list.splice(where.index, 1);
                const spot = freeSpot(where.owner);
                spot.list.splice(spot.index, 0, l);
            }
        }
        flowAll(held);
    }

    // A frame's own fields: x and y move what's in it too; a width or height typed
    // makes that side fixed; one padding sets both.
    function frameWrite(l, path, value) {
        if (!l.layout) return false;
        if (path === "layout.pad") {
            l.layout.padx = l.layout.pady = Math.max(0, value);
        } else if (path === "layout.padx" || path === "layout.pady") {
            l.layout[path.slice(7)] = Math.max(0, value);
        } else if (path === "w" || path === "h") {
            l[path] = Math.max(1, value);
            l.layout[path] = "fixed";
        } else if (path === "x" || path === "y") {
            const by = value - l[path];
            l.children.forEach(c => shiftLayer(c, path === "x" ? by : 0, path === "y" ? by : 0));
            l[path] = value;
        } else {
            return false;
        }
        return true;
    }

    // Shift+A: the picked layers in a new auto layout frame, in the order they're in
    // across (or down, when they're spread further that way), the gap between them
    // what it was on average - so they stay about where they were. A frame with no
    // layout, picked alone, is given one instead.
    function autoLayout() {
        const all = picked();
        if (!all.length || state.adding) return;
        const spreadOf = list => {
            const boxes = list.map(outerBox);
            const xs = boxes.map(b => (b.l + b.r) / 2), ys = boxes.map(b => (b.t + b.b) / 2);
            return Math.max(...xs) - Math.min(...xs) >= Math.max(...ys) - Math.min(...ys) ? "row" : "column";
        };
        if (all.length === 1 && all[0].layout && all[0].layout.direction === "none") {
            all[0].layout.direction = all[0].children.length ? spreadOf(all[0].children) : "row";
            return commit();
        }
        const owner = find(all[0].id).owner;
        if (!sameOwner(all) || (owner && owner.kind === "boolean") || full()) return;
        const direction = all.length > 1 ? spreadOf(all) : "row";
        const row = direction === "row";
        const {list, taken, index} = lift(all);
        const rows = taken.map(l => ({l, b: outerBox(l)})).sort((p, q) => row ? p.b.l - q.b.l : p.b.t - q.b.t);
        const gaps = rows.slice(1).map((r, i) => row ? r.b.l - rows[i].b.r : r.b.t - rows[i].b.b);
        const gap = gaps.length ? Math.max(0, Math.round(gaps.reduce((a, b) => a + b, 0) / gaps.length)) : 16;
        const b = boundsOf(taken.flatMap(l => leaves(l)));
        const made = fresh("frame", {x: (b.l + b.r) / 2, y: (b.t + b.b) / 2, w: Math.max(1, b.r - b.l), h: Math.max(1, b.b - b.t),
            fill: {type: "none", colour: "#3c82e0", angle: 0, stops: [{at: 0, colour: "#3c82e0"}, {at: 100, colour: "#9a5cf0"}]},
            stroke: {width: 0, colour: "#ffffff", join: "miter", cap: "butt"}, radius: 0,
            layout: {direction, gap, padx: 0, pady: 0, align: "center", w: "hug", h: "hug"}, children: rows.map(r => r.l)});
        delete made.blur;
        list.splice(index, 0, made);
        sel = [made.id];
        commit();
    }

    function fillFrame() {
        const all = picked(), f = all.length === 1 && all[0].layout ? all[0] : null;
        const owner = all.length ? (find(all[0].id) || {}).owner : null;
        show("auto-layout", all.length > 0 && sameOwner(all) && !(owner && owner.kind === "boolean"));
        show("layout-group", !!f);
        if (!f) return panels.layout.show(false);
        const turn = document.querySelector('#box-fields [data-for="rot"]');
        if (turn) turn.hidden = true;                                    // a frame is never turned
        const L = f.layout;
        show("layout-flow", L.direction !== "none");
        put($("layout-pad"), L.padx === L.pady ? L.padx : "");
        const names = L.direction === "column" ? ["Left", "Centre", "Right"] : ["Top", "Middle", "Bottom"];
        $("layout-align").querySelectorAll("button[data-value]").forEach((b, i) => { b.textContent = Buddy.t(names[i]); });
    }

    function bindFrames() {
        $("auto-layout").addEventListener("click", autoLayout);
    }

    // ---------------------------------------------------- the pop-ups --
    // The detailed options live in small panels over the page, one at a time: opened
    // by a button, moved by the head, closed by the X or Escape.

    const panels = {};

    function panel(name, popId, buttonId) {
        const pop = $(popId), button = $(buttonId), head = pop.querySelector(".dg-pop-head");
        const me = {
            show(on) {
                if (pop.hidden === !on) return;
                if (on) Object.values(panels).forEach(other => { if (other !== me) other.show(false); });
                pop.hidden = !on;
                button.setAttribute("aria-expanded", String(on));
            },
        };
        panels[name] = me;
        const close = pop.querySelector(".dg-pop-close");
        close.append(Buddy.icon("close"));
        close.addEventListener("click", () => me.show(false));
        button.addEventListener("click", () => me.show(pop.hidden));
        let grip = null;
        head.addEventListener("pointerdown", e => {
            if (e.target.closest("button")) return;
            const box = pop.getBoundingClientRect();
            grip = [e.clientX - box.left, e.clientY - box.top];
            head.setPointerCapture(e.pointerId);
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
    }

    function fillDesigns() {
        const rows = (state.designs || []).map(d => el("div.ds-design" + (d.id === state.design_id ? ".current" : ""), {}, [
            el("span.ds-design-name", {text: d.name || Buddy.t("Untitled"), translate: "no", title: d.name}),
            el("button.btn", {type: "button", text: "Open", onclick: () => openDesign(d)}),
            el("button.btn.ghost.icon", {type: "button", title: "Delete this saved design", "aria-label": "Delete this saved design",
                                         onclick: () => deleteDesign(d)}, [Buddy.icon("trash")]),
        ]));
        $("designs").replaceChildren(...rows);
        show("no-designs", !rows.length);
        put($("design-name"), doc.name);
        $("save-copy").disabled = !state.design_id;
    }

    // Whether what's on the canvas can be let go: asked, when it isn't saved.
    async function letGo(ok) {
        if (state.saved || !doc.layers.length) return true;
        return Buddy.confirm({title: "This design isn't saved", text: "What you've changed since it was last saved will be lost.",
                              ok, cancel: "Keep working"});
    }

    async function openDesign(d) {
        if (d.id !== state.design_id && !(await letGo("Open the other one"))) return;
        sel = [];
        editing = null;
        view.fit = true;
        ask("open", {id: d.id});
    }

    async function deleteDesign(d) {
        const yes = await Buddy.confirm({title: "Delete this saved design?", text: "It can't be brought back. What's on the canvas stays.",
                                         ok: "Delete", danger: true});
        if (yes) ask("delete_design", {id: d.id});
    }

    // ---- components --------------------------------------------------------
    // A main component is a container (like a group) with a box of its own, "box":
    // the straight box its instances are fitted from. An instance is a leaf with a box
    // (x, y, w, h, rot) that draws its main's children ("of" is the main's id) as they
    // are now, carried from the main's box into its own by the rule in ../model.py
    // (resolve) - resolveInstance() here is that function, sum for sum. Linked (the
    // default), an instance scales uniformly and exactly; unlinked, it stretches, and a
    // stretched child turned other than a multiple of 90 keeps its turn's way and its
    // area but not the slant (model.py says how).
    //
    // The main's box is kept round its children as it is edited (settleMains, on
    // every commit): moved as a whole, the box goes with it and no instance changes;
    // changed inside (a child moved, added, removed, resized), each instance is given
    // the box that draws everything it drew before exactly where it was - so a child
    // moved in the main moves in every instance, and nothing else does. No instance
    // has changes of its own yet (colours, text): that is the next step.

    const KIND_TAGS = {group: "Group", boolean: "Combined", frame: "Auto layout", component: "Component", instance: "Instance"};
    const drawingMains = new Set();         // mains being drawn, so a loop never runs away

    function stretchy(l) {
        return SHAPES.includes(l.kind) || l.kind === "instance";
    }

    function mainById(id) {
        const l = layer(id);
        return l && l.kind === "component" ? l : null;
    }

    function allMains() {
        const out = [];
        if (doc) walk(doc.layers, l => { if (l.kind === "component") out.push(l); });
        return out;
    }

    function instancesOf(id) {
        const out = [];
        walk(doc.layers, l => { if (l.kind === "instance" && l.of === id) out.push(l); });
        return out;
    }

    // Every main in some layers, wherever it is in them.
    function mainsIn(list) {
        const out = [];
        walk(list, l => { if (l.kind === "component" && !out.includes(l)) out.push(l); });
        return out;
    }

    // The straight box round a main's children as they are now: {x, y, w, h}.
    function liveBox(m) {
        const b = boundsOf(leaves(m));
        return {x: (b.l + b.r) / 2, y: (b.t + b.b) / 2, w: Math.max(1, b.r - b.l), h: Math.max(1, b.b - b.t)};
    }

    // Whether the main is being changed as a whole: it, or a layer it is in, is picked.
    function wholeEdit(m) {
        for (let at = find(m.id); at; at = at.owner ? find(at.owner.id) : null) {
            if (sel.includes(at.layer.id)) return true;
        }
        return false;
    }

    const sameSize = (a, b) => Math.abs(a.w - b.w) < 0.01 && Math.abs(a.h - b.h) < 0.01;
    const sameBox = (a, b) => sameSize(a, b) && Math.abs(a.x - b.x) < 0.01 && Math.abs(a.y - b.y) < 0.01;

    // The box a main's instances are drawn from: its own - or, while it's moved as a
    // whole, the box round where its children are now (settleMains does the same).
    function mainBox(m) {
        const kept = m.box || liveBox(m);
        if (!wholeEdit(m)) return kept;
        const live = liveBox(m);
        return sameSize(live, kept) ? live : kept;
    }

    // An instance given the box `to` in place of `from`, drawing all it drew before.
    function refit(inst, from, to) {
        const sx = inst.w / from.w, sy = inst.h / from.h;
        const [dx, dy] = turn(sx * (to.x - from.x), sy * (to.y - from.y), inst.rot);
        Object.assign(inst, {x: inst.x + dx, y: inst.y + dy, w: Math.max(1, sx * to.w), h: Math.max(1, sy * to.h)});
    }

    function settleMains() {
        if (!doc) return;
        // A refitted instance inside another main changes that one's box in turn.
        for (let pass = 0; pass < 8; pass++) {
            let again = false;
            allMains().forEach(m => {
                const live = liveBox(m), kept = m.box;
                if (kept && sameBox(live, kept)) return;
                if (kept && !(wholeEdit(m) && sameSize(live, kept))) {
                    instancesOf(m.id).forEach(inst => refit(inst, kept, live));
                    again = true;
                }
                m.box = live;
            });
            if (!again) break;
        }
    }

    // What an instance draws: a group in its place of copies of its main's children,
    // carried into its box (model.resolve) - or null when its main isn't there.
    function resolveInstance(inst) {
        const main = mainById(inst.of);
        if (!main) return null;
        const box = mainBox(main);
        const sx = inst.w / box.w, sy = inst.h / box.h, k = Math.sqrt(sx * sy);
        const place = (x, y) => {
            const [dx, dy] = turn((x - box.x) * sx, (y - box.y) * sy, inst.rot);
            return [inst.x + dx, inst.y + dy];
        };
        const round = deg => (deg % 360 + 360) % 360;
        const carried = l => {
            const out = JSON.parse(JSON.stringify(l));
            out.id = inst.id + "/" + l.id;
            if (l.children) {
                out.children = l.children.map(carried);
                if (l.kind === "boolean") out.fill.angle = round(l.fill.angle + inst.rot);
            }
            if (!l.children || l.w !== undefined) {
                // A leaf - or an auto layout frame's own box, carried as a leaf's is.
                const ax = sx * Math.cos(rad(l.rot)), ay = sy * Math.sin(rad(l.rot));
                const across = Math.hypot(ax, ay), down = sx * sy / across;
                [out.x, out.y] = place(l.x, l.y);
                out.rot = round(Math.atan2(ay, ax) * 180 / Math.PI + inst.rot);
                if (l.layout) Object.assign(out.layout, {gap: l.layout.gap * k, padx: l.layout.padx * k, pady: l.layout.pady * k});
                if (l.kind === "image") {
                    out.w = l.w * k;
                    out.h = out.w * l.px[1] / l.px[0];
                } else {
                    out.w = l.w * across;
                    out.h = l.h * down;
                }
                if (l.kind === "text") out.size = l.size * down;
                if (l.radius !== undefined) out.radius = l.radius * k;
                if (l.radii) out.radii = l.radii.map(r => r * k);
                if (l.stroke) out.stroke.width = l.stroke.width * k;
            }
            if (l.shadow) ["dx", "dy", "blur"].forEach(key => { out.shadow[key] = l.shadow[key] * k; });
            if (l.blur !== undefined) out.blur = l.blur * k;
            return out;
        };
        return {id: inst.id, kind: "group", name: inst.name, hidden: inst.hidden, locked: inst.locked,
                opacity: inst.opacity, children: main.children.map(carried)};
    }

    function drawInstance(to, l, depth) {
        if (drawingMains.has(l.of)) return;
        const made = resolveInstance(l);
        if (!made) return;
        drawingMains.add(l.of);
        try { drawLayer(to, made, depth); } finally { drawingMains.delete(l.of); }
    }

    function onInstance(l, p) {
        if (drawingMains.has(l.of)) return false;
        const made = resolveInstance(l);
        if (!made) return false;
        drawingMains.add(l.of);
        try { return leaves(made).some(leaf => onLeaf(leaf, p)); } finally { drawingMains.delete(l.of); }
    }

    // How many layers the design draws, every instance counted as all its main's - and
    // what one main weighs.
    function weigher() {
        const memo = {};
        const of = id => {
            const m = mainById(id);
            if (!m) return 1;
            if (memo[id] === undefined) {
                memo[id] = 1;
                memo[id] = 1 + m.children.reduce((sum, c) => sum + weigh(c), 0);
            }
            return memo[id];
        };
        const weigh = l => l.kind === "instance" ? of(l.of) : 1 + (l.children || []).reduce((sum, c) => sum + weigh(c), 0);
        return {of, total: doc.layers.reduce((sum, l) => sum + weigh(l), 0)};
    }

    function overDrawn(more) {
        // more: what's about to be added weighs (anything weighs at least one).
        const most = state.max_drawn || Infinity;
        if (weigher().total + Math.max(1, more) <= most) return false;
        Buddy.toast(Buddy.t("A design draws up to {n} layers, each instance counted as all of its main's layers.").replace("{n}", most));
        return true;
    }

    // Whether putting l into the container `into` would have a main draw itself.
    function wouldLoop(into, l) {
        const owners = [];
        for (let at = into ? find(into.id) : null; at; at = at.owner ? find(at.owner.id) : null) owners.push(at.layer);
        const home = owners.filter(o => o.kind === "component").map(o => o.id);
        if (!home.length) return false;
        const reaches = (x, seen) => {
            let hit = false;
            walk([x], c => {
                if (hit || c.kind !== "instance") return;
                if (home.includes(c.of)) hit = true;
                else if (!seen.includes(c.of) && mainById(c.of)) hit = reaches(mainById(c.of), [...seen, c.of]);
            });
            return hit;
        };
        return reaches(l, []);
    }

    function instanceOf(main) {
        const b = main.box || liveBox(main);
        return {id: newId(), kind: "instance", name: main.name, hidden: false, locked: false, opacity: 100,
                of: main.id, x: b.x, y: b.y, w: b.w, h: b.h, rot: 0, linked: true};
    }

    // What a copy of a layer is (Duplicate, Alt-drag, paste): a main becomes an instance
    // of it, as in Figma, while the main is there; an instance whose main has gone, the
    // plain group it drew when it was copied.
    function twinOf(l) {
        const main = l.kind === "component" ? mainById(l.id) : null;
        if (main) return instanceOf(main);
        if (l.kind === "instance") {
            const {_as, ...rest} = l;
            return mainById(l.of) ? rest : _as || rest;
        }
        return l.children ? {...l, children: l.children.map(twinOf)} : l;
    }

    // An instance's picture as plain layers, instances inside it too, every id new.
    function flattened(inst) {
        const made = resolveInstance(inst);
        if (!made) return null;
        const open = list => list.map(c => c.kind === "instance" ? flattened(c) : c.children ? {...c, children: open(c.children)} : c)
            .filter(Boolean);
        made.children = open(made.children);
        walk([made], c => { c.id = newId(); });
        return made;
    }

    // A layer as copied: instances carry what they draw, for a paste once their main has gone.
    function keptForPaste(l) {
        const out = JSON.parse(JSON.stringify(l));
        walk([out], c => {
            if (c.kind !== "instance") return;
            const kept = flattened(c);
            if (kept) c._as = kept;
        });
        return out;
    }

    // Detach: the instance becomes a plain group of copies at its place and size (an
    // instance inside it stays one).
    function detachOne(inst) {
        const where = find(inst.id), made = resolveInstance(inst);
        if (!where || !made) return null;
        walk([made], c => { c.id = newId(); });
        where.list.splice(where.index, 1, made);
        return made.id;
    }

    function detachPicked() {
        const all = picked().filter(l => l.kind === "instance");
        if (!all.length) return;
        sel = all.map(detachOne).filter(Boolean);
        commit();
    }

    // Before a main goes (deleted, ungrouped), its instances outside what goes are
    // detached - they stay as they look - once it's said they will be. True when it
    // has taken over (then() runs once the answer is yes).
    function guardMains(mains, going, then, how) {
        const gone = new Set();
        walk(going, l => gone.add(l));
        const left = mains.flatMap(m => instancesOf(m.id)).filter(i => !gone.has(i));
        if (!left.length) return false;
        Buddy.confirm({title: how === "ungroup" ? "Ungroup this component?" : "Delete this component?",
                       text: left.length === 1 ? Buddy.t("Its instance becomes a plain group, where it is now, looking as it does.")
                           : Buddy.t("Its {n} instances become plain groups, where they are now, looking as they do.").replace("{n}", left.length),
                       ok: how === "ungroup" ? "Ungroup" : "Delete", danger: true}).then(yes => {
            if (!yes) return;
            left.forEach(detachOne);
            then();
        });
        return true;
    }

    // Create component: the picked layers become a main - one group picked becomes one.
    function canMake(all) {
        if (!all.length || !sameOwner(all) || mainsIn(all).length) return false;
        for (let at = find(all[0].id).owner; at; at = (find(at.id) || {}).owner) {
            if (at.kind === "component" || at.kind === "boolean") return false;
        }
        return true;
    }

    function makeComponent() {
        const all = picked();
        if (!canMake(all)) return;
        let made;
        if (all.length === 1 && all[0].kind === "group") {
            made = all[0];
            made.kind = "component";
        } else {
            if (full()) return;
            const {list, taken, index} = lift(all);
            made = {id: newId(), kind: "component", hidden: false, locked: false, opacity: 100, children: taken};
            list.splice(index, 0, made);
        }
        if (!made.name || made.name === Buddy.t("Group")) {
            made.name = `${Buddy.t(TITLES.component)} ${allMains().length}`;
        }
        made.box = liveBox(made);
        sel = [made.id];
        commit();
    }

    function goToMain(m) {
        if (!m) return;
        if (editing) stopEditing();
        sel = [m.id];
        fillAll();
        zoomToPicked();
    }

    // An instance of a main in the middle of the view, on top.
    function placeInstance(m) {
        if (!m || state.adding || full() || overDrawn(weigher().of(m.id))) return;
        const box = $("stage").parentElement, made = instanceOf(m);
        made.x = (box.clientWidth / 2 - view.ox) / view.k;
        made.y = (box.clientHeight / 2 - view.oy) / view.k;
        doc.layers.push(made);
        sel = [made.id];
        commit();
    }

    function fillComponentButtons(all) {
        const one = all.length === 1 ? all[0] : null;
        show("make-component", canMake(all));
        show("detach", all.some(l => l.kind === "instance"));
        show("go-main", !!one && one.kind === "instance" && !!mainById(one.of));
    }

    function fillComponents() {
        const rows = allMains().map(m => el("div.ds-design" + (sel.includes(m.id) ? ".current" : ""), {}, [
            el("span.ds-design-name", {text: m.name, translate: "no", title: m.name}),
            el("button.btn", {type: "button", text: "Place", title: "An instance of it in the middle of the view",
                              onclick: () => placeInstance(m)}),
            el("button.btn", {type: "button", text: "Show", title: "Go to main component", onclick: () => goToMain(m)}),
        ]));
        $("components").replaceChildren(...rows);
        show("no-components", !rows.length);
    }

    function bindComponents() {
        $("make-component").addEventListener("click", makeComponent);
        $("detach").addEventListener("click", detachPicked);
        $("go-main").addEventListener("click", () => {
            const one = picked()[0];
            if (one && one.kind === "instance") goToMain(mainById(one.of));
        });
        // For whoever moves layers between containers (the layers list): what may go where.
        window.designComponents = {wouldLoop, resolveInstance};
    }

    // -------------------------------------------------------------- keys --

    // A field showing what put() last put there (or a list to choose from): nothing typed.
    function untouched(target) {
        const field = target.closest("input, select");
        if (!field) return false;
        if (field.tagName === "SELECT") return true;
        return field.dataset.put !== undefined && field.value === field.dataset.put;
    }

    function typing(target) {
        const field = target && target.closest && target.closest("input, textarea, select, [contenteditable]");
        return !!field && !(field.tagName === "INPUT" && /^(checkbox|radio|range|color|button)$/.test(field.type));
    }

    function bindKeys() {
        document.addEventListener("keyup", e => {
            if (e.key === " ") space = false;
            if (e.key === "Alt" && gauge) { gauge = null; paint(); }
        });
        document.addEventListener("keydown", e => {
            const blocked = document.querySelector(".modal-backdrop") || document.getElementById("picker");
            if (!doc || blocked) return;
            if (e.key === "Escape") {
                if (pen) finishPen(false);
                else if (editing) stopEditing();
                else if (tool !== "select") setTool("select");
                Object.values(panels).forEach(p => p.show(false));
                if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
                return;
            }
            // Ctrl+Z in a field that only has the focus - nothing typed in it since it was
            // filled - is the design's undo, not the field's.
            if ((e.ctrlKey || e.metaKey) && !e.altKey && /^[zy]$/i.test(e.key) && typing(e.target) && untouched(e.target)) {
                e.preventDefault();
                e.target.blur();
                if (editing) stopEditing();
                return ask(e.key.toLowerCase() === "y" || e.shiftKey ? "redo" : "undo");
            }
            if (typing(e.target)) {
                if (e.key === "Enter" && e.target.blur) e.target.blur();
                return;
            }
            const key = e.key.toLowerCase(), cmd = e.ctrlKey || e.metaKey;
            const did = () => e.preventDefault();
            if (key === " ") { space = true; return did(); }
            if (key === "f2" && sel.length === 1 && !editing && !pen) { did(); return renameRow(sel[0]); }
            if (cmd && !e.altKey && (key === "z" || key === "y")) {
                did();
                if (editing) stopEditing();
                return ask(key === "y" || e.shiftKey ? "redo" : "undo");
            }
            if (rulerKey(e, cmd)) return did();
            if (state.adding) return;
            if (cmd && e.altKey && (e.code === "KeyK" || e.code === "KeyB")) {
                did();
                return e.code === "KeyK" ? makeComponent() : detachPicked();
            }
            if (key === "enter" && pen) return finishPen(false);
            if (key === "delete" || key === "backspace") { did(); return editing ? removePoint() : remove(); }
            if (cmd && key === "c") return copy();
            if (cmd && key === "x") { copy(); return remove(); }
            if (cmd && key === "v") { did(); return paste(); }
            if (cmd && key === "d") { did(); return duplicate(); }
            if (cmd && key === "g") { did(); return e.shiftKey ? ungroup() : group(); }
            if (cmd && key === "a") { did(); sel = doc.layers.filter(l => !l.locked && !l.hidden).map(l => l.id); fillAll(); return paint(); }
            if (cmd && e.shiftKey && (key === "h" || key === "l") && sel.length) {
                // Hide or show, lock or unlock what's picked.
                did();
                const flag = key === "h" ? "hidden" : "locked", on = !picked()[0][flag];
                picked().forEach(l => { l[flag] = on; });
                return commit();
            }
            if (cmd && e.code === "Digit0") { did(); return zoomTo(1); }
            if (!cmd && e.shiftKey && e.code === "Digit1") { did(); view.fit = true; return paint(); }
            if (!cmd && e.shiftKey && e.code === "Digit2") { did(); return zoomToPicked(); }
            if (!cmd && !e.altKey && (key === "+" || key === "=")) return zoomTo(view.k * 1.25);
            if (!cmd && !e.altKey && key === "-") return zoomTo(view.k / 1.25);
            if (key === "tab" && doc.layers.length) {
                // The next layer up (Shift: down), round and round.
                did();
                const tops = doc.layers.filter(l => !l.hidden && !l.locked);
                if (!tops.length) return;
                const i = tops.findIndex(l => l.id === sel[0]);
                sel = [tops[((i < 0 ? (e.shiftKey ? 0 : -1) : i) + (e.shiftKey ? -1 : 1) + tops.length) % tops.length].id];
                fillAll();
                return paint();
            }
            if (!cmd && !e.altKey && !e.shiftKey && /^Digit\d$/.test(e.code) && sel.length) {
                // 1 to 9 are 10% to 90% opacity, 0 is 100%.
                const digit = Number(e.code.slice(5));
                picked().forEach(l => { l.opacity = digit ? digit * 10 : 100; });
                return commit("opacity", TYPING_MS);
            }
            if (cmd && key === "]") { did(); return order(e.shiftKey ? "front" : 1); }
            if (cmd && key === "[") { did(); return order(e.shiftKey ? "back" : -1); }
            if (key.startsWith("arrow")) {
                did();
                const step = e.shiftKey ? 10 : 1;
                return nudge(key === "arrowleft" ? -step : key === "arrowright" ? step : 0,
                             key === "arrowup" ? -step : key === "arrowdown" ? step : 0);
            }
            if (!cmd && !e.altKey && e.shiftKey && (key === "h" || key === "v") && sel.length) {
                did();
                return flip(key === "h" ? "x" : "y");
            }
            if (!cmd && !e.altKey && e.shiftKey && key === "a") { did(); return autoLayout(); }
            if (cmd || e.altKey) return;
            const tools = {v: "select", r: "rect", o: "ellipse", l: "line", p: "pen", t: "text"};
            if (tools[key]) setTool(tools[key]);
        });
    }

    // ---- eyedropper --
    // A colour taken from the design as it's drawn. I - or the dropper button beside a
    // colour - makes the cursor a dropper over the canvas; a click takes the colour there,
    // and Escape, a right-click or a click anywhere else stops. The colour is the design's
    // own at that pixel: its layers over each other as they're seen, without the squares
    // under them (the canvas is see-through in Resolve). Where nothing is drawn there is no
    // colour to take, so the dropper stays on; a see-through colour is taken at full
    // strength. From I it goes to the picked layers' fill (a text's colour, a line's
    // stroke) - Shift, on the I or the click, to their stroke; from a button, to that
    // button's field. While it's on, "Anywhere on the screen" hands the same field to
    // Color Palette's screen dropper (page.py), which sends back a colour, never a picture.

    const FAINT = 8;                // alpha (of 255) below which nothing is there
    const LOUPE = 11, CELL = 8;     // the magnifier: pixels across, screen px each
    let dropper = null;             // {aim: {ids, path | stop | stroke}, button, at: {sx, sy}}
    let screenPick = null;          // {aim, ending} while the screen dropper is out
    const shot = {canvas: null, doc: null, key: ""};
    const magnified = document.createElement("canvas");

    // A pixel (not premultiplied, as getImageData gives it) as its colour: "#rrggbb", as
    // a colour field and the model write it - or null where (almost) nothing is drawn.
    function colourOf(r, g, b, a) {
        if (!(a >= FAINT)) return null;
        return "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");
    }

    // The design alone, drawn as the stage draws it - same pixels, no squares and nothing
    // over it - kept while nothing it shows has changed.
    function designShot() {
        const stage = $("stage"), ratio = window.devicePixelRatio || 1;
        const loaded = [...pictures.values()].filter(p => p.complete && p.naturalWidth).length;
        const key = [localRev, view.k, view.ox, view.oy, stage.width, stage.height, ratio, loaded].join();
        if (shot.canvas && shot.doc === doc && shot.key === key) return shot.canvas.getContext("2d");
        const c = shot.canvas || document.createElement("canvas");
        c.width = stage.width;
        c.height = stage.height;
        const ctx = c.getContext("2d", {willReadFrequently: true});
        const [W, H] = doc.size;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.beginPath();
        ctx.rect(view.ox, view.oy, W * view.k, H * view.k);
        ctx.clip();
        ctx.setTransform(view.k * ratio, 0, 0, view.k * ratio, view.ox * ratio, view.oy * ratio);
        doc.layers.forEach(l => drawLayer(ctx, l, 0));
        Object.assign(shot, {canvas: c, doc, key});
        return ctx;
    }

    // The design's pixels round a point on the stage (screen px): `n` across, row by row.
    function pixelsAt(sx, sy, n) {
        const ratio = window.devicePixelRatio || 1, half = (n - 1) / 2;
        try {
            return designShot().getImageData(Math.floor(sx * ratio) - half, Math.floor(sy * ratio) - half, n, n).data;
        } catch (_unreadable) {
            return null;
        }
    }

    function colourAt(sx, sy) {
        const px = pixelsAt(sx, sy, 1);
        return px ? colourOf(px[0], px[1], px[2], px[3]) : null;
    }

    // The colour onto what it was taken for. Whether anything took it.
    function giveColour(aim, hex, shift) {
        let took = 0;
        const stroke = !!(shift || aim.stroke);
        const all = aim.ids.map(layer).filter(Boolean);
        const set = (l, path) => { write(l, path, hex); took++; };
        if (aim.path) {
            const root = aim.path.split(".")[0];
            all.filter(l => l[root] !== undefined && !(root === "fill" && l.kind === "line")).forEach(l => set(l, aim.path));
        } else if (aim.stop !== undefined) {
            all.forEach(l => { if (l.fill && l.fill.stops && l.fill.stops[aim.stop]) { l.fill.stops[aim.stop].colour = hex; took++; } });
        } else {
            const onto = l => {
                if (l.kind === "text") return stroke ? null : set(l, "colour");
                if ((stroke || l.kind === "line") && l.stroke) {
                    if (!(l.stroke.width > 0)) l.stroke.width = 4;
                    return set(l, "stroke.colour");
                }
                if (!stroke && l.fill) {
                    l.fill.type = "solid";
                    return set(l, "fill.colour");
                }
                if (l.children && !l.fill) l.children.forEach(onto);
                return null;
            };
            all.forEach(onto);
        }
        if (took) commit();
        return took > 0;
    }

    function dropperHint() {
        $("hint").textContent = !dropper ? ""
            : dropper.aim.path || dropper.aim.stop !== undefined ? "Click the design to take its colour. Escape stops."
            : dropper.aim.stroke ? "Click the design to take its colour for the stroke. Escape stops."
            : "Click the design to take its colour. Shift-click gives it to the stroke. Escape stops.";
    }

    function startDropper(aim, button) {
        if (!doc || !state || state.adding || !aim.ids.length) return;
        if (pen) finishPen(false);
        if (editing) stopEditing();
        dropper = {aim, button: button || null, at: null};
        const stage = $("stage");
        stage.style.cursor = "";
        stage.classList.add("ds-dropping");
        showDropper();
        paint();
    }

    function stopDropper() {
        if (!dropper) return;
        dropper = null;
        shot.canvas = null;
        const stage = $("stage");
        stage.classList.remove("ds-dropping");
        stage.style.cursor = tool === "select" ? "" : "crosshair";
        showDropper();
        drawHint();
        paint();
    }

    function showDropper() {
        document.querySelectorAll(".ds-drop").forEach(b => b.setAttribute("aria-pressed", String(!!dropper && dropper.button === b)));
        $("ds-drop-screen").hidden = !dropper;
        if (dropper) dropperHint();
    }

    // The magnifier, over the stage at the cursor: the design's pixels round it, the one
    // it takes in a square, and the colour that is.
    function drawDropper(ctx) {
        if (!dropper || !dropper.at) return;
        const R = LOUPE * CELL / 2, half = (LOUPE - 1) / 2;
        const px = pixelsAt(dropper.at.sx, dropper.at.sy, LOUPE);
        const sx = Math.round(dropper.at.sx), sy = Math.round(dropper.at.sy);
        const mid = (half * LOUPE + half) * 4;
        const hex = px ? colourOf(px[mid], px[mid + 1], px[mid + 2], px[mid + 3]) : null;
        ctx.save();
        ctx.beginPath();
        ctx.arc(sx, sy, R, 0, Math.PI * 2);
        ctx.save();
        ctx.clip();
        ctx.fillStyle = "#16181c";
        ctx.fillRect(sx - R, sy - R, R * 2, R * 2);
        if (px) {
            magnified.width = magnified.height = LOUPE;
            magnified.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(px), LOUPE, LOUPE), 0, 0);
            ctx.imageSmoothingEnabled = false;
            ctx.drawImage(magnified, sx - R, sy - R, R * 2, R * 2);
        }
        ctx.restore();
        ctx.lineWidth = 1;
        ctx.strokeStyle = "#ffffff";
        ctx.strokeRect(Math.round(sx - CELL / 2) + 0.5, Math.round(sy - CELL / 2) + 0.5, CELL - 1, CELL - 1);
        ctx.lineWidth = 6;
        ctx.strokeStyle = "#000000";
        ctx.stroke();
        ctx.lineWidth = 4;
        ctx.strokeStyle = hex || "#5a5f69";
        ctx.stroke();
        ctx.restore();
        tag(ctx, hex ? hex.toUpperCase() : Buddy.t("Nothing here"), sx, sy + R + 16, "#1e2026");
    }

    // A dropper button beside a colour field; `aimOf` says what the colour is for.
    function swatch(input, aimOf) {
        const wrap = el("span.ds-swatch");
        if (input.parentNode) input.replaceWith(wrap);
        const button = el("button.btn.ghost.icon.ds-drop", {type: "button", "aria-pressed": "false",
            title: "Take this colour from the canvas", "aria-label": "Take this colour from the canvas",
            onclick: () => {
                const again = dropper && dropper.button === button;
                stopDropper();
                if (!again) startDropper({ids: sel.slice(), ...aimOf()}, button);
            }}, [Buddy.icon("dropper")]);
        wrap.append(input, button);
        return wrap;
    }

    function bindDropper() {
        document.querySelectorAll("input.dg-colour[data-prop]").forEach(input => swatch(input, () => ({path: input.dataset.prop})));
        const screen = el("button.btn.ghost#ds-drop-screen", {type: "button", text: "Anywhere on the screen",
            title: "Take the colour from anywhere on the screen, outside Buddy too. A right-click stops."});
        screen.hidden = true;
        $("hint").after(screen);
        screen.addEventListener("click", () => {
            if (!dropper) return;
            const aim = dropper.aim;
            stopDropper();
            const mine = screenPick = {aim, ending: false};
            // Python always answers with dropper_done; this is for if it never can.
            setTimeout(() => { if (screenPick === mine) screenPick = null; }, 60000);
            Buddy.send("screen_dropper");
        });

        const box = $("stage-box");
        box.addEventListener("pointerdown", e => {
            if (!dropper || e.button === 1 || space) return;         // panning still works
            e.stopPropagation();
            e.preventDefault();
            if (e.button !== 0) return stopDropper();
            const p = at(e), hex = colourAt(p.sx, p.sy);
            if (!hex) return Buddy.toast("Nothing is drawn there to take a colour from.");
            const aim = dropper.aim;
            stopDropper();
            if (!giveColour(aim, hex, e.shiftKey)) {
                Buddy.toast("What's picked has no colour like that to change.");
            }
        }, true);
        box.addEventListener("pointermove", e => {
            if (!dropper || drag) return;
            e.stopPropagation();
            const p = at(e);
            dropper.at = {sx: p.sx, sy: p.sy};
            paint();
        }, true);
        box.addEventListener("pointerleave", () => {
            if (dropper && dropper.at) { dropper.at = null; paint(); }
        });
        box.addEventListener("contextmenu", e => { if (dropper) e.preventDefault(); });
        // A click anywhere else puts the dropper down (and does what it does).
        window.addEventListener("pointerdown", e => {
            if (dropper && !box.contains(e.target) && !e.target.closest(".ds-drop, #ds-drop-screen")) stopDropper();
        }, true);
        // While the screen dropper is out, a click that lands on this page is the dropper's.
        ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick", "contextmenu"].forEach(type =>
            window.addEventListener(type, e => {
                if (!screenPick) return;
                e.stopPropagation();
                e.preventDefault();
            }, true));

        window.addEventListener("keydown", e => {
            if (!doc || !state) return;
            if (dropper && e.key === "Escape") {
                e.stopPropagation();
                e.preventDefault();
                return stopDropper();
            }
            const blocked = document.querySelector(".modal-backdrop") || document.getElementById("picker");
            if (blocked || typing(e.target) || state.adding || drag) return;
            const cmd = e.ctrlKey || e.metaKey;
            if (e.key.toLowerCase() === "i" && !cmd && !e.altKey) {
                e.stopPropagation();
                e.preventDefault();
                if (dropper) return stopDropper();
                if (!sel.length) return Buddy.toast("Pick a layer first, then press I to take a colour for it.");
                return startDropper({ids: sel.slice(), stroke: e.shiftKey});
            }
            // Another key does what it does, with the dropper put down.
            if (dropper && !["Shift", "Control", "Alt", "Meta", " "].includes(e.key)) stopDropper();
        }, true);

        Buddy.on("dropped", data => {
            if (!screenPick || screenPick.ending || !doc || !data || !Buddy.parseHex(data.hex)) return;
            if (!giveColour(screenPick.aim, Buddy.parseHex(data.hex).toLowerCase(), false)) Buddy.toast("What's picked has no colour like that to change.");
        });
        Buddy.on("dropper_done", () => {
            if (!screenPick) return;
            // The click that ended it may still have its mouse-up to come here.
            const mine = screenPick;
            mine.ending = true;
            setTimeout(() => { if (screenPick === mine) screenPick = null; }, 400);
        });
    }

    // ------------------------------------------------------------ state --

    function fillAll() {
        if (!doc || !state) return;
        sel = sel.filter(id => layer(id));
        if (editing && !layer(editing.id)) editing = null;
        fillLayers();
        fillPicked();
        fillDesigns();
        fillComponents();
        put($("canvas-w"), doc.size[0]);
        put($("canvas-h"), doc.size[1]);
        put($("seconds"), doc.seconds);
        $("name").textContent = doc.name || Buddy.t("Untitled");
        const [fw, fh] = state.frame, same = fw === doc.size[0] && fh === doc.size[1];
        $("size-note").textContent = !state.connected || same ? ""
            : Buddy.t("The timeline is {w} x {h}: the design is fitted into it, as large as goes.").replace("{w}", fw).replace("{h}", fh);
        $("match-timeline").disabled = !state.connected || same || !!state.adding;
    }

    Buddy.on("state", s => {
        const first = !state;
        state = s;
        // The design Python has, unless this view has changed it since (or holds it).
        if (first || (s.rev >= localRev && !drag && !pending)) {
            doc = JSON.parse(JSON.stringify(s.spec));
            localRev = Math.max(localRev, s.rev);
            // Laid out as this view measures text: told to Python with the next change.
            if (flowAll() > 0.01) unsent = true;
            if (editing) {
                const l = layer(editing.id);
                if (l && l.kind === "path") startEditing(l);
                else editing = null;
            }
        }
        $("timeline").textContent = s.timeline || "";
        $("dot").classList.toggle("on", !!s.connected);
        $("problem").textContent = s.problem || "";
        $("replace").checked = !!s.replace;
        rulerState(s);
        $("add").disabled = !!s.adding;
        $("undo").disabled = !s.can_undo || !!s.adding;
        $("redo").disabled = !s.can_redo || !!s.adding;
        show("unsaved", !s.saved && doc.layers.length > 0);
        fillAll();
        paint();
    });

    Buddy.on("fonts", data => {
        fonts = data.fonts || {};
        $("font").replaceChildren(...Object.keys(fonts).map(f => el("option", {value: f, text: f})));
        if (doc) fillAll();
    });

    Buddy.on("picked", data => {
        sel = data.ids || [];
        if (doc) { fillAll(); paint(); }
    });

    Buddy.on("drop_hover", on => $("stage-box").classList.toggle("drop", on === true));
    Buddy.on("toast", t => Buddy.toast(t.text, 3200));
    Buddy.on("alert", a => Buddy.modal({title: a.title, body: el("p.modal-text", {text: a.text}),
                                        buttons: [{label: "OK", kind: "accent"}]}));

    function bind() {
        document.querySelectorAll("[data-prop]").forEach(node => {
            const path = node.dataset.prop;
            if (node.classList.contains("segmented")) {
                node.addEventListener("click", e => {
                    const button = e.target.closest("button[data-value]");
                    if (button) setProp(path, button.dataset.value);
                });
            } else if (node.type === "checkbox") {
                node.addEventListener("change", () => setProp(path, node.checked));
            } else if (node.type === "range" || node.type === "color") {
                node.addEventListener("input", () => setProp(path, node.value, 30));
            } else {
                node.addEventListener("input", () => setProp(path, node.value, TYPING_MS));
                node.addEventListener("change", () => { setProp(path, node.value); flush(); });
                if (NUMBERS.test(path)) stepping(node, path);
            }
        });
        $("link-size").addEventListener("click", () => {
            const targets = props("linked");
            if (!targets.length) return;
            const on = !targets[0].linked;
            targets.forEach(l => { l.linked = on; });
            commit();
        });
        $("align").addEventListener("click", e => {
            const button = e.target.closest("button[data-spread]");
            if (button) spread(button.dataset.spread);
        });
        $("font").addEventListener("change", e => {
            const styles = fonts[e.target.value] || [];
            props("font").forEach(l => {
                l.font = e.target.value;
                if (styles.length && !styles.includes(l.style)) l.style = styles.includes("Bold") ? "Bold" : styles.includes("Regular") ? "Regular" : styles[0];
                lastFace = {font: l.font, style: l.style};
            });
            commit();
        });
        $("style").addEventListener("change", e => setProp("style", e.target.value));
        $("tools").addEventListener("click", e => {
            const button = e.target.closest("button[data-tool]");
            if (button && !state.adding) setTool(button.dataset.tool);
        });
        $("new-image").addEventListener("click", () => { setTool("select"); ask("new_image"); });
        $("align").addEventListener("click", e => {
            const button = e.target.closest("button[data-align]");
            if (button) lineUp(button.dataset.align);
        });
        $("flip").addEventListener("click", e => {
            const button = e.target.closest("button[data-flip]");
            if (button) flip(button.dataset.flip);
        });
        $("combine").addEventListener("click", e => {
            const button = e.target.closest("button[data-op]");
            if (button) combine(button.dataset.op);
        });
        $("group").addEventListener("click", group);
        $("ungroup").addEventListener("click", ungroup);
        $("duplicate").addEventListener("click", duplicate);
        $("remove").addEventListener("click", remove);
        $("order-up").addEventListener("click", () => order(1));
        $("order-down").addEventListener("click", () => order(-1));
        $("add-stop").addEventListener("click", addStop);
        $("reset-layer").addEventListener("click", resetLayers);
        $("edit-points").addEventListener("click", () => {
            const l = picked()[0];
            if (!l || !SHAPES.includes(l.kind) || l.locked) return;
            setTool("select");
            if (editing) return stopEditing();
            if (l.kind !== "path") {
                asPath(l);
                commit();
            }
            startEditing(l);
        });
        const size = () => {
            const w = Math.round(calc($("canvas-w").value)), h = Math.round(calc($("canvas-h").value));
            if (!(w > 0 && h > 0) || (w === doc.size[0] && h === doc.size[1])) return;
            doc.size = [w, h];
            view.fit = true;
            commit();
        };
        $("canvas-w").addEventListener("change", size);
        $("canvas-h").addEventListener("change", size);
        $("seconds").addEventListener("change", e => {
            const v = calc(e.target.value);
            if (!isFinite(v)) return;
            doc.seconds = v;
            commit();
        });
        $("match-timeline").addEventListener("click", () => {
            doc.size = state.frame.slice();
            view.fit = true;
            commit();
        });
        $("design-name").addEventListener("change", e => {
            doc.name = e.target.value.trim();
            commit();
        });
        $("save").addEventListener("click", () => ask("save", {name: $("design-name").value.trim()}));
        $("save-copy").addEventListener("click", () => ask("save", {name: $("design-name").value.trim(), copy: true}));
        $("new-design").addEventListener("click", async () => {
            if (!(await letGo("Start a new one"))) return;
            sel = [];
            editing = null;
            view.fit = true;
            ask("new_design");
        });
        $("zoom-in").addEventListener("click", () => zoomTo(view.k * 1.25));
        $("zoom-out").addEventListener("click", () => zoomTo(view.k / 1.25));
        $("zoom-fit").addEventListener("click", () => { view.fit = true; paint(); });
        $("undo").append(Buddy.icon("undo"));
        $("redo").append(Buddy.icon("undo"));          // turned round in graphics.css
        $("undo").addEventListener("click", () => { if (editing) stopEditing(); ask("undo"); });
        $("redo").addEventListener("click", () => { if (editing) stopEditing(); ask("redo"); });
        $("refresh").append(Buddy.icon("refresh"));
        $("refresh").addEventListener("click", () => Buddy.send("refresh"));
        $("replace").addEventListener("change", e => Buddy.send("option", {key: "replace", value: e.target.checked}));
        $("add").addEventListener("click", () => {
            if (pen) finishPen(false);
            if (unsent) commit("layout");
            flush();
            Buddy.send("add");
        });
    }

    // A number field: Up and Down step it (Shift: by 10), and its name is a handle -
    // dragged left or right, the number follows.
    function stepping(node, path) {
        const step = (by, wait) => {
            const now = calc(node.value);
            if (!isFinite(now)) return;
            node.value = plain(now + by);
            setProp(path, node.value, wait);
        };
        node.addEventListener("keydown", e => {
            if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
            e.preventDefault();
            step((e.key === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 10 : 1), TYPING_MS);
        });
        const label = node.closest("label");
        if (!label) return;
        label.classList.add("ds-scrub");
        let held = null;
        label.addEventListener("pointerdown", e => {
            if (e.target === node || e.button !== 0 || node.disabled) return;
            const from = calc(node.value);
            if (!isFinite(from)) return;
            held = {x: e.clientX, from, moved: false};
            try { label.setPointerCapture(e.pointerId); } catch (_gone) { /* a pointer that has already left */ }
        });
        label.addEventListener("pointermove", e => {
            if (!held) return;
            const by = Math.round((e.clientX - held.x) / 2) * (e.shiftKey ? 10 : 1);
            if (!held.moved && !by) return;
            held.moved = true;
            node.value = plain(held.from + by);
            setProp(path, node.value, 30);
        });
        const drop = () => {
            if (held && held.moved) flush();
            held = null;
        };
        label.addEventListener("pointerup", drop);
        label.addEventListener("pointercancel", drop);
        // A drag on the name isn't a click on it: the field isn't given the caret.
        label.addEventListener("click", e => { if (e.target !== node) e.preventDefault(); });
    }

    panel("gradient", "gradient-pop", "edit-gradient");
    panel("stroke", "stroke-pop", "edit-stroke");
    panel("corners", "corners-pop", "edit-corners");
    panel("effects", "effects-pop", "edit-effects");
    panel("designs", "designs-pop", "edit-designs");
    panel("layout", "layout-pop", "edit-layout");
    panel("components", "components-pop", "edit-components");
    bind();
    bindFrames();
    bindComponents();
    bindStage();
    bindKeys();
    bindRowDrag();
    bindRulers();
    bindDropper();
    setTool("select");
    new ResizeObserver(paint).observe($("stage").parentElement);
    if (document.fonts && document.fonts.addEventListener) document.fonts.addEventListener("loadingdone", paint);
    // For checking the canvas against Resolve: the design alone, drawn at its own size
    // (a PNG as a data URL) - what a frame exported from the clip is laid over.
    window.designView = {
        design() { return doc; },
        pick(ids) { sel = ids; fillAll(); paint(); },
        moveLayers,                         // the layers list's drag rule, on its own (layers-drag)
        shapePoints,                        // a shape's outline, to hold against geometry._local
        cornerDots: () => cornerDots().map(({x, y, corner}) => ({x, y, corner})),
        picture() {
            const c = document.createElement("canvas");
            [c.width, c.height] = doc.size;
            const ctx = c.getContext("2d");
            doc.layers.forEach(l => drawLayer(ctx, l, 0));
            return c.toDataURL("image/png");
        },
    };
})();
