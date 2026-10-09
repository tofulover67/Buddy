/* Animation > Titles: a composed title drawn as SVG, from the numbers titles/studio.py
   sends - each word's box with its letters' outline (Qt's own, render.outline: path
   data at 1000 px a letter, even-odd filled), each word's style and its animation
   state (titles_motion.js). It follows titles/render.py step for step, so the page
   shows what Buddy's renderer - and Resolve - would:

     bars first, under every word
     under (all sharp words): copies -> background box -> shadows -> glows -> outer strokes
     over  (all sharp words): fill (colour or gradient) -> inner strokes -> centre strokes
     words in motion blur: drawn whole on their own layer, blurred, on top

   Lengths in a style are shares of the letter size; a word turns and scales about its
   own centre; shadows, copies and gradients are laid out in frame space, as render.py's
   are. Opacity goes on every element, not the word's group: render.py's painter opacity
   applies to each draw, so an outline shows through a half-faded fill there too. Blurs
   take render.py's real deviation (three box blurs of radius at least 1).

   Checked against render.py pixel for pixel over every look (2026-10-05): letters as
   paths (SVG text strokes come out heavier), on a see-through layer of their own (the
   caller's .ti-title: no ClearType fringes).

   TitlesSvg.draw(c) -> markup for a viewBox of the frame. c: {frame, boxes, styles, bars,
   states (optional, at rest without), outScale (output px per frame px), id (unique
   prefix for the gradients and filters - several drawings share the page's ids)}; a
   kinetic title also kinetic (its blocks), camera (the clock's keys) and t (the moment,
   else the camera settled) - or screen: true for boxes already in the frame's px. */
"use strict";

