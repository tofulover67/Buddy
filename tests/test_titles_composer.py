"""Animation > Titles: the composer (titles/composer.py) - roles, line breaks, sizes and
placement from a hook's text - with a fake measure function. The tests check the
layout's shape (the key word alone on its line, no line ending on "the", a forced
break kept, overrides surviving an edit), not just its counts."""

import time
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.text_animator.titles import composer as C
from pages.text_animator.titles import looks as L

LANDSCAPE, PORTRAIT = (1920, 1080), (1080, 1920)
FRAMES = (LANDSCAPE, PORTRAIT)
HOOKS = [
    "3 secrets nobody tells you",
    "Why you should never quit your job",
    "STOP",
    "The best way to learn to code",
    "How I made $10,000 in 30 days with one simple trick nobody uses",
    "the secret to the perfect morning routine",
    "You are doing it wrong",
    "This changes everything",
]


def fake(text, font, size):
    return len(text) * size * 0.55


def compose(text, look="hormozi", frame=LANDSCAPE, **changes):
    doc = C.new_doc(text, look)
    for key, value in changes.items():
        if key in doc["over"]:
            doc["over"][key] = value
        else:
            doc[key] = value
    return C.compose(doc, frame, fake)


def line_of(layout, word_id):
    return next(ids for ids in layout["lines"] if word_id in ids)


def size_of(layout, word_id):
    return next(b["size"] for b in layout["boxes"] if b["id"] == word_id)


class TokenTests(unittest.TestCase):
    def test_ids_are_the_word_and_its_occurrence(self):
        ids = [t["id"] for t in C.tokenize("The cat and THE hat?")]
        self.assertEqual(ids, ["the#1", "cat#1", "and#1", "the#2", "hat#1"])

    def test_numbers_keep_their_signs(self):
        self.assertEqual([t["norm"] for t in C.tokenize("$10,000 in 30% (now)")], ["$10,000", "in", "30%", "now"])

    def test_capitals_follow_the_language(self):
        self.assertEqual(C.upper("straße"), "STRASSE")
        self.assertEqual(C.upper("istanbul", "tr"), "İSTANBUL")


class RoleTests(unittest.TestCase):
    def roles(self, text, look=None, manual=None):
        tokens = C.tokenize(text)
        roles = C.assign_roles(tokens, look or L.get("hormozi"), manual)
        return {t["id"]: r for t, (r, _src) in zip(tokens, roles)}

    def test_one_key_and_a_number_wins_it(self):
        roles = self.roles("3 secrets nobody tells you")
        self.assertEqual([w for w, r in roles.items() if r == "key"], ["3#1"])

    def test_long_hooks_get_two_keys(self):
        roles = self.roles("How I made $10,000 in 30 days with one simple trick")
        self.assertEqual(sorted(w for w, r in roles.items() if r == "key"), ["$10,000#1", "30#1"])

    def test_a_connector_is_never_the_key(self):
        for hook in HOOKS:
            for word, role in self.roles(hook).items():
                if role == "key":
                    self.assertFalse(C.is_stopword(word.split("#")[0]), (hook, word))

    def test_connectors_in_other_languages(self):
        for text, key in (("Das solltest du niemals tun", "das#1"), ("Ce que personne ne vous dit", "ce#1"),
                          ("Nadie te cuenta esto", "te#1")):
            self.assertNotEqual(self.roles(text)[key], "key", text)
            self.assertTrue(C.is_stopword(key.split("#")[0]), key)
        self.assertFalse(C.is_stopword("die"))      # German "die" - and the English verb

    def test_a_hook_of_only_connectors_still_gets_one_key(self):
        self.assertEqual(list(self.roles("to the").values()).count("key"), 1)

    def test_a_role_set_by_hand_wins_and_counts(self):
        roles = self.roles("3 secrets nobody tells you", manual={"secrets#1": "key"})
        self.assertEqual([w for w, r in roles.items() if r == "key"], ["secrets#1"])

    def test_an_accent_only_where_the_look_has_one(self):
        text = "Why you should never quit your job"
        self.assertEqual(list(self.roles(text, {"accent": "auto"}).values()).count("accent"), 1)
        self.assertNotIn("accent", self.roles(text, {"accent": "none"}).values())


