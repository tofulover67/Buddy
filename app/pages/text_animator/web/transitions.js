/* Animation > Transitions: Buddy's seamless motion transitions - a tile for
   each, playing live, the feel and sizes to choose, and Apply / Remove for
   the cuts in Resolve's selection (page.py; transition.py has the maths,
   line for line as trPose / trAt here; transition_resolve.py puts them on).
   Loaded after animation.js: uses its $, el, send, state and tick. */

const TR_KINDS = [
    ["zoom_in", "Zoom in", {move: "zoom", sign: 1}],
    ["zoom_out", "Zoom out", {move: "zoom", sign: -1}],
    ["whip_left", "Whip left", {move: "pan", dir: [-1, 0]}],
    ["whip_right", "Whip right", {move: "pan", dir: [1, 0]}],
    ["whip_up", "Whip up", {move: "pan", dir: [0, 1]}],
    ["whip_down", "Whip down", {move: "pan", dir: [0, -1]}],
    ["spin_left", "Spin left", {move: "spin", sign: 1}],
    ["spin_right", "Spin right", {move: "spin", sign: -1}],
    ["stretch_x", "Stretch sideways", {move: "stretch", axis: "x"}],
    ["stretch_y", "Stretch up", {move: "stretch", axis: "y"}],
    ["shake", "Shake", {move: "shake"}],
    ["zoom_spin", "Zoom spin", {move: "twist", sign: 1}],
    ["whip_diag", "Whip diagonal", {move: "pan", dir: [.7071, .7071]}],
    ["tunnel", "Tunnel", {move: "zoom", sign: 1, nest: true}],
    ["chroma_zoom", "Chroma zoom", {move: "zoom", sign: 1, chroma: .05}],
    ["roll_left", "Roll left", {move: "roll", sign: 1}],
    ["roll_right", "Roll right", {move: "roll", sign: -1}],
    ["punch", "Punch zoom", {move: "zoom", sign: 1, flash: 1.2, jolt: .03}],
    ["zoom_out_in", "Zoom out-in", {move: "zoom", sign: -1, back: true}],
    ["light_leak", "Light leak", {group: "light", fade: .15, gain: .4, soft: .25, glow: 1, sharp: 1, light: "noise"}],
    ["film_burn", "Film burn", {group: "light", fade: .12, gain: .8, soft: .4, glow: 1, sharp: 1, light: "noise"}],
    ["dreamy", "Dreamy blur", {group: "light", fade: .3, gain: .25, soft: 1, glow: .3, sharp: 1, light: "solid"}],
    ["flash", "Flash", {group: "light", fade: 0, gain: 2.5, soft: .3, glow: .85, sharp: 3, light: "solid"}],
    ["light_rays", "Light rays", {group: "light", fade: .2, gain: .2, soft: 0, glow: 0, sharp: 1, light: "none", fx: "rays",
                                  color: false}],
    ["lens_flare", "Lens flare", {group: "light", fade: .15, gain: .3, soft: .15, glow: 1, sharp: 1, light: "flare"}],
    ["bloom", "Bloom", {group: "light", fade: .2, gain: .15, soft: .1, glow: 0, sharp: 1, light: "none", fx: "bloom",
                        color: false}],
    ["bokeh", "Bokeh", {group: "light", fade: .25, gain: .1, soft: 0, glow: 0, sharp: 1, light: "none", fx: "bokeh",
                        color: false}],
    ["dip", "Dip to colour", {group: "light", fade: 0, gain: 0, soft: 0, glow: 1, sharp: 1, light: "dip"}],
    ["light_sweep", "Light sweep", {group: "light", fade: .15, gain: .15, soft: 0, glow: .9, sharp: 1, light: "band"}],
    ["blur_to_color", "Blur to colour", {group: "light", fade: 0, gain: 0, soft: .8, glow: 1, sharp: 1, light: "dip"}],
    ["negative", "Negative", {group: "light", fade: .06, gain: 0, soft: .15, glow: 0, sharp: 3, light: "none", fx: "invert",
                              color: false}],
    ["dir_blur", "Directional blur", {group: "light", fade: .25, gain: .1, soft: 3, glow: 0, sharp: 1, light: "none",
                                      axis: "x", color: false}],
    ["zoom_blur", "Zoom blur", {group: "light", fade: .2, gain: .15, soft: .5, glow: 0, sharp: 1, light: "none",
                                axis: "zoom", color: false}],
    ["spin_blur", "Spin blur", {group: "light", fade: .2, gain: .1, soft: .12, glow: 0, sharp: 1, light: "none",
                                axis: "spin", color: false}],
    ["liquid", "Liquid", {group: "wipe", map: "linear", noise: .35, band: .06, edge: .08}],
    ["ink", "Ink", {group: "wipe", map: "radial", noise: .5, band: .07, edge: .05, dark: true}],
    ["circle", "Circle", {group: "wipe", map: "radial", noise: 0, band: .15, edge: .02}],
    ["bars", "Bars", {group: "wipe", map: "bars", noise: 0, band: .15, edge: .02}],
    ["swipe", "Swipe", {group: "wipe", map: "linear", noise: 0, band: .18, edge: .02}],
    ["clock", "Clock", {group: "wipe", map: "angle", flip: true, noise: 0, band: .05, edge: .01}],
    ["box", "Box", {group: "wipe", map: "square", noise: 0, band: .1, edge: .02}],
    ["barn_doors", "Barn doors", {group: "wipe", map: "reflect", noise: 0, band: .12, edge: .02}],
    ["star", "Star", {group: "wipe", map: "star", noise: 0, band: .12, edge: .01}],
    ["luma_fade", "Luma fade", {group: "wipe", map: "luma", flip: true, noise: 0, band: 0, edge: .25, color: false}],
    ["blinds", "Blinds", {group: "wipe", map: "blinds", noise: 0, band: .05, edge: .01}],
    ["halftone", "Halftone", {group: "wipe", map: "halftone", noise: 0, band: .08, edge: .02}],
    ["shutter", "Shutter", {group: "wipe", map: "shutter", noise: 0, band: 0, edge: 0, color: false}],
    ["luma_burn", "Luma burn", {group: "wipe", map: "luma", flip: true, noise: 0, band: .1, edge: .05}],
    ["scanner", "Scanner", {group: "wipe", map: "down", noise: 0, band: .02, edge: .01, glow: .9}],
    ["diagonal", "Diagonal", {group: "wipe", map: "diag", noise: 0, band: .12, edge: .03}],
    ["spiral", "Spiral", {group: "wipe", map: "spiral", noise: 0, band: .06, edge: .02}],
    ["corner", "Corner", {group: "wipe", map: "corner", noise: 0, band: .12, edge: .02}],
    ["zigzag", "Zigzag", {group: "wipe", map: "zigzag", noise: 0, band: .08, edge: .01}],
    ["diamond", "Diamond", {group: "wipe", map: "star", shape: "diamond", noise: 0, band: .1, edge: .01}],
    ["heart", "Heart", {group: "wipe", map: "star", shape: "heart", noise: 0, band: .1, edge: .01}],
    ["iris", "Iris", {group: "wipe", map: "shutter", shape: "circle", noise: 0, band: 0, edge: 0, color: false}],
    ["vhs", "VHS", {group: "glitch", split: .014, tear: .05, rows: 60, hard: false, scan: .5, grain: .35,
                    flicker: .15, fade: .65, block: 0, stutter: .2}],
    ["bad_signal", "Bad signal", {group: "glitch", split: .02, tear: .08, rows: 6, hard: true, scan: .15, grain: .3,
                                  flicker: .4, fade: .2, block: 0, stutter: .8}],
    ["digital", "Digital", {group: "glitch", split: .012, tear: .04, rows: 4, hard: true, scan: 0, grain: .1,
                            flicker: .2, fade: 0, block: 48, stutter: .6}],
    ["pixelate", "Pixelate", {group: "glitch", style: "pixelate", fade: .12, block: 80}],
    ["tv_off", "TV switch", {group: "glitch", style: "crt"}],
    ["strobe", "Strobe", {group: "glitch", split: 0, tear: 0, rows: 6, hard: false, scan: 0, grain: 0, flicker: .7, fade: 0,
                          block: 0, stutter: 1}],
    ["chaos", "Chaos", {group: "glitch", split: .03, tear: .12, rows: 10, hard: true, scan: .25, grain: .35, flicker: .5,
                        fade: .4, block: 56, stutter: 1, jump: .04, invert: .35}],
    ["paper_tear", "Paper tear", {group: "paper", style: "tear", noise: .12, tilt: 6,
                                  grain: {XScale: 6, YScale: 6, Detail: 6, Contrast: 1.2}, color: false}],
    ["burn_away", "Burn away", {group: "paper", style: "burn", map: "radial", noise: .75, band: .03, char: .06,
                                edge: .03, glow: .9, grain: {XScale: 4, YScale: 4, Detail: 5, Contrast: 1.6}}],
    ["brush", "Brush stroke", {group: "paper", style: "brush", map: "linear", noise: .08, band: .08, edge: .01,
                               grain: {XScale: .3, YScale: 140, Detail: 3, Contrast: 1.4}}],
    ["grunge", "Grunge", {group: "paper", style: "grunge", map: "linear", noise: .65, band: .15, edge: .01,
                          dark: true, dirt: .8, jitter: .004, grain: {XScale: 5, YScale: 5, Detail: 4, Contrast: 2}}],
    ["ink_bleed", "Ink bleed", {group: "paper", style: "brush", map: "radial", noise: .6, band: .05, edge: .08, dark: true,
                                grain: {XScale: 3, YScale: 3, Detail: 5, Contrast: 1.3}}],
    ["paint_splash", "Paint splash", {group: "paper", style: "brush", map: "radial", noise: .85, band: .12, edge: .01,
                                      grain: {XScale: 2.5, YScale: 2.5, Detail: 2, Contrast: 2.2}}],
    ["twirl", "Twirl", {group: "distort", style: "twirl", fade: .06, degrees: 540}],
    ["ripple", "Ripple", {group: "distort", style: "ripple", fade: .2, amp: .3}],
    ["kaleido", "Kaleidoscope", {group: "distort", style: "kaleido", fade: .1, segments: 6, spin: .25, zoom: .6}],
    ["bulge", "Bulge", {group: "distort", style: "bulge", fade: .12, strength: .9}],
    ["pinch", "Pinch", {group: "distort", style: "pinch", fade: .12, strength: .22}],
    ["warp", "Warp", {group: "distort", style: "warp", fade: .25, refract: .08}],
    ["wave", "Wave", {group: "distort", style: "wave", fade: .2, amp: .25}],
    ["melt", "Melt", {group: "distort", style: "melt", fade: .25, refract: .3}],
    ["fisheye_zoom", "Fisheye zoom", {group: "distort", style: "lens", fade: .1, zoom: 2, strength: .9}],
    ["glass", "Glass", {group: "distort", style: "glass", fade: .12, refract: .035}],
    ["split_open", "Split open", {group: "split", layout: [2, 1], src: "a", move: "slide", from: "apart",
                                  distance: .55, spread: 0, order: "along", push: true}],
    ["split_diag", "Diagonal split", {group: "split", layout: [1, 1], src: "a", move: "slide", cut: "diag", distance: .55,
                                      spread: 0, order: "along", push: true}],
    ["panels", "Panels", {group: "split", layout: [4, 1], src: "b", move: "slide", from: [0, -1], spread: .45,
                          order: "along"}],
    ["strips", "Strips", {group: "split", layout: [1, 6], src: "b", move: "slide", from: "sides", spread: .35,
                          order: "along"}],
    ["grid", "Grid", {group: "split", layout: [4, 3], src: "b", move: "grow", spread: .5, order: "wave"}],
    ["mosaic", "Mosaic", {group: "split", layout: [4, 3], src: "b", move: "slide", from: "random", spread: .6,
                          order: "random"}],
    ["push_left", "Push left", {group: "split", layout: [1, 1], src: "pair", move: "slide", from: [-1, 0], distance: 1,
                                spread: 0, order: "along"}],
    ["push_up", "Push up", {group: "split", layout: [1, 1], src: "pair", move: "slide", from: [0, 1], distance: 1,
                            spread: 0, order: "along"}],
    ["push_right", "Push right", {group: "split", layout: [1, 1], src: "pair", move: "slide", from: [1, 0], distance: 1,
                                  spread: 0, order: "along"}],
    ["push_down", "Push down", {group: "split", layout: [1, 1], src: "pair", move: "slide", from: [0, -1], distance: 1,
                                spread: 0, order: "along"}],
    ["slices", "Slices", {group: "split", layout: [1, 6], src: "pair", move: "slide", from: "sides", distance: 1,
                          spread: .3, order: "along"}],
    ["columns", "Columns", {group: "split", layout: [6, 1], src: "b", move: "slide", from: "updown", spread: .4,
                            order: "along"}],
    ["checker", "Checker", {group: "split", layout: [4, 3], src: "b", move: "grow", spread: .5, order: "checker"}],
    ["zoom_push", "Zoom push", {group: "split", layout: [1, 1], src: "pair", move: "zoompush", from: [-1, 0], scale: .6,
                                spread: 0, order: "along"}],
    ["film_roll", "Film roll", {group: "split", layout: [1, 1], src: "pair", move: "zoompush", from: [0, 1], scale: .8,
                                spread: 0, order: "along", film: true, color: false}],
    ["squeeze", "Squeeze", {group: "split", layout: [1, 1], src: "pair", move: "squeeze", from: [-1, 0], spread: 0,
                            order: "along"}],
    ["squeeze_up", "Squeeze up", {group: "split", layout: [1, 1], src: "pair", move: "squeeze", from: [0, 1], spread: 0,
                                  order: "along"}],
    ["flip", "Flip", {group: "turn", style: "flip", axis: "y", sign: 1}],
    ["flip_up", "Flip up", {group: "turn", style: "flip", axis: "x", sign: 1}],
    ["page_turn", "Page turn", {group: "turn", style: "page", axis: "y", sign: 1}],
    ["louvers", "Louvers", {group: "turn", style: "louver", axis: "x", sign: 1, spread: .4}],
    ["cube", "Cube", {group: "turn", style: "cube", axis: "y", sign: -1}],
    ["cube_up", "Cube up", {group: "turn", style: "cube", axis: "x", sign: -1}],
    ["cube_zoom", "Cube zoom", {group: "turn", style: "cube", axis: "y", sign: -1, pull: .8}],
    ["flip_zoom", "Flip zoom", {group: "turn", style: "flip", axis: "y", sign: 1, pull: .8}],
    ["door", "Door", {group: "turn", style: "door", axis: "y", sign: 1, hinge: [0, .5], color: false}],
    ["fall", "Fall back", {group: "turn", style: "door", axis: "x", sign: -1, hinge: [.5, 0], color: false}],
];
const TR_GROUPS = [["motion", "Motion"], ["light", "Light & blur"], ["wipe", "Wipes"], ["glitch", "Glitch"],
                   ["paper", "Paper & grunge"], ["distort", "Distortions"], ["split", "Split screen"], ["turn", "3D"]];
