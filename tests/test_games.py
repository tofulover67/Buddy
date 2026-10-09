"""Games: a main tab under its own heading, with Pong on its first sub-tab.
The game itself runs in the view (pages/games/web/pong.js); the page keeps
its settings and the record against the computer."""

import ast
import os
import unittest

import _paths
from pages.games import page as page_mod

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def registry_page(category):
    """The page class REGISTRY names under this heading, and whether it runs
    on a Mac too, read from registry.py rather than imported: importing it
    pulls in every page, some needing packages the release machines don't
    install (numpy). The Windows registry names the class; the Mac one
    wraps it in _tool(tool_id, name, category, module, class, mac=...)."""
    tree = ast.parse((_paths.APP / "registry.py").read_text(encoding="utf-8"))
    entries = next(node.value.elts for node in tree.body if isinstance(node, ast.Assign)
                   and any(getattr(t, "id", "") == "REGISTRY" for t in node.targets))
    for entry in entries:
        heading, page = entry.elts
        if heading.value != category:
            continue
        if isinstance(page, ast.Name):
            return page.id, True
        mac = next((k.value.value for k in page.keywords if k.arg == "mac"), False)
        return page.args[4].value, mac
    return None, False


class GamesTabTests(unittest.TestCase):
    def test_games_is_a_main_tab_with_its_own_heading(self):
        self.assertEqual(registry_page("Games"), ("GamesPage", True))
        self.assertFalse(page_mod.GamesPage.is_placeholder)

    def test_ask_buddy_knows_it(self):
        from core.tools_kb import get_tool
        info = get_tool("games", [("Games", page_mod.GamesPage)])
        self.assertIsNotNone(info)
        self.assertTrue(info.is_available)
        self.assertFalse(info.needs_resolve)

    def test_a_record_is_counts_per_difficulty(self):
        self.assertEqual(page_mod.clean_record(None),
                         {lv: {"won": 0, "lost": 0} for lv in ("easy", "medium", "hard")})
        self.assertEqual(page_mod.clean_record({"hard": {"won": 3, "lost": -1}, "easy": {"won": True, "lost": "2"},
                                                "silly": {"won": 9}})["hard"], {"won": 3, "lost": 0})
        self.assertEqual(page_mod.clean_record({"easy": {"won": True, "lost": "2"}})["easy"], {"won": 0, "lost": 0})


class Mem(dict):
    def save(self):
        pass


