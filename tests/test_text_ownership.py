"""Whose it is: each feature that animates or styles a Text+ takes off and replaces only
what it put there itself - never another feature's Follower, Character Level Styling or
the person's own keys - whatever order they're used in.

Measured on Studio 21.1 (2026-10-09): a Text+'s text takes ONE modifier. A Follower asked
for where Character Level Styling is deletes the styling (and the other way round), and
reading the words of a Follower with styling chained behind it froze Resolve. So nothing
attaches a Follower over another modifier, and nothing reads or takes apart a chain. The
fakes here are as unkind: a second modifier replaces the first and empties the words, and
a read through a chain is written down (comp.chain_reads) and raises."""

import unittest
from unittest import mock

import _paths  # noqa: F401
from core import i18n
from pages.text_animator import highlight as hl
from pages.text_animator import highlight_resolve as hr
from pages.text_animator import motion, motion_resolve as mr, options
from pages.text_animator.animation_engine import FusionAnimationEngine as E
from pages.text_animator.titles import titles_resolve
from test_letter_animations import Comp
from test_motion_presets import PRESETS, Item, chain
from test_motion_units import WORDS, Timeline, controller, on_timeline, text_clip, text_tool

try:
    from pages.text_animator import text_plus
    from pages.text_animator.page import AnimationPage, apply_summary, held_note, highlight_summary, on_clips
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

CLS = "StyledTextCLS"       # Character Level Styling's registry ID


def styled(comp):
    """Character Level Styling on the fake Text+'s text, as the person puts it on."""
    comp.text_plus.AddModifier("StyledText", CLS)
    return E.text_source(comp.text_plus)


def curve(tool, input_name, keys, name=None):
    """A curve keyed on one of a tool's inputs - by the person in Fusion, or one of Resolve's own titles'."""
    spline = tool.comp.add("BezierSpline", name or f"{tool.Name}{input_name}")
    spline.keys.update(keys)
    getattr(tool, input_name).ConnectTo(spline.Value)
    return spline


def earlier(comp):
    """The Text+ as a Buddy before the record left it: animated, nothing written down."""
    comp.text_plus.data.clear()
    return comp


