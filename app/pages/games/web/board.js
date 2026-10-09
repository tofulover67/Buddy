/* Chess and Checkers (chess.js, checkers.js), loaded after games.js: what
   they share - the board's layout and colours, where a click lands, the
   side panel beside the board, the record against the computer, and the
   computer's thinking in a Web Worker. */
"use strict";

/* The court's layout, in canvas units: the board, and the side panel
   beside it - or, in a narrow pane, under it with bigger text (stacked):
   beside a board shrunk to fit, its moves were 6 pixels high. boardKit.fit
   picks one; PX, PY, PW, W and H follow it. */
const BOARD = (() => {
    const CELL = 68, X = 34, Y = 26, SIZE = CELL * 8;
    return {CELL, X, Y, SIZE, stacked: false, PX: 0, PY: 0, PW: 0, W: 0, H: 0,
            LIGHT: "#f0d9b5", DARK: "#b58863", LAST: "rgba(155, 199, 0, 0.41)", PICKED: "rgba(20, 85, 30, 0.5)",
            DOT: "rgba(20, 85, 30, 0.5)", CHECK: "rgba(230, 40, 30, 0.85)"};
})();
const BOARD_LAYOUTS = {
    beside: {stacked: false, PX: BOARD.X + BOARD.SIZE + 30, PY: BOARD.Y, PW: 236,
             W: BOARD.X + BOARD.SIZE + 30 + 236 + 18, H: BOARD.Y + BOARD.SIZE + 34},
    under: {stacked: true, PX: BOARD.X, PY: BOARD.Y + BOARD.SIZE + 34, PW: BOARD.SIZE,
            W: BOARD.X + BOARD.SIZE + 18, H: BOARD.Y + BOARD.SIZE + 34 + 232},
};
Object.assign(BOARD, BOARD_LAYOUTS.beside);
const NARROW_BOARD = 560;   // a court narrower than this (CSS pixels) always has the panel under the board

