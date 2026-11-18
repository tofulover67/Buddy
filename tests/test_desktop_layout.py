"""The desktop layout's window bookkeeping (app/core/desktop_layout.py):
where windows open, what survives the settings file, stacking, and the
Cascade / Tile arrangements. No Qt."""

import unittest

import _paths  # noqa: F401
from core import desktop_layout as dl

AREA = (1200, 800)


def inside(geom, area=AREA):
    x, y, w, h = geom
    return x >= 0 and y >= 0 and x + w <= area[0] and y + h <= area[1]


class ClampTests(unittest.TestCase):
    def test_a_window_is_pulled_back_onto_the_desk(self):
        self.assertEqual(dl.clamp((1100, 700, 400, 300), AREA), (800, 500, 400, 300))
        self.assertEqual(dl.clamp((-50, -20, 400, 300), AREA), (0, 0, 400, 300))

    def test_never_smaller_than_the_minimum_or_bigger_than_the_desk(self):
        self.assertEqual(dl.clamp((10, 10, 50, 40), AREA)[2:], (dl.MIN_WIDTH, dl.MIN_HEIGHT))
        self.assertEqual(dl.clamp((0, 0, 5000, 5000), AREA), (0, 0, *AREA))

    def test_a_desk_smaller_than_the_minimum_is_filled(self):
        self.assertEqual(dl.clamp((0, 0, 50, 50), (200, 100)), (0, 0, 200, 100))

    def test_a_dragged_window_may_leave_the_desk_but_for_its_title_bar(self):
        keep_x, keep_y = dl.KEEP
        # Free inside the desk, as before.
        self.assertEqual(dl.reach((100, 80, 400, 300), AREA), (100, 80, 400, 300))
        # Past the left, right and bottom edges: KEEP of the title bar stays on.
        self.assertEqual(dl.reach((-5000, 80, 400, 300), AREA), (keep_x - 400, 80, 400, 300))
        self.assertEqual(dl.reach((5000, 80, 400, 300), AREA), (AREA[0] - keep_x, 80, 400, 300))
        self.assertEqual(dl.reach((100, 5000, 400, 300), AREA), (100, AREA[1] - keep_y, 400, 300))
        # Never past the top: the title bar is what it's moved by.
        self.assertEqual(dl.reach((100, -200, 400, 300), AREA)[1], 0)
        # A window as big as the desk (tiled, or just large) can still be moved aside.
        x, y, w, h = dl.reach((300, 250, *AREA), AREA)
        self.assertEqual((x, y, w, h), (300, 250, *AREA))
        # Sized like clamp: no bigger than the desk, no smaller than the minimum.
        self.assertEqual(dl.reach((0, 0, 5000, 5000), AREA)[2:], AREA)
        self.assertEqual(dl.reach((0, 0, 10, 10), AREA)[2:], (dl.MIN_WIDTH, dl.MIN_HEIGHT))
        # Resizing one that's partly off the desk doesn't pull it back on.
        self.assertEqual(dl.resize((-200, 80, 400, 300), frozenset({"right"}), 50, 0, AREA), (-200, 80, 450, 300))


class ResizeTests(unittest.TestCase):
    FRAME = (0, 0, 600, 400)     # left, top, right, bottom

    def edges(self, x, y):
        return set(dl.edges_at(x, y, self.FRAME, grip=6, corner=18))

    def test_every_edge_and_corner_can_be_grabbed(self):
        self.assertEqual(self.edges(2, 200), {"left"})
        self.assertEqual(self.edges(598, 200), {"right"})
        self.assertEqual(self.edges(300, 2), {"top"})
        self.assertEqual(self.edges(300, 398), {"bottom"})
        self.assertEqual(self.edges(2, 2), {"top", "left"})
        self.assertEqual(self.edges(598, 2), {"top", "right"})
        self.assertEqual(self.edges(2, 398), {"bottom", "left"})
        self.assertEqual(self.edges(598, 398), {"bottom", "right"})
        self.assertEqual(self.edges(300, 200), set())

    def test_corners_reach_further_along_the_edges_than_the_grip(self):
        # 15px along the top edge from the top-right corner is still the corner.
        self.assertEqual(self.edges(585, 3), {"top", "right"})
        self.assertEqual(self.edges(3, 15), {"top", "left"})
        # ...but only on the edge itself: 15px in from both edges is the title bar.
        self.assertEqual(self.edges(585, 15), set())

    def test_the_opposite_edges_stay_put(self):
        g = (100, 100, 500, 300)
        self.assertEqual(dl.resize(g, {"top", "left"}, -40, -30, AREA), (60, 70, 540, 330))
        self.assertEqual(dl.resize(g, {"top", "right"}, 40, 30, AREA), (100, 130, 540, 270))
        self.assertEqual(dl.resize(g, {"bottom", "right"}, 40, 30, AREA), (100, 100, 540, 330))

    def test_a_dragged_edge_stops_at_the_desk_and_the_minimum(self):
        g = (100, 100, 500, 300)
        # Past the top-left of the desk: pinned there, the window doesn't slide.
        self.assertEqual(dl.resize(g, {"top", "left"}, -500, -500, AREA), (0, 0, 600, 400))
        # Past the right of the desk: stops at it rather than pushing left.
        self.assertEqual(dl.resize(g, {"right"}, 5000, 0, AREA), (100, 100, 1100, 300))
        # Shrunk past the minimum from the top: the bottom edge still stays.
        x, y, w, h = dl.resize(g, {"top"}, 0, 1000, AREA)
        self.assertEqual((h, y + h), (dl.MIN_HEIGHT, 400))


