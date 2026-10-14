"""Animation > Transitions: the seamless motion shapes (transition.py), how
they find and fit the selection's cuts and get keyed into a transition's
comp (transition_resolve.py), and what the page accepts - no Resolve, no Qt."""

import json
import math
import unittest

import _paths  # noqa: F401
from pages.text_animator import transition as tr
from pages.text_animator import transition_resolve as trr


def settings(**kw):
    return tr.clean({"kind": "zoom_in", "feel": "ease", **kw})


class ShapeTests(unittest.TestCase):
    def test_settings_are_cleaned(self):
        s = tr.clean({"kind": "nonsense", "feel": "wobble", "length": 99, "amount": "x", "blur": -1})
        self.assertEqual(s, {**tr.DEFAULTS, "length": 3.0, "blur": 0.0})
        self.assertEqual(tr.clean(None), tr.DEFAULTS)
        self.assertEqual(tr.clean({"amount": float("nan")})["amount"], tr.DEFAULTS["amount"])

    def test_every_feel_runs_from_start_to_end_through_the_middle(self):
        for feel in tr.FEELS:
            self.assertAlmostEqual(tr.feel(0, feel), 0, places=6)
            self.assertAlmostEqual(tr.feel(1, feel), 1, places=6)
            self.assertAlmostEqual(tr.feel(.5, feel), .5, places=6)
        self.assertLess(min(tr.feel(p / 100, "swing") for p in range(50)), 0)                # pulls back
        self.assertGreater(max(tr.feel(p / 100, "swing") for p in range(50, 101)), 1)        # overshoots
        self.assertLess(tr.feel(.25, "hit"), tr.feel(.25, "ease"))                           # a longer run-up

    def test_one_move_carries_across_the_cut(self):
        # Zoom in: the outgoing clip grows to the cut, the incoming one grows
        # on from small - always growing, the same rate either side.
        s = settings(amount=1)
        before, after = tr.at(s, .5 - 1e-9), tr.at(s, .5)
        self.assertEqual((before["side"], after["side"]), ("a", "b"))
        self.assertAlmostEqual(before["sx"], tr.ZOOM_SCALE, places=4)
        self.assertAlmostEqual(after["sx"], 1 / tr.ZOOM_SCALE, places=4)
        sizes = [tr.at(s, p / 200)["sx"] for p in range(1, 200)]
        a, b = sizes[:99], sizes[100:]
        self.assertTrue(all(y >= x for x, y in zip(a, a[1:])) and all(y >= x for x, y in zip(b, b[1:])))
        self.assertAlmostEqual(tr.at(s, 0)["sx"], 1)
        self.assertAlmostEqual(tr.at(s, 1)["sx"], 1)

    def test_a_whip_leaves_one_way_and_arrives_from_the_other(self):
        s = settings(kind="whip_left")
        self.assertAlmostEqual(tr.at(s, .5 - 1e-9)["cx"], .5 - tr.PAN_DISTANCE, places=4)   # gone off left
        self.assertAlmostEqual(tr.at(s, .5)["cx"], .5 + tr.PAN_DISTANCE, places=4)          # in from the right
        self.assertEqual(tr.at(s, .5)["blur_angle"], 180.0)
        up = tr.at(settings(kind="whip_up"), .5 - 1e-9)
        self.assertGreater(up["cy"], .5)                                                    # y up, as Fusion's

    def test_blur_follows_the_speed_and_peaks_at_the_cut(self):
        for kind in ("zoom_in", "whip_right", "spin_left", "stretch_x"):
            s = settings(kind=kind, blur=1)
            blurs = [tr.at(s, (f + .5) / 24, 24)["blur"] for f in range(24)]
            peak = max(range(24), key=lambda f: blurs[f])
            self.assertIn(peak, (11, 12), kind)
            self.assertLess(blurs[0], blurs[peak] / 4, kind)
        self.assertEqual(tr.at(settings(kind="spin_left", blur=0), .45, 24)["blur"], 0)

    def test_the_plan_holds_each_side_still_while_the_other_shows(self):
        plan = tr.plan(settings(), 15)
        self.assertEqual(plan["cut"], 7)
        a, b = plan["a"]["XSize"], plan["b"]["XSize"]
        self.assertEqual(len(a), 15)
        self.assertTrue(all(v == a[6] for v in a[7:]))          # a rests on its last after the cut
        self.assertTrue(all(v == b[7] for v in b[:7]))          # b waits on its first before it
        self.assertEqual(plan["a"]["Length"][10], 0.0)          # and is never blurred unseen
        self.assertEqual(tr.cut_frame(14), 7)
        self.assertEqual(tr.cut_frame(2), 1)

    def test_shake_settles_at_both_ends(self):
        s = settings(kind="shake", amount=2)
        for p in (0, 1):
            v = tr.at(s, p)
            self.assertAlmostEqual(v["cx"], .5)
            self.assertAlmostEqual(v["sx"], 1)
        self.assertGreater(tr.at(s, .5)["sx"], 1.1)


