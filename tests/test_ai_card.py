"""Settings > AI's graphics card (core/ai_card.py): which card search by
description, Transcribe and Generative run on, on a PC with more than one -
Automatic leaving each to its own choice."""

import os
import sys
import unittest
from unittest import mock

import _paths  # noqa: F401
from core import ai_card

SMI = ("NVIDIA GeForce RTX 5070 Ti, GPU-11111111-2222-3333-4444-555555555555, 16303, 12.0\n"
       "NVIDIA GeForce RTX 3060, GPU-66666666-7777-8888-9999-000000000000, 12288, 8.6\n")
CARDS = ["NVIDIA GeForce RTX 5070 Ti", "NVIDIA GeForce RTX 3060", "AMD Radeon(TM) Graphics"]


class AiCardTests(unittest.TestCase):
    def setUp(self):
        for name, value in {"_chosen": [], "_nvidia": [ai_card.parse_nvidia(SMI)],
                            "cards": lambda: list(CARDS)}.items():
            patcher = mock.patch.object(ai_card, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def choose(self, value):
        """As if Settings held value as Buddy started."""
        ai_card._chosen[:] = []
        with mock.patch("core.settings_store.SharedSettings", return_value={ai_card.KEY: value}):
            return ai_card.chosen()

    def test_two_of_the_same_card_are_told_apart(self):
        self.assertEqual(ai_card.numbered(["RTX A4000", "AMD", "RTX A4000", "RTX A4000"]),
                         ["RTX A4000", "AMD", "RTX A4000 #2", "RTX A4000 #3"])
        twins = SMI.replace("RTX 3060", "RTX 5070 Ti")
        self.assertEqual([c["label"] for c in ai_card.parse_nvidia(twins)],
                         ["NVIDIA GeForce RTX 5070 Ti", "NVIDIA GeForce RTX 5070 Ti #2"])

    def test_nvidia_smi_is_read_line_by_line(self):
        rows = ai_card.parse_nvidia(SMI + "garbage\n")
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["name"], rows[0]["memory_mb"], rows[0]["compute"]),
                         ("NVIDIA GeForce RTX 5070 Ti", 16303, 12.0))
        self.assertTrue(rows[1]["uuid"].startswith("GPU-6666"))

    def test_settings_lists_the_cards_only_when_there_is_a_choice(self):
        self.assertEqual(ai_card.choices()[0], ("", "Automatic (each tool's own choice)"))
        self.assertEqual([v for v, _ in ai_card.choices()[1:]], CARDS)
        with mock.patch.object(ai_card, "cards", lambda: CARDS[:1]):
            self.assertEqual(ai_card.choices(), [])

    def test_automatic_leaves_each_engine_its_own_choice(self):
        self.choose("")
        env = {"PATH": "x"}
        self.assertEqual(ai_card.cuda_env(env), {"PATH": "x"})
        self.assertEqual(ai_card.vulkan_card(), "")
        self.assertEqual(ai_card.cuda_card()["name"], "NVIDIA GeForce RTX 5070 Ti")     # nvidia-smi's first

    def test_an_nvidia_card_is_all_cuda_is_shown(self):
        self.choose("NVIDIA GeForce RTX 3060")
        env = ai_card.cuda_env({})
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "GPU-66666666-7777-8888-9999-000000000000")
        self.assertEqual(ai_card.cuda_card()["memory_mb"], 12288)
        self.assertEqual(ai_card.vulkan_card(), "NVIDIA GeForce RTX 3060")

    def test_another_kind_of_card_keeps_cuda_off_the_nvidia_ones(self):
        self.choose("AMD Radeon(TM) Graphics")
        env = ai_card.cuda_env({})
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "-1")                # Transcribe and Generative: the processor
        self.assertEqual(env["BUDDY_AI_CARD"], "AMD Radeon(TM) Graphics")  # what Generative says why
        self.assertIsNone(ai_card.cuda_card())
        self.assertEqual(ai_card.vulkan_card(), "AMD Radeon(TM) Graphics")  # search runs on it

    def test_a_card_that_is_gone_is_automatic(self):
        self.assertEqual(self.choose("NVIDIA GeForce RTX 2080"), "")
        self.assertEqual(ai_card.cuda_env({}), {})
        self.assertEqual(self.choose(None), "")                             # a settings file without it

    def test_without_an_nvidia_card_cuda_is_left_alone(self):
        self.choose("AMD Radeon(TM) Graphics")
        with mock.patch.object(ai_card, "_nvidia", [[]]):
            self.assertEqual(ai_card.cuda_env({}), {})

    def test_the_setting_is_read_once_a_run(self):
        self.choose("NVIDIA GeForce RTX 3060")
        with mock.patch("core.settings_store.SharedSettings", return_value={ai_card.KEY: ""}):
            self.assertEqual(ai_card.chosen(), "NVIDIA GeForce RTX 3060")    # takes effect at the next start


class PlatformTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform != "win32", "not Windows")
    def test_only_windows_lists_cards(self):
        self.assertEqual(ai_card.cards(), [])            # a Mac has one GPU, or switches between two itself
        self.assertEqual(ai_card.choices(), [])

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_lists_its_own_cards(self):
        from core import gpu_adapter
        with mock.patch.object(gpu_adapter, "_found", ([
                {"name": "NVIDIA GeForce RTX 5070 Ti", "luid": (0, 1), "software": False},
                {"name": "Microsoft Basic Render Driver", "luid": (0, 2), "software": True}], None)):
            self.assertEqual(ai_card.cards(), ["NVIDIA GeForce RTX 5070 Ti"])       # never the software renderer


class EnginesStartOnTheCardTests(unittest.TestCase):
    """Transcribe's and Generative's engines start with cuda_env's."""

    def test_transcribe(self):
        from pages.transcribe import jobs
        run = jobs.WorkerRun()
        with mock.patch.object(jobs.ai_card, "cuda_env", lambda env: dict(env, CUDA_VISIBLE_DEVICES="GPU-x")), \
                mock.patch.object(jobs.subprocess, "Popen") as popen:
            popen.return_value.stdout = iter(())
            popen.return_value.wait.return_value = 0
            popen.return_value.returncode = 0
            try:
                run._run_once(["probe"], lambda message: None)
            except Exception:  # noqa: BLE001 - only how it was started matters here
                pass
        self.assertEqual(popen.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"], "GPU-x")
        self.assertIn("PATH", popen.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
