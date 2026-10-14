#!/usr/bin/env python3
"""
Games - a main tab of its own, under its own heading, with a sub-tab per
game (web/index.html): Pong (web/pong.js), against the computer or two
players on one keyboard, Snake (web/snake.js), Minesweeper (web/mines.js),
Solitaire (web/solitaire.js, Klondike), Falling blocks (web/blocks.js),
2048 (web/g2048.js), Breakout (web/breakout.js), Sudoku
(web/sudoku.js), Wordin' (web/words.js, its word lists in
web/words_list.js), Checkers (web/checkers.js) and Chess (web/chess.js) -
those two against the computer, whose play is in web/checkers_engine.js
and web/chess_engine.js (run as Web Workers), or two players.

Each game runs entirely in the view. Python keeps what should outlast a
restart: the open sub-tab, each game's settings (prefs, as Essentials
keeps its fields), Pong's record against the computer, Snake's best
score for each speed, walls on or off, Minesweeper's best time for
each difficulty, Solitaire's games won and best time for each deal, the
best Falling blocks, 2048 and Breakout scores, Sudoku's puzzles
solved and best time for each difficulty, Wordin''s words played and
won, streaks and guesses taken (daily and unlimited apart; a guess in
progress is a pref), and the games of Checkers and
Chess won, lost and drawn against the computer at each difficulty.

Never touches Resolve or the internet. A Wordin' result can be shared:
its squares - colours only, never the word - go into Buddy Network's
message box (and the clipboard), to send from whichever room is open.

Protocol:
    to the view    state, record, snake_best, mines_best, solitaire_stats,
                   blocks_best, best_2048, breakout_best, sudoku_stats,
                   checkers_record, chess_record, words_stats
    from the view  tab, pref, result, share_words
"""

import datetime
import os

from PySide6.QtGui import QGuiApplication

from core.web_page import WebToolPage

TABS = ("pong", "snake", "mines", "solitaire", "blocks", "2048", "breakout", "sudoku", "words", "checkers", "chess")
SOLITAIRE_MODES = ("draw1", "draw3")
MINES_LEVELS = ("beginner", "intermediate", "expert")
MAX_MINES_SECONDS = 24 * 3600
DIFFICULTIES = ("easy", "medium", "hard")
BOARD_GAMES = ("checkers", "chess")     # played against the computer; won, lost or drawn
OUTCOMES = ("won", "lost", "drawn")
SNAKE_KEYS = tuple(f"{speed}_{edge}" for speed in ("slow", "normal", "fast") for edge in ("walls", "wrap"))
MAX_SNAKE_SCORE = 32 * 20       # every square of the board
SUDOKU_LEVELS = ("easy", "medium", "hard")
MAX_SCORE = 100_000_000
MAX_2048_TILE = 1 << 17         # the biggest tile a 4 x 4 board can hold
DEFAULTS = {"tab": "pong", "prefs": {}, "pong_record": {}, "snake_best": {}, "mines_best": {},
            "solitaire_stats": {}, "blocks_best": {}, "best_2048": {}, "breakout_best": {}, "sudoku_stats": {},
            "checkers_record": {}, "chess_record": {}, "words_stats": {}}
MAX_PREFS = 60          # every game's settings together (37 so far)
WORD_MODES = ("daily", "unlimited")
WORD_ROWS = 6
MAX_PREF_KEY = 40
MAX_PREF_TEXT = 100
MAX_COUNT = 1_000_000
WORD_LEN = 5
WORD_DAY0 = datetime.date(2026, 1, 1)      # words.js's DAY0: the daily word's number 1
WORD_SQUARES = {"correct": "🟩", "present": "🟨", "absent": "⬛"}            # green, yellow, black
WORD_SQUARES_CONTRAST = {"correct": "🟧", "present": "🟦", "absent": "⬛"}   # orange, blue


