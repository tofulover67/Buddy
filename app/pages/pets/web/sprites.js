/* The pets' pictures - one SVG per species, drawn into a 130 x 100 box with
   the feet on the ground line (y = 96). Shared by the pet's own window
   (pet/) and the Pets tab's preview and gallery, so they're the same
   animal.

   Every sprite has the same named parts, which sprites.css animates by
   the holder's data-state ("idle", "walk", "sit", "sleep", "work",
   "happy", "cheer") and flips by data-facing ("1" right, "-1" left):
   .rig (everything that bobs), .head, .eye, .leg (.l1 .l2), .arm, and
   the effects shown only in a state: .fx-zzz, .fx-hearts, .fx-confetti,
   .fx-laptop.

       Sprites.render(holder, "blob")   draws (or redraws) the species
       Sprites.SPECIES                  the ids, in order */
"use strict";

const Sprites = (() => {
    const NS = "http://www.w3.org/2000/svg";

    function node(tag, attrs = {}, children = []) {
        const n = document.createElementNS(NS, tag);
        for (const [k, v] of Object.entries(attrs)) {
            if (k === "class") n.setAttribute("class", v);
            else if (k === "text") n.textContent = v;
            else n.setAttribute(k, String(v));
        }
        for (const c of children) n.append(c);
        return n;
    }
    const g = (cls, children, attrs = {}) => node("g", {class: cls, ...attrs}, children);
    const circle = (cx, cy, r, fill, cls = "", extra = {}) => node("circle", {cx, cy, r, fill, class: cls, ...extra});
    const ellipse = (cx, cy, rx, ry, fill, cls = "") => node("ellipse", {cx, cy, rx, ry, fill, class: cls});
    const rect = (x, y, w, h, fill, rx = 0, cls = "") => node("rect", {x, y, width: w, height: h, fill, rx, class: cls});
    const path = (d, fill, cls = "", extra = {}) => node("path", {d, fill, class: cls, ...extra});

    const INK = "#1b1b1f";
    const PINK = "#f28fb0";

    /* The parts every species gets. */
    const shadow = () => ellipse(65, 97, 34, 4, "rgba(0,0,0,.22)", "shadow");
    const heart = (x, y, s) => path(`M${x} ${y + s * .3} c ${-s} ${-s * .9}, ${-s * 1.6} ${s * .5}, 0 ${s * 1.2} c ${s * 1.6} ${-s * .7}, ${s} ${-s * 2.1}, 0 ${-s * 1.2} z`, PINK);
    function fx(headX, headTop) {
        headTop = Math.max(headTop, 26);      // the z's and hearts stay inside the box, whatever the head's height
        const zzz = g("fx fx-zzz", [
            node("text", {x: headX + 10, y: headTop - 2, class: "z z1", text: "z"}),
            node("text", {x: headX + 18, y: headTop - 12, class: "z z2", text: "z"}),
            node("text", {x: headX + 27, y: headTop - 23, class: "z z3", text: "z"}),
        ]);
        const hearts = g("fx fx-hearts", [heart(headX - 8, headTop - 6, 5), heart(headX + 14, headTop - 12, 4),
                                           heart(headX + 2, headTop - 20, 3.5)].map((h, i) => (h.classList.add(`h${i + 1}`), h)));
        const colours = ["#f6c343", "#59c3ff", "#f0567a", "#8be38a", "#c79bff", "#ffa94d", "#59c3ff", "#f6c343"];
        const confetti = g("fx fx-confetti", colours.map((c, i) =>
            rect(14 + i * 14, 18 + (i % 3) * 7, 5, 8, c, 1, `c c${i + 1}`)));
        const laptop = g("fx fx-laptop", [
            rect(96, 74, 30, 20, "#4a4f5c", 3), rect(99, 77, 24, 13, "#9fd8ff", 1, "screen"),
            rect(92, 92, 38, 4, "#6a7080", 2),
        ]);
        return [zzz, hearts, confetti, laptop];
    }

    /* The robot's screen while it works: its face gives way to lines of
       code going by (sprites.css steps them up, a line at a time), clipped
       to the screen. Two of the same seven lines, so the loop is seamless. */
    let codeClips = 0;
    function codeFace(glow) {
        const id = `pet-code-${++codeClips}`;
        const widths = [[0, 14], [4, 22], [4, 9], [8, 18], [4, 12], [0, 20], [0, 7]];
        const lines = [];
        for (let i = 0; i < 14; i++) {
            const [indent, w] = widths[i % 7];
            lines.push(rect(46 + indent, 21 + i * 4.5, w, 2.2, i % 7 === 3 ? "#ffc857" : glow, 1));
        }
        return g("fx fx-code", [
            node("defs", {}, [node("clipPath", {id}, [rect(42, 18, 46, 32, "#000", 6)])]),
            g("code", lines),
        ], {"clip-path": `url(#${id})`});
    }

    const DRAW = {
        /* A tall blue dome with two stacked eyes, a wide grin and a pair of
           line arms - a cheerful, simple shape with no legs: it hops along. */
        blob(rig) {
            const blue = "#3d8fe6";
            const line = {fill: "none", stroke: INK, "stroke-width": 3.2, "stroke-linecap": "round"};
            rig.append(
                path("M34 96 V 46 C 34 10, 96 10, 96 46 V 96 Z", blue, "body"),
                path("M95 60 c 14 -3, 18 10, 6 16", "none", "arm arm-r", line),
                path("M35 66 c -12 2, -12 14, -2 18", "none", "arm arm-l", line),
                g("head", [
                    circle(55, 40, 13, "#fff", "eyeball", {stroke: INK, "stroke-width": 2.4}),
                    circle(76, 35, 15, "#fff", "eyeball", {stroke: INK, "stroke-width": 2.4}),
                    circle(58, 43, 3, INK, "eye l"), circle(79, 38, 3.4, INK, "eye r"),
                    path("M47 56 c 10 20, 34 20, 44 -2 c -14 6, -30 6, -44 2 z", INK, "mouth"),
                    path("M63 68 c 6 4, 14 3, 20 -3 c -6 -1, -14 -1, -20 3 z", "#f08a7a", "tongue"),
                    path("M50 56 c 12 3, 28 3, 40 -1", "none", "teeth", {stroke: "#fff", "stroke-width": 2.2, "stroke-linecap": "round"}),
                ]),
            );
            return [66, 18];
        },
        /* A rounded screen for a head, with a happy face drawn on it in light;
           a small body, claw hands and two short legs. */
        robot(rig) {
            const shell = "#aeb6c6", steel = "#8d96a8", dark = "#4a5160", glow = "#7ef5ff";
            const claw = {fill: "none", stroke: "#6a7284", "stroke-width": 5, "stroke-linecap": "round"};
            const face = {fill: "none", stroke: glow, "stroke-linecap": "round"};
            rig.append(
                rect(52, 80, 9, 16, dark, 3, "leg l1"), rect(69, 80, 9, 16, dark, 3, "leg l2"),
                rect(48, 56, 34, 28, steel, 8, "body"),
                rect(57, 63, 16, 9, "#3b4150", 2),
                circle(61, 67.5, 2, "#8be38a", "lamp"), circle(69, 67.5, 2, "#ffc857"),
                path("M48 64 c -14 0, -14 14, -4 16", "none", "arm arm-l", claw),
                path("M82 64 c 14 0, 14 14, 4 16", "none", "arm arm-r", claw),
                g("head", [
                    rect(60, 6, 10, 6, "#6a7284", 2), rect(63, 2, 4, 8, "#6a7284", 0, "antenna"),
                    rect(36, 12, 58, 44, shell, 10),
                    rect(42, 18, 46, 32, "#141820", 6, "face"),
                    path("M52 33 q 4 -6 8 0", "none", "eye l", {...face, "stroke-width": 3}),
                    path("M70 33 q 4 -6 8 0", "none", "eye r", {...face, "stroke-width": 3}),
                    path("M58 41 q 7 5 14 0", "none", "mouth", {...face, "stroke-width": 2.4}),
                    codeFace(glow),
                ]),
            );
            return [65, 4];
        },
        /* A toadstool: a red, white-spotted cap over a cream stem with the
           face on it, line arms and two little feet. */
        shroomy(rig) {
            const cap = "#e53935", stem = "#f6e7c1", dark = "#1b1b1f";
            const line = {fill: "none", stroke: dark, "stroke-width": 3, "stroke-linecap": "round"};
            rig.append(
                rect(50, 84, 11, 12, stem, 5, "leg l1"), rect(69, 84, 11, 12, stem, 5, "leg l2"),
                path("M44 90 V 54 C 44 44, 86 44, 86 54 V 90 Z", stem, "body"),
                path("M86 68 c 12 -4, 16 8, 6 14", "none", "arm arm-r", line),
                path("M44 72 c -12 0, -12 12, -2 14", "none", "arm arm-l", line),
                g("head", [
                    path("M28 54 C 28 20, 102 20, 102 54 c 0 4, -4 6, -8 6 H 36 c -4 0, -8 -2, -8 -6 z", cap, "cap"),
                    circle(48, 36, 5, "#fff"), circle(70, 28, 4, "#fff"), circle(88, 42, 4.5, "#fff"), circle(60, 48, 3, "#fff"),
                    circle(56, 68, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(72, 68, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(57.5, 69, 2.4, dark, "eye l"), circle(73.5, 69, 2.4, dark, "eye r"),
                    path("M54 78 c 6 9, 16 9, 22 0 c -7 2, -15 2, -22 0 z", dark, "mouth"),
                    path("M60 82 c 3 2, 7 2, 10 0 c -3 -1, -7 -1, -10 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 18];
        },
        /* A cloud: puffs of pale blue with the face, line arms, no feet - it drifts. */
        poof(rig) {
            const puff = "#e7f3ff", edge = "#a9cdf0", dark = "#1b1b1f";
            const line = {fill: "none", stroke: dark, "stroke-width": 3, "stroke-linecap": "round"};
            rig.append(
                path("M96 72 c 12 -4, 16 8, 6 14", "none", "arm arm-r", line),
                path("M34 74 c -12 0, -12 12, -2 14", "none", "arm arm-l", line),
                // The puffs twice: a little bigger in the edge colour under
                // the pale ones, so the outline follows the cloud's outside
                // only and no puff shows through another.
                g("body", [
                    ...[[44, 72, 16], [86, 72, 16], [56, 52, 19], [78, 54, 18], [65, 72, 22]].map(([x, y, r]) => circle(x, y, r + 2, edge)),
                    rect(36, 70, 58, 20, edge, 10),
                    ...[[44, 72, 16], [86, 72, 16], [56, 52, 19], [78, 54, 18], [65, 72, 22]].map(([x, y, r]) => circle(x, y, r, puff)),
                    rect(38, 72, 54, 16, puff, 8),
                ]),
                g("head", [
                    circle(56, 60, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(73, 58, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(57.5, 61, 2.4, dark, "eye l"), circle(74.5, 59, 2.4, dark, "eye r"),
                    path("M57 72 c 5 8, 13 8, 18 0 c -6 2, -12 2, -18 0 z", dark, "mouth"),
                    path("M62 75 c 3 2, 6 2, 9 0 c -3 -1, -6 -1, -9 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 30];
        },
        /* A flame: orange outside, yellow within, the face in the yellow,
           line arms, no feet - it flickers. */
        blaze(rig) {
            const fire = "#ff8a1f", core = "#ffd23f", dark = "#1b1b1f";
            const line = {fill: "none", stroke: dark, "stroke-width": 3, "stroke-linecap": "round"};
            rig.append(
                path("M90 70 c 12 -4, 16 8, 6 14", "none", "arm arm-r", line),
                path("M40 72 c -12 0, -12 12, -2 14", "none", "arm arm-l", line),
                path("M65 6 c 8 14, 26 24, 28 46 c 2 22, -12 42, -28 44 c -16 -2, -30 -22, -28 -44 c 1 -12, 8 -16, 11 -27 c 4 8, 6 10, 8 10 c -2 -10, 3 -21, 9 -29 z", fire, "body flicker"),
                path("M65 38 c 5 9, 15 14, 15 28 c 0 14, -8 25, -15 26 c -7 -1, -15 -12, -15 -26 c 0 -9, 5 -13, 7 -17 c 2 4, 3 5, 4 5 c -1 -5, 1 -10, 4 -16 z", core, "core"),
                g("head", [
                    circle(58, 62, 6, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(73, 60, 6, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(59.5, 63, 2.2, dark, "eye l"), circle(74.5, 61, 2.2, dark, "eye r"),
                    path("M57 72 c 5 9, 13 9, 18 0 c -6 2, -12 2, -18 0 z", dark, "mouth"),
                    path("M62 75 c 3 2, 6 2, 9 0 c -3 -1, -6 -1, -9 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 30];
        },
        /* A cactus: a tall green body with two branch arms and a few
           spines. No feet - it shuffles. */
        spike(rig) {
            const green = "#5fbf63", deep = "#3e9a44", dark = "#1b1b1f";
            const spine = {fill: "none", stroke: deep, "stroke-width": 1.6, "stroke-linecap": "round"};
            rig.append(
                path("M46 62 c -12 0, -17 -8, -15 -22", "none", "arm arm-l", {stroke: green, "stroke-width": 11, "stroke-linecap": "round"}),
                path("M84 56 c 12 0, 17 -8, 15 -22", "none", "arm arm-r", {stroke: green, "stroke-width": 11, "stroke-linecap": "round"}),
                path("M44 96 V 40 C 44 14, 86 14, 86 40 V 96 Z", green, "body"),
                path("M55 30 v 50 M65 26 v 60 M75 30 v 50", "none", "", {stroke: deep, "stroke-width": 2, opacity: .6}),
                path("M50 46 l -5 -3 M50 66 l -5 3 M80 50 l 5 -3 M80 70 l 5 3 M60 86 l -3 4 M70 86 l 3 4", "none", "", spine),
                g("head", [
                    circle(57, 48, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(73, 46, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(58.5, 49, 2.4, dark, "eye l"), circle(74.5, 47, 2.4, dark, "eye r"),
                    path("M56 60 c 5 8, 13 8, 18 0 c -6 2, -12 2, -18 0 z", dark, "mouth"),
                    path("M61 63 c 3 2, 6 2, 9 0 c -3 -1, -6 -1, -9 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 20];
        },
        /* A wedge of cheese, holes and all, with line arms. No feet - it shuffles. */
        cheddar(rig) {
            const cheese = "#ffd23f", rind = "#f0b400", hole = "#e2a400", dark = "#1b1b1f";
            const line = {fill: "none", stroke: dark, "stroke-width": 3, "stroke-linecap": "round"};
            rig.append(
                path("M96 72 c 12 -4, 16 8, 6 14", "none", "arm arm-r", line),
                path("M34 76 c -12 0, -12 12, -2 14", "none", "arm arm-l", line),
                path("M30 94 L 30 46 L 100 28 L 100 94 Z", cheese, "body"),
                path("M30 46 L 100 28 L 100 38 L 30 54 Z", rind),
                circle(44, 72, 5, hole), circle(90, 80, 4, hole), circle(72, 50, 3, hole), circle(50, 88, 2.5, hole),
                g("head", [
                    circle(60, 62, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(77, 58, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(61.5, 63, 2.4, dark, "eye l"), circle(78.5, 59, 2.4, dark, "eye r"),
                    path("M58 74 c 6 9, 16 9, 22 0 c -7 2, -15 2, -22 0 z", dark, "mouth"),
                    path("M64 77 c 3 2, 7 2, 10 0 c -3 -1, -7 -1, -10 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 26];
        },
        /* A cookie with a bite out of it, chips and crumbs, and line arms. No feet - it rolls along. */
        crumb(rig) {
            const dough = "#d9a35c", edge = "#b8803f", chip = "#5a3a22", dark = "#1b1b1f";
            const line = {fill: "none", stroke: dark, "stroke-width": 3, "stroke-linecap": "round"};
            rig.append(
                path("M96 70 c 12 -4, 16 8, 6 14", "none", "arm arm-r", line),
                path("M34 72 c -12 0, -12 12, -2 14", "none", "arm arm-l", line),
                // Round the cookie the long way (clockwise on screen) from the
                // bite's lower edge to its upper one, then the bite's own small
                // arc curving in.
                path("M93.2 51.7 A 30 30 0 1 1 75.3 33.8 A 13 13 0 0 0 93.2 51.7 Z", dough, "body", {stroke: edge, "stroke-width": 2}),
                circle(46, 52, 3.5, chip), circle(52, 76, 3, chip), circle(84, 76, 3.2, chip), circle(70, 86, 2.6, chip), circle(42, 66, 2.4, chip),
                circle(104, 40, 2, dough), circle(110, 48, 1.5, dough), circle(100, 30, 1.6, dough),
                g("head", [
                    circle(58, 58, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(74, 55, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(59.5, 59, 2.4, dark, "eye l"), circle(75.5, 56, 2.4, dark, "eye r"),
                    path("M56 70 c 6 9, 16 9, 22 0 c -7 2, -15 2, -22 0 z", dark, "mouth"),
                    path("M62 73 c 3 2, 7 2, 10 0 c -3 -1, -7 -1, -10 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 26];
        },
        /* An acorn: a scaly cap with a stalk over a brown nut, the face on
           the nut, line arms and two little feet. */
        nutty(rig) {
            const nut = "#c47a3a", cap = "#7a4a22", capLine = "#5c3717", dark = "#1b1b1f";
            const line = {fill: "none", stroke: dark, "stroke-width": 3, "stroke-linecap": "round"};
            rig.append(
                rect(52, 86, 9, 10, nut, 4, "leg l1"), rect(69, 86, 9, 10, nut, 4, "leg l2"),
                path("M92 66 c 12 -4, 16 8, 6 14", "none", "arm arm-r", line),
                path("M38 70 c -12 0, -12 12, -2 14", "none", "arm arm-l", line),
                path("M38 52 C 38 86, 56 90, 65 92 C 74 90, 92 86, 92 52 Z", nut, "body"),
                g("head", [
                    rect(62, 22, 6, 14, cap, 2),
                    path("M34 54 C 34 28, 96 28, 96 54 c 0 3, -2 4, -4 4 H 38 c -2 0, -4 -1, -4 -4 z", cap, "cap"),
                    path("M40 44 q 25 -10 50 0 M38 52 q 27 -8 54 0 M46 36 q 19 -8 38 0", "none", "", {stroke: capLine, "stroke-width": 1.6, opacity: .8}),
                    circle(57, 68, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(73, 66, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(58.5, 69, 2.4, dark, "eye l"), circle(74.5, 67, 2.4, dark, "eye r"),
                    path("M56 78 c 5 7, 13 7, 18 0 c -6 2, -12 2, -18 0 z", dark, "mouth"),
                    path("M61 80 c 3 2, 6 2, 9 0 c -3 -1, -6 -1, -9 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 16];
        },
        /* A rock with a sprout on top and a plaster on its side, line arms,
           a shy smile. No feet - it shuffles. */
        pebble(rig) {
            const stone = "#9aa3ad", shade = "#7d8791", dark = "#1b1b1f";
            const line = {fill: "none", stroke: dark, "stroke-width": 3, "stroke-linecap": "round"};
            rig.append(
                path("M94 72 c 12 -4, 16 8, 6 14", "none", "arm arm-r", line),
                path("M36 76 c -12 0, -12 12, -2 14", "none", "arm arm-l", line),
                path("M32 90 C 28 70, 36 44, 52 38 C 68 32, 96 40, 100 60 C 104 78, 92 92, 76 94 C 60 96, 40 96, 32 90 Z", stone, "body"),
                path("M44 86 c 10 4, 30 4, 44 -2", "none", "", {stroke: shade, "stroke-width": 2, opacity: .7}),
                g("head", [
                    path("M62 38 c -2 -8, -8 -12, -16 -10 c 4 6, 10 8, 16 10 z M64 36 c 0 -8, 6 -14, 14 -14 c -2 7, -7 11, -14 14 z", "#7ac35a", "sprout"),
                    path("M63 40 c 0 -3, 1 -6, 1 -8", "none", "", {stroke: "#3e7a2a", "stroke-width": 2, "stroke-linecap": "round"}),
                    circle(56, 60, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(73, 58, 6.5, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(57.5, 61, 2.4, dark, "eye l"), circle(74.5, 59, 2.4, dark, "eye r"),
                    path("M58 72 q 7 6 14 0", "none", "mouth", {stroke: dark, "stroke-width": 2.4, "stroke-linecap": "round"}),
                    path("M64 74 c 2 3, 5 3, 7 0 c -2 -1, -5 -1, -7 0 z", "#f08a7a", "tongue"),
                    rect(80, 70, 16, 7, "#e8c89a", 3, "", ),
                    rect(85, 70, 6, 7, "#d9b07d", 0),
                ]),
            );
            return [65, 20];
        },
        /* A crab: a round red body, eyes on stalks, two claws for arms and
           legs either side. */
        snappy(rig) {
            const shell = "#ef5a4a", deep = "#c9392b", dark = "#1b1b1f";
            const legLine = {fill: "none", stroke: deep, "stroke-width": 4, "stroke-linecap": "round"};
            rig.append(
                path("M44 84 l -10 12 M52 88 l -6 10 M86 84 l 10 12 M78 88 l 6 10", "none", "legs", legLine),
                path("M40 68 c -10 -2, -14 -14, -4 -16", "none", "arm arm-l", {...legLine, "stroke-width": 5}),
                path("M90 68 c 10 -2, 14 -14, 4 -16", "none", "arm arm-r", {...legLine, "stroke-width": 5}),
                path("M22 46 c 8 -8, 18 -4, 16 6 l -6 -2 l 2 6 c -8 2, -14 -4, -12 -10 z", shell, "claw claw-l"),
                path("M108 46 c -8 -8, -18 -4, -16 6 l 6 -2 l -2 6 c 8 2, 14 -4, 12 -10 z", shell, "claw claw-r"),
                ellipse(65, 72, 32, 22, shell, "body"),
                path("M40 78 c 12 6, 38 6, 50 0", "none", "", {stroke: deep, "stroke-width": 2, opacity: .7}),
                g("head", [
                    // Asleep, the stalks shrink and the eyes come to rest on the shell (sprites.css).
                    path("M54 56 v -14 M76 56 v -14", "none", "stalks", {stroke: deep, "stroke-width": 4, "stroke-linecap": "round"}),
                    circle(54, 38, 7, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(76, 36, 7, "#fff", "eyeball", {stroke: dark, "stroke-width": 2}),
                    circle(55.5, 39, 2.5, dark, "eye l"), circle(77.5, 37, 2.5, dark, "eye r"),
                    path("M54 66 c 6 9, 16 9, 22 0 c -7 2, -15 2, -22 0 z", dark, "mouth"),
                    path("M60 69 c 3 2, 7 2, 10 0 c -3 -1, -7 -1, -10 0 z", "#f08a7a", "tongue"),
                ]),
            );
            return [65, 28];
        },
    };

    const NAMES = {blob: "Blob", robot: "Robot", shroomy: "Shroomy", poof: "Poof", blaze: "Blaze", spike: "Spike",
                   cheddar: "Cheddar", crumb: "Crumb", nutty: "Nutty", pebble: "Pebble", snappy: "Snappy"};

    /* Skins (skins.py lists them): each species' own colour swaps the
       colours it was drawn with; the accessories every pet can wear are
       placed from where its eyes, mouth and head are. */
    const RECOLOUR = {
        blob: {mint: {"#3d8fe6": "#5fcf9a"}},
        robot: {gold: {"#aeb6c6": "#f2c14e", "#8d96a8": "#d9a52a", "#4a5160": "#8a6a1e"}},
        shroomy: {bluecap: {"#e53935": "#3d8fe6"}},
        poof: {storm: {"#e7f3ff": "#9aa3ad", "#a9cdf0": "#6f7a86"}},
        blaze: {bluefire: {"#ff8a1f": "#4a7bff", "#ffd23f": "#9fd8ff"}},
        spike: {autumn: {"#5fbf63": "#e0943a", "#3e9a44": "#b4702a"}},
        cheddar: {blue: {"#ffd23f": "#d9e3ea", "#f0b400": "#b9c7d1", "#e2a400": "#5aa0c8"}},
        crumb: {choc: {"#d9a35c": "#6b4226", "#b8803f": "#4a2d19"}},
        nutty: {golden: {"#c47a3a": "#e7b94a", "#7a4a22": "#b78428", "#5c3717": "#8f6a1f"}},
        pebble: {mossy: {"#9aa3ad": "#7fa36b", "#7d8791": "#5d7f4c"}},
        snappy: {bluecrab: {"#ef5a4a": "#4a90e2", "#c9392b": "#2f6bb5"}},
    };

    function recolour(svg, map) {
        for (const n of svg.querySelectorAll("[fill], [stroke]")) {
            for (const attr of ["fill", "stroke"]) {
                const v = n.getAttribute(attr);
                if (v && map[v]) n.setAttribute(attr, map[v]);
            }
        }
    }

    function accessory(svg, rig, kind, headX, headTop) {
        // Where the eyes are: circles say so; an eye drawn as an arc
        // ("M52 33 q 4 -6 8 0", the robot's) is read from its numbers.
        const eyes = [...rig.querySelectorAll(".eye")].map(e => {
            if (e.tagName === "circle") return [+e.getAttribute("cx"), +e.getAttribute("cy"), +e.getAttribute("r")];
            const n = (e.getAttribute("d") || "").match(/-?[\d.]+/g);
            if (!n || n.length < 6) return null;
            const [x, y, , dy, ex] = n.map(Number);
            return [x + ex / 2, y + dy / 2, ex / 2 + 1];
        }).filter(Boolean);
        const mouth = rig.querySelector(".mouth");
        const m = mouth ? /M\s*([\d.]+)[ ,]+([\d.]+)/.exec(mouth.getAttribute("d") || "") : null;
        const wear = g(`skin skin-${kind}`, []);
        if (kind === "shades") {
            // One black silhouette across both eyes, like a cartoon's: a
            // straight top edge rising to the right, where it's tallest and
            // ends in a corner; the bottom scalloped into the two lenses,
            // the left one shallower; and the strap tapering off past the
            // head on the left. Drawn level over the eye whites, then turned
            // to the eyes' own slant and a little more.
            if (eyes.length < 2) return;
            const whites = [...rig.querySelectorAll(".eyeball")].map(e => [+e.getAttribute("cx"), +e.getAttribute("cy"), +e.getAttribute("r")]);
            const [L, R] = (whites.length === 2 ? whites : eyes).slice().sort((a, b) => a[0] - b[0]);
            const xm = (L[0] + R[0]) / 2, ym = (L[1] + R[1]) / 2;
            const tilt = Math.atan2(R[1] - L[1], R[0] - L[0]) * 180 / Math.PI - 14;
            const r = Math.max(L[2], R[2]), hL = r * 1.25 + 2, hR = r * 1.7 + 2, top = ym - hR * .4, c = Math.min(5, r * .5);
            const x0 = L[0] - L[2] - 2, x1 = R[0] + R[2] + 3, bL = top + hL, bR = top + hR, mid = (L[0] + R[0]) / 2;
            const d = `M${x0} ${top + 2.5} L${x1} ${top} L${x1 - 2} ${bR - c} Q${x1 - 2} ${bR} ${x1 - 2 - c} ${bR} H${mid + 3} ` +
                      `Q${mid} ${bR} ${mid} ${bR - 4} Q${mid} ${bL} ${mid - 3} ${bL} H${x0 + c} Q${x0} ${bL} ${x0} ${bL - c} Z`;
            const onDark = !!rig.querySelector(".face");       // the robot's screen: a pale edge so they show
            wear.setAttribute("transform", `rotate(${tilt.toFixed(1)} ${xm} ${ym})`);
            // Nothing of the eyes shows past them - whites, pupils, lids.
            rig.querySelectorAll(".eyeball, .eye, .lid").forEach(e => e.setAttribute("style", "display:none"));
            wear.append(path(d, "#111", "", onDark ? {stroke: "#aab3c2", "stroke-width": 1.2} : {}),
                        path(`M${x0} ${top + 3.5} l -16 -2`, "none", "", {stroke: "#111", "stroke-width": 3, "stroke-linecap": "round"}));
        } else if (kind === "tophat") {
            wear.append(rect(headX - 19, headTop - 1, 38, 5, INK, 2), rect(headX - 12, headTop - 27, 24, 27, INK, 2),
                        rect(headX - 12, headTop - 9, 24, 4, "#c0392b"));
        } else if (kind === "crown") {
            const y = headTop + 2;
            wear.append(node("polygon", {points: `${headX - 16},${y} ${headX - 16},${y - 16} ${headX - 8},${y - 8} ${headX},${y - 18} ${headX + 8},${y - 8} ${headX + 16},${y - 16} ${headX + 16},${y}`, fill: "#f2c14e"}),
                        circle(headX - 8, y - 4, 2, "#e74c3c"), circle(headX, y - 5, 2, "#3498db"), circle(headX + 8, y - 4, 2, "#2ecc71"));
        } else if (kind === "bow") {
            if (!m) return;
            const x = +m[1] + 11, y = +m[2] + 17;
            wear.append(node("polygon", {points: `${x},${y} ${x - 10},${y - 5} ${x - 10},${y + 5}`, fill: "#c0392b"}),
                        node("polygon", {points: `${x},${y} ${x + 10},${y - 5} ${x + 10},${y + 5}`, fill: "#c0392b"}),
                        circle(x, y, 2.6, "#922b21"));
        }
        rig.append(wear);
    }

    /* A colour of the person's own ("custom", skins.py): the species' main
       colour becomes it, and the colours drawn darker alongside become a
       darker shade of it - the same ones its own colour skin swaps. */
    /* Closed eyes for sleeping: one relaxed, downward curve where each eye
       white is, drawn in the white's own outline colour and shown only
       asleep, when sprites.css hides the whites and pupils. A pet with no
       whites (the robot's eyes are drawn as arcs) keeps its own eyes and
       narrows them instead. */
    function lids(rig) {
        const whites = [...rig.querySelectorAll(".eyeball")];
        if (!whites.length) return;
        for (const w of whites) {
            const x = +w.getAttribute("cx"), y = +w.getAttribute("cy"), r = +w.getAttribute("r");
            const lid = path(`M${x - r} ${y - r * .1} q ${r} ${r * .75} ${2 * r} 0`, "none", "lid",
                             {stroke: w.getAttribute("stroke") || INK, "stroke-width": Math.min(3.2, Math.max(2.2, r * .26)),
                              "stroke-linecap": "round"});
            w.parentNode.insertBefore(lid, w);
        }
        rig.classList.add("has-lids");
    }

    function shade(hex, by) {
        const n = parseInt(hex.slice(1), 16);
        const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => Math.max(0, Math.min(255, Math.round(v * by))));
        return "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
    }

    const luma = hex => { const n = parseInt(hex.slice(1), 16); return .299 * ((n >> 16) & 255) + .587 * ((n >> 8) & 255) + .114 * (n & 255); };

    function customColours(species, colour) {
        const own = RECOLOUR[species] || {};
        const swaps = Object.keys(own).length ? Object.keys(own[Object.keys(own)[0]]) : [];
        const map = {};
        // The other colours keep their relation to the main one: a lighter
        // one (the flame's core) stays lighter, a darker one darker.
        swaps.forEach((from, i) => {
            if (i === 0) { map[from] = colour; return; }
            const lighter = luma(from) > luma(swaps[0]);
            map[from] = shade(colour, lighter ? (i === 1 ? 1.45 : 1.7) : (i === 1 ? .78 : .62));
        });
        return map;
    }

    function render(holder, species, skin = "classic", colour = "#7c5cff") {
        if (!DRAW[species]) species = "blob";
        if (!/^#[0-9a-fA-F]{6}$/.test(colour || "")) colour = "#7c5cff";
        const stamp = `${species}|${skin}|${skin === "custom" ? colour : ""}`;
        if (holder.dataset.stamp === stamp && holder.firstChild) return;
        holder.dataset.species = species;
        holder.dataset.skin = skin;
        holder.dataset.stamp = stamp;
        const svg = node("svg", {viewBox: "0 0 130 100", xmlns: NS, "aria-hidden": "true"});
        const rig = g("rig", []);
        const [headX, headTop] = DRAW[species](rig);
        lids(rig);
        svg.append(shadow(), rig, ...fx(headX, headTop));
        const colours = skin === "custom" ? customColours(species, colour) : (RECOLOUR[species] || {})[skin];
        if (colours) recolour(svg, colours);
        else if (skin !== "classic") accessory(svg, rig, skin, headX, headTop);
        holder.replaceChildren(svg);
    }

    return {render, SPECIES: Object.keys(DRAW), NAMES};
})();