class TimelineAnimationTakesOffOnlyItsOwn(unittest.TestCase):
    """Subtitles > Timeline animation (animation_engine)."""

    def test_what_it_put_on_is_written_on_the_text_plus(self):
        comp = Comp()
        self.assertTrue(E.apply_pop_preset_to_clip(comp.text_plus, comp)[0])
        E.mark_preset(comp.text_plus, "Pop (Scale)", "Fast", "From Left", 30.0)
        self.assertEqual(E.animation_on(comp.text_plus)["inputs"], ["LayoutSize"])
        self.assertEqual(E.preset_on(comp.text_plus), ("Pop (Scale)", "Fast", "From Left", 30.0))
        removed, _logs = E.remove_animations_from_clip(comp.text_plus, comp)
        self.assertTrue(removed)
        self.assertEqual(comp.ids(), ["TextPlus"])
        self.assertIsNone(E.preset_on(comp.text_plus))
        self.assertEqual(E.remove_animations_from_clip(comp.text_plus, comp)[0], False)    # nothing left to take off

    def test_once_it_has_handled_a_clip_it_goes_by_its_record_not_by_looks(self):
        comp = Comp()
        E.apply_fade_preset_to_clip(comp.text_plus, comp)
        E.remove_animations_from_clip(comp.text_plus, comp)
        self.assertEqual(E.animation_on(comp.text_plus), {"inputs": [], "follower": False})     # kept, empty
        # The person now keys a scale-in that happens to be exactly Pop's shape: it's theirs.
        theirs = curve(comp.text_plus, "LayoutSize", {0: 0.001, 9: 1.1, 15: 1.0})
        self.assertFalse(E.remove_animations_from_clip(comp.text_plus, comp)[0])
        self.assertIn(theirs, comp.tools)
        self.assertEqual(E.conflict(comp.text_plus, comp, "Pop (Scale)"), "input")

    def test_a_clip_it_never_animated_is_left_exactly_as_it_is(self):
        comp = Comp()
        comp.text_plus.SetInput("Opacity1", 0.6)                        # set by hand: it used to be reset to 1
        comp.text_plus.SetInput("LineOffset", [0.1, 0.0])
        pulse = curve(comp.text_plus, "LayoutSize", {0: 1.0, 12: 1.2, 24: 1.0})
        fade_in = curve(comp.text_plus, "Opacity1", {0: 0.0, 12: 1.0})  # from nothing, like a preset - but not its keys
        path = comp.add("XYPath", "Path1")
        comp.text_plus.Pivot.ConnectTo(path.Value)                      # a path of their own on the pivot
        removed, _logs = E.remove_animations_from_clip(comp.text_plus, comp)
        self.assertFalse(removed)
        for theirs in (pulse, fade_in, path):
            self.assertIn(theirs, comp.tools)
        self.assertIs(E._feeding(comp.text_plus, "Opacity1"), fade_in)
        self.assertEqual(comp.text_plus.GetInput("LineOffset"), [0.1, 0.0])
        self.assertIsNone(E.animation_on(comp.text_plus))               # and nothing written on it either
        self.assertIsNone(E.legacy_preset(comp.text_plus, comp))

    def test_resolves_own_titles_are_not_taken_for_an_earlier_buddys(self):
        # Rise Fade / Drop In: a Follower with a two-key opacity ramp, no delay curve. Scale Up: its
        # letters' width keyed, their height an expression. Vertical Reveal: the Text+'s opacity from 0.
        for build in (lambda f: curve(f, "Opacity1", {0: 0.0, 10: 1.0}),
                      lambda f: curve(f, "CharacterSizeX", {0: 0.0, 6: 1.15, 10: 1.0})):
            comp = Comp()
            comp.text_plus.AddModifier("StyledText", E.FOLLOWER_ID)
            follower = E.follower_of(comp.text_plus)
            build(follower)
            self.assertFalse(E.remove_animations_from_clip(comp.text_plus, comp)[0])
            self.assertIs(E.follower_of(comp.text_plus), follower)
            self.assertEqual(E.conflict(comp.text_plus, comp, "Typewriter (Letters)"), "text")
        comp = Comp()
        reveal = curve(comp.text_plus, "Opacity1", {4: 0.0, 14: 1.0})
        self.assertFalse(E.remove_animations_from_clip(comp.text_plus, comp)[0])
        self.assertIn(reveal, comp.tools)
        self.assertEqual(E.conflict(comp.text_plus, comp, "Fade (Opacity)"), "input")

    def test_an_earlier_buddys_unrecorded_presets_still_come_off_and_are_named(self):
        for name, apply in (("Pop (Scale)", E.apply_pop_preset_to_clip),
                            ("Bounce (Extra Rebound)", E.apply_bounce_preset_to_clip),
                            ("Fade (Opacity)", E.apply_fade_preset_to_clip),
                            ("Slide (Direction + Fade)", E.apply_slide_preset_to_clip)):
            comp = Comp()
            self.assertTrue(apply(comp.text_plus, comp)[0], name)
            earlier(comp)
            self.assertEqual(E.legacy_preset(comp.text_plus, comp), name)
            self.assertIsNone(E.conflict(comp.text_plus, comp, "Fade (Opacity)"), name)     # an Apply replaces it
            self.assertTrue(E.remove_animations_from_clip(comp.text_plus, comp)[0], name)
            self.assertEqual(comp.ids(), ["TextPlus"], name)
            self.assertEqual(comp.text_plus.GetInput("LayoutSize") or 1.0, 1.0, name)

    def test_a_grow_keyed_from_exactly_nothing_is_an_earlier_buddys_too(self):
        comp = Comp()                                                   # Pop before 2026-09-28 started at 0, not 0.001
        curve(comp.text_plus, "LayoutSize", {0: 0.0, 9: 1.1, 15: 1.0})
        self.assertEqual(E.legacy_preset(comp.text_plus, comp), "Pop (Scale)")

    def test_an_earlier_buddys_preset_is_known_however_the_clip_was_trimmed_since(self):
        # Keyed at frame 0 (as every Buddy before 1.1.38 did, whatever the comp's range); the clip's
        # head trimmed since, so the comp now starts inside the move.
        comp = Comp(render_start=12.0)
        E.apply_pop_preset_to_clip(comp.text_plus, comp, start_frame=0, duration=15)
        earlier(comp)
        self.assertEqual(E.legacy_preset(comp.text_plus, comp), "Pop (Scale)")
        self.assertTrue(E.remove_animations_from_clip(comp.text_plus, comp)[0])

    def test_keys_that_cant_be_read_fall_back_to_where_the_curve_starts(self):
        comp = Comp(render_start=24.0)
        spline = curve(comp.text_plus, "Opacity1", {0: 0.0, 12: 1.0})
        spline.GetKeyFrames = mock.Mock(side_effect=RuntimeError("no keys for you"))
        self.assertTrue(E._starts_like_a_preset(comp.text_plus, comp, "Opacity1"))      # read at frame 0, not 24
        self.assertTrue(E.remove_animations_from_clip(comp.text_plus, comp)[0])         # as before shapes were read
        late = Comp()
        fade_out = curve(late.text_plus, "Opacity1", {0: 1.0, 100: 1.0, 120: 0.0})
        fade_out.GetKeyFrames = mock.Mock(side_effect=RuntimeError)
        self.assertFalse(E.remove_animations_from_clip(late.text_plus, late)[0])        # a fade-OUT is never a preset's

    def test_an_earlier_buddys_letter_presets_still_come_off_and_are_named(self):
        for name in E.LETTER_PRESETS:
            comp = Comp()
            E.apply_letter_preset_to_clip(comp.text_plus, comp, name)
            earlier(comp)
            self.assertEqual(E.legacy_preset(comp.text_plus, comp), name)
            self.assertTrue(E.remove_animations_from_clip(comp.text_plus, comp)[0], name)
            self.assertEqual(comp.ids(), ["TextPlus"], name)
            self.assertEqual(comp.text_plus.GetInput("StyledText"), "HELLO WORLD")

    def letters_like_follower(self, comp):
        """A Follower keyed exactly as Letter Fade keys one - so only what's asked about keeps it."""
        comp.text_plus.AddModifier("StyledText", E.FOLLOWER_ID)
        follower = E.follower_of(comp.text_plus)
        curve(follower, "Opacity1", {0: 0.0, 8: 1.0, 9: 1.0})
        return follower

    def test_another_features_mark_keeps_its_follower(self):
        for tag, record in ((E._UNITS_TAG, '{"preset": "pop"}'), (E._HIGHLIGHT_TAG, '{"pop": true}')):
            comp = Comp()
            follower = self.letters_like_follower(comp)
            self.assertTrue(E._unrecorded(comp.text_plus, comp)["follower"])     # the control: unmarked, a letter preset's
            comp.text_plus.SetData(tag, record)                         # Animate by's, the highlight's pop
            self.assertFalse(E.remove_animations_from_clip(comp.text_plus, comp)[0], tag)
            self.assertIs(E.follower_of(comp.text_plus), follower, tag)
            self.assertEqual(E.conflict(comp.text_plus, comp, "Letter Pop (Letters)"), "text", tag)

    def test_a_follower_whose_delays_are_on_a_curve_is_never_a_letter_presets(self):
        comp = Comp()                                                   # unrecorded: the person's, or an untagged Animate by's
        follower = self.letters_like_follower(comp)
        curve(follower, "DelayByCharacterPosition", {0: 0.0, 1: 4.8})
        self.assertFalse(E.remove_animations_from_clip(comp.text_plus, comp)[0])
        self.assertIs(E.follower_of(comp.text_plus), follower)

        # Recorded as this tab's - but that Follower was deleted in Fusion, and Animate by put its own on since.
        comp = Comp()
        E.apply_letter_preset_to_clip(comp.text_plus, comp, "Letter Fade (Letters)")
        self.assertTrue(E.animation_on(comp.text_plus)["follower"])
        E.follower_of(comp.text_plus).Delete()
        follower = self.letters_like_follower(comp)
        curve(follower, "DelayByCharacterPosition", {0: 0.0, 1: 4.8})
        self.assertFalse(E.remove_animations_from_clip(comp.text_plus, comp)[0])
        self.assertIs(E.follower_of(comp.text_plus), follower)
        self.assertEqual(E.conflict(comp.text_plus, comp, "Typewriter (Letters)"), "text")

    def test_the_names_and_tags_shared_between_modules(self):
        self.assertEqual(E._UNITS_TAG, mr.TEXT_TAG)
        self.assertEqual(E._HIGHLIGHT_TAG, hr.TAG)
        self.assertEqual(E.ANIMATION_TAG, mr.LETTERS_TAG)
        self.assertEqual(E.FOLLOWER_ID, mr.FOLLOWER_ID)
        self.assertEqual(options.ANIM_PRESETS, list(E.WHOLE_PRESETS + E.LETTER_PRESETS))