class PlacementTests(unittest.TestCase):
    def test_new_windows_cascade_and_stay_on_the_desk(self):
        state = dl.DesktopState()
        spots = []
        for tool in "abcdefghij":
            state.open(tool)
            state.place_new(tool, AREA, preferred=(700, 500))
            spots.append(state.windows[tool]["geom"])
        self.assertTrue(all(inside(g) for g in spots))
        self.assertNotEqual(spots[0][:2], spots[1][:2])
        self.assertEqual(spots[1][0] - spots[0][0], dl.CASCADE_STEP)

    def test_opening_size_keeps_the_margin(self):
        w, h = dl.opening_size(AREA, (5000, 5000))
        self.assertEqual((w, h), (AREA[0] - 2 * dl.MARGIN, AREA[1] - 2 * dl.MARGIN))
        self.assertEqual(dl.opening_size(AREA), (int(AREA[0] * dl.DEFAULT_SHARE), int(AREA[1] * dl.DEFAULT_SHARE)))

    def test_tile_fills_the_desk_without_overlap(self):
        for count in range(1, 10):
            cells = dl.tile(count, AREA)
            self.assertEqual(len(cells), count)
            for g in cells:
                self.assertTrue(inside(g), (count, g))
            for i, a in enumerate(cells):
                for b in cells[i + 1:]:
                    overlap = (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
                    self.assertFalse(overlap, (count, a, b))


class StateTests(unittest.TestCase):
    def test_open_close_minimise_and_what_is_in_front(self):
        state = dl.DesktopState()
        self.assertTrue(state.open("a"))
        self.assertTrue(state.open("b"))
        self.assertEqual(state.front(), "b")
        self.assertFalse(state.open("a"))          # already on show: just to the front
        self.assertEqual(state.front(), "a")
        state.minimize("a")
        self.assertEqual(state.front(), "b")
        self.assertEqual(state.window_state("a"), "min")
        self.assertTrue(state.open("a"))           # back from minimised counts as newly shown
        state.close("b")
        self.assertIsNone(state.window_state("b"))
        self.assertEqual(state.open_ids(), ["a"])

    def test_moving_a_window_un_maximises_it(self):
        state = dl.DesktopState()
        state.open("a")
        self.assertTrue(state.toggle_max("a"))
        state.set_geom("a", (10, 20, 400, 300))
        self.assertFalse(state.windows["a"]["max"])

    def test_cascade_and_tile_bring_back_minimised_windows(self):
        state = dl.DesktopState()
        for tool in "abc":
            state.open(tool)
        state.minimize("b")
        state.tile(AREA)
        self.assertEqual([state.window_state(t) for t in "abc"], ["open"] * 3)
        state.toggle_max("a")
        state.cascade(AREA)
        self.assertFalse(state.windows["a"]["max"])
        self.assertTrue(all(inside(state.windows[t]["geom"]) for t in "abc"))

    def test_open_windows_get_different_title_colours(self):
        state = dl.DesktopState()
        for tool in "abcd":
            state.open(tool)
        self.assertEqual(sorted(state.windows[t]["color"] for t in "abcd"), list(range(dl.WINDOW_COLORS)))
        # A fifth shares, and a tool reopened keeps its colour when it's free.
        state.open("e")
        colour = state.windows["b"]["color"]
        state.close("b")
        state.close("e")
        state.open("b")
        self.assertEqual(state.windows["b"]["color"], colour)

    def test_hidden_tools_lose_their_windows(self):
        state = dl.DesktopState()
        for tool in "abc":
            state.open(tool)
        state.keep_only({"a", "c"})
        self.assertEqual(state.open_ids(), ["a", "c"])


class TaskbarTests(unittest.TestCase):
    def test_pins_first_then_open_windows_in_the_order_they_opened(self):
        state = dl.DesktopState()
        for tool in ("c", "pinned_open", "a"):
            state.open(tool)
        state.raise_("c")            # stacking doesn't reorder the taskbar
        ids = dl.taskbar_ids(["p1", "pinned_open"], state, {"p1", "pinned_open", "a", "c"})
        self.assertEqual(ids, [("p1", True), ("pinned_open", True), ("c", False), ("a", False)])

    def test_tools_out_of_the_rail_are_left_off(self):
        state = dl.DesktopState()
        state.open("hidden")
        self.assertEqual(dl.taskbar_ids(["gone"], state, {"x"}), [])

    def test_clicking_the_window_in_front_minimises_it(self):
        state = dl.DesktopState()
        state.open("a")
        state.open("b")
        self.assertEqual(dl.task_click(state, "b"), "minimize")
        self.assertEqual(dl.task_click(state, "a"), "open")       # behind: bring forward
        state.minimize("b")
        self.assertEqual(dl.task_click(state, "b"), "open")       # minimised: restore
        self.assertEqual(dl.task_click(state, "never"), "open")

    def test_saved_pins_are_checked_and_an_empty_list_is_kept(self):
        known = {"manual_chat", "time_tracker", "x"}
        self.assertEqual(dl.load_pins(None, known), ["manual_chat", "time_tracker"])
        self.assertEqual(dl.load_pins([], known), [])
        self.assertEqual(dl.load_pins(["x", "x", "gone", 3], known), ["x"])


class SettingsTests(unittest.TestCase):
    def test_round_trip(self):
        state = dl.DesktopState()
        state.open("a")
        state.set_geom("a", (1, 2, 300, 200))
        state.open("b")
        state.minimize("b")
        again = dl.DesktopState.from_dict(state.to_dict(), {"a", "b"})
        self.assertEqual(again.to_dict(), state.to_dict())

    def test_a_damaged_file_is_trusted_only_as_far_as_it_checks_out(self):
        data = {"windows": {"a": {"geom": [1, 2, "x", 4], "open": 1}, "gone": {"open": True}, "b": "nope"},
                "order": ["gone", "a", "a"]}
        state = dl.DesktopState.from_dict(data, {"a", "b"})
        self.assertEqual(list(state.windows), ["a"])
        self.assertIsNone(state.windows["a"]["geom"])
        self.assertTrue(state.windows["a"]["open"])
        self.assertEqual(state.order, ["a"])
        self.assertEqual(dl.DesktopState.from_dict("junk", {"a"}).windows, {})



class ArrangementTests(unittest.TestCase):
    """The Tile button's arrangements: where the windows go, who is where, and trading places."""

    def state(self, *ids):
        state = dl.DesktopState()
        for tool_id in ids:
            state.open(tool_id)
        return state

    def test_every_arrangement_keeps_the_windows_apart_and_on_the_desk(self):
        for preset in dl.PRESET_IDS:
            for count in range(1, 7):
                got = dl.cells(preset, count, AREA)
                self.assertEqual(len(got), count, preset)
                for x, y, w, h in got:
                    self.assertTrue(x >= dl.MARGIN and y >= dl.MARGIN and w > 0 and h > 0, (preset, count))
                    self.assertTrue(x + w <= AREA[0] - dl.MARGIN + 1 and y + h <= AREA[1] - dl.MARGIN + 1, (preset, count))
                for i, a in enumerate(got):
                    for b in got[i + 1:]:
                        apart = a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
                        self.assertTrue(apart, (preset, count, a, b))

    def test_a_grid_is_even_when_it_can_be(self):
        wide, squarish = (2560, 1277), (1200, 800)
        for area in (wide, squarish):
            four = dl.tile(4, area)
            self.assertEqual(len({c[0] for c in four}), 2, area)       # two by two, not three and one
            self.assertEqual(len({c[1] for c in four}), 2, area)
            self.assertEqual(len({(c[2], c[3]) for c in four}), 1, area)   # all four the same size
            self.assertEqual(6 % dl.grid_columns(6, area), 0)          # six: an even grid either way round
            self.assertEqual(dl.grid_columns(2, area), 2)              # two side by side
            self.assertEqual(dl.grid_columns(1, area), 1)
        self.assertEqual(dl.grid_columns(6, wide), 3)                  # three by two on a wide desk
        self.assertEqual(dl.grid_columns(8, wide), 4)
        self.assertEqual(dl.grid_columns(5, wide), 3)                  # no even grid: three and two
        self.assertEqual(dl.grid_columns(4, (500, 1600)), 1)           # a tall narrow desk: stacked

    def test_the_main_window_is_the_big_one_where_the_name_says(self):
        left, right, top = (dl.cells(p, 3, AREA) for p in ("main_left", "main_right", "main_top"))
        self.assertGreater(left[0][2], left[1][2])
        self.assertLess(left[0][0], left[1][0])                    # the main one on the left, the rest stacked beside it
        self.assertEqual(left[1][0], left[2][0])
        self.assertGreater(right[0][0], right[1][0])
        self.assertLess(top[0][1], top[1][1])                      # on top, the rest in a row below
        self.assertEqual(top[1][1], top[2][1])
        self.assertEqual([c[1] for c in dl.cells("columns", 3, AREA)], [dl.MARGIN] * 3)
        self.assertEqual([c[0] for c in dl.cells("rows", 3, AREA)], [dl.MARGIN] * 3)
        self.assertEqual(dl.cells("grid", 4, AREA), dl.tile(4, AREA))
        self.assertEqual(dl.cells("no such thing", 2, AREA), dl.tile(2, AREA))

    def test_the_front_window_takes_the_main_place_and_tile_remembers_the_choice(self):
        state = self.state("a", "b", "c")
        state.raise_("b")
        state.arrange("main_left", AREA)
        self.assertEqual(state.slots, ["b", "a", "c"])             # in front first, then as they were opened
        self.assertEqual(state.windows["b"]["geom"], dl.cells("main_left", 3, AREA)[0])
        state.set_geom("a", (5, 5, 300, 200))                      # moved by hand: no longer arranged
        self.assertIsNone(state.slots)
        state.tile(AREA)                                           # Tile: the arrangement last chosen
        self.assertEqual(state.preset, "main_left")
        self.assertEqual(state.windows["c"]["geom"], dl.cells("main_left", 3, AREA)[2])

    def test_two_arranged_windows_trade_places(self):
        state = self.state("a", "b", "c")
        state.arrange("columns", AREA)
        first, _second, third = dl.cells("columns", 3, AREA)
        self.assertEqual(state.slots, ["c", "a", "b"])
        self.assertEqual(state.slot_at(third[0] + 10, 100, AREA), "b")
        self.assertIsNone(state.slot_at(2, 2, AREA))               # the margin is nobody's
        self.assertTrue(state.swap("c", "b", AREA))
        self.assertEqual(state.slots, ["b", "a", "c"])
        self.assertEqual((state.windows["b"]["geom"], state.windows["c"]["geom"]), (first, third))
        self.assertFalse(state.swap("c", "c", AREA))
        self.assertFalse(state.swap("c", "nobody", AREA))
        state.close("a")
        self.assertFalse(state.swap("c", "b", AREA))               # one closed: not arranged any more

    def test_an_arrangement_follows_the_desk_and_is_saved(self):
        state = self.state("a", "b")
        state.arrange("rows", AREA)
        state.reflow((1000, 600))
        self.assertEqual(state.windows["b"]["geom"], dl.cells("rows", 2, (1000, 600))[0])
        again = dl.DesktopState.from_dict(state.to_dict(), {"a", "b"})
        self.assertEqual((again.preset, again.slots), ("rows", ["b", "a"]))
        saved = state.to_dict()
        saved["slots"] = ["b", "gone"]                             # not the windows on show: not arranged
        self.assertIsNone(dl.DesktopState.from_dict(saved, {"a", "b"}).slots)
        self.assertEqual(dl.DesktopState.from_dict({"preset": "sideways"}, set()).preset, "grid")
        state.cascade(AREA)
        self.assertIsNone(state.slots)
        state.reflow((900, 500))                                   # nothing arranged: nothing moves


if __name__ == "__main__":
    unittest.main()
