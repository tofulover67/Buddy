/*
 * The DAW's instruments (they began as Audio Assistant's Keys tab): Buddy's
 * own, made from oscillators and filters (Web Audio) like Sound Design's
 * sounds - nothing to download, nobody's samples. Each instrument is a voice: a note started at a time, stopped at
 * another, ringing out with its release.
 *
 * The same voices play live (the on-screen and computer keyboard, the
 * recorder's playback) and offline (a take rendered to place on the
 * timeline), through the same chain - the instrument's tone, the room, the
 * level and a limiter - so a take placed sounds like it played.
 *
 *   Instruments.KINDS[name]          {label, about, defaults: {attack, release, ...}, voice}
 *   Instruments.PARAMS               the shared settings: tone, attack, release, room, width, level
 *   Instruments.live                 {noteOn(midi, vel, at?), noteOff(midi, at?), play(midi, vel, at, dur), allOff(), now()}
 *   Instruments.setup(name, values)  the instrument and its settings for live playing
 *   await Instruments.render(name, values, notes, seconds)    an AudioBuffer: notes [{midi, vel, t, dur}] (seconds)
 *   Instruments.click(at, accent)    the metronome
 *   Instruments.chain(ctx, p, dest)  the chain voices go into (engine.js builds one a track)
 */
"use strict";