class APresetDoesntGoOverSomeoneElses(unittest.TestCase):
    def test_letters_are_refused_over_character_styling(self):
        comp = Comp()
        cls = styled(comp)
        self.assertEqual(E.conflict(comp.text_plus, comp, "Typewriter (Letters)"), "text")
        ok, _logs = E.apply_letter_preset_to_clip(comp.text_plus, comp, "Typewriter (Letters)")
        self.assertFalse(ok)
        self.assertIs(E.text_source(comp.text_plus), cls)               # the styling is still what feeds the text
        self.assertEqual(comp.ids(), [CLS, "TextPlus"])
        self.assertEqual(E.attach_follower(comp.text_plus)[0], None)    # nor by the back door
        self.assertIs(E.text_source(comp.text_plus), cls)

    def test_the_fake_replaces_as_resolve_was_measured_to(self):
        comp = Comp()
        styled(comp)
        comp.text_plus.AddModifier("StyledText", E.FOLLOWER_ID)         # what an unguarded Apply would do
        self.assertEqual(comp.ids(), ["StyledTextFollower", "TextPlus"])     # the styling is gone...
        self.assertEqual(comp.text_plus.GetInput("StyledText"), "")     # ...and the words read back empty

    def test_its_own_letter_preset_is_replaced_as_before(self):
        comp = Comp()
        E.apply_letter_preset_to_clip(comp.text_plus, comp, "Letter Fade (Letters)")
        self.assertIsNone(E.conflict(comp.text_plus, comp, "Typewriter (Letters)"))
        self.assertIsNone(E.conflict(comp.text_plus, comp, "Pop (Scale)"))

    def test_a_whole_clip_preset_leaves_a_hand_keyed_input_alone(self):
        comp = Comp()
        curve(comp.text_plus, "LayoutSize", {0: 1.0, 12: 1.2})
        self.assertEqual(E.conflict(comp.text_plus, comp, "Pop (Scale)"), "input")
        self.assertEqual(E.conflict(comp.text_plus, comp, "Bounce (Extra Rebound)"), "input")
        self.assertIsNone(E.conflict(comp.text_plus, comp, "Fade (Opacity)"))       # opacity is free
        fade_out = curve(comp.text_plus, "Opacity1", {0: 1.0, 100: 1.0, 120: 0.0})
        self.assertEqual(E.conflict(comp.text_plus, comp, "Fade (Opacity)"), "input")
        self.assertEqual(E.conflict(comp.text_plus, comp, "Slide (Direction + Fade)"), "input")
        self.assertIn(fade_out, comp.tools)

    def test_slide_leaves_a_pivot_path_of_the_persons_alone(self):
        comp = Comp()
        path = comp.add("XYPath", "Path1")
        comp.text_plus.Pivot.ConnectTo(path.Value)
        self.assertEqual(E.conflict(comp.text_plus, comp, "Slide (Direction + Fade)"), "input")
        self.assertIsNone(E.conflict(comp.text_plus, comp, "Fade (Opacity)"))       # which doesn't key the pivot

    def test_a_recorded_clip_with_an_input_the_record_doesnt_list(self):
        comp = Comp()
        E.apply_fade_preset_to_clip(comp.text_plus, comp)               # this tab's: Opacity1
        theirs = curve(comp.text_plus, "LayoutSize", {0: 0.001, 9: 1.1, 15: 1.0})    # theirs, Pop's very shape
        self.assertEqual(E.conflict(comp.text_plus, comp, "Pop (Scale)"), "input")
        self.assertTrue(E.remove_animations_from_clip(comp.text_plus, comp)[0])      # the Fade goes
        self.assertIn(theirs, comp.tools)                               # the recorded clip's other curve stays

    def test_a_whole_clip_preset_goes_beside_another_features_follower(self):
        comp = Comp()
        comp.text_plus.AddModifier("StyledText", E.FOLLOWER_ID)
        curve(E.follower_of(comp.text_plus), "DelayByCharacterPosition", {0: 0.0, 1: 4.8})
        comp.text_plus.SetData(E._UNITS_TAG, '{"preset": "pop"}')
        self.assertIsNone(E.conflict(comp.text_plus, comp, "Pop (Scale)"))      # it keys the Text+'s size, not the text

    def chained_own_letters(self):
        comp = Comp()
        E.apply_letter_preset_to_clip(comp.text_plus, comp, "Letter Fade (Letters)")
        E.mark_preset(comp.text_plus, "Letter Fade (Letters)", "Medium", None, 24.0)
        follower = E.follower_of(comp.text_plus)
        follower.AddModifier("Text", CLS)                               # the person's, on the Follower's own Text
        return comp, follower

    def test_its_own_follower_with_styling_chained_behind_is_not_read_keyed_or_taken_apart(self):
        comp, follower = self.chained_own_letters()
        self.assertTrue(E.chained(comp.text_plus))
        self.assertIsNone(E.words_of(comp.text_plus))
        for preset in ("Letter Pop (Letters)", "Typewriter (Letters)", "Pop (Scale)", "Fade (Opacity)"):
            self.assertEqual(E.conflict(comp.text_plus, comp, preset), "text", preset)
        self.assertFalse(E.apply_letter_preset_to_clip(comp.text_plus, comp, "Letter Pop (Letters)")[0])
        self.assertEqual(follower.inputs.get("CharacterSizeX"), None)   # nothing new keyed on it
        took, logs = E.remove_follower(comp.text_plus)
        self.assertFalse(took)
        self.assertTrue(any("chained" in line for line in logs))
        self.assertFalse(E.remove_animations_from_clip(comp.text_plus, comp)[0])
        self.assertIs(E.follower_of(comp.text_plus), follower)
        self.assertTrue(E.animation_on(comp.text_plus)["follower"])     # still this tab's, for when it can come off
        self.assertEqual(comp.chain_reads, [])                          # and not one read through the chain

    def test_the_fakes_chain_read_is_caught(self):
        comp, _follower = self.chained_own_letters()
        with self.assertRaises(AssertionError):
            comp.text_plus.GetInput("StyledText")
        self.assertEqual(comp.chain_reads, [("TextPlus", "StyledText")])


