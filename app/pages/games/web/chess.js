/* Chess (loaded after games.js, board.js and chess_engine.js): against the
   computer (Easy, Medium or Hard; it thinks in a Web Worker) or two players
   on one computer. Every rule - castling, en passant, promotion with a
   choice of piece, check, checkmate, stalemate, and the draws by fifty
   moves, three repeats or too little left to mate. Click a piece and then
   where it goes, or drag it; Ctrl+Z takes back a move. */
"use strict";

const chess = (() => {
    const {CELL} = BOARD;
    const GLYPH = ["", "♟", "♞", "♝", "♜", "♛", "♚"], OUTLINE = ["", "♙", "♘", "♗", "♖", "♕", "♔"];
    const PIECE_FONT = '"Segoe UI Symbol", "Apple Symbols", "Noto Sans Symbols 2", "DejaVu Sans", serif';
    const LEVELS = ["easy", "medium", "hard"];
    const START_COUNT = [0, 8, 2, 2, 2, 1, 0];
    const WORTH = [0, 1, 3, 3, 5, 9, 0];
    const MIN_THINK = 450;                 // ms: a move that comes at once looks like a glitch

    const court = $("chess-court"), canvas = $("chess-canvas"), ctx = boardKit.context(canvas);
    const rules = chessRules();
    const computer = boardKit.computer("chess_engine.js", chessRules);
    let moves = [], sans = [];
    let you = 1, flipped = false, levelName = "medium", twoPlayers = false;
    let sel = -1, drag = null, promo = null;
    let state = "play";                    // play, thinking, over
    let ending = null, reported = false, game = 0;
    let anim = null;
    let record = {};
    let colors = {}, scale = 1;

    const soundOn = () => prefs.chess_sound !== false;
    const hintsOn = () => prefs.chess_hints !== false;
    const blip = (freq, length, type, at) => gameSound.blip(soundOn(), freq, length, type, at);
    const sideName = s => s > 0 ? "White" : "Black";

    /* -------------------------------------------- squares on screen -- */
    const cellOf = sq => flipped ? [7 - (sq & 7), sq >> 3] : [sq & 7, 7 - (sq >> 3)];
    const squareOf = (c, r) => flipped ? (r * 8 + 7 - c) : ((7 - r) * 8 + c);
    const centreOf = sq => { const [c, r] = cellOf(sq), [x, y] = boardKit.cellXY(c, r); return [x + CELL / 2, y + CELL / 2]; };

    /* ----------------------------------------------------- the game -- */
    function newGame() {
        computer.stop();
        game += 1;
        twoPlayers = prefs.chess_mode === "two";
        levelName = LEVELS.includes(prefs.chess_level) ? prefs.chess_level : "medium";
        const pick = prefs.chess_color;
        you = twoPlayers ? 1 : pick === "black" ? -1 : pick === "random" ? (Math.random() < 0.5 ? 1 : -1) : 1;
        flipped = !twoPlayers && you < 0;
        rules.load(rules.START);
        moves = []; sans = [];
        sel = -1; drag = null; promo = null; anim = null;
        ending = null; reported = false;
        setState("play");
        drawOptions();
        if (computersTurn()) think();
    }

    const computersTurn = () => !twoPlayers && rules.side !== you && state !== "over";
    const yourTurn = () => state === "play" && (twoPlayers || rules.side === you);

    function play(m) {
        const from = rules.from(m), to = rules.to(m), kind = rules.kind(m);
        const piece = rules.board[from];
        const capSq = kind === 1 ? to - 8 * Math.sign(piece) : to;
        const captured = rules.board[capSq];
        const legal = rules.legal();
        sans.push(rules.san(m, legal));
        // What moves on screen, and what's left behind until the move lands.
        const movers = [{piece: rules.promo(m) ? rules.promo(m) * Math.sign(piece) : piece, from, to, shown: piece}];
        if (kind === 2) movers.push({piece: 4 * Math.sign(piece), from: to > from ? from + 3 : from - 4,
                                     to: to > from ? from + 1 : from - 1, shown: 4 * Math.sign(piece)});
        anim = {movers, ghost: captured ? {piece: captured, sq: capSq} : null, start: performance.now(), length: 170};
        rules.make(m);
        moves.push(m);
        sel = -1; promo = null;
        const now = rules.status();
        if (now.over) {
            ending = now;
            blip(captured ? 260 : 420, 0.05, "triangle");
            finish();
        } else {
            if (now.check) blip(880, 0.09, "square");
            else if (captured) blip(260, 0.07, "triangle");
            else blip(420, 0.04, "triangle");
            setState("play");
            if (computersTurn()) think();
        }
        animate();
    }

    function think() {
        setState("thinking");
        const token = game, asked = performance.now(), level = levelName, played = [...moves];
        computer.ask({fen: rules.START, moves: played, level}, local => {
            local.load(local.START);
            for (const m of played) local.make(m);
            return local.think(level);
        }).then(m => {
            const wait = Math.max(0, MIN_THINK - (performance.now() - asked));
            setTimeout(() => {
                if (token !== game || state !== "thinking") return;
                if (!rules.legal().includes(m)) return setState("play");     // can't happen; never stuck
                state = "play";
                play(m);
            }, wait);
        });
    }

    function finish() {
        state = "over";
        const winner = ending.winner;
        const outcome = !winner ? "drawn" : winner === you ? "won" : "lost";
        if (!twoPlayers && !reported) {
            reported = true;
            send("result", {game: "chess", difficulty: levelName, outcome});
        }
        const tune = outcome === "lost" && !twoPlayers ? [392, 330, 262] : winner ? [523, 659, 784, 1047] : [440, 440];
        tune.forEach((f, k) => blip(f, 0.12, "sine", 0.15 + k * 0.1));
        setState("over");
    }

    function takeBack() {
        if (!moves.length || anim) return;
        computer.stop();
        game += 1;
        const undoOne = () => { rules.unmake(); moves.pop(); sans.pop(); };
        if (twoPlayers) undoOne();
        else {
            // Back to before your last move (and the computer's answer to it).
            let yours = false;
            while (moves.length && !yours) {
                undoOne();
                if (rules.side === you) yours = true;
            }
        }
        sel = -1; promo = null; drag = null; ending = null;
        setState("play");
        if (computersTurn()) think();
    }

    /* ------------------------------------------------------- input -- */
    function targetsOf(from) {
        return rules.legal().filter(m => rules.from(m) === from);
    }

    function tryMove(from, to) {
        const options = targetsOf(from).filter(m => rules.to(m) === to);
        if (!options.length) return false;
        if (options.length > 1) {              // a pawn promoting: which piece?
            promo = {to, options};
            draw();
            return true;
        }
        play(options[0]);
        return true;
    }

    // The promotion choice: queen, knight, rook, bishop, down from the far row.
    function promoCells() {
        if (!promo) return [];
        const [c, r] = cellOf(promo.to), dir = r === 0 ? 1 : -1;
        return [5, 2, 4, 3].map((type, k) => ({type, c, r: r + dir * k}));
    }

    court.addEventListener("mousedown", e => {
        if (e.button !== 0) return;
        e.preventDefault();
        court.focus({preventScroll: true});
        gameSound.prime();
        if (state === "over") return;           // the last position stays to look at; New game starts again
        const [x, y] = boardKit.pointAt(canvas, e);
        const cell = boardKit.cellAt(x, y);
        if (promo) {
            const hit = cell && promoCells().find(p => p.c === cell[0] && p.r === cell[1]);
            const choice = hit && promo.options.find(m => rules.promo(m) === hit.type);
            promo = null;
            if (choice) play(choice);
            else draw();
            return;
        }
        if (!yourTurn() || !cell) { sel = -1; draw(); return; }
        const sq = squareOf(...cell), piece = rules.board[sq];
        if (sel >= 0 && sq !== sel && tryMove(sel, sq)) return;
        if (piece && Math.sign(piece) === rules.side) {
            sel = sq;
            drag = {from: sq, x, y, moved: false};
        } else sel = -1;
        draw();
    });

    document.addEventListener("mousemove", e => {
        if (!drag) return;
        const [x, y] = boardKit.pointAt(canvas, e);
        if (Math.hypot(x - drag.x, y - drag.y) > 4) drag.moved = true;
        drag.x = x; drag.y = y;
        if (drag.moved) draw();
    });

    document.addEventListener("mouseup", e => {
        if (!drag) return;
        const d = drag;
        drag = null;
        if (d.moved) {
            const cell = boardKit.cellAt(...boardKit.pointAt(canvas, e));
            if (cell && squareOf(...cell) !== d.from && yourTurn()) tryMove(d.from, squareOf(...cell));
        }
        draw();
    });

    document.addEventListener("keydown", e => {
        if (gameTab !== "chess" || e.altKey) return;
        if (e.target.closest && e.target.closest("input, select, textarea, button, .modal, .dropdown-pop")) return;
        if (e.ctrlKey || e.metaKey) {     // Ctrl+Z, or ⌘Z on a Mac
            if (e.code === "KeyZ" && !e.shiftKey) { e.preventDefault(); takeBack(); }
            return;
        }
        if (e.code === "Escape") { sel = -1; promo = null; draw(); }
    });

    /* ----------------------------------------------------- drawing -- */
    function fit() {
        if (court.offsetParent === null) return;
        scale = boardKit.fit(court, canvas);   // the panel beside the board, or under it
        draw();
    }

    function animate() {
        draw();
        if (!anim) return;
        if (performance.now() - anim.start >= anim.length) { anim = null; draw(); return; }
        requestAnimationFrame(animate);
    }

    function drawPiece(p, x, y, size) {
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.font = `${Math.round(size * 0.86)}px ${PIECE_FONT}`;
        const t = Math.abs(p), dy = size * 0.04;
        ctx.lineJoin = "round";
        if (p > 0) {
            ctx.lineWidth = size * 0.06;
            ctx.strokeStyle = "#fff";
            ctx.fillStyle = "#fff";
            ctx.strokeText(GLYPH[t] + "︎", x, y + dy);
            ctx.fillText(GLYPH[t] + "︎", x, y + dy);
            ctx.fillStyle = "#1b1b1b";
            ctx.fillText(OUTLINE[t] + "︎", x, y + dy);
        } else {
            ctx.lineWidth = size * 0.035;
            ctx.strokeStyle = "rgba(255, 255, 255, 0.55)";
            ctx.strokeText(GLYPH[t] + "︎", x, y + dy);
            ctx.fillStyle = "#1b1b1b";
            ctx.fillText(GLYPH[t] + "︎", x, y + dy);
        }
    }

    // Pieces each side has taken, and who's ahead (in pawns).
    function taken() {
        const left = [[0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0]];
        for (const p of rules.board) if (p) left[p > 0 ? 0 : 1][Math.abs(p)] += 1;
        const lost = left.map(l => l.map((n, t) => Math.max(0, START_COUNT[t] - n)));
        const worth = l => l.reduce((sum, n, t) => sum + n * WORTH[t], 0);
        const material = side => left[side].reduce((sum, n, t) => sum + n * WORTH[t], 0);
        return {byWhite: lost[1], byBlack: lost[0], lead: material(0) - material(1), worth};
    }

    function draw() {
        if (!canvas.width) return;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        const last = moves.length ? moves[moves.length - 1] : null;
        const tint = {};
        const key = sq => cellOf(sq).join(",");
        if (last !== null) { tint[key(rules.from(last))] = BOARD.LAST; tint[key(rules.to(last))] = BOARD.LAST; }
        if (sel >= 0) tint[key(sel)] = BOARD.PICKED;
        boardKit.drawSquares(ctx, tint);
        // Coordinates round the edge.
        ctx.fillStyle = colors.dim;
        ctx.font = `600 13px ${colors.font}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        for (let i = 0; i < 8; i++) {
            const [x, y] = boardKit.cellXY(i, i);
            ctx.fillText("abcdefgh"[flipped ? 7 - i : i], x + CELL / 2, BOARD.Y + BOARD.SIZE + 16);
            ctx.fillText(String(flipped ? i + 1 : 8 - i), BOARD.X - 15, y + CELL / 2);
        }
        // A king in check glows red.
        const status = state === "over" ? ending : {check: rules.inCheck()};
        if (status && (status.check || (status.result === "checkmate"))) {
            const [x, y] = centreOf(rules.king(rules.side));
            const glow = ctx.createRadialGradient(x, y, 2, x, y, CELL * 0.62);
            glow.addColorStop(0, BOARD.CHECK);
            glow.addColorStop(1, "rgba(230, 40, 30, 0)");
            ctx.fillStyle = glow;
            ctx.fillRect(x - CELL / 2, y - CELL / 2, CELL, CELL);
        }
        // Pieces; moving ones and a dragged one are drawn last, on top.
        const t = anim ? Math.min(1, (performance.now() - anim.start) / anim.length) : 1;
        const ease = 1 - (1 - t) * (1 - t);
        const hidden = new Set(anim ? anim.movers.map(m => m.to) : []);
        if (drag && drag.moved) hidden.add(drag.from);
        for (let sq = 0; sq < 64; sq++) {
            const p = rules.board[sq];
            if (!p || hidden.has(sq)) continue;
            drawPiece(p, ...centreOf(sq), CELL);
        }
        if (anim && anim.ghost && ease < 0.75) drawPiece(anim.ghost.piece, ...centreOf(anim.ghost.sq), CELL);
        // Where the picked piece can go: a dot, or a ring round a piece it would take.
        if (sel >= 0 && hintsOn() && yourTurn()) {
            for (const m of targetsOf(sel)) {
                const to = rules.to(m), [x, y] = centreOf(to);
                ctx.fillStyle = BOARD.DOT;
                ctx.strokeStyle = BOARD.DOT;
                if (rules.board[to] || rules.kind(m) === 1) {
                    ctx.lineWidth = 5;
                    ctx.beginPath();
                    ctx.arc(x, y, CELL / 2 - 4, 0, Math.PI * 2);
                    ctx.stroke();
                } else {
                    ctx.beginPath();
                    ctx.arc(x, y, CELL * 0.15, 0, Math.PI * 2);
                    ctx.fill();
                }
            }
        }
        if (anim) {
            for (const m of anim.movers) {
                const [x0, y0] = centreOf(m.from), [x1, y1] = centreOf(m.to);
                drawPiece(t < 1 ? m.shown : m.piece, x0 + (x1 - x0) * ease, y0 + (y1 - y0) * ease, CELL);
            }
        }
        if (drag && drag.moved) drawPiece(rules.board[drag.from], drag.x, drag.y, CELL * 1.08);
        // The promotion choice, over the board.
        if (promo) {
            for (const p of promoCells()) {
                const [x, y] = boardKit.cellXY(p.c, p.r);
                ctx.fillStyle = "rgba(250, 250, 250, 0.96)";
                ctx.fillRect(x, y, CELL, CELL);
                ctx.strokeStyle = "rgba(0, 0, 0, 0.25)";
                ctx.lineWidth = 1;
                ctx.strokeRect(x + 0.5, y + 0.5, CELL - 1, CELL - 1);
                drawPiece(p.type * rules.side, x + CELL / 2, y + CELL / 2, CELL);
            }
        }
        drawSide();
    }

    function drawSide() {
        const caps = taken();
        const row = (list, by) => (x, y) => {
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            let at = x;
            for (let t = 5; t >= 1; t--) {
                for (let n = 0; n < list[t]; n++) {
                    drawPiece(t * -by, at + 9, y + 2, 22);
                    at += t === 1 ? 10 : 13;
                }
                if (list[t]) at += 6;
            }
            const lead = caps.lead * by;
            if (lead > 0) {
                ctx.fillStyle = colors.dim;
                ctx.font = `600 13px ${colors.font}`;
                ctx.textAlign = "left";
                ctx.fillText(`+${lead}`, at + 8, y + 2);
            }
        };
        const person = s => twoPlayers ? {name: sideName(s)}
            : s === you ? {name: "You", sub: sideName(s)} : {name: "Computer", sub: {easy: "Easy", medium: "Medium", hard: "Hard"}[levelName]};
        const topSide = flipped ? 1 : -1;
        const top = {...person(topSide), extra: row(topSide > 0 ? caps.byWhite : caps.byBlack, topSide),
                     turn: state !== "over" && rules.side === topSide};
        const bottom = {...person(-topSide), extra: row(-topSide > 0 ? caps.byWhite : caps.byBlack, -topSide),
                        turn: state !== "over" && rules.side === -topSide};
        let status, note = "", hint = "", statusColor = colors.fg;
        if (state === "over") {
            status = !ending.winner ? "Draw" : twoPlayers ? `${sideName(ending.winner)} wins` : ending.winner === you ? "You win!" : "The computer wins";
            note = endNote();
            hint = "Press New game to play again";
            statusColor = colors.accent;
        } else if (state === "thinking") {
            status = "Thinking…";
        } else {
            status = twoPlayers ? `${sideName(rules.side)} to move` : "Your move";
            if (rules.inCheck()) { note = "Check!"; }
        }
        const rows = [];
        for (let i = 0; i < sans.length; i += 2) rows.push([sans[i], sans[i + 1] || ""]);
        boardKit.drawPanel(ctx, colors, {top, bottom, status, note, hint, statusColor, moves: rows});
    }

    function endNote() {
        return {checkmate: "Checkmate", stalemate: "Stalemate - no legal move, but not in check",
                fifty: "Fifty moves without a capture or a pawn move", repetition: "The same position three times",
                material: "Not enough pieces left to checkmate"}[ending.result] || "";
    }

    /* ------------------------------------------------------ states -- */
    function setState(next) {
        state = next;
        $("chess-undo").disabled = !moves.length;
        draw();
    }

    function drawOptions() {
        const two = prefs.chess_mode === "two";
        for (const b of document.querySelectorAll("#chess-mode button")) b.setAttribute("aria-pressed", String(b.dataset.mode === (two ? "two" : "cpu")));
        $("chess-level-wrap").hidden = two;
        $("chess-color-wrap").hidden = two;
        $("chess-record").hidden = two;
    }

    $("chess-mode").addEventListener("click", e => {
        const b = e.target.closest("button[data-mode]");
        if (b && b.dataset.mode !== (prefs.chess_mode === "two" ? "two" : "cpu")) setPref("chess_mode", b.dataset.mode);
    });
    $("chess-new").addEventListener("click", newGame);
    $("chess-undo").addEventListener("click", takeBack);
    onTab(name => { if (name === "chess") requestAnimationFrame(fit); });
    onPrefs(key => {
        drawOptions();
        if (key === "chess_mode" || key === "chess_level" || key === "chess_color") newGame();
        else draw();
    });

    let started = false;
    Buddy.on("state", s => {
        record = s.chess_record || {};
        boardKit.showRecord($("chess-record"), record);
        // The saved settings have just arrived: the first game is played with them.
        if (!started) { started = true; newGame(); }
        drawOptions();
    });
    Buddy.on("chess_record", r => { record = r; boardKit.showRecord($("chess-record"), record); });
    Buddy.on("theme", () => requestAnimationFrame(() => { colors = gameColors(); draw(); }));
    Buddy.on("i18n", () => requestAnimationFrame(draw));
    Buddy.holdWhile(() => moves.length > 0 && state !== "over");             // a game under way: kept (buddy.js keepAlive)
    watchCourt(court, fit);

    colors = gameColors();
    boardKit.showRecord($("chess-record"), record);
    return {newGame, takeBack, play, tryMove, get state() { return state; }, get rules() { return rules; },
            get moves() { return moves; }, get sans() { return sans; }, get you() { return you; },
            get viaWorker() { return computer.viaWorker; }, select: sq => { sel = sq; draw(); }};
})();