class BreakTests(unittest.TestCase):
    def test_every_way_to_cut_the_words(self):
        sets = C.break_sets(4, 1, 4, 4)
        self.assertEqual(len(sets), 8)          # 2^(n-1)
        for lines in sets:
            self.assertEqual(lines[0][0], 0)
            self.assertEqual(lines[-1][1], 4)
            self.assertTrue(all(a[1] == b[0] for a, b in zip(lines, lines[1:])))

    def test_the_limits_hold(self):
        self.assertTrue(all(len(ls) == 2 and all(j - i <= 3 for i, j in ls) for ls in C.break_sets(5, 2, 2, 3)))
        self.assertEqual(len(C.break_sets(14, 1, 14, 14, limit=50)), 50)


class LayoutTests(unittest.TestCase):
    # The looks the composer's scoring was tuned on: in these the key always gets its own
    # line. Later looks with a quieter hierarchy (Cinematic, Luxury, Minimal...) may keep
    # "3 SECRETS" together - a giant "3" alone is too big beside their words - but no
    # look ever draws its key smaller than another word.
    CORE_LOOKS = ("hormozi", "script", "clean_outline", "outline_fill", "echo", "neon_extrude", "title_bar",
                  "long_shadow", "pop_3d", "mixed")

    def test_the_key_word_is_never_smaller_than_another(self):
        for frame in FRAMES:
            for look in L.LOOKS:
                layout = compose("3 secrets nobody tells you", look, frame)
                self.assertGreaterEqual(size_of(layout, "3#1") + 0.01, max(b["size"] for b in layout["boxes"]),
                                        (look, frame))

    def test_the_key_word_is_alone_on_its_line(self):
        """Or, in a look that stacks, with the words beside it in rows as tall as it."""
        for frame in FRAMES:
            for look in self.CORE_LOOKS:
                layout = compose("3 secrets nobody tells you", look, frame)
                line = line_of(layout, "3#1")
                if L.get(look).get("stack") and len(line) > 1:
                    key = next(b for b in layout["boxes"] if b["id"] == "3#1")
                    side = [b for b in layout["boxes"] if b["id"] in line and b["id"] != "3#1"]
                    self.assertGreater(len({round(b["baseline"]) for b in side}), 1, (look, frame))
                    self.assertTrue(all(b["size"] < key["size"] for b in side), (look, frame))
                else:
                    self.assertEqual(line, ["3#1"], (look, frame))

    def test_a_key_alone_on_its_line_comes_out_biggest(self):
        layout = compose("3 secrets nobody tells you", frame=PORTRAIT)
        self.assertEqual(max(layout["boxes"], key=lambda b: b["size"])["id"], "3#1")

    def test_no_line_ends_on_the(self):
        """(Not in a look whose key has its line to itself - Echo, or Title + bar with its
        caption: "THE" before the key "BEST" then has nowhere else to go.)"""
        for hook in HOOKS:
            for frame in FRAMES:
                for look in [lk for lk in L.LOOKS if not (L.get(lk).get("keyAlone") or L.get(lk).get("others"))]:
                    for ids in compose(hook, look, frame)["lines"][:-1]:
                        self.assertNotEqual(ids[-1].split("#")[0], "the", (hook, look, frame))

    def test_a_forced_break_is_kept_and_a_forbidden_one_never_made(self):
        layout = compose("3 secrets nobody tells you", brk={"tells#1": True})
        self.assertTrue(any(ids[0] == "tells#1" for ids in layout["lines"]))
        layout = compose("3 secrets nobody tells you", frame=PORTRAIT, brk={"secrets#1": False})
        self.assertFalse(any(ids[0] == "secrets#1" for ids in layout["lines"]))

    def test_a_line_count_set_by_hand(self):
        for n in (1, 2, 4):
            self.assertEqual(len(compose("Why you should never quit your job", lines=n)["lines"]), n)

    def test_editing_the_text_keeps_the_other_words_overrides(self):
        before = compose("secrets nobody tells you", roles={"tells#1": "accent"}, size={"you#1": 1.3})
        after = compose("3 secrets nobody tells you", roles={"tells#1": "accent"}, size={"you#1": 1.3})
        for layout in (before, after):
            roles = {t["id"]: (t["role"], t["role_from"]) for t in layout["tokens"]}
            self.assertEqual(roles["tells#1"], ("accent", "manual"))
        # "you" stays 1.3x its line's size - the nudge followed the word, not its index.
        for layout in (before, after):
            line = line_of(layout, "you#1")
            others = [size_of(layout, w) for w in line if w != "you#1"]
            if others:
                self.assertAlmostEqual(size_of(layout, "you#1") / others[0], 1.3, places=6)

    def test_everything_stays_in_the_frame_and_the_block_in_the_safe_zone(self):
        for hook in HOOKS:
            for frame in FRAMES:
                for look in L.LOOKS:
                    layout = compose(hook, look, frame)
                    x0, y0, x1, y1 = layout["safe"]
                    block = layout["block"]
                    self.assertGreaterEqual(block["x"], x0 - 0.5, hook)
                    self.assertLessEqual(block["x"] + block["w"], x1 + 0.5, hook)
                    self.assertGreaterEqual(block["y"], y0 - 0.5, hook)
                    self.assertLessEqual(block["y"] + block["h"], y1 + 0.5, hook)
                    for b in C.hit_boxes(layout):
                        self.assertTrue(0 <= b["x"] and b["x"] + b["w"] <= 1 and 0 <= b["y"] <= 1, (hook, b))

    def test_lines_never_overlap(self):
        """A line is as tall as its biggest word: the next starts below it."""
        for hook in HOOKS:
            for frame in FRAMES:
                layout = compose(hook, "hormozi", frame)
                tops = {}
                for b in layout["boxes"]:
                    top, bottom = tops.get(b["line"], (1e9, -1e9))
                    tops[b["line"]] = (min(top, b["y"]), max(bottom, b["y"] + b["h"]))
                spans = [tops[k] for k in sorted(tops)]
                for (_t, bottom), (top, _b) in zip(spans, spans[1:]):
                    self.assertGreater(top, bottom, hook)

    def test_a_capital_that_dips_below_the_baseline_gets_room(self):
        """$10,000's comma reaches under the baseline: the next line starts below it."""
        def ink(text, font):
            return (0.7, 0.2 if "," in text else 0.0)
        doc = C.new_doc("$10,000 in 30 days", "hormozi")
        doc["over"]["brk"] = {"in#1": True}
        layout = C.compose(doc, LANDSCAPE, fake, ink=ink)
        money = next(b for b in layout["boxes"] if b["id"] == "$10,000#1")
        below = min(b["y"] for b in layout["boxes"] if b["line"] == money["line"] + 1)
        self.assertGreater(below, money["baseline"] + 0.2 * money["size"])

    def test_the_nine_positions(self):
        for pos in C.POSITIONS:
            layout = compose("3 secrets nobody tells you", pos=pos)
            x0, y0, x1, y1 = layout["safe"]
            b = layout["block"]
            cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            col = {"l": x0 + b["w"] / 2, "c": (x0 + x1) / 2, "r": x1 - b["w"] / 2}[pos[1]]
            row = {"t": y0 + b["h"] / 2, "m": (y0 + y1) / 2, "b": y1 - b["h"] / 2}[pos[0]]
            self.assertAlmostEqual(cx, col, places=6)
            self.assertAlmostEqual(cy, row, places=6)

    def test_a_custom_position_is_the_block_centre(self):
        b = compose("3 secrets nobody tells you", custom=True, gpos=(0.3, 0.7))["block"]
        self.assertAlmostEqual(b["x"] + b["w"] / 2, 0.3 * 1920, places=6)
        self.assertAlmostEqual(b["y"] + b["h"] / 2, 0.7 * 1080, places=6)

    def test_a_nudge_moves_only_its_word(self):
        plain = compose("3 secrets nobody tells you")
        nudged = compose("3 secrets nobody tells you", offset={"tells#1": (0.5, -0.25)})
        for a, b in zip(plain["boxes"], nudged["boxes"]):
            if a["id"] == "tells#1":
                self.assertAlmostEqual(b["x"] - a["x"], 0.5 * a["size"])
                self.assertAlmostEqual(b["y"] - a["y"], -0.25 * a["size"])
            else:
                self.assertEqual((a["x"], a["y"]), (b["x"], b["y"]))

    def test_the_arrangements(self):
        layout = compose("Why you should never quit your job", arrange="left")
        lefts = {round(b["x"], 6) for b in layout["boxes"] if b["id"] in [ids[0] for ids in layout["lines"]]}
        self.assertEqual(len(lefts), 1)
        cascade = compose("Why you should never quit your job", arrange="cascade", lines=3)
        firsts = [next(b["x"] for b in cascade["boxes"] if b["id"] == ids[0]) for ids in cascade["lines"]]
        self.assertNotEqual(len(set(round(x, 3) for x in firsts)), 1)

    def test_empty_text(self):
        layout = compose("   ")
        self.assertEqual((layout["boxes"], layout["lines"], layout["block"]), ([], [], None))

    def test_fourteen_words_compose_quickly(self):
        start = time.perf_counter()
        compose("How I made $10,000 in 30 days with one simple trick nobody else uses at all")
        self.assertLess(time.perf_counter() - start, 0.5)


