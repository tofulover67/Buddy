"""Animation's node layout (pages/text_animator/flow_layout.py): where a
clip's Fusion nodes go in the exported comp text - the clip's path on one
row, masks above what they mask, key holders underneath, the person's own
placed nodes left alone - and that the text is otherwise untouched."""

import unittest

import _paths  # noqa: F401
from pages.text_animator import flow_layout as fl

COL, ROW = fl.COLUMN, fl.ROW


def link(inp, src, out="Output"):
    return f'\n\t\t\t\t{inp} = Input {{\n\t\t\t\t\tSourceOp = "{src}",\n\t\t\t\t\tSource = "{out}",\n\t\t\t\t}},'


def tool(name, kind, links=(), pos=None, extra=""):
    view = f"\n\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }}," if pos else ""
    inputs = f"\n\t\t\tInputs = {{{''.join(link(*l) for l in links)}\n\t\t\t}}," if links else ""
    return f"\n\t\t{name} = {kind} {{{inputs}{view}{extra}\n\t\t}},"


def comp(*tools):
    return ("Composition {\n\tCurrentTime = 0,\n\tTools = {" + "".join(tools) + "\n\t},"
            "\n\tPrefs = {\n\t\tComp = {\n\t\t\tX = 1,\n\t\t},\n\t},\n}\n")


def still(*extra):
    """A still's comp after Buddy's move: MediaIn -> squash -> Merge (canvas
    behind, a wipe mask) -> MediaOut, a path modifier and two key holders."""
    return comp(
        tool("MediaIn1", "Loader"),
        tool("MediaOut1", "Saver", [("Input", "BuddyMotion")]),
        tool("BuddyMotionSquash", "Transform", [("Input", "MediaIn1"), ("XSize", "BuddyMotionKeysWidthX", "Value")]),
        tool("BuddyMotionCanvas", "Background"),
        tool("BuddyMotionMask", "RectangleMask", [("Center", "XYPath1", "Value")]),
        tool("XYPath1", "XYPath"),
        tool("BuddyMotion", "Merge", [("EffectMask", "BuddyMotionMask", "Mask"), ("Background", "BuddyMotionCanvas"),
                                      ("Foreground", "BuddyMotionSquash")]),
        tool("BuddyMotionKeysY", "Transform"),
        tool("BuddyMotionKeysX", "Transform"),
        *extra,
    )


