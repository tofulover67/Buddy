"""Animation > Titles: placing a title (titles/titles_resolve.py) - its id, which clips
are in the way, the track it picks - against a fake timeline. Never over anyone else's
clip; Buddy's own earlier title in the spot is the one thing replaced."""

import unittest

import _paths  # noqa: F401
from pages.text_animator.titles import titles_resolve as tr


class Item:
    def __init__(self, name, start, end, ours=False):
        self.name, self.start, self.end, self.ours = name, start, end, ours

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end


class Timeline:
    def __init__(self, tracks):
        self.tracks = tracks

    def GetTrackCount(self, kind):
        return len(self.tracks)

    def GetItemListInTrack(self, kind, track):
        return self.tracks[track - 1]


def ours(item):
    return item.ours


class PlaceTests(unittest.TestCase):
    def test_the_id_is_the_exact_title(self):
        doc = {"text": "3 secrets", "look": "hormozi"}
        a = tr.title_id(doc, (1920, 1080), 24)
        self.assertEqual(a, tr.title_id(dict(doc), (1920, 1080), 24.0))
        self.assertTrue(a.startswith("bt-"))
        self.assertNotEqual(a, tr.title_id({**doc, "text": "4 secrets"}, (1920, 1080), 24))
        self.assertNotEqual(a, tr.title_id(doc, (1080, 1920), 24))
        self.assertNotEqual(a, tr.title_id(doc, (1920, 1080), 30))

    def test_what_is_in_the_way(self):
        mine, theirs, apart = Item("Title", 100, 172, True), Item("Interview_02", 150, 400), Item("B-roll", 172, 200)
        got = tr.in_the_way([mine, theirs, apart], 100, 172, ours)
        self.assertEqual(got, ([mine], [theirs]))       # B-roll starts as the title ends: not in the way

    def test_the_topmost_track_free_of_other_clips(self):
        tl = Timeline([[Item("A", 0, 500)], [], [Item("B", 300, 400)]])
        self.assertEqual(tr.pick_track(tl, 100, 172, ours), 3)      # B is later: V3 is free here
        self.assertEqual(tr.pick_track(tl, 350, 420, ours), 2)
        full = Timeline([[Item("A", 0, 500)], [Item("B", 0, 500)]])
        self.assertEqual(tr.pick_track(full, 100, 172, ours), 3)    # a new track above them
        mine = Timeline([[Item("A", 0, 500)], [Item("T", 100, 172, True)]])
        self.assertEqual(tr.pick_track(mine, 100, 172, ours), 2)    # Buddy's own title gets replaced

    def test_clock(self):
        self.assertEqual(tr.clock(86400 + 400, 24, 86400), "0:16.7")
        self.assertEqual(tr.clock(86400 + 24 * 3725, 24, 86400), "1:02:05.0")


if __name__ == "__main__":
    unittest.main()
