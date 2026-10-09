/* Solitaire's solver (solitaire.js): finds a way to win a Klondike game
   from a position, knowing every card - the face-down ones too. Used to
   deal only games that can be won (a random deal is tried until one can)
   and for Hint (the first move of a winning line from where you are).
   Loaded on the page, and started again as a Web Worker so the table
   stays live while it thinks.

   A search, depth first, with the moves most likely to help tried first:
   to a foundation, then moves that turn a face-down card over, then cards
   from the deck. Cards that nothing still needs go up by themselves (a
   card is safe on its foundation once both cards of the other colour one
   below it are up). The deck isn't drawn card by card: every card that a
   number of draws (turning the deck over when it runs out) would bring to
   the top is a move of its own - in Draw three, only every third card.
   Positions seen before aren't searched again; it gives up after `limit`
   positions. A few moves are never tried (part of a run moved, except to
   free the card under it for its foundation), so "lost" means no line was
   found rather than that none exists. */
"use strict";

function solitaireSolver() {
    const suitOf = id => (id / 13) | 0;
    const rankOf = id => (id % 13) + 1;                      // 1 (ace) to 13 (king)
    const redOf = id => { const s = suitOf(id); return s === 1 || s === 2; };   // ♥ ♦
    const top = a => a[a.length - 1];

    /* The page's game ({stock, waste, found, tab}, cards {s, r, up}) as the
       solver's position: columns of card ids with how many are face down,
       each suit's foundation height, and the deck in the order its cards
       are drawn (the waste first, then the stock) with how many are drawn. */
    function fromGame(g) {
        const id = c => c.s * 13 + c.r - 1;
        const found = [0, 0, 0, 0];
        for (const pile of g.found) if (pile.length) found[pile[0].s] = pile.length;
        return {
            tab: g.tab.map(col => col.map(id)),
            down: g.tab.map(col => { let n = 0; while (n < col.length && !col[n].up) n++; return n; }),
            found,
            talon: g.waste.map(id).concat(g.stock.slice().reverse().map(id)),
            pos: g.waste.length,
        };
    }

    /* A deal from 52 card ids, as solitaire.js deals it: columns of 1 to 7
       from the front, the rest the stock (its last card on top). */
    function fromDeck(deck) {
        const rest = deck.slice(), tab = [];
        for (let col = 0; col < 7; col++) tab.push(rest.splice(0, col + 1));
        return {tab, down: tab.map(col => col.length - 1), found: [0, 0, 0, 0], talon: rest.reverse(), pos: 0};
    }

    const copy = st => ({tab: st.tab.map(c => c.slice()), down: st.down.slice(), found: st.found.slice(),
                         talon: st.talon.slice(), pos: st.pos, unwound: !!st.unwound});

    function key(st, draw) {
        const cols = st.tab.map((c, i) => `${st.down[i]}:${c.join(",")}`).sort().join("|");
        // Drawing one, every card in the deck can always be reached: where it's up to doesn't matter.
        return `${cols}/${st.found.join(",")}/${st.talon.join(",")}@${draw === 1 ? 0 : st.pos}${st.unwound ? "u" : ""}`;
    }

    const onFoundation = (st, id) => st.found[suitOf(id)] === rankOf(id) - 1;
    function onColumn(st, id, j) {
        const col = st.tab[j];
        if (!col.length) return rankOf(id) === 13;
        const t = top(col);
        return redOf(t) !== redOf(id) && rankOf(t) === rankOf(id) + 1;
    }
    /* Nothing still needs it: both cards of the other colour one rank below are up. */
    function safe(st, id) {
        const r = rankOf(id);
        if (r <= 2) return true;
        const others = redOf(id) ? [0, 3] : [1, 2];
        return others.every(s => st.found[s] >= r - 1);
    }

    /* Where the deck can be drawn to: each reachable count of cards drawn
       (its top card is talon[p - 1]), the deck turned over as often as need be. */
    function reachable(st, draw) {
        const len = st.talon.length, out = [];
        if (!len) return out;
        const seen = new Set();
        let p = st.pos;
        for (let guard = 0; guard < 3 * len + 6; guard++) {
            if (p > 0) {
                if (seen.has(p)) break;
                seen.add(p);
                out.push(p);
            }
            p = p >= len ? Math.min(draw, len) : Math.min(p + draw, len);
        }
        return out;
    }

    function turnOver(st, i) {
        const n = st.tab[i].length;
        if (st.down[i] >= n) st.down[i] = Math.max(0, n - 1);
    }

    /* A move on a copy of the position. Moves: tf (column i's top to its
       foundation), wf (deck card p to its foundation), tt (column i from
       card k on to column j), wt (deck card p to column j), ft (suit s's top
       card down to column j). */
    function apply(st, m) {
        const n = copy(st);
        if (m.t === "tf") {
            const id = n.tab[m.i].pop();
            n.found[suitOf(id)]++;
            turnOver(n, m.i);
        } else if (m.t === "wf" || m.t === "wt") {
            const [id] = n.talon.splice(m.p - 1, 1);
            n.pos = m.p - 1;
            if (m.t === "wf") n.found[suitOf(id)]++;
            else n.tab[m.j].push(id);
        } else if (m.t === "tt") {
            n.tab[m.j].push(...n.tab[m.i].splice(m.k));
            turnOver(n, m.i);
        } else if (m.t === "ft") {
            const id = m.s * 13 + n.found[m.s] - 1;
            n.found[m.s]--;
            n.tab[m.j].push(id);
            n.unwound = true;
        }
        return n;
    }

    /* Cards nothing still needs, up onto their foundations; the moves made.
       Not once a line has taken a card down from a foundation: "nothing
       needs it" doesn't count cards brought down from the foundations, and
       the card would just go straight back up. */
    function autoMoves(st, made) {
        if (st.unwound) return made;
        for (let again = true; again;) {
            again = false;
            for (let i = 0; i < 7; i++) {
                const col = st.tab[i];
                if (col.length && col.length > st.down[i] && onFoundation(st, top(col)) && safe(st, top(col))) {
                    const m = {t: "tf", i};
                    Object.assign(st, apply(st, m));
                    made.push(m);
                    again = true;
                }
            }
            if (st.pos > 0) {
                const id = st.talon[st.pos - 1];
                if (onFoundation(st, id) && safe(st, id)) {
                    const m = {t: "wf", p: st.pos};
                    Object.assign(st, apply(st, m));
                    made.push(m);
                    again = true;
                }
            }
        }
        return made;
    }

    /* The moves worth trying from a position, the likeliest first. */
    function moves(st, draw) {
        const first = [], reveal = [], deckToFound = [], deck = [], emptying = [], partial = [], down = [];
        const deckCards = reachable(st, draw);
        const firstEmpty = st.tab.findIndex(c => !c.length);
        const targets = id => [0, 1, 2, 3, 4, 5, 6].filter(j => (st.tab[j].length || j === firstEmpty) && onColumn(st, id, j));
        for (let i = 0; i < 7; i++) {
            const col = st.tab[i];
            if (col.length > st.down[i] && onFoundation(st, top(col))) first.push({t: "tf", i});
        }
        for (const p of deckCards) if (onFoundation(st, st.talon[p - 1])) deckToFound.push({t: "wf", p});
        for (let i = 0; i < 7; i++) {
            const col = st.tab[i], b = st.down[i];
            if (col.length <= b) continue;
            for (const j of targets(col[b])) {
                if (j === i) continue;
                if (b > 0) reveal.push({t: "tt", i, k: b, j, w: b});
                else if (st.tab[j].length) emptying.push({t: "tt", i, k: b, j});
            }
            // Part of a run, only to free the card under it for its foundation.
            for (let k = b + 1; k < col.length; k++) {
                if (!onFoundation(st, col[k - 1])) continue;
                for (const j of targets(col[k])) if (j !== i) partial.push({t: "tt", i, k, j});
            }
        }
        reveal.sort((a, b) => b.w - a.w);                // the column with the most face down first
        for (const p of deckCards) for (const j of targets(st.talon[p - 1])) deck.push({t: "wt", p, j});
        for (let s = 0; s < 4; s++) {
            if (st.found[s] < 2) continue;
            const id = s * 13 + st.found[s] - 1;
            for (const j of targets(id)) down.push({t: "ft", s, j});     // a king to an empty column too
        }
        return [...first, ...deckToFound, ...reveal, ...deck, ...emptying, ...partial, ...down];
    }

    const won = st => st.found.every(f => f === 13)
        || (!st.talon.length && st.down.every(d => d === 0));      // all face up, deck used: it plays itself out

    /* A winning line from `start`: {result: "won" | "lost" | "unknown",
       moves: [...], nodes}. "lost": every line was tried; "unknown": it
       gave up after `limit` positions. */
    function solve(start, draw, limit = 200000) {
        const st = copy(start), path = autoMoves(st, []);
        const seen = new Set();
        let nodes = 0, gaveUp = false;
        function search(pos) {
            if (won(pos)) return true;
            if (++nodes > limit) { gaveUp = true; return false; }
            const k = key(pos, draw);
            if (seen.has(k)) return false;
            seen.add(k);
            for (const m of moves(pos, draw)) {
                const next = apply(pos, m);
                const before = path.length;
                path.push(m);
                autoMoves(next, path);
                if (search(next)) return true;
                path.length = before;
                if (gaveUp) return false;
            }
            return false;
        }
        const ok = search(st);
        return {result: ok ? "won" : gaveUp ? "unknown" : "lost", moves: ok ? path : [], nodes};
    }

    /* Deals the solver won (found with a far bigger budget), for when a
       search runs out of time - so a deal is always one that can be won. */
    const WON = {
        1: [
            [25,29,48,2,37,28,10,11,6,14,30,1,8,35,13,46,4,47,0,33,23,51,43,12,18,7,49,38,19,41,45,39,32,5,20,40,26,31,50,22,44,15,16,17,42,21,34,24,27,3,9,36],
            [42,38,40,49,22,29,30,18,51,35,47,44,3,16,23,46,24,21,36,12,28,10,34,5,20,45,4,6,48,15,9,31,8,41,19,33,25,13,37,39,0,11,7,1,32,17,26,2,27,43,14,50],
            [22,32,44,19,11,31,36,6,30,23,40,24,37,41,38,45,5,46,47,9,50,13,17,26,12,27,1,16,28,7,42,21,49,39,10,51,0,34,33,35,14,2,43,18,29,15,25,8,3,4,20,48],
            [34,10,42,49,35,2,18,20,48,12,14,11,51,3,32,36,25,41,13,44,33,7,22,0,47,19,15,17,16,40,37,8,46,21,30,26,38,45,43,1,27,9,39,5,4,29,28,31,24,50,6,23],
        ],
        3: [
            [49,38,10,42,4,29,17,31,34,37,0,39,45,1,24,12,26,18,22,28,32,5,41,43,9,35,13,44,19,48,50,6,3,40,46,25,33,16,23,36,21,2,27,15,8,30,14,20,47,7,11,51],
            [35,11,12,7,37,50,18,17,46,45,25,26,28,44,33,24,39,0,51,14,31,36,43,48,29,20,8,16,42,1,21,27,4,49,40,19,15,6,32,5,3,23,47,30,10,34,38,9,22,41,13,2],
            [48,45,14,25,20,51,36,24,23,4,3,39,42,19,26,2,12,34,50,21,30,40,38,9,33,41,0,7,31,16,44,29,10,37,28,27,46,47,13,15,43,8,11,6,18,35,1,5,32,17,49,22],
            [33,37,44,22,16,15,41,45,43,32,29,24,39,4,3,21,27,0,10,13,23,40,11,36,46,31,18,25,1,38,14,48,34,8,5,17,42,47,2,30,6,12,35,28,51,26,9,7,49,20,50,19],
        ],
    };
    // Suits swapped so colours stay apart (the reds, the blacks, or reds for
    // blacks): a deal that can be won still can after any of them. Black 0, 3; red 1, 2.
    const SUIT_SWAPS = [[0, 1, 2, 3], [3, 1, 2, 0], [0, 2, 1, 3], [3, 2, 1, 0],
                        [1, 0, 3, 2], [2, 0, 3, 1], [1, 3, 0, 2], [2, 3, 0, 1]];
    function wonDeal(draw, random) {
        const list = WON[draw] || WON[1];
        const deck = list[Math.floor(random() * list.length)];
        const swap = SUIT_SWAPS[Math.floor(random() * SUIT_SWAPS.length)];
        return deck.map(id => swap[(id / 13) | 0] * 13 + id % 13);
    }

    /* A deal that can be won: random decks until the solver wins one
       (within `limit` positions each, `ms` in all). Time run out, one of
       WON, its suits swapped - never a deal nobody's shown can be won.
       Returns {deck: 52 ids, solved: true, tries, kept: true if from WON}. */
    function deal(draw, limit = 60000, ms = 4000, random = Math.random) {
        const t0 = Date.now();
        let tries = 0;
        do {
            const deck = Array.from({length: 52}, (_, i) => i);
            for (let i = 51; i > 0; i--) {
                const j = Math.floor(random() * (i + 1));
                [deck[i], deck[j]] = [deck[j], deck[i]];
            }
            tries++;
            if (solve(fromDeck(deck), draw, limit).result === "won") return {deck, solved: true, tries};
        } while (Date.now() - t0 < ms);
        return {deck: wonDeal(draw, random), solved: true, tries, kept: true};
    }

    return {fromGame, fromDeck, solve, deal, reachable, apply, moves, WON, SUIT_SWAPS};
}

/* Started as a worker: a winnable deal, or a winning line from a position. */
if (typeof window === "undefined" && typeof importScripts === "function") {
    const solver = solitaireSolver();
    self.onmessage = e => {
        const {id, kind, draw, game, limit} = e.data;
        if (kind === "deal") self.postMessage({id, ...solver.deal(draw)});
        else self.postMessage({id, ...solver.solve(solver.fromGame(game), draw, limit)});
    };
}

if (typeof module !== "undefined") module.exports = {solitaireSolver};