class LightTests(unittest.TestCase):
    def test_the_swell_peaks_at_the_cut_and_dies_at_the_ends(self):
        for feel in tr.FEELS:
            self.assertAlmostEqual(tr.swell(0, feel), 0)
            self.assertAlmostEqual(tr.swell(1, feel), 0)
            self.assertAlmostEqual(tr.swell(.5, feel), 1)
        self.assertLess(tr.swell(.35, "hit"), tr.swell(.35, "ease"))        # a spike
        self.assertGreater(tr.swell(.35, "swing"), tr.swell(.35, "ease"))   # a long swell

    def test_the_clips_cross_under_the_light(self):
        leak = settings(kind="light_leak")
        self.assertEqual(tr.at(leak, .2)["mix"], 0.0)
        self.assertAlmostEqual(tr.at(leak, .5)["mix"], .5)
        self.assertEqual(tr.at(leak, .8)["mix"], 1.0)
        flash = settings(kind="flash")
        self.assertEqual((tr.at(flash, .499)["mix"], tr.at(flash, .5)["mix"]), (0.0, 1.0))     # a cut, under the flash
        self.assertGreater(tr.at(flash, .5)["gain"], 3)
        self.assertLess(tr.at(flash, .35)["gain"], tr.at(leak, .35)["gain"] + 1)            # and a quick one

    def test_amount_and_blur_scale_the_light(self):
        a, b = tr.at(settings(kind="dreamy", blur=1), .5), tr.at(settings(kind="dreamy", blur=.5), .5)
        self.assertAlmostEqual(a["soft"], 2 * b["soft"])
        self.assertLessEqual(tr.at(settings(kind="film_burn", amount=2), .5)["glow"], 1.0)

    def test_the_plan_lists_every_frame(self):
        plan = tr.plan(settings(kind="light_leak", color="cool"), 12)
        self.assertEqual(plan["group"], "light")
        self.assertEqual(len(plan["mix"]), 12)
        self.assertLess(plan["sweep"][0], plan["sweep"][-1])                # the light drifts across
        self.assertEqual(tr.clean({"color": "cool"})["color"], "cool")
        self.assertEqual(tr.clean({"color": "plaid"})["color"], "warm")


class WipeTests(unittest.TestCase):
    def test_the_edge_sweeps_from_before_the_start_to_past_the_end(self):
        for kind in ("liquid", "ink", "circle", "bars", "swipe"):
            for feel in tr.FEELS:
                s = settings(kind=kind, feel=feel)
                first, last = tr.at(s, 0), tr.at(s, 1)
                self.assertLess(first["m1"] + first["soft"] / 2, 1e-9, (kind, feel))     # nothing shown yet
                self.assertGreater(last["m2"] - last["soft"] / 2, 1 - 1e-9, (kind, feel))  # all of it shown

    def test_the_colour_band_leads_by_its_width(self):
        v = tr.at(settings(kind="swipe", amount=2), .5)
        self.assertAlmostEqual(v["m1"] - v["m2"], 2 * tr.KINDS["swipe"]["band"])
        self.assertLess(tr.at(settings(kind="circle", blur=0), .5)["soft"], tr.at(settings(kind="circle", blur=1), .5)["soft"])

    def test_a_swing_never_runs_backwards_past_the_ends(self):
        s = settings(kind="swipe", feel="swing")
        prog = [tr.at(s, p / 50)["m1"] for p in range(51)]
        self.assertGreaterEqual(min(prog), prog[0])
        self.assertLessEqual(max(prog), prog[-1])


class ColourTests(unittest.TestCase):
    def test_any_colour_as_well_as_the_four(self):
        self.assertEqual(tr.clean({"color": "#1A2B3C"})["color"], "#1a2b3c")
        for bad in ("#12345", "#gggggg", "red", 7, None):
            self.assertEqual(tr.clean({"color": bad})["color"], "warm", bad)
        self.assertEqual(tr.rgb("cool"), tr.COLORS["cool"])
        self.assertEqual(tr.rgb("#ff8000"), (1.0, 128 / 255, 0.0))
        self.assertEqual(tr.rgb("nonsense"), tr.COLORS["warm"])

    def test_a_burn_deepens_any_colour(self):
        r, g, b = tr.burn_dark("#00ff00")
        self.assertEqual((r, b), (0.0, 0.0))
        self.assertAlmostEqual(g, 0.25)


