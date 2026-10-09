"""The Web tab's history (pages/web/history_log.py): visits kept in order,
the same page again soon one visit, searched newest first, removed and
cleared by time, 90 days and 20,000 at the most, and a damaged file
starting afresh."""

import json
import os
import time
import shutil
import tempfile
import unittest

import _paths  # noqa: F401
from pages.web import history_log as h

DAY = 86400


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="buddy_history_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "default.json")

    def test_only_the_webs_own_pages(self):
        log = h.History(self.path)
        for url in ("about:blank", "", "file:///C:/a.html", "data:text/html,x", None, "https://x/" + "a" * 3000):
            self.assertIsNone(log.add(url, "t", now=1000))
        self.assertIsNotNone(log.add("https://example.com/", "Example", now=1000))
        self.assertEqual(len(log.entries), 1)

    def test_the_same_page_again_soon_is_one_visit(self):
        log = h.History(self.path)
        log.add("https://a.com/", "", now=1000)
        log.add("https://b.com/", "B", now=1100)
        log.add("https://a.com/", "A", now=1200)                 # reloaded: moved to the top, titled
        self.assertEqual([(e["url"], e["title"], e["t"]) for e in log.entries],
                         [("https://b.com/", "B", 1100), ("https://a.com/", "A", 1200)])
        log.add("https://a.com/", "", now=1200 + h.MERGE_S + 1)   # much later: a visit of its own
        self.assertEqual(len(log.entries), 3)
        log.retitle("https://a.com/", "A again")
        self.assertEqual(log.entries[-1]["title"], "A again")
        self.assertEqual(log.entries[1]["title"], "A")

    def test_search_is_newest_first_with_every_word(self):
        log = h.History(self.path)
        for i in range(10):
            log.add(f"https://site{i}.com/page", f"Colour grading {i}" if i % 2 else f"Audio {i}", now=1000 + i * 1000)
        found, more = log.search("grading colour")
        self.assertEqual([e["title"] for e in found], ["Colour grading 9", "Colour grading 7", "Colour grading 5",
                                                       "Colour grading 3", "Colour grading 1"])
        self.assertFalse(more)
        found, more = log.search("site", limit=4)
        self.assertEqual(len(found), 4)
        self.assertTrue(more)
        self.assertEqual(log.search("SITE3.COM")[0][0]["url"], "https://site3.com/page")

    def test_removed_and_cleared_by_time(self):
        log = h.History(self.path)
        now = time.time()
        for age in (10 * DAY, 2 * DAY, 2 * 3600, 30):                 # oldest first, as they happen
            log.add(f"https://{age}.com/", "", now=now - age)
        first = log.entries[0]["id"]
        log.remove([first, 999])
        self.assertEqual(len(log.entries), 3)
        log.clear("hour", now=now)
        self.assertEqual([e["url"] for e in log.entries], ["https://172800.com/", "https://7200.com/"])
        log.clear("week", now=now)
        self.assertEqual(log.entries, [])
        log.add("https://x.com/", "", now=now)
        log.clear("forever", now=now)                               # not a range: nothing
        self.assertEqual(len(log.entries), 1)
        log.clear("all")
        self.assertEqual(log.entries, [])

    def test_saved_trimmed_and_read_back(self):
        log = h.History(self.path)
        now = time.time()
        log.add("https://old.com/", "Old", now=now - 91 * DAY)
        log.add("https://new.com/", "New", now=now - DAY)
        log.save()
        self.assertFalse(log.dirty)
        with open(self.path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual([e["url"] for e in raw], ["https://new.com/"])  # past 90 days: gone
        again = h.History(self.path)
        self.assertEqual([e["title"] for e in again.entries], ["New"])
        self.assertGreater(again.add("https://next.com/", "", now=now)["id"], again.entries[0]["id"])
        again.delete_file()
        self.assertFalse(os.path.exists(self.path))

    def test_bad_entries_are_dropped_and_a_damaged_file_starts_afresh(self):
        now = 1000 * DAY
        raw = [{"id": 1, "url": "https://ok.com/", "title": "OK", "t": now}, {"id": 2, "url": "ftp://x/", "t": now},
               {"id": True, "url": "https://x.com/", "t": now}, {"id": 3, "url": "https://y.com/", "t": "noon"},
               "junk", {"id": 4, "url": "https://z.com/", "title": 5, "t": now - 1}]
        cleaned = h.clean(raw, now=now)
        self.assertEqual([(e["id"], e["title"]) for e in cleaned], [(4, ""), (1, "OK")])   # oldest first
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertEqual(h.History(self.path).entries, [])

    def test_cleared_history_cannot_return_from_the_backup(self):
        log = h.History(self.path)
        log.add("https://private.example/", "Private")
        log.save()
        log.clear("all")
        log.save()
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("broken")
        self.assertEqual(h.History(self.path).entries, [])
        self.assertFalse(os.path.exists(self.path + ".bak"))

    def test_removed_visits_cannot_return_but_other_visits_are_kept(self):
        log = h.History(self.path)
        removed = log.add("https://private.example/", "Private")
        log.add("https://keep.example/", "Keep")
        log.save()
        log.remove([removed["id"]])
        log.save()
        self.assertEqual([e["url"] for e in h.History(self.path).entries], ["https://keep.example/"])
        self.assertFalse(os.path.exists(self.path + ".bak"))

    def test_deleting_a_profile_removes_its_backup_and_recovered_copies(self):
        log = h.History(self.path)
        log.add("https://private.example/", "Private")
        log.save()
        log.add("https://other.example/", "Other")
        log.save()
        damaged = self.path + ".corrupt-1"
        shutil.copy2(self.path, damaged)
        log.delete_file()
        self.assertFalse(os.path.exists(self.path + ".bak"))
        self.assertFalse(os.path.exists(damaged))
        self.assertEqual(h.History(self.path).entries, [])

    def test_at_most_max_entries(self):
        log = h.History(self.path)
        with unittest.mock.patch.object(h, "MAX_ENTRIES", 50):
            start = time.time() - 1000
            for i in range(700):
                log.add(f"https://s{i}.com/", "", now=start + i)
            log.save()
            self.assertEqual(len(h.History(self.path).entries), 50)
            self.assertEqual(h.History(self.path).entries[-1]["url"], "https://s699.com/")


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
