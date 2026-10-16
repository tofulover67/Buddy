/*
 * The DAW's drum machine: kits made of oscillators and noise (Web Audio), as
 * the instruments are - nobody's samples. A kit is eight sounds, one a row of
 * the step sequencer and a note on a drum track (ROWS: its note numbers are
 * General MIDI's, so a drum clip reads the same anywhere).
 *
 *   Drums.ROWS                       [{m, name}] - Kick, Snare, Clap, Closed hat, Open hat, Low tom, High tom, Rim
 *   Drums.KITS[name]                 {label, about, defaults: {tune, decay, tone, ...}}
 *   Drums.hit(ctx, out, kit, row, vel, t, values)    one hit, at t (the context's time)
 */
"use strict";

const Drums = (() => {
    const ROWS = [
        {m: 36, name: "Kick"}, {m: 38, name: "Snare"}, {m: 39, name: "Clap"}, {m: 42, name: "Closed hat"},
        {m: 46, name: "Open hat"}, {m: 45, name: "Low tom"}, {m: 50, name: "High tom"}, {m: 37, name: "Rim"},
    ];

    /* Each kit's own character: its sounds' pitches and lengths (seconds). */
    const KITS = {
        tight: {
            label: "Tight", about: "Short and punchy – a modern electronic kit.",
            defaults: {tune: 0, decay: 100, tone: 55, drive: 10, room: 12, echo: 0, width: 30},
            kick: {from: 170, to: 50, drop: 0.03, len: 0.32, click: 0.5},
            snare: {f: 200, len: 0.16, noise: 0.9}, clap: {len: 0.16}, hat: {len: 0.045, open: 0.32},
            tom: {low: 105, high: 165, len: 0.28}, rim: {f: 820, len: 0.035},
        },
        boom: {
            label: "Boom", about: "A long, booming kick and a snappy snare – the classic drum machine.",
            defaults: {tune: 0, decay: 100, tone: 45, drive: 20, room: 18, echo: 0, width: 25},
            kick: {from: 120, to: 44, drop: 0.07, len: 1.1, click: 0.25},
            snare: {f: 180, len: 0.22, noise: 1.1}, clap: {len: 0.22}, hat: {len: 0.06, open: 0.45},
            tom: {low: 85, high: 130, len: 0.45}, rim: {f: 760, len: 0.04},
        },
        trailer: {
            label: "Trailer", about: "Huge toms and hits in a big room – for builds and trailers.",
            defaults: {tune: 0, decay: 100, tone: 40, drive: 25, room: 55, echo: 10, width: 60},
            kick: {from: 95, to: 36, drop: 0.12, len: 1.6, click: 0.6},
            snare: {f: 160, len: 0.5, noise: 1.4}, clap: {len: 0.35}, hat: {len: 0.08, open: 0.6},
            tom: {low: 62, high: 92, len: 1.1}, rim: {f: 600, len: 0.08},
        },
    };

    const noises = new WeakMap();
    function noise(ctx) {
        if (!noises.has(ctx)) {
            const len = ctx.sampleRate, buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
            let a = 2463534242;
            for (let i = 0; i < len; i++) { a ^= a << 13; a ^= a >>> 17; a ^= a << 5; d[i] = ((a >>> 0) / 4294967296) * 2 - 1; }
            noises.set(ctx, buf);
        }
        const src = ctx.createBufferSource();
        src.buffer = noises.get(ctx);
        src.loop = true;
        return src;
    }

    const env = (ctx, t, peak, len, attack = 0.001) => {
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(peak, t + attack);
        g.gain.exponentialRampToValueAtTime(0.0001, t + attack + Math.max(0.01, len));
        return g;
    };
    const filter = (ctx, type, f, q = 0.7) => { const n = ctx.createBiquadFilter(); n.type = type; n.frequency.value = f; n.Q.value = q; return n; };

    function tone(ctx, out, type, from, to, drop, len, peak, t) {
        const o = ctx.createOscillator();
        o.type = type;
        o.frequency.setValueAtTime(from, t);
        o.frequency.exponentialRampToValueAtTime(Math.max(20, to), t + Math.max(0.005, drop));
        const g = env(ctx, t, peak, len);
        o.connect(g).connect(out);
        o.start(t);
        o.stop(t + len + 0.05);
    }

    function hiss(ctx, out, t, len, peak, chain, offset = 0) {
        const n = noise(ctx);
        const g = env(ctx, t, peak, len);
        let into = g;
        for (const f of chain) { f.connect(into); into = f; }
        n.connect(into);
        g.connect(out);
        n.start(t, offset % 0.9);
        n.stop(t + len + 0.05);
    }

    /* One hit of row `row` (ROWS' index) at t: vel 0-1, values the kit's settings. */
    function hit(ctx, out, kitName, row, vel, t, v) {
        const kit = KITS[kitName] || KITS.tight;
        const pitch = Math.pow(2, (v.tune || 0) / 12), long = (v.decay || 100) / 100, bright = (v.tone ?? 50) / 100;
        const peak = Math.max(0.05, Math.min(1, vel));
        switch (ROWS[row] ? ROWS[row].m : 36) {
        case 36: {
            const k = kit.kick;
            tone(ctx, out, "sine", k.from * pitch, k.to * pitch, k.drop, k.len * long, 0.95 * peak, t);
            hiss(ctx, out, t, 0.012, k.click * peak * (0.4 + bright), [filter(ctx, "highpass", 1500 + bright * 3000)]);
            break;
        }
        case 38: {
            const s = kit.snare;
            tone(ctx, out, "triangle", s.f * 1.6 * pitch, s.f * pitch, 0.04, 0.12 * long, 0.5 * peak, t);
            hiss(ctx, out, t, s.len * long, 0.55 * s.noise * peak,
                 [filter(ctx, "highpass", 900), filter(ctx, "lowpass", 4000 + bright * 8000)]);
            break;
        }
        case 39: {
            const len = kit.clap.len * long;
            for (let i = 0; i < 3; i++) {
                hiss(ctx, out, t + i * 0.011, 0.012, 0.45 * peak, [filter(ctx, "bandpass", 1100 + bright * 800, 1.4)], i * 0.13);
            }
            hiss(ctx, out, t + 0.033, len, 0.4 * peak, [filter(ctx, "bandpass", 1100 + bright * 800, 1.2)], 0.5);
            break;
        }
        case 42:
        case 46: {
            const len = (ROWS[row].m === 42 ? kit.hat.len : kit.hat.open) * long;
            hiss(ctx, out, t, len, 0.32 * peak, [filter(ctx, "highpass", 6000 + bright * 2500), filter(ctx, "peaking", 10000)],
                 ROWS[row].m * 0.01);
            break;
        }
        case 45:
        case 50: {
            const f = (ROWS[row].m === 45 ? kit.tom.low : kit.tom.high) * pitch;
            tone(ctx, out, "sine", f * 1.7, f, 0.09, kit.tom.len * long, 0.8 * peak, t);
            hiss(ctx, out, t, 0.05 * long, 0.12 * peak * (0.5 + bright), [filter(ctx, "bandpass", f * 4, 1)]);
            break;
        }
        default: {
            const r = kit.rim;
            tone(ctx, out, "triangle", r.f * pitch, r.f * 0.9 * pitch, 0.01, r.len * long, 0.45 * peak, t);
            hiss(ctx, out, t, r.len * 0.6, 0.25 * peak, [filter(ctx, "highpass", 2500 + bright * 2000)], 0.3);
        }
        }
    }

    /* The row a drum note plays (-1 for none). */
    const rowOf = midi => ROWS.findIndex(r => r.m === midi);

    /* The row a key plays on a drum track: the white keys from `base` (a C) in turn - C the
       kick, D the snare... - and the black keys none. */
    function keyRow(midi, base) {
        const rel = midi - base, oct = Math.floor(rel / 12);
        const white = [0, 2, 4, 5, 7, 9, 11].indexOf(((rel % 12) + 12) % 12);
        return white < 0 ? -1 : (((oct * 7 + white) % ROWS.length) + ROWS.length) % ROWS.length;
    }

    return {ROWS, KITS, hit, rowOf, keyRow};
})();