class ScaleTests(unittest.TestCase):
    """A role's scale goes on after the width fit - fitted with it, a 1.5x key alone on its
    line would be shrunk back to the same width and the same size as the rest."""

    def look(self, mode):
        return {"id": "t", "mode": mode, "caps": True, "lines": (2, 4), "maxSize": 0.5, "bw": 0.6,
                "roles": {"normal": {"font": ("F", "R")}, "key": {"font": ("F", "R"), "scale": 1.5},
                          "small": {"font": ("F", "R"), "scale": 0.5}}}

    def test_fit_a_scaled_key_comes_out_bigger_than_the_fit(self):
        with mock.patch.dict(L.LOOKS, {"t": self.look("fit")}):
            layout = compose("go ahead and win big", "t", PORTRAIT, lines=3, roles={"win#1": "key"},
                             brk={"win#1": True, "big#1": True})
            self.assertEqual(line_of(layout, "win#1"), ["win#1"])
            block_w = 0.6 * (layout["safe"][2] - layout["safe"][0])
            fitted = block_w / (fake("WIN", None, 100) / 100)
            self.assertGreater(size_of(layout, "win#1"), fitted * 1.2)

    def test_ratio_one_body_size_with_the_roles_on_top(self):
        with mock.patch.dict(L.LOOKS, {"t": self.look("ratio")}):
            layout = compose("go ahead and win big", "t", PORTRAIT, roles={"win#1": "key", "and#1": "small"})
            body = [b["size"] for b in layout["boxes"] if b["role"] == "normal"]
            self.assertAlmostEqual(min(body), max(body))
            size = body[0]
            self.assertAlmostEqual(size_of(layout, "win#1"), 1.5 * size)
            self.assertAlmostEqual(size_of(layout, "and#1"), 0.5 * size)