class GlitchTests(unittest.TestCase):
    def test_quiet_at_the_ends_wild_at_the_cut(self):
        for kind in ("vhs", "bad_signal", "digital"):
            s = settings(kind=kind)
            for p in (0, 1):
                v = tr.at(s, p, 24)
                self.assertAlmostEqual(v["split"], 0, places=9)
                self.assertAlmostEqual(v["tear"], 0, places=9)
                self.assertEqual(v["block"], 1.0, kind)
                self.assertAlmostEqual(v["gain"], 1)
            peak = max(abs(tr.at(s, (f + .5) / 24, 24)["tear"]) for f in range(24))
            self.assertGreater(peak, tr.KINDS[kind]["tear"] * .4)

    def test_every_frame_its_own_dice_the_same_each_time(self):
        s = settings(kind="bad_signal")
        a = [tr.at(s, (f + .5) / 24, 24)["split"] for f in range(24)]
        self.assertEqual(a, [tr.at(s, (f + .5) / 24, 24)["split"] for f in range(24)])
        self.assertGreater(len({round(v, 6) for v in a}), 12)

    def test_the_cut_stutters_then_settles(self):
        for frames in (12, 18, 24, 36):
            mix = tr.plan(settings(kind="bad_signal"), frames)["mix"]
            self.assertEqual((mix[0], mix[-1]), (0.0, 1.0))
            flips = sum(1 for x, y in zip(mix, mix[1:]) if x != y)
            self.assertGreater(flips, 1, frames)                         # back and forth, not one clean cut
        self.assertTrue(any(b > 1 for b in tr.plan(settings(kind="digital"), 24)["block"]))


class PaperTests(unittest.TestCase):
    def test_a_tear_starts_whole_and_ends_gone(self):
        s = settings(kind="paper_tear", feel="swing")
        start, end = tr.at(s, 0), tr.at(s, 1)
        self.assertEqual((start["lx"], start["rx"], start["fringe"]), (.5, .5, 0.0))      # whole, no white line
        self.assertLessEqual(end["lx"], .5 - tr.TEAR_DISTANCE + 1e-9)
        self.assertGreaterEqual(end["rx"], .5 + tr.TEAR_DISTANCE - 1e-9)
        self.assertTrue(all(tr.at(s, p / 50)["open"] >= 0 for p in range(51)))    # a swing never closes it past whole

    def test_the_wipes_start_clean_and_end_clean(self):
        for kind in ("burn_away", "brush", "grunge"):
            for feel in tr.FEELS:
                s = settings(kind=kind, feel=feel)
                first, last = tr.at(s, 0), tr.at(s, 1)
                scorch = 1.5 if kind == "burn_away" else .5
                self.assertLess(first["m0"] + first["soft"] * scorch, 1e-9, (kind, feel))
                self.assertGreater(last["m2"] - last["soft"] / 2, 1 - 1e-9, (kind, feel))
                for v in (first["dirt"], last["dirt"], last["jx"]):
                    self.assertAlmostEqual(v, 0.0, places=9)

    def test_a_burn_chars_ahead_of_its_ember(self):
        v = tr.at(settings(kind="burn_away", amount=2), .5)
        self.assertAlmostEqual(v["m0"] - v["m1"], 2 * tr.KINDS["burn_away"]["char"])
        self.assertAlmostEqual(v["m1"] - v["m2"], 2 * tr.KINDS["burn_away"]["band"])

    def test_grunge_steps_twelve_a_second(self):
        plan = tr.plan(settings(kind="grunge", length=1.0), 60)
        changes = sum(1 for x, y in zip(plan["m1"], plan["m1"][1:]) if x != y)
        self.assertEqual(changes, 11)                                            # 12 steps
        self.assertEqual(len(set(plan["seethe"])), 12)
        self.assertGreater(max(plan["dirt"]), 0)

    def test_paper_kinds_build_on_their_own_chains(self):
        for kind in ("paper_tear", "burn_away", "brush", "grunge"):
            self.assertEqual(tr.plan(settings(kind=kind), 20)["group"], "paper")
        self.assertIs(tr.KINDS["paper_tear"]["color"], False)


class DistortTests(unittest.TestCase):
    def test_plain_at_the_ends_bent_at_the_cut(self):
        bends = {"twirl": "angle_a", "ripple": "amp", "kaleido": "kaleido", "bulge": "strength", "pinch": "strength",
                 "warp": "refract"}
        for kind, key in bends.items():
            for feel in tr.FEELS:
                s = settings(kind=kind, feel=feel)
                first, last = tr.at(s, 0), tr.at(s, 1)
                self.assertAlmostEqual(first[key], 0, places=9, msg=(kind, feel))
                self.assertEqual((first["mix"], last["mix"]), (0.0, 1.0))
                self.assertAlmostEqual(first["soft"], 0, places=9)
            self.assertGreater(abs(tr.at(settings(kind=kind), .5 - 1e-6)[key]), 0, kind)

    def test_a_twirl_turns_one_way_across_the_cut(self):
        s = settings(kind="twirl")
        a = [tr.at(s, p / 100)["angle_a"] for p in range(101)]
        b = [tr.at(s, p / 100)["angle_b"] for p in range(101)]
        self.assertTrue(all(y >= x for x, y in zip(a, a[1:])))          # winding up...
        self.assertTrue(all(y >= x for x, y in zip(b, b[1:])))          # ... and on, unwinding
        self.assertAlmostEqual(b[-1], 0)
        self.assertAlmostEqual(a[50] - b[50], 2 * tr.KINDS["twirl"]["degrees"])

    def test_a_bulge_never_turns_inside_out(self):
        self.assertLessEqual(tr.at(settings(kind="bulge", amount=2), .5)["strength"], tr.BULGE_MAX)
        self.assertLessEqual(tr.at(settings(kind="pinch", amount=2), .5)["strength"], tr.PINCH_MAX)