const TR_COLORS = {warm: [1, .55, .15], cool: [.3, .62, 1], rose: [1, .38, .6], white: [1, 1, 1]};
// A colour choice - a name above or any "#rrggbb" - as [r, g, b], 0-1 (transition.rgb).
function trRgb(color) {
    if (TR_COLORS[color]) return TR_COLORS[color];
    if (/^#[0-9a-f]{6}$/i.test(color || "")) return [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16) / 255);
    return TR_COLORS.warm;
}
const TR_KIND = Object.fromEntries(TR_KINDS.map(([id, label, k]) => [id, {...k, label}]));
const TR = {ZOOM_SCALE: 3, PAN_DISTANCE: 1, SPIN_DEGREES: 180, STRETCH_SCALE: 4, SHAKE_SIZE: .05,
            BLUR_PAN: .5, BLUR_ZOOM: .5, BLUR_SPIN: .5 / 360, BLUR_MAX: 1,
            SOFT_MAX: 40, SWEEP: [.2, .8], SEETHE: 2.5, BARS: 6, EDGE_MIN: .004, STUTTER: .25,
            TEAR_DISTANCE: .8, TEAR_DROP: .08, TEAR_FRINGE: .008, TEAR_OVERLAP: .004, STEP_RATE: 12, GLOW_SIZE: 30,
            TWIRL_SIZE: 1.4, RIPPLE_FREQ: 12, RIPPLE_SPEED: 2, BULGE_SIZE: 1.4, BULGE_MAX: 1.3, KALEIDO_OUT: 1.4,
            WARP_SCALE: 5, SOFT_DISTORT: 12, PINCH_MAX: .3, SLIDE_DISTANCE: 1.05, GAP: .01, PUSH: .15,
            ASPECT: 16 / 9, TWIST_DEGREES: 90, RAYS_CENTER_Y: .6, FOCUS_MAX: 40, STAR_COVER: 3.2, CRT_LINE: .006,
            CRT_DOT: .012, TURN_PERSPECTIVE: 1.2, SHADE: .45, NEST_STEP: .5, NEST_COPIES: 5, ROLL_DEGREES: 40,
            ROLL_PIVOT: -1.5, BAND_WIDTH: .12, BAND_SOFT: .06, BAND_ANGLE: 30, BAND_FROM: -.3, BAND_TO: 1.3,
            BLINDS: 8, HALFTONE: 16, HEX_COVER: 1.3, PAGE_FLAT: 2.5, PAGE_SHADE: .35, GLASS_CELLS: 10, LOUVERS: 6,
            PUSH_GAP: .04, IRIS_COVER: 1.16, DIAMOND_COVER: 1.6,
            HEART_COVER: 1.25, ZIGZAG: 8, FILM_PITCH: .125, FILM_DUTY: .45, FILM_HOLE_X: .5625, FILM_HOLE_W: .045,
            FILM_HOLE: [.86, .85, .82]};
TR.BLUR_ROLL = .5 * (.5 - TR.ROLL_PIVOT) * Math.PI / 180 / TR.ASPECT;
const trGroup = kind => TR_KIND[kind].group || "motion";
const TR_HOLD = .5;             // seconds a tile rests on each picture before it goes again
// Play: On hover - where a tile rests when it isn't: just before the cut (a move earlier, before its blur hides it).
const trRest = kind => trGroup(kind) === "motion" ? .35 : .45;

// ---- transition.py's maths, line for line.

function trPower(p, n) { return p < .5 ? .5 * (2 * p) ** n : 1 - .5 * (2 - 2 * p) ** n; }

function trBounceOut(u) {
    const n = 7.5625, d = 2.75;
    if (u < 1 / d) return n * u * u;
    if (u < 2 / d) { u -= 1.5 / d; return n * u * u + .75; }
    if (u < 2.5 / d) { u -= 2.25 / d; return n * u * u + .9375; }
    u -= 2.625 / d;
    return n * u * u + .984375;
}

function trFeel(p, how) {
    p = Math.min(Math.max(p, 0), 1);
    if (how === "hit") return trPower(p, 5);
    if (how === "swing") {
        const c = 1.70158 * 1.525;
        return p < .5 ? ((2 * p) ** 2 * ((c + 1) * 2 * p - c)) / 2
            : ((2 * p - 2) ** 2 * ((c + 1) * (2 * p - 2) + c) + 2) / 2;
    }
    if (how === "bounce") return p < .5 ? .5 * (2 * p) ** 3 : .5 + .5 * trBounceOut(2 * p - 1);
    return trPower(p, 3);
}

const trNoise = (t, seed) => Math.sin(t * 61 + seed) * .5 + Math.sin(t * 97 + seed * 2.1) * .3
    + Math.sin(t * 151 + seed * 3.7) * .2;

// [side, sx, sy, cx, cy, deg]: positions shares of the frame, y up; degrees anticlockwise.
function trPose(s, p) {
    const kind = TR_KIND[s.kind], amount = s.amount;
    const side = p < .5 ? "a" : "b";
    const d = trFeel(p, s.feel);
    const w = side === "a" ? 2 * d : 2 * d - 2;
    let sx = 1, sy = 1, cx = .5, cy = .5, deg = 0;
    if (kind.move === "zoom") {
        const sign = kind.back && side === "b" ? -kind.sign : kind.sign;
        sx = sy = (1 + (TR.ZOOM_SCALE - 1) * amount) ** (sign * w);
        if (kind.jolt) {
            const jolt = kind.jolt * amount * Math.sin(Math.PI * p) ** 6;
            cx += jolt * trNoise(p, 1.3);
            cy += jolt * trNoise(p, 4.7);
        }
    } else if (kind.move === "pan") {
        cx += w * TR.PAN_DISTANCE * amount * kind.dir[0];
        cy += w * TR.PAN_DISTANCE * amount * kind.dir[1];
    } else if (kind.move === "spin") {
        deg = w * TR.SPIN_DEGREES * amount * kind.sign;
    } else if (kind.move === "roll") {
        deg = w * TR.ROLL_DEGREES * amount * kind.sign;
    } else if (kind.move === "twist") {
        sx = sy = (1 + (TR.ZOOM_SCALE - 1) * amount) ** (kind.sign * w);
        deg = w * TR.TWIST_DEGREES * amount * kind.sign;
    } else if (kind.move === "stretch") {
        const stretch = (1 + (TR.STRETCH_SCALE - 1) * amount) ** Math.abs(w);
        if (kind.axis === "x") sx = stretch; else sy = stretch;
    } else if (kind.move === "shake") {
        const envelope = Math.sin(Math.PI * p) ** (s.feel !== "hit" ? 2 : 6);
        const size = TR.SHAKE_SIZE * amount * envelope;
        cx += size * trNoise(p, 1.3);
        cy += size * trNoise(p, 4.7);
        deg = 3 * amount * envelope * trNoise(p, 8.1);
        sx = sy = 1 + 2 * size;
    }
    return [side, sx, sy, cx, cy, deg];
}

function trSwell(p, how) {
    p = Math.min(Math.max(p, 0), 1);
    const bell = Math.sin(Math.PI * p);
    if (how === "hit") return bell ** 8;
    if (how === "swing") return bell;
    if (how === "bounce") return bell ** 2 * (.7 + .3 * Math.cos(12 * Math.PI * p));
    return bell ** 2;
}

function trLight(s, p) {
    const kind = TR_KIND[s.kind];
    let e = trSwell(p, s.feel) ** kind.sharp;
    if (kind.light === "dip") e = trSwell(p, s.feel) ** (1 / s.amount);
    let mix;
    if (kind.fade <= 0) mix = p < .5 ? 0 : 1;
    else {
        const u = Math.min(Math.max((p - (.5 - kind.fade)) / (2 * kind.fade), 0), 1);
        mix = u * u * (3 - 2 * u);
    }
    return {mix, gain: 1 + kind.gain * s.amount * e, soft: TR.SOFT_MAX * kind.soft * s.blur * e,
            glow: kind.light === "dip" ? e : Math.min(1, kind.glow * s.amount * e),
            sweep: TR.SWEEP[0] + (TR.SWEEP[1] - TR.SWEEP[0]) * trFeel(p, "ease"), seethe: TR.SEETHE * p,
            fx: Math.min(1, s.amount * e), focus: TR.FOCUS_MAX * s.blur * e,
            band: TR.BAND_FROM + (TR.BAND_TO - TR.BAND_FROM) * trFeel(p, s.feel)};
}

function trWipe(s, p) {
    const kind = TR_KIND[s.kind];
    const soft = TR.EDGE_MIN + kind.edge * s.blur, band = kind.band * s.amount;
    const prog = Math.min(Math.max(trFeel(p, s.feel), 0), 1);
    const m1 = -soft + (1 + band + 2 * soft) * prog;
    if (kind.map === "shutter") {
        return {m1, m2: m1 - band, soft, mix: p < .5 ? 0 : 1, open: Math.min(1, Math.abs(2 * trFeel(p, s.feel) - 1)),
                turn: 60 * trFeel(p, s.feel)};
    }
    return {m1, m2: m1 - band, soft};
}

function trDice(k, salt) {
    const v = Math.sin(k * 12.9898 + salt * 78.233) * 43758.5453;
    return v - Math.floor(v);
}

function trGlitch(s, p, frames) {
    const kind = TR_KIND[s.kind];
    if (kind.style === "pixelate") {
        const e = trSwell(p, s.feel);
        return {mix: trCrossfade(p, kind.fade), block: Math.max(1, Math.round(1 + (kind.block * s.amount - 1) * e))};
    }
    if (kind.style === "crt") {
        const d = trFeel(p, s.feel), t = Math.min(Math.max(p < .5 ? 2 * d : 2 - 2 * d, 0), 1);
        const squash = Math.min(1, t / .6), narrow = Math.min(Math.max((t - .6) / .3, 0), 1);
        const out = Math.min(Math.max((t - .9) / .1, 0), 1);
        return {mix: p < .5 ? 0 : 1, sx: 1 - (1 - TR.CRT_DOT) * narrow, sy: 1 - (1 - TR.CRT_LINE) * squash,
                gain: (1 + 2 * s.amount * squash) * (1 - out)};
    }
    const k = Math.min(frames - 1, Math.max(0, Math.floor(p * frames)));
    const e = trSwell(p, s.feel), hit = e * (.4 + .6 * trDice(k, 1)), amount = s.amount;
    const w = TR.STUTTER * kind.stutter;
    let mix;
    if (w <= 0) mix = p < .5 ? 0 : 1;
    else {
        const q = Math.min(Math.max((p - (.5 - w)) / (2 * w), 0), 1);
        mix = q > .15 && q < .85 ? k % 2 : q >= .5 ? 1 : 0;
    }
    const side = trDice(k, 2) < .5 ? -1 : 1;
    let block = 1;
    if (kind.block > 0 && trDice(k, 6) < e) block = Math.max(1, Math.round(kind.block * amount * (.3 + .7 * trDice(k, 7))));
    const jump = (kind.jump || 0) * amount * hit;
    return {mix, split: kind.split * amount * hit * side, tear: kind.tear * amount * hit, seethe: 3.7 * k,
            gain: 1 + kind.flicker * amount * e * (trDice(k, 4) * 2 - 1), sat: 1 - kind.fade * e,
            scan: kind.scan * e, grain: kind.grain * e, block,
            jx: jump * (trDice(k, 21) * 2 - 1), jy: jump * .5 * (trDice(k, 22) * 2 - 1),
            neg: trDice(k, 23) < (kind.invert || 0) * e ? 1 : 0};
}

function trPaper(s, p) {
    const kind = TR_KIND[s.kind], amount = s.amount;
    if (kind.style === "tear") {
        const d = Math.max(0, trFeel(p, s.feel)), drop = TR.TEAR_DROP * amount * d, tilt = kind.tilt * amount * d;
        return {open: d, lx: .5 - TR.TEAR_DISTANCE * d, ly: .5 - drop, la: tilt,
                rx: .5 + TR.TEAR_DISTANCE * d, ry: .5 - drop, ra: -tilt, fringe: TR.TEAR_FRINGE * Math.min(1, 8 * d)};
    }
    let out = {dirt: 0, jx: 0, jy: 0, seethe: 0};
    if (kind.style === "grunge") {
        const steps = Math.max(4, Math.floor(s.length * TR.STEP_RATE + .5));
        const j = Math.min(steps - 1, Math.floor(p * steps));
        p = j / (steps - 1);
        const e = trSwell(p, s.feel);
        out = {dirt: kind.dirt * e, jx: (trDice(j, 8) * 2 - 1) * kind.jitter * amount * e,
               jy: (trDice(j, 9) * 2 - 1) * kind.jitter * amount * e, seethe: 1.7 * j};
    }
    if (kind.style === "burn") {
        const soft = TR.EDGE_MIN + kind.edge * s.blur, band = kind.band * amount, char = kind.char * amount;
        const prog = Math.min(Math.max(trFeel(p, s.feel), 0), 1);
        const m0 = -soft * 1.5 + (1 + soft * 2 + char + band) * prog;
        return {m0, m1: m0 - char, m2: m0 - char - band, soft, ...out};
    }
    const v = trWipe(s, p);
    return {m0: v.m1, ...v, ...out};
}

function trCrossfade(p, fade) {
    if (fade <= 0) return p < .5 ? 0 : 1;
    const u = Math.min(Math.max((p - (.5 - fade)) / (2 * fade), 0), 1);
    return u * u * (3 - 2 * u);
}

function trDistort(s, p) {
    const kind = TR_KIND[s.kind], amount = s.amount, e = trSwell(p, s.feel);
    const out = {mix: trCrossfade(p, kind.fade), soft: TR.SOFT_DISTORT * s.blur * e};
    if (kind.style === "twirl") {
        const d = trFeel(p, s.feel);
        return {...out, angle_a: kind.degrees * amount * 2 * d, angle_b: kind.degrees * amount * (2 * d - 2)};
    }
    if (kind.style === "ripple" || kind.style === "wave") return {...out, amp: kind.amp * amount * e, phase: TR.RIPPLE_SPEED * p};
    if (kind.style === "kaleido") {
        return {...out, kaleido: Math.min(1, 2 * e), spin: kind.spin * amount * (trFeel(p, s.feel) - .5),
                zoom: 1 + kind.zoom * amount * e};
    }
    if (kind.style === "bulge") return {...out, strength: Math.min(TR.BULGE_MAX, kind.strength * amount) * e};
    if (kind.style === "pinch") return {...out, strength: Math.min(TR.PINCH_MAX, kind.strength * amount) * e};
    if (kind.style === "glass") return {...out, refract: kind.refract * amount * e};
    if (kind.style === "lens") {
        const d = trFeel(p, s.feel);
        return {...out, zoom_a: kind.zoom ** (2 * d), zoom_b: kind.zoom ** (2 * d - 2),
                strength: Math.min(TR.BULGE_MAX, kind.strength * amount) * e};
    }
    return {...out, refract: kind.refract * amount * e, seethe: TR.SEETHE * p};
}

const trPieceCache = {};
function trPieces(kindId) {
    if (trPieceCache[kindId]) return trPieceCache[kindId];
    const k = TR_KIND[kindId], [cols, rows] = k.layout, n = cols * rows;
    if (k.cut === "diag") {
        return (trPieceCache[kindId] = [["ul", [-1, 1]], ["lr", [1, -1]]].map(([half, dir]) =>
            ({home: [.5, .5, 1, 1], dir, order: 0, src: k.src, half})));
    }
    const ranked = [...Array(n).keys()].sort((a, b) => trDice(a, 31) - trDice(b, 31));
    const out = [];
    for (let i = 0; i < n; i++) {
        const row = Math.floor(i / cols), col = i % cols, way = k.from;
        let d;
        if (way === "apart") d = col < cols / 2 ? [-1, 0] : [1, 0];
        else if (way === "sides") d = row % 2 === 0 ? [-1, 0] : [1, 0];
        else if (way === "updown") d = col % 2 === 0 ? [0, -1] : [0, 1];
        else if (way === "random") d = [[-1, 0], [1, 0], [0, -1], [0, 1]][Math.min(3, Math.floor(trDice(i, 32) * 4))];
        else d = way ? [...way] : [0, 0];
        const order = k.order === "wave" ? (col + row) / Math.max(1, cols + rows - 2)
            : k.order === "random" ? ranked.indexOf(i) / Math.max(1, n - 1)
            : k.order === "checker" ? (col + row) % 2 : i / Math.max(1, n - 1);
        const home = [(col + .5) / cols, 1 - (row + .5) / rows, 1 / cols, 1 / rows];
        if (k.src === "pair") {
            out.push({home, dir: d, order, src: "a"});
            out.push({home, dir: [-d[0], -d[1]], order, src: "b"});
        } else out.push({home, dir: d, order, src: k.src});
    }
    return (trPieceCache[kindId] = out);
}

const trSmooth = u => { u = Math.min(Math.max(u, 0), 1); return u * u * (3 - 2 * u); };

function trShare(kind, piece, p, how) {
    const q = Math.min(Math.max(trFeel(p, how), 0), 1);
    return piece.src === "a" ? 1 - q : q;
}

function trSquash(kind, piece, p, how) {
    if (kind.move !== "squeeze") return [1, 1];
    const share = trShare(kind, piece, p, how);
    return piece.dir[0] ? [share, 1] : [1, share];
}

function trPiece(kind, piece, p, how) {
    const [hx, hy] = piece.home;
    if (kind.move === "squeeze") {
        const away = (1 - trShare(kind, piece, p, how)) / 2;
        return [hx + piece.dir[0] * away, hy + piece.dir[1] * away, 1];
    }
    if (kind.move === "zoompush") {
        const z = 1 - (1 - kind.scale) * (trSmooth(p / .35) - trSmooth((p - .65) / .35));
        const q = trFeel((p - .3) / .4, how), away = (piece.src === "a" ? q : 1 - q) * (z + TR.PUSH_GAP);
        return [hx + piece.dir[0] * away, hy + piece.dir[1] * away, z];
    }
    const d = trFeel((p - kind.spread * piece.order) / (1 - kind.spread), how);
    if (kind.move === "grow") return [hx, hy, Math.max(0, d)];
    const away = (piece.src === "a" ? d : 1 - d) * (kind.distance ?? TR.SLIDE_DISTANCE);
    return [hx + piece.dir[0] * away, hy + piece.dir[1] * away, 1];
}

function trSplit(s, p, frames, aspect = TR.ASPECT) {
    const kind = TR_KIND[s.kind], back = Math.max(0, p - 1 / Math.max(1, frames - 1));
    const x = [], y = [], sz = [], blur = [], sw = [], sh = [];
    for (const piece of trPieces(s.kind)) {
        const [px, py, z] = trPiece(kind, piece, p, s.feel), [ox, oy, oz] = trPiece(kind, piece, back, s.feel);
        const [w, h] = trSquash(kind, piece, p, s.feel);
        sw.push(w);
        sh.push(h);
        const speed = kind.move === "grow" ? Math.abs(z - oz) * TR.BLUR_ZOOM : Math.hypot(px - ox, (py - oy) / aspect) * TR.BLUR_PAN;
        x.push(px);
        y.push(py);
        sz.push(z);
        blur.push(Math.min(TR.BLUR_MAX, speed * s.blur));
    }
    const push = kind.push ? 1 + TR.PUSH * (1 - trFeel(p, s.feel)) : 1;
    const out = {x, y, s: sz, sw, sh, blur, border: kind.film ? 0 : TR.GAP * s.amount * trSwell(p, s.feel), push};
    if (kind.film) Object.assign(out, {hole_y: y[0], hole_p: TR.FILM_PITCH * sz[0], hole_x: TR.FILM_HOLE_X * sz[0],
                                       hole_w: TR.FILM_HOLE_W * sz[0]});
    return out;
}

function trTurnRig(kindId, persp, aspect = TR.ASPECT) {
    const k = TR_KIND[kindId];
    if (k.style === "cube") {
        const half = k.axis === "y" ? .5 : .5 / aspect;
        return {pivot: [.5, .5], zpivot: -half, zmove: (persp + half) / persp};
    }
    return {pivot: k.hinge || [.5, .5], zpivot: 0, zmove: 1};
}

function trTurn(s, p) {
    const kind = TR_KIND[s.kind], d = trFeel(p, s.feel), sign = kind.sign;
    let a, b, showA = 1, showB = 1, shade = 1;
    if (kind.style === "flip") {
        [a, b] = p < .5 ? [180 * d, -90] : [90, 180 * d - 180];
        [showA, showB] = p < .5 ? [1, 0] : [0, 1];
    } else if (kind.style === "cube") {
        [a, b] = [90 * d, 90 * d - 90];
    } else if (kind.style === "louver") {
        const a = [], b = [], sa = [], sb = [];
        for (let i = 0; i < TR.LOUVERS; i++) {
            const q = trFeel((p - kind.spread * i / (TR.LOUVERS - 1)) / (1 - kind.spread), s.feel), turn = 180 * q;
            const first = turn < 90;
            a.push((first ? turn : 90) * sign);
            b.push((first ? -90 : turn - 180) * sign);
            sa.push(first ? 1 : 0);
            sb.push(first ? 0 : 1);
        }
        return {rot_a: a, rot_b: b, show_a: sa, show_b: sb, persp: TR.TURN_PERSPECTIVE / s.amount, shade: 1, pull: 1};
    } else if (kind.style === "page") {
        const turn = Math.min(Math.max(180 * d, 0), 180);
        [a, b] = [-Math.min(turn, 90), 180 - Math.max(turn, 90)];
        [showA, showB] = [turn < 90 ? 1 : 0, turn >= 90 ? 1 : 0];
        shade = 1 - TR.PAGE_SHADE * Math.sin(turn * Math.PI / 180);
    } else {
        [a, b] = [Math.min(90, 90 * d), 0];
        showA = a < 90 ? 1 : 0;
        shade = 1 - TR.SHADE * (1 - Math.min(Math.max(d, 0), 1));
    }
    const flat = kind.style === "page" ? TR.PAGE_FLAT : 1;
    return {rot_a: a * sign, rot_b: b * sign, show_a: showA, show_b: showB, persp: TR.TURN_PERSPECTIVE * flat / s.amount, shade,
            pull: 1 + (kind.pull || 0) * trSwell(p, s.feel)};
}

function trAt(s, p, frames) {
    if (trGroup(s.kind) === "turn") return {side: p < .5 ? "a" : "b", ...trTurn(s, p)};
    if (trGroup(s.kind) === "split") return {side: p < .5 ? "a" : "b", ...trSplit(s, p, frames)};
    if (trGroup(s.kind) === "distort") return {side: p < .5 ? "a" : "b", ...trDistort(s, p)};
    if (trGroup(s.kind) === "paper") return {side: p < .5 ? "a" : "b", ...trPaper(s, p)};
    if (trGroup(s.kind) === "glitch") return {side: p < .5 ? "a" : "b", ...trGlitch(s, p, frames)};
    if (trGroup(s.kind) === "light") return {side: p < .5 ? "a" : "b", ...trLight(s, p)};
    if (trGroup(s.kind) === "wipe") return {side: p < .5 ? "a" : "b", ...trWipe(s, p)};
    const [side, sx, sy, cx, cy, deg] = trPose(s, p);
    const kind = TR_KIND[s.kind], step = 1 / Math.max(1, frames);
    const [, osx, osy, ocx, ocy, odeg] = trPose(s, side === "a" ? Math.max(0, p - step) : Math.min(1, p + step));
    let blur, angle = 0;
    if (kind.move === "zoom") blur = Math.abs(Math.log(sx) - Math.log(osx)) * TR.BLUR_ZOOM;
    else if (kind.move === "spin") blur = Math.abs(deg - odeg) * TR.BLUR_SPIN;
    else if (kind.move === "roll") blur = Math.abs(deg - odeg) * TR.BLUR_ROLL;
    else if (kind.move === "twist") {
        blur = Math.max(Math.abs(deg - odeg) * TR.BLUR_SPIN, Math.abs(Math.log(sx) - Math.log(osx)) * TR.BLUR_ZOOM * .5);
    } else if (kind.move === "stretch") {
        const [now, then] = kind.axis === "x" ? [sx, osx] : [sy, osy];
        blur = Math.abs(Math.log(now) - Math.log(then)) * TR.BLUR_ZOOM;
        angle = kind.axis === "x" ? 0 : 90;
    } else {
        blur = Math.hypot(cx - ocx, cy - ocy) * TR.BLUR_PAN;
        if (kind.move === "pan") angle = Math.atan2(kind.dir[1], kind.dir[0]) * 180 / Math.PI;
        else if (blur > 0) angle = Math.atan2(cy - ocy, cx - ocx) * 180 / Math.PI;
    }
    const e = trSwell(p, s.feel);
    return {side, sx, sy, cx, cy, deg, move: kind.move, blur: Math.min(TR.BLUR_MAX, blur * s.blur), angle,
            nest: kind.nest ? e : 0, chroma: (kind.chroma || 0) * s.amount * e,
            flash: 1 + (kind.flash || 0) * s.amount * trSwell(p, "hit")};
}

// ---- the two sample pictures the tiles play between: a warm and a cool one.

function trPicture(warm) {
    const c = document.createElement("canvas");
    c.width = 320;
    c.height = 180;
    const g = c.getContext("2d");
    const sky = g.createLinearGradient(0, 0, 0, 180);
    sky.addColorStop(0, warm ? "#ff9a5a" : "#2b6f9e");
    sky.addColorStop(1, warm ? "#ffd7a0" : "#9fd8e8");
    g.fillStyle = sky;
    g.fillRect(0, 0, 320, 180);
    g.fillStyle = warm ? "#fff3c4" : "#ffffff";
    g.beginPath();
    g.arc(warm ? 220 : 90, warm ? 70 : 52, warm ? 26 : 18, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = warm ? "#7a3b4f" : "#1d4a5e";
    g.beginPath();
    g.moveTo(0, 180);
    for (let x = 0; x <= 320; x += 20) g.lineTo(x, 120 + Math.sin(x / (warm ? 37 : 23)) * (warm ? 22 : 30));
    g.lineTo(320, 180);
    g.fill();
    g.fillStyle = warm ? "#4a1f33" : "#0f2c3a";
    g.fillRect(warm ? 40 : 230, warm ? 110 : 100, 26, 70);
    return c;
}
const TR_PICS = {a: null, b: null};

/* A light's look for the previews: soft blobs of its colour on black, wider
   than the frame so it can drift across - close to Fusion's FastNoise at
   the transition's settings, not the same noise. */
const trLights = {};
function trLightTexture(kind, color) {
    const key = kind + ":" + color;
    if (trLights[key]) return trLights[key];
    const c = document.createElement("canvas");
    c.width = 640;
    c.height = 180;
    const g = c.getContext("2d");
    const [r, gr, b] = trRgb(color).map(v => Math.round(v * 255));
    const burn = kind === "film_burn";
    // A burn's darks: its colour deepened (transition.burn_dark).
    const [cr, cg, cb] = trRgb(color);
    g.fillStyle = burn ? "rgb(" + [cr * cr * .55, cg * cg * .25, cb * cb * .3].map(v => Math.round(v * 255)).join(",") + ")" : "#000";
    g.fillRect(0, 0, 640, 180);
    let seed = burn ? 7 : 3;
    const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
    for (let i = 0; i < (burn ? 22 : 9); i++) {
        const x = rand() * 640, y = rand() * 180, rad = (burn ? 30 : 70) + rand() * (burn ? 50 : 90);
        const grad = g.createRadialGradient(x, y, 0, x, y, rad);
        grad.addColorStop(0, "rgba(" + [r, gr, b].join(",") + "," + (.75 + rand() * .25) + ")");
        grad.addColorStop(1, "rgba(" + [r, gr, b].join(",") + ",0)");
        g.fillStyle = grad;
        g.fillRect(x - rad, y - rad, rad * 2, rad * 2);
    }
    return (trLights[key] = c);
}

/* A wipe's map for the previews, W x H: transition_resolve's gradients
   (Fusion measures them in frame widths, y up), with a smooth value noise
   standing in for FastNoise. */
const trMaps = {};
function trNoise2(x, y, seed) {
    const h = (i, j) => {
        const v = Math.sin(i * 127.1 + j * 311.7 + seed * 74.7) * 43758.5453;
        return v - Math.floor(v);
    };
    const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
    const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    return (h(i, j) * (1 - u) + h(i + 1, j) * u) * (1 - v) + (h(i, j + 1) * (1 - u) + h(i + 1, j + 1) * u) * v;
}
function trMap(kindId, W, H, seethe = 0) {
    const key = kindId + ":" + W + ":" + seethe;
    if (trMaps[key]) return trMaps[key];
    if (Object.keys(trMaps).length > 60) for (const k in trMaps) delete trMaps[k];
    const kind = TR_KIND[kindId], a = H / W, map = new Float32Array(W * H);
    const grad = (x, y, sx, sy, ex, ey) => {
        const dx = ex - sx, dy = (ey - sy) * a;
        return ((x - sx) * dx + (y - sy) * a * dy) / (dx * dx + dy * dy);
    };
    const clamp01 = v => Math.min(1, Math.max(0, v));
    const step = 1 / TR.BARS, ink = !!kind.dark;
    for (let py = 0; py < H; py++) {
        for (let px = 0; px < W; px++) {
            const x = (px + .5) / W, y = 1 - (py + .5) / H;
            let v;
            if (kind.map === "radial") v = clamp01(Math.hypot(x - .5, (y - .5) * a) / .6);       // to (1.1, .5): .6 widths
            else if (kind.map === "square") v = clamp01(Math.max(Math.abs(x - .5), Math.abs(y - .5) * a) / .5);
            else if (kind.map === "reflect") v = clamp01(Math.abs(x - .5) / .5);
            else if (kind.map === "down") v = 1 - y;
            else if (kind.map === "diag") v = clamp01(grad(x, y, 0, 1, 1, 0));
            else if (kind.map === "spiral") {
                const t = Math.atan2(-(x - .5), (y - .5) * a) / (2 * Math.PI);
                v = (t < 0 ? t + 1 : t) / 3 + 2 / 3 * clamp01(Math.hypot(x - .5, (y - .5) * a) / .6);
            }
            else if (kind.map === "corner") v = clamp01(Math.hypot(x, (y - 1) * a) / 1.15);
            else if (kind.map === "zigzag") {
                // across, toothed by a Ping-Pong gradient down the frame
                let t = grad(x, y, .5, 1, .5, 1 - .5 / TR.ZIGZAG);
                t -= 2 * Math.floor(t / 2);
                v = .88 * clamp01(grad(x, y, 0, .5, 1, .5)) + .12 * (t > 1 ? 2 - t : t);
            }
            else if (kind.map === "blinds") { const t = (1 - y) * TR.BLINDS; v = t - Math.floor(t); }
            else if (kind.map === "halftone") {
                // each square cell its distance out from its middle, a diagonal sweep mixed in
                const cx = x * TR.HALFTONE, cy = (1 - y) * TR.HALFTONE * a;
                const cell = Math.max(Math.abs(cx - Math.floor(cx) - .5), Math.abs(cy - Math.floor(cy) - .5)) * 2;
                v = .5 * clamp01(grad(x, y, 0, 1, 1, 0)) + .5 * cell;
            } else if (kind.map === "luma") {
                const B = trPixelsOf("b", W, H), j = (py * W + px) * 4;
                v = (.299 * B[j] + .587 * B[j + 1] + .114 * B[j + 2]) / 255;
            }
            else if (kind.map === "angle") {
                // 0 straight up, rising anticlockwise (Fusion's Angle gradient)
                const t = Math.atan2(-(x - .5), (y - .5) * a) / (2 * Math.PI);
                v = t < 0 ? t + 1 : t;
            }
            else if (kind.map === "bars") {
                const saw = grad(x, y, 0, 1, step, 1 - step * 9 / 16);
                v = .4 * clamp01(grad(x, y, 0, 1, 1, 0)) + .6 * (saw - Math.floor(saw));
            } else v = clamp01(grad(x, y, 0, .5, 1, .5));
            if (kind.grain) {
                // FastNoise's stand-in: octaves of value noise, stretched as XScale / YScale say.
                const g = kind.grain;
                let n = 0, total = 0, amp = .5, f = 1;
                for (let o = 0; o < Math.round(g.Detail); o++, amp /= 2, f *= 2) {
                    n += amp * trNoise2(x * g.XScale * f, y * g.YScale * a * f, o + seethe * 7.3);
                    total += amp;
                }
                n = clamp01(.5 + (n / total - .5) * g.Contrast * 1.6);
                v = v * (1 - kind.noise) + n * kind.noise;
            } else if (kind.noise > 0) {
                const sc = ink ? 6 : 3;
                let n = 0, total = 0, amp = .5, f = 1;
                for (let o = 0; o < (ink ? 4 : 2); o++, amp /= 2, f *= 2) {
                    n += amp * trNoise2(x * sc * f, y * sc * a * f, o);
                    total += amp;
                }
                n = clamp01(.5 + (n / total - .5) * (ink ? 1.4 : 1));
                v = v * (1 - kind.noise) + n * kind.noise;
            }
            map[py * W + px] = v;
        }
    }
    return (trMaps[key] = map);
}

const trPixels = {};
function trPixelsOf(which, W, H) {
    const key = which + ":" + W;
    if (!trPixels[key]) {
        const c = document.createElement("canvas");
        c.width = W;
        c.height = H;
        const g = c.getContext("2d");
        g.drawImage(TR_PICS[which], 0, 0, W, H);
        trPixels[key] = g.getImageData(0, 0, W, H).data;
    }
    return trPixels[key];
}

// The wipe group: the band, then the incoming picture, over the outgoing one where the map is below each edge.
function trStarPath(g, W, H, m, shape) {
    if (shape === "heart") {
        // A square on its point and a circle on each upper side, its middle the frame's (transition_resolve).
        const S = Math.max(0, m) * TR.HEART_COVER * W, lobe = Math.sqrt(.125) * S, d = Math.sqrt(.5) * S;
        const drop = (lobe + S / 2 - d) / 2, cx = W / 2, cy = H / 2 + drop;
        g.beginPath();
        g.moveTo(cx, cy - d);
        g.lineTo(cx + d, cy);
        g.lineTo(cx, cy + d);
        g.lineTo(cx - d, cy);
        g.closePath();
        for (const side of [-1, 1]) {
            g.moveTo(cx + side * lobe + S / 2, cy - lobe);
            g.arc(cx + side * lobe, cy - lobe, S / 2, 0, 2 * Math.PI);
        }
        return;
    }
    if (shape === "diamond") {
        // sNGon's four sides: tips left and right, Width tip to tip.
        const R = Math.max(0, m) * TR.DIAMOND_COVER * W / 2;
        g.beginPath();
        g.moveTo(W / 2 + R, H / 2);
        g.lineTo(W / 2, H / 2 - R);
        g.lineTo(W / 2 - R, H / 2);
        g.lineTo(W / 2, H / 2 + R);
        g.closePath();
        return;
    }
    // sStar: 5 points, Width (frame widths) the outer diameter, turned as it grows.
    const R = Math.max(0, m) * TR.STAR_COVER * W / 2, r = R * .45, turn = 72 * Math.max(0, m) * Math.PI / 180;
    g.beginPath();
    for (let i = 0; i < 10; i++) {
        const a = Math.PI / 2 + turn + i * Math.PI / 5, rad = i % 2 ? r : R;
        g[i ? "lineTo" : "moveTo"](W / 2 + rad * Math.cos(a), H / 2 - rad * Math.sin(a));
    }
    g.closePath();
}

function trDrawStar(ctx, s, p) {
    const W = ctx.canvas.width, H = ctx.canvas.height, v = trWipe(s, p);
    ctx.drawImage(TR_PICS.a, 0, 0, W, H);
    ctx.save();
    trStarPath(ctx, W, H, v.m1, TR_KIND[s.kind].shape);
    ctx.clip();
    ctx.fillStyle = "rgb(" + trRgb(s.color).map(c => Math.round(c * 255)).join(",") + ")";
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
    ctx.save();
    trStarPath(ctx, W, H, v.m2, TR_KIND[s.kind].shape);
    ctx.clip();
    ctx.drawImage(TR_PICS.b, 0, 0, W, H);
    ctx.restore();
}

function trDrawWipe(ctx, s, p) {
    const W = ctx.canvas.width, H = ctx.canvas.height, v = trWipe(s, p), kind = TR_KIND[s.kind];
    if (kind.map === "star") return trDrawStar(ctx, s, p);
    if (kind.map === "shutter") {
        // The clip of the moment through a turning six-sided aperture, on black.
        ctx.save();
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, W, H);
        ctx.beginPath();
        if (kind.shape === "circle") ctx.arc(W / 2, H / 2, v.open * TR.IRIS_COVER * W / 2, 0, 2 * Math.PI);
        else {
            const R = v.open * TR.HEX_COVER * W / 2, turn = v.turn * Math.PI / 180;
            for (let i = 0; i < 6; i++) {
                const t = turn + i * Math.PI / 3;
                ctx[i ? "lineTo" : "moveTo"](W / 2 + R * Math.cos(t), H / 2 - R * Math.sin(t));
            }
        }
        ctx.closePath();
        ctx.clip();
        ctx.drawImage(TR_PICS[v.mix >= .5 ? "b" : "a"], 0, 0, W, H);
        ctx.restore();
        return;
    }
    const map = trMap(s.kind, W, H), A = trPixelsOf("a", W, H), B = trPixelsOf("b", W, H);
    const k = kind.dark ? .12 : 1, col = trRgb(s.color).map(c => c * k * 255);
    const out = ctx.createImageData(W, H), o = out.data, half = v.soft / 2;
    for (let i = 0, n = W * H; i < n; i++) {
        const m = kind.flip ? 1 - map[i] : map[i], j = i * 4;
        const a1 = Math.min(1, Math.max(0, (v.m1 + half - m) / v.soft));
        const a2 = Math.min(1, Math.max(0, (v.m2 + half - m) / v.soft));
        for (let c = 0; c < 3; c++) {
            const banded = A[j + c] * (1 - a1) + col[c] * a1;
            o[j + c] = banded * (1 - a2) + B[j + c] * a2;
        }
        o[j + 3] = 255;
    }
    ctx.putImageData(out, 0, 0);
    if (kind.glow) {
        // The band alone, blurred and screened on (transition_resolve's glow).
        const glow = trScratch(W, H), g = glow.getContext("2d"), band = g.createImageData(W, H), d = band.data;
        for (let i = 0, n = W * H; i < n; i++) {
            const m = map[i], a1 = Math.min(1, Math.max(0, (v.m1 + half - m) / v.soft));
            const a2 = Math.min(1, Math.max(0, (v.m2 + half - m) / v.soft)), e = a1 * (1 - a2);
            d[i * 4] = col[0] * e; d[i * 4 + 1] = col[1] * e; d[i * 4 + 2] = col[2] * e; d[i * 4 + 3] = 255;
        }
        g.putImageData(band, 0, 0);
        ctx.save();
        ctx.globalCompositeOperation = "screen";
        ctx.globalAlpha = kind.glow;
        ctx.filter = `blur(${(TR.GLOW_SIZE * W / 1920 * .5).toFixed(1)}px)`;
        ctx.drawImage(glow, 0, 0);
        ctx.restore();
    }
}

/* The glitch group for the previews, a pixel at a time: the clip the
   stutter shows, in blocks, its red and blue pulled apart, rows torn
   sideways (a noise down the frame, standing in for FastNoise), flickered,
   faded, scanlined and grained - Fusion's chain, close, not exact. */
function trDrawGlitch(ctx, s, p, frames) {
    const W = ctx.canvas.width, H = ctx.canvas.height, v = trGlitch(s, p, frames), kind = TR_KIND[s.kind];
    if (kind.style === "pixelate") {
        // Crossfaded, then down to a pixel a block and back up, unsmoothed.
        const scratch = trScratch(W, H), g = scratch.getContext("2d");
        g.globalAlpha = 1;
        g.drawImage(TR_PICS.a, 0, 0, W, H);
        g.globalAlpha = v.mix;
        g.drawImage(TR_PICS.b, 0, 0, W, H);
        g.globalAlpha = 1;
        const z = Math.max(1, v.block * W / 1920), w = Math.max(1, Math.round(W / z)), h = Math.max(1, Math.round(H / z));
        if (w >= W) return ctx.drawImage(scratch, 0, 0);       // no blocks: the scratch is the picture
        const small = trScratch(w, h), sg = small.getContext("2d");
        sg.clearRect(0, 0, w, h);
        sg.drawImage(scratch, 0, 0, w, h);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(small, 0, 0, w, h, 0, 0, W, H);
        ctx.imageSmoothingEnabled = true;
        return;
    }
    if (kind.style === "crt") {
        ctx.save();
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, W, H);
        ctx.filter = `brightness(${v.gain.toFixed(3)})`;
        const w = Math.max(1, W * v.sx), h = Math.max(1, H * v.sy);
        ctx.drawImage(TR_PICS[v.mix >= .5 ? "b" : "a"], (W - w) / 2, (H - h) / 2, w, h);
        ctx.restore();
        return;
    }
    const src = trPixelsOf(v.mix >= .5 ? "b" : "a", W, H);
    const k = Math.min(frames - 1, Math.max(0, Math.floor(p * frames)));
    const block = Math.max(1, v.block * W / 1920);
    // Each row's tear: a smooth (or, hard, stepped) noise down the frame.
    const shift = new Float32Array(H);
    for (let y = 0; y < H; y++) {
        const t = y / H * kind.rows + v.seethe * .37;
        const n = kind.hard ? trDice(Math.floor(t), 11 + v.seethe) : trNoise2(t, .5, 13);
        shift[y] = (n - .5) * 2 * v.tear * W;
    }
    const red = v.split * W, out = ctx.createImageData(W, H), o = out.data;
    const at = (x, y, c) => {
        let px = Math.min(W - 1, Math.max(0, x)), py = Math.min(H - 1, Math.max(0, y));
        if (block > 1) {
            px = Math.min(W - 1, Math.floor(Math.floor(px / block) * block + block / 2));
            py = Math.min(H - 1, Math.floor(Math.floor(py / block) * block + block / 2));
        }
        return src[(py * W + Math.round(px)) * 4 + c];
    };
    for (let y = 0; y < H; y++) {
        const saw = (y * 270 / H) % 1, lines = 1 - v.scan + v.scan * saw;
        const sy = Math.round(y + v.jy * H);
        for (let x = 0; x < W; x++) {
            const sx = x - shift[y] - v.jx * W;
            let r = at(sx - red, sy, 0), g = at(sx, sy, 1), b = at(sx + red, sy, 2);
            const grey = .3 * r + .59 * g + .11 * b;
            r = grey + (r - grey) * v.sat;
            g = grey + (g - grey) * v.sat;
            b = grey + (b - grey) * v.sat;
            const grain = (trDice(x * 7 + y * 131 + k * 9973, 5) - .5) * v.grain * 90;
            const j = (y * W + x) * 4;
            r *= v.gain; g *= v.gain; b *= v.gain;
            if (v.neg) { r = Math.abs(255 - r); g = Math.abs(255 - g); b = Math.abs(255 - b); }
            o[j] = r * lines + grain;
            o[j + 1] = g * lines + grain;
            o[j + 2] = b * lines + grain;
            o[j + 3] = 255;
        }
    }
    ctx.putImageData(out, 0, 0);
}