class FontStylingSwitchesOffOnlyWhatItSwitchedOn(unittest.TestCase):
    def test_the_note_of_what_it_switched_on(self):
        tool = Comp().text_plus
        self.assertEqual(E.styled_elements(tool), set())
        E.note_element(tool, E.OUTLINE_ELEMENT, True)
        E.note_element(tool, E.SHADOW_ELEMENT, True)
        self.assertEqual(E.styled_elements(tool), {2, 3})
        E.note_element(tool, E.OUTLINE_ELEMENT, False)
        self.assertEqual(E.styled_elements(tool), {3})
        E.note_element(tool, E.SHADOW_ELEMENT, False)
        self.assertIsNone(tool.GetData(E.STYLE_TAG))                    # nothing of its own left: no note

    def test_whether_an_element_is_on(self):
        tool = Comp().text_plus
        self.assertFalse(E.element_is_on(tool, 2))
        tool.SetInput("Enabled2", 1.0)
        self.assertTrue(E.element_is_on(tool, 2))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TheTextPlusTabs(unittest.TestCase):
    """text_plus.TextPlusTools' handlers, on a stand-in that borrows them (no Qt page)."""

    NAMES = ("on_apply_animation", "on_remove_animations", "_apply_animation_to_tool", "_animation_clip_key",
             "_preset_before", "_timeline_fps", "_comp_fps", "_record_batch_action", "_push_live_preview_action",
             "_apply_live_preview_action_part", "_apply_action", "_action_label", "on_undo", "on_redo",
             "_apply_font_style_to_tool", "_apply_style", "_find_text_tools", "_lock_comp")

    def tools(self, clips, preset="Pop (Scale)", ticked=()):
        Tools = type("Tools", (), {n: text_plus.TextPlusTools.__dict__[n] for n in self.NAMES})
        tools = Tools()
        tools.toasts, tools.lines, tools.clips = [], [], list(clips)
        tools.emit = lambda name, payload=None: tools.toasts.append(payload["text"]) if name == "toast" else None
        tools.log = tools.lines.append
        tools.host = type("Host", (), {"set_busy": lambda self, on, message=None: None,
                                       "pump_busy": lambda self, message=None: None})()
        tools._preview_timer = type("Timer", (), {"stop": lambda self: None, "start": lambda self: None})()
        tools._resolve = lambda quiet=False: object()
        tools._timeline_or_log = lambda resolve: type("TL", (), {"GetSetting": lambda self, key: "24"})()
        tools._scope_or_toast = lambda timeline, scope, track: ([(c, 1) for c in tools.clips], "the test clips")
        tools._get_fusion_comp = lambda clip: clip.comp
        tools._find_text_tool = lambda comp: comp.text_plus
        tools._push_history = lambda: None
        tools._refresh_live_preview = lambda force=False: None
        tools._live_preview_undo_stack, tools._live_preview_redo_stack = [], []
        tools._style_kept = 0
        tools.current_fusion_pos = (0.5, 0.5)
        on = {"font_on": False, "outline_on": False, "shadow_on": False, "background_on": False,
              **{name: True for name in ticked}}
        tools.opts = type("Opts", (), {
            "anim_preset": preset, "anim_speed": "Medium", "anim_direction": "From Left", "style_scope": "timeline",
            "scope": lambda self, key: "timeline", "track": lambda self, key: 1,
            "toggle": lambda self, key: on[key], "color": lambda self, key: (1.0, 0.0, 0.0),
            "slider": lambda self, key: 0.5, "font_face": ("Arial", "Regular")})()
        return tools

    def clip(self, n=1):
        comp = Comp()
        return type("Clip", (), {"comp": comp, "GetName": lambda self: f"Text+ {n}", "GetStart": lambda self: n * 100,
                                 "GetEnd": lambda self: n * 100 + 50, "GetDuration": lambda self: 50})()

    def test_apply_leaves_a_clip_whose_text_is_taken_and_says_so(self):
        free, taken = self.clip(1), self.clip(2)
        cls = styled(taken.comp)
        tools = self.tools([free, taken], preset="Typewriter (Letters)")
        tools.on_apply_animation()
        self.assertIsNotNone(E.follower_of(free.comp.text_plus))
        self.assertIs(E.text_source(taken.comp.text_plus), cls)
        self.assertEqual(tools.toasts[-1], "Animated 1 Text+ clip(s). Left 1 alone: something else already has the "
                                           "text, size, opacity or pivot there.")
        tools.on_undo()                                                 # only the clip that was animated is a step
        self.assertIsNone(E.follower_of(free.comp.text_plus))
        self.assertIs(E.text_source(taken.comp.text_plus), cls)

    def test_remove_counts_only_the_clips_it_took_something_off(self):
        ours, bare, theirs = self.clip(1), self.clip(2), self.clip(3)
        tools = self.tools([ours])
        tools.on_apply_animation()
        curve(theirs.comp.text_plus, "Opacity1", {0: 1.0, 40: 0.0})     # a fade-out keyed by hand
        tools.clips = [ours, bare, theirs]
        tools.on_remove_animations()
        self.assertEqual(tools.toasts[-1], "Removed animations from 1 Text+ clip(s)")
        self.assertEqual(ours.comp.ids(), ["TextPlus"])
        self.assertEqual(theirs.comp.ids(), ["BezierSpline", "TextPlus"])
        tools.on_remove_animations()                                    # nothing of this tab's left anywhere
        self.assertEqual(tools.toasts[-1], "None of those clips has an animation from this tab.")

    def test_remove_says_when_a_letter_animation_had_to_stay(self):
        ours, stuck = self.clip(1), self.clip(2)
        tools = self.tools([ours, stuck], preset="Letter Fade (Letters)")
        tools.on_apply_animation()
        E.follower_of(stuck.comp.text_plus).AddModifier("Text", CLS)    # styling chained behind it since
        tools.on_remove_animations()
        self.assertEqual(tools.toasts[-1], "Removed animations from 1 Text+ clip(s). On 1, a letter animation stays: "
                                           "another text modifier is chained behind its Follower – take the Follower "
                                           "off in Fusion.")
        self.assertIsNotNone(E.follower_of(stuck.comp.text_plus))
        tools.clips = [stuck]
        tools.on_apply_animation()                                      # and nothing more goes on it
        self.assertIn("Animated 0 Text+ clip(s). Left 1 alone", tools.toasts[-1])
        self.assertEqual(stuck.comp.chain_reads, [])

    def test_undo_puts_back_what_the_text_plus_says_was_on_it(self):
        clip = self.clip()
        tools = self.tools([clip], preset="Fade (Opacity)")
        tools.on_apply_animation()
        tools._live_preview_undo_stack.clear()                          # Buddy restarted since
        tools.on_remove_animations()
        self.assertEqual(clip.comp.ids(), ["TextPlus"])
        tools.on_undo()
        self.assertEqual(tools.toasts[-1], "Undid animation change")
        self.assertEqual(E.preset_on(clip.comp.text_plus), ("Fade (Opacity)", "Medium", "From Left", 24.0))
        self.assertIsNotNone(clip.comp.text_plus.Opacity1.GetConnectedOutput())
        tools.on_redo()
        self.assertEqual(tools.toasts[-1], "Redid animation change")
        self.assertEqual(clip.comp.ids(), ["TextPlus"])

    def test_undo_after_an_earlier_buddys_preset_was_replaced_puts_that_preset_back(self):
        clip = self.clip()
        E.apply_pop_preset_to_clip(clip.comp.text_plus, clip.comp)
        earlier(clip.comp)
        tools = self.tools([clip], preset="Fade (Opacity)")
        tools.on_apply_animation()
        self.assertEqual(E.preset_on(clip.comp.text_plus)[0], "Fade (Opacity)")
        self.assertIsNone(clip.comp.text_plus.LayoutSize.GetConnectedOutput())
        tools.on_undo()
        self.assertEqual(E.preset_on(clip.comp.text_plus)[0], "Pop (Scale)")       # not left with neither
        self.assertIsNotNone(clip.comp.text_plus.LayoutSize.GetConnectedOutput())
        self.assertIsNone(clip.comp.text_plus.Opacity1.GetConnectedOutput())

    def test_remove_of_an_earlier_buddys_preset_can_be_undone_too(self):
        clip = self.clip()
        E.apply_bounce_preset_to_clip(clip.comp.text_plus, clip.comp)
        earlier(clip.comp)
        tools = self.tools([clip])
        tools.on_remove_animations()
        self.assertEqual(tools.toasts[-1], "Removed animations from 1 Text+ clip(s)")
        self.assertEqual(clip.comp.ids(), ["TextPlus"])
        tools.on_undo()
        self.assertEqual(tools.toasts[-1], "Undid animation change")
        self.assertEqual(E.preset_on(clip.comp.text_plus)[0], "Bounce (Extra Rebound)")

    def test_another_clip_at_the_same_spot_doesnt_inherit_a_preset(self):
        first, second = self.clip(1), self.clip(1)                      # same name, start, end and track
        tools = self.tools([first])
        tools.on_apply_animation()                                      # Pop on the first
        tools.clips = [second]
        tools.opts.__class__.anim_preset = "Fade (Opacity)"
        tools.on_apply_animation()
        tools.on_undo()
        self.assertEqual(second.comp.ids(), ["TextPlus"])               # back to nothing, not the first clip's Pop
        self.assertIsNone(E.preset_on(second.comp.text_plus))

    def test_an_unticked_group_goes_off_only_where_font_styling_switched_it_on(self):
        by_buddy, by_hand = self.clip(1), self.clip(2)
        by_hand.comp.text_plus.SetInput("Enabled2", 1.0)                # an outline set in the Inspector
        ticked = self.tools([by_buddy], ticked=("outline_on",))
        ticked._apply_style()
        self.assertEqual(ticked.toasts[-1], "Styled 1 Text+ clip(s)")
        self.assertEqual(by_buddy.comp.text_plus.GetInput("Enabled2"), 1)
        self.assertEqual(E.styled_elements(by_buddy.comp.text_plus), {2})

        unticked = self.tools([by_buddy, by_hand])
        unticked._apply_style()
        self.assertEqual(by_buddy.comp.text_plus.GetInput("Enabled2"), 0)       # Font Styling's own: off
        self.assertEqual(E.styled_elements(by_buddy.comp.text_plus), set())
        self.assertEqual(by_hand.comp.text_plus.GetInput("Enabled2"), 1.0)      # the person's: as it was
        self.assertEqual(unticked.toasts[-1], "Styled 1 Text+ clip(s). On 1, an outline, shadow or box stays that "
                                              "Buddy has no note of setting – tick it and apply to take it over.")
        self.assertFalse(any("Warning" in line for line in unticked.lines))     # nothing to do isn't a failure
        unticked.clips = [by_buddy]
        unticked._apply_style()
        self.assertEqual(unticked.toasts[-1], "Styled 0 Text+ clip(s)")         # the count starts again each time

    def test_taking_over_an_element_then_unticking_it_removes_it(self):
        clip = self.clip()
        clip.comp.text_plus.SetInput("Enabled3", 1.0)                   # a shadow an earlier Buddy (or the person) set
        self.tools([clip], ticked=("shadow_on",))._apply_style()
        self.tools([clip])._apply_style()
        self.assertEqual(clip.comp.text_plus.GetInput("Enabled3"), 0)

    def with_words(self, clip, *names):
        """Text+ tools added to a clip's comp, which then lists them the way Resolve's comps do."""
        comp = clip.comp
        words = [comp.text_plus] + [comp.add("TextPlus", name, StyledText=name) for name in names]
        comp.GetToolList = lambda _selected, reg_id=None: dict(enumerate(words, 1))
        return words

    def test_font_styling_goes_on_every_text_plus_of_a_clip(self):
        title = self.clip()
        words = self.with_words(title, "Word2", "Word3")
        self.tools([title], ticked=("font_on",))._apply_style()
        self.assertEqual([word.GetInput("Font") for word in words], ["Arial"] * 3)

    def test_a_clip_with_several_text_plus_counts_once(self):
        title = self.clip()
        self.with_words(title, "Word2")
        tools = self.tools([title], ticked=("font_on",))
        tools._apply_style()
        self.assertEqual(tools.toasts[-1], "Styled 1 Text+ clip(s)")

    def test_a_comp_that_cant_list_its_tools_has_its_one_text_plus(self):
        clip = self.clip()
        tools = self.tools([clip])
        self.assertEqual(tools._find_text_tools(clip.comp), [clip.comp.text_plus])
        clip.comp.GetToolList = lambda _selected, reg_id=None: {}
        self.assertEqual(tools._find_text_tools(clip.comp), [clip.comp.text_plus])
        self.assertEqual(tools._find_text_tools(None), [])


