/*
 * Sound Design's synth: Buddy's own built-in sounds, made from nothing but
 * oscillators, noise and filters (Web Audio) - no samples, nothing to
 * download, no one's licence. Each sound is rendered whole, offline
 * (OfflineAudioContext), and that one rendering is both what Play plays and
 * what Place sends to page.py (sounds.py writes it) - so what's heard is
 * exactly what lands on the timeline. Noise comes from a seeded generator:
 * the same settings and Variation always make the same sound.
 *
 * A sound is made for the song: its tempo (a riser's length in bars and its
 * flutter in note lengths, a whoosh's length in beats, the echo's time) and
 * its key (every tone, sub, ring and chord on the key's root, moved by
 * Transpose). Each has a sync point - the moment it's for: a riser's top, an
 * impact's hit, a whoosh's peak - which is what lands on the moment, not the
 * sound's start. Played backwards, the moment is turned round with it.
 *
 * Each sound's settings are in groups (params[].group). A layer's group has
 * a switch (a param with `toggle`, shown in the group's heading): off, the
 * layer is silent and keeps its settings. The last three groups are the
 * same for every sound: Colour (drive, lo-fi, punch, low and high cut,
 * width), Echo and reverb, and Output (level, transpose, fades, backwards).
 * The path: layers -> EQ -> drive -> width -> out, with the echo and reverb
 * sent from there. Then, on the rendering: backwards, the fades, and - with
 * it brought to full scale first, so how loud the layers were doesn't
 * matter - punch and lo-fi, and last its Level. Each sound has presets: starting points, a few settings
 * each over its defaults.
 *
 *   Synth.SOUNDS[kind]              {label, about, params, presets, length, build, prepare?}
 *   await Synth.render(kind, values, song, seed)   {buffer, sync, values}
 *   Synth.play(buffer, {from, loop, onEnd}) / Synth.stop() / Synth.position()
 *   Synth.grain(buffer, at)          a moment of it, for scrubbing
 *
 * song: {bpm, tonic (0 = C), mode} - when nothing has been listened to,
 * NO_SONG (120 BPM, C major).
 */
"use strict";