/* The paper group. A tear: each pixel looked for in the halves (moved and
   turned as Fusion's Transforms do, y up), the left's paper reaching past
   the line under the right's, a hard shadow under each; the rest, the
   wipe's compose with a charred band (a burn - its ember glow blurred and
   screened over), or a boiling map, dust, scratches and a jitter (grunge). */
function trDrawPaper(ctx, s, p) {
    const W = ctx.canvas.width, H = ctx.canvas.height, v = trPaper(s, p), kind = TR_KIND[s.kind];
    const A = trPixelsOf("a", W, H), B = trPixelsOf("b", W, H);
    const out = ctx.createImageData(W, H), o = out.data;
    if (kind.style === "tear") {
        const map = trMap(s.kind, W, H), paper = [245, 240, 227];
        const halves = [[v.lx, v.ly, v.la, true], [v.rx, v.ry, v.ra, false]].map(([cx, cy, deg, left]) => {
            const t = -deg * Math.PI / 180;
            return {cx: cx * W, cy: cy * H, cos: Math.cos(t), sin: Math.sin(t), left};
        });
        const sx = .006 * W, sy = .01 * H;
        // 0 not this half's, 1 its paper, 2 its picture
        const part = (h, x, y) => {
            if (x < 0 || y < 0 || x >= W || y >= H) return 0;
            const m = map[(y | 0) * W + (x | 0)];
            if (h.left) return m < .5 + TR.TEAR_OVERLAP ? (m < .5 + TR.TEAR_OVERLAP - v.fringe ? 2 : 1) : 0;
            return m > .5 ? (m > .5 + v.fringe ? 2 : 1) : 0;
        };
        for (let y = 0; y < H; y++) {
            for (let x = 0; x < W; x++) {
                const j = (y * W + x) * 4;
                let r = B[j], g = B[j + 1], b = B[j + 2];
                for (const h of halves) {
                    const dx = x + .5 - h.cx, dy = (H - y - .5) - h.cy;
                    const ux = h.cos * dx - h.sin * dy + W / 2, uy = H - (h.sin * dx + h.cos * dy + H / 2);
                    const what = part(h, ux, uy);
                    if (what === 2) { const i = ((uy | 0) * W + (ux | 0)) * 4; r = A[i]; g = A[i + 1]; b = A[i + 2]; }
                    else if (what === 1) [r, g, b] = paper;
                    else if (part(h, ux - sx, uy - sy)) { r *= .45; g *= .45; b *= .45; }
                }
                o[j] = r; o[j + 1] = g; o[j + 2] = b; o[j + 3] = 255;
            }
        }
        return ctx.putImageData(out, 0, 0);
    }
    const map = trMap(s.kind, W, H, v.seethe), rgb = trRgb(s.color);
    const col = rgb.map(c => c * (kind.dark ? .12 : 1) * 255);
    const char = kind.style === "burn" ? [rgb[0] * .12, rgb[1] * .08, rgb[2] * .06].map(c => c * 255) : null;
    const ember = kind.style === "burn" ? new Uint8ClampedArray(W * H * 4) : null;
    const step = (m, at, soft) => Math.min(1, Math.max(0, (at + soft / 2 - m) / soft));
    const jx = Math.round(v.jx * W), jy = Math.round(v.jy * H);
    for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
            // The jitter: the picture moved, its edges held.
            const qx = Math.min(W - 1, Math.max(0, x - jx)), qy = Math.min(H - 1, Math.max(0, y + jy));
            const i = qy * W + qx, m = map[i], j = (y * W + x) * 4, k = i * 4;
            const a1 = step(m, v.m1, v.soft), a2 = step(m, v.m2, v.soft), a0 = char ? step(m, v.m0, 3 * v.soft) : 0;
            for (let c = 0; c < 3; c++) {
                let px = A[k + c];
                if (char) px = px * (1 - a0) + char[c] * a0;
                px = px * (1 - a1) + col[c] * a1;
                o[j + c] = px * (1 - a2) + B[k + c] * a2;
            }
            o[j + 3] = 255;
            if (ember) {
                const e = a1 * (1 - a2);
                for (let c = 0; c < 3; c++) ember[j + c] = col[c] * e;
                ember[j + 3] = 255;
            }
        }
    }
    ctx.putImageData(out, 0, 0);
    if (ember) {
        // The ember's glow, blurred and screened on.
        const glow = trScratch(W, H);
        glow.getContext("2d").putImageData(new ImageData(ember, W, H), 0, 0);
        ctx.save();
        ctx.globalCompositeOperation = "screen";
        ctx.globalAlpha = kind.glow;
        ctx.filter = `blur(${(TR.GLOW_SIZE * W / 1920 * .5).toFixed(1)}px)`;
        ctx.drawImage(glow, 0, 0);
        ctx.restore();
    }
    if (v.dirt > 0) {
        // Dust and scratches, a new throw each step.
        const seed = Math.round(v.seethe * 10);
        ctx.save();
        ctx.globalAlpha = Math.min(1, v.dirt);
        ctx.fillStyle = "rgb(13,10,8)";
        for (let n = 0; n < 40; n++) {
            const r = (.4 + 2 * trDice(seed + n, 21) ** 3) * W / 320;
            ctx.beginPath();
            ctx.ellipse(trDice(seed + n, 22) * W, trDice(seed + n, 23) * H, r, r * (.5 + trDice(seed + n, 24)), 0, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.fillStyle = "rgb(217,212,199)";
        for (let n = 0; n < 3; n++) ctx.fillRect(trDice(seed + n, 25) * W, 0, Math.max(1, W / 640), H);
        ctx.restore();
    }
}

/* The distortion group: each side's pixels looked up through the bend -
   stand-ins for Fusion's Vortex, Drip, the polar kaleidoscope, Dent and
   Displace, close, not exact - crossfaded, softened at the peak. */
function trDrawDistort(ctx, s, p) {
    const W = ctx.canvas.width, H = ctx.canvas.height, v = trDistort(s, p), kind = TR_KIND[s.kind];
    const cx = W / 2, cy = H / 2, out = ctx.createImageData(W, H), o = out.data;
    const sides = [["a", 1 - v.mix], ["b", v.mix]].filter(([, w]) => w > 0);
    const seg = Math.PI * 2 / (kind.segments || 6);
    for (const [side, weight] of sides) {
        const src = trPixelsOf(side, W, H), angle = (side === "a" ? v.angle_a : v.angle_b) || 0;
        const look = (x, y, c) => src[((Math.min(H - 1, Math.max(0, y | 0)) * W) + Math.min(W - 1, Math.max(0, x | 0))) * 4 + c];
        for (let y = 0; y < H; y++) {
            for (let x = 0; x < W; x++) {
                const dx = x + .5 - cx, dy = y + .5 - cy, r = Math.hypot(dx, dy);
                let ux = x, uy = y, k = 0, kx = 0, ky = 0;
                if (kind.style === "twirl") {
                    const R = TR.TWIRL_SIZE * W / 2;
                    if (r < R) {
                        const t = angle * Math.PI / 180 * (1 - r / R) ** 2, c = Math.cos(t), sn = Math.sin(t);
                        ux = cx + dx * c - dy * sn;
                        uy = cy + dx * sn + dy * c;
                    }
                } else if (kind.style === "wave") {
                    // Drip's Horizontal: rows pushed up and down in bands across the frame.
                    uy = y + v.amp * .2 * H * Math.sin(2 * Math.PI * (TR.RIPPLE_FREQ * y / H * .5 - v.phase));
                } else if (kind.style === "melt") {
                    // Pulled down in streaks: each column by its own noise.
                    uy = y - v.refract * H * trNoise2(x / W * 12, y / H * .6 + v.seethe * .3, 5);
                } else if (kind.style === "ripple") {
                    const push = v.amp * .2 * W * Math.sin(2 * Math.PI * (TR.RIPPLE_FREQ * r / W - v.phase));
                    if (r > 0) { ux = cx + dx * (r + push) / r; uy = cy + dy * (r + push) / r; }
                } else if (kind.style === "bulge") {
                    const R = TR.BULGE_SIZE * W / 2;
                    if (r < R) { const f = 1 - .85 * v.strength * (1 - r / R) ** 2; ux = cx + dx * f; uy = cy + dy * f; }
                } else if (kind.style === "glass") {
                    // Each block shows its piece of the picture shifted by where in the block it is.
                    const cw = W / TR.GLASS_CELLS, fx = x / cw - Math.floor(x / cw), fy = y / cw - Math.floor(y / cw);
                    ux = x + (fx - .5) * v.refract * W;
                    uy = y + (fy - .5) * v.refract * W;
                } else if (kind.style === "lens") {
                    // Bulged, then zoomed (Fusion's order): back through the zoom, then the bulge.
                    const z = side === "a" ? v.zoom_a : v.zoom_b, qx = dx / z, qy = dy / z, q = Math.hypot(qx, qy);
                    const R = TR.BULGE_SIZE * W / 2;
                    const f = q < R ? 1 - .85 * v.strength * (1 - q / R) ** 2 : 1;
                    ux = cx + qx * f;
                    uy = cy + qy * f;
                    // mirrored past the edges, as the Transform's are
                    ux = ((ux % (2 * W)) + 2 * W) % (2 * W); if (ux >= W) ux = 2 * W - ux - 1;
                    uy = ((uy % (2 * H)) + 2 * H) % (2 * H); if (uy >= H) uy = 2 * H - uy - 1;
                } else if (kind.style === "pinch") {
                    // Looked for further out the nearer the middle: drawn in to a point.
                    const R = TR.BULGE_SIZE * W / 2;
                    if (r < R) { const f = 1 / Math.max(.04, 1 - 4 * v.strength * (1 - r / R) ** 2); ux = cx + dx * f; uy = cy + dy * f; }
                } else if (kind.style === "warp") {
                    const sc = TR.WARP_SCALE, nx = x / W * sc + v.seethe * .3, ny = y / W * sc;
                    ux = x + (trNoise2(nx, ny, 3) - .5) * 2 * v.refract * W * 1.5;
                    uy = y + (trNoise2(nx, ny, 9) - .5) * 2 * v.refract * W * 1.5;
                } else if (v.kaleido > 0) {
                    // A wedge of the picture mirrored round, turned and zoomed.
                    let a = Math.atan2(-dy, dx) + v.spin * Math.PI * 2;
                    const n = Math.floor(a / seg);
                    a -= n * seg;
                    if (n % 2) a = seg - a;
                    // The wedge points right, over the sample pictures' sun and horizon.
                    a -= seg / 2;
                    const rr = r * 1.3 / v.zoom;
                    k = v.kaleido;
                    kx = cx + rr * Math.cos(a);
                    ky = cy - rr * Math.sin(a);
                }
                const j = (y * W + x) * 4;
                for (let c = 0; c < 3; c++) {
                    let px = look(ux, uy, c);
                    if (k > 0) px = px * (1 - k) + look(kx, ky, c) * k;
                    o[j + c] += px * weight;
                }
                o[j + 3] = 255;
            }
        }
    }
    ctx.putImageData(out, 0, 0);
    const blur = v.soft * W / 1920 * .5;
    if (blur < .3) return;
    // Softened over itself, so the blur's edges never show what was there before.
    const scratch = trScratch(W, H);
    scratch.getContext("2d").putImageData(out, 0, 0);
    ctx.save();
    ctx.filter = `blur(${blur.toFixed(1)}px)`;
    ctx.drawImage(scratch, 0, 0);
    ctx.restore();
}

/* The split-screen group: each piece a card of colour with its picture
   inside (moved and scaled with it, about its home), drawn on a scratch
   canvas and laid over the clip underneath blurred as it moves - Fusion's
   layers. Split open's clip underneath pushes in. */
function trDrawSplit(ctx, s, p, frames) {
    const W = ctx.canvas.width, H = ctx.canvas.height, kind = TR_KIND[s.kind], v = trSplit(s, p, frames);
    const col = "rgb(" + trRgb(s.color).map(c => Math.round(c * 255)).join(",") + ")", b = v.border * W;
    ctx.save();
    ctx.fillStyle = kind.src === "pair" && !kind.film ? col : "#000";
    ctx.fillRect(0, 0, W, H);
    if (kind.src !== "pair") {
        ctx.translate(W / 2, H / 2);
        ctx.scale(v.push, v.push);
        ctx.drawImage(TR_PICS[kind.src === "a" ? "b" : "a"], -W / 2, -H / 2, W, H);
    }
    ctx.restore();
    const scratch = trScratch(W, H), g = scratch.getContext("2d");
    trPieces(s.kind).forEach((piece, i) => {
        const [hx, hy, hw, hh] = piece.home, x = v.x[i], y = v.y[i], z = v.s[i], src = TR_PICS[piece.src];
        const zx = z * v.sw[i], zy = z * v.sh[i], w = hw * zx * W, h = hh * zy * H;
        if (w <= 0 || h <= 0) return;
        if (piece.half) {
            // A half of the corner-to-corner cut, moved.
            g.clearRect(0, 0, W, H);
            g.save();
            g.translate((x - hx) * W, -(y - hy) * H);
            g.beginPath();
            if (piece.half === "ul") { g.moveTo(0, 0); g.lineTo(W, 0); g.lineTo(0, H); }
            else { g.moveTo(W, 0); g.lineTo(W, H); g.lineTo(0, H); }
            g.closePath();
            g.clip();
            g.drawImage(src, 0, 0, W, H);
            g.restore();
            const blur = v.blur[i] * W * .35;
            ctx.save();
            ctx.filter = blur > .3 ? `blur(${blur.toFixed(1)}px)` : "none";
            ctx.drawImage(scratch, 0, 0);
            ctx.restore();
            return;
        }
        const left = x * W - w / 2, top = (1 - y) * H - h / 2;
        g.clearRect(0, 0, W, H);
        g.fillStyle = col;
        g.fillRect(left, top, w, h);
        if (w > 2 * b && h > 2 * b) {
            g.save();
            g.beginPath();
            g.rect(left + b, top + b, w - 2 * b, h - 2 * b);
            g.clip();
            g.translate(x * W, (1 - y) * H);
            g.scale(zx, zy);
            g.translate(-hx * W, -(1 - hy) * H);
            g.drawImage(src, 0, 0, W, H);
            g.restore();
        }
        const blur = v.blur[i] * W * .35;
        ctx.save();
        ctx.filter = blur > .3 ? `blur(${blur.toFixed(1)}px)` : "none";
        ctx.drawImage(scratch, 0, 0);
        ctx.restore();
    });
    if (kind.film) {
        // The sprocket holes: a column each side, riding with the frames.
        const g2 = scratch.getContext("2d"), light = "rgb(" + TR.FILM_HOLE.map(c => Math.round(c * 255)).join(",") + ")";
        g2.clearRect(0, 0, W, H);
        g2.fillStyle = light;
        const top = v.hole_y, pitch = v.hole_p;
        for (let k = Math.floor((top - 1) / pitch) - 1; k <= Math.ceil(top / pitch) + 1; k++) {
            const y0 = top - k * pitch;                 // a hole's top, y up
            for (const side of [-1, 1]) {
                const cx = (.5 + side * v.hole_x) * W;
                g2.fillRect(cx - v.hole_w * W / 2, (1 - y0) * H, v.hole_w * W, TR.FILM_DUTY * pitch * H);
            }
        }
        const blur = v.blur[0] * W * .35;
        ctx.save();
        ctx.filter = blur > .3 ? `blur(${blur.toFixed(1)}px)` : "none";
        ctx.drawImage(scratch, 0, 0);
        ctx.restore();
    }
}

/* The 3D group: each picture drawn in DVE's perspective (transition.turn_rig:
   the camera Perspective x Z Move in front of the pivot, the picture Z
   Pivot behind it), a slice at a time - columns for a turn round the
   upright, rows round the level - each slice straight, so it's exact. */
function trDrawPlane(ctx, pic, rig, persp, axis, deg, W, H, from = 0, to = 1) {
    const a = H / W, t = deg * Math.PI / 180, c = Math.cos(t), sn = Math.sin(t);
    const cam = persp * rig.zmove, [px, py] = rig.pivot, N = 48;
    const at = (X, Y) => {           // plane point, widths from the pivot (y up) -> screen pixels and its scale
        let x, y, z;
        if (axis === "y") { x = X * c - rig.zpivot * sn; y = Y; z = X * sn + rig.zpivot * c; }
        else { x = X; y = Y * c + rig.zpivot * sn; z = -Y * sn + rig.zpivot * c; }
        const k = persp / (cam + z);
        return [(px + x * k) * W, (1 - (py + y * k / a)) * H, k];
    };
    for (let i = 0; i < N; i++) {
        const u0 = i / N, u1 = (i + 1) / N;
        if (u1 <= from + 1e-9 || u0 >= to - 1e-9) continue;
        let p0, p1, q0, q1;
        if (axis === "y") {
            p0 = at(u0 - px, (1 - py) * a); p1 = at(u1 - px, (1 - py) * a);
            q0 = at(u0 - px, -py * a); q1 = at(u1 - px, -py * a);
            if (p0[2] <= 0 || p1[2] <= 0 || p1[0] <= p0[0]) continue;
            const x0 = p0[0], x1 = p1[0], top = (p0[1] + p1[1]) / 2, bot = (q0[1] + q1[1]) / 2;
            ctx.drawImage(pic, u0 * pic.width, 0, pic.width / N, pic.height, x0, top, x1 - x0 + .6, bot - top);
        } else {
            // rows, top down: v0 the slice's top (share of the height from the top)
            p0 = at(-px, (1 - u0 - py) * a); p1 = at(-px, (1 - u1 - py) * a);
            q0 = at(1 - px, (1 - u0 - py) * a); q1 = at(1 - px, (1 - u1 - py) * a);
            if (p0[2] <= 0 || p1[2] <= 0 || p1[1] <= p0[1]) continue;
            const y0 = p0[1], y1 = p1[1], left = (p0[0] + p1[0]) / 2, right = (q0[0] + q1[0]) / 2;
            ctx.drawImage(pic, 0, u0 * pic.height, pic.width, pic.height / N, left, y0, right - left, y1 - y0 + .6);
        }
    }
}

function trDrawTurn(ctx, s, p) {
    const W = ctx.canvas.width, H = ctx.canvas.height, kind = TR_KIND[s.kind], v = trTurn(s, p);
    const rig = trTurnRig(s.kind, v.persp);
    rig.zmove *= v.pull;
    ctx.save();
    if (kind.style === "louver") {
        ctx.fillStyle = "rgb(" + trRgb(s.color).map(c => Math.round(c * 255)).join(",") + ")";
        ctx.fillRect(0, 0, W, H);
        for (let i = 0; i < TR.LOUVERS; i++) {
            const y = 1 - (i + .5) / TR.LOUVERS, slat = {pivot: [.5, y], zpivot: 0, zmove: 1};
            const rows = [i / TR.LOUVERS, (i + 1) / TR.LOUVERS];
            if (v.show_b[i]) trDrawPlane(ctx, TR_PICS.b, slat, v.persp, "x", v.rot_b[i], W, H, ...rows);
            if (v.show_a[i]) trDrawPlane(ctx, TR_PICS.a, slat, v.persp, "x", v.rot_a[i], W, H, ...rows);
        }
        ctx.restore();
        return;
    }
    if (kind.style === "page") {
        // Under the page: the incoming clip, the outgoing one's left half on it; the page shaded as it stands.
        ctx.drawImage(TR_PICS.b, 0, 0, W, H);
        ctx.drawImage(TR_PICS.a, 0, 0, TR_PICS.a.width / 2, TR_PICS.a.height, 0, 0, W / 2, H);
        ctx.filter = `brightness(${v.shade.toFixed(3)})`;
        if (v.show_a) trDrawPlane(ctx, TR_PICS.a, rig, v.persp, "y", v.rot_a, W, H, .5, 1);
        if (v.show_b) trDrawPlane(ctx, TR_PICS.b, rig, v.persp, "y", v.rot_b, W, H, 0, .5);
        ctx.restore();
        return;
    }
    if (kind.style === "door") {
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, W, H);
        ctx.filter = `brightness(${v.shade.toFixed(3)})`;
        ctx.drawImage(TR_PICS.b, 0, 0, W, H);
        ctx.filter = "none";
    } else {
        ctx.fillStyle = "rgb(" + trRgb(s.color).map(c => Math.round(c * 255)).join(",") + ")";
        ctx.fillRect(0, 0, W, H);
        if (v.show_b) trDrawPlane(ctx, TR_PICS.b, rig, v.persp, kind.axis, v.rot_b, W, H);
    }
    if (v.show_a) trDrawPlane(ctx, TR_PICS.a, rig, v.persp, kind.axis, v.rot_a, W, H);
    ctx.restore();
}

