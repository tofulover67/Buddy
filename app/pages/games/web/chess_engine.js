/* Chess's rules and the computer's play. chess.js uses the rules on the
   page; the same file is started again as a Web Worker, where the computer
   thinks - so the board never freezes while it does (chess.js falls back to
   thinking on the page if a worker can't start).

   Squares are 0 (a1) to 63 (h8); pieces 1-6 (pawn, knight, bishop, rook,
   queen, king), positive for White and negative for Black. A move is one
   number: from | to << 6 | promotion << 12 | kind << 15 (kind: 1 en
   passant, 2 castling, 3 a pawn's two-square step). */
"use strict";

function chessRules() {
    const P = 1, N = 2, B = 3, R = 4, Q = 5, K = 6;
    const F_EP = 1, F_CASTLE = 2, F_DOUBLE = 3;
    const VALUE = [0, 100, 320, 330, 500, 900, 0];
    const MATE = 30000;
    const rank = s => s >> 3;

    /* ------------------------------------------------- the geometry -- */
    const DIRS = [[0, 1], [0, -1], [1, 0], [-1, 0], [1, 1], [1, -1], [-1, 1], [-1, -1]];  // rook's 4, then bishop's
    const inside = (f, r) => f >= 0 && f < 8 && r >= 0 && r < 8;
    const KNIGHT = [], KING = [], RAYS = [], PAWN_ATT = [[], []], PAWN_FROM = [[], []];
    for (let s = 0; s < 64; s++) {
        const f = s & 7, r = s >> 3;
        const hop = list => list.filter(([df, dr]) => inside(f + df, r + dr)).map(([df, dr]) => s + df + dr * 8);
        KNIGHT[s] = hop([[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2], [-2, -1], [-2, 1], [-1, 2]]);
        KING[s] = hop(DIRS);
        RAYS[s] = DIRS.map(([df, dr]) => {
            const out = [];
            for (let ff = f + df, rr = r + dr; inside(ff, rr); ff += df, rr += dr) out.push(ff + rr * 8);
            return out;
        });
        PAWN_ATT[0][s] = hop([[-1, 1], [1, 1]]);       // where a White pawn here takes
        PAWN_ATT[1][s] = hop([[-1, -1], [1, -1]]);
        PAWN_FROM[0][s] = hop([[-1, -1], [1, -1]]);    // where a White pawn taking here stands
        PAWN_FROM[1][s] = hop([[-1, 1], [1, 1]]);
    }
    // Castling rights a move from or to a square leaves: 1 White short, 2 White long, 4 Black short, 8 Black long.
    const KEEPS = new Uint8Array(64).fill(15);
    KEEPS[0] = 13; KEEPS[7] = 14; KEEPS[4] = 12; KEEPS[56] = 7; KEEPS[63] = 11; KEEPS[60] = 3;

    /* Hash keys, two 32-bit halves each (same every run: a fixed seed). */
    let seed = 0x2545f491;
    const rnd = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return seed | 0; };
    const ZH = new Int32Array(13 * 64).map(rnd), ZL = new Int32Array(13 * 64).map(rnd);
    const CH = new Int32Array(16).map(rnd), CL = new Int32Array(16).map(rnd);
    const EH = new Int32Array(8).map(rnd), EL = new Int32Array(8).map(rnd);
    const SH = rnd(), SL = rnd();

    /* ------------------------------------------------- the position -- */
    const b = new Int8Array(64);
    let side = 1, castle = 15, ep = -1, half = 0, full = 1;
    let pHi = 0, pLo = 0;                 // pieces and side to move; castling and en passant added in hashes
    const kings = [4, 60];
    const MAXP = 4096;
    const uMove = new Int32Array(MAXP), uCap = new Int8Array(MAXP), uCastle = new Uint8Array(MAXP),
        uEp = new Int8Array(MAXP), uHalf = new Int16Array(MAXP), uPHi = new Int32Array(MAXP), uPLo = new Int32Array(MAXP);
    const hHi = new Int32Array(MAXP + 1), hLo = new Int32Array(MAXP + 1);    // the position's hash at each ply
    let ply = 0;

    const kingOf = s => kings[s > 0 ? 0 : 1];
    function put(sq, piece) {
        b[sq] = piece;
        pHi ^= ZH[(piece + 6) * 64 + sq]; pLo ^= ZL[(piece + 6) * 64 + sq];
    }
    function take(sq) {
        const piece = b[sq];
        if (piece) { pHi ^= ZH[(piece + 6) * 64 + sq]; pLo ^= ZL[(piece + 6) * 64 + sq]; b[sq] = 0; }
        return piece;
    }
    // En passant only counts towards a position when a pawn could really take
    // - legally: not a pawn pinned to its king, whose capture would leave it
    // in check (the same position again, otherwise, never counted as a repeat).
    function epLive() {
        if (ep < 0) return false;
        const from = PAWN_FROM[side > 0 ? 0 : 1][ep];
        const victim = ep - 8 * side;
        for (let i = 0; i < from.length; i++) {
            if (b[from[i]] !== P * side) continue;
            const pawn = b[from[i]], gone = b[victim];        // the capture, tried on the board alone
            b[from[i]] = 0; b[ep] = pawn; b[victim] = 0;
            const safe = !attacked(kingOf(side), -side);
            b[from[i]] = pawn; b[ep] = 0; b[victim] = gone;
            if (safe) return true;
        }
        return false;
    }
    function stamp() {
        let hi = pHi ^ CH[castle], lo = pLo ^ CL[castle];
        if (epLive()) { hi ^= EH[ep & 7]; lo ^= EL[ep & 7]; }
        hHi[ply] = hi; hLo[ply] = lo;
    }

    const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    function load(fen) {
        const [place, stm, cas = "-", epSq = "-", hm = "0", fm = "1"] = String(fen || START).trim().split(/\s+/);
        b.fill(0); pHi = 0; pLo = 0;
        let r = 7, f = 0;
        for (const ch of place) {
            if (ch === "/") { r -= 1; f = 0; continue; }
            if (/[1-8]/.test(ch)) { f += Number(ch); continue; }
            const t = "pnbrqk".indexOf(ch.toLowerCase()) + 1;
            if (!t || !inside(f, r)) throw new Error("bad FEN");
            const piece = ch === ch.toLowerCase() ? -t : t;
            put(r * 8 + f, piece);
            if (t === K) kings[piece > 0 ? 0 : 1] = r * 8 + f;
            f += 1;
        }
        side = stm === "b" ? -1 : 1;
        if (side < 0) { pHi ^= SH; pLo ^= SL; }
        castle = (cas.includes("K") ? 1 : 0) | (cas.includes("Q") ? 2 : 0) | (cas.includes("k") ? 4 : 0) | (cas.includes("q") ? 8 : 0);
        ep = epSq !== "-" ? (epSq.charCodeAt(0) - 97) + (Number(epSq[1]) - 1) * 8 : -1;
        half = Number(hm) || 0;
        full = Number(fm) || 1;
        ply = 0;
        stamp();
    }

    function fen() {
        let out = "";
        for (let r = 7; r >= 0; r--) {
            let gap = 0;
            for (let f = 0; f < 8; f++) {
                const p = b[r * 8 + f];
                if (!p) { gap += 1; continue; }
                if (gap) { out += gap; gap = 0; }
                const ch = " pnbrqk"[Math.abs(p)];
                out += p > 0 ? ch.toUpperCase() : ch;
            }
            if (gap) out += gap;
            if (r) out += "/";
        }
        const cas = (castle & 1 ? "K" : "") + (castle & 2 ? "Q" : "") + (castle & 4 ? "k" : "") + (castle & 8 ? "q" : "");
        const epSq = ep >= 0 ? "abcdefgh"[ep & 7] + (rank(ep) + 1) : "-";
        return `${out} ${side > 0 ? "w" : "b"} ${cas || "-"} ${epSq} ${half} ${full}`;
    }

    function make(m) {
        const from = m & 63, to = (m >> 6) & 63, promo = (m >> 12) & 7, kind = m >>> 15;
        uMove[ply] = m; uCastle[ply] = castle; uEp[ply] = ep; uHalf[ply] = half; uPHi[ply] = pHi; uPLo[ply] = pLo;
        const piece = b[from], type = piece > 0 ? piece : -piece;
        const cap = kind === F_EP ? take(to - 8 * side) : take(to);
        uCap[ply] = cap;
        take(from);
        put(to, promo ? promo * side : piece);
        if (kind === F_CASTLE) {
            if (to > from) put(from + 1, take(from + 3));
            else put(from - 1, take(from - 4));
        }
        if (type === K) kings[side > 0 ? 0 : 1] = to;
        ep = kind === F_DOUBLE ? from + 8 * side : -1;
        castle &= KEEPS[from] & KEEPS[to];
        half = type === P || cap ? 0 : half + 1;
        if (side < 0) full += 1;
        side = -side;
        pHi ^= SH; pLo ^= SL;
        ply += 1;
        stamp();
    }

    function unmake() {
        ply -= 1;
        const m = uMove[ply];
        const from = m & 63, to = (m >> 6) & 63, promo = (m >> 12) & 7, kind = m >>> 15;
        side = -side;
        if (side < 0) full -= 1;
        const piece = promo ? P * side : b[to];
        b[from] = piece;
        b[to] = 0;
        if (kind === F_CASTLE) {
            if (to > from) { b[from + 3] = b[from + 1]; b[from + 1] = 0; }
            else { b[from - 4] = b[from - 1]; b[from - 1] = 0; }
        }
        if (kind === F_EP) b[to - 8 * side] = uCap[ply];
        else b[to] = uCap[ply];
        if (piece === K || piece === -K) kings[side > 0 ? 0 : 1] = from;
        castle = uCastle[ply]; ep = uEp[ply]; half = uHalf[ply]; pHi = uPHi[ply]; pLo = uPLo[ply];
    }

    /* Is square sq attacked by side `by`? */
    function attacked(sq, by) {
        const pawns = PAWN_FROM[by > 0 ? 0 : 1][sq];
        for (let i = 0; i < pawns.length; i++) if (b[pawns[i]] === P * by) return true;
        const kn = KNIGHT[sq];
        for (let i = 0; i < kn.length; i++) if (b[kn[i]] === N * by) return true;
        const kg = KING[sq];
        for (let i = 0; i < kg.length; i++) if (b[kg[i]] === K * by) return true;
        const rays = RAYS[sq];
        for (let d = 0; d < 8; d++) {
            const ray = rays[d];
            for (let i = 0; i < ray.length; i++) {
                const p = b[ray[i]];
                if (!p) continue;
                const t = p * by;
                if (t === Q || t === (d < 4 ? R : B)) return true;
                break;
            }
        }
        return false;
    }

    const inCheck = () => attacked(kingOf(side), -side);

    function pawnTo(out, s, t) {
        if (t >= 56 || t < 8) for (const pr of [Q, N, R, B]) out.push(s | t << 6 | pr << 12);
        else out.push(s | t << 6);
    }

    /* Every move the rules of movement allow, legal or not (a king left in
       check is weeded out later); `noisy`: only captures and promotions. */
    function gen(out, noisy) {
        const us = side;
        for (let s = 0; s < 64; s++) {
            const p = b[s] * us;
            if (p <= 0) continue;
            if (p === P) {
                const caps = PAWN_ATT[us > 0 ? 0 : 1][s];
                for (let i = 0; i < caps.length; i++) {
                    const t = caps[i];
                    if (b[t] * us < 0) pawnTo(out, s, t);
                    else if (t === ep) out.push(s | t << 6 | F_EP << 15);
                }
                const fwd = s + 8 * us;
                if (!b[fwd]) {
                    if (!noisy || fwd >= 56 || fwd < 8) pawnTo(out, s, fwd);
                    const startRank = us > 0 ? 1 : 6;
                    if (!noisy && rank(s) === startRank && !b[fwd + 8 * us]) out.push(s | (fwd + 8 * us) << 6 | F_DOUBLE << 15);
                }
            } else if (p === N || p === K) {
                const hops = p === N ? KNIGHT[s] : KING[s];
                for (let i = 0; i < hops.length; i++) {
                    const t = hops[i], q = b[t] * us;
                    if (q > 0 || (noisy && !q)) continue;
                    out.push(s | t << 6);
                }
                if (p === K && !noisy) castling(out, s, us);
            } else {
                const d0 = p === B ? 4 : 0, d1 = p === R ? 4 : 8, rays = RAYS[s];
                for (let d = d0; d < d1; d++) {
                    const ray = rays[d];
                    for (let i = 0; i < ray.length; i++) {
                        const t = ray[i], q = b[t] * us;
                        if (q > 0) break;
                        if (q < 0) { out.push(s | t << 6); break; }
                        if (!noisy) out.push(s | t << 6);
                    }
                }
            }
        }
        return out;
    }

    function castling(out, s, us) {
        const home = us > 0 ? 4 : 60, short = us > 0 ? 1 : 4, long = us > 0 ? 2 : 8;
        if (s !== home || !(castle & (short | long)) || attacked(home, -us)) return;
        if (castle & short && !b[home + 1] && !b[home + 2] && b[home + 3] === R * us && !attacked(home + 1, -us)) {
            out.push(home | (home + 2) << 6 | F_CASTLE << 15);
        }
        if (castle & long && !b[home - 1] && !b[home - 2] && !b[home - 3] && b[home - 4] === R * us && !attacked(home - 1, -us)) {
            out.push(home | (home - 2) << 6 | F_CASTLE << 15);
        }
    }

    function legal() {
        const out = [];
        for (const m of gen([], false)) {
            make(m);
            if (!attacked(kingOf(-side), side)) out.push(m);
            unmake();
        }
        return out;
    }

    function perft(depth) {
        if (!depth) return 1;
        let n = 0;
        for (const m of gen([], false)) {
            make(m);
            if (!attacked(kingOf(-side), side)) n += perft(depth - 1);
            unmake();
        }
        return n;
    }

    /* ---------------------------------------------------- the end -- */
    function repetitions() {
        let n = 1;
        for (let i = ply - 2; i >= Math.max(0, ply - half); i -= 2) if (hHi[i] === hHi[ply] && hLo[i] === hLo[ply]) n += 1;
        return n;
    }

    // Neither side could ever mate: kings alone, a king and one minor piece,
    // or bishops only, all on squares of one colour.
    function deadDraw() {
        let minors = 0, bishopsLight = 0, bishopsDark = 0, knights = 0;
        for (let s = 0; s < 64; s++) {
            const t = Math.abs(b[s]);
            if (!t || t === K) continue;
            if (t === P || t === R || t === Q) return false;
            minors += 1;
            if (t === N) knights += 1;
            else if (((s & 7) + (s >> 3)) % 2) bishopsLight += 1;
            else bishopsDark += 1;
        }
        return minors <= 1 || (!knights && (!bishopsLight || !bishopsDark));
    }

    /* How things stand: {over, result, winner} - result is checkmate,
       stalemate, fifty, repetition or material; winner 1, -1 or 0. */
    function status() {
        if (!legal().length) return inCheck() ? {over: true, result: "checkmate", winner: -side} : {over: true, result: "stalemate", winner: 0};
        if (half >= 100) return {over: true, result: "fifty", winner: 0};
        if (repetitions() >= 3) return {over: true, result: "repetition", winner: 0};
        if (deadDraw()) return {over: true, result: "material", winner: 0};
        return {over: false, result: "", winner: 0, check: inCheck()};
    }

    /* A move in the usual notation: Nf3, exd5, O-O, e8=Q+, Qh4#. */
    function san(m, moves = legal()) {
        const from = m & 63, to = (m >> 6) & 63, promo = (m >> 12) & 7, kind = m >>> 15;
        const type = Math.abs(b[from]), square = "abcdefgh"[to & 7] + (rank(to) + 1);
        let text;
        if (kind === F_CASTLE) text = to > from ? "O-O" : "O-O-O";
        else {
            const capture = b[to] !== 0 || kind === F_EP;
            if (type === P) text = (capture ? "abcdefgh"[from & 7] + "x" : "") + square + (promo ? "=" + " PNBRQK"[promo] : "");
            else {
                const rivals = moves.filter(o => o !== m && ((o >> 6) & 63) === to && Math.abs(b[o & 63]) === type);
                let which = "";
                if (rivals.length) {
                    const sameFile = rivals.some(o => ((o & 63) & 7) === (from & 7));
                    const sameRank = rivals.some(o => rank(o & 63) === rank(from));
                    which = !sameFile ? "abcdefgh"[from & 7] : !sameRank ? String(rank(from) + 1)
                        : "abcdefgh"[from & 7] + (rank(from) + 1);
                }
                text = " PNBRQK"[type] + which + (capture ? "x" : "") + square;
            }
        }
        make(m);
        const check = inCheck(), mate = check && !legal().length;
        unmake();
        return text + (mate ? "#" : check ? "+" : "");
    }

    /* --------------------------------------------- the computer -- */
    /* Square tables (Tomasz Michniewski's), from White's side, a8 first. */
    const TABLES = {
        1: [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10, 5, 5, 10, 25, 25, 10, 5, 5,
            0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5, 5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
        2: [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30,
            -30, 5, 15, 20, 20, 15, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30,
            -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50],
        3: [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10,
            -10, 5, 5, 10, 10, 5, 5, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10,
            -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20],
        4: [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5,
            -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0],
        5: [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 5, 5, 5, 0, -10,
            -5, 0, 5, 5, 5, 5, 0, -5, 0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10,
            -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20],
        6: [-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30,
            -30, -40, -40, -50, -50, -40, -40, -30, -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20, -20, -10,
            20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20],
        7: [-50, -40, -30, -20, -20, -30, -40, -50, -30, -20, -10, 0, 0, -10, -20, -30, -30, -10, 20, 30, 30, 20, -10, -30,
            -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 20, 30, 30, 20, -10, -30,
            -30, -30, 0, 0, 0, 0, -30, -30, -50, -30, -30, -30, -30, -30, -30, -50],
    };
    // PST[type][colour][square]: the table read from that side.
    const PST = [];
    for (let t = 1; t <= 7; t++) {
        PST[t] = [new Int16Array(64), new Int16Array(64)];
        for (let s = 0; s < 64; s++) {
            PST[t][0][s] = TABLES[t][(7 - rank(s)) * 8 + (s & 7)];
            PST[t][1][s] = TABLES[t][rank(s) * 8 + (s & 7)];
        }
    }
    const PHASE = [0, 0, 1, 1, 2, 4, 0];
    const centre = s => Math.max(3 - (s & 7), (s & 7) - 4) + Math.max(3 - rank(s), rank(s) - 4);

    /* The position's worth to the side to move, in hundredths of a pawn. */
    function evaluate() {
        let score = 0, phase = 0, wB = 0, bB = 0, material = 0;
        for (let s = 0; s < 64; s++) {
            const p = b[s];
            if (!p) continue;
            const t = p > 0 ? p : -p, c = p > 0 ? 0 : 1, sign = p > 0 ? 1 : -1;
            phase += PHASE[t];
            if (t === K) continue;
            score += sign * (VALUE[t] + PST[t][c][s]);
            material += sign * VALUE[t];
            if (t === B) { if (p > 0) wB += 1; else bB += 1; }
        }
        phase = Math.min(24, phase);
        const wk = kings[0], bk = kings[1];
        score += ((PST[6][0][wk] - PST[6][1][bk]) * phase + (PST[7][0][wk] - PST[7][1][bk]) * (24 - phase)) / 24;
        if (wB >= 2) score += 30;
        if (bB >= 2) score -= 30;
        // Well ahead late on: drive the other king to the edge and walk ours up to it.
        if (phase < 12 && Math.abs(material) >= 400) {
            const lead = material > 0 ? 1 : -1, loser = lead > 0 ? bk : wk;
            const near = Math.abs((wk & 7) - (bk & 7)) + Math.abs(rank(wk) - rank(bk));
            score += lead * (10 * centre(loser) + 4 * (14 - near));
        }
        return Math.round(score) * side;
    }

    const TT_BITS = 18, TT_MASK = (1 << TT_BITS) - 1;
    const ttHi = new Int32Array(1 << TT_BITS), ttLo = new Int32Array(1 << TT_BITS), ttMove = new Int32Array(1 << TT_BITS);
    const ttScore = new Int16Array(1 << TT_BITS), ttDepth = new Int8Array(1 << TT_BITS), ttFlag = new Uint8Array(1 << TT_BITS);
    const killers = new Int32Array(256), history = new Int32Array(4096);
    let nodes = 0, stopAt = 0, stopped = false, mayStop = false;

    function order(moves, best, h) {
        const keys = new Float64Array(moves.length);
        for (let i = 0; i < moves.length; i++) {
            const m = moves[i], from = m & 63, to = (m >> 6) & 63, victim = Math.abs(b[to]);
            let k;
            if (m === best) k = 1e9;
            else if (victim || (m >>> 15) === F_EP) k = 1e7 + VALUE[victim || P] * 10 - VALUE[Math.abs(b[from])] / 10;
            else if ((m >> 12) & 7) k = 9e6;
            else if (m === killers[h * 2]) k = 8e6;
            else if (m === killers[h * 2 + 1]) k = 7e6;
            else k = history[from * 64 + to];
            keys[i] = k;
        }
        const idx = [...moves.keys()].sort((x, y) => keys[y] - keys[x]);
        return idx.map(i => moves[i]);
    }

    function tick() {
        if ((++nodes & 1023) === 0 && mayStop && Date.now() > stopAt) stopped = true;
        return stopped;
    }

    function quiesce(alpha, beta, h) {
        if (tick()) return 0;
        const stand = evaluate();
        if (stand >= beta) return stand;
        if (stand > alpha) alpha = stand;
        if (h > 60) return alpha;
        for (const m of order(gen([], true), 0, Math.min(h, 127))) {
            make(m);
            if (attacked(kingOf(-side), side)) { unmake(); continue; }
            const score = -quiesce(-beta, -alpha, h + 1);
            unmake();
            if (stopped) return 0;
            if (score >= beta) return score;
            if (score > alpha) alpha = score;
        }
        return alpha;
    }

    function negamax(depth, alpha, beta, h) {
        if (tick()) return 0;
        if (h > 0 && (half >= 100 || repetitions() > 1 || deadDraw())) return 0;
        const check = inCheck();
        if (check) depth += 1;
        if (depth <= 0) return quiesce(alpha, beta, h);
        const slot = hLo[ply] & TT_MASK, hit = ttHi[slot] === hHi[ply] && ttLo[slot] === hLo[ply];
        const ttm = hit ? ttMove[slot] : 0;
        if (hit && h > 0 && ttDepth[slot] >= depth) {
            let s = ttScore[slot];
            if (s > MATE - 500) s -= h; else if (s < -MATE + 500) s += h;
            const flag = ttFlag[slot];
            if (flag === 0 || (flag === 1 && s >= beta) || (flag === 2 && s <= alpha)) return s;
        }
        const startAlpha = alpha;
        let best = -MATE - 1, bestMove = 0, count = 0;
        for (const m of order(gen([], false), ttm, Math.min(h, 127))) {
            make(m);
            if (attacked(kingOf(-side), side)) { unmake(); continue; }
            count += 1;
            const score = -negamax(depth - 1, -beta, -alpha, h + 1);
            unmake();
            if (stopped) return 0;
            if (score > best) {
                best = score;
                bestMove = m;
                if (score > alpha) alpha = score;
                if (alpha >= beta) {
                    if (!b[(m >> 6) & 63] && !((m >> 12) & 7) && h < 128) {
                        if (killers[h * 2] !== m) { killers[h * 2 + 1] = killers[h * 2]; killers[h * 2] = m; }
                        history[(m & 63) * 64 + ((m >> 6) & 63)] += depth * depth;
                    }
                    break;
                }
            }
        }
        if (!count) return check ? -MATE + h : 0;
        let stored = best;
        if (stored > MATE - 500) stored += h; else if (stored < -MATE + 500) stored -= h;
        ttHi[slot] = hHi[ply]; ttLo[slot] = hLo[ply]; ttMove[slot] = bestMove;
        ttScore[slot] = stored; ttDepth[slot] = depth;
        ttFlag[slot] = best <= startAlpha ? 2 : best >= beta ? 1 : 0;
        return best;
    }

    /* How each difficulty plays. Easy looks a move ahead (and the captures
       that follow), often picks a worse move on purpose; Medium looks three
       moves ahead with a little doubt; Hard thinks deeper, for 1.5 seconds. */
    const LEVELS = {
        easy: {depth: 1, ms: 300, noise: 140, slip: 0.3},
        medium: {depth: 3, ms: 900, noise: 30, slip: 0},
        hard: {depth: 64, ms: 1500, noise: 0, slip: 0},
    };

    /* The computer's move for the side to move. */
    function think(levelName) {
        const level = LEVELS[levelName] || LEVELS.medium;
        let root = legal();
        if (root.length <= 1) return root[0] || 0;
        for (let i = root.length - 1; i > 0; i--) {           // equal moves: a different one each game
            const j = Math.floor(Math.random() * (i + 1));
            [root[i], root[j]] = [root[j], root[i]];
        }
        nodes = 0; stopped = false; mayStop = false; stopAt = Date.now() + level.ms;
        killers.fill(0); history.fill(0);
        let best = root[0], scores = new Map();
        for (let depth = 1; depth <= level.depth; depth++) {
            mayStop = depth > 1;
            const done = new Map();
            let alpha = -MATE - 1, bestNow = 0;
            root = order(root, best, 0);
            for (const m of root) {
                make(m);
                // With doubt (noise), every move needs its own score: no cut-offs at the root.
                const score = -negamax(depth - 1, -MATE - 1, level.noise ? MATE + 1 : -alpha, 1);
                unmake();
                if (stopped) break;
                done.set(m, score);
                if (score > alpha) { alpha = score; bestNow = m; }
            }
            if (stopped) break;
            best = bestNow || best;
            scores = done;
            if (alpha > MATE - 500) break;                   // a mate found: no need to look further
            if (Date.now() > stopAt) break;
        }
        if (level.noise && scores.size) {
            const top = Math.max(...scores.values());
            const pool = [...scores].filter(([, s]) => s > -MATE + 500 && s >= top - (level.slip && Math.random() < level.slip ? 250 : level.noise * 2));
            if (pool.length) {
                let pick = best, pickScore = -Infinity;
                for (const [m, s] of pool) {
                    const v = s + (Math.random() * 2 - 1) * level.noise;
                    if (v > pickScore) { pickScore = v; pick = m; }
                }
                best = pick;
            }
        }
        return best;
    }

    load(START);
    return {
        load, fen, make, unmake, legal, perft, san, status, think, evaluate, inCheck, START,
        get board() { return b; }, get side() { return side; }, get ply() { return ply; }, get nodes() { return nodes; },
        get castle() { return castle; }, get ep() { return ep; }, get half() { return half; },
        from: m => m & 63, to: m => (m >> 6) & 63, promo: m => (m >> 12) & 7, kind: m => m >>> 15,
        king: s => kingOf(s),
    };
}

/* Started as a worker: think about a position when asked. */
if (typeof window === "undefined" && typeof importScripts === "function") {
    const rules = chessRules();
    self.onmessage = e => {
        const {id, fen, moves, level} = e.data;
        rules.load(fen);
        for (const m of moves) rules.make(m);
        self.postMessage({id, move: rules.think(level)});
    };
}
