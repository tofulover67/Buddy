/* Animation > Titles: the clock played in the page - titles/timing.py's state(),
   apply_curve() and copy_at(), line for line, so a word moves here exactly as its
   Text+ keys will move it in Resolve. Python works out the clock (when each word
   starts, how long, its exit, its copies' fan-out and breaths) and sends it; this only
   says where a word is at a moment, so playing and scrubbing never wait on Python.
   tests/test_titles_web.py checks the two agree. Loaded before titles.js. */
"use strict";

const TitlesMotion = (() => {
    const REST = {alpha: 1, scale: 1, dx: 0, dy: 0, rot: 0, blur: 0, spread: 1, pulse: 0};
    const DIRECTIONS = {u: [0, -1], d: [0, 1], l: [-1, 0], r: [1, 0],
                        ul: [-0.7071, -0.7071], ur: [0.7071, -0.7071], dl: [-0.7071, 0.7071], dr: [0.7071, 0.7071]};
    const OPPOSITE = {u: "d", d: "u", l: "r", r: "l", ul: "dr", dr: "ul", ur: "dl", dl: "ur"};
    const CHANNELS = ["alpha", "scale", "move", "blur", "rot"];
    const CHANNEL_REST = {alpha: 1, scale: 1, move: 0, blur: 0, rot: 0};
    const MIN_SCALE = 0.001, COPY_TRAVEL = 9 / 30, COPY_LAG = 2 / 30, PULSE_IN = 0.15, PULSE_DIM = 0.3;
    let curves = {};                    // the built-in entrances' curves (timing.CURVES)

    const clamp = p => p <= 0 ? 0 : p >= 1 ? 1 : p;
    const easeOut = p => 1 - (1 - p) ** 3;

    function bezier(p0, p1, p2, p3, s) {
        const u = 1 - s;
        return u * u * u * p0 + 3 * u * u * s * p1 + 3 * u * s * s * p2 + s * s * s * p3;
    }

    /* A channel's value at progress t (timing.eval_keys). */
    function evalKeys(keys, t) {
        if (!keys || !keys.length) return 0;
        if (t <= keys[0].t) return keys[0].v;
        if (t >= keys[keys.length - 1].t) return keys[keys.length - 1].v;
        let i = 0;
        while (t > keys[i + 1].t) i++;
        const a = keys[i], b = keys[i + 1];
        if (a.step) return a.v;
        const t0 = a.t, t1 = b.t, x1 = t0 + a.ho[0], x2 = t1 + b.hi[0], span = t1 - t0;
        let s;
        if (Math.abs(x1 - (t0 + span / 3)) < 1e-9 && Math.abs(x2 - (t1 - span / 3)) < 1e-9) s = (t - t0) / span;
        else {
            let lo = 0, hi = 1;
            for (let n = 0; n < 40; n++) {
                const mid = (lo + hi) / 2;
                if (bezier(t0, x1, x2, t1, mid) < t) lo = mid; else hi = mid;
            }
            s = (lo + hi) / 2;
        }
        return bezier(a.v, a.v + a.ho[1], b.v + b.hi[1], b.v, s);
    }

    const curveOf = kind => curves[kind] || curves.pop || {ch: {}};

    function applyCurve(curve, p, direction = "u") {
        p = clamp(p);
        const ch = (curve && curve.ch) || {};
        const v = {};
        for (const name of CHANNELS) v[name] = ch[name] && ch[name].length ? evalKeys(ch[name], p) : CHANNEL_REST[name];
        const s = {...REST};
        s.alpha = Math.min(1, Math.max(0, v.alpha));
        s.scale = Math.max(v.scale, MIN_SCALE);
        if (v.move) {
            const [vx, vy] = DIRECTIONS[direction] || DIRECTIONS.u;
            s.dx = -vx * v.move;
            s.dy = -vy * v.move;
        }
        s.blur = Math.max(0, v.blur);
        s.rot = v.rot;
        return s;
    }

    const entrance = (kind, p, direction = "u") => applyCurve(curveOf(kind), p, direction);
    const own = word => word.curve || curveOf(word.kind);

    function motionState(word, t) {
        const out = word.exit;
        if (out && t >= out.at) {
            const p = out.len > 0 ? (t - out.at) / out.len : 1;
            if (out.curve) return applyCurve(out.curve, p, out.dir);      // drawn as it plays
            if (out.reverse) return applyCurve(own(word), 1 - p, out.dir);
            return entrance(out.kind, 1 - p, out.dir);
        }
        if (word.len <= 0) return t >= word.start ? {...REST} : applyCurve(own(word), 0, word.dir);
        return applyCurve(own(word), (t - word.start) / word.len, word.dir);
    }

    function spread(word, t) {
        const out = word.exit;
        if (out && t >= out.at) return out.len > 0 ? 1 - clamp((t - out.at) / out.len) : 0;
        const fan = word.spread;
        return clamp((t - fan.start) / fan.len);
    }

    function pulse(word, t) {
        const beat = word.pulse;
        const p = (t - beat.start) / beat.len;
        if (p <= 0 || p >= 1) return 0;
        return (1 - Math.cos(2 * Math.PI * beat.breaths * p)) / 2;
    }

    /* A word's state t seconds into the title (timing.state). */
    function state(word, t) {
        const s = motionState(word, t);
        if (word.spread) s.spread = spread(word, t);
        if (word.pulse) s.pulse = pulse(word, t);
        return s;
    }

    /* A curve set backwards in time: an entrance as the exit that undoes it - what an
       exit's built-in curve looks like drawn as it plays (at rest to gone). */
    function mirror(curve) {
        const out = {...(curve || {}), ch: {}};
        for (const [name, ks] of Object.entries((curve && curve.ch) || {})) {
            const n = ks.length;
            out.ch[name] = ks.map((k, i) => {
                const m = ks[n - 1 - i];
                const key = {t: 1 - m.t, v: m.v, hi: [-m.ho[0], m.ho[1]], ho: [-m.hi[0], m.hi[1]]};
                if (i < n - 1 && ks[n - 2 - i].step) key.step = true;
                return key;
            });
        }
        return out;
    }

    /* Copy k of a copies group: [share of its distance, share of its opacity] (timing.copy_at). */
    function copyAt(group, k, spr, pul = 0) {
        if (group.anim !== "spread") return [1, 1];
        let out = 1, shown = 1;
        if (spr < 1) {
            const n = Math.max(1, Math.trunc(group.n ?? 3));
            const travel = Number(group.travel ?? COPY_TRAVEL), lag = Number(group.lag ?? COPY_LAG);
            const q = travel > 0 ? clamp((spr * (travel + (n - 1) * lag) - (k - 1) * lag) / travel) : 1;
            out = easeOut(q);
            shown = clamp(q / 0.35);
        }
        if (pul > 0) {
            out *= 1 - (group.pulseIn ?? PULSE_IN) * pul;
            shown *= 1 - (group.pulseDim ?? PULSE_DIM) * pul;
        }
        return [out, shown];
    }

    /* ---------------------------------------------------------- kinetic --
       titles/kinetic.py's camera: where it looks at a moment, and each word's box as the
       frame shows it then. */
    const KINETIC_FILL = {each: 0.62, all: 0.8};
    const kEase = p => { p = clamp(p); return p < 0.5 ? 4 * p ** 3 : 1 - (-2 * p + 2) ** 3 / 2; };
    const mod = (a, m) => ((a % m) + m) % m;

    function cameraOn(block, which, frame) {
        const [fw, fh] = frame;
        const [x0, y0, x1, y1] = block[which === "all" ? "all" : "own"];
        const [a, b, c, d, e, f] = block.frame;
        const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
        const fill = KINETIC_FILL[which === "all" ? "all" : "each"];
        return {cx: a * mx + c * my + e, cy: b * mx + d * my + f, angle: block.angle,
                z: Math.min(fw * fill / Math.max(x1 - x0, 1e-9), fh * fill / Math.max(y1 - y0, 1e-9))};
    }

    function cameraAt(keys, t) {
        if (!keys || !keys.length) return {cx: 0, cy: 0, angle: 0, z: 1};
        if (t <= keys[0].t) return {...keys[0]};
        for (let i = 1; i < keys.length; i++) {
            const a = keys[i - 1], b = keys[i];
            if (t < b.t) {
                const span = b.t - a.t, u = span > 0 ? kEase((t - a.t) / span) : 1;
                const da = mod(b.angle - a.angle + 180, 360) - 180;
                return {t, cx: a.cx + (b.cx - a.cx) * u, cy: a.cy + (b.cy - a.cy) * u, angle: a.angle + da * u,
                        z: a.z * (b.z / a.z) ** u};
            }
        }
        return {...keys[keys.length - 1]};
    }

    function screenBox(box, cam, frame) {
        const [fw, fh] = frame, z = cam.z;
        const r = -cam.angle * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
        const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
        const px = cx - cam.cx, py = cy - cam.cy;
        const sx = c * z * px - s * z * py + fw / 2, sy = s * z * px + c * z * py + fh / 2;
        const w = box.w * z, h = box.h * z;
        return {...box, x: sx - w / 2, y: sy - h / 2, w, h, size: box.size * z, baseline: sy + (box.baseline - cy) * z,
                rot: (box.rot || 0) - cam.angle, track: (box.track || 0) * z, frameRot: (box.frameRot || 0) - cam.angle};
    }

    /* The camera of a drawing c ({kinetic, camera, frame, t}) - at c.t by its keys, else
       settled on the last block - or null for a title that isn't kinetic. */
    function cameraOf(c) {
        const kin = c.kinetic;
        if (!kin || !kin.blocks || !kin.blocks.length) return null;
        if (c.t != null && c.camera && c.camera.length) return cameraAt(c.camera, c.t);
        return cameraOn(kin.blocks[kin.blocks.length - 1], kin.camera, c.frame);
    }

    /* c's boxes in the frame's px: a kinetic title's where its camera has them. */
    function onScreen(c) {
        const cam = cameraOf(c);
        return cam ? c.boxes.map(b => screenBox(b, cam, c.frame)) : c.boxes;
    }

    return {REST, DIRECTIONS, CHANNELS, CHANNEL_REST, setCurves: c => { curves = c || {}; }, curveOf,
            evalKeys, applyCurve, entrance, state, copyAt, mirror, OPPOSITE,
            cameraOn, cameraAt, screenBox, cameraOf, onScreen};
})();
