/* Wordin' (loaded after games.js and words_list.js, whose lists it
   uses): find the hidden five-letter word in six guesses. Each guess must
   be a word; its letters then turn green (right letter, right place),
   yellow (in the word, somewhere else) or grey (not in the word) - a
   letter guessed twice is coloured only as many times as the word holds
   it. Daily: one word a day, the same for everyone on that day, played
   once; Unlimited: a new word whenever you like. Hard mode: letters found
   must be used in every guess after. Typed, or clicked on the keyboard
   under the board; a guess in progress is kept between runs. Turn by
   turn, so there's nothing to pause. */
"use strict";

const words = (() => {
    const ROWS = 6, LEN = 5;
    const ANSWERS = WORD_ANSWERS.split(" ");
    const VALID = new Set(WORD_GUESSES.split(" ").concat(ANSWERS));
    const DAY0 = Date.UTC(2026, 0, 1);
    const PRAISE = ["First try!", "Brilliant!", "Great!", "Nice!", "Good!", "Phew!"];
    const STAGGER = 260, FLIP = 320;          // ms between tiles turning over; one tile's turn
    const KEY_ROWS = ["qwertyuiop", "asdfghjkl", "+zxcvbnm-"];      // + Enter, - Backspace
    const RANK = {absent: 1, present: 2, correct: 3};

    /* The daily order: the answers shuffled once with a fixed seed, so
       every Buddy has the same word on the same day. */
    const ORDER = (() => {
        let s = 0x2f6b3c1d;
        const rand = () => {
            s = (s + 0x6d2b79f5) | 0;
            let t = Math.imul(s ^ (s >>> 15), 1 | s);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
        const idx = ANSWERS.map((_, i) => i);
        for (let i = idx.length - 1; i > 0; i--) {
            const j = Math.floor(rand() * (i + 1));
            [idx[i], idx[j]] = [idx[j], idx[i]];
        }
        return idx;
    })();

    const court = $("words-court"), board = $("words-board"), keys = $("words-keys");
    let mode = "daily";        // daily, unlimited
    let index = 0;             // the answer's place in ANSWERS
    let answer = "";
    let day = "";              // the daily word's date (YYYY-MM-DD, local)
    let guesses = [];          // submitted words
    let marks = [];            // per guess: LEN of correct / present / absent
    let typed = "";
    let state = "play";        // play, won, lost
    let hard = false;          // this word's hard mode, fixed once a guess is in
    let busy = false;          // tiles turning over: input waits
    let stats = {};            // {daily: {played, won, streak, best, dist, last}, unlimited: {...}} (page.py)
    let shownEnd = false;      // the end panel came up for this word
    let toastTimer = null, tickTimer = null;
    // Each word its own number: the reveal's steps run later, and one from a
    // word left behind (New word, Daily / Unlimited mid-reveal) mustn't touch
    // the next - it painted a new board's row green and sent its win twice.
    let gen = 0;
    const timers = [];
    const later = (fn, ms) => { const g = gen; timers.push(setTimeout(() => { if (g === gen) fn(); }, ms)); };

    const soundOn = () => prefs.words_sound !== false;
    const blip = (freq, length, type, at) => gameSound.blip(soundOn(), freq, length, type, at);
    const upper = w => w.toUpperCase();

    /* ------------------------------------------------------- dates -- */
    function dateKey(d) {
        const p = n => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }
    const today = () => dateKey(new Date());
    function yesterday() {
        const d = new Date();
        d.setDate(d.getDate() - 1);
        return dateKey(d);
    }
    function dailyIndex(key) {
        const [y, m, d] = key.split("-").map(Number);
        const n = Math.round((Date.UTC(y, m - 1, d) - DAY0) / 86400000);
        return ORDER[((n % ORDER.length) + ORDER.length) % ORDER.length];
    }
    function untilMidnight() {
        const now = new Date(), next = new Date(now);
        next.setHours(24, 0, 0, 0);
        const s = Math.max(0, Math.round((next - now) / 1000));
        return `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }

    /* ---------------------------------------------------- the rules -- */
    /* correct / present / absent for each letter of `guess`. Greens first,
       then yellows only while the answer has that letter left over. */
    function score(guess, target) {
        const out = Array(LEN).fill("absent"), left = {};
        for (let i = 0; i < LEN; i++) {
            if (guess[i] === target[i]) out[i] = "correct";
            else left[target[i]] = (left[target[i]] || 0) + 1;
        }
        for (let i = 0; i < LEN; i++) {
            if (out[i] === "correct" || !left[guess[i]]) continue;
            out[i] = "present";
            left[guess[i]] -= 1;
        }
        return out;
    }

    /* Hard mode: why `guess` doesn't use what's been found, or "" - in
       English, translated as it shows (buddy.js). */
    function hardProblem(guess) {
        for (let g = 0; g < guesses.length; g++) {
            const word = guesses[g], m = marks[g], need = {};
            for (let i = 0; i < LEN; i++) {
                if (m[i] === "correct" && guess[i] !== word[i]) {
                    return `Letter ${i + 1} must be ${upper(word[i])}`;
                }
                if (m[i] !== "absent") need[word[i]] = (need[word[i]] || 0) + 1;
            }
            for (const [letter, count] of Object.entries(need)) {
                if (guess.split(letter).length - 1 < count) {
                    return `Guess must contain ${upper(letter)}`;
                }
            }
        }
        return "";
    }

    /* ----------------------------------------------------- drawing -- */
    function build() {
        board.replaceChildren(...Array.from({length: ROWS}, () =>
            el("div.words-row", {}, Array.from({length: LEN}, () => el("div.words-tile")))));
        keys.replaceChildren(...KEY_ROWS.map(row => el("div.words-key-row", {}, [...row].map(k => {
            const key = k === "+" ? "enter" : k === "-" ? "back" : k;
            const b = el("button.words-key", {type: "button", tabindex: "-1", dataset: {key},
                                              text: key === "enter" ? "Enter" : key === "back" ? "⌫" : upper(k)});
            if (key !== "enter" && key !== "back") b.setAttribute("aria-label", upper(k));
            else if (key === "back") b.setAttribute("aria-label", "Backspace");
            if (key === "enter" || key === "back") b.classList.add("wide");
            return b;
        }))));
    }

    function tile(r, c) {
        return board.children[r].children[c];
    }

    /* Every tile and key as the game stands (no animation). */
    function render() {
        for (let r = 0; r < ROWS; r++) {
            for (let c = 0; c < LEN; c++) {
                const t = tile(r, c);
                let letter = "", cls = "";
                if (r < guesses.length) {
                    letter = guesses[r][c];
                    cls = marks[r][c];
                } else if (r === guesses.length && state === "play") {
                    letter = typed[c] || "";
                    cls = letter ? "filled" : "";
                }
                t.textContent = upper(letter);
                t.className = `words-tile ${cls}`.trim();
            }
        }
        paintKeys();
    }

    function paintKeys() {
        const best = {};
        guesses.forEach((g, r) => [...g].forEach((letter, i) => {
            const m = marks[r][i];
            if (!best[letter] || RANK[m] > RANK[best[letter]]) best[letter] = m;
        }));
        for (const b of keys.querySelectorAll(".words-key")) {
            b.classList.remove("correct", "present", "absent");
            if (best[b.dataset.key]) b.classList.add(best[b.dataset.key]);
        }
    }

    function toast(text, ms = 1500) {
        const t = $("words-toast");
        t.textContent = text;
        t.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { t.hidden = true; }, ms);
    }

    function shake() {
        const row = board.children[Math.min(guesses.length, ROWS - 1)];
        row.classList.remove("shake");
        void row.offsetWidth;                              // restart the animation
        row.classList.add("shake");
        blip(140, 0.12, "sawtooth");
    }

    /* The largest board and keyboard the card has room for. */
    function fit() {
        const card = court.parentElement;
        if (!card.clientWidth) return;
        const style = getComputedStyle(card);
        const roomW = card.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 16;
        const roomH = card.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) - 16;
        // Tile t: board 6t + 5 gaps (t/10), then a gap, then 3 key rows (0.9t) and 2 gaps.
        const t = Math.max(26, Math.min(66, Math.floor(Math.min((roomH - 30) / (6.5 + 2.7 + 0.25), roomW / 5.4))));
        const keyGap = Math.max(4, Math.round(t / 11));
        const keyW = Math.max(18, Math.min(Math.round(t * 0.78), Math.floor((roomW - 9 * keyGap) / 10)));
        court.style.setProperty("--t", `${t}px`);
        court.style.setProperty("--kw", `${keyW}px`);
        court.style.setProperty("--kh", `${Math.round(t * 0.9)}px`);
        court.style.setProperty("--kg", `${keyGap}px`);
    }

    /* -------------------------------------------------- end of a word -- */
    function statsFor(m) {
        const s = stats[m] || {};
        const dist = Array.isArray(s.dist) ? s.dist : [];
        let streak = s.streak || 0;
        // A daily streak lasts while yesterday's (or today's) word was solved.
        if (m === "daily" && s.last !== today() && s.last !== yesterday()) streak = 0;
        return {played: s.played || 0, won: s.won || 0, streak, best: s.best || 0,
                dist: Array.from({length: ROWS}, (_, i) => dist[i] || 0)};
    }

    function drawScore() {
        const s = statsFor(mode);
        const pct = s.played ? Math.round(100 * s.won / s.played) : 0;
        $("words-score").replaceChildren(
            el("span.muted", {text: "Played"}), ` ${s.played}  ·  `,
            el("span.muted", {text: "Won"}), ` ${pct}%  ·  `,
            el("span.muted", {text: "Streak"}), ` ${s.streak}`,
        );
        $("words-new").hidden = mode !== "unlimited";
        for (const b of document.querySelectorAll("#words-mode button")) b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
    }

    /* The end panel (or the statistics, mid-word): how it went, the
       record, the spread of guesses, and what's next. */
    function showPanel() {
        const s = statsFor(mode);
        const over = state !== "play";
        const title = state === "won" ? PRAISE[guesses.length - 1] : state === "lost" ? "Out of guesses" : "Statistics";
        $("words-overlay-title").textContent = title;
        $("words-overlay-text").textContent = state === "won" ? `Found in ${guesses.length} of 6`
            : state === "lost" ? `The word was ${upper(answer)}` : "";
        $("words-overlay-text").hidden = !over;
        const pct = s.played ? Math.round(100 * s.won / s.played) : 0;
        $("words-statline").replaceChildren(...[["Played", s.played], ["Won", `${pct}%`], ["Streak", s.streak], ["Best streak", s.best]]
            .map(([label, value]) => el("div.words-stat", {}, [el("b", {text: String(value)}), el("span.muted.small", {text: label})])));
        const top = Math.max(1, ...s.dist);
        $("words-dist").replaceChildren(...s.dist.map((n, i) => el("div.words-bar-row", {}, [
            el("span.words-bar-label", {text: String(i + 1)}),
            el("div.words-bar" + (state === "won" && guesses.length === i + 1 ? ".now" : ""),
               {style: `width: ${Math.max(7, 100 * n / top)}%`}, [el("span", {text: String(n)})]),
        ])));
        $("words-share").hidden = !over;
        $("words-overlay").hidden = false;
        tickNext();
    }

    /* The result for Buddy Network: only the colours go (page.py makes the
       squares), never a letter - the word stays hidden for everyone else. */
    function share() {
        if (state === "play") return;
        send("share_words", {mode, day: mode === "daily" ? day : undefined, won: state === "won",
                             hard, contrast: prefs.words_contrast === true, marks});
    }

    function tickNext() {
        clearInterval(tickTimer);
        const next = $("words-overlay-next");
        const write = () => {
            if (mode === "daily") {
                next.textContent = state === "play" ? "Click to carry on" : `Next word in ${untilMidnight()}`;
                if (state !== "play" && day !== today()) loadDaily();
            } else {
                next.textContent = state === "play" ? "Click to carry on" : "Press Enter or click for a new word";
            }
        };
        write();
        if (mode === "daily" && state !== "play") tickTimer = setInterval(() => { if (!$("words-overlay").hidden) write(); }, 1000);
    }

    function hidePanel() {
        $("words-overlay").hidden = true;
        clearInterval(tickTimer);
        court.focus({preventScroll: true});
    }

    function finish() {
        drawScore();
        if (state === "won") {
            [523, 659, 784, 1047].forEach((f, i) => blip(f, 0.12, "triangle", i * 0.09));
            board.children[guesses.length - 1].classList.add("bounce");
        } else {
            blip(196, 0.25, "triangle");
            toast(upper(answer), 2600);
        }
        later(() => { if (state !== "play" && !shownEnd) { shownEnd = true; showPanel(); } }, state === "won" ? 1400 : 1700);
    }

    /* --------------------------------------------------- playing -- */
    function save() {
        const text = `${mode === "daily" ? day : index}|${hard ? 1 : 0}|${guesses.join(",")}`;
        setPref(mode === "daily" ? "words_today" : "words_free", text);
    }

    function type(letter) {
        if (state !== "play" || busy) return;
        if (typed.length >= LEN) return;
        typed += letter;
        const t = tile(guesses.length, typed.length - 1);
        t.textContent = upper(letter);
        t.className = "words-tile filled pop";
        blip(620, 0.03);
    }

    function back() {
        if (state !== "play" || busy || !typed) return;
        typed = typed.slice(0, -1);
        const t = tile(guesses.length, typed.length);
        t.textContent = "";
        t.className = "words-tile";
    }

    function submit() {
        if (state !== "play" || busy) return;
        if (typed.length < LEN) { shake(); return toast("Not enough letters"); }
        if (!VALID.has(typed)) { shake(); return toast("Not in the word list"); }
        if (!guesses.length) hard = prefs.words_hard === true;
        const problem = hard ? hardProblem(typed) : "";
        if (problem) { shake(); return toast(problem); }
        const guess = typed, m = score(guess, answer), row = guesses.length;
        guesses.push(guess);
        marks.push(m);
        typed = "";
        busy = true;
        if (guess === answer) state = "won";
        else if (guesses.length >= ROWS) state = "lost";
        save();
        // The word's result goes now, once - not at the end of the reveal,
        // which a new word or a closed page can cut short.
        if (state !== "play") {
            send("result", {game: "words", mode, won: state === "won", guesses: guesses.length,
                            ...(mode === "daily" ? {day} : {})});
        }
        const tones = {correct: 880, present: 660, absent: 330};
        for (let i = 0; i < LEN; i++) {
            const t = tile(row, i);
            later(() => {
                t.classList.add("flip");
                blip(tones[m[i]], 0.06, "sine");
            }, i * STAGGER);
            later(() => { t.className = `words-tile ${m[i]} flip`; }, i * STAGGER + FLIP / 2);
        }
        later(() => {
            busy = false;
            for (let i = 0; i < LEN; i++) tile(row, i).classList.remove("flip");
            paintKeys();
            if (state !== "play") finish();
        }, (LEN - 1) * STAGGER + FLIP + 30);
    }

    function press(key) {
        gameSound.prime();
        if (!$("words-overlay").hidden) {
            if (key === "enter") overlayClick();
            return;
        }
        if (key === "enter") submit();
        else if (key === "back") back();
        else if (/^[a-z]$/.test(key)) type(key);
    }

    /* ---------------------------------------------- loading a word -- */
    function start(nextMode, nextIndex, saved) {
        gen++;                                             // the last word's reveal stops here
        timers.forEach(clearTimeout);
        timers.length = 0;
        mode = nextMode;
        index = nextIndex;
        answer = ANSWERS[index];
        guesses = [];
        marks = [];
        typed = "";
        busy = false;
        hard = prefs.words_hard === true;
        state = "play";
        if (saved) {
            hard = saved.hard;
            for (const g of saved.guesses) {
                if (guesses.length >= ROWS || state !== "play" || !VALID.has(g)) break;
                guesses.push(g);
                marks.push(score(g, answer));
                if (g === answer) state = "won";
                else if (guesses.length >= ROWS) state = "lost";
            }
        }
        shownEnd = state !== "play";
        clearTimeout(toastTimer);
        $("words-toast").hidden = true;
        $("words-overlay").hidden = true;
        clearInterval(tickTimer);
        for (const row of board.children) row.classList.remove("shake", "bounce");
        render();
        drawScore();
    }

    /* A saved word: "key|hard|guess,guess" (key: the day, or the index). */
    function readSaved(text) {
        const parts = typeof text === "string" ? text.split("|") : [];
        if (parts.length !== 3) return null;
        return {key: parts[0], hard: parts[1] === "1", guesses: parts[2] ? parts[2].split(",").filter(g => /^[a-z]{5}$/.test(g)) : []};
    }

    function loadDaily() {
        day = today();
        const saved = readSaved(prefs.words_today);
        start("daily", dailyIndex(day), saved && saved.key === day ? saved : null);
    }

    function loadUnlimited(fresh) {
        const saved = fresh ? null : readSaved(prefs.words_free);
        const i = saved ? Number(saved.key) : NaN;
        if (saved && Number.isInteger(i) && i >= 0 && i < ANSWERS.length) {
            start("unlimited", i, saved);
            if (state === "play") return;
        }
        let next = Math.floor(Math.random() * ANSWERS.length);
        if (next === index && ANSWERS.length > 1) next = (next + 1) % ANSWERS.length;
        start("unlimited", next, null);
        save();
    }

    function load() {
        if ((prefs.words_mode === "unlimited" ? "unlimited" : "daily") === "unlimited") loadUnlimited(false);
        else loadDaily();
    }

    function overlayClick() {
        if (state !== "play" && mode === "unlimited") return loadUnlimited(true);
        hidePanel();
    }

    /* ------------------------------------------------------- input -- */
    keys.addEventListener("mousedown", e => {
        const b = e.target.closest(".words-key");
        e.preventDefault();                                   // the court keeps the keyboard focus
        if (b) press(b.dataset.key);
    });
    court.addEventListener("mousedown", e => {
        if (e.target.closest(".words-key")) return;
        court.focus({preventScroll: true});
        gameSound.prime();
    });
    $("words-overlay").addEventListener("click", overlayClick);
    $("words-close").append(Buddy.icon("close"));
    $("words-close").addEventListener("click", e => { e.stopPropagation(); hidePanel(); });
    $("words-share").append(Buddy.icon("send"), Buddy.t("Share on Buddy Network"));
    $("words-share").title = Buddy.t("Your squares, without the word");
    $("words-share").addEventListener("click", e => { e.stopPropagation(); share(); });

    document.addEventListener("keydown", e => {
        if (gameTab !== "words" || e.altKey || e.ctrlKey || e.metaKey) return;
        if (e.target.closest && e.target.closest("input, select, textarea, .modal, .dropdown-pop")) return;
        if (e.target.closest && e.target.closest("button") && (e.key === "Enter" || e.key === " ")) return;
        if (e.key === "Enter") { e.preventDefault(); press("enter"); }
        else if (e.key === "Backspace") { e.preventDefault(); press("back"); }
        else if (e.key === "Escape") { if (!$("words-overlay").hidden) hidePanel(); }
        else if (/^[a-zA-Z]$/.test(e.key)) { e.preventDefault(); press(e.key.toLowerCase()); }
    });

    $("words-mode").addEventListener("click", e => {
        const b = e.target.closest("button[data-mode]");
        if (b && b.dataset.mode !== mode) setPref("words_mode", b.dataset.mode);
        court.focus({preventScroll: true});
    });
    $("words-new").addEventListener("click", () => { loadUnlimited(true); court.focus({preventScroll: true}); });
    $("words-stats").addEventListener("click", () => {
        if ($("words-overlay").hidden) showPanel();
        else hidePanel();
    });
    onPrefs(key => {
        if (key === null) return;                           // the saved state arriving: Buddy.on("state") loads
        if (key === "words_mode") load();
        if (key === "words_hard" && guesses.length && state === "play" && hard !== (prefs.words_hard === true)) {
            toast("Hard mode changes with the next word", 2200);
        }
        if (key === "words_hard" && !guesses.length) hard = prefs.words_hard === true;
        if (key === "words_contrast") court.classList.toggle("contrast", prefs.words_contrast === true);
    });
    onTab(name => {
        if (name !== "words") return;
        requestAnimationFrame(fit);
        if (mode === "daily" && day !== today() && !busy) loadDaily();       // past midnight since
        court.focus({preventScroll: true});
    });

    Buddy.on("state", s => {
        stats = JSON.parse(JSON.stringify(s.words_stats || {}));
        court.classList.toggle("contrast", prefs.words_contrast === true);
        load();
    });
    Buddy.on("words_stats", s => {
        stats = JSON.parse(JSON.stringify(s));
        drawScore();
        if (!$("words-overlay").hidden) showPanel();
    });
    Buddy.on("i18n", () => { if (!$("words-overlay").hidden) showPanel(); });
    Buddy.holdWhile(() => state === "play" && (guesses.length > 0 || typed.length > 0));     // a word on the go: kept
    watchCourt(court, fit);

    build();
    start("daily", dailyIndex(today()), null);
    day = today();
    return {press, submit, type, back, score, load, loadDaily, loadUnlimited,
            get state() { return state; }, get answer() { return answer; }, get guesses() { return guesses; },
            get marks() { return marks; }, get mode() { return mode; }, get hard() { return hard; },
            get busy() { return busy; }, valid: w => VALID.has(w), dailyIndex, answers: ANSWERS};
})();