class Host:
    def __init__(self, saved=None):
        self.tools = {"games": Mem(saved)} if saved is not None else {}

    def tool_settings(self, tool_id, defaults=None):
        return self.tools.setdefault(tool_id, Mem(defaults or {}))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])

    def make(self, saved=None):
        self.host = Host(saved)
        page = page_mod.GamesPage(self.host)
        self.addCleanup(page.deleteLater)
        self.events = []
        page.emit = lambda name, payload=None: self.events.append((name, payload))
        return page

    def last(self, name):
        found = [p for n, p in self.events if n == name]
        return found[-1] if found else None

    def test_it_opens_as_it_was_left(self):
        page = self.make({"tab": "pong", "prefs": {"pong_mode": "two", "pong_mouse": True},
                          "pong_record": {"medium": {"won": 2, "lost": 5}}})
        page.web_ready()
        state = self.last("state")
        self.assertEqual(state["tab"], "pong")
        self.assertEqual(state["prefs"], {"pong_mode": "two", "pong_mouse": True})
        self.assertEqual(state["record"]["medium"], {"won": 2, "lost": 5})

    def test_mouse_control_is_off_until_ticked(self):
        page = self.make()
        page.web_ready()
        self.assertNotIn("pong_mouse", self.last("state")["prefs"])   # the view reads a missing tick as off

    def test_settings_are_saved(self):
        page = self.make()
        page.on_pref({"key": "pong_level", "value": "hard"})
        page.on_pref({"key": "", "value": "x"})
        page.on_pref({"key": "pong_mouse", "value": True})
        page.on_tab({"tab": "go"})                         # no such game
        settings = self.host.tools["games"]
        self.assertEqual(settings["prefs"], {"pong_level": "hard", "pong_mouse": True})
        self.assertEqual(settings["tab"], "pong")

    def test_a_finished_game_against_the_computer_is_counted(self):
        page = self.make()
        page.on_result({"game": "pong", "difficulty": "hard", "won": True})
        page.on_result({"game": "pong", "difficulty": "hard", "won": False})
        page.on_result({"game": "pong", "difficulty": "hard", "won": False})
        for junk in ({"game": "pong", "difficulty": "impossible", "won": True},
                     {"game": "pong", "difficulty": "easy", "won": "yes"}, {"game": "chess", "difficulty": "easy", "won": True}, None):
            page.on_result(junk)
        self.assertEqual(self.host.tools["games"]["pong_record"]["hard"], {"won": 1, "lost": 2})
        self.assertEqual(self.host.tools["games"]["pong_record"]["easy"], {"won": 0, "lost": 0})
        self.assertEqual(self.last("record")["hard"], {"won": 1, "lost": 2})

    def test_snake_keeps_the_best_score_for_each_setting(self):
        page = self.make()
        page.on_result({"game": "snake", "speed": "normal", "walls": True, "score": 12})
        page.on_result({"game": "snake", "speed": "normal", "walls": True, "score": 7})     # not a best: kept at 12
        page.on_result({"game": "snake", "speed": "fast", "walls": False, "score": 3})
        for junk in ({"game": "snake", "speed": "ludicrous", "walls": True, "score": 5},
                     {"game": "snake", "speed": "slow", "walls": "yes", "score": 5},
                     {"game": "snake", "speed": "slow", "walls": True, "score": 0},
                     {"game": "snake", "speed": "slow", "walls": True, "score": 10_000},
                     {"game": "snake", "speed": "slow", "walls": True, "score": True}):
            page.on_result(junk)
        self.assertEqual(self.host.tools["games"]["snake_best"], {"normal_walls": 12, "fast_wrap": 3})
        self.assertEqual(self.last("snake_best"), {"normal_walls": 12, "fast_wrap": 3})
        page = self.make({"snake_best": {"normal_walls": 12, "slow_wrap": -4, "nonsense": 9}, "tab": "snake"})
        page.web_ready()
        self.assertEqual(self.last("state")["snake_best"], {"normal_walls": 12})
        self.assertEqual(self.last("state")["tab"], "snake")

    def test_minesweeper_keeps_the_best_time_for_each_difficulty(self):
        page = self.make()
        page.on_result({"game": "mines", "level": "beginner", "seconds": 41.26})
        page.on_result({"game": "mines", "level": "beginner", "seconds": 55})       # slower: kept at 41.3
        page.on_result({"game": "mines", "level": "beginner", "seconds": 38.04})
        page.on_result({"game": "mines", "level": "expert", "seconds": 412})
        for junk in ({"game": "mines", "level": "custom", "seconds": 5},
                     {"game": "mines", "level": "beginner", "seconds": 0},
                     {"game": "mines", "level": "beginner", "seconds": -3},
                     {"game": "mines", "level": "beginner", "seconds": True},
                     {"game": "mines", "level": "beginner", "seconds": "1"}):
            page.on_result(junk)
        self.assertEqual(self.host.tools["games"]["mines_best"], {"beginner": 38.0, "expert": 412.0})
        self.assertEqual(self.last("mines_best"), {"beginner": 38.0, "expert": 412.0})
        page = self.make({"mines_best": {"intermediate": 99.95, "expert": 0, "silly": 3}, "tab": "mines"})
        page.web_ready()
        self.assertEqual(self.last("state")["mines_best"], {"intermediate": 100.0})
        self.assertEqual(self.last("state")["tab"], "mines")

    def test_solitaire_counts_wins_and_keeps_the_best_time_for_each_deal(self):
        page = self.make()
        page.on_result({"game": "solitaire", "draw": 1, "seconds": 300.04})
        page.on_result({"game": "solitaire", "draw": 1, "seconds": 410})       # a win, not a best
        page.on_result({"game": "solitaire", "draw": 3, "seconds": 520})
        for junk in ({"game": "solitaire", "draw": 2, "seconds": 100},
                     {"game": "solitaire", "draw": True, "seconds": 100},
                     {"game": "solitaire", "draw": 1, "seconds": 0},
                     {"game": "solitaire", "draw": 1, "seconds": "fast"}):
            page.on_result(junk)
        expected = {"draw1": {"won": 2, "best": 300.0}, "draw3": {"won": 1, "best": 520.0}}
        self.assertEqual(self.host.tools["games"]["solitaire_stats"], expected)
        self.assertEqual(self.last("solitaire_stats"), expected)
        page = self.make({"solitaire_stats": {"draw1": {"won": 4, "best": 199.96}, "draw3": {"won": 0, "best": 50},
                                              "draw9": {"won": 2}}, "tab": "solitaire"})
        page.web_ready()
        self.assertEqual(self.last("state")["solitaire_stats"], {"draw1": {"won": 4, "best": 200.0}})
        self.assertEqual(self.last("state")["tab"], "solitaire")

    def test_falling_blocks_and_breakout_keep_the_best_score(self):
        page = self.make()
        page.on_result({"game": "blocks", "score": 4200, "lines": 31, "level": 4})
        page.on_result({"game": "blocks", "score": 900, "lines": 8, "level": 1})           # not a best
        page.on_result({"game": "breakout", "score": 1530, "level": 3})
        for junk in ({"game": "blocks", "score": 0, "lines": 3}, {"game": "blocks", "score": -5},
                     {"game": "blocks", "score": True}, {"game": "blocks", "score": "9000"},
                     {"game": "breakout", "score": 10 ** 12}, {"game": "tetris", "score": 50}):
            page.on_result(junk)
        saved = self.host.tools["games"]
        self.assertEqual(saved["blocks_best"], {"score": 4200, "lines": 31, "level": 4})
        self.assertEqual(saved["breakout_best"], {"score": 1530, "level": 3})
        self.assertEqual(self.last("blocks_best"), {"score": 4200, "lines": 31, "level": 4})
        page.on_result({"game": "blocks", "score": 5000, "lines": 40, "level": 500})       # a level that can't be: dropped
        self.assertEqual(saved["blocks_best"], {"score": 5000, "lines": 40})

    def test_2048_keeps_the_best_score_and_the_biggest_tile_apart(self):
        page = self.make()
        page.on_result({"game": "2048", "score": 20000, "tile": 1024})
        page.on_result({"game": "2048", "score": 9000, "tile": 2048})     # a lower score, a bigger tile
        page.on_result({"game": "2048", "score": 50, "tile": 3})          # no such tile: the score alone
        self.assertEqual(self.host.tools["games"]["best_2048"], {"score": 20000, "tile": 2048})
        self.assertEqual(self.last("best_2048"), {"score": 20000, "tile": 2048})

    def test_sudoku_counts_puzzles_solved_and_keeps_the_best_time_for_each_difficulty(self):
        page = self.make()
        page.on_result({"game": "sudoku", "level": "easy", "seconds": 312.44})
        page.on_result({"game": "sudoku", "level": "easy", "seconds": 400})
        page.on_result({"game": "sudoku", "level": "hard", "seconds": 1500})
        for junk in ({"game": "sudoku", "level": "evil", "seconds": 100}, {"game": "sudoku", "level": "easy", "seconds": 0},
                     {"game": "sudoku", "level": "easy", "seconds": "5"}, {"game": "sudoku", "level": True, "seconds": 9}):
            page.on_result(junk)
        expected = {"easy": {"won": 2, "best": 312.4}, "hard": {"won": 1, "best": 1500.0}}
        self.assertEqual(self.host.tools["games"]["sudoku_stats"], expected)
        self.assertEqual(self.last("sudoku_stats"), expected)

    def test_the_new_games_open_as_they_were_left(self):
        page = self.make({"tab": "2048", "blocks_best": {"score": 800, "lines": 6, "level": 2},
                          "best_2048": {"score": -3, "tile": 64}, "breakout_best": {"score": 70, "level": "two"},
                          "sudoku_stats": {"medium": {"won": 3, "best": 610}, "expert": {"won": 9}}})
        page.web_ready()
        state = self.last("state")
        self.assertEqual(state["tab"], "2048")
        self.assertEqual(state["blocks_best"], {"score": 800, "lines": 6, "level": 2})
        self.assertEqual(state["best_2048"], {})                           # no score, nothing kept
        self.assertEqual(state["breakout_best"], {"score": 70})
        self.assertEqual(state["sudoku_stats"], {"medium": {"won": 3, "best": 610.0}})
        for tab in ("blocks", "breakout", "sudoku"):
            page.on_tab({"tab": tab})
            self.assertEqual(self.host.tools["games"]["tab"], tab)

    def test_checkers_and_chess_count_won_lost_and_drawn_for_each_difficulty(self):
        page = self.make()
        for outcome in ("won", "won", "lost", "drawn"):
            page.on_result({"game": "chess", "difficulty": "hard", "outcome": outcome})
        page.on_result({"game": "checkers", "difficulty": "easy", "outcome": "lost"})
        for junk in ({"game": "chess", "difficulty": "grandmaster", "outcome": "won"},
                     {"game": "chess", "difficulty": "easy", "outcome": "resigned"},
                     {"game": "checkers", "difficulty": "easy", "outcome": True}, {"game": "go", "difficulty": "easy", "outcome": "won"}):
            page.on_result(junk)
        nothing = {"won": 0, "lost": 0, "drawn": 0}
        chess = {"easy": nothing, "medium": nothing, "hard": {"won": 2, "lost": 1, "drawn": 1}}
        checkers = {"easy": {"won": 0, "lost": 1, "drawn": 0}, "medium": nothing, "hard": nothing}
        self.assertEqual(self.host.tools["games"]["chess_record"], chess)
        self.assertEqual(self.host.tools["games"]["checkers_record"], checkers)
        self.assertEqual(self.last("chess_record"), chess)
        self.assertEqual(self.last("checkers_record"), checkers)

    def test_checkers_and_chess_open_as_they_were_left(self):
        page = self.make({"tab": "chess", "chess_record": {"easy": {"won": 4, "lost": -1, "drawn": "x"}},
                          "checkers_record": "broken"})
        page.web_ready()
        state = self.last("state")
        self.assertEqual(state["tab"], "chess")
        self.assertEqual(state["chess_record"]["easy"], {"won": 4, "lost": 0, "drawn": 0})
        self.assertEqual(state["checkers_record"]["hard"], {"won": 0, "lost": 0, "drawn": 0})
        page.on_tab({"tab": "checkers"})
        self.assertEqual(self.host.tools["games"]["tab"], "checkers")

    def test_word_guess_counts_words_and_streaks_daily_and_unlimited_apart(self):
        page = self.make()
        page.on_result({"game": "words", "mode": "daily", "won": True, "guesses": 4, "day": "2026-10-04"})
        page.on_result({"game": "words", "mode": "daily", "won": True, "guesses": 3, "day": "2026-10-05"})
        page.on_result({"game": "words", "mode": "daily", "won": True, "guesses": 2, "day": "2026-10-05"})    # counted already
        page.on_result({"game": "words", "mode": "daily", "won": False, "guesses": 6, "day": "2026-10-03"})   # before the last
        daily = self.host.tools["games"]["words_stats"]["daily"]
        self.assertEqual(daily, {"played": 2, "won": 2, "streak": 2, "best": 2, "dist": [0, 0, 1, 1, 0, 0],
                                 "last": "2026-10-05"})
        # A day missed: the streak starts again; a word lost ends it.
        page.on_result({"game": "words", "mode": "daily", "won": True, "guesses": 5, "day": "2026-10-07"})
        self.assertEqual(self.host.tools["games"]["words_stats"]["daily"]["streak"], 1)
        page.on_result({"game": "words", "mode": "daily", "won": False, "guesses": 6, "day": "2026-10-08"})
        daily = self.host.tools["games"]["words_stats"]["daily"]
        self.assertEqual((daily["played"], daily["won"], daily["streak"], daily["best"]), (4, 3, 0, 2))
        for won in (True, True, False, True):
            page.on_result({"game": "words", "mode": "unlimited", "won": won, "guesses": 2 if won else 6})
        free = self.host.tools["games"]["words_stats"]["unlimited"]
        self.assertEqual(free, {"played": 4, "won": 3, "streak": 1, "best": 2, "dist": [0, 3, 0, 0, 0, 0]})
        self.assertNotIn("last", free)
        for junk in ({"game": "words", "mode": "weekly", "won": True, "guesses": 3},
                     {"game": "words", "mode": "unlimited", "won": True, "guesses": 7},
                     {"game": "words", "mode": "unlimited", "won": True, "guesses": True},
                     {"game": "words", "mode": "unlimited", "won": "yes", "guesses": 3},
                     {"game": "words", "mode": "daily", "won": True, "guesses": 3, "day": "2026-13-40"},
                     {"game": "words", "mode": "daily", "won": True, "guesses": 3}):
            page.on_result(junk)
        self.assertEqual(self.host.tools["games"]["words_stats"]["unlimited"], free)
        self.assertEqual(self.host.tools["games"]["words_stats"]["daily"], daily)
        self.assertEqual(self.last("words_stats")["unlimited"], free)

    def test_word_guess_opens_as_it_was_left(self):
        page = self.make({"tab": "words", "words_stats": {
            "daily": {"played": 9, "won": 12, "streak": 3, "best": "x", "dist": [1, 2, -1], "last": "2026-10-05"},
            "unlimited": {"played": 0, "won": 0}, "hourly": {"played": 4}}})
        page.web_ready()
        state = self.last("state")
        self.assertEqual(state["tab"], "words")
        self.assertEqual(state["words_stats"], {"daily": {"played": 9, "won": 9, "streak": 3, "best": 0,
                                                          "dist": [1, 2, 0, 0, 0, 0], "last": "2026-10-05"}})