def words_share_text(payload) -> str | None:
    """A finished Wordin' as Wordle shares it - the title line, then a row
    of squares per guess. Built from the colours alone, so the word can't
    get in. None for anything that isn't a finished game."""
    mode, won, marks = payload.get("mode"), payload.get("won"), payload.get("marks")
    if mode not in WORD_MODES or not isinstance(won, bool) or not isinstance(marks, list):
        return None
    if not 1 <= len(marks) <= WORD_ROWS or not all(
            isinstance(row, list) and len(row) == WORD_LEN and all(m in WORD_SQUARES for m in row) for row in marks):
        return None
    if won != all(m == "correct" for m in marks[-1]) or any(all(m == "correct" for m in row) for row in marks[:-1]):
        return None
    if not won and len(marks) != WORD_ROWS:
        return None
    squares = WORD_SQUARES_CONTRAST if payload.get("contrast") is True else WORD_SQUARES
    score = f"{len(marks) if won else 'X'}/{WORD_ROWS}" + ("*" if payload.get("hard") is True else "")
    if mode == "daily":
        day = clean_day(payload.get("day"))
        if day is None:
            return None
        title = f"Wordin' #{(datetime.date.fromisoformat(day) - WORD_DAY0).days + 1} {score}"
    else:
        title = f"Wordin' (unlimited) {score}"
    return "\n".join([title, ""] + ["".join(squares[m] for m in row) for row in marks])


def clean_pref(value):
    """A pref is one plain value - a choice, a number or a tick."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) < 1e9 else None
    if isinstance(value, str):
        return value[:MAX_PREF_TEXT]
    return None


def clean_record(raw, keys=("won", "lost")) -> dict:
    """Against the computer: {difficulty: {"won": n, "lost": n}} - and
    "drawn" too for Checkers and Chess (keys)."""
    out = {}
    for level in DIFFICULTIES:
        entry = raw.get(level) if isinstance(raw, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        out[level] = {key: min(MAX_COUNT, value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                      else 0 for key, value in ((k, entry.get(k)) for k in keys)}
    return out


def clean_snake_best(raw) -> dict:
    """Snake's best score for each speed and walls setting ("normal_walls")."""
    raw = raw if isinstance(raw, dict) else {}
    return {key: raw[key] for key in SNAKE_KEYS
            if isinstance(raw.get(key), int) and not isinstance(raw.get(key), bool) and 0 < raw[key] <= MAX_SNAKE_SCORE}


def clean_seconds(value):
    """A Minesweeper time: seconds to a tenth, or None if it isn't one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= MAX_MINES_SECONDS:
        return None
    return round(float(value), 1)


def clean_mines_best(raw) -> dict:
    """Minesweeper's best time for each difficulty."""
    raw = raw if isinstance(raw, dict) else {}
    return {level: clean_seconds(raw[level]) for level in MINES_LEVELS if clean_seconds(raw.get(level)) is not None}


