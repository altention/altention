"""Messaging review P0-2 (C-186, manager 11-12): who sent a post is the Claude Code altention dc post runs under, by
that Claude's own environment; the command's own DCBOT_CHANNEL is never taken, a different one is said in #audit;
a Claude Code with no channel counts for the talk limit, a program (no Claude above it) does not."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import importlib.machinery
import importlib.util
import os
import unittest

from bot import posts
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def dcpost():
    loader = importlib.machinery.SourceFileLoader("dcpost_p02", os.path.join(ENGINE, "altention", "dc", "post.py"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcpost_p02", loader))
    loader.exec_module(mod)
    return mod


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.build = dcpost().build

    def test_the_claudes_environment_not_the_commands(self):
        p = self.build(["st-0", "x"], "", {"DCBOT_CHANNEL": "9"}, "lap", "/w/proj", claude={"DCBOT_CHANNEL": "5"})
        self.assertEqual((p["from_channel"], p["claimed_channel"], p["verified"], p["from_name"]),
                         ("5", "9", True, "lap 上的 Claude（proj）"))
        p = self.build(["st-0", "x"], "", {"DCBOT_CHANNEL": "5"}, "lap", "/w/proj", claude={"DCBOT_CHANNEL": "5"})
        self.assertNotIn("claimed_channel", p, "the same: nothing to say")
        p = self.build(["st-0", "x"], "", {"DCBOT_CHANNEL": "9", "TMUX": "/x,1,0", "TMUX_PANE": "%9"}, "lap", "/w",
                       claude={"TMUX": "/s,1,0", "TMUX_PANE": "%3"})
        self.assertEqual((p["pane"], p["claimed_channel"], p.get("from_channel")), ("lap|/s|%3", "9", None))

    def test_a_claude_with_neither(self):
        p = self.build(["st-0", "x"], "", {}, "lap", "/w/proj", claude={})
        self.assertEqual((p["verified"], p.get("from_channel"), p.get("pane"), p["from_name"]),
                         (True, None, None, "lap 上的 Claude（proj）"))

    def test_no_claude_above_is_a_program(self):
        p = self.build(["st-0", "x"], "", {"DCBOT_CHANNEL": "9", "TMUX": "/s,1,0", "TMUX_PANE": "%3"}, "lap", "/w/proj")
        self.assertEqual((p.get("from_channel"), p.get("pane"), p.get("verified"), p["from_name"], p["claimed_channel"]),
                         (None, None, None, "lap 上的程式（proj）", "9"))


class Viewed(Chan):
    async def send(self, text=None, view=None, **kw):
        self.views = getattr(self, "views", []) + [[i.label for i in view.children] if view is not None else None]
        return await Chan.send(self, text, view=view, **kw)

    def get_partial_message(self, mid):
        class Msg(object):
            async def edit(self, **kw):
                pass
        return Msg()


class Bot_(unittest.TestCase):
    def setUp(self):
        self.bot = Bot()
        self.a, self.b = Viewed(1, "st-1"), Viewed(2, "st-0")
        self.bot.chans = {1: self.a, 2: self.b}
        self.bot.guild.text_channels = [self.a, self.b]
        self.bot.channels_state = {"1": {"pane": {"id": "%1"}}, "2": {"tmux": "dc-x", "talk_limit": 2}}
        self.p = posts.Posts(self.bot)

    def handled(self, **post):
        async def go():
            await self.p.handle(dict(post))
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(go())

    def test_a_different_claim_is_said_in_audit_and_the_post_goes(self):
        self.handled(channel="st-0", text="hi", from_channel="1", claimed_channel="2", verified=True)
        self.assertEqual(self.b.sent[-1], "📨 來自 #st-1\nhi", "as the one looked up")
        self.assertIn("#st-1 的 Claude 傳到 #st-0 的訊息自稱來自 #st-0", self.bot.audits[-1])
        self.handled(channel="st-0", text="hi", from_name="lap 上的程式（w）", claimed_channel="777")
        self.assertIn("lap 上的程式（w） 傳到 #st-0 的訊息自稱來自 777", self.bot.audits[-1])
        n = len(self.bot.audits)
        self.handled(channel="st-0", text="hi", from_channel="1", claimed_channel="1", verified=True)
        self.assertEqual(len(self.bot.audits), n)

    def test_a_claude_without_a_channel_counts_a_program_does_not(self):
        claude = dict(from_name="lap 上的 Claude（w）", from_host="lap", verified=True)
        for i in range(3):
            self.handled(channel="st-0", text="m%d" % i, **claude)
        self.assertEqual(len(self.bot.tmux.given), 2, "held past st-0's own limit")
        self.assertIn("⏸️ lap 上的 Claude（w） 和這個頻道的 Claude 已經連續來回 2 次", self.b.sent[-1])
        self.assertEqual(self.b.views[-1], ["繼續轉交", "停下來"], "no pair to let go")
        self.assertIn("/talklimit", self.b.sent[-1])
        self.p.owner_spoke(2)
        self.handled(channel="st-0", text="after", **claude)
        self.assertEqual(len(self.bot.tmux.given), 3, "the owner spoke: a fresh stretch")
        for i in range(10):
            self.handled(channel="st-0", text="cron %d" % i, from_name="lap 上的程式（w）", from_host="lap")
        self.assertEqual(len(self.bot.tmux.given), 13, "a program is never held")

    def test_the_held_claudes_post_goes_on_by_the_channels_own_claude(self):
        claude = dict(from_name="lap 上的 Claude（w）", from_host="lap", verified=True)
        for i in range(3):
            self.handled(channel="st-0", text="m%d" % i, **claude)
        self.assertIn(2, self.p.held)
        self.handled(channel="st-0", talk_continue=True, why="w", from_channel="2", verified=True)
        self.assertNotIn(2, self.p.held)
        self.assertEqual(len(self.bot.tmux.given), 3)
        self.assertIn("#st-0 的 Claude（查證過）讓轉交繼續", self.bot.audits[-1])
        self.handled(channel="st-0", text="next", **claude)
        self.assertEqual(len(self.bot.tmux.given), 4, "counted afresh")

    def test_talk_continue_says_whether_the_asking_claude_was_looked_up(self):
        """#code-review-2 on a58eba48: the mark is the asking Claude's, not the held post's."""
        claude = dict(from_name="lap 上的 Claude（w）", from_host="lap", verified=True)
        for i in range(3):
            self.handled(channel="st-0", text="m%d" % i, **claude)
        self.handled(channel="st-0", talk_continue=True, why="w", from_channel="2")
        self.assertIn("#st-0 的 Claude（自稱）讓轉交繼續", self.bot.audits[-1])
        for _ in range(2):  # (the second and third of the day)
            for i in range(3):
                self.handled(channel="st-0", text="m%d" % i, **claude)
            self.handled(channel="st-0", talk_continue=True, why="w", from_channel="2", verified=True)
        self.assertIn("#st-0 的 Claude（查證過）想繼續轉交，沒送", self.bot.audits[-1])

    def test_pause_lines_say_looked_up_or_said(self):
        self.handled(channel="st-0", pause=1800003600, why="x", from_channel="1", verified=True)
        self.assertIn("#st-1（查證過） 暫停 #st-0", self.bot.audits[-1])
        self.handled(channel="st-0", unpause=True, why="y", from_channel="1")
        self.assertTrue(any("#st-1（自稱） 提早恢復 #st-0" in a for a in self.bot.audits), self.bot.audits)


if __name__ == "__main__":
    unittest.main()