SPLITS = tuple(k for k in tr.ORDER if tr.group(k) == "split")


class SplitTests(unittest.TestCase):
    def test_the_first_frame_is_the_outgoing_clip_and_the_last_the_incoming(self):
        for kind in SPLITS:
            for feel in tr.FEELS:
                plan = tr.plan(settings(kind=kind, feel=feel), 13)
                self.assertAlmostEqual(plan["border"][0], 0.0, places=12)
                self.assertAlmostEqual(plan["border"][-1], 0.0, places=12)
                for piece in plan["pieces"]:
                    arriving = piece["src"] == "b"
                    hx, hy, hw, hh = piece["home"]
                    home = [abs(piece["x"][f] - hx) < 1e-9 and abs(piece["y"][f] - hy) < 1e-9
                            and abs(piece["s"][f] - 1) < 1e-9 and piece["sw"][f] == piece["sh"][f] == 1
                            for f in (0, -1)]
                    self.assertEqual(home, [not arriving, arriving], (kind, feel))
                    # Nowhere to be seen as it arrives (or once it's left): no size, or wholly off the frame.
                    f = 0 if arriving else -1
                    x, y = piece["x"][f], piece["y"][f]
                    off = x + hw / 2 <= 1e-9 or x - hw / 2 >= 1 - 1e-9 or y + hh / 2 <= 1e-9 or y - hh / 2 >= 1 - 1e-9
                    if "half" in piece:     # a corner-to-corner half: gone once it's moved half the frame each way
                        off = abs(x - hx) >= 0.5 and abs(y - hy) >= 0.5
                    self.assertTrue(piece["s"][f] * piece["sw"][f] * piece["sh"][f] == 0 or off, (kind, feel))

    def test_the_pieces_cover_the_frame(self):
        for kind in SPLITS:
            shapes = tr.pieces(kind)
            for src in {p["src"] for p in shapes}:
                mine = [p for p in shapes if p["src"] == src]
                share = [0.5 if "half" in p else 1.0 for p in mine]       # a diagonal split's halves: half each
                self.assertAlmostEqual(sum(k * w * h for k, (_x, _y, w, h) in zip(share, (p["home"] for p in mine))),
                                       1.0, msg=kind)
            orders = [p["order"] for p in shapes]
            if tr.KINDS[kind]["spread"]:            # the first at once, the last at the end
                self.assertEqual((min(orders), max(orders)), (0.0, 1.0), kind)

    def test_one_after_another(self):
        plan = tr.plan(settings(kind="panels"), 24)
        arrive = [next(f for f, y in enumerate(p["y"]) if abs(y - p["home"][1]) < 1e-9) for p in plan["pieces"]]
        self.assertEqual(arrive, sorted(arrive))
        self.assertLess(arrive[0], arrive[-1])
        dirs = [p["dir"] for p in tr.pieces("strips")]
        self.assertEqual(dirs[:2], [(-1, 0), (1, 0)])                   # turn about
        self.assertEqual({p["dir"] for p in tr.pieces("split_open")}, {(-1, 0), (1, 0)})
        push = tr.pieces("push_left")                                     # the outgoing one out left, the incoming in from the right
        self.assertEqual([(p["src"], p["dir"]) for p in push], [("a", (-1, 0)), ("b", (1, 0))])
        self.assertEqual({p["order"] for p in tr.pieces("checker")}, {0.0, 1.0})

    def test_a_zoom_push_shows_both_screens_side_by_side_mid_way(self):
        v = tr.at(settings(kind="zoom_push"), .5)
        self.assertEqual(v["s"], [tr.KINDS["zoom_push"]["scale"]] * 2)
        (ax, bx) = v["x"]
        self.assertAlmostEqual(bx - ax, tr.KINDS["zoom_push"]["scale"] + tr.PUSH_GAP)

    def test_a_diagonal_split_moves_its_halves_apart_along_the_other_diagonal(self):
        a, b = tr.pieces("split_diag")
        self.assertEqual((a["half"], b["half"]), ("ul", "lr"))
        v = tr.at(settings(kind="split_diag"), 0.7)
        self.assertLess(v["x"][0], 0.5)
        self.assertGreater(v["y"][0], 0.5)
        self.assertAlmostEqual(v["x"][0] - 0.5, 0.5 - v["y"][0])
        self.assertAlmostEqual(v["x"][0] + v["x"][1], 1.0)

    def test_a_squeeze_shares_the_frame_without_a_gap(self):
        for kind, axis in (("squeeze", "x"), ("squeeze_up", "y")):
            for feel in tr.FEELS:
                for p in (0.0, 0.2, 0.5, 0.8, 1.0):
                    v = tr.at(settings(kind=kind, feel=feel), p)
                    sizes = v["sw"] if axis == "x" else v["sh"]
                    middles = v["x"] if axis == "x" else v["y"]
                    self.assertAlmostEqual(sum(sizes), 1.0, msg=(kind, feel, p))
                    # The outgoing one's far edge stays on the frame's; the two meet.
                    a_mid, b_mid = middles
                    a_size, b_size = sizes
                    if axis == "x":
                        self.assertAlmostEqual(a_mid - a_size / 2, 0.0)
                        self.assertAlmostEqual(a_mid + a_size / 2, b_mid - b_size / 2)
                    else:
                        self.assertAlmostEqual(a_mid + a_size / 2, 1.0)
                        self.assertAlmostEqual(a_mid - a_size / 2, b_mid + b_size / 2)

    def test_the_line_swells_with_amount_and_the_push_settles(self):
        a, b = tr.at(settings(kind="grid", amount=2), .5), tr.at(settings(kind="grid", amount=1), .5)
        self.assertAlmostEqual(a["border"], 2 * b["border"])
        plan = tr.plan(settings(kind="split_open"), 10)
        self.assertAlmostEqual(plan["push"][0], 1 + tr.PUSH)
        self.assertAlmostEqual(plan["push"][-1], 1.0)
        self.assertEqual(set(tr.plan(settings(kind="grid"), 10)["push"]), {1.0})
        self.assertEqual(max(max(p["blur"]) for p in tr.plan(settings(kind="mosaic", blur=0), 10)["pieces"]), 0.0)