class WordListTests(unittest.TestCase):
    """web/words_list.js: the answers (common words) and every word a guess
    may be - five small letters each, every answer a guess too."""

    def lists(self):
        text = (_paths.APP / "pages" / "games" / "web" / "words_list.js").read_text(encoding="utf-8")
        found = {}
        for name in ("WORD_ANSWERS", "WORD_GUESSES"):
            start = text.index(f"const {name} = \"") + len(f"const {name} = \"")
            found[name] = text[start:text.index("\"", start)].split(" ")
        return found["WORD_ANSWERS"], found["WORD_GUESSES"]

    def test_the_lists_hold_five_letter_words(self):
        answers, guesses = self.lists()
        self.assertGreater(len(answers), 1000)
        self.assertGreater(len(guesses), 5000)
        for word in answers + guesses:
            self.assertRegex(word, r"^[a-z]{5}$")
        self.assertEqual(len(set(answers)), len(answers))
        self.assertEqual(len(set(guesses)), len(guesses))
        self.assertFalse(set(answers) & set(guesses))       # answers are guesses already (words.js joins them)
        self.assertEqual(answers, sorted(answers))          # the daily order depends on this order


class WordShareTests(unittest.TestCase):
    """A finished Wordin' as squares for Buddy Network - never its letters."""

    WON = [["absent", "present", "absent", "absent", "correct"],
           ["correct", "correct", "absent", "present", "correct"],
           ["correct"] * 5]

    def test_squares_and_the_daily_number_without_the_word(self):
        text = page_mod.words_share_text({"mode": "daily", "day": "2026-10-08", "won": True, "hard": True,
                                          "marks": self.WON, "answer": "crane", "guesses": ["slate"]})
        self.assertEqual(text.split("\n"), ["Wordin' #281 3/6*", "", "⬛🟨⬛⬛🟩", "🟩🟩⬛🟨🟩", "🟩" * 5])
        self.assertFalse(any(c.isalpha() for c in text.split("\n", 1)[1]))      # the squares: no letter
        lost = page_mod.words_share_text({"mode": "unlimited", "won": False, "contrast": True,
                                          "marks": [["absent", "present", "correct", "absent", "absent"]] * 6})
        self.assertTrue(lost.startswith("Wordin' (unlimited) X/6\n\n"))
        self.assertIn("🟧", lost)                    # high contrast: orange and blue
        self.assertIn("🟦", lost)

    def test_anything_but_a_finished_game_shares_nothing(self):
        for junk in ({"mode": "daily", "day": "2026-10-08", "won": True, "marks": self.WON[:2]},     # not solved
                     {"mode": "daily", "won": True, "marks": self.WON},                               # no day
                     {"mode": "unlimited", "won": False, "marks": self.WON[:1]},                      # not over
                     {"mode": "unlimited", "won": True, "marks": [["correct"] * 4]},
                     {"mode": "unlimited", "won": True, "marks": [["c", "r", "a", "n", "e"]]},
                     {"mode": "unlimited", "won": True, "marks": [["correct"] * 5] * 2},               # won twice
                     {"mode": "hourly", "won": True, "marks": self.WON[2:]}):
            self.assertIsNone(page_mod.words_share_text(junk), junk)


if __name__ == "__main__":
    unittest.main()
