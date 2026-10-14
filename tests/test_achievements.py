"""Achievements (core/achievements.py): counts and peaks reaching goals,
tiers and points, the derived numbers (days used, achievements unlocked -
which can unlock more), what the page is shown, the file round trip and a
damaged file - and the Settings page (pages/achievements/page.py) with an
in-memory host. No Resolve."""

import datetime
import json
import os
import re
import tempfile
import unittest

import _paths  # noqa: F401
from core import achievements as ach_mod
from core.achievements import ACHIEVEMENTS, BY_ID, GROUPS, STATS, Achievements, level_for, time_text

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from pages.achievements import page as page_mod
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class _Temp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "achievements.json")

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, **kw):
        return Achievements(self.path, **kw)


class DefinitionTests(unittest.TestCase):
    def test_every_achievement_is_well_formed(self):
        ids = [a["id"] for a in ACHIEVEMENTS]
        self.assertEqual(len(ids), len(set(ids)))
        for a in ACHIEVEMENTS:
            with self.subTest(a["id"]):
                self.assertIn(a["stat"], STATS)
                self.assertIn(a["group"], GROUPS)
                self.assertGreater(a["goal"], 0)
                self.assertIn(a["points"], ach_mod.TIER_POINTS)
                self.assertTrue(a["title"] and a["text"])
                self.assertNotIn("{n}", a["text"])             # the goal is filled in

    def test_tiers_of_a_family_climb(self):
        by_stat = {}
        for a in ACHIEVEMENTS:
            by_stat.setdefault(a["stat"], []).append(a)
        for stat, tiers in by_stat.items():
            goals = [t["goal"] for t in tiers]
            self.assertEqual(goals, sorted(goals), stat)
            points = [t["points"] for t in tiers]
            self.assertEqual(points, sorted(points), stat)

    def test_the_singular_wording_only_at_one(self):
        self.assertEqual(BY_ID["proxy_1"]["text"], "Render a proxy with Project Setup")
        self.assertEqual(BY_ID["proxy_100"]["text"], "Render 100 proxies with Project Setup")
        self.assertEqual(BY_ID["proxy_1000"]["text"], "Render 1,000 proxies with Project Setup")
        self.assertEqual(BY_ID["render_long"]["text"], "Finish a render that takes 30 minutes or longer")
        self.assertEqual(BY_ID["render_hours_10"]["text"], "Spend 10 hours rendering, all together")

    def test_every_title_and_text_is_translated(self):
        from core.i18n import TRANSLATIONS
        for a in ACHIEVEMENTS:
            with self.subTest(a["id"]):
                self.assertIn(a["title"], TRANSLATIONS)
                self.assertIn(re.sub(r"[\d,]+", "{n}", a["text"], count=1), TRANSLATIONS)

    def test_levels_and_time(self):
        self.assertEqual(level_for(0), 1)
        self.assertEqual(level_for(99), 1)
        self.assertEqual(level_for(100), 2)
        self.assertEqual(level_for(1100), 12)
        self.assertEqual(time_text(30), "30 s")
        self.assertEqual(time_text(90), "1 min")
        self.assertEqual(time_text(3600), "1 h")
        self.assertEqual(time_text(7800), "2 h 10 min")