class LayoutTests(unittest.TestCase):
    def test_the_clip_runs_left_to_right_with_the_rest_around_it(self):
        pos = fl.layout(fl.parse(still()))
        y = pos["MediaIn1"][1]
        # The clip's path: one row, a column apart, MediaOut on the right.
        self.assertEqual([pos[n] for n in ("MediaIn1", "BuddyMotionSquash", "BuddyMotion", "MediaOut1")],
                         [(0, y), (COL, y), (2 * COL, y), (3 * COL, y)])
        self.assertEqual(pos["BuddyMotionMask"], (2 * COL, y - ROW))           # straight above what it masks
        self.assertLess(pos["BuddyMotionCanvas"][1], y)                       # the canvas comes in from above
        self.assertNotIn("XYPath1", pos)                                      # modifiers aren't in the flow
        self.assertNotIn("BuddyMotionKeysWidthX", pos)
        # Key holders: a row underneath, in name order.
        self.assertEqual(pos["BuddyMotionKeysX"][1], pos["BuddyMotionKeysY"][1])
        self.assertGreater(pos["BuddyMotionKeysX"][1], y + ROW)
        self.assertLess(pos["BuddyMotionKeysX"][0], pos["BuddyMotionKeysY"][0])
        self.assertEqual(len(set(pos.values())), len(pos))                    # no two on one spot

    def test_the_persons_own_placed_nodes_stay_and_buddys_follow_them(self):
        text = comp(
            tool("Template", "TextPlus", pos=(220, 49.5)),
            tool("MediaOut1", "Saver", [("Input", "BuddyMotion")], pos=(440, 49.5)),
            tool("BuddyMotionCanvas", "Background"),
            tool("BuddyMotion", "Merge", [("Background", "BuddyMotionCanvas"), ("Foreground", "Template")]),
        )
        pos = fl.layout(fl.parse(text))
        self.assertNotIn("Template", pos)
        self.assertEqual(pos["BuddyMotion"], (220 + COL, 49.5))
        self.assertEqual(pos["MediaOut1"], (220 + 2 * COL, 49.5))             # moved on past Buddy's

    def test_after_remove_the_mediaout_sits_beside_the_clip(self):
        text = comp(tool("Template", "TextPlus", pos=(220, 49.5)),
                    tool("MediaOut1", "Saver", [("Input", "Template")], pos=(900, 300)))
        self.assertEqual(fl.layout(fl.parse(text)), {"MediaOut1": (220 + COL, 49.5)})

    def test_blinds_slats_stack_up_and_trails_keep_the_clip_on_its_row(self):
        slats = [tool("BuddyMotionMask1", "RectangleMask", [("EffectMask", "BuddyMotionMask2", "Mask")]),
                 tool("BuddyMotionMask2", "RectangleMask")]
        text = comp(
            tool("MediaIn1", "Loader"),
            tool("MediaOut1", "Saver", [("Input", "BuddyMotionEcho")]),
            tool("BuddyMotionCanvas", "Background"),
            tool("BuddyMotion", "Merge", [("EffectMask", "BuddyMotionMask1", "Mask"),
                                          ("Background", "BuddyMotionCanvas"), ("Foreground", "MediaIn1")]),
            *slats,
            tool("BuddyMotionEchoCanvas", "Background"),
            tool("BuddyMotionEchoLate1", "TimeSpeed", [("Input", "BuddyMotion")]),
            tool("BuddyMotionEcho1", "Merge", [("Background", "BuddyMotionEchoCanvas"),
                                               ("Foreground", "BuddyMotionEchoLate1")]),
            tool("BuddyMotionEcho", "Merge", [("Background", "BuddyMotionEcho1"), ("Foreground", "BuddyMotion")]),
        )
        pos = fl.layout(fl.parse(text))
        y = pos["MediaOut1"][1]
        # The clip's path: MediaIn, Buddy's Merge, the trails' last Merge, MediaOut.
        for n in ("MediaIn1", "BuddyMotion", "BuddyMotionEcho"):
            self.assertEqual(pos[n][1], y, n)
        self.assertLess(pos["MediaIn1"][0], pos["BuddyMotion"][0])
        self.assertLess(pos["BuddyMotion"][0], pos["BuddyMotionEcho"][0])
        # The copies: off the clip's row.
        for n in ("BuddyMotionEchoLate1", "BuddyMotionEcho1", "BuddyMotionEchoCanvas"):
            self.assertNotEqual(pos[n][1], y, n)
        # The slats: a stack straight above the Merge.
        mx, my = pos["BuddyMotion"]
        self.assertEqual(pos["BuddyMotionMask1"], (mx, my - ROW))
        self.assertEqual(pos["BuddyMotionMask2"], (mx, my - 2 * ROW))
        self.assertEqual(len(set(pos.values())), len(pos))


class TextTests(unittest.TestCase):
    def test_only_positions_change_and_a_second_pass_changes_nothing(self):
        text = still()
        tidy = fl.tidy(text)
        self.assertNotEqual(tidy, text)
        stripped = "\n".join(line for line in tidy.split("\n") if "ViewInfo = OperatorInfo" not in line)
        self.assertEqual(stripped, text)
        self.assertEqual(fl.tidy(tidy), tidy)
        parsed = fl.parse(tidy)
        self.assertEqual({n: parsed[n]["pos"] for n in fl.layout(fl.parse(text))},
                         {n: (float(x), float(y)) for n, (x, y) in fl.layout(fl.parse(text)).items()})

    def test_an_existing_position_is_rewritten_in_place(self):
        text = comp(tool("MediaIn1", "Loader", pos=(0, 0)),
                    tool("MediaOut1", "Saver", [("Input", "MediaIn1")], pos=(5, 5)))
        tidy = fl.tidy(text)
        self.assertEqual(tidy.count("ViewInfo"), 2)
        self.assertEqual(fl.parse(tidy)["MediaOut1"]["pos"], (COL, 0.0))

    def test_text_it_cant_read_comes_back_as_it_was(self):
        for text in ("", "not a comp", "Composition {\n\tTools = {\n\t\tA = Merge {\n", comp()):
            self.assertEqual(fl.tidy(text), text)

    def test_a_node_placed_some_other_way_is_left_alone(self):
        odd = "\n\t\t\tViewInfo = GroupInfo {\n\t\t\t\tPos = { 7, 7 },\n\t\t\t},"
        text = comp(tool("MediaIn1", "Loader"),
                    tool("MediaOut1", "Saver", [("Input", "BuddyMotion")]),
                    tool("BuddyMotion", "Merge", [("Foreground", "MediaIn1")], extra=odd))
        tidy = fl.tidy(text)
        self.assertIn(odd, tidy)
        self.assertEqual(tidy.count("GroupInfo"), 1)


if __name__ == "__main__":
    unittest.main()
