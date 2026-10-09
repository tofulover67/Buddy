/* Checkers (loaded after games.js, board.js and checkers_engine.js):
   American checkers against the computer (Easy, Medium or Hard; it thinks
   in a Web Worker) or two players on one computer. Black moves first.
   Jumps are forced and go on while they can; a man on the far row is
   crowned. Click a piece and then where it lands - for a double jump, the
   last square, or each landing in turn - or drag it. Ctrl+Z takes back. */
"use strict";

const checkers = (() => {
    const {CELL} = BOARD;
    const LEVELS = ["easy", "medium", "hard"];
    const MIN_THINK = 450;
    const HOP = 150;                        // ms a piece takes for each square it moves

    const court = $("checkers-court"), canvas = $("checkers-canvas"), ctx = boardKit.context(canvas);
    const rules = checkersRules();
    const computer = boardKit.computer("checkers_engine.js", checkersRules);
    let played = [], texts = [];            // each move as its index in legal(), and in notation
    let you = 1, flipped = false, levelName = "medium", twoPlayers = false;
    let sel = -1, hops = [], drag = null;
    let state = "play";                     // play, thinking, over
    let ending = null, reported = false, game = 0;
    let anim = null, lastMove = null;
    let record = {};
    let colors = {}, scale = 1;

    const soundOn = () => prefs.checkers_sound !== false;
    const hintsOn = () => prefs.checkers_hints !== false;
    const blip = (freq, length, type, at) => gameSound.blip(soundOn(), freq, length, type, at);
    const sideName = s => s > 0 ? "Black" : "White";

    /* -------------------------------------------- squares on screen -- */
    // Black's side is at the top of the usual diagram: turned round when you play Black.
    const cellOf = s => flipped ? [7 - rules.col(s), 7 - rules.row(s)] : [rules.col(s), rules.row(s)];
    const squareOf = (c, r) => flipped ? rules.at(7 - r, 7 - c) : rules.at(r, c);
    const centreOf = s => { const [c, r] = cellOf(s), [x, y] = boardKit.cellXY(c, r); return [x + CELL / 2, y + CELL / 2]; };

    /* ----------------------------------------------------- the game -- */
    function newGame() {
        computer.stop();
        game += 1;
        twoPlayers = prefs.checkers_mode === "two";
        levelName = LEVELS.includes(prefs.checkers_level) ? prefs.checkers_level : "medium";
        const pick = prefs.checkers_color;
        you = twoPlayers ? -1 : pick === "white" ? -1 : pick === "random" ? (Math.random() < 0.5 ? 1 : -1) : 1;
        flipped = !twoPlayers && you > 0;
        rules.reset();
        played = []; texts = [];
        sel = -1; hops = []; drag = null; anim = null; lastMove = null;
        ending = null; reported = false;
        setState("play");
        drawOptions();
        if (computersTurn()) think();
    }

    const computersTurn = () => !twoPlayers && rules.side !== you && state !== "over";
    const yourTurn = () => state === "play" && (twoPlayers || rules.side === you);

    function play(index) {
        const m = rules.legal()[index];
        const piece = rules.board[m.from];
        texts.push(rules.notation(m));
        anim = {piece, crowned: m.crown ? 2 * Math.sign(piece) : piece, path: [m.from, ...m.path],
                caps: m.caps.map(c => ({sq: c, piece: rules.board[c]})), start: performance.now()};
        rules.make(m);
        played.push(index);
        lastMove = m;
        sel = -1; hops = [];
        const now = rules.status();
        if (m.caps.length) m.caps.forEach((_, k) => blip(300 - k * 30, 0.06, "triangle", k * HOP / 1000));
        else blip(420, 0.04, "triangle");
        if (m.crown) blip(990, 0.1, "sine", m.path.length * HOP / 1000);
        if (now.over) {
            ending = now;
            finish();
        } else {
            setState("play");
            if (computersTurn()) think();
        }
        animate();
    }

    function think() {
        setState("thinking");
        const token = game, asked = performance.now(), level = levelName, moves = [...played];
        computer.ask({moves, level}, local => {
            local.reset();
            for (const i of moves) local.make(local.legal()[i]);
            return local.think(level);
        }).then(index => {
            // Waits for the last move to finish landing, too.
            const landing = anim ? anim.path.length * HOP - (performance.now() - anim.start) : 0;
            const wait = Math.max(0, MIN_THINK - (performance.now() - asked), landing);
            setTimeout(() => {
                if (token !== game || state !== "thinking") return;
                if (!(index >= 0 && index < rules.legal().length)) return setState("play");
                state = "play";
                play(index);
            }, wait);
        });
    }

    function finish() {
        state = "over";
        const winner = ending.winner;
        const outcome = !winner ? "drawn" : winner === you ? "won" : "lost";
        if (!twoPlayers && !reported) {
            reported = true;
            send("result", {game: "checkers", difficulty: levelName, outcome});
        }
        const tune = outcome === "lost" && !twoPlayers ? [392, 330, 262] : winner ? [523, 659, 784, 1047] : [440, 440];
        tune.forEach((f, k) => blip(f, 0.12, "sine", 0.3 + k * 0.1));
        setState("over");
    }

    function takeBack() {
        if (!played.length) return;
        computer.stop();
        game += 1;
        anim = null;
        const undoOne = () => { rules.unmake(); played.pop(); texts.pop(); };
        if (twoPlayers) undoOne();
        else {
            let yours = false;
            while (played.length && !yours) {
                undoOne();
                if (rules.side === you) yours = true;
            }
        }
        const moves = rules.moves;
        lastMove = moves.length ? moves[moves.length - 1] : null;
        sel = -1; hops = []; drag = null; ending = null;
        setState("play");
        if (computersTurn()) think();
    }

    /* ------------------------------------------------------- input -- */
    // The moves still open for the picked piece, given the landings chosen so far.
    function options() {
        if (sel < 0) return [];
        return rules.legal().map((m, i) => ({m, i}))
            .filter(({m}) => m.from === sel && hops.every((h, k) => m.path[k] === h));
    }

    // Lands on square s: a whole move if that settles it, or one more hop of a jump.
    function landOn(s) {
        const open = options();
        const ends = open.filter(({m}) => m.path[m.path.length - 1] === s && m.path.length > hops.length);
        if (ends.length === 1) { play(ends[0].i); return true; }
        const next = open.filter(({m}) => m.path[hops.length] === s);
        if (!next.length) return false;
        hops.push(s);
        const exact = next.filter(({m}) => m.path.length === hops.length);
        if (exact.length === 1 && next.length === 1) play(exact[0].i);
        else draw();
        return true;
    }

    const movable = () => new Set(rules.legal().map(m => m.from));

    court.addEventListener("mousedown", e => {
        if (e.button !== 0) return;
        e.preventDefault();
        court.focus({preventScroll: true});
        gameSound.prime();
        if (state === "over") return;           // the last position stays to look at; New game starts again
        const [x, y] = boardKit.pointAt(canvas, e);
        const cell = boardKit.cellAt(x, y);
        const s = cell ? squareOf(...cell) : -1;
        if (!yourTurn() || s < 0) { sel = -1; hops = []; draw(); return; }
        if (sel >= 0 && s !== sel && landOn(s)) return;
        if (movable().has(s)) {
            sel = s;
            hops = [];
            drag = {from: s, x, y, moved: false};
        } else if (!hops.length) sel = -1;
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
        if (d.moved && yourTurn()) {
            const cell = boardKit.cellAt(...boardKit.pointAt(canvas, e));
            const s = cell ? squareOf(...cell) : -1;
            if (s >= 0 && s !== d.from) landOn(s);
        }
        draw();
    });

    document.addEventListener("keydown", e => {
        if (gameTab !== "checkers" || e.altKey) return;
        if (e.target.closest && e.target.closest("input, select, textarea, button, .modal, .dropdown-pop")) return;
        if (e.ctrlKey || e.metaKey) {     // Ctrl+Z, or ⌘Z on a Mac
            if (e.code === "KeyZ" && !e.shiftKey) { e.preventDefault(); takeBack(); }
            return;
        }
        if (e.code === "Escape") { sel = -1; hops = []; draw(); }
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
        if (performance.now() - anim.start >= (anim.path.length - 1) * HOP) { anim = null; draw(); return; }
        requestAnimationFrame(animate);
    }

    function drawPiece(p, x, y, size, faded) {
        const black = p > 0, r = size * 0.39;
        const body = black ? "#2a2a2a" : "#f3ede1", rim = black ? "#111" : "#b9ab90", ring = black ? "#4a4a4a" : "#d8ccb4";
        ctx.globalAlpha = faded ? 0.35 : 1;
        const layer = dy => {
            ctx.fillStyle = "rgba(0, 0, 0, 0.3)";
            ctx.beginPath();
            ctx.arc(x, y + dy + size * 0.045, r, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = body;
            ctx.strokeStyle = rim;
            ctx.lineWidth = size * 0.03;
            ctx.beginPath();
            ctx.arc(x, y + dy, r, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            ctx.strokeStyle = ring;
            ctx.lineWidth = size * 0.025;
            ctx.beginPath();
            ctx.arc(x, y + dy, r * 0.68, 0, Math.PI * 2);
            ctx.stroke();
        };
        const king = p === 2 || p === -2;
        layer(king ? size * 0.05 : 0);
        if (king) {
            layer(-size * 0.07);
            // A crown on top.
            const cy = y - size * 0.07, w = r * 0.9, h = r * 0.55;
            ctx.fillStyle = "#e0b640";
            ctx.beginPath();
            ctx.moveTo(x - w / 2, cy + h / 2);
            ctx.lineTo(x - w / 2, cy - h / 2);
            ctx.lineTo(x - w / 4, cy);
            ctx.lineTo(x, cy - h / 2 - 2);
            ctx.lineTo(x + w / 4, cy);
            ctx.lineTo(x + w / 2, cy - h / 2);
            ctx.lineTo(x + w / 2, cy + h / 2);
            ctx.closePath();
            ctx.fill();
        }
        ctx.globalAlpha = 1;
    }

    function draw() {
        if (!canvas.width) return;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        const tint = {};
        const key = s => cellOf(s).join(",");
        if (lastMove) for (const s of [lastMove.from, ...lastMove.path]) tint[key(s)] = BOARD.LAST;
        if (sel >= 0) { tint[key(sel)] = BOARD.PICKED; for (const h of hops) tint[key(h)] = BOARD.PICKED; }
        boardKit.drawSquares(ctx, tint);
        // Square numbers, faint, in the corner of each dark square (the notation's).
        ctx.fillStyle = "rgba(255, 255, 255, 0.45)";
        ctx.font = `600 11px ${colors.font}`;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        for (let s = 0; s < 32; s++) {
            const [x, y] = boardKit.cellXY(...cellOf(s));
            ctx.fillText(String(s + 1), x + 4, y + 3);
        }
        // Pieces; the moving one is drawn last, on top.
        const elapsed = anim ? performance.now() - anim.start : 0;
        const hop = anim ? Math.min(anim.path.length - 1, elapsed / HOP) : 0;
        const moving = anim ? anim.path[anim.path.length - 1] : -1;
        const chosen = hops.length ? hops : [];
        const passed = new Set(chosen.map((h, k) => options()[0] && options()[0].m.caps[k]).filter(c => c !== undefined));
        for (let s = 0; s < 32; s++) {
            const p = rules.board[s];
            if (!p || s === moving || (drag && drag.moved && s === drag.from) || (hops.length && s === sel)) continue;
            drawPiece(p, ...centreOf(s), CELL, passed.has(s));
        }
        if (anim) {
            anim.caps.forEach((c, k) => { if (hop < k + 0.6) drawPiece(c.piece, ...centreOf(c.sq), CELL); });
        }
        // The picked piece part-way through a jump stands on its last landing.
        if (hops.length && sel >= 0) drawPiece(rules.board[sel], ...centreOf(hops[hops.length - 1]), CELL);
        // Where it can land next (a dot), and which pieces can move when a jump is forced (a ring).
        if (yourTurn() && hintsOn()) {
            if (sel >= 0) {
                const next = new Set(options().map(({m}) => m.path[hops.length]).filter(s => s !== undefined));
                for (const s of next) {
                    const [x, y] = centreOf(s);
                    ctx.fillStyle = BOARD.DOT;
                    ctx.beginPath();
                    ctx.arc(x, y, CELL * 0.15, 0, Math.PI * 2);
                    ctx.fill();
                }
            }
            const legal = rules.legal();
            if (legal.length && legal[0].caps.length && sel < 0) {
                ctx.strokeStyle = BOARD.DOT;
                ctx.lineWidth = 4;
                for (const s of new Set(legal.map(m => m.from))) {
                    const [x, y] = centreOf(s);
                    ctx.beginPath();
                    ctx.arc(x, y, CELL / 2 - 4, 0, Math.PI * 2);
                    ctx.stroke();
                }
            }
        }
        if (anim) {
            const k = Math.floor(hop), f = hop - k;
            const a = anim.path[Math.min(k, anim.path.length - 1)], bSq = anim.path[Math.min(k + 1, anim.path.length - 1)];
            const [x0, y0] = centreOf(a), [x1, y1] = centreOf(bSq);
            const ease = 1 - (1 - f) * (1 - f);
            const done = hop >= anim.path.length - 1;
            drawPiece(done ? anim.crowned : anim.piece, x0 + (x1 - x0) * ease, y0 + (y1 - y0) * ease, CELL);
        }
        if (drag && drag.moved) drawPiece(rules.board[drag.from], drag.x, drag.y, CELL * 1.08);
        drawSide();
    }

    function drawSide() {
        const counts = [0, 0];
        for (const p of rules.board) if (p) counts[p > 0 ? 0 : 1] += 1;
        const row = side => (x, y) => {
            const took = 12 - counts[side > 0 ? 1 : 0];        // the other side's pieces gone
            for (let n = 0; n < took; n++) drawPiece(-side, x + 9 + n * 15, y + 2, 26);
        };
        const person = s => twoPlayers ? {name: sideName(s)}
            : s === you ? {name: "You", sub: sideName(s)} : {name: "Computer", sub: {easy: "Easy", medium: "Medium", hard: "Hard"}[levelName]};
        const topSide = flipped ? -1 : 1;
        const top = {...person(topSide), extra: row(topSide), turn: state !== "over" && rules.side === topSide};
        const bottom = {...person(-topSide), extra: row(-topSide), turn: state !== "over" && rules.side === -topSide};
        let status, line = "", hint = "", statusColor = colors.fg;
        if (state === "over") {
            status = !ending.winner ? "Draw" : twoPlayers ? `${sideName(ending.winner)} wins` : ending.winner === you ? "You win!" : "The computer wins";
            line = endNote();
            hint = "Press New game to play again";
            statusColor = colors.accent;
        } else if (state === "thinking") {
            status = "Thinking…";
        } else {
            status = twoPlayers ? `${sideName(rules.side)} to move` : "Your move";
            const legal = rules.legal();
            if (legal.length && legal[0].caps.length) line = "A jump is forced";
        }
        const rows = [];
        for (let i = 0; i < texts.length; i += 2) rows.push([texts[i], texts[i + 1] || ""]);
        boardKit.drawPanel(ctx, colors, {top, bottom, status, note: line, hint, statusColor, moves: rows});
    }

    function endNote() {
        if (!ending) return "";
        if (ending.result === "nomoves") return twoPlayers ? "No moves left" : ending.winner === you ? "The computer has no moves left" : "You have no moves left";
        return {forty: "40 moves each without a jump or a man moving", repetition: "The same position three times"}[ending.result] || "";
    }

    /* ------------------------------------------------------ states -- */
    function setState(next) {
        state = next;
        $("checkers-undo").disabled = !played.length;
        draw();
    }

    function drawOptions() {
        const two = prefs.checkers_mode === "two";
        for (const b of document.querySelectorAll("#checkers-mode button")) b.setAttribute("aria-pressed", String(b.dataset.mode === (two ? "two" : "cpu")));
        $("checkers-level-wrap").hidden = two;
        $("checkers-color-wrap").hidden = two;
        $("checkers-record").hidden = two;
    }

    $("checkers-mode").addEventListener("click", e => {
        const b = e.target.closest("button[data-mode]");
        if (b && b.dataset.mode !== (prefs.checkers_mode === "two" ? "two" : "cpu")) setPref("checkers_mode", b.dataset.mode);
    });
    $("checkers-new").addEventListener("click", newGame);
    $("checkers-undo").addEventListener("click", takeBack);
    onTab(name => { if (name === "checkers") requestAnimationFrame(fit); });
    onPrefs(key => {
        drawOptions();
        if (key === "checkers_mode" || key === "checkers_level" || key === "checkers_color") newGame();
        else draw();
    });

    let started = false;
    Buddy.on("state", s => {
        record = s.checkers_record || {};
        boardKit.showRecord($("checkers-record"), record);
        if (!started) { started = true; newGame(); }
        drawOptions();
    });
    Buddy.on("checkers_record", r => { record = r; boardKit.showRecord($("checkers-record"), record); });
    Buddy.on("theme", () => requestAnimationFrame(() => { colors = gameColors(); draw(); }));
    Buddy.on("i18n", () => requestAnimationFrame(draw));
    Buddy.holdWhile(() => played.length > 0 && state !== "over");         // a game under way: kept (buddy.js keepAlive)
    watchCourt(court, fit);

    colors = gameColors();
    boardKit.showRecord($("checkers-record"), record);
    return {newGame, takeBack, play, landOn, get state() { return state; }, get rules() { return rules; },
            get played() { return played; }, get texts() { return texts; }, get you() { return you; },
            get viaWorker() { return computer.viaWorker; },
            pick: s => { sel = s; hops = []; draw(); }};
})();