def clean_count(value, top=MAX_SCORE):
    """A whole number from 0 to `top`, or None."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
        return None
    return value


def clean_best(raw, fields) -> dict:
    """A best score and what went with it: {"score": n, field: n, ...}
    ({} if there's no score yet). fields: {name: largest}."""
    raw = raw if isinstance(raw, dict) else {}
    score = clean_count(raw.get("score"))
    if not score:
        return {}
    out = {"score": score}
    for name, top in fields.items():
        value = clean_count(raw.get(name), top)
        if value is not None:
            out[name] = value
    return out


BLOCKS_FIELDS = {"lines": 1_000_000, "level": 100}
FIELDS_2048 = {"tile": MAX_2048_TILE}
BREAKOUT_FIELDS = {"level": 10_000}


def clean_solitaire_stats(raw) -> dict:
    """Solitaire, for each deal (draw one, draw three): games won and the best time."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for mode in SOLITAIRE_MODES:
        entry = raw.get(mode) if isinstance(raw.get(mode), dict) else {}
        won = entry.get("won")
        won = min(MAX_COUNT, won) if isinstance(won, int) and not isinstance(won, bool) and won > 0 else 0
        best = clean_seconds(entry.get("best"))
        if won:
            out[mode] = {"won": won, "best": best or 0}
    return out


def clean_sudoku_stats(raw) -> dict:
    """Sudoku, for each difficulty: puzzles solved and the best time."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for level in SUDOKU_LEVELS:
        entry = raw.get(level) if isinstance(raw.get(level), dict) else {}
        won = clean_count(entry.get("won"), MAX_COUNT)
        if won:
            out[level] = {"won": won, "best": clean_seconds(entry.get("best")) or 0}
    return out


def clean_day(value):
    """A date as "YYYY-MM-DD", or None."""
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def clean_words_stats(raw) -> dict:
    """Wordin', daily and unlimited apart: words played and won, the
    streak of words won in a row (daily: day after day) and the longest,
    how many guesses each word won took (dist: 1 to 6), and for daily the
    last day played."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for mode in WORD_MODES:
        entry = raw.get(mode) if isinstance(raw.get(mode), dict) else {}
        played = clean_count(entry.get("played"), MAX_COUNT)
        if not played:
            continue
        dist = entry.get("dist") if isinstance(entry.get("dist"), list) else []
        clean = {"played": played,
                 **{key: clean_count(entry.get(key), MAX_COUNT) or 0 for key in ("won", "streak", "best")},
                 "dist": [clean_count(dist[i], MAX_COUNT) or 0 if i < len(dist) else 0 for i in range(WORD_ROWS)]}
        clean["won"] = min(clean["won"], played)
        if mode == "daily" and clean_day(entry.get("last")):
            clean["last"] = clean_day(entry.get("last"))
        out[mode] = clean
    return out


class GamesPage(WebToolPage):
    tool_id = "games"
    display_name = "Games"
    category = "Games"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        prefs = self.settings.get("prefs")
        self.prefs = {str(k)[:MAX_PREF_KEY]: clean_pref(v) for k, v in prefs.items()} if isinstance(prefs, dict) else {}
        self.record = clean_record(self.settings.get("pong_record"))
        self.board_records = {g: clean_record(self.settings.get(f"{g}_record"), OUTCOMES) for g in BOARD_GAMES}
        self.snake_best = clean_snake_best(self.settings.get("snake_best"))
        self.mines_best = clean_mines_best(self.settings.get("mines_best"))
        self.solitaire_stats = clean_solitaire_stats(self.settings.get("solitaire_stats"))
        self.blocks_best = clean_best(self.settings.get("blocks_best"), BLOCKS_FIELDS)
        self.best_2048 = clean_best(self.settings.get("best_2048"), FIELDS_2048)
        self.breakout_best = clean_best(self.settings.get("breakout_best"), BREAKOUT_FIELDS)
        self.sudoku_stats = clean_sudoku_stats(self.settings.get("sudoku_stats"))
        self.words_stats = clean_words_stats(self.settings.get("words_stats"))

    def web_ready(self):
        tab = self.settings.get("tab")
        self.emit("state", {"tab": tab if tab in TABS else TABS[0], "prefs": self.prefs, "record": self.record,
                            "snake_best": self.snake_best, "mines_best": self.mines_best,
                            "solitaire_stats": self.solitaire_stats, "blocks_best": self.blocks_best,
                            "best_2048": self.best_2048, "breakout_best": self.breakout_best,
                            "sudoku_stats": self.sudoku_stats, "words_stats": self.words_stats,
                            **{f"{g}_record": self.board_records[g] for g in BOARD_GAMES}})

    def on_tab(self, payload):
        tab = (payload or {}).get("tab")
        if tab in TABS and tab != self.settings.get("tab"):
            self.settings["tab"] = tab
            self.settings.save()

    def on_pref(self, payload):
        payload = payload or {}
        key = payload.get("key")
        if not isinstance(key, str) or not key or len(key) > MAX_PREF_KEY:
            return
        if key not in self.prefs and len(self.prefs) >= MAX_PREFS:
            return
        value = clean_pref(payload.get("value"))
        if self.prefs.get(key) == value:
            return
        self.prefs[key] = value
        self.settings["prefs"] = dict(self.prefs)
        self.settings.save()

    def on_result(self, payload):
        """A game finished: Pong against the computer is one more won or
        lost; Snake's score and a Minesweeper win's time are kept if
        they're the best yet."""
        payload = payload or {}
        if payload.get("game") == "snake":
            return self._snake_result(payload)
        if payload.get("game") == "mines":
            return self._mines_result(payload)
        if payload.get("game") == "solitaire":
            return self._solitaire_result(payload)
        if payload.get("game") == "sudoku":
            return self._sudoku_result(payload)
        if payload.get("game") == "words":
            return self._words_result(payload)
        if payload.get("game") in BOARD_GAMES:
            return self._board_result(payload)
        best = {"blocks": ("blocks_best", BLOCKS_FIELDS), "2048": ("best_2048", FIELDS_2048),
                "breakout": ("breakout_best", BREAKOUT_FIELDS)}.get(payload.get("game"))
        if best:
            return self._best_score(payload, *best)
        level, won = payload.get("difficulty"), payload.get("won")
        if payload.get("game") != "pong" or level not in DIFFICULTIES or not isinstance(won, bool):
            return
        entry = self.record[level]
        key = "won" if won else "lost"
        if won:
            self.achieve("games_won", 1)
        entry[key] = min(MAX_COUNT, entry[key] + 1)
        self.settings["pong_record"] = self.record
        self.settings.save()
        self.emit("record", self.record)

    def _snake_result(self, payload):
        speed, walls, score = payload.get("speed"), payload.get("walls"), payload.get("score")
        key = f"{speed}_{'walls' if walls else 'wrap'}"
        if (key not in SNAKE_KEYS or not isinstance(walls, bool) or not isinstance(score, int)
                or isinstance(score, bool) or not 0 < score <= MAX_SNAKE_SCORE):
            return
        if score > self.snake_best.get(key, 0):
            self.snake_best[key] = score
            self.settings["snake_best"] = dict(self.snake_best)
            self.settings.save()
        self.emit("snake_best", self.snake_best)

    def _mines_result(self, payload):
        """A board cleared (only wins are sent): the time, if it's the best."""
        level, seconds = payload.get("level"), clean_seconds(payload.get("seconds"))
        if level not in MINES_LEVELS or seconds is None:
            return
        self.achieve("games_won", 1)
        if level not in self.mines_best or seconds < self.mines_best[level]:
            self.mines_best[level] = seconds
            self.settings["mines_best"] = dict(self.mines_best)
            self.settings.save()
        self.emit("mines_best", self.mines_best)

    def _solitaire_result(self, payload):
        """A game of Solitaire won (only wins are sent): one more won, and the time if it's the best."""
        draw, seconds = payload.get("draw"), clean_seconds(payload.get("seconds"))
        mode = f"draw{draw}" if draw in (1, 3) and not isinstance(draw, bool) else None
        if mode is None or seconds is None:
            return
        self.achieve("games_won", 1)
        entry = self.solitaire_stats.get(mode, {"won": 0, "best": 0})
        self.solitaire_stats[mode] = {"won": min(MAX_COUNT, entry["won"] + 1),
                                      "best": seconds if not entry["best"] or seconds < entry["best"] else entry["best"]}
        self.settings["solitaire_stats"] = {k: dict(v) for k, v in self.solitaire_stats.items()}
        self.settings.save()
        self.emit("solitaire_stats", self.solitaire_stats)

    def _best_score(self, payload, key, fields):
        """Falling blocks, 2048 or Breakout over: kept if it's the best
        score yet. 2048 keeps its biggest tile whatever the score."""
        result = clean_best(payload, fields)
        if not result:
            return
        current = getattr(self, key)
        updated = dict(result) if result["score"] > current.get("score", 0) else dict(current)
        if key == "best_2048" and "tile" in result:
            updated["tile"] = max(result["tile"], current.get("tile", 0))
        if updated != current:
            setattr(self, key, updated)
            self.settings[key] = dict(updated)
            self.settings.save()
        self.emit(key, updated)

    def _sudoku_result(self, payload):
        """A puzzle solved: one more solved, and the time if it's the best."""
        level, seconds = payload.get("level"), clean_seconds(payload.get("seconds"))
        if level not in SUDOKU_LEVELS or seconds is None:
            return
        self.achieve("games_won", 1)
        entry = self.sudoku_stats.get(level, {"won": 0, "best": 0})
        self.sudoku_stats[level] = {"won": min(MAX_COUNT, entry["won"] + 1),
                                    "best": seconds if not entry["best"] or seconds < entry["best"] else entry["best"]}
        self.settings["sudoku_stats"] = {k: dict(v) for k, v in self.sudoku_stats.items()}
        self.settings.save()
        self.emit("sudoku_stats", self.sudoku_stats)

    def on_share_words(self, payload):
        """Into Buddy Network's message box, to send from the room that's
        open (or not) - and on the clipboard, for anywhere else."""
        text = words_share_text(payload or {})
        if text is None:
            return
        QGuiApplication.clipboard().setText(text)
        network = getattr(self.host, "pages", {}).get("buddy_network")
        if network is None or not hasattr(network, "share_text"):
            self.emit("toast", {"text": "Copied – paste it wherever you like."})
            return
        self.host.switch_tool("buddy_network")
        network.share_text(text)

    def _words_result(self, payload):
        """A word found or not: one more played (and won, with how many
        guesses it took), the streak on or broken. A day's daily word counts
        once; its streak goes on only from the day before."""
        mode, won, taken = payload.get("mode"), payload.get("won"), payload.get("guesses")
        if mode not in WORD_MODES or not isinstance(won, bool):
            return
        if won and (isinstance(taken, bool) or not isinstance(taken, int) or not 1 <= taken <= WORD_ROWS):
            return
        entry = self.words_stats.get(mode, {"played": 0, "won": 0, "streak": 0, "best": 0, "dist": [0] * WORD_ROWS})
        entry = {**entry, "dist": list(entry["dist"])}
        if mode == "daily":
            day = clean_day(payload.get("day"))
            if day is None:
                return
            last = entry.get("last")
            if last is not None and day <= last:
                return self.emit("words_stats", self.words_stats)      # that day is already counted
            follows = last == (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()
            entry["streak"] = (entry["streak"] + 1 if follows else 1) if won else 0
            entry["last"] = day
        else:
            entry["streak"] = entry["streak"] + 1 if won else 0
        entry["played"] = min(MAX_COUNT, entry["played"] + 1)
        if won:
            entry["won"] = min(MAX_COUNT, entry["won"] + 1)
            entry["dist"][taken - 1] = min(MAX_COUNT, entry["dist"][taken - 1] + 1)
            self.achieve("games_won", 1)
        entry["streak"] = min(MAX_COUNT, entry["streak"])
        entry["best"] = max(entry["best"], entry["streak"])
        self.words_stats[mode] = entry
        self.settings["words_stats"] = {m: {**v, "dist": list(v["dist"])} for m, v in self.words_stats.items()}
        self.settings.save()
        self.emit("words_stats", self.words_stats)

    def _board_result(self, payload):
        """A game of Checkers or Chess against the computer over: one more
        won, lost or drawn at that difficulty."""
        game, level, outcome = payload.get("game"), payload.get("difficulty"), payload.get("outcome")
        if level not in DIFFICULTIES or outcome not in OUTCOMES:
            return
        entry = self.board_records[game][level]
        entry[outcome] = min(MAX_COUNT, entry[outcome] + 1)
        if outcome == "won":
            self.achieve("games_won", 1)
        self.settings[f"{game}_record"] = {lv: dict(v) for lv, v in self.board_records[game].items()}
        self.settings.save()
        self.emit(f"{game}_record", self.board_records[game])