class ChoiceTests(unittest.TestCase):
    def test_the_score_says_why(self):
        layout = compose("3 secrets nobody tells you")
        self.assertEqual(set(layout["terms"]), set(C.WEIGHTS))
        self.assertAlmostEqual(layout["score"], sum(C.WEIGHTS[t] * v for t, v in layout["terms"].items()))
        self.assertIn("lines", C.describe(layout))

    def test_suggestions_have_other_line_counts_and_can_be_picked(self):
        layout = compose("Why you should never quit your job", frame=PORTRAIT)
        alts = layout["alternates"]
        self.assertTrue(1 <= len(alts) <= 3)
        counts = [a["lines"] for a in alts]
        self.assertNotIn(len(layout["lines"]), counts)
        self.assertEqual(len(counts), len(set(counts)))
        picked = compose("Why you should never quit your job", frame=PORTRAIT, forced=list(alts[0]["key"]))
        self.assertEqual(len(picked["lines"]), alts[0]["lines"])
        self.assertEqual(tuple(picked["key"]), tuple(alts[0]["key"]))

    def test_the_previous_layout_is_kept_while_near_equal(self):
        checked = 0
        for hook in HOOKS:
            for frame in FRAMES:
                doc = C.new_doc(hook)
                first = C.compose(doc, frame, fake)
                for alt in first["alternates"]:
                    kept = C.compose(doc, frame, fake, prev=alt["key"])["key"]
                    near = alt["score"] <= first["score"] + C.STICK
                    self.assertEqual(kept, alt["key"] if near else first["key"], (hook, frame, alt["lines"]))
                    checked += near
        self.assertGreater(checked, 0, "no hook had a near-equal runner-up to keep")

    def test_typing_keeps_the_layout_unless_another_clearly_wins(self):
        """The previous layout's breaks are word ids, so they still apply after an edit:
        it stays while it's within STICK of the best."""
        edits = [("Why you should never quit your job", "Why you should never quit your job today"),
                 ("3 secrets nobody tells you", "3 secrets nobody ever tells you"),
                 ("The best way to learn to code", "The best way to learn to code fast")]
        for frame in FRAMES:
            for before, after in edits:
                prev = C.compose(C.new_doc(before), frame, fake)["key"]
                fresh = C.compose(C.new_doc(after), frame, fake)
                kept = C.compose(C.new_doc(after), frame, fake, prev=prev)
                if kept["key"] != fresh["key"]:
                    self.assertEqual(kept["key"], prev)
                    self.assertLessEqual(kept["score"], fresh["score"] + C.STICK)


