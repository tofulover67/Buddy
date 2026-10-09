/* Checkers' rules (American checkers / English draughts) and the
   computer's play. checkers.js uses the rules on the page; the same file is
   started again as a Web Worker, where the computer thinks (checkers.js
   falls back to the page if a worker can't start).

   The 32 dark squares are numbered as in checkers notation: 1-4 along the
   top row (Black's side) to 29-32 along the bottom (White's). Pieces: 1 a
   Black man, 2 a Black king, -1 and -2 White's. Black moves first, down
   the board. Captures are forced and a jump goes on while it can; a man
   reaching the far row is crowned, and that ends the move. A move is
   {from, path: [squares landed on], caps: [squares jumped], crown}. */
"use strict";

function checkersRules() {
    const MATE = 30000;
    const row = s => s >> 2;
    const col = s => 2 * (s & 3) + (row(s) % 2 === 0 ? 1 : 0);
    const at = (r, c) => (r < 0 || r > 7 || c < 0 || c > 7 || (r + c) % 2 === 0) ? -1 : r * 4 + (c >> 1);
    // Directions: 0 up-left, 1 up-right (White's way), 2 down-left, 3 down-right (Black's way).
    const DR = [-1, -1, 1, 1], DC = [-1, 1, -1, 1];
    const STEP = [], JUMP = [];
    for (let s = 0; s < 32; s++) {
        STEP[s] = DR.map((dr, d) => at(row(s) + dr, col(s) + DC[d]));
        JUMP[s] = DR.map((dr, d) => at(row(s) + 2 * dr, col(s) + 2 * DC[d]));
    }
    const dirsOf = p => (p === 2 || p === -2) ? [0, 1, 2, 3] : p > 0 ? [2, 3] : [0, 1];
    const crowns = (p, s) => (p === 1 && row(s) === 7) || (p === -1 && row(s) === 0);

    let seed = 0x6c8e9cf5;
    const rnd = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return seed | 0; };
    const ZH = new Int32Array(5 * 32).map(rnd), ZL = new Int32Array(5 * 32).map(rnd);
    const SH = rnd(), SL = rnd();

    const b = new Int8Array(32);
    let side = 1, quiet = 0, hi = 0, lo = 0;
    const undo = [];
    const seen = [];              // hashes of every position so far, for repeats

    function hashAll() {
        hi = side < 0 ? SH : 0; lo = side < 0 ? SL : 0;
        for (let s = 0; s < 32; s++) if (b[s]) { hi ^= ZH[(b[s] + 2) * 32 + s]; lo ^= ZL[(b[s] + 2) * 32 + s]; }
    }
    function flip(s, p) { hi ^= ZH[(p + 2) * 32 + s]; lo ^= ZL[(p + 2) * 32 + s]; }

    function reset() {
        b.fill(0);
        for (let s = 0; s < 12; s++) b[s] = 1;
        for (let s = 20; s < 32; s++) b[s] = -1;
        side = 1; quiet = 0;
        undo.length = 0;
        hashAll();
        seen.length = 0;
        seen.push(hi, lo);
    }

    /* From a picture: 32 characters, b/B Black man/king, w/W White's, . empty; then the side to move. */
    function load(text, toMove = 1) {
        for (let s = 0; s < 32; s++) b[s] = {b: 1, B: 2, w: -1, W: -2}[text[s]] || 0;
        side = toMove; quiet = 0;
        undo.length = 0;
        hashAll();
        seen.length = 0;
        seen.push(hi, lo);
    }

    function jumps(out, from, s, p, path, caps) {
        let more = false;
        if (!(path.length && crowns(p, s))) {
            for (const d of dirsOf(p)) {
                const over = STEP[s][d], land = JUMP[s][d];
                if (land < 0 || b[land] || b[over] * p >= 0 || caps.includes(over)) continue;
                more = true;
                jumps(out, from, land, p, [...path, land], [...caps, over]);
            }
        }
        if (!more && path.length) out.push({from, path, caps, crown: crowns(p, s)});
    }

    /* Every legal move: the captures if there are any (they're forced). */
    function legal() {
        const out = [];
        for (let s = 0; s < 32; s++) {
            const p = b[s];
            if (p * side <= 0) continue;
            b[s] = 0;                     // lifted: its own square can be crossed again
            jumps(out, s, s, p, [], []);
            b[s] = p;
        }
        if (out.length) return out;
        for (let s = 0; s < 32; s++) {
            const p = b[s];
            if (p * side <= 0) continue;
            for (const d of dirsOf(p)) {
                const t = STEP[s][d];
                if (t >= 0 && !b[t]) out.push({from: s, path: [t], caps: [], crown: crowns(p, t)});
            }
        }
        return out;
    }

    function make(m) {
        const p = b[m.from], to = m.path[m.path.length - 1];
        const taken = m.caps.map(c => b[c]);
        undo.push({m, p, taken, quiet, hi, lo});
        b[m.from] = 0; flip(m.from, p);
        m.caps.forEach(c => { flip(c, b[c]); b[c] = 0; });
        const now = m.crown ? 2 * Math.sign(p) : p;
        b[to] = now; flip(to, now);
        quiet = m.caps.length || Math.abs(p) === 1 ? 0 : quiet + 1;
        side = -side;
        hi ^= SH; lo ^= SL;
        seen.push(hi, lo);
    }

    function unmake() {
        const u = undo.pop();
        const to = u.m.path[u.m.path.length - 1];
        b[to] = 0;
        u.m.caps.forEach((c, i) => { b[c] = u.taken[i]; });
        b[u.m.from] = u.p;
        quiet = u.quiet; hi = u.hi; lo = u.lo;
        side = -side;
        seen.length -= 2;
    }

    // How many times this position has been seen, this time included.
    function repeats() {
        let n = 0;
        for (let i = seen.length - 2; i >= 0 && i >= seen.length - 2 - 2 * quiet; i -= 4) {
            if (seen[i] === hi && seen[i + 1] === lo) n += 1;
        }
        return n;
    }

    /* How things stand: {over, result, winner}; result is "nomoves"
       (the side to move has no pieces or can't move - it loses),
       "forty" (40 moves each without a capture or a man moving) or
       "repetition" (the same position a third time) - both draws. */
    function status() {
        if (!legal().length) return {over: true, result: "nomoves", winner: -side};
        if (quiet >= 80) return {over: true, result: "forty", winner: 0};
        if (repeats() >= 3) return {over: true, result: "repetition", winner: 0};
        return {over: false, result: "", winner: 0};
    }

    /* In checkers notation: 11-15 for a step, 22x15 or 22x15x6 for jumps. */
    function notation(m) {
        return [m.from, ...m.path].map(s => s + 1).join(m.caps.length ? "x" : "-");
    }

    /* --------------------------------------------- the computer -- */
    const CENTRE = new Int8Array(32).map((_, s) => (col(s) >= 2 && col(s) <= 5 && row(s) >= 2 && row(s) <= 5) ? 1 : 0);

    function evaluate() {
        let score = 0, mine = 0, theirs = 0, men = [0, 0];
        for (let s = 0; s < 32; s++) {
            const p = b[s];
            if (!p) continue;
            const sign = p > 0 ? 1 : -1;
            if (p * side > 0) mine += 1; else theirs += 1;
            if (p === 1 || p === -1) {
                men[p > 0 ? 0 : 1] += 1;
                const ahead = p > 0 ? row(s) : 7 - row(s);
                score += sign * (100 + ahead * 3 + CENTRE[s] * 4);
                if (ahead === 0) score += sign * 6;          // the back row, guarding the crowning squares
            } else {
                score += sign * (155 + CENTRE[s] * 6);
            }
        }
        let own = score * side;
        // Ahead: trade down, and bring the kings to the other side's pieces.
        const total = mine + theirs;
        if (own > 80) own += (24 - total) * 4;
        else if (own < -80) own -= (24 - total) * 4;
        if (Math.abs(own) > 80 && total <= 10) {
            const lead = own > 0 ? side : -side;
            let near = 0;
            for (let s = 0; s < 32; s++) {
                if (b[s] !== 2 * lead) continue;
                let best = 14;
                for (let t = 0; t < 32; t++) {
                    if (b[t] * lead < 0) best = Math.min(best, Math.max(Math.abs(row(s) - row(t)), Math.abs(col(s) - col(t))));
                }
                near += best;
            }
            own += (lead === side ? -1 : 1) * near * 2;
        }
        return own;
    }

    let nodes = 0, stopAt = 0, stopped = false, mayStop = false;

    function negamax(depth, alpha, beta, h) {
        if ((++nodes & 1023) === 0 && mayStop && Date.now() > stopAt) stopped = true;
        if (stopped) return 0;
        if (h > 0 && (quiet >= 80 || repeats() >= 2)) return 0;     // been here before: a draw
        const moves = legal();
        if (!moves.length) return -MATE + h;
        // Out of depth: keep going while captures are on (they're forced), up to a limit.
        if (depth <= 0 && (!moves[0].caps.length || h > 40)) return evaluate();
        moves.sort((x, y) => y.caps.length - x.caps.length || (y.crown ? 1 : 0) - (x.crown ? 1 : 0));
        let best = -MATE - 1;
        for (const m of moves) {
            make(m);
            const score = -negamax(depth - 1, -beta, -alpha, h + 1);
            unmake();
            if (stopped) return 0;
            if (score > best) best = score;
            if (score > alpha) alpha = score;
            if (alpha >= beta) break;
        }
        return best;
    }

    const LEVELS = {
        easy: {depth: 2, ms: 300, noise: 70, slip: 0.3},
        medium: {depth: 6, ms: 800, noise: 12, slip: 0},
        hard: {depth: 30, ms: 1500, noise: 0, slip: 0},
    };

    /* The computer's move for the side to move (an index into legal()). */
    function think(levelName) {
        const level = LEVELS[levelName] || LEVELS.medium;
        const root = legal().map((m, i) => ({m, i}));
        if (root.length <= 1) return root.length ? 0 : -1;
        for (let k = root.length - 1; k > 0; k--) {
            const j = Math.floor(Math.random() * (k + 1));
            [root[k], root[j]] = [root[j], root[k]];
        }
        nodes = 0; stopped = false; stopAt = Date.now() + level.ms;
        let best = root[0], scores = [];
        for (let depth = 1; depth <= level.depth; depth++) {
            mayStop = depth > 1;
            const done = [];
            let alpha = -MATE - 1, bestNow = null;
            for (const r of root) {
                make(r.m);
                const score = -negamax(depth - 1, -MATE - 1, level.noise ? MATE + 1 : -alpha, 1);
                unmake();
                if (stopped) break;
                done.push({r, score});
                if (score > alpha) { alpha = score; bestNow = r; }
            }
            if (stopped) break;
            best = bestNow || best;
            scores = done;
            root.sort((x, y) => (x === best ? -1 : y === best ? 1 : 0));
            if (alpha > MATE - 500) break;
            if (Date.now() > stopAt) break;
        }
        if (level.noise && scores.length) {
            const top = Math.max(...scores.map(s => s.score));
            const margin = level.slip && Math.random() < level.slip ? 150 : level.noise * 2;
            let pick = best, pickScore = -Infinity;
            for (const {r, score} of scores) {
                if (score < top - margin || score < -MATE + 500) continue;
                const v = score + (Math.random() * 2 - 1) * level.noise;
                if (v > pickScore) { pickScore = v; pick = r; }
            }
            best = pick;
        }
        return best.i;
    }

    function count(depth) {
        if (!depth) return 1;
        let n = 0;
        for (const m of legal()) { make(m); n += count(depth - 1); unmake(); }
        return n;
    }

    reset();
    return {
        reset, load, legal, make, unmake, status, notation, think, evaluate, count, row, col, at,
        get board() { return b; }, get side() { return side; }, get nodes() { return nodes; }, get quiet() { return quiet; },
        get moves() { return undo.map(u => u.m); },
    };
}

/* Started as a worker: think about a position when asked. */
if (typeof window === "undefined" && typeof importScripts === "function") {
    const rules = checkersRules();
    self.onmessage = e => {
        const {id, moves, level} = e.data;
        rules.reset();
        for (const i of moves) rules.make(rules.legal()[i]);
        self.postMessage({id, move: rules.think(level)});
    };
}