class AnimateByNeverReplacesAnotherModifier(unittest.TestCase):
    """Animation > Previews > Animate by (motion_resolve.apply_units)."""

    def plan(self, n, ratio, a):
        return motion.plan(PRESETS["pop"], n, 24 * ratio, at=a)

    def run_apply(self, *items):
        return mr.run_apply(controller(Timeline(*items)), PRESETS["pop"],
                            lambda n, fps, a: motion.plan(PRESETS["pop"], n, fps, at=a),
                            options={"way": "both", "speed": 1.0, **WORDS})

    def test_a_text_plus_with_character_styling_is_handed_back_untouched(self):
        item = text_clip()
        tp = text_tool(item)
        tp.AddModifier("StyledText", CLS)
        tp.StyledText.value = "a extraordinarily b c"                   # its words, as the styling carries them
        cls = mr.text_source(tp)
        done = mr.apply_units(item, "pop", self.plan, 120, 24.0, WORDS)
        self.assertEqual(done, mr.HELD)
        self.assertIs(mr.text_source(text_tool(item)), cls)
        self.assertEqual(item.reloads, 0)                               # the comp wasn't touched at all

    def test_it_then_moves_as_a_whole_and_is_counted(self):
        item = on_timeline(text_clip())
        text_tool(item).AddModifier("StyledText", CLS)
        failing = on_timeline(Item(name="one frame"), frames=1)         # another clip failing doesn't hide it
        failing.GetMediaPoolItem = lambda: None
        result = self.run_apply(item, failing)
        self.assertEqual((result["applied"], result["whole"], result["held"], len(result["failed"])), (1, 1, 1, 1))
        comp = item.comps[-1]
        self.assertEqual(chain(comp), ["MediaOut1", "BuddyMotion", "TextPlus1"])
        self.assertEqual(mr.text_source(mr.text_plus(comp)).reg_id, CLS)        # the styling still feeds the text

    def test_a_chain_is_neither_read_nor_taken_apart(self):
        item = text_clip()
        self.assertTrue(mr.apply_units(item, "pop", self.plan, 120, 24.0, WORDS))
        tp = text_tool(item)
        mr.follower(tp).AddModifier("Text", CLS)                        # styling chained behind Buddy's Follower
        self.assertTrue(mr.chained(tp))
        self.assertEqual(mr._text_of(tp), "")
        before = item.reloads
        for act in (lambda: mr.remove_units(item), lambda: mr.apply_units(item, "pop", self.plan, 120, 24.0, WORDS),
                    lambda: mr.remove(item)):
            with self.assertRaises(RuntimeError) as raised:
                act()
            self.assertIn("chained", str(raised.exception))
        self.assertIsNotNone(mr.follower(text_tool(item)))              # still there, with what's behind it
        self.assertEqual(item.reloads, before)                          # the comp was never swapped for a copy
        self.assertEqual([c.chain_reads for c in item.comps], [[]])     # and its words never read

    def test_a_plain_text_plus_is_not_chained(self):
        item = text_clip()
        self.assertFalse(mr.chained(text_tool(item)))
        mr.apply_units(item, "pop", self.plan, 120, 24.0, WORDS)
        self.assertFalse(mr.chained(text_tool(item)))
        self.assertTrue(mr.timed(mr.follower(text_tool(item))))         # Animate by's delays are always on a curve

    def letters_under_a_stale_record(self):
        """Animate by went on; its Follower was deleted in Fusion (its record stays); then a
        Subtitles letter preset went on, unrecorded as an earlier Buddy left them."""
        item = text_clip()
        mr.apply_units(item, "pop", self.plan, 120, 24.0, WORDS)
        tp = text_tool(item)
        mr.follower(tp).Delete()
        tp.AddModifier("StyledText", mr.FOLLOWER_ID)
        letters = mr.follower(tp)
        letters.Text.value = "a extraordinarily b c"
        mr._key(item.comps[-1], letters.Opacity1, {0: {"value": 0.0}, 8: {"value": 1.0}, 9: {"value": 1.0}})
        self.assertIsNotNone(mr._units_record(tp))
        return item, letters

    def test_a_left_over_record_doesnt_make_someone_elses_follower_animate_bys(self):
        item, letters = self.letters_under_a_stale_record()
        with self.assertRaises(RuntimeError) as raised:
            mr.apply_units(item, "pop", self.plan, 120, 24.0, WORDS)
        self.assertIn("Subtitles > Timeline animation", str(raised.exception))
        self.assertIs(mr.follower(text_tool(item)), letters)

        self.assertTrue(mr.remove_units(item))                          # Previews' Remove clears its own left-overs...
        tp = text_tool(item)
        self.assertIsNone(mr._units_record(tp))
        kept = mr.follower(tp)
        self.assertIsNotNone(kept)                                      # ...and leaves the letters on
        self.assertIsNotNone(mr._source_tool(kept.Opacity1))

    def test_a_whole_clip_apply_over_it_leaves_the_letters_too(self):
        item, _letters = self.letters_under_a_stale_record()
        on_timeline(item)
        result = mr.run_apply(controller(Timeline(item)), PRESETS["pop"],
                              lambda n, fps, a: motion.plan(PRESETS["pop"], n, fps, at=a), options={"way": "both"})
        self.assertEqual((result["applied"], result["failed"]), (1, []))
        tp = mr.text_plus(item.comps[-1])
        self.assertIsNotNone(mr.follower(tp))
        self.assertIsNone(mr._units_record(tp))

    def test_what_it_calls_each_follower_it_wont_replace(self):
        mine = text_clip()
        text_tool(mine).AddModifier("StyledText", mr.FOLLOWER_ID)       # the person's: nothing keyed that Buddy keys
        with self.assertRaises(RuntimeError) as raised:
            mr.apply_units(mine, "pop", self.plan, 120, 24.0, WORDS)
        self.assertIn("a text Follower of its own", str(raised.exception))

        recorded = text_clip()
        tp = text_tool(recorded)
        tp.AddModifier("StyledText", mr.FOLLOWER_ID)
        tp.SetData(mr.LETTERS_TAG, '{"inputs": [], "follower": true}')  # this Buddy's letter preset
        with self.assertRaises(RuntimeError) as raised:
            mr.apply_units(recorded, "pop", self.plan, 120, 24.0, WORDS)
        self.assertIn("Subtitles > Timeline animation", str(raised.exception))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class WhatPreviewsSays(unittest.TestCase):
    def test_the_note_about_held_clips(self):
        self.assertIn("1 Text+ has character styling", held_note(1))
        self.assertIn("3 Text+ clips have character styling", held_note(3))

    def test_the_hot_key_says_it(self):
        preset = {"label": "Pop"}
        plain = {"applied": 2, "whole": 0, "held": 0, "skipped": {}, "failed": []}
        self.assertEqual(apply_summary(preset, plain, None), ("Pop is on 2 clips.", True))
        held = {**plain, "applied": 1, "whole": 1, "held": 1}
        self.assertEqual(apply_summary(preset, held, None), (held_note(1), True))      # not "it isn't a Text+"

    def done_apply(self, result):
        Page = type("Page", (), {"_done_apply": AnimationPage._done_apply, "achieve": AnimationPage.achieve})
        page = Page()
        page.said = []
        page.emit = lambda name, payload=None: page.said.append((name, payload.get("title") or payload["text"]))
        page._read_after = page._push_state = lambda: None
        page._done_apply({"label": "Pop"}, result, None)
        return page.said

    def test_the_apply_button_says_it_even_when_another_clip_failed(self):
        result = {"applied": 2, "whole": 1, "held": 1, "skipped": {}, "failed": [("clip", "its reason")]}
        said = self.done_apply(result)
        self.assertEqual(said[0], ("toast", "Pop is on 2 clips."))      # the held one isn't called "not a Text+"
        self.assertIn(("alert", "Some clips weren't animated"), said)
        self.assertIn(("alert", "Some clips move as a whole"), said)
        self.assertEqual(self.done_apply({**result, "failed": []})[0], ("toast", on_clips("Pop", 2, 0)))