const trScratches = {};
function trScratch(W, H) {
    const key = W + "x" + H;
    if (!trScratches[key]) {
        const c = document.createElement("canvas");
        c.width = W;
        c.height = H;
        trScratches[key] = c;
    }
    return trScratches[key];
}

// The light group: the two pictures crossed, brightened and softened, the light screened over.
function trDrawLight(ctx, s, p) {
    const W = ctx.canvas.width, H = ctx.canvas.height, v = trLight(s, p), kind = TR_KIND[s.kind];
    ctx.save();
    const px = (v.soft + (kind.fx === "bokeh" ? v.focus * .6 : 0)) * W / 1920;
    if (kind.axis === "zoom" || kind.axis === "spin") {
        // Smeared out from the middle (or round it): copies grown (or turned) a little each, averaged - the
        // first one as it is, so nothing's uncovered.
        const L = v.soft / TR.SOFT_MAX, n = 9;
        ctx.filter = "brightness(" + v.gain.toFixed(3) + ")";
        for (const [pic, w] of [[TR_PICS.a, 1], [TR_PICS.b, v.mix]]) {
            if (w <= 0) continue;
            for (let i = 0; i < n; i++) {
                const t = i === 0 ? 0 : (i % 2 ? 1 : -1) * Math.ceil(i / 2) / ((n - 1) / 2);
                ctx.save();
                ctx.globalAlpha = w / (i + 1);
                ctx.translate(W / 2, H / 2);
                if (kind.axis === "zoom") ctx.scale(1 + L * Math.abs(t), 1 + L * Math.abs(t));
                else ctx.rotate(t * L * Math.PI);
                ctx.drawImage(pic, -W / 2, -H / 2, W, H);
                ctx.restore();
            }
        }
    } else if (kind.axis === "x") {
        // Smeared across only: copies side by side, averaged.
        ctx.filter = "brightness(" + v.gain.toFixed(3) + ")";
        const n = 9;
        for (const [pic, w] of [[TR_PICS.a, 1], [TR_PICS.b, v.mix]]) {
            if (w <= 0) continue;
            for (let i = 0; i < n; i++) {
                ctx.globalAlpha = w / (i + 1);
                ctx.drawImage(pic, (i / (n - 1) - .5) * px * 1.5, 0, W, H);
            }
        }
    } else {
        ctx.filter = (px > .3 ? "blur(" + px.toFixed(1) + "px) " : "") + "brightness(" + v.gain.toFixed(3) + ")";
        ctx.drawImage(TR_PICS.a, 0, 0, W, H);
        if (v.mix > 0) {
            ctx.globalAlpha = v.mix;
            ctx.drawImage(TR_PICS.b, 0, 0, W, H);
        }
    }
    ctx.globalAlpha = 1;
    ctx.filter = "none";
    if (kind.fx === "invert" && v.fx > 0) {
        ctx.globalCompositeOperation = "difference";
        ctx.globalAlpha = v.fx;
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
        return;
    }
    if (kind.fx === "bloom" || kind.fx === "rays") {
        // Its own brights, spread: blurred (bloom) or smeared out from a point (rays), added on.
        const glow = trScratch(W, H), g = glow.getContext("2d");
        g.clearRect(0, 0, W, H);
        g.filter = "brightness(.7) contrast(4)";          // the brights only
        g.drawImage(ctx.canvas, 0, 0);
        g.filter = "none";
        ctx.globalCompositeOperation = "screen";
        if (kind.fx === "bloom") {
            ctx.filter = `blur(${(W / 25).toFixed(1)}px)`;
            ctx.globalAlpha = .75 * v.fx;
            ctx.drawImage(glow, 0, 0);
        } else {
            const cx = v.sweep * W, cy = (1 - TR.RAYS_CENTER_Y) * H;
            ctx.globalAlpha = .22 * v.fx;
            for (let i = 1; i <= 7; i++) {
                const z = 1 + i * .06;
                ctx.drawImage(glow, cx - cx * z, cy - cy * z, W * z, H * z);
            }
        }
        ctx.restore();
        return;
    }
    if (kind.light === "none") return ctx.restore();
    ctx.globalAlpha = v.glow;
    if (kind.light === "dip") {             // laid over, not lit
        ctx.fillStyle = "rgb(" + trRgb(s.color).map(c => Math.round(c * 255)).join(",") + ")";
        ctx.fillRect(0, 0, W, H);
        return ctx.restore();
    }
    ctx.globalCompositeOperation = "screen";
    if (kind.light === "band") {
        // A soft slanted band crossing the frame.
        const [r, g, b] = trRgb(s.color).map(c => Math.round(c * 255)), half = (TR.BAND_WIDTH / 2 + TR.BAND_SOFT) * W;
        ctx.translate(v.band * W, H / 2);
        ctx.rotate(-TR.BAND_ANGLE * Math.PI / 180);
        const grad = ctx.createLinearGradient(-half, 0, half, 0), edge = TR.BAND_SOFT * W / (2 * half);
        grad.addColorStop(0, `rgba(${r},${g},${b},0)`);
        grad.addColorStop(edge * 2, `rgba(${r},${g},${b},1)`);
        grad.addColorStop(1 - edge * 2, `rgba(${r},${g},${b},1)`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grad;
        ctx.fillRect(-half, -H * 2, 2 * half, H * 4);
        return ctx.restore();
    }
    if (kind.light === "flare") {
        // An anamorphic streak, wide and thin, swept across.
        const [r, g, b] = trRgb(s.color).map(c => Math.round(c * 255)), cx = v.sweep * W, cy = .45 * H;
        ctx.translate(cx, cy);
        ctx.scale(5, 1);
        const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, H * .3);
        grad.addColorStop(0, "rgba(255,255,255,1)");
        grad.addColorStop(.25, `rgba(${r},${g},${b},.9)`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grad;
        ctx.fillRect(-H * .3, -H * .3, H * .6, H * .6);
    } else if (kind.light === "noise") {
        const tex = trLightTexture(s.kind, s.color), drift = (v.sweep - .5) * W;
        ctx.drawImage(tex, -W / 2 - drift, 0, W * 2, H);
    } else {
        ctx.fillStyle = "rgb(" + trRgb(s.color).map(c => Math.round(c * 255)).join(",") + ")";
        ctx.fillRect(0, 0, W, H);
    }
    ctx.restore();
}

// One side's picture, mirrored out past its edges as Fusion's Edges: Mirror does.
function trDraw(ctx, s, p, frames) {
    if (trGroup(s.kind) === "light") return trDrawLight(ctx, s, p);
    if (trGroup(s.kind) === "wipe") return trDrawWipe(ctx, s, p);
    if (trGroup(s.kind) === "glitch") return trDrawGlitch(ctx, s, p, frames);
    if (trGroup(s.kind) === "paper") return trDrawPaper(ctx, s, p);
    if (trGroup(s.kind) === "distort") return trDrawDistort(ctx, s, p);
    if (trGroup(s.kind) === "split") return trDrawSplit(ctx, s, p, frames);
    if (trGroup(s.kind) === "turn") return trDrawTurn(ctx, s, p);
    const W = ctx.canvas.width, H = ctx.canvas.height;
    const v = trAt(s, p, frames);
    let pic = TR_PICS[v.side];
    if (v.nest > 0) {
        // The picture with its nested copies, as strong as the swell.
        const nest = trScratch(W, H), g = nest.getContext("2d");
        g.globalAlpha = 1;
        g.drawImage(pic, 0, 0, W, H);
        g.globalAlpha = v.nest;
        for (let i = 1, z = TR.NEST_STEP; i <= TR.NEST_COPIES; i++, z *= TR.NEST_STEP) {
            g.drawImage(pic, (W - W * z) / 2, (H - H * z) / 2, W * z, H * z);
        }
        g.globalAlpha = 1;
        pic = nest;
    }
    ctx.save();
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    // A canvas blur is round, not directional: a little less reads closer.
    const px = v.blur * (v.move === "pan" || v.move === "shake" || v.move === "stretch" ? W : W * .4) * .35;
    ctx.filter = px > .3 ? `blur(${px.toFixed(1)}px)` : "none";
    ctx.translate(v.cx * W, (1 - v.cy) * H);
    if (v.move === "roll") {                 // turned round the point far below the frame
        const down = (.5 - TR.ROLL_PIVOT) * H;
        ctx.translate(0, down);
        ctx.rotate(-v.deg * Math.PI / 180);
        ctx.translate(0, -down);
    } else ctx.rotate(-v.deg * Math.PI / 180);
    ctx.scale(v.sx, v.sy);
    const reach = Math.min(4, Math.ceil(1.5 / Math.min(v.sx, v.sy)) + 1);
    for (let i = -reach; i <= reach; i++) {
        for (let j = -reach; j <= reach; j++) {
            ctx.save();
            ctx.translate(i * W, j * H);
            ctx.scale(i % 2 ? -1 : 1, j % 2 ? -1 : 1);
            ctx.drawImage(pic, -W / 2 - 1, -H / 2 - 1, W + 2, H + 2);    // overlapped: no seams between copies
            ctx.restore();
        }
    }
    ctx.restore();
    if (v.chroma > .001) {
        // Red a little bigger, blue a little smaller, about the middle.
        const img = ctx.getImageData(0, 0, W, H), src = new Uint8ClampedArray(img.data), o = img.data;
        const look = (x, y, c, z) => {
            const ux = Math.min(W - 1, Math.max(0, Math.round(W / 2 + (x - W / 2) / z)));
            const uy = Math.min(H - 1, Math.max(0, Math.round(H / 2 + (y - H / 2) / z)));
            return src[(uy * W + ux) * 4 + c];
        };
        for (let y = 0; y < H; y++) {
            for (let x = 0; x < W; x++) {
                const j = (y * W + x) * 4;
                o[j] = look(x, y, 0, 1 + v.chroma);
                o[j + 2] = look(x, y, 2, 1 - v.chroma);
            }
        }
        ctx.putImageData(img, 0, 0);
    }
    if (v.flash > 1.001) {
        // A punch's flash: the frame brightened.
        const lit = trScratch(W, H), g = lit.getContext("2d");
        g.clearRect(0, 0, W, H);
        g.filter = `brightness(${v.flash.toFixed(3)})`;
        g.drawImage(ctx.canvas, 0, 0);
        g.filter = "none";
        ctx.drawImage(lit, 0, 0);
    }
}

// ---- the tiles.

const trTiles = [];
const TR_BUDGET = 12;           // ms of tile drawing a frame: the rest wait for the next ones
// Which tiles are on screen (the scroll box and the page clip them): only those are drawn.
const trSeen = new IntersectionObserver(entries => {
    for (const e of entries) {
        const t = trTiles.find(t => t.node === e.target);
        if (t) t.seen = e.isIntersecting;
    }
});

function trOptions() {
    return {kind: "zoom_in", feel: "ease", length: .6, amount: 1, blur: .6, color: "warm", ...(state.tr || {})};
}

function trBuild() {
    if (trTiles.length) return;
    TR_PICS.a = trPicture(true);
    TR_PICS.b = trPicture(false);
    TR_KINDS.forEach(([id, label], index) => {
        const canvas = el("canvas", {width: 320, height: 180});
        const choose = () => trSet({kind: id});
        const node = el("div.card.tr-tile", {
            role: "button", tabindex: "0", "aria-pressed": "false", title: label, onclick: choose,
            onkeydown: e => {
                if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); choose(); }
            },
        }, [el("div.tr-stage", {}, [canvas]), el("span.tr-name", {text: label})]);
        const t = {id, node, ctx: canvas.getContext("2d"), offset: index * .23, hoverAt: null, seen: false};
        trSeen.observe(node);
        // Play: On hover - a tile plays from the start while the pointer (or the keyboard) is on it.
        node.addEventListener("mouseenter", () => { t.hoverAt = performance.now(); });
        node.addEventListener("mouseleave", () => { t.hoverAt = null; });
        node.addEventListener("focus", () => { t.hoverAt = performance.now(); });
        node.addEventListener("blur", () => { t.hoverAt = null; });
        trTiles.push(t);
    });
    $("tr-grid").replaceChildren(...TR_GROUPS.flatMap(([group, title]) => [
        el("div.tr-group", {text: title}),
        ...trTiles.filter(t => trGroup(t.id) === group).map(t => t.node),
    ]));
}

