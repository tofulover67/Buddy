"""Starting Transcribe's audio render (app/pages/transcribe/resolve_ext.py
render_timeline_audio) against a fake Resolve whose StartRendering refuses
a set number of times first - what Resolve now and then does for a job that
starts fine a moment later. It's retried, but only START_TRIES times; and
when every try is refused, the whole render is set up again with a new job
- once (RENDER_ROUNDS), as pressing Transcribe again did."""

import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.transcribe import resolve_ext
from pages.transcribe.resolve_ext import (RENDER_ROUNDS, START_TRIES, RenderCancelled, TranscribeController,
                                          TranscribeResolveError)


class Project:
    def __init__(self, refusals, starts_anyway=False):
        self.refusals, self.starts_anyway = refusals, starts_anyway
        self.start_calls, self.rendering, self.out = 0, False, None
        self.presets, self.jobs, self.jobs_added = set(), [], 0

    # What render_timeline_audio reads and sets around the render.
    def GetCurrentTimeline(self):
        return object()

    def GetCurrentRenderMode(self):
        return 0

    def SetCurrentRenderMode(self, mode):
        return True

    def SaveAsNewRenderPreset(self, name):
        self.presets.add(name)
        return True

    def LoadRenderPreset(self, name):
        return name in self.presets

    def DeleteRenderPreset(self, name):
        self.presets.discard(name)
        return True

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        return True

    def SetRenderSettings(self, settings):
        self.out = os.path.join(settings["TargetDir"], settings["CustomName"] + ".mov")
        return True

    def AddRenderJob(self):
        self.jobs_added += 1
        job = f"job-{self.jobs_added}"
        self.jobs.append(job)
        return job

    def DeleteRenderJob(self, job):
        self.jobs.remove(job)
        return True

    def StartRendering(self, job):
        self.start_calls += 1
        if self.start_calls <= self.refusals:
            self.rendering = self.starts_anyway
            return False
        self.rendering = True
        return True

    def IsRenderingInProgress(self):
        if self.rendering:          # renders instantly: in progress until it's asked once
            self.rendering = False
            with open(self.out, "wb"):
                pass
            return True
        return False

    status_word = "Complete"   # what Resolve shows, in its own language
    error = ""

    def GetRenderJobStatus(self, job):
        done = os.path.exists(self.out)
        return {"JobStatus": self.status_word if done else "Ready", "CompletionPercentage": 100 if done else 0,
                **({"Error": self.error} if self.error else {})}


class Controller:
    def __init__(self, project):
        self.project = project

    def current_project(self):
        return self.project


class RenderStartTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name in ("START_RETRY_WAIT", "ROUND_WAIT"):
            patcher = mock.patch.object(resolve_ext, name, 0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def render(self, project, cancelled=lambda: False):
        return TranscribeController(Controller(project)).render_timeline_audio(self.dir, cancelled=cancelled)

    def test_starts_first_time(self):
        project = Project(refusals=0)
        self.assertTrue(os.path.isfile(self.render(project)))
        self.assertEqual(project.start_calls, 1)

    def test_refused_then_started_is_retried(self):
        project = Project(refusals=START_TRIES - 1)
        self.assertTrue(os.path.isfile(self.render(project)))
        self.assertEqual(project.start_calls, START_TRIES)

    def test_every_try_refused_then_set_up_again_and_started(self):
        project = Project(refusals=START_TRIES)     # the first job never starts; a new one does
        self.assertTrue(os.path.isfile(self.render(project)))
        self.assertEqual(project.start_calls, START_TRIES + 1)
        self.assertEqual(project.jobs_added, 2)     # a job of its own the second time
        self.assertEqual(project.jobs, [])          # both cleaned up
        self.assertEqual(project.presets, set())    # and the user's settings restored

    def test_gives_up_after_the_second_round(self):
        project = Project(refusals=100)
        with self.assertRaisesRegex(TranscribeResolveError, "didn't start rendering"):
            self.render(project)
        self.assertEqual(project.start_calls, START_TRIES * RENDER_ROUNDS)
        self.assertEqual(project.jobs_added, RENDER_ROUNDS)
        self.assertEqual(project.jobs, [])          # the jobs are still cleaned up
        self.assertEqual(project.presets, set())    # and the user's settings restored

    def test_stop_pressed_before_the_second_round(self):
        project = Project(refusals=100)
        with self.assertRaises(RenderCancelled):
            self.render(project, cancelled=lambda: project.start_calls >= START_TRIES)
        self.assertEqual((project.start_calls, project.jobs_added), (START_TRIES, 1))
        self.assertEqual((project.jobs, project.presets), ([], set()))

    def test_refused_but_rendering_counts_as_started(self):
        project = Project(refusals=1, starts_anyway=True)
        self.assertTrue(os.path.isfile(self.render(project)))
        self.assertEqual(project.start_calls, 1)

    def test_finished_in_any_language(self):
        project = Project(refusals=0)
        project.status_word = "Concluso"            # an Italian Resolve's "Complete"
        self.assertTrue(os.path.isfile(self.render(project)))

    def test_a_job_that_failed_says_why(self):
        project = Project(refusals=0)
        project.error = "Disk full"
        with self.assertRaisesRegex(TranscribeResolveError, "Disk full"):
            self.render(project)
        self.assertEqual(project.jobs, [])

    def test_retries_stay_small(self):
        self.assertLessEqual(START_TRIES, 3)       # a first try plus at most two retries
        self.assertLessEqual(RENDER_ROUNDS, 2)     # set up again once at most


if __name__ == "__main__":
    unittest.main()
