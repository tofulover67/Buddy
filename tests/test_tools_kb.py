"""What Ask Buddy is told about the tools (core/tools_kb.py): a text's
[[win:...]] and [[mac:...]] parts read right on each platform, and none is
ever left in for the agent to see."""

import unittest
from unittest import mock

import _paths  # noqa: F401
from core import tools_kb


class PlatformTextTests(unittest.TestCase):
    def test_each_platform_reads_its_own_wording(self):
        text = "hold [[win:Alt]][[mac:Option]] and scroll[[mac:, or pinch]]"
        with mock.patch.object(tools_kb.sys, "platform", "win32"):
            self.assertEqual(tools_kb._here(text), "hold Alt and scroll")
        with mock.patch.object(tools_kb.sys, "platform", "darwin"):
            self.assertEqual(tools_kb._here(text), "hold Option and scroll, or pinch")

    def test_no_tool_is_described_with_a_marker_left_in(self):
        for platform in ("win32", "darwin"):
            with mock.patch.object(tools_kb.sys, "platform", platform):
                tools = tools_kb.load_tools()
                self.assertTrue(tools)
                for tool in tools:
                    with self.subTest(platform=platform, tool=tool.tool_id):
                        self.assertNotIn("[[", tool.detail())
                        self.assertNotIn("]]", tool.detail())

    def test_generative_says_what_is_windows_only_on_a_mac(self):
        with mock.patch.object(tools_kb.sys, "platform", "darwin"):
            mac = tools_kb.get_tool("generative").detail()
        with mock.patch.object(tools_kb.sys, "platform", "win32"):
            windows = tools_kb.get_tool("generative").detail()
        self.assertIn("Graphics card (Windows only, not on a Mac)", mac)
        self.assertIn("Option + scroll wheel", mac)
        self.assertNotIn("Windows only", windows)
        self.assertIn("Alt + scroll wheel", windows)


if __name__ == "__main__":
    unittest.main()