const Synth = (() => {
    const RATE = 48000;
    const NO_SONG = {bpm: 120, tonic: 0, mode: "major"};
    const NOTES = ["C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];

    const beatS = song => 60 / ((song && song.bpm) || NO_SONG.bpm);
    const hz = (pc, octave) => 440 * Math.pow(2, (pc - 9) / 12 + (octave - 4));
    /* The key's root low enough to be felt, high enough for small speakers to hint at: 40-80 Hz. */
    const subHz = pc => { let f = hz(pc, 1); while (f < 40) f *= 2; while (f >= 80) f /= 2; return f; };
    const st = n => Math.pow(2, n / 12);

    /* mulberry32: small, fast, and the same numbers for the same seed everywhere. */
    function rng(seed) {
        let a = seed >>> 0;
        return () => {
            a = (a + 0x6D2B79F5) | 0;
            let t = Math.imul(a ^ (a >>> 15), 1 | a);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    /* Stereo noise: white (every frequency alike), pink (each octave alike -
       softer, fuller) or brown (rumbling, mostly low). */
    function noise(ctx, seconds, seed, colour = "white") {
        const len = Math.max(1, Math.ceil(seconds * ctx.sampleRate));
        const buf = ctx.createBuffer(2, len, ctx.sampleRate);
        const r = rng(seed);
        for (let c = 0; c < 2; c++) {
            const d = buf.getChannelData(c);
            let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, last = 0;
            for (let i = 0; i < len; i++) {
                const w = r() * 2 - 1;
                if (colour === "pink") {           // Paul Kellet's filter
                    b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759;
                    b2 = 0.96900 * b2 + w * 0.1538520; b3 = 0.86650 * b3 + w * 0.3104856;
                    b4 = 0.55000 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.0168980;
                    d[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362) * 0.11;
                    b6 = w * 0.115926;
                } else if (colour === "brown") {
                    last = (last + 0.02 * w) / 1.02;
                    d[i] = last * 3.5;
                } else {
                    d[i] = w;
                }
            }
        }
        return buf;
    }

    /* A room to ring in: noise dying away over `seconds`, duller as it goes
       (a one-pole filter closing; `bright` 0-1 how slowly) - a reverb with
       no recording behind it. */
    function room(ctx, seconds, seed, bright = 0.5) {
        const len = Math.max(1, Math.ceil(seconds * ctx.sampleRate));
        const buf = ctx.createBuffer(2, len, ctx.sampleRate);
        const r = rng(seed + 101);
        const top = 0.15 + 0.85 * bright, bottom = 0.01 + 0.25 * bright;
        for (let c = 0; c < 2; c++) {
            const d = buf.getChannelData(c);
            let y = 0;
            for (let i = 0; i < len; i++) {
                const left = 1 - i / len;
                const k = bottom + (top - bottom) * left * left;
                y += k * ((r() * 2 - 1) - y);
                d[i] = y * Math.pow(left, 2.5);
            }
        }
        return buf;
    }

    const source = (ctx, buffer) => { const s = ctx.createBufferSource(); s.buffer = buffer; return s; };
    const filter = (ctx, type, freq, q = 0.7) => {
        const f = ctx.createBiquadFilter();
        f.type = type;
        f.frequency.value = freq;
        f.Q.value = q;
        return f;
    };
    const gain = (ctx, value = 1) => { const g = ctx.createGain(); g.gain.value = value; return g; };
    const chain = (...nodes) => { for (let i = 0; i + 1 < nodes.length; i++) nodes[i].connect(nodes[i + 1]); return nodes[nodes.length - 1]; };

    /* A sweep that seems to move evenly: exponential, from `a` to `b`. */
    function sweep(param, a, b, from, to) {
        param.setValueAtTime(Math.max(1e-4, a), from);
        param.exponentialRampToValueAtTime(Math.max(1e-4, b), Math.max(from + 1e-3, to));
    }

    /* `floor` to `peak` over [from, to], shaped by `power`: 1 a straight line,
       more holds back and builds late, less comes up early. */
    function rise(param, peak, from, to, power, floor = 0) {
        const n = 256, curve = new Float32Array(n);
        for (let i = 0; i < n; i++) curve[i] = floor + (peak - floor) * Math.pow(i / (n - 1), power);
        param.setValueCurveAtTime(curve, from, Math.max(1e-3, to - from));
    }
    /* `peak` down to 0 over [from, to] - `power` as rise(), mirrored. */
    function fall(param, peak, from, to, power) {
        const n = 256, curve = new Float32Array(n);
        for (let i = 0; i < n; i++) curve[i] = peak * Math.pow(1 - i / (n - 1), power);
        param.setValueCurveAtTime(curve, from, Math.max(1e-3, to - from));
    }
    /* A setting's 0-100 "shape" as a power: 0 -> 0.4 (early), 50 -> ~1.6, 100 -> 4.5 (late). */
    const power = v => 0.4 * Math.pow(4.5 / 0.4, v / 100);

    /* The intervals each chord choice plays, in the song's mode. */
    function chord(name, mode) {
        if (name === "fifth") return [0, 7];
        if (name === "octave") return [0, 12];
        if (name === "triad") return [0, mode === "minor" ? 3 : 4, 7];
        if (name === "stack") return [0, 7, 12];
        if (name === "sus") return [0, 5, 7];
        return [0];
    }

    /* Oscillators for each note, `voices` of them a note, spread in tune by
       `detune` cents and across the stereo by `spread` (0-1), into `dest`.
       Returns them, for their pitch to be moved. */
    function voices(ctx, freqs, wave, count, detune, spread, dest, stop) {
        const out = [];
        for (const f of freqs) {
            for (let v = 0; v < count; v++) {
                const pos = count === 1 ? 0 : v / (count - 1) * 2 - 1;     // -1 .. 1
                const osc = ctx.createOscillator();
                osc.type = wave;
                osc.frequency.value = f;
                osc.detune.value = pos * detune;
                const pan = ctx.createStereoPanner();
                pan.pan.value = pos * spread;
                chain(osc, pan, dest);
                osc.start(0);
                osc.stop(stop);
                out.push({osc, f});
            }
        }
        return out;
    }

    /* A wobble in pitch: an LFO on each oscillator's detune. */
    function vibrato(ctx, oscs, cents, rate, stop) {
        if (!cents) return;
        const lfo = ctx.createOscillator();
        lfo.frequency.value = rate;
        const depth = gain(ctx, cents);
        lfo.connect(depth);
        for (const {osc} of oscs) depth.connect(osc.detune);
        lfo.start(0);
        lfo.stop(stop);
    }

    /* Everything after a sound's layers: EQ, drive, width, then the echo and
       the reverb sent from it. Returns the node the layers go into. */
    function finish(ctx, p, song, seed, out) {
        const input = gain(ctx);
        input.channelCount = 2;
        input.channelCountMode = "explicit";
        const low = filter(ctx, "highpass", p.lowcut, 0.7);
        const high = filter(ctx, "lowpass", p.highcut, 0.7);
        let node = chain(input, low, high);
        if (p.drive > 0) {
            const shaper = ctx.createWaveShaper();
            const k = 1 + p.drive / 100 * 24, n = 2048, curve = new Float32Array(n);
            for (let i = 0; i < n; i++) { const x = i / (n - 1) * 2 - 1; curve[i] = Math.tanh(k * x) / Math.tanh(k); }
            shaper.curve = curve;
            shaper.oversample = "4x";
            node = chain(node, shaper, gain(ctx, 1 / (1 + p.drive / 100 * 1.5)));
        }
        // Width: each side's own channel and some of the other's - 0 is mono, 1 as it is, 2 wider.
        const w = p.width / 100, a = (1 + w) / 2, b = (1 - w) / 2;
        const split = ctx.createChannelSplitter(2), merge = ctx.createChannelMerger(2);
        const ll = gain(ctx, a), rr = gain(ctx, a), lr = gain(ctx, b), rl = gain(ctx, b);
        node.connect(split);
        split.connect(ll, 0); split.connect(lr, 0); split.connect(rr, 1); split.connect(rl, 1);
        ll.connect(merge, 0, 0); rl.connect(merge, 0, 0);
        rr.connect(merge, 0, 1); lr.connect(merge, 0, 1);
        merge.connect(out);
        if (p.space_on && p.echo > 0) {
            const send = gain(ctx, p.echo / 100);
            const delay = ctx.createDelay(8);
            delay.delayTime.value = Math.min(8, p.echo_time * beatS(song));
            const back = gain(ctx, p.echo_fb / 100);
            const dull = filter(ctx, "lowpass", 5000, 0.5);
            chain(merge, send, delay, dull, back, delay);
            dull.connect(out);
        }
        if (p.space_on && p.verb > 0) {
            const send = gain(ctx, p.verb / 100);
            const pre = ctx.createDelay(1);
            pre.delayTime.value = p.predelay / 1000;
            const verb = ctx.createConvolver();
            verb.normalize = true;
            verb.buffer = room(ctx, p.verb_size, seed, p.verb_tone / 100);
            chain(merge, send, pre, verb, out);
        }
        return input;
    }

    /* How long the echo and the reverb ring on after the sound itself. */
    function tail(p, song) {
        let t = 0.02;
        if (!p.space_on) return t;
        if (p.verb > 0) t = Math.max(t, p.predelay / 1000 + p.verb_size);
        if (p.echo > 0 && p.echo_fb > 0) {
            const repeats = Math.min(30, Math.ceil(Math.log(0.001) / Math.log(Math.max(0.01, p.echo_fb / 100))));
            t = Math.max(t, Math.min(12, repeats * p.echo_time * beatS(song)));
        }
        return t;
    }

    // ---------------------------------------------------------- settings --

    const WAVES = [["sine", "Sine wave"], ["triangle", "Triangle wave"], ["sawtooth", "Saw wave"], ["square", "Square wave"]];
    const COLOURS = [["white", "White noise"], ["pink", "Pink noise"], ["brown", "Brown noise"]];
    const CHORDS = [["root", "Root"], ["fifth", "Root and fifth"], ["octave", "Octave"], ["stack", "Root, fifth, octave"],
                    ["triad", "Chord in the key"], ["sus", "Suspended"]];
    const NOTE_LENGTHS = [[1, "1/4"], [2, "1/8"], [3, "1/8 triplet"], [4, "1/16"], [8, "1/32"]];
    /* A layer's switch, shown in its group's heading. */
    const toggle = (key, group, value = true) => ({key, group, toggle: true, value, label: "On"});

    /* Colour, Echo and reverb, and Output: the same for every sound, with that sound's defaults. */
    function shared(d = {}) {
        const or = (key, fallback) => (d[key] === undefined ? fallback : d[key]);
        return [
            {key: "drive", label: "Drive", group: "Colour", min: 0, max: 100, value: or("drive", 0), unit: "%"},
            {key: "lofi", label: "Lo-fi", group: "Colour", min: 0, max: 100, value: 0, unit: "%", hint: "Fewer steps of loudness – a gritty, digital edge"},
            {key: "punch", label: "Punch", group: "Colour", min: 0, max: 100, value: or("punch", 0), unit: "%", hint: "Squeezes it louder and denser"},
            {key: "lowcut", label: "Low cut", group: "Colour", min: 20, max: 800, value: 20, unit: "Hz", log: true},
            {key: "highcut", label: "High cut", group: "Colour", min: 1000, max: 20000, value: 20000, unit: "Hz", log: true},
            {key: "width", label: "Width", group: "Colour", min: 0, max: 200, value: 100, unit: "%"},
            toggle("space_on", "Echo and reverb"),
            {key: "echo", label: "Echo", group: "Echo and reverb", min: 0, max: 100, value: or("echo", 0), unit: "%"},
            {key: "echo_time", label: "Echo time", group: "Echo and reverb", choices: [[0.25, "1/16"], [0.5, "1/8"], [0.75, "Dotted 1/8"],
                [1, "1/4"], [1.5, "Dotted 1/4"], [2, "1/2"]], value: 0.75},
            {key: "echo_fb", label: "Echo repeats", group: "Echo and reverb", min: 0, max: 90, value: 35, unit: "%"},
            {key: "verb", label: "Reverb", group: "Echo and reverb", min: 0, max: 100, value: or("verb", 0), unit: "%"},
            {key: "verb_size", label: "Reverb size", group: "Echo and reverb", min: 0.3, max: 8, step: 0.1, value: or("verb_size", 2), unit: "s"},
            {key: "verb_tone", label: "Reverb brightness", group: "Echo and reverb", min: 0, max: 100, value: or("verb_tone", 50), unit: "%"},
            {key: "predelay", label: "Pre-delay", group: "Echo and reverb", min: 0, max: 200, value: 10, unit: "ms"},
            {key: "level", label: "Level", group: "Output", min: -24, max: 0, step: 0.5, value: or("level", -6), unit: "dB", hint: "How loud its loudest moment is"},
            {key: "transpose", label: "Transpose", group: "Output", min: -12, max: 12, value: 0, unit: "st", hint: "Every note moved up or down from the song's key"},
            {key: "fade_in", label: "Fade in", group: "Output", min: 0, max: 2000, value: 0, unit: "ms"},
            {key: "fade_out", label: "Fade out", group: "Output", min: 0, max: 4000, value: 0, unit: "ms"},
            {key: "backwards", label: "Backwards", group: "Output", toggleRow: true, value: false, hint: "The whole sound played in reverse – a riser becomes a downlifter"},
        ];
    }

    // --------------------------------------------------------------- sounds --

    const SOUNDS = {
        riser: {
            label: "Riser",
            about: "Builds up to the moment and stops on it – noise opening up, a tone climbing in the song's key, a flutter that speeds up with the beat.",
            params: [
                {key: "bars", label: "Length", group: "Timing", choices: [[0.5, "½ bar"], [1, "1 bar"], [2, "2 bars"], [4, "4 bars"], [8, "8 bars"]], value: 2},
                {key: "build", label: "Build-up", group: "Timing", min: 0, max: 100, value: 70, unit: "%", hint: "Low comes up early, high holds back and rushes in at the end"},
                {key: "start", label: "Starts from", group: "Timing", min: 0, max: 80, value: 0, unit: "%", hint: "How loud it already is at its start"},
                {key: "ending", label: "Ending", group: "Timing", choices: [["dead", "Stops – the room rings on"], ["all", "Everything stops"]], value: "dead"},
                toggle("air_on", "Noise"),
                {key: "air", label: "Level", group: "Noise", min: 0, max: 100, value: 70, unit: "%"},
                {key: "air_colour", label: "Colour", group: "Noise", choices: COLOURS, value: "white"},
                {key: "air_type", label: "Filter type", group: "Noise", choices: [["bandpass", "Band"], ["highpass", "High-pass"], ["lowpass", "Low-pass"]], value: "bandpass"},
                {key: "air_from", label: "Sweep from", group: "Noise", min: 60, max: 4000, value: 250, unit: "Hz", log: true},
                {key: "air_to", label: "Sweep to", group: "Noise", min: 500, max: 18000, value: 9000, unit: "Hz", log: true},
                {key: "air_q", label: "Resonance", group: "Noise", min: 0.3, max: 15, step: 0.1, value: 0.8, unit: "Q"},
                toggle("tone_on", "Tone"),
                {key: "tone", label: "Level", group: "Tone", min: 0, max: 100, value: 50, unit: "%"},
                {key: "wave", label: "Wave", group: "Tone", choices: WAVES, value: "sawtooth"},
                {key: "chord", label: "Chord", group: "Tone", choices: CHORDS, value: "root"},
                {key: "octave", label: "Octave", group: "Tone", min: -2, max: 2, value: 0, unit: "oct"},
                {key: "climb", label: "Climb", group: "Tone", min: -24, max: 24, value: 12, unit: "st", hint: "Below zero it falls instead"},
                {key: "voices", label: "Voices", group: "Tone", min: 1, max: 7, value: 3, unit: ""},
                {key: "detune", label: "Detune", group: "Tone", min: 0, max: 60, value: 14, unit: "ct"},
                {key: "spread", label: "Spread", group: "Tone", min: 0, max: 100, value: 60, unit: "%"},
                {key: "vib", label: "Vibrato", group: "Tone", min: 0, max: 100, value: 0, unit: "ct"},
                {key: "vib_rate", label: "Vibrato speed", group: "Tone", min: 1, max: 12, step: 0.5, value: 5.5, unit: "Hz"},
                {key: "tone_from", label: "Filter from", group: "Tone", min: 80, max: 4000, value: 300, unit: "Hz", log: true},
                {key: "tone_to", label: "Filter to", group: "Tone", min: 400, max: 18000, value: 7000, unit: "Hz", log: true},
                {key: "tone_q", label: "Resonance", group: "Tone", min: 0.5, max: 20, step: 0.1, value: 5, unit: "Q"},
                toggle("sub_on", "Sub", false),
                {key: "sub", label: "Level", group: "Sub", min: 0, max: 100, value: 60, unit: "%"},
                {key: "sub_climb", label: "Climb", group: "Sub", min: 0, max: 24, value: 12, unit: "st"},
                toggle("pulse_on", "Flutter"),
                {key: "pulse", label: "Depth", group: "Flutter", min: 0, max: 100, value: 35, unit: "%"},
                {key: "pulse_from", label: "Starts at", group: "Flutter", choices: NOTE_LENGTHS, value: 1},
                {key: "pulse_to", label: "Ends at", group: "Flutter", choices: NOTE_LENGTHS, value: 4},
                {key: "pulse_shape", label: "Pattern", group: "Flutter", choices: [["sine", "Smooth"], ["triangle", "Pointed"], ["square", "Chopped"]], value: "sine"},
                ...shared({level: -6, verb: 15, verb_size: 2}),
            ],
            presets: [
                {name: "Classic", values: {}},
                {name: "Cinematic", values: {bars: 4, build: 80, air: 55, air_colour: "pink", tone: 70, chord: "stack", voices: 5, detune: 22,
                    sub_on: true, sub: 60, pulse: 20, verb: 35, verb_size: 4}},
                {name: "Noise sweep", values: {tone_on: false, air: 100, air_type: "highpass", air_from: 200, air_to: 12000, air_q: 3,
                    pulse_on: false, verb: 20}},
                {name: "Tonal swell", values: {air: 15, tone: 90, wave: "triangle", chord: "triad", climb: 0, voices: 4, detune: 10, vib: 18,
                    pulse_on: false, build: 55, verb: 40, verb_size: 5}},
                {name: "Glitchy", values: {pulse: 90, pulse_shape: "square", pulse_from: 2, pulse_to: 8, lofi: 55, drive: 35, echo: 30, echo_time: 0.25}},
                {name: "Downlifter", values: {backwards: true, climb: 12, build: 60, verb: 30, verb_size: 3}},
            ],
            length: (p, song) => p.bars * 4 * beatS(song) + (p.ending === "all" ? 0.02 : tail(p, song)),
            build(ctx, p, song, seed, out) {
                const B = beatS(song), L = p.bars * 4 * B, shape = power(p.build), floor = p.start / 100;
                const end = gain(ctx);
                if (p.ending === "all") {               // the echo and room stop with it
                    end.gain.setValueAtTime(1, L - 0.008);
                    end.gain.linearRampToValueAtTime(0, L);
                }
                end.connect(out);
                const post = finish(ctx, p, song, seed, end);
                // Stops on the moment (a few ms, not a click); the room rings on after unless it all stops.
                const cut = gain(ctx, 1);
                cut.gain.setValueAtTime(1, L - 0.008);
                cut.gain.linearRampToValueAtTime(0, L);
                cut.connect(post);
                // Flutter: the whole of it through a gate, getting faster.
                const depth = p.pulse_on ? p.pulse / 100 : 0;
                const gate = gain(ctx, 1 - depth / 2);
                gate.connect(cut);
                if (depth > 0) {
                    const lfo = ctx.createOscillator();
                    lfo.type = p.pulse_shape;
                    sweep(lfo.frequency, p.pulse_from / B, p.pulse_to / B, 0, L);
                    chain(lfo, gain(ctx, depth / 2), gate.gain);
                    lfo.start(0);
                    lfo.stop(L + 0.05);
                }
                // Noise, through a filter opening up.
                if (p.air_on && p.air > 0) {
                    const air = source(ctx, noise(ctx, L + 0.05, seed, p.air_colour));
                    const band = filter(ctx, p.air_type, p.air_from, p.air_q);
                    sweep(band.frequency, p.air_from, p.air_to, 0, L);
                    const airGain = gain(ctx, 0);
                    rise(airGain.gain, p.air / 100, 0, L, shape, floor * p.air / 100);
                    chain(air, band, airGain, gate);
                    air.start(0);
                }
                // Tone: the key's notes, climbing.
                if (p.tone_on && p.tone > 0) {
                    const notes = chord(p.chord, song.mode).map(iv => hz(song.tonic, 3 + p.octave) * st(iv));
                    const lp = filter(ctx, "lowpass", p.tone_from, p.tone_q);
                    sweep(lp.frequency, p.tone_from, p.tone_to, 0, L);
                    const toneGain = gain(ctx, 0);
                    const peak = p.tone / 100 * 0.6 / Math.sqrt(notes.length * p.voices);
                    rise(toneGain.gain, peak, 0, L, shape, floor * peak);
                    const oscs = voices(ctx, notes, p.wave, p.voices, p.detune, p.spread / 100, lp, L + 0.05);
                    for (const {osc, f} of oscs) if (p.climb) sweep(osc.frequency, f, f * st(p.climb), 0, L);
                    vibrato(ctx, oscs, p.vib, p.vib_rate, L + 0.05);
                    chain(lp, toneGain, gate);
                }
                // Sub: a sine on the root, low down, climbing too.
                if (p.sub_on && p.sub > 0) {
                    const sub = ctx.createOscillator();
                    const f = subHz(song.tonic);
                    if (p.sub_climb) sweep(sub.frequency, f, f * st(p.sub_climb), 0, L); else sub.frequency.value = f;
                    const subGain = gain(ctx, 0);
                    rise(subGain.gain, p.sub / 100 * 0.8, 0, L, shape, floor * p.sub / 100 * 0.8);
                    chain(sub, subGain, gate);
                    sub.start(0);
                    sub.stop(L + 0.05);
                }
                return L;
            },
        },
        impact: {
            label: "Impact",
            about: "A hit for the moment itself – a sub boom tuned to the song's key, a body and a crack on top, ringing out in a room – with a reversed swell into it if you like.",
            params: [
                {key: "reverse", label: "Reverse swell", group: "Hit", choices: [[0, "None"], [0.5, "½ beat"], [1, "1 beat"], [2, "2 beats"], [4, "4 beats"]], value: 0,
                 hint: "The hit and its room played backwards, rushing into the moment"},
                {key: "reverse_level", label: "Swell level", group: "Hit", min: 0, max: 100, value: 60, unit: "%"},
                toggle("sub_on", "Sub"),
                {key: "sub", label: "Level", group: "Sub", min: 0, max: 100, value: 80, unit: "%"},
                {key: "sub_oct", label: "Octave", group: "Sub", min: -1, max: 1, value: 0, unit: "oct"},
                {key: "sub_drop", label: "Pitch drop", group: "Sub", min: 0, max: 36, value: 19, unit: "st"},
                {key: "sub_drop_time", label: "Drop time", group: "Sub", min: 10, max: 800, value: 150, unit: "ms", log: true},
                {key: "sub_decay", label: "Decay", group: "Sub", min: 0.1, max: 6, step: 0.05, value: 2.4, unit: "s"},
                {key: "sub_wave", label: "Wave", group: "Sub", choices: [["sine", "Sine wave"], ["triangle", "Triangle wave"]], value: "sine"},
                toggle("body_on", "Body"),
                {key: "body", label: "Level", group: "Body", min: 0, max: 100, value: 60, unit: "%"},
                {key: "body_colour", label: "Colour", group: "Body", choices: COLOURS, value: "white"},
                {key: "body_from", label: "Opens at", group: "Body", min: 300, max: 12000, value: 2500, unit: "Hz", log: true},
                {key: "body_to", label: "Closes to", group: "Body", min: 40, max: 2000, value: 120, unit: "Hz", log: true},
                {key: "body_decay", label: "Decay", group: "Body", min: 0.03, max: 2, step: 0.01, value: 0.35, unit: "s"},
                toggle("crack_on", "Crack"),
                {key: "crack", label: "Level", group: "Crack", min: 0, max: 100, value: 40, unit: "%"},
                {key: "crack_hz", label: "From", group: "Crack", min: 800, max: 12000, value: 3000, unit: "Hz", log: true},
                {key: "crack_len", label: "Length", group: "Crack", min: 3, max: 300, value: 60, unit: "ms", log: true},
                toggle("tone_on", "Tone"),
                {key: "tone", label: "Level", group: "Tone", min: 0, max: 100, value: 25, unit: "%"},
                {key: "wave", label: "Wave", group: "Tone", choices: WAVES, value: "triangle"},
                {key: "chord", label: "Chord", group: "Tone", choices: CHORDS, value: "stack"},
                {key: "octave", label: "Octave", group: "Tone", min: -2, max: 2, value: 0, unit: "oct"},
                {key: "tone_bend", label: "Bend", group: "Tone", min: -24, max: 12, value: 0, unit: "st", hint: "The notes slide down (or up) as they ring"},
                {key: "tone_decay", label: "Decay", group: "Tone", min: 0.05, max: 6, step: 0.05, value: 1.5, unit: "s"},
                toggle("ring_on", "Ring", false),
                {key: "ring", label: "Level", group: "Ring", min: 0, max: 100, value: 60, unit: "%", hint: "A struck-metal ring on the key's notes"},
                {key: "ring_oct", label: "Octave", group: "Ring", min: -1, max: 2, value: 1, unit: "oct"},
                {key: "ring_decay", label: "Decay", group: "Ring", min: 0.2, max: 8, step: 0.1, value: 2.5, unit: "s"},
                {key: "ring_metal", label: "Metal", group: "Ring", min: 0, max: 100, value: 60, unit: "%", hint: "Low rings like a bell, high clangs like sheet metal"},
                ...shared({level: -3, verb: 40, verb_size: 2.5, verb_tone: 40}),
            ],
            presets: [
                {name: "Classic", values: {}},
                {name: "Deep boom", values: {sub: 100, sub_decay: 3.5, sub_drop: 24, body: 40, crack: 15, tone_on: false, verb: 30, verb_size: 3}},
                {name: "Trailer hit", values: {reverse: 1, reverse_level: 70, sub: 90, body: 80, crack: 60, ring_on: true, ring: 45, tone: 40,
                    verb: 60, verb_size: 5, drive: 25, punch: 30}},
                {name: "Punchy", values: {sub_decay: 0.6, body_decay: 0.15, crack: 70, crack_len: 30, verb: 10, verb_size: 0.8, punch: 60, tone_on: false}},
                {name: "Metallic", values: {ring_on: true, ring: 90, ring_decay: 3.5, ring_metal: 80, body: 30, sub: 50, tone_on: false, verb: 50}},
                {name: "Reverse into hit", values: {reverse: 2, reverse_level: 90, verb: 55, verb_size: 3.5}},
            ],
            length: (p, song) => p.reverse * beatS(song) + 0.01
                + Math.max(p.sub_decay * 1.6, p.body_decay * 3, p.tone_decay * 1.6, p.ring_on ? p.ring_decay * 1.2 : 0,
                           p.crack_len / 1000 * 3, 0.2) + tail(p, song),
            /* The reversed swell: the hit itself made without one, its first moments turned round. */
            async prepare(p, song, seed) {
                if (!p.reverse) return {};
                const span = p.reverse * beatS(song);
                const dry = Object.assign({}, p, {reverse: 0, space_on: true, verb: Math.max(p.verb, 50)});
                const raw = await renderRaw("impact", dry, song, seed + 31, span);
                for (let c = 0; c < raw.numberOfChannels; c++) {
                    const d = raw.getChannelData(c);
                    d.reverse();
                    for (let i = 0, n = Math.floor(d.length * 0.3); i < n; i++) d[i] *= i / n;    // in from nothing
                }
                return {reversed: raw};
            },
            build(ctx, p, song, seed, out, assets = {}) {
                const t = p.reverse * beatS(song) + 0.01;
                const post = finish(ctx, p, song, seed, out);
                if (assets.reversed) {
                    const back = source(ctx, assets.reversed);
                    chain(back, gain(ctx, p.reverse_level / 100 * 0.6), out);
                    back.start(Math.max(0, t - assets.reversed.duration));
                }
                const strike = (g, level, attack, decay) => {
                    g.gain.setValueAtTime(0, 0);
                    g.gain.setValueAtTime(0, t);
                    g.gain.linearRampToValueAtTime(level, t + attack);
                    g.gain.setTargetAtTime(0, t + attack + 0.001, decay);
                };
                // Sub: falling onto the key's root.
                if (p.sub_on && p.sub > 0) {
                    const f = subHz(song.tonic) * Math.pow(2, p.sub_oct);
                    const sub = ctx.createOscillator();
                    sub.type = p.sub_wave;
                    sweep(sub.frequency, f * st(p.sub_drop), f, t, t + p.sub_drop_time / 1000);
                    const g = gain(ctx, 0);
                    strike(g, p.sub / 100, 0.004, p.sub_decay / 4);
                    chain(sub, g, post);
                    sub.start(0);
                    sub.stop(t + p.sub_decay * 1.6 + 0.1);
                }
                // Body: noise, closing down.
                if (p.body_on && p.body > 0) {
                    const thump = source(ctx, noise(ctx, t + p.body_decay * 3 + 0.1, seed, p.body_colour));
                    const lp = filter(ctx, "lowpass", p.body_from, 1);
                    sweep(lp.frequency, p.body_from, p.body_to, t, t + p.body_decay * 1.5);
                    const g = gain(ctx, 0);
                    strike(g, p.body / 100 * 0.8, 0.003, p.body_decay / 3);
                    chain(thump, lp, g, post);
                    thump.start(0);
                }
                // Crack: the very top, gone in a moment.
                if (p.crack_on && p.crack > 0) {
                    const snap = source(ctx, noise(ctx, t + p.crack_len / 1000 * 3 + 0.05, seed + 7));
                    const g = gain(ctx, 0);
                    strike(g, p.crack / 100 * 0.7, 0.002, p.crack_len / 1000 / 3);
                    chain(snap, filter(ctx, "highpass", p.crack_hz), g, post);
                    snap.start(0);
                }
                // Tone: the key's notes, struck once, maybe sliding.
                if (p.tone_on && p.tone > 0) {
                    const notes = chord(p.chord, song.mode).map(iv => hz(song.tonic, 3 + p.octave) * st(iv));
                    const g = gain(ctx, 0);
                    strike(g, p.tone / 100 * 0.3 / Math.sqrt(notes.length), 0.005, p.tone_decay / 4);
                    const oscs = voices(ctx, notes, p.wave, 1, 0, 0, g, t + p.tone_decay * 1.6 + 0.2);
                    if (p.tone_bend) for (const {osc, f} of oscs) sweep(osc.frequency, f, f * st(p.tone_bend), t, t + p.tone_decay);
                    g.connect(post);
                }
                // Ring: a short burst through narrow bands at a bell's (or a sheet's) out-of-tune partials.
                if (p.ring_on && p.ring > 0) {
                    const root = hz(song.tonic, 3 + p.ring_oct), k = p.ring_metal / 100;
                    const partials = [1, 2.0 + 0.76 * k, 3.0 + 2.4 * k, 4.2 + 4.7 * k, 5.4 + 7.1 * k];
                    const burst = source(ctx, noise(ctx, t + 0.05, seed + 13));
                    const hit = gain(ctx, 0);
                    hit.gain.setValueAtTime(0, 0);
                    hit.gain.setValueAtTime(1, t);
                    hit.gain.setTargetAtTime(0, t + 0.002, 0.004);
                    burst.connect(hit);
                    burst.start(0);
                    const g = gain(ctx, 0);
                    strike(g, p.ring / 100 * 6, 0.002, p.ring_decay / 4);
                    partials.forEach((ratio, i) => {
                        const band = filter(ctx, "bandpass", Math.min(18000, root * ratio), 60 + 40 * i);
                        chain(hit, band, gain(ctx, 1 / (1 + i * 0.6)), g);
                    });
                    g.connect(post);
                }
                return t;
            },
        },
        whoosh: {
            label: "Whoosh",
            about: "Rushes past – rising to its peak on the moment and away again, moving across the stereo.",
            params: [
                {key: "beats", label: "Length", group: "Timing", choices: [[0.25, "¼ beat"], [0.5, "½ beat"], [1, "1 beat"], [2, "2 beats"], [4, "4 beats"], [8, "8 beats"]], value: 2},
                {key: "peak", label: "Peak at", group: "Timing", min: 5, max: 95, value: 65, unit: "%"},
                {key: "in_shape", label: "Comes in", group: "Timing", min: 0, max: 100, value: 70, unit: "%", hint: "Low arrives early, high rushes in at the last moment"},
                {key: "out_shape", label: "Goes away", group: "Timing", min: 0, max: 100, value: 30, unit: "%", hint: "Low lingers, high is gone at once"},
                toggle("air_on", "Noise"),
                {key: "air", label: "Level", group: "Noise", min: 0, max: 100, value: 100, unit: "%"},
                {key: "air_colour", label: "Colour", group: "Noise", choices: COLOURS, value: "white"},
                {key: "air_from", label: "Starts at", group: "Noise", min: 60, max: 4000, value: 350, unit: "Hz", log: true},
                {key: "air_peak", label: "Peaks at", group: "Noise", min: 300, max: 16000, value: 4800, unit: "Hz", log: true},
                {key: "air_to", label: "Ends at", group: "Noise", min: 60, max: 8000, value: 400, unit: "Hz", log: true},
                {key: "air_q", label: "Resonance", group: "Noise", min: 0.3, max: 15, step: 0.1, value: 1.8, unit: "Q", hint: "High turns the rush into a whistle"},
                toggle("tone_on", "Tone"),
                {key: "tone", label: "Level", group: "Tone", min: 0, max: 100, value: 15, unit: "%"},
                {key: "wave", label: "Wave", group: "Tone", choices: WAVES, value: "sine"},
                {key: "octave", label: "Octave", group: "Tone", min: -2, max: 2, value: 1, unit: "oct"},
                {key: "bend_up", label: "Bends up", group: "Tone", min: 0, max: 24, value: 7, unit: "st"},
                {key: "bend_down", label: "Bends down", group: "Tone", min: 0, max: 24, value: 5, unit: "st"},
                toggle("rumble_on", "Low end", false),
                {key: "rumble", label: "Level", group: "Low end", min: 0, max: 100, value: 60, unit: "%", hint: "A deep rumble under it, for something big going past"},
                {key: "rumble_hz", label: "Up to", group: "Low end", min: 40, max: 400, value: 140, unit: "Hz", log: true},
                {key: "moves", label: "Moves", group: "Movement", choices: [["lr", "Left to right"], ["rl", "Right to left"], ["c", "Stays centred"]], value: "lr"},
                {key: "travel", label: "How far", group: "Movement", min: 0, max: 100, value: 90, unit: "%"},
                {key: "spin", label: "Spin", group: "Movement", min: 0, max: 100, value: 0, unit: "%", hint: "A wobble, as if it turned over as it passed"},
                {key: "spin_rate", label: "Spin speed", group: "Movement", min: 1, max: 30, step: 0.5, value: 8, unit: "Hz"},
                ...shared({level: -6, verb: 15, verb_size: 1.5}),
            ],
            presets: [
                {name: "Classic", values: {}},
                {name: "Swish", values: {beats: 0.5, peak: 55, air_peak: 7000, air_q: 1.2, tone_on: false, verb: 5}},
                {name: "Fly-by", values: {beats: 4, peak: 50, air_colour: "pink", travel: 100, spin: 30, spin_rate: 6, tone: 20, bend_down: 12,
                    rumble_on: true, rumble: 40, verb: 25}},
                {name: "Whistle", values: {air_q: 12, tone: 60, octave: 2, bend_up: 12, bend_down: 12}},
                {name: "Deep swoosh", values: {air_colour: "brown", air_peak: 1500, air_from: 120, air_to: 150, rumble_on: true, rumble: 80,
                    tone_on: false, verb: 30, verb_size: 3}},
            ],
            length: (p, song) => p.beats * beatS(song) + tail(p, song),
            build(ctx, p, song, seed, out) {
                const L = p.beats * beatS(song), P = L * p.peak / 100;
                const post = finish(ctx, p, song, seed, out);
                const pan = ctx.createStereoPanner();
                const far = p.travel / 100;
                const [a, b] = p.moves === "rl" ? [far, -far] : p.moves === "c" ? [0, 0] : [-far, far];
                pan.pan.setValueAtTime(a, 0);
                pan.pan.linearRampToValueAtTime(b, L);
                const spin = gain(ctx, 1 - p.spin / 200);
                if (p.spin > 0) {
                    const lfo = ctx.createOscillator();
                    lfo.frequency.value = p.spin_rate;
                    chain(lfo, gain(ctx, p.spin / 200), spin.gain);
                    lfo.start(0);
                    lfo.stop(L + 0.05);
                }
                chain(spin, pan, post);
                const envelope = level => {
                    const g = gain(ctx, 0);
                    rise(g.gain, level, 0, P, power(p.in_shape));
                    fall(g.gain, level, P + 1e-4, L, power(p.out_shape));
                    return g;
                };
                // Noise through a band rising to the peak and down past it.
                if (p.air_on && p.air > 0) {
                    const rush = source(ctx, noise(ctx, L + 0.05, seed, p.air_colour));
                    const band = filter(ctx, "bandpass", p.air_from, p.air_q);
                    band.frequency.setValueAtTime(p.air_from, 0);
                    band.frequency.exponentialRampToValueAtTime(p.air_peak, Math.max(1e-3, P));
                    band.frequency.exponentialRampToValueAtTime(p.air_to, L);
                    chain(rush, band, envelope(p.air / 100), spin);
                    rush.start(0);
                }
                // Tone: a whistle in the key, bending up to the peak and down past it.
                if (p.tone_on && p.tone > 0) {
                    const whistle = ctx.createOscillator();
                    whistle.type = p.wave;
                    const f = hz(song.tonic, 4 + p.octave);
                    whistle.frequency.setValueAtTime(f, 0);
                    whistle.frequency.exponentialRampToValueAtTime(f * st(p.bend_up), Math.max(1e-3, P));
                    whistle.frequency.exponentialRampToValueAtTime(f * st(-p.bend_down), L);
                    chain(whistle, envelope(p.tone / 100 * 0.25), spin);
                    whistle.start(0);
                    whistle.stop(L + 0.05);
                }
                // Low end: rumbling noise under it, centred, with the same rise and fall.
                if (p.rumble_on && p.rumble > 0) {
                    const low = source(ctx, noise(ctx, L + 0.05, seed + 5, "brown"));
                    chain(low, filter(ctx, "lowpass", p.rumble_hz, 0.9), envelope(p.rumble / 100 * 1.4), post);
                    low.start(0);
                }
                return P;
            },
        },
    };

    /* The settings, each kept in its range (or one of its choices, or on/off). */
    function values(kind, given) {
        const out = {};
        for (const p of SOUNDS[kind].params) {
            const v = given && given[p.key];
            if (p.toggle || p.toggleRow) out[p.key] = typeof v === "boolean" ? v : p.value;
            else if (p.choices) out[p.key] = p.choices.some(c => c[0] === v) ? v : p.value;
            else out[p.key] = Number.isFinite(v) ? Math.min(p.max, Math.max(p.min, v)) : p.value;
        }
        return out;
    }

    /* A preset's settings: the sound's own, with the preset's over them. */
    function preset(kind, name) {
        const found = SOUNDS[kind].presets.find(q => q.name === name);
        return values(kind, Object.assign(values(kind), found ? found.values : {}));
    }

    /* The sound as built, not levelled - `seconds` of it (its whole length if not given). */
    async function renderRaw(kind, p, song, seed, seconds) {
        const sound = SOUNDS[kind];
        const assets = sound.prepare ? await sound.prepare(p, song, seed) : {};
        const length = seconds || sound.length(p, song);
        const ctx = new OfflineAudioContext(2, Math.max(1, Math.ceil(length * RATE)), RATE);
        const out = gain(ctx);
        out.connect(ctx.destination);
        const sync = sound.build(ctx, p, song, seed, out, assets);
        const buffer = await ctx.startRendering();
        buffer.sync = sync;
        return buffer;
    }

    const peakOf = chans => chans.reduce((m, d) => { for (let i = 0; i < d.length; i++) m = Math.max(m, Math.abs(d[i])); return m; }, 0);
    const scale = (chans, k) => { for (const d of chans) for (let i = 0; i < d.length; i++) d[i] *= k; };

    /* Punch: a compressor on the sound at full scale - both channels move
       together (from the louder), fast in and slower out, so hits keep their
       front and the rest comes up. */
    function squeeze(chans, amount) {
        const threshold = -amount / 100 * 30, ratio = 1 + amount / 100 * 9;
        const attack = Math.exp(-1 / (0.005 * RATE)), release = Math.exp(-1 / (0.12 * RATE));
        let env = 0;
        for (let i = 0; i < chans[0].length; i++) {
            const x = Math.max(Math.abs(chans[0][i]), Math.abs(chans[1][i]));
            env = x > env ? attack * env + (1 - attack) * x : release * env + (1 - release) * x;
            const db = 20 * Math.log10(Math.max(env, 1e-6));
            if (db > threshold) {
                const k = Math.pow(10, (threshold + (db - threshold) / ratio - db) / 20);
                chans[0][i] *= k;
                chans[1][i] *= k;
            }
        }
    }

    /* Lo-fi: fewer steps of loudness - at 100%, 3-bit grit. */
    function crush(chans, amount) {
        const steps = Math.round(Math.pow(2, 15 - amount / 100 * 12));
        for (const d of chans) for (let i = 0; i < d.length; i++) d[i] = Math.round(d[i] * steps) / steps;
    }

    /* The sound, made whole: {buffer, sync (s into it), values}. Transposed,
       turned round if asked, faded, given its punch and lo-fi at full scale,
       and levelled so its loudest sample sits at its Level. */
    async function render(kind, given, song, seed = 1) {
        const p = values(kind, given);
        const s = Object.assign({}, NO_SONG, song || {});
        s.tonic = (s.tonic || 0) + p.transpose;
        const buffer = await renderRaw(kind, p, s, seed);
        const chans = [buffer.getChannelData(0), buffer.getChannelData(1)];
        const n = buffer.length;
        const fadeIn = Math.min(n, Math.round(p.fade_in / 1000 * RATE)), fadeOut = Math.min(n, Math.round(p.fade_out / 1000 * RATE));
        for (const d of chans) {
            if (p.backwards) d.reverse();
            for (let i = 0; i < fadeIn; i++) d[i] *= i / fadeIn;
            for (let i = 0; i < fadeOut; i++) d[n - 1 - i] *= i / fadeOut;
        }
        const sync = p.backwards ? buffer.duration - buffer.sync : buffer.sync;
        let peak = peakOf(chans);
        if (peak > 0 && (p.punch > 0 || p.lofi > 0)) {
            scale(chans, 1 / peak);
            if (p.punch > 0) squeeze(chans, p.punch);
            if (p.lofi > 0) crush(chans, p.lofi);
            peak = peakOf(chans);
        }
        if (peak > 0) scale(chans, Math.pow(10, p.level / 20) / peak);
        return {buffer, sync, values: p};
    }

    // ------------------------------------------------------------ playing --

    let live = null, playing = null;

    function context() {
        live = live || new AudioContext({sampleRate: RATE});
        if (live.state === "suspended") live.resume();
        return live;
    }

    /* Plays from `from` seconds in (to the end, or round and round with loop). */
    function play(buffer, {from = 0, loop = false, onEnd} = {}) {
        stop();
        const ctx = context();
        const s = ctx.createBufferSource();
        s.buffer = buffer;
        s.loop = loop;
        s.connect(ctx.destination);
        const at = Math.min(Math.max(0, from), Math.max(0, buffer.duration - 0.001));
        playing = {s, started: ctx.currentTime, from: at, loop, duration: buffer.duration};
        const mine = playing;
        s.onended = () => { if (playing === mine) { playing = null; if (onEnd) onEnd(); } };
        s.start(0, at);
    }

    function stop() {
        if (!playing) return;
        const {s} = playing;
        playing = null;
        try { s.stop(); } catch (_e) { /* already over */ }
    }

    /* Seconds into the sound being played, or null. */
    function position() {
        if (!playing || !live) return null;
        const t = playing.from + (live.currentTime - playing.started);
        return playing.loop ? t % playing.duration : Math.min(t, playing.duration);
    }

    /* A short moment of the sound around `at` - what scrubbing plays as the
       pointer moves: faded in and out, so it never clicks. */
    function grain(buffer, at, seconds = 0.08) {
        const ctx = context();
        const s = ctx.createBufferSource();
        s.buffer = buffer;
        const g = ctx.createGain();
        const now = ctx.currentTime;
        g.gain.setValueAtTime(0, now);
        g.gain.linearRampToValueAtTime(1, now + seconds * 0.25);
        g.gain.setValueAtTime(1, now + seconds * 0.75);
        g.gain.linearRampToValueAtTime(0, now + seconds);
        chain(s, g, ctx.destination);
        s.start(now, Math.min(Math.max(0, at - seconds / 2), Math.max(0, buffer.duration - seconds)), seconds);
    }

    /* The buffer's channels as base64 of their float32 samples, for page.py. */
    function channels(buffer) {
        const out = [];
        for (let c = 0; c < buffer.numberOfChannels; c++) {
            const bytes = new Uint8Array(buffer.getChannelData(c).slice().buffer);
            let bin = "";
            for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
            out.push(btoa(bin));
        }
        return out;
    }

    const noteName = pc => NOTES[((pc % 12) + 12) % 12];

    return {RATE, NO_SONG, SOUNDS, values, preset, render, play, stop, position, grain, channels, noteName, beatS,
            isPlaying: () => !!playing};
})();