class TurnTests(unittest.TestCase):
    TURNS = tuple(k for k in tr.ORDER if tr.group(k) == "turn" and tr.KINDS[k]["style"] != "louver")

    def test_flat_at_the_ends(self):
        for kind in self.TURNS:
            for feel in tr.FEELS:
                plan = tr.plan(settings(kind=kind, feel=feel), 15)
                self.assertEqual((plan["rot_a"][0], plan["show_a"][0]), (0.0, 1.0), (kind, feel))
                self.assertAlmostEqual(plan["rot_b"][-1], 0.0, msg=(kind, feel))
                self.assertEqual(plan["show_b"][-1], 1.0)
                if tr.KINDS[kind]["style"] == "door":
                    self.assertEqual((plan["shade"][0], plan["shade"][-1]), (1 - tr.SHADE, 1.0))

    def test_louver_slats_turn_top_first_and_land_flat(self):
        plan = tr.plan(settings(kind="louvers"), 24)
        self.assertEqual(plan["rot_a"][0], [0.0] * tr.LOUVERS)
        self.assertEqual(plan["show_b"][-1], [1.0] * tr.LOUVERS)
        for v in plan["rot_b"][-1]:
            self.assertAlmostEqual(v, 0.0)
        mid = plan["show_b"][12]
        self.assertEqual(mid, sorted(mid, reverse=True))          # the top slats over first
        self.assertGreater(sum(mid), 0)
        self.assertLess(sum(mid), tr.LOUVERS)

    def test_a_flip_is_edge_on_at_the_cut(self):
        before, after = tr.at(settings(kind="flip"), .5 - 1e-9), tr.at(settings(kind="flip"), .5)
        self.assertAlmostEqual(before["rot_a"], 90, places=4)
        self.assertAlmostEqual(after["rot_b"], -90, places=4)
        self.assertEqual((before["show_b"], after["show_a"]), (0.0, 0.0))

    def test_a_cube_keeps_its_faces_square_on_to_each_other(self):
        for p in (.1, .4, .7):
            v = tr.at(settings(kind="cube", feel="swing"), p)
            self.assertAlmostEqual(abs(v["rot_a"] - v["rot_b"]), 90)
        rig = tr.turn_rig("cube", 1.2)
        self.assertAlmostEqual(1.2 / (1.2 * rig["zmove"] + rig["zpivot"]), 1.0)     # the face full size at rest
        self.assertLess(tr.at(settings(kind="cube", amount=2), .3)["persp"], tr.at(settings(kind="cube"), .3)["persp"])


