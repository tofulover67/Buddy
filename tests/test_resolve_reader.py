"""The Resolve reader's process (core/resolve_reader_child.py): what it says
first, how it answers each read, and when it gives up - on a fake Resolve,
through in-memory pipes."""

import io
import json
import unittest

import _paths  # noqa: F401
from core import resolve_reader_child as child


class NoProject:
    """A Resolve with no project open."""

    def current_project(self):
        return None


class Gone:
    """A Resolve that's gone away: every call fails."""

    def current_project(self):
        raise RuntimeError("connection lost")


def run(lines, controller):
    inp = io.StringIO("".join(json.dumps(line) + "\n" for line in lines))
    out = io.StringIO()
    code = child.main(inp, out, connect=lambda: controller)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


class ChildTests(unittest.TestCase):
    def test_says_it_is_ready_then_answers_each_read(self):
        code, said = run([{"id": 1, "do": "zoom.summary"}, {"id": 2, "do": "animation.summary"}], NoProject())
        self.assertEqual(code, 0)
        self.assertEqual(said[0], {"ready": True})
        self.assertEqual([s["id"] for s in said[1:]], [1, 2])
        self.assertTrue(all(s["ok"] for s in said[1:]))
        self.assertEqual(said[1]["result"]["reason"], "no_timeline")

    def test_only_its_own_reads(self):
        _code, said = run([{"id": 3, "do": "os.remove", "args": {"path": "x"}}], NoProject())
        self.assertFalse(said[1]["ok"])
        self.assertIn("unknown read", said[1]["error"])

    def test_a_resolve_it_cannot_reach(self):
        out = io.StringIO()

        def refuse():
            raise RuntimeError("no Resolve")

        self.assertEqual(child.main(io.StringIO(""), out, connect=refuse), 1)
        said = json.loads(out.getvalue())
        self.assertFalse(said["ready"])
        self.assertIn("no Resolve", said["error"])

    def test_gives_up_once_resolve_has_gone(self):
        reads = [{"id": n, "do": "zoom.summary"} for n in range(10)]
        code, said = run(reads, Gone())
        self.assertEqual(code, 2)
        self.assertEqual(len(said), 1 + child.GIVE_UP_AFTER)               # then Buddy starts a fresh one
        self.assertEqual(said[1]["type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