const boardKit = {
    /* The court fitted to its card (games.js fitCourt), with the panel
       under the board when that leaves the board bigger than beside it.
       Returns canvas pixels per unit. */
    fit(court, canvas) {
        const card = court.parentElement, style = getComputedStyle(card);
        const padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
        const roomW = card.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        if (roomW < NARROW_BOARD) {
            // A narrow pane: the panel under the board, the board as wide as
            // the pane - the card grows to hold it and the page scrolls (in a
            // short pane, beside it the moves were 6 pixels high).
            Object.assign(BOARD, BOARD_LAYOUTS.under);
            const tall = `${Math.ceil(roomW * BOARD.H / BOARD.W + padY + 4)}px`;
            if (card.style.minHeight !== tall) card.style.minHeight = tall;
        } else {
            if (card.style.minHeight) card.style.minHeight = "";
            const roomH = card.clientHeight - padY;
            const scaleOf = l => Math.min(roomW / l.W, roomH / l.H);
            const under = scaleOf(BOARD_LAYOUTS.under) > scaleOf(BOARD_LAYOUTS.beside) * 1.08;
            Object.assign(BOARD, under ? BOARD_LAYOUTS.under : BOARD_LAYOUTS.beside);
        }
        return fitCourt(court, canvas, BOARD.W, BOARD.H);
    },

    /* The board's 2D context, drawn on the CPU (willReadFrequently). On the
       GPU (core/web_flags.py: GPU painting, MSAA off) the AMD graphics bite
       a wedge out of circles - checkers' pieces, the move dots and rings.
       A board only redraws when something moves, so the CPU costs nothing. */
    context: canvas => canvas.getContext("2d", {willReadFrequently: true}),

    /* Canvas units of a pointer event over a court of BOARD.W x BOARD.H. */
    pointAt(canvas, e) {
        const r = canvas.getBoundingClientRect();
        return [(e.clientX - r.left) / r.width * BOARD.W, (e.clientY - r.top) / r.height * BOARD.H];
    },

    /* The screen cell (column, row from the top) under a point, or null. */
    cellAt(x, y) {
        const c = Math.floor((x - BOARD.X) / BOARD.CELL), r = Math.floor((y - BOARD.Y) / BOARD.CELL);
        return c >= 0 && c < 8 && r >= 0 && r < 8 ? [c, r] : null;
    },

    cellXY: (c, r) => [BOARD.X + c * BOARD.CELL, BOARD.Y + r * BOARD.CELL],

    /* The squares, with a tint on the cells `tint` names ({key "c,r": colour}). */
    drawSquares(ctx, tint) {
        for (let r = 0; r < 8; r++) {
            for (let c = 0; c < 8; c++) {
                const [x, y] = boardKit.cellXY(c, r);
                ctx.fillStyle = (r + c) % 2 ? BOARD.DARK : BOARD.LIGHT;
                ctx.fillRect(x, y, BOARD.CELL, BOARD.CELL);
                const t = tint[`${c},${r}`];
                if (t) { ctx.fillStyle = t; ctx.fillRect(x, y, BOARD.CELL, BOARD.CELL); }
            }
        }
    },

    /* Text no wider than `width`: the size shrinks to fit (translations run long). */
    fitText(ctx, text, x, y, width, size, weight, font) {
        let s = size;
        do {
            ctx.font = `${weight} ${s}px ${font}`;
            s -= 1;
        } while (s > 9 && ctx.measureText(text).width > width);
        ctx.fillText(text, x, y);
    },

    /* The side panel: a player at the top and bottom (the bottom one is
       whoever sits at the bottom of the board), how things stand between
       them, and the moves so far. */
    drawPanel(ctx, colors, panel) {
        const {PX, PY, PW, SIZE} = BOARD;
        // Where each part goes: a column beside the board, or - under it -
        // the players and how things stand on the left, the moves on the right.
        const g = BOARD.stacked
            ? {x: PX, w: 300, ts: 1.45, top: PY + 26, bottom: PY + 96, status: PY + 160,
               mx: PX + 318, mw: PW - 318, ms: 1.45, num: 34, col: 64, mTop: PY + 34, mBottom: PY + 206}
            : {x: PX, w: PW, ts: 1, top: PY + 16, bottom: PY + SIZE - 46, status: PY + 104,
               mx: PX, mw: PW, ms: 1, num: 38, col: 96, mTop: PY + 168, mBottom: PY + SIZE - 82};
        const font = colors.font, ts = g.ts;
        ctx.textAlign = "left";
        ctx.textBaseline = "alphabetic";
        const player = (p, y) => {
            ctx.textAlign = "left";
            ctx.textBaseline = "alphabetic";
            ctx.fillStyle = colors.fg;
            boardKit.fitText(ctx, Buddy.t(p.name), g.x, y, g.w, 17 * ts, 700, font);
            if (p.sub) {
                ctx.textAlign = "left";
                ctx.fillStyle = colors.dim;
                boardKit.fitText(ctx, Buddy.t(p.sub), g.x, y + 19 * ts, g.w, 13 * ts, 500, font);
            }
            if (p.extra) p.extra(g.x, y + 30 * ts);
            if (p.turn) {
                ctx.fillStyle = colors.accent;
                ctx.beginPath();
                ctx.arc(g.x + g.w - 8, y - 6 * ts, 5 * ts, 0, Math.PI * 2);
                ctx.fill();
            }
        };
        player(panel.top, g.top);
        player(panel.bottom, g.bottom);
        // How things stand.
        ctx.textAlign = "left";
        ctx.textBaseline = "alphabetic";
        ctx.fillStyle = panel.statusColor || colors.fg;
        boardKit.fitText(ctx, Buddy.t(panel.status || ""), g.x, g.status, g.w, 20 * ts, 800, font);
        if (panel.note) {
            ctx.fillStyle = colors.dim;
            boardKit.fitText(ctx, Buddy.t(panel.note), g.x, g.status + 22 * ts, g.w, 13 * ts, 500, font);
        }
        if (panel.hint) {
            ctx.fillStyle = colors.dim;
            boardKit.fitText(ctx, Buddy.t(panel.hint), g.x, g.status + 40 * ts, g.w, 12 * ts, 500, font);
        }
        // The moves, the latest at the bottom.
        const ms = g.ms, top = g.mTop, bottom = g.mBottom, line = 21 * ms;
        ctx.globalAlpha = 0.25;
        ctx.fillStyle = colors.dim;
        ctx.fillRect(g.mx, top - 16 * ms, g.mw, 1);
        ctx.fillRect(g.mx, bottom + 6, g.mw, 1);
        ctx.globalAlpha = 1;
        const rows = panel.moves || [];
        const fits = Math.max(1, Math.floor((bottom - top) / line) + 1);
        const first = Math.max(0, rows.length - fits);
        ctx.font = `500 ${14 * ms}px ${font}`;
        ctx.textAlign = "left";
        rows.slice(first).forEach((row, i) => {
            const y = top + i * line;
            ctx.fillStyle = colors.dim;
            ctx.fillText(`${first + i + 1}.`, g.mx, y);
            row.forEach((text, k) => {
                if (!text) return;
                ctx.fillStyle = first + i === rows.length - 1 && k === row.length - 1 ? colors.accent : colors.fg;
                ctx.fillText(text, g.mx + (g.num + k * g.col) * ms, y);
            });
        });
    },

    /* "Your record against the computer: Easy 2–1–0 · ..." (won, lost, drawn). */
    showRecord(node, record) {
        const parts = [el("span", {text: "Your record against the computer"}), ": "];
        ["easy", "medium", "hard"].forEach((lv, i) => {
            const r = (record || {})[lv] || {won: 0, lost: 0, drawn: 0};
            if (i) parts.push(" · ");
            parts.push(el("span.muted", {text: {easy: "Easy", medium: "Medium", hard: "Hard"}[lv]}),
                       ` ${r.won}–${r.lost}–${r.drawn}`);
        });
        node.replaceChildren(...parts);
        node.title = "Won – lost – drawn";
    },

    /* The computer's player: the engine file run as a Web Worker, so the
       board stays live while it thinks. If a worker can't start, it thinks
       on the page instead (`onPage(rules)` works the move out there). */
    computer(file, makeRules) {
        let worker = null, broken = false, waiting = null, id = 0, rules = null;

        function start() {
            if (worker || broken) return;
            try {
                worker = new Worker(file);
                worker.onmessage = e => {
                    if (waiting && e.data.id === waiting.id) {
                        const w = waiting;
                        waiting = null;
                        w.resolve(e.data.move);
                    }
                };
                worker.onerror = () => {
                    broken = true;
                    worker = null;
                    if (waiting) onPage(waiting);
                };
            } catch (err) {
                broken = true;
                worker = null;
            }
        }

        function onPage(w) {
            setTimeout(() => {
                if (waiting !== w) return;
                rules = rules || makeRules();
                const move = w.onPage(rules);
                if (waiting === w) { waiting = null; w.resolve(move); }
            }, 30);
        }

        /* The move for this message; a newer ask (or stop) drops the old one. */
        function ask(message, onPageFn) {
            stop();
            start();
            return new Promise(resolve => {
                waiting = {id: ++id, resolve, onPage: onPageFn};
                if (worker) worker.postMessage({...message, id});
                else onPage(waiting);
            });
        }

        // An answer no longer wanted: the worker is stopped mid-thought (a new one starts next time).
        function stop() {
            if (!waiting) return;
            waiting = null;
            if (worker) { worker.terminate(); worker = null; }
        }

        return {ask, stop, get viaWorker() { return !!worker && !broken; }};
    },
};