class PackTests(unittest.TestCase):
    def test_a_dip_is_all_colour_at_the_cut_whatever_the_amount(self):
        for amount in (0.25, 1.0, 2.0):
            self.assertAlmostEqual(tr.at(settings(kind="dip", amount=amount), .5)["glow"], 1.0)
        self.assertGreater(tr.at(settings(kind="dip", amount=2), .3)["glow"], tr.at(settings(kind="dip"), .3)["glow"])

    def test_a_shutter_shuts_at_the_cut(self):
        s = settings(kind="shutter")
        self.assertAlmostEqual(tr.at(s, .5)["open"], 0.0)
        self.assertEqual((tr.at(s, 0)["open"], tr.at(s, 1)["open"]), (1.0, 1.0))
        self.assertEqual((tr.at(s, .4)["mix"], tr.at(s, .6)["mix"]), (0.0, 1.0))

    def test_tunnel_copies_and_chroma_only_around_the_cut(self):
        for kind, key in (("tunnel", "nest"), ("chroma_zoom", "chroma")):
            s = settings(kind=kind)
            self.assertAlmostEqual(tr.at(s, 0)[key], 0.0)
            self.assertAlmostEqual(tr.at(s, 1)[key], 0.0)
            self.assertGreater(tr.at(s, .5)[key], 0)
        self.assertEqual(tr.at(settings(kind="zoom_in"), .5)["nest"], 0.0)

    def test_a_page_lands_flat(self):
        plan = tr.plan(settings(kind="page_turn"), 20)
        self.assertEqual(plan["show_a"][0] + plan["show_b"][0], 1.0)
        self.assertAlmostEqual(plan["shade"][0], 1.0)
        self.assertAlmostEqual(plan["shade"][-1], 1.0)
        self.assertLess(min(plan["shade"]), 1 - tr.PAGE_SHADE * 0.9)


class MoreGlitchTests(unittest.TestCase):
    def test_a_tv_goes_out_at_the_cut_and_comes_back(self):
        plan = tr.plan(settings(kind="tv_off"), 24)
        self.assertGreater(plan["sy"][0], .9)
        self.assertLess(plan["sy"][11], .05)                                    # a bright line by just before the cut
        self.assertGreater(plan["gain"][11], plan["gain"][0])
        mid = tr.at(settings(kind="tv_off"), .5 - 1e-9)
        self.assertAlmostEqual(mid["gain"], 0.0, places=6)
        self.assertAlmostEqual(tr.at(settings(kind="tv_off"), 1)["sy"], 1.0)

    def test_pixelate_blocks_peak_at_the_cut(self):
        s = settings(kind="pixelate")
        self.assertEqual((tr.at(s, 0)["block"], tr.at(s, 1)["block"]), (1.0, 1.0))
        self.assertEqual(tr.at(s, .5)["block"], tr.KINDS["pixelate"]["block"])


class FitTests(unittest.TestCase):
    def test_centred_when_both_sides_have_half(self):
        self.assertEqual(trr.fit(24, (12, 12)), (24, "center"))
        self.assertEqual(trr.fit(24, (None, None)), (24, "center"))       # stills: as many as wanted

    def test_to_one_side_when_only_one_has_room(self):
        self.assertEqual(trr.fit(24, (30, 0)), (24, "right"))             # all after the cut: the left's frames
        self.assertEqual(trr.fit(24, (0, 30)), (24, "left"))

    def test_shorter_or_not_at_all(self):
        self.assertEqual(trr.fit(24, (5, 9)), (10, "center"))
        self.assertIsNone(trr.fit(24, (1, 1)))


class Item:
    def __init__(self, name, start, end, track=1, pool=True, frames=1000, src=(100, 199), fps=24, sel=False):
        self.name, self.start, self.end, self.track = name, start, end, track
        self.pool, self.frames, self.src, self.fps, self.sel = pool, frames, src, fps, sel
        self.comp = None

    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetUniqueId(self): return self.name
    def GetTrackTypeAndIndex(self): return ["video", self.track]
    def GetSourceStartFrame(self): return self.src[0]
    def GetSourceEndFrame(self): return self.src[1]
    def GetSpeed(self): return {"Percentage": 100}
    def GetDuration(self): return self.end - self.start
    def GetFusionCompByIndex(self, _i): return self.comp

    def GetMediaPoolItem(self):
        if not self.pool:
            return None
        item = self

        class Pool:
            def GetClipProperty(self, key=None):
                props = {"Type": "Video", "FPS": item.fps, "Frames": item.frames}
                return props[key] if key else props
        return Pool()


class Timeline:
    def __init__(self, items):
        self.items = items

    def GetItemListInTrack(self, _type, track):
        return [i for i in self.items if i.track == track]

    def GetSelectedClips(self):
        return [i for i in self.items if i.sel]

    def GetSetting(self, _key):
        return "24"

    def GetName(self):
        return "Edit"