class NewLookTests(unittest.TestCase):
    """The looks from the person's references: letter spacing, a caption tracked out to
    the title's width on a bar of its own, copies that keep their line clear."""

    def test_letter_spacing_widens_a_word_between_its_letters(self):
        layout = compose("Stop wasting money on gear", "title_bar")
        caption = [b for b in layout["boxes"] if b["role"] == "small"]
        self.assertTrue(caption)
        for b in caption:
            plain = fake(b["text"], b["font"], b["size"])
            self.assertAlmostEqual(b["w"], plain + b["track"] * (len(b["text"]) - 1), places=6)
            self.assertGreater(b["track"], 0)

    def test_a_caption_is_one_line_of_its_own_on_a_bar(self):
        for frame in FRAMES:
            layout = compose("Stop wasting money on gear", "title_bar", frame)
            roles = {b["id"]: b["role"] for b in layout["boxes"]}
            self.assertEqual(roles["stop#1"], "key")
            self.assertTrue(all(r == "small" for w, r in roles.items() if w != "stop#1"))   # "others": small
            for ids in layout["lines"]:
                self.assertEqual(len({roles[w] for w in ids}), 1, (frame, layout["lines"]))   # never mixed
            if frame == LANDSCAPE:
                self.assertEqual(len(layout["lines"]), 2)
            bars = layout["bars"]
            self.assertEqual({b["line"] for b in bars},
                             {k for k, ids in enumerate(layout["lines"]) if roles[ids[0]] == "small"})
            for bar in bars:
                for b in (x for x in layout["boxes"] if x["line"] == bar["line"]):
                    self.assertTrue(bar["x"] <= b["x"] and b["x"] + b["w"] <= bar["x"] + bar["w"])
                    self.assertTrue(bar["y"] <= b["y"] and b["baseline"] <= bar["y"] + bar["h"])

    def test_a_caption_stretches_toward_the_titles_width(self):
        """A caption shorter than the title is spaced out toward the title's width (as far as
        MAX_SPREAD lets it); one longer than the title isn't squeezed."""
        layout = compose("This changes everything", "title_bar")
        for b in layout["boxes"]:
            if b["role"] == "small":
                self.assertGreater(b["track"], 0.25 * b["size"] + 1e-6)      # spread past the role's own

    def test_echo_copies_have_room_and_the_key_its_line(self):
        for hook in ("Stop wasting money on gear", "This changes everything"):
            for frame in FRAMES:
                layout = compose(hook, "echo", frame)
                key = next(b for b in layout["boxes"] if b["role"] == "key")
                self.assertEqual(len(layout["lines"][key["line"]]), 1, (hook, frame))
                reach = 2 * 0.58 * key["size"]          # the look's copies: 2 a side, 0.58 apart
                for b in layout["boxes"]:
                    if b["line"] == key["line"] + 1:
                        self.assertGreater(b["y"], key["baseline"] + reach - 1, (hook, frame))
                    if b["line"] == key["line"] - 1:
                        self.assertLess(b["baseline"], key["y"] - reach + key["size"] * 0.3 + 1, (hook, frame))

    def test_mixed_sizes_stays_in_the_safe_zone(self):
        for frame in FRAMES:
            for hook in HOOKS:
                layout = compose(hook, "mixed", frame)
                x0, _y0, x1, _y1 = layout["safe"]
                self.assertLessEqual(layout["block"]["w"], x1 - x0 + 0.5, (hook, frame))

    def stacked(self, text="animating text like this can", key="like#1", frame=LANDSCAPE, **changes):
        layout = compose(text, "mixed", frame, roles={key: "key"}, **changes)
        boxes = {b["id"]: b for b in layout["boxes"]}
        line = line_of(layout, key)
        return layout, boxes[key], [boxes[i] for i in line if i != key]

    def test_words_beside_the_key_stack_as_tall_as_it(self):
        """"like [this / can]": the top row's ink starts where the key's does, the last
        row sits on the key's baseline, and the rows are the title's words' size or more."""
        top = 0.70 * 1.05                       # lower-case ink without ink(): the fake's cap height
        for frame in FRAMES:
            layout, key, side = self.stacked(frame=frame)
            self.assertEqual([b["text"] for b in side], ["this", "can"], frame)
            this, can = side
            self.assertAlmostEqual(this["baseline"] - this["size"] * top, key["baseline"] - key["size"] * top,
                                   delta=0.5, msg=frame)
            self.assertAlmostEqual(can["baseline"], key["baseline"], delta=0.5, msg=frame)
            self.assertLess(this["baseline"], can["baseline"] - can["size"] * top, frame)    # rows apart
            self.assertGreater(this["x"], key["x"] + key["w"], frame)
            self.assertAlmostEqual(this["x"], can["x"], delta=0.5, msg=frame)            # beside the key, left-aligned
            body = size_of(layout, "animating#1")
            self.assertGreaterEqual(this["size"], C.STACK_MIN * body - 0.5, frame)

    def test_a_stack_before_the_key_is_pressed_against_it(self):
        layout, key, side = self.stacked("the best way to grow", key="grow#1", lines=1)
        rows = sorted({round(b["baseline"]) for b in side})
        self.assertEqual(len(rows), 2)
        for row in rows:
            ends = [b["x"] + b["w"] for b in side if round(b["baseline"]) == row]
            self.assertAlmostEqual(max(ends), max(b["x"] + b["w"] for b in side), delta=0.5)
        self.assertLess(max(b["x"] + b["w"] for b in side), key["x"])

    def test_one_word_beside_the_key_stays_on_its_baseline(self):
        _layout, key, side = self.stacked("the best", key="best#1")
        self.assertEqual(len(side), 1)
        self.assertAlmostEqual(side[0]["baseline"], key["baseline"], delta=0.01)

    def test_other_looks_do_not_stack(self):
        layout = compose("like this can", "hormozi", lines=1, roles={"like#1": "key"})
        self.assertEqual(len({round(b["baseline"]) for b in layout["boxes"]}), 1)

    def test_a_stacked_block_stays_near_the_looks_fill(self):
        """Stacked lines are short - the block mustn't grow to fill the frame for it."""
        look = L.get("mixed")
        for frame in FRAMES:
            layout = compose("animating text like this can", "mixed", frame)
            x0, y0, x1, y1 = layout["safe"]
            cover = layout["block"]["w"] * layout["block"]["h"] / ((x1 - x0) * (y1 - y0))
            self.assertLessEqual(cover, look["fill"] + 1e-6, frame)

    def test_a_size_change_on_a_stacked_word_is_kept(self):
        _layout, _key, (this, can) = self.stacked(size={"this#1": 1.5})
        self.assertAlmostEqual(this["size"], can["size"] * 1.5, delta=0.01)


if __name__ == "__main__":
    unittest.main()