const TitlesSvg = (() => {
    const PATH_PX = 1000;

    const n = v => (Math.round(v * 1000) / 1000).toString();
    const matrix = m => `matrix(${[m.a, m.b, m.c, m.d, m.e, m.f].map(n).join(" ")})`;

    /* "#rrggbb", "#rrggbbaa" or "rgba(r, g, b, a)" -> [css colour, alpha]: the alpha kept
       apart to multiply into an element's opacity, as render.py does. */
    function colour(value, fallback) {
        const text = String(value || fallback || "#ffffff").trim();
        const m = text.match(/^rgba?\(([^)]*)\)$/);
        if (m) {
            const p = m[1].split(",").map(Number);
            return [`rgb(${p[0]},${p[1]},${p[2]})`, p.length > 3 ? Math.max(0, Math.min(1, p[3])) : 1];
        }
        if (/^#[0-9a-fA-F]{8}$/.test(text)) return [text.slice(0, 7), parseInt(text.slice(7), 16) / 255];
        return [text, 1];
    }

    /* render.py's blur: three box blurs of radius max(1, ...) - a Gaussian of deviation
       sqrt(r(r+1)) in the pixels it blurs in. skipBelow: render.py doesn't blur a shadow
       or glow of deviation 0.3 px or less at all. */
    function boxSigma(sigma, skipBelow) {
        if (skipBelow && sigma <= 0.3) return 0;
        const r = Math.max(1, Math.round((Math.sqrt(4 * sigma * sigma + 1) - 1) / 2));
        return Math.sqrt(r * (r + 1));
    }

    function draw(c) {
        // A kinetic title: its words where the camera has them (titles_motion.js).
        if (c.kinetic && !c.screen) c = {...c, boxes: TitlesMotion.onScreen(c), screen: true};
        const [, fh] = c.frame;
        const idp = c.id || "t";
        const defs = [];
        let uid = 0;
        const filters = new Map();
        function blurFilter(sigma) {
            const key = sigma.toFixed(2);
            if (!filters.has(key)) {
                const id = `${idp}b${filters.size}`;
                filters.set(key, id);
                defs.push(`<filter id="${id}" filterUnits="userSpaceOnUse" x="-20000" y="-20000" width="40000" height="40000" color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="${key}"/></filter>`);
            }
            return filters.get(key);
        }

        const words = c.boxes.map((box, i) => {
            const st = (c.states && c.states[i]) || {};
            const s = st.scale ?? 1;
            const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
            const M = new DOMMatrix().translate(cx + (st.dx || 0) * fh, cy + (st.dy || 0) * fh)
                .rotate((box.rot || 0) + (st.rot || 0)).scale(s, s).translate(-cx, -cy);
            // The letters' ink box in frame px, where the word is now (render.py's path.boundingRect()).
            const ink = box.ink || [0, -0.7, box.w / box.size, 0];
            const pts = [[ink[0], ink[1]], [ink[2], ink[1]], [ink[0], ink[3]], [ink[2], ink[3]]]
                .map(([x, y]) => M.transformPoint(new DOMPoint(box.x + x * box.size, box.baseline + y * box.size)));
            const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
            return {
                box, style: c.styles[i] || {}, M, size: box.size * s,
                alpha: st.alpha ?? 1, spread: st.spread ?? 1, pulse: st.pulse || 0, blur: (st.blur || 0) * box.size,
                rect: {x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys)},
            };
        });

        /* The word's letters with these paint attributes. Stroke widths and blur are given in
           frame px (in the word's own space) and divided by the path's scale here; a filter
           goes on a wrapper so its deviation stays in those px. */
        function letters(w, attrs, strokeWidth = 0, sigma = 0) {
            const k = w.box.size / PATH_PX;
            const sw = strokeWidth ? ` stroke-width="${n(strokeWidth / k)}" stroke-linejoin="round"` : "";
            const p = `<path transform="translate(${n(w.box.x)} ${n(w.box.baseline)}) scale(${n(k)})" fill-rule="evenodd" clip-rule="evenodd" d="${w.box.d || ""}" ${attrs}${sw}/>`;
            return sigma > 0 ? `<g filter="url(#${blurFilter(sigma)})">${p}</g>` : p;
        }
        const at = (w, ox, oy, inner) => `<g transform="translate(${n(ox)} ${n(oy)}) ${matrix(w.M)}">${inner}</g>`;
        const local = (w, inner) => `<g transform="${matrix(w.M)}">${inner}</g>`;
        const strokesOf = (style, pos) => (style.strokes || []).filter(k => (k.pos || "outer") === pos);
        const outerReach = style => strokesOf(style, "outer").reduce((a, k) => a + Number(k.w || 0), 0);

        function under(w, alpha) {
            const {style, box} = w;
            let out = "";
            for (const g of style.copies || []) {
                const cnt = Math.trunc(g.n ?? 3);
                const [sx, sy] = (g.step || [0, -0.5]).map(v => v * w.size);
                const a0 = g.alpha ?? 1, fade = g.fade ?? 1;
                const sides = g.both ? [1, -1] : [1];
                for (let k = cnt; k >= 1; k--) {
                    const [o, shown] = TitlesMotion.copyAt(g, k, w.spread, w.pulse);
                    const a = a0 * fade ** (k - 1) * shown;
                    if (a <= 0.003) continue;
                    for (const side of sides) {
                        let inner = "";
                        if (g.fill) { const [col, ca] = colour(g.fill); inner += letters(w, `fill="${col}" opacity="${n(alpha * a * ca)}"`); }
                        if (g.stroke) {
                            const [col, ca] = colour(g.stroke.colour, "#ffffff");
                            inner += letters(w, `fill="none" stroke="${col}" opacity="${n(alpha * a * ca)}"`, (g.stroke.w ?? 0.03) * box.size);
                        }
                        out += at(w, side * k * sx * o, side * k * sy * o, inner);
                    }
                }
            }
            if (style.bg) {
                const pad = (style.bg.pad ?? 0.15) * w.size, r = (style.bg.radius ?? 0.1) * w.size;
                const [col, ca] = colour(style.bg.colour, "#000000");
                out += `<rect x="${n(w.rect.x - pad)}" y="${n(w.rect.y - pad)}" width="${n(w.rect.w + 2 * pad)}" height="${n(w.rect.h + 2 * pad)}" rx="${n(r)}" fill="${col}" opacity="${n(alpha * ca)}"/>`;
            }
            const reach = outerReach(style);
            // A shadow or glow is cast from the letters grown by the outer strokes (+ spread):
            // filled, stroked at twice that reach with round joins, then blurred.
            const grown = (reachLetters, col, opacity, sigmaLocal) =>
                letters(w, `fill="${col}" ${reachLetters > 0 ? `stroke="${col}"` : ""} opacity="${n(opacity)}"`,
                        2 * reachLetters * box.size, sigmaLocal);
            const scale = w.size / box.size;
            for (const sh of style.shadows || []) {
                const ang = (sh.angle ?? 180) * Math.PI / 180, dist = (sh.dist ?? 0.05) * w.size;
                const [col, ca] = colour(sh.colour, "#000000");
                out += at(w, Math.sin(ang) * dist, -Math.cos(ang) * dist,
                          grown(reach + (sh.spread || 0), col, alpha * ca, boxSigma((sh.blur ?? 0.1) * w.size / 2, true) / scale));
            }
            for (const gl of style.glows || []) {
                const [col, ca] = colour(gl.colour, "#ffffff");
                for (let strength = gl.strength ?? 1; strength > 0; strength -= 1) {
                    out += local(w, grown(reach + (gl.spread || 0), col, alpha * ca * Math.min(strength, 1),
                                          boxSigma((gl.blur ?? 0.3) * w.size / 2, true) / scale));
                }
            }
            const outer = strokesOf(style, "outer");
            let acc = 0;
            const reaches = outer.map(k => (acc += Number(k.w || 0)));
            for (let i = outer.length - 1; i >= 0; i--) {
                const [col, ca] = colour(outer[i].colour, "#000000");
                out += local(w, letters(w, `fill="none" stroke="${col}" opacity="${n(alpha * ca)}"`, 2 * reaches[i] * box.size));
            }
            return out;
        }

        // The space a fill's userSpaceOnUse gradient is read in: the word's and its path's
        // own placement (a path's transform attribute sets its user space).
        const gradSpace = w => w.M.multiply(new DOMMatrix().translate(w.box.x, w.box.baseline).scale(w.box.size / PATH_PX));

        function over(w, alpha) {
            const {style, box} = w;
            let out = "";
            if ((style.fill ?? "#ffffff") !== "none" || style.gradient) {
                let fill, fa = 1;
                const g = style.gradient;
                if (g && g.stops && g.stops.length) {
                    // In frame space over the ink box, as render.py's _fill_brush.
                    const a = (g.angle ?? 90) * Math.PI / 180, dx = Math.sin(a), dy = -Math.cos(a);
                    const half = Math.abs(w.rect.w / 2 * dx) + Math.abs(w.rect.h / 2 * dy);
                    const ccx = w.rect.x + w.rect.w / 2, ccy = w.rect.y + w.rect.h / 2;
                    const id = `${idp}g${uid++}`;
                    defs.push(`<linearGradient id="${id}" gradientUnits="userSpaceOnUse" gradientTransform="${matrix(gradSpace(w).inverse())}" ` +
                        `x1="${n(ccx - dx * half)}" y1="${n(ccy - dy * half)}" x2="${n(ccx + dx * half)}" y2="${n(ccy + dy * half)}">` +
                        g.stops.map(([o, col]) => { const [cc, ca] = colour(col); return `<stop offset="${o}" stop-color="${cc}" stop-opacity="${ca}"/>`; }).join("") +
                        `</linearGradient>`);
                    fill = `url(#${id})`;
                } else [fill, fa] = colour(style.fill, "#ffffff");
                out += letters(w, `fill="${fill}" opacity="${n(alpha * fa)}"`);
            }
            const inner = strokesOf(style, "inner");
            if (inner.length) {
                const id = `${idp}c${uid++}`;
                defs.push(`<clipPath id="${id}">${letters(w, "")}</clipPath>`);
                for (const k of inner) {
                    const [col, ca] = colour(k.colour, "#000000");
                    out += letters(w, `fill="none" stroke="${col}" clip-path="url(#${id})" opacity="${n(alpha * ca)}"`, 2 * (k.w || 0) * box.size);
                }
            }
            for (const k of strokesOf(style, "center")) {
                const [col, ca] = colour(k.colour, "#000000");
                out += letters(w, `fill="none" stroke="${col}" opacity="${n(alpha * ca)}"`, (k.w || 0) * box.size);
            }
            return local(w, out);
        }

        let body = "";
        const alphaOf = Object.fromEntries(words.map(w => [w.box.id, w.alpha]));
        for (const bar of c.bars || []) {
            const a = alphaOf[bar.first] ?? 1;
            if (a > 0.001) {
                const [col, ca] = colour(bar.colour, "#000000");
                body += `<rect x="${n(bar.x)}" y="${n(bar.y)}" width="${n(bar.w)}" height="${n(bar.h)}" fill="${col}" opacity="${n(a * ca)}"/>`;
            }
        }
        const os = c.outScale || 1;
        const shown = words.filter(w => w.alpha > 0.001);
        const sharp = shown.filter(w => w.blur * os < 0.5);
        for (const w of sharp) body += under(w, w.alpha);
        for (const w of sharp) body += over(w, w.alpha);
        for (const w of shown) {
            if (sharp.includes(w)) continue;
            body += `<g opacity="${n(w.alpha)}" filter="url(#${blurFilter(boxSigma(w.blur * os / 2, false) / os)})">${under(w, 1)}${over(w, 1)}</g>`;
        }
        const [fw] = c.frame;
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${fw} ${fh}" preserveAspectRatio="xMidYMid meet">` +
            `<defs>${defs.join("")}</defs>${body}</svg>`;
    }

    return {draw, colour};
})();