class CutTests(unittest.TestCase):
    def layout(self):
        a = Item("A", 0, 48, sel=True)
        b = Item("B", 48, 96, sel=True, src=(0, 47))            # no frames before its start
        c = Item("C", 96, 144)
        d = Item("D", 200, 240)                                  # after a gap: no cut
        on = Item("Cross Dissolve", 90, 102, pool=False)         # a transition over the B|C cut
        return Timeline([a, on, b, c, d]), on

    def test_transitions_are_told_from_clips_by_overlapping(self):
        timeline, on = self.layout()
        clips, transitions = trr.track_layout(timeline, 1)
        self.assertEqual([c.GetName() for c in clips], ["A", "B", "C", "D"])
        self.assertEqual(transitions, [on])

    def test_the_cuts_between_selected_clips(self):
        timeline, _on = self.layout()
        found = trr.cuts(timeline)
        self.assertEqual([c["id"] for c in found], ["1:48"])
        self.assertEqual(found[0]["room"], (1000 - 1 - 199, 0))   # A's frames after its end, B's before its start

    def test_one_clip_selected_gives_the_cuts_at_its_ends(self):
        timeline, on = self.layout()
        for item in timeline.items:
            item.sel = item.name == "C"
        found = trr.cuts(timeline)
        self.assertEqual([c["id"] for c in found], ["1:96"])                  # D is after a gap
        self.assertIs(found[0]["items"][2], on)
        self.assertEqual(found[0]["existing"], "Cross Dissolve")
        self.assertIsNone(found[0]["buddy"])                                   # not one of Buddy's

    def test_handles_count_in_timeline_frames(self):
        clip = Item("x", 0, 10, fps=48, frames=500, src=(100, 399))
        self.assertEqual(trr.handles(clip, 24.0, True), 50)                  # 100 file frames at 48 fps
        self.assertEqual(trr.handles(clip, 24.0, False), 50)
        self.assertIsNone(trr.handles(Item("t", 0, 10, pool=False), 24.0, True))

    def test_keys_only_where_values_change(self):
        keys = trr._keys([1, 1, 1, 2, 3, 3, 3])
        self.assertEqual(sorted(keys), [0, 2, 3, 4, 6])
        self.assertEqual(keys[4], {"value": 3.0})


class PageTests(unittest.TestCase):
    def page(self):
        from pages.text_animator.page import AnimationPage
        names = ("on_tr_option", "on_tr_apply", "_done_tr_apply", "on_tr_remove", "_done_tr_remove", "achieve")
        page = type("Page", (), {n: getattr(AnimationPage, n) for n in names})()
        page.settings, page.emitted, page.started, page.working = {}, [], [], "transition"
        page._save = page.settings.__setitem__
        page.emit = lambda name, data: page.emitted.append((name, data))
        page._start = lambda what, job, done: page.started.append(what)
        page._push_state = page._read_after = lambda: None
        return page

    def test_options_are_kept_clean(self):
        page = self.page()
        page.on_tr_option({"kind": "spin_left"})
        page.on_tr_option({"length": 50, "feel": "bogus"})
        self.assertEqual(page.settings["transition"], {**tr.DEFAULTS, "kind": "spin_left", "length": 3.0})

    def test_apply_and_remove_need_cuts(self):
        page = self.page()
        page.on_tr_apply({"cuts": []})
        page.on_tr_apply({"cuts": "1:48"})
        page.on_tr_apply({"cuts": ["1:48"]})
        page.on_tr_remove({"cuts": ["1:48"]})
        self.assertEqual(page.started, ["transition", "untransition"])

    def test_what_happened_is_said(self):
        page = self.page()
        page._done_tr_apply({"applied": 2, "shorter": 1, "failed": [("A → B", "no room")]}, None)
        self.assertIn("2 cuts", page.emitted[0][1]["text"])
        self.assertIn("shorter", page.emitted[0][1]["text"])
        self.assertEqual(page.emitted[1][0], "alert")
        page._done_tr_remove(0, None)
        self.assertEqual(page.emitted[-1][1]["text"], "There were no transitions to take off.")