function trTick(now) {
    if ($("panel-transitions").hidden || !trTiles.length) return;
    const o = trOptions(), fps = Number(state.tr_selection?.fps) || 24;
    const frames = Math.max(4, Math.round(o.length * fps));
    const cycle = o.length + 2 * TR_HOLD, said = JSON.stringify(o) + frames;
    const hover = state.play === "hover", start = performance.now();
    for (const t of trTiles) {
        // Off screen, nothing; on it, as many as fit the frame's budget (a
        // change of option redraws them all - 0.3-0.8 s at once, measured).
        if (!t.seen || performance.now() - start > TR_BUDGET) continue;
        // Whole frames, as Resolve shows them: a fast move's jumps show too.
        // On hover, a tile at rest holds one frame; hovered, it starts at once.
        let p = (Math.floor(trRest(t.id) * frames) + .5) / frames;
        if (!hover || t.hoverAt !== null) {
            const local = hover ? ((now - t.hoverAt) / 1000 % cycle) - .1 : ((now / 1000 + t.offset) % cycle) - TR_HOLD;
            const f = Math.floor(local / o.length * frames);
            p = local < 0 ? 0 : local >= o.length ? 1 : (f + .5) / frames;
        }
        // A tile only changes a frame at a time: drawn again only then (the
        // pixel-by-pixel ones cost a few ms each, some tens).
        const shown = p + "|" + said;
        if (t.shown === shown) continue;
        t.shown = shown;
        trDraw(t.ctx, {...o, kind: t.id}, p, frames);
    }
}