const Instruments = (() => {
    const RATE = 48000;
    const hz = midi => 440 * Math.pow(2, (midi - 69) / 12);
    const gain = (ctx, value = 1) => { const g = ctx.createGain(); g.gain.value = value; return g; };
    const filter = (ctx, type, freq, q = 0.7) => {
        const f = ctx.createBiquadFilter();
        f.type = type;
        f.frequency.value = freq;
        f.Q.value = q;
        return f;
    };
    const osc = (ctx, type, freq, detune = 0) => {
        const o = ctx.createOscillator();
        o.type = type;
        o.frequency.value = freq;
        o.detune.value = detune;
        return o;
    };

    /* An envelope on a gain: up over `attack` to `peak`, down to `sustain`
       (a share of peak) over `decay`; stop() lets it go over `release`. */
    function envelope(g, t, peak, attack, decay, sustain) {
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(peak, t + Math.max(0.002, attack));
        g.gain.setTargetAtTime(peak * sustain, t + Math.max(0.002, attack), Math.max(0.01, decay / 3));
        // Let go from wherever the envelope is at that moment (held, not jumped to).
        return (when, release) => {
            g.gain.cancelAndHoldAtTime(when);
            g.gain.setTargetAtTime(0, when, Math.max(0.01, release / 4));
        };
    }

    /* A voice of oscillators into one envelope; `stop` returns when it's silent. */
    function voiceOf(ctx, out, oscs, t, vel, p, shape) {
        const g = gain(ctx, 0);
        const letGo = envelope(g, t, vel * shape.peak, p.attack, shape.decay, shape.sustain);
        if (shape.filter) shape.filter.connect(g);
        for (const o of oscs) { o.connect(shape.filter || g); o.start(t); }
        g.connect(out);
        return {
            stop(when) {
                const at = Math.max(when, t + 0.005);
                letGo(at, p.release);
                const end = at + p.release * 1.5 + 0.05;
                for (const o of oscs) o.stop(end);
                return end;
            },
        };
    }

    /* The tone setting (0-100) as a filter's cutoff: dark to bright. */
    const cutoff = (tone, lo, hi) => lo * Math.pow(hi / lo, tone / 100);

    const KINDS = {
        pad: {
            label: "Pad", about: "Warm and slow – a bed of sound for chords.",
            defaults: {attack: 0.45, release: 1.4, tone: 45, room: 45},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const lp = filter(ctx, "lowpass", cutoff(p.tone, 300, 6000), 1.2);
                lp.frequency.setValueAtTime(lp.frequency.value * 0.5, t);
                lp.frequency.linearRampToValueAtTime(cutoff(p.tone, 300, 6000), t + Math.max(0.1, p.attack * 1.5));
                return voiceOf(ctx, out, [osc(ctx, "sawtooth", f, -8), osc(ctx, "sawtooth", f, 8), osc(ctx, "triangle", f / 2)],
                               t, vel, p, {peak: 0.26, decay: 1, sustain: 0.85, filter: lp});
            },
        },
        pluck: {
            label: "Pluck", about: "Short and bright – plucked, then gone. For riffs and arpeggios.",
            defaults: {attack: 0.002, release: 0.25, tone: 60, room: 30},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const lp = filter(ctx, "lowpass", 300, 3);
                lp.frequency.setValueAtTime(cutoff(p.tone, 1200, 12000), t);
                lp.frequency.setTargetAtTime(300, t + 0.005, 0.12);
                return voiceOf(ctx, out, [osc(ctx, "sawtooth", f), osc(ctx, "square", f, 5)], t, vel, p,
                               {peak: 0.25, decay: 1.4, sustain: 0.001, filter: lp});
            },
        },
        keys: {
            label: "Electric piano", about: "A soft tine with a bell on top – for chords and melodies.",
            defaults: {attack: 0.003, release: 0.5, tone: 50, room: 25},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const carrier = osc(ctx, "sine", f);
                const mod = osc(ctx, "sine", f);
                const depth = gain(ctx, 0);
                depth.gain.setValueAtTime(f * (0.5 + p.tone / 50) * vel, t);
                depth.gain.setTargetAtTime(f * 0.15, t + 0.002, 0.25);
                mod.connect(depth);
                depth.connect(carrier.frequency);
                mod.start(t);
                const v = voiceOf(ctx, out, [carrier, osc(ctx, "sine", f * 2)], t, vel, p, {peak: 0.3, decay: 2.2, sustain: 0.25});
                const stop = v.stop;
                v.stop = when => { const end = stop(when); mod.stop(end); return end; };
                return v;
            },
        },
        bass: {
            label: "Bass", about: "Round and low, with a little bite as each note starts.",
            defaults: {attack: 0.004, release: 0.12, tone: 35, room: 5},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const lp = filter(ctx, "lowpass", 200, 4);
                lp.frequency.setValueAtTime(cutoff(p.tone, 400, 4000), t);
                lp.frequency.setTargetAtTime(cutoff(p.tone, 150, 900), t + 0.01, 0.08);
                return voiceOf(ctx, out, [osc(ctx, "sawtooth", f), osc(ctx, "square", f / 2)], t, vel, p,
                               {peak: 0.32, decay: 0.6, sustain: 0.7, filter: lp});
            },
        },
        bell: {
            label: "Bell", about: "Glassy and ringing – out of tune in the way a bell is.",
            defaults: {attack: 0.002, release: 2.5, tone: 55, room: 50},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const carrier = osc(ctx, "sine", f);
                const mod = osc(ctx, "sine", f * 3.5);
                const depth = gain(ctx, 0);
                depth.gain.setValueAtTime(f * (1 + p.tone / 40) * vel, t);
                depth.gain.setTargetAtTime(0, t + 0.002, 1.2);
                mod.connect(depth);
                depth.connect(carrier.frequency);
                mod.start(t);
                const v = voiceOf(ctx, out, [carrier], t, vel, p, {peak: 0.32, decay: 4, sustain: 0.001});
                const stop = v.stop;
                v.stop = when => { const end = stop(Math.max(when, t + 2)); mod.stop(end); return end; };
                return v;
            },
        },
        lead: {
            label: "Supersaw", about: "Wide and bright – a stack of saws for big leads and hooks.",
            defaults: {attack: 0.01, release: 0.35, tone: 65, room: 35},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const lp = filter(ctx, "lowpass", cutoff(p.tone, 800, 14000), 0.8);
                const oscs = [-24, -12, -5, 0, 5, 12, 24].map(c => osc(ctx, "sawtooth", f, c * (0.5 + p.width / 200)));
                return voiceOf(ctx, out, oscs, t, vel, p, {peak: 0.15, decay: 0.8, sustain: 0.8, filter: lp});
            },
        },
        choir: {
            label: "Choir", about: "An 'ah' from no one in particular – voices made of filters.",
            defaults: {attack: 0.3, release: 0.9, tone: 50, room: 55},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const src = [osc(ctx, "sawtooth", f, -6), osc(ctx, "sawtooth", f, 6)];
                const mix = gain(ctx, 1);
                const vib = osc(ctx, "sine", 5.2);
                const vibDepth = gain(ctx, 9);
                vib.connect(vibDepth);
                for (const o of src) vibDepth.connect(o.detune);
                vib.start(t);
                // An "ah": three formants, a little brighter with the tone.
                const lift = 0.85 + p.tone / 330;
                for (const [fc, q, lvl] of [[700, 8, 1], [1220, 10, 0.6], [2600, 12, 0.3]]) {
                    const band = filter(ctx, "bandpass", fc * lift, q);
                    band.connect(gain(ctx, lvl * 4)).connect(mix);
                    for (const o of src) o.connect(band);
                }
                const g = gain(ctx, 0);
                const letGo = envelope(g, t, vel * 0.3, p.attack, 1, 0.9);
                mix.connect(g);
                g.connect(out);
                for (const o of src) o.start(t);
                return {stop(when) {
                    const at = Math.max(when, t + 0.005);
                    letGo(at, p.release);
                    const end = at + p.release * 1.5 + 0.05;
                    for (const o of src) o.stop(end);
                    vib.stop(end);
                    return end;
                }};
            },
        },
        sub: {
            label: "Sub", about: "A pure low sine – felt more than heard. For bass lines under the music.",
            defaults: {attack: 0.005, release: 0.15, tone: 20, room: 0},
            voice(ctx, out, midi, vel, t, p) {
                const f = hz(midi);
                const shaper = ctx.createWaveShaper();
                const k = 1 + p.tone / 12, n = 1024, curve = new Float32Array(n);
                for (let i = 0; i < n; i++) { const x = i / (n - 1) * 2 - 1; curve[i] = Math.tanh(k * x) / Math.tanh(k); }
                shaper.curve = curve;
                return voiceOf(ctx, out, [osc(ctx, "sine", f)], t, vel, p, {peak: 0.45, decay: 0.5, sustain: 0.9, filter: shaper});
            },
        },
    };

    const PARAMS = [
        {key: "tone", label: "Tone", min: 0, max: 100, unit: "%", hint: "Darker or brighter"},
        {key: "attack", label: "Attack", min: 0.002, max: 2, step: 0.001, unit: "s", log: true, hint: "How long each note takes to come in"},
        {key: "release", label: "Release", min: 0.02, max: 4, step: 0.01, unit: "s", log: true, hint: "How long each note rings after you let go"},
        {key: "room", label: "Room", min: 0, max: 100, unit: "%"},
        {key: "width", label: "Width", min: 0, max: 100, unit: "%"},
        {key: "level", label: "Level", min: -24, max: 0, step: 0.5, unit: "dB"},
    ];

    /* An instrument's settings: its own defaults, then what's been set, each in range. */
    function values(name, given) {
        const base = Object.assign({tone: 50, attack: 0.01, release: 0.3, room: 20, width: 50, level: -6}, KINDS[name].defaults);
        const out = {};
        for (const p of PARAMS) {
            const v = given && given[p.key];
            out[p.key] = Number.isFinite(v) ? Math.min(p.max, Math.max(p.min, v)) : base[p.key];
        }
        return out;
    }

    /* A room to ring in (as synth.js's): noise dying away, duller as it goes. */
    function room(ctx, seconds) {
        const len = Math.ceil(seconds * ctx.sampleRate), buf = ctx.createBuffer(2, len, ctx.sampleRate);
        let a = 12345;
        const r = () => { a = (a * 1103515245 + 12345) & 0x7fffffff; return a / 0x7fffffff; };
        for (let c = 0; c < 2; c++) {
            const d = buf.getChannelData(c);
            let y = 0;
            for (let i = 0; i < len; i++) {
                const left = 1 - i / len, k = 0.03 + 0.6 * left * left;
                y += k * ((r() * 2 - 1) - y);
                d[i] = y * Math.pow(left, 2.5);
            }
        }
        return buf;
    }

    const rooms = new WeakMap();            // a context's room, made once (it's the same every time)

    /* The chain every voice goes into: its room, its width, its level, a limiter. Returns the input.
       And when they're set: drive, warmth before it all, and echo (p.echoTime seconds
       apart - a beat's share, from engine.js). */
    function chain(ctx, p, dest) {
        const input = gain(ctx);
        input.channelCount = 2;
        input.channelCountMode = "explicit";
        let from = input;
        if (p.drive > 0) {
            const shaper = ctx.createWaveShaper();
            const k = 1 + p.drive / 6, n = 1024, curve = new Float32Array(n);
            for (let i = 0; i < n; i++) { const x = i / (n - 1) * 2 - 1; curve[i] = Math.tanh(k * x) / Math.tanh(k); }
            shaper.curve = curve;
            shaper.oversample = "2x";
            input.connect(shaper);
            from = shaper;
        }
        const level = gain(ctx, Math.pow(10, p.level / 20));
        const limit = ctx.createDynamicsCompressor();
        limit.threshold.value = -6;
        limit.knee.value = 0;
        limit.ratio.value = 20;
        limit.attack.value = 0.003;
        limit.release.value = 0.15;
        // Width: a short delay on one side, mixed in as it widens.
        const split = ctx.createChannelSplitter(2), merge = ctx.createChannelMerger(2);
        const late = ctx.createDelay(0.05);
        late.delayTime.value = 0.012;
        const wet = gain(ctx, p.width / 100 * 0.6);
        from.connect(split);
        split.connect(merge, 0, 0);
        split.connect(merge, 1, 1);
        split.connect(late, 0);
        late.connect(wet).connect(merge, 0, 1);
        merge.connect(level);
        if (p.room > 0) {
            const verb = ctx.createConvolver();
            if (!rooms.has(ctx)) rooms.set(ctx, room(ctx, 2.6));
            verb.buffer = rooms.get(ctx);
            merge.connect(gain(ctx, p.room / 100 * 0.5)).connect(verb).connect(level);
        }
        if (p.echo > 0) {
            const delay = ctx.createDelay(2);
            delay.delayTime.value = Math.min(1.9, Math.max(0.05, p.echoTime || 0.375));
            const back = gain(ctx, 0.38), dull = filter(ctx, "lowpass", 3800, 0.5);
            merge.connect(delay);
            delay.connect(dull).connect(back).connect(delay);
            dull.connect(gain(ctx, p.echo / 100 * 0.55)).connect(level);
        }
        level.connect(limit).connect(dest);
        return input;
    }

    // ------------------------------------------------------------- live --

    let ctx = null, input = null, current = {name: "pad", p: values("pad")};
    const held = new Map();                 // midi -> [voice] while a key is down

    function context() {
        if (!ctx) ctx = new AudioContext({sampleRate: RATE, latencyHint: "interactive"});
        if (ctx.state === "suspended") ctx.resume();
        if (!input) input = chain(ctx, current.p, ctx.destination);
        return ctx;
    }

    function setup(name, given) {
        current = {name: KINDS[name] ? name : "pad", p: values(KINDS[name] ? name : "pad", given)};
        if (ctx) {
            if (input) input.disconnect();
            input = chain(ctx, current.p, ctx.destination);
        }
    }

    const live = {
        now: () => context().currentTime,
        noteOn(midi, vel = 0.8, at = null) {
            const c = context();
            const t = at === null ? c.currentTime : at;
            const v = KINDS[current.name].voice(c, input, midi, vel, t, current.p);
            const list = held.get(midi) || [];
            list.push(v);
            held.set(midi, list);
        },
        noteOff(midi, at = null) {
            const list = held.get(midi);
            if (!list || !list.length) return;
            const t = at === null ? context().currentTime : at;
            list.shift().stop(t);
            if (!list.length) held.delete(midi);
        },
        /* A note with its length known: the recorder's playback, the arpeggiator. */
        play(midi, vel, at, dur) {
            const c = context();
            const v = KINDS[current.name].voice(c, input, midi, vel, at, current.p);
            v.stop(at + dur);
        },
        allOff() {
            const t = ctx ? ctx.currentTime : 0;
            for (const list of held.values()) for (const v of list) v.stop(t);
            held.clear();
        },
    };

    /* The metronome: a short blip, higher on the bar's first beat. */
    function click(at, accent) {
        const c = context();
        const o = osc(c, "sine", accent ? 1760 : 1175);
        const g = gain(c, 0);
        g.gain.setValueAtTime(0, at);
        g.gain.linearRampToValueAtTime(accent ? 0.35 : 0.22, at + 0.002);
        g.gain.setTargetAtTime(0, at + 0.004, 0.015);
        o.connect(g).connect(c.destination);
        o.start(at);
        o.stop(at + 0.12);
    }

    /* A take, rendered whole: notes [{midi, vel, t, dur}] in seconds, the
       buffer `seconds` long (its release and room ring on past the last note
       up to that). */
    async function render(name, given, notes, seconds) {
        const p = values(name, given);
        const off = new OfflineAudioContext(2, Math.max(1, Math.ceil(seconds * RATE)), RATE);
        const into = chain(off, p, off.destination);
        for (const n of notes) KINDS[name].voice(off, into, n.midi, n.vel, n.t, p).stop(n.t + n.dur);
        const buffer = await off.startRendering();
        // The limiter catches a little late: never past -1 dBFS on the timeline.
        let peak = 0;
        for (let c = 0; c < buffer.numberOfChannels; c++) {
            const d = buffer.getChannelData(c);
            for (let i = 0; i < d.length; i++) peak = Math.max(peak, Math.abs(d[i]));
        }
        const most = Math.pow(10, -1 / 20);
        if (peak > most) {
            for (let c = 0; c < buffer.numberOfChannels; c++) {
                const d = buffer.getChannelData(c);
                for (let i = 0; i < d.length; i++) d[i] *= most / peak;
            }
        }
        return buffer;
    }

    return {RATE, KINDS, PARAMS, values, setup, live, click, render, hz, chain, room,
            tail: (name, given) => values(name, given).release * 1.5 + (values(name, given).room > 0 ? 2.6 : 0) + 0.1};
})();