class RecordingTests(_Temp):
    def test_a_count_reaching_its_goal_unlocks(self):
        a = self.make()
        got = []
        a.on_unlock.append(got.extend)
        self.assertEqual(a.record("proxies", 1), [BY_ID["proxy_1"]])
        self.assertEqual([x["id"] for x in got], ["proxy_1"])
        self.assertEqual(a.record("proxies", 98), [])
        self.assertEqual([x["id"] for x in a.record("proxies", 1)], ["proxy_100"])
        self.assertEqual(a.value("proxies"), 100)
        self.assertEqual(a.points(), 10 + 25)
        self.assertEqual(a.level(), 1)

    def test_junk_is_ignored(self):
        a = self.make()
        for stat, amount in (("nothing", 1), ("proxies", 0), ("proxies", -3), ("proxies", True),
                             ("proxies", "5"), ("render_longest", 1), ("days_used", 1), ("achievements", 1)):
            self.assertEqual(a.record(stat, amount), [], (stat, amount))
        self.assertEqual(a.stats, {})
        self.assertEqual(a.peak("proxies", 5), [])                 # a count isn't a peak
        self.assertEqual(a.peak("render_longest", -1), [])
        self.assertEqual(a.record("proxies", 10 ** 12), [BY_ID["proxy_1"], BY_ID["proxy_100"], BY_ID["proxy_1000"]])
        self.assertEqual(a.value("proxies"), ach_mod.MAX_VALUE)

    def test_a_peak_keeps_the_largest(self):
        a = self.make()
        self.assertEqual(a.peak("render_longest", 600), [])
        self.assertEqual(a.peak("render_longest", 300), [])
        self.assertEqual(a.value("render_longest"), 600)
        self.assertEqual([x["id"] for x in a.peak("render_longest", 1800)], ["render_long"])
        self.assertEqual(a.peak("render_longest", 1800), [])        # not larger: nothing
        self.assertEqual([x["id"] for x in a.peak("render_longest", 3 * 3600)], ["render_overnight"])

    def test_unlocking_counts_towards_the_collector(self):
        a = self.make()
        for stat in ("proxies", "renders", "graded", "presets", "transcripts", "automations", "games_won"):
            a.record(stat, 1)
        self.assertEqual(a.value("achievements"), 7)
        a.record("audits", 10)
        a.record("zooms", 25)
        self.assertEqual(a.value("achievements"), 9)
        unlocked = a.record("stills", 50)                            # the 10th - and so the Collector too
        self.assertEqual([x["id"] for x in unlocked], ["stills_50", "coll_10"])
        self.assertEqual(a.value("achievements"), 11)

    def test_days_used(self):
        day = [datetime.date(2026, 10, 1)]
        a = self.make(today=lambda: day[0])
        self.assertEqual(a.touch_day(), [])
        self.assertEqual(a.touch_day(), [])                           # the same day again: nothing
        self.assertEqual(a.value("days_used"), 1)
        for i in range(2, 8):
            day[0] = datetime.date(2026, 10, i)
            unlocked = a.touch_day()
        self.assertEqual([x["id"] for x in unlocked], ["days_7"])
        self.assertEqual(a.value("days_used"), 7)

    def test_seen_and_new(self):
        a = self.make()
        a.record("proxies", 1)
        a.record("games_won", 1)
        self.assertEqual(a.summary()["new"], 2)
        self.assertTrue(all(p["new"] for p in a.progress() if p["done"]))
        a.mark_seen()
        self.assertEqual(a.summary()["new"], 0)
        a.record("renders", 1)
        self.assertEqual([p["id"] for p in a.progress() if p["new"]], ["render_1"])

    def test_progress_and_summary(self):
        a = self.make()
        a.record("proxies", 37)
        a.peak("render_longest", 600)
        rows = {p["id"]: p for p in a.progress()}
        self.assertEqual(rows["proxy_100"]["value"], 37)
        self.assertEqual(rows["proxy_100"]["pct"], 37)
        self.assertEqual(rows["proxy_100"]["value_text"], "37")
        self.assertEqual(rows["proxy_1"]["pct"], 100)
        self.assertTrue(rows["proxy_1"]["done"] and rows["proxy_1"]["when"])
        self.assertEqual(rows["render_long"]["value_text"], "10 min")
        self.assertEqual(rows["render_long"]["goal_text"], "30 min")
        self.assertEqual(rows["render_overnight"]["goal_text"], "3 h")
        s = a.summary()
        self.assertEqual((s["unlocked"], s["total"], s["points"], s["level"]), (1, len(ACHIEVEMENTS), 10, 1))
        self.assertEqual(s["total_points"], ach_mod.TOTAL_POINTS)
        self.assertEqual([x["id"] for x, _when in a.recent()], ["proxy_1"])

    def test_reset(self):
        a = self.make()
        a.record("proxies", 5)
        a.note("render_jobs", {"j1": "Complete"})
        a.reset()
        self.assertEqual((a.stats, a.unlocked, a.memo, a.days), ({}, {}, {}, []))
        again = self.make()
        self.assertEqual(again.stats, {})

    def test_listeners_that_break_never_lose_the_record(self):
        a = self.make()

        def broken(*_args):
            raise RuntimeError("bug")

        a.on_unlock.append(broken)
        a.on_change.append(broken)
        self.assertEqual([x["id"] for x in a.record("proxies", 1)], ["proxy_1"])
        self.assertEqual(self.make().value("proxies"), 1)