// ---- choices.

// Changes go to page.py together: a slider sends after it rests, a click at once,
// and one never drops another still waiting to go.
let trSendTimer = 0, trPending = {};
function trSet(change) {
    state.tr = {...trOptions(), ...change};
    trPending = {...trPending, ...change};
    clearTimeout(trSendTimer);
    trSendTimer = setTimeout(() => {
        const sending = trPending;
        trPending = {};
        send("tr_option", sending);
    }, "kind" in change || "feel" in change ? 0 : 150);
    trSync();
}

for (const b of $("tr-feel").querySelectorAll("button")) b.onclick = () => trSet({feel: b.dataset.feel});
// Play is the Previews tab's setting too: one choice for both.
for (const b of $("tr-play").querySelectorAll("button")) b.onclick = () => send("play", {play: b.dataset.play});
for (const b of $("tr-color").querySelectorAll("button[data-color]")) b.onclick = () => trSet({color: b.dataset.color});
// Any colour: Buddy's own picker, starting from the colour in use.
$("tr-color-any").onclick = e => {
    const now = trRgb(trOptions().color).map(v => Math.round(v * 255).toString(16).padStart(2, "0")).join("");
    Buddy.pickColor({
        hex: "#" + now, title: "Colour", at: e.currentTarget.getBoundingClientRect(),
        onPick: hex => trSet({color: String(hex).toLowerCase()}),
    });
};
for (const [id, fmt] of [["length", v => `${v.toFixed(2)}s`], ["amount", v => `${v.toFixed(2)}×`],
                         ["blur", v => `${Math.round(v * 100)}%`]]) {
    $(`tr-${id}`).addEventListener("input", e => {
        const value = Number(e.target.value);
        $(`tr-${id}-text`).textContent = fmt(value);
        trSet({[id]: value});
    });
}