class TheHighlightsPop(unittest.TestCase):
    INK = [(0.4, 0.5, 0.1, 0.05), (0.6, 0.5, 0.1, 0.05)]

    def highlighted(self, style=False):
        item = text_clip("ab cd")
        comp, tp = item.comps[-1], text_tool(item)
        if style:
            tp.AddModifier("StyledText", CLS)
            tp.StyledText.value = "ab cd"
        pops = hr._build(comp, tp, "ab cd", [0.0, 12.0], self.INK, hl.clean_options({}), 0.0, 120, 24.0, "even",
                         [0.0, 0.5])
        return item, tp, pops

    def test_it_pops_plain_words(self):
        _item, tp, pops = self.highlighted()
        self.assertTrue(pops)
        self.assertTrue(mr.timed(mr.follower(tp)))                      # its delays are always on a curve
        self.assertTrue(hr.record(tp)["pop"])

    def test_no_follower_goes_over_character_styling(self):
        _item, tp, pops = self.highlighted(style=True)
        self.assertFalse(pops)                                          # the highlight goes on, the words don't pop
        self.assertEqual(mr.text_source(tp).reg_id, CLS)                # and the styling still feeds the text
        self.assertFalse(hr.record(tp)["pop"])
        self.assertIsNotNone(tp.comp.FindTool(hr.MERGE_NAME))

    def test_remove_leaves_a_follower_the_pop_didnt_build(self):
        item, tp, _pops = self.highlighted()
        mr.follower(tp).Delete()                                        # deleted in Fusion; a letter preset since
        tp.AddModifier("StyledText", mr.FOLLOWER_ID)
        letters = mr.follower(tp)
        letters.Text.value = "ab cd"
        self.assertTrue(hr.remove(item))
        after = mr.follower(text_tool(item))
        self.assertIsNotNone(after)
        self.assertIsNone(hr.record(text_tool(item)))

    def test_a_chain_behind_the_pop_is_left_whole(self):
        item, tp, _pops = self.highlighted()
        mr.follower(tp).AddModifier("Text", CLS)
        before = item.reloads
        for act in (lambda: hr.remove(item), lambda: hr.apply(item, lambda text, kept: ([0.0, 12.0], "even"),
                                                               hl.clean_options({}), 24.0)):
            with self.assertRaises(RuntimeError) as raised:
                act()
            self.assertIn("chained", str(raised.exception))
        self.assertEqual(item.reloads, before)
        self.assertEqual(item.comps[-1].chain_reads, [])

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_what_it_says_when_words_dont_pop(self):
        base = {"applied": 2, "spoken": 2, "even": 0, "popless": 1, "other": 0, "failed": []}
        self.assertIn("already has an animation or styling on its text", highlight_summary(base)[1])


