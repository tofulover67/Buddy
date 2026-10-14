"""What the achievements read from Resolve (core/resolve_stats.py, with a
fake Resolve) and what the watcher makes of it (core/render_watch.py's pure
parts): a render counts once it was seen waiting and is then complete, a
timeline's grades count once, and audio-only jobs (Buddy's own) don't."""

import unittest

import _paths  # noqa: F401
from core import resolve_stats
from core.render_watch import grade_changes, render_changes


class _Item:
    def __init__(self, nodes=1, cdl=None, fails=False):
        self._nodes, self._cdl, self._fails = nodes, cdl, fails

    def GetNumNodes(self):
        if self._fails:
            raise RuntimeError("no node graph")
        return self._nodes

    def GetCDL(self):
        if self._cdl is None:
            raise RuntimeError("no CDL")
        return self._cdl


class _Timeline:
    def __init__(self, tracks, fps=25.0, start=86400, end=86400 + 25 * 90, uid="tl-1", name="Cut"):
        self._tracks, self._fps, self._start, self._end, self._uid, self._name = tracks, fps, start, end, uid, name

    def GetSetting(self, key):
        return str(self._fps) if key == "timelineFrameRate" else ""

    def GetStartFrame(self):
        return self._start

    def GetEndFrame(self):
        return self._end

    def GetTrackCount(self, kind):
        return len(self._tracks) if kind == "video" else 0

    def GetItemListInTrack(self, _kind, index):
        return self._tracks[index - 1]

    def GetUniqueId(self):
        return self._uid

    def GetName(self):
        return self._name


class _Project:
    def __init__(self, jobs, statuses, timeline=None):
        self._jobs, self._statuses, self._timeline = jobs, statuses, timeline

    def GetRenderJobList(self):
        return self._jobs

    def GetRenderJobStatus(self, job_id):
        status = self._statuses.get(job_id)
        if status == "boom":
            raise RuntimeError("gone")
        return status

    def GetCurrentTimeline(self):
        return self._timeline


class _Controller:
    def __init__(self, project):
        self._project = project

    def current_project(self):
        return self._project


IDENTITY = {"NodeIndex": 1, "Slope": "1.0 1.0 1.0", "Offset": "0.0 0.0 0.0", "Power": "1.0 1.0 1.0", "Saturation": "1.0"}
WARM = {"NodeIndex": 1, "Slope": "1.1 1.0 0.9", "Offset": "0.0 0.0 0.0", "Power": "1.0 1.0 1.0", "Saturation": "1.0"}


class ReadTests(unittest.TestCase):
    def test_render_jobs(self):
        jobs = [{"JobId": "a", "RenderJobName": "Master", "TimelineName": "Cut", "IsExportVideo": True},
                {"JobId": "b", "IsExportVideo": False},                     # audio only: Buddy's own measuring
                {"JobId": "c", "TimelineName": "Cut"}, "junk", {"NoId": 1}]
        project = _Project(jobs, {"a": {"JobStatus": "Complete", "TimeTakenToRenderInMs": "61500"}, "c": "boom"})
        got = resolve_stats.render_jobs(_Controller(project))
        self.assertEqual(got, [{"id": "a", "name": "Master", "timeline": "Cut", "status": "Complete", "ms": 61500},
                               {"id": "c", "name": "", "timeline": "Cut", "status": "", "ms": 0}])

    def test_no_project_raises_the_connection_error(self):
        from core.resolve_bridge import ResolveConnectionError
        with self.assertRaises(ResolveConnectionError):
            resolve_stats.render_jobs(_Controller(None))

    def test_timeline_facts_count_clips_length_and_grades(self):
        tracks = [[_Item(1, IDENTITY), _Item(3), _Item(1, WARM), _Item(fails=True)], [_Item(1), _Item(2, IDENTITY)]]
        facts = resolve_stats.timeline_facts(_Controller(_Project([], {}, _Timeline(tracks))))
        self.assertEqual(facts, {"id": "tl-1", "name": "Cut", "clips": 6, "seconds": 90, "graded": 3, "looked": 6})
        self.assertEqual(resolve_stats.timeline_facts(_Controller(_Project([], {}, None))), {})

    def test_a_long_timeline_is_looked_at_so_far(self):
        tracks = [[_Item(2) for _ in range(resolve_stats.MAX_ITEMS + 50)]]
        facts = resolve_stats.timeline_facts(_Controller(_Project([], {}, _Timeline(tracks))))
        self.assertEqual((facts["clips"], facts["looked"], facts["graded"]),
                         (resolve_stats.MAX_ITEMS + 50, resolve_stats.MAX_ITEMS, resolve_stats.MAX_ITEMS))

    def test_identity_cdl(self):
        self.assertTrue(resolve_stats._identity_cdl(IDENTITY))
        self.assertFalse(resolve_stats._identity_cdl(WARM))
        self.assertFalse(resolve_stats._identity_cdl({**IDENTITY, "Saturation": "0.8"}))
        self.assertTrue(resolve_stats._identity_cdl({"Slope": "nonsense"}))     # unreadable: not a grade


class WatchTests(unittest.TestCase):
    def test_a_render_counts_once_seen_waiting_then_complete(self):
        job = lambda status, ms=0: {"id": "j1", "status": status, "ms": ms, "timeline": "Cut"}    # noqa: E731
        noted, finished = render_changes({}, [job("Complete", 5000)])        # complete before Buddy looked: no
        self.assertEqual((noted, finished), ({"j1": "Complete"}, []))
        noted, finished = render_changes({}, [job("Ready")])
        self.assertEqual((noted, finished), ({"j1": "Ready"}, []))
        noted, finished = render_changes(noted, [job("Rendering")])
        self.assertEqual(finished, [])
        noted, finished = render_changes(noted, [job("Complete", 61500)])
        self.assertEqual(finished, [{"id": "j1", "ms": 61500, "timeline": "Cut"}])
        noted, finished = render_changes(noted, [job("Complete", 61500)])   # still complete: counted already
        self.assertEqual((noted, finished), ({"j1": "Complete"}, []))
        noted, finished = render_changes(noted, [])                         # deleted: forgotten
        self.assertEqual(noted, {})

    def test_junk_jobs_are_skipped(self):
        noted, finished = render_changes({"x": "Ready"}, ["junk", {"status": "Complete"}, {"id": "x", "status": "Complete"}])
        self.assertEqual(finished, [{"id": "x", "ms": 0, "timeline": ""}])

    def test_grades_count_once_per_timeline(self):
        noted, more = grade_changes({}, {"id": "tl-1", "graded": 4})
        self.assertEqual((noted, more), ({"tl-1": 4}, 4))
        noted, more = grade_changes(noted, {"id": "tl-1", "graded": 3})        # fewer now: nothing
        self.assertEqual((noted, more), ({"tl-1": 4}, 0))
        noted, more = grade_changes(noted, {"id": "tl-1", "graded": 10})
        self.assertEqual((noted, more), ({"tl-1": 10}, 6))
        noted, more = grade_changes(noted, {"id": "tl-2", "graded": 1})
        self.assertEqual((noted, more), ({"tl-1": 10, "tl-2": 1}, 1))
        self.assertEqual(grade_changes(noted, {"graded": 5}), (noted, 0))      # no id: nothing


if __name__ == "__main__":
    unittest.main()