/* What the selection's cuts are, said in a line - whole phrases, each its
   own span, so each translates - and whether a transition of `frames` fits
   each (transition_resolve.fit: centred on half each side, else wholly to
   one side). */
function trWhere(sel, frames) {
    if (!state.connected) return ["Not connected to Resolve"];
    if (state.problem) return [state.problem];
    if (!sel) return [state.resolve_busy ? "Resolve is busy – Buddy carries on once playback stops" : "Reading the selection…"];
    const cuts = sel.cuts || [];
    if (!cuts.length) return [sel.selected ? "No cuts between or beside the selected clips" : "Select clips around a cut in Resolve"];
    const fits = room => {
        const [l, r] = room.map(v => (v == null ? Infinity : v)), half = Math.ceil(frames / 2);
        return (l >= half && r >= half) || l >= frames || r >= frames;
    };
    const short = cuts.filter(c => !fits(c.room)).length;
    const there = cuts.filter(c => c.existing).length;
    const parts = [cuts.length === 1 ? "1 cut" : `${cuts.length} cuts`];
    if (there) parts.push(there === 1 ? "1 has a transition" : `${there} have transitions`);
    if (short) parts.push(short === 1 ? "1 will be shorter" : `${short} will be shorter`);
    return parts;
}

function trSync() {
    trBuild();
    const o = trOptions(), sel = state.tr_selection, cuts = sel?.cuts || [];
    const fps = Number(sel?.fps) || 24, frames = Math.max(4, Math.round(o.length * fps));
    for (const t of trTiles) t.node.setAttribute("aria-pressed", String(t.id === o.kind));
    pressed($("tr-feel"), "feel", o.feel);
    pressed($("tr-play"), "play", state.play);
    pressed($("tr-color"), "color", o.color);
    const custom = String(o.color).startsWith("#");
    $("tr-color-any").setAttribute("aria-pressed", String(custom));
    $("tr-color-any").firstChild.style.background = custom ? o.color : "";
    // Colour is the light's and the wipe's band; Amount there is how bright / how wide.
    $("tr-color-box").hidden = ["motion", "glitch", "distort"].includes(trGroup(o.kind)) || TR_KIND[o.kind].color === false;
    for (const [id, fmt] of [["length", v => `${v.toFixed(2)}s`], ["amount", v => `${v.toFixed(2)}×`],
                             ["blur", v => `${Math.round(v * 100)}%`]]) {
        if (document.activeElement !== $(`tr-${id}`)) $(`tr-${id}`).value = o[id];
        $(`tr-${id}-text`).textContent = fmt(Number(o[id]));
    }
    const where = trWhere(sel, frames);
    if (JSON.stringify(where) !== $("tr-where").dataset.said) {
        $("tr-where").dataset.said = JSON.stringify(where);
        $("tr-where").replaceChildren(...where.flatMap((text, i) => [i ? " · " : null, el("span", {text})]).filter(Boolean));
    }
    $("tr-where").title = cuts.map(c => `V${c.track}: ${c.left} → ${c.right}${c.existing ? ` (${c.existing})` : ""}`).join("\n");
    const busy = !!state.working;
    $("tr-apply").disabled = busy || !cuts.length;
    $("tr-apply").textContent = state.working === "transition" ? "Applying…"
        : cuts.length > 1 ? `Apply to ${cuts.length} cuts` : "Apply";
    $("tr-remove").disabled = busy || !cuts.some(c => c.existing);
    $("tr-remove").textContent = state.working === "untransition" ? "Removing…" : "Remove";
}

$("tr-apply").onclick = () => {
    const cuts = state.tr_selection?.cuts || [];
    if (cuts.length) send("tr_apply", {cuts: cuts.map(c => c.id)});
};
$("tr-remove").onclick = () => {
    const cuts = (state.tr_selection?.cuts || []).filter(c => c.existing);
    if (cuts.length) send("tr_remove", {cuts: cuts.map(c => c.id)});
};