class TheCompSwap(unittest.TestCase):
    def test_an_import_that_replaces_in_place_leaves_the_one_comp_alone(self):
        class InPlace(Item):
            def ImportFusionComp(self, path):                           # a title from Buddy's Text+ bin (21.1)
                self.reloads += 1
                self.comps[-1] = self.exported[path]
                return self.comps[-1]

            def DeleteFusionCompByName(self, name):
                self.deleted.append(name)                               # a Resolve that would do it
                del self.comps[self.names.index(name)], self.names[self.names.index(name)]
                return True

        item = InPlace(text_clip().comps[0])
        path = mr._export(item, 1)
        try:
            self.assertTrue(mr._swap_in(item, 1, path))
        finally:
            mr._forget(path)
        self.assertEqual(item.deleted, [])
        self.assertEqual(item.GetFusionCompCount(), 1)

    def test_an_import_that_adds_a_comp_still_drops_the_old_one(self):
        item = text_clip()
        path = mr._export(item, 1)
        try:
            self.assertTrue(mr._swap_in(item, 1, path))
        finally:
            mr._forget(path)
        self.assertEqual(item.deleted, ["Composition 1"])
        self.assertEqual(item.GetFusionCompNameList(), ["Composition 1"])    # the new one took its name


class PlacingATitle(unittest.TestCase):
    def test_its_messages_can_say_a_time(self):
        # place() once kept its timing in a local called clock, hiding the function its
        # messages call: "'dict' object is not callable" instead of the track and time.
        self.assertNotIn("clock", titles_resolve.place.__code__.co_varnames)
        self.assertIn("clock", titles_resolve.place.__code__.co_names)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class EverySentenceIsTranslated(unittest.TestCase):
    """The new sentences, as the code says them, each find their own translation."""

    SAID = (
        "Animated 3 Text+ clip(s). Left 2 alone: something else already has the text, size, opacity or pivot there.",
        "None of those clips has an animation from this tab.",
        "Removed animations from 3 Text+ clip(s). On 2, a letter animation stays: another text modifier is chained "
        "behind its Follower – take the Follower off in Fusion.",
        "Styled 3 Text+ clip(s). On 2, an outline, shadow or box stays that Buddy has no note of setting – tick it "
        "and apply to take it over.",
        "Undid animation change", "Redid animation change", "Some clips move as a whole",
        "The highlight follows your timing now – without the pop, as its text is already animated or styled.",
        "Outline, shadow and box that are switched off are turned off where Font styling switched them on",
    ) + tuple(f"Removes the animations this tab put on {where}. Animate by, the highlight and anything Buddy can "
              "tell isn't its own stay."
              for where in ("the selected Text+ clips", "the Text+ clips under the playhead",
                            "the Text+ clips on video track 3", "every Text+ clip on the timeline"))

    def test_each_is(self):
        popless = {"applied": 4, "spoken": 4, "even": 0, "other": 0, "failed": []}
        said = self.SAID + (held_note(1), held_note(4), highlight_summary({**popless, "popless": 1})[1],
                            highlight_summary({**popless, "popless": 3})[1])
        for text in said:
            for language in ("Deutsch", "日本語"):
                self.assertNotEqual(i18n.translate(text, language), text, f"{language}: {text}")

    def test_the_numbers_come_through(self):
        german = i18n.translate(self.SAID[0], "Deutsch")
        self.assertIn("3", german)
        self.assertIn("2", german)


if __name__ == "__main__":
    unittest.main()