class PunchTests(unittest.TestCase):
    def test_a_punch_flashes_and_jolts_at_the_cut_only(self):
        mid, end = tr.at(settings(kind="punch"), .5), tr.at(settings(kind="punch"), 0.0)
        self.assertAlmostEqual(mid["flash"], 1 + tr.KINDS["punch"]["flash"])
        self.assertAlmostEqual(end["flash"], 1.0)
        self.assertEqual((end["cx"], end["cy"]), (0.5, 0.5))
        near = tr.at(settings(kind="punch"), .45)
        self.assertNotEqual((near["cx"], near["cy"]), (0.5, 0.5))
        self.assertEqual(tr.at(settings(kind="zoom_in"), .5)["flash"], 1.0)      # a plain zoom: none
        plan = tr.plan(settings(kind="punch"), 20)
        self.assertGreater(max(plan["a"]["Flash"]), 1.5)

    def test_a_strobe_swaps_every_frame_through_the_middle(self):
        mixes = [tr.glitch_at(settings(kind="strobe"), (k + 0.5) / 24, 24)["mix"] for k in range(24)]
        middle = mixes[9:15]
        self.assertTrue(all(a != b for a, b in zip(middle, middle[1:])), mixes)
        self.assertEqual((mixes[0], mixes[-1]), (0.0, 1.0))

    def test_zoom_and_spin_blurs_peak_at_the_cut(self):
        for kind in ("zoom_blur", "spin_blur"):
            soft = [tr.light_at(settings(kind=kind), p)["soft"] for p in (0.0, 0.25, 0.5)]
            self.assertEqual(soft[0], 0.0)
            self.assertLess(soft[1], soft[2])

    def test_zoom_out_in_pulls_back_then_pushes_in(self):
        before, after = tr.at(settings(kind="zoom_out_in"), 0.4), tr.at(settings(kind="zoom_out_in"), 0.6)
        self.assertLess(before["sx"], 1.0)
        self.assertLess(after["sx"], 1.0)
        self.assertAlmostEqual(tr.at(settings(kind="zoom_out_in"), 1.0)["sx"], 1.0)

    def test_a_cube_zoom_pulls_the_camera_back_at_the_cut_only(self):
        pulls = [tr.turn_at(settings(kind="cube_zoom"), p)["pull"] for p in (0.0, 0.5, 1.0)]
        self.assertEqual([round(v, 9) for v in pulls], [1.0, 1 + tr.KINDS["cube_zoom"]["pull"], 1.0])
        self.assertEqual(tr.turn_at(settings(kind="cube"), 0.5)["pull"], 1.0)

    def test_chaos_jumps_and_flashes_negative_only_around_the_cut(self):
        rows = [tr.glitch_at(settings(kind="chaos"), (k + 0.5) / 24, 24) for k in range(24)]
        self.assertTrue(any(r["neg"] for r in rows[8:16]))
        self.assertTrue(any(abs(r["jx"]) > 0.005 for r in rows[8:16]))
        self.assertEqual((rows[0]["neg"], round(rows[0]["jx"], 4)), (0.0, 0.0))
        quiet = [tr.glitch_at(settings(kind="vhs"), (k + 0.5) / 24, 24) for k in range(24)]
        self.assertFalse(any(r["neg"] or r["jx"] or r["jy"] for r in quiet))          # the others don't

    def test_a_film_roll_runs_up_with_its_holes(self):
        a, b = tr.at(settings(kind="film_roll"), 0.4), tr.at(settings(kind="film_roll"), 0.6)
        self.assertGreater(b["hole_y"], a["hole_y"])                    # the strip runs up
        self.assertEqual(a["hole_y"], a["y"][0])
        self.assertEqual(a["border"], 0.0)                              # black between the frames, no line
        mid = tr.at(settings(kind="film_roll"), 0.5)
        self.assertLess(mid["hole_x"] + mid["hole_w"] / 2, 0.5)         # shrunk: the holes are on the frame
        end = tr.at(settings(kind="film_roll"), 1.0)
        self.assertGreater(end["hole_x"] - end["hole_w"] / 2, 0.5)      # full size: they're past its edges

    def test_an_iris_closes_to_nothing_at_the_cut(self):
        v = [tr.wipe_at(settings(kind="iris"), p) for p in (0.0, 0.5, 1.0)]
        self.assertEqual([round(x["open"], 9) for x in v], [1.0, 0.0, 1.0])
        self.assertEqual([x["mix"] for x in v], [0.0, 1.0, 1.0])


class ParityTests(unittest.TestCase):
    """transitions.js mirrors transition.py: the same numbers from both."""

    def test_js_matches_python(self):
        import os
        import shutil
        import subprocess
        node = shutil.which("node")
        if node is None:
            self.skipTest("no node")
        web = os.path.join(os.path.dirname(os.path.abspath(tr.__file__)), "web", "transitions.js")
        src = open(web, encoding="utf-8").read()
        maths = src[:src.index("// ---- the two sample pictures")]
        cases = [(k, f, p) for k in tr.ORDER for f in tr.FEELS for p in (0.05, 0.3, 0.49, 0.5, 0.7, 0.95)]
        script = maths + "\nconst out = [];\n" + "".join(
            f"out.push(trAt({{kind: '{k}', feel: '{f}', length: .6, amount: 1.3, blur: .8}}, {p}, 18));\n"
            for k, f, p in cases) + "console.log(JSON.stringify(out));"
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
        self.addCleanup(os.remove, f.name)
        got = json.loads(subprocess.run([node, f.name], capture_output=True, text=True, check=True).stdout)
        for (k, f, p), js in zip(cases, got):
            py = tr.at(tr.clean({"kind": k, "feel": f, "length": .6, "amount": 1.3, "blur": .8}), p, 18)
            keys = {"light": tuple(key for key in py if key != "side"), "wipe": tuple(key for key in py if key != "side"),
                    "glitch": tuple(key for key in py if key != "side"),
                    "motion": ("sx", "sy", "cx", "cy", "deg", "blur", "nest", "chroma", "flash"),
                    "paper": tuple(key for key in py if key != "side"),
                    "distort": tuple(key for key in py if key != "side"),
                    "split": tuple(key for key in py if key != "side"),
                    "turn": tuple(key for key in py if key != "side")}[tr.group(k)]
            for key in keys:
                pys, jss = (py[key], js[key]) if isinstance(py[key], list) else ([py[key]], [js[key]])
                self.assertEqual(len(pys), len(jss), (k, key))
                for a, b in zip(pys, jss):
                    self.assertTrue(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12), (k, f, p, key))
            self.assertEqual(py["side"], js["side"])


if __name__ == "__main__":
    unittest.main()