class FileTests(_Temp):
    def test_round_trip(self):
        a = self.make()
        a.record("proxies", 3)
        a.peak("timeline_clips", 120)
        a.touch_day()
        a.note("graded", {"tl-1": 4})
        a.record("games_won", 1)
        a.mark_seen()
        b = self.make()
        self.assertEqual(b.stats, {"proxies": 3, "timeline_clips": 120, "games_won": 1})
        self.assertEqual(set(b.unlocked), {"proxy_1", "win_1"})
        self.assertEqual(b.seen, {"proxy_1", "win_1"})
        self.assertEqual(b.memo, {"graded": {"tl-1": 4}})
        self.assertEqual(len(b.days), 1)
        self.assertEqual(b.load_warnings, [])

    def test_odd_contents_are_dropped(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"stats": {"proxies": "9", "renders": -1, "nothing": 5, "graded": 2.9, "days_used": 4},
                       "unlocked": {"proxy_1": "not a time", "win_1": "2026-10-01T10:00:00", "ghost": "2026-10-01T10:00:00"},
                       "seen": ["ghost", "win_1", "proxy_1"], "days": ["2026-10-01", "junk", "2026-10-01"],
                       "memo": "no"}, f)
        a = self.make()
        self.assertEqual(a.stats, {"graded": 2})
        self.assertEqual(a.unlocked, {"win_1": "2026-10-01T10:00:00"})
        self.assertEqual(a.seen, {"win_1"})
        self.assertEqual(a.days, ["2026-10-01"])
        self.assertEqual(a.memo, {})

    def test_a_damaged_file_is_set_aside(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"stats": {"proxies": ')
        a = self.make()
        self.assertEqual(a.stats, {})
        self.assertEqual(len(a.load_warnings), 1)
        self.assertTrue(any(n.startswith("achievements.json.corrupt") for n in os.listdir(self._tmp.name)))
        a.record("proxies", 1)
        self.assertEqual(self.make().value("proxies"), 1)

    def test_a_damaged_file_with_a_backup_recovers(self):
        a = self.make()
        a.record("proxies", 1)
        a.record("proxies", 1)                                       # the second save makes the .bak
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{")
        b = self.make()
        self.assertEqual(b.value("proxies"), 1)
        self.assertEqual(len(b.load_warnings), 1)


class Mem(dict):
    def save(self):
        pass


if HAVE_QT:
    class Host(QObject):
        def __init__(self, path):
            super().__init__()
            self.achievements = Achievements(path)
            self.tools = {}

        def tool_settings(self, tool_id, defaults=None):
            return self.tools.setdefault(tool_id, Mem(defaults or {}))


class _UI:
    """Settings' ui for a page: every question answered yes, every status kept."""

    def __init__(self):
        self.statuses = []

    def confirm(self, _title, _text, ok="OK", danger=False):
        return True

    def status(self, text, tone=""):
        self.statuses.append(text)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(_Temp):
    def setUp(self):
        super().setUp()
        self.app = QApplication.instance() or QApplication([])

    def make_page(self):
        self.host = Host(self.path)
        return page_mod.AchievementsSettings(self.host)

    @staticmethod
    def field(page, kind):
        return next(f for f in page.settings_pages()[0]["fields"] if f["kind"] == kind)

    def test_it_is_a_settings_page_under_general_not_a_tab(self):
        import ast
        tree = ast.parse((_paths.APP / "registry.py").read_text(encoding="utf-8"))
        tool_ids = [node.args[0].value for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_tool"]
        self.assertNotIn("achievements", tool_ids)
        self.assertNotIn("pets", tool_ids)
        page = self.make_page()
        pages = page.settings_pages()
        self.assertEqual([(p["id"], p["group"]) for p in pages], [("achievements", "general")])

    def test_the_page_shows_every_badge_and_marks_the_new_ones_for_a_while(self):
        page = self.make_page()
        self.host.achievements.record("proxies", 1)
        badges = self.field(page, "achievements")
        self.assertEqual(len(badges["items"]), len(ACHIEVEMENTS))
        self.assertEqual(badges["summary"]["unlocked"], 1)
        first = next(i for i in badges["items"] if i["id"] == "proxy_1")
        self.assertTrue(first["done"] and first["new"])
        self.assertEqual(self.host.achievements.summary()["new"], 0)        # seen, once the page was built
        again = next(i for i in self.field(page, "achievements")["items"] if i["id"] == "proxy_1")
        self.assertTrue(again["new"])                                       # still marked while it's fresh
        page._fresh_at = 0.0                                                # long ago
        later = next(i for i in self.field(page, "achievements")["items"] if i["id"] == "proxy_1")
        self.assertFalse(later["new"])
        infos = {f["label"]: f["text"] for f in page.settings_pages()[0]["fields"] if f["kind"] == "info"}
        self.assertEqual(infos["Level"], "1")
        self.assertEqual(infos["Unlocked"], f"1 of {len(ACHIEVEMENTS)}")

    def test_settings_and_reset(self):
        page = self.make_page()
        self.host.achievements.record("games_won", 3)
        page.on_setting("notify", False, None)
        self.assertIs(self.host.tools["achievements"]["notify"], False)
        ui = _UI()
        page.on_settings_action("reset", ui)
        self.assertEqual(self.host.achievements.value("games_won"), 0)
        self.assertEqual(self.host.achievements.unlocked, {})
        self.assertEqual(ui.statuses, ["Achievements reset."])


if __name__ == "__main__":
    unittest.main()
