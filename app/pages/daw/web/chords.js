/*
 * Chords from one key: what a single key on the keyboard (on screen or the
 * computer's) plays on an instrument track when Chords isn't Off. Pure: no
 * sound, no page - engine.js asks it for the notes.
 *
 *   In the key   chords built from the song's scale on the key played - a
 *                triad, a seventh, a ninth... - so each comes out major,
 *                minor or diminished as the key has it, and no note is wrong.
 *                A key outside the scale plays the chord on the scale's note
 *                nearest it (a tie goes up)
 *   Chords       one shape on every key: major, minor, sevenths, ninths,
 *                elevenths, thirteenths, sus, altered...
 *   Intervals    a second note (or two) with the one played: octaves, fifths,
 *                thirds and sixths - fixed, or in the key
 *
 * Then the inversion (the lowest notes up an octave) and the voicing: close,
 * open (the second note up an octave), drop 2 (the second-highest down an
 * octave), or with the root again an octave below.
 *
 *   Chords.GROUPS                       [{group, items: [[id, label]]}], for the list
 *   Chords.VOICINGS, Chords.INVERSIONS  [[id, label]]
 *   Chords.notes(midi, {type, voicing, inversion}, {tonic, mode})   -> [midi], low to high
 */
"use strict";

const Chords = (() => {
    const SCALES = {major: [0, 2, 4, 5, 7, 9, 11], minor: [0, 2, 3, 5, 7, 8, 10]};

    // In the key: scale steps above the key's note (0 the note, 2 its third, 4 its fifth...).
    const IN_KEY = {
        key_triad: [0, 2, 4], key_seventh: [0, 2, 4, 6], key_ninth: [0, 2, 4, 6, 8], key_eleventh: [0, 2, 4, 6, 8, 10],
        key_thirteenth: [0, 2, 4, 6, 8, 12], key_sixth: [0, 2, 4, 5], key_add9: [0, 2, 4, 8], key_sus2: [0, 1, 4],
        key_sus4: [0, 3, 4], key_seventh_sus4: [0, 3, 4, 6], key_shell: [0, 2, 6], key_quartal: [0, 3, 6, 9],
        key_third: [0, 2], key_third_below: [-2, 0], key_sixth_below: [-5, 0], key_sixth_above: [0, 5], key_tenth: [0, 9], key_fifth: [0, 4],
    };
    // One shape on every key: semitones above the key played.
    const FIXED = {
        major: [0, 4, 7], minor: [0, 3, 7], dim: [0, 3, 6], aug: [0, 4, 8], sus2: [0, 2, 7], sus4: [0, 5, 7],
        six: [0, 4, 7, 9], m6: [0, 3, 7, 9], six_nine: [0, 4, 7, 9, 14], dom7: [0, 4, 7, 10], maj7: [0, 4, 7, 11],
        m7: [0, 3, 7, 10], m7b5: [0, 3, 6, 10], dim7: [0, 3, 6, 9], m_maj7: [0, 3, 7, 11], seven_sus4: [0, 5, 7, 10],
        aug7: [0, 4, 8, 10], maj7_sharp5: [0, 4, 8, 11], add9: [0, 4, 7, 14], m_add9: [0, 3, 7, 14], dom9: [0, 4, 7, 10, 14],
        maj9: [0, 4, 7, 11, 14], m9: [0, 3, 7, 10, 14], dom11: [0, 4, 7, 10, 14, 17], m11: [0, 3, 7, 10, 14, 17],
        maj7_sharp11: [0, 4, 7, 11, 18], dom13: [0, 4, 7, 10, 14, 21], maj13: [0, 4, 7, 11, 14, 21], m13: [0, 3, 7, 10, 14, 21],
        seven_flat9: [0, 4, 7, 10, 13], seven_sharp9: [0, 4, 7, 10, 15], seven_sharp11: [0, 4, 7, 10, 18],
        seven_flat5: [0, 4, 6, 10], quartal: [0, 5, 10, 15], so_what: [0, 5, 10, 15, 19],
        // Intervals
        octave: [0, 12], octave_below: [-12, 0], two_octaves: [-12, 0, 12], fifth: [0, 7], power: [0, 7, 12],
        fourth: [0, 5], major_third: [0, 4], minor_third: [0, 3], major_sixth: [0, 9], minor_sixth: [0, 8],
        tritone: [0, 6], second: [0, 2],
    };

    const GROUPS = [
        {group: "Off", items: [["off", "Off – one note"]]},
        {group: "In the key", items: [
            ["key_triad", "Triads in the key"], ["key_seventh", "Sevenths in the key"], ["key_ninth", "Ninths in the key"],
            ["key_eleventh", "Elevenths in the key"], ["key_thirteenth", "Thirteenths in the key"], ["key_sixth", "Sixths in the key"],
            ["key_add9", "Add9 in the key"], ["key_sus2", "Sus2 in the key"], ["key_sus4", "Sus4 in the key"],
            ["key_seventh_sus4", "7sus4 in the key"], ["key_shell", "Shells in the key (root, 3rd, 7th)"],
            ["key_quartal", "Stacked fourths in the key"]]},
        {group: "Chords", items: [
            ["major", "Major"], ["minor", "Minor"], ["dim", "Diminished"], ["aug", "Augmented"], ["sus2", "Sus2"], ["sus4", "Sus4"],
            ["six", "6"], ["m6", "Minor 6"], ["six_nine", "6/9"], ["dom7", "7"], ["maj7", "Major 7"], ["m7", "Minor 7"],
            ["m7b5", "Half-diminished (m7♭5)"], ["dim7", "Diminished 7"], ["m_maj7", "Minor-major 7"], ["seven_sus4", "7sus4"],
            ["aug7", "Augmented 7"], ["maj7_sharp5", "Major 7♯5"], ["seven_flat5", "7♭5"], ["add9", "Add9"], ["m_add9", "Minor add9"],
            ["dom9", "9"], ["maj9", "Major 9"], ["m9", "Minor 9"], ["dom11", "11"], ["m11", "Minor 11"], ["maj7_sharp11", "Major 7♯11"],
            ["dom13", "13"], ["maj13", "Major 13"], ["m13", "Minor 13"], ["seven_flat9", "7♭9"], ["seven_sharp9", "7♯9"],
            ["seven_sharp11", "7♯11"], ["quartal", "Stacked fourths"], ["so_what", "So What (fourths and a third)"]]},
        {group: "Intervals", items: [
            ["octave", "Octave above"], ["octave_below", "Octave below"], ["two_octaves", "Octaves above and below"],
            ["fifth", "Fifth"], ["power", "Power chord (fifth and octave)"], ["fourth", "Fourth"], ["major_third", "Major third"],
            ["minor_third", "Minor third"], ["major_sixth", "Major sixth"], ["minor_sixth", "Minor sixth"], ["tritone", "Tritone"],
            ["second", "Second"], ["key_third", "Third above, in the key"], ["key_third_below", "Third below, in the key"],
            ["key_sixth_below", "Sixth below, in the key"],
            ["key_sixth_above", "Sixth above, in the key"], ["key_tenth", "Tenth above, in the key"], ["key_fifth", "Fifth, in the key"]]},
    ];
    // "Close voicing", not "Close": Buddy's "Close" and "Open" are its windows' buttons, translated as such.
    const VOICINGS = [["close", "Close voicing"], ["open", "Open voicing"], ["drop2", "Drop 2"], ["bass", "With the root below"]];
    const INVERSIONS = [[0, "Root position"], [1, "1st inversion"], [2, "2nd inversion"], [3, "3rd inversion"]];
    const TYPES = new Set(GROUPS.flatMap(g => g.items.map(([id]) => id)));

    const mod = (a, n) => ((a % n) + n) % n;

    /* The scale's note at or nearest `midi` (a tie goes up). */
    function inScale(midi, key) {
        const scale = SCALES[key.mode] || SCALES.major;
        for (let d = 0; d <= 2; d++) {
            if (scale.includes(mod(midi + d - key.tonic, 12))) return midi + d;
            if (d && scale.includes(mod(midi - d - key.tonic, 12))) return midi - d;
        }
        return midi;
    }

    /* Scale steps (any, even below) above a note of the scale. */
    function steps(root, list, key) {
        const scale = SCALES[key.mode] || SCALES.major;
        const rel = mod(root - key.tonic, 12), degree = scale.indexOf(rel);
        return list.map(s => {
            const d = degree + s;
            return root - rel + scale[mod(d, 7)] + 12 * Math.floor(d / 7);
        });
    }

    /* What one key plays: {type, voicing, inversion} as the keyboard is set, in key {tonic, mode}. */
    function notes(midi, opt, key) {
        const type = opt && TYPES.has(opt.type) ? opt.type : "off";
        if (type === "off") return [midi];
        let root = midi, out;
        if (IN_KEY[type]) {
            root = inScale(midi, key);
            out = steps(root, IN_KEY[type], key);
        } else {
            out = FIXED[type].map(s => root + s);
        }
        out.sort((a, b) => a - b);
        // The inversion: the lowest notes up an octave, one at a time - on above a note already
        // there (an inverted octave would otherwise land on its own top).
        const inv = Math.min(Number(opt.inversion) || 0, out.length - 1);
        for (let i = 0; i < inv; i++) {
            let up = out.shift() + 12;
            while (out.includes(up)) up += 12;
            out.push(up);
            out.sort((a, b) => a - b);
        }
        if (out.length >= 3 && opt.voicing === "open") { out[1] += 12; out.sort((a, b) => a - b); }
        if (out.length >= 3 && opt.voicing === "drop2") { out[out.length - 2] -= 12; out.sort((a, b) => a - b); }
        if (opt.voicing === "bass") out.unshift(root - 12);
        return [...new Set(out.filter(m => m >= 0 && m <= 127))].sort((a, b) => a - b);
    }

    return {GROUPS, VOICINGS, INVERSIONS, TYPES, notes};
})();
