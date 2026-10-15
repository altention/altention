"""Forwarded messages reach Claude, merged with the comment that follows; dcread finds its way to dcctl."""
import asyncio
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest

import discord

from bot import config, util
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


class Snap(object):
    def __init__(self, content):
        self.content, self.attachments = content, []


class Chan(object):
    id, name = 50, "here"


class Msg(object):
    def __init__(self, content, snaps=(), mid=1):
        self.content, self.message_snapshots, self.channel, self.id, self.attachments = content, list(snaps), Chan(), mid, []
        self.reference = type("Ref", (), {"channel_id": 77, "message_id": 5, "type": discord.MessageReferenceType.forward})() if snaps else None


class Forward(unittest.TestCase):
    def setUp(self):
        self.old_state = config.STATE
        config.STATE = tempfile.mkdtemp()
        from bot import main as botmain
        self.bot = botmain.Bot()
        self.bot.FORWARD_WAIT = 0.3
        self.bot.get_channel = lambda cid: None  # (the channel a forward comes from is not to be had: tests/test_forwarded.py has it)

        async def no_channel(cid):
            raise discord.NotFound(type("R", (), {"status": 404, "reason": "gone"})(), "gone")
        self.bot.fetch_channel = no_channel
        self.got = []

        async def deliver(message, text):
            self.got.append((message.id, text))
        self.bot.deliver = deliver

    def tearDown(self):
        config.STATE = self.old_state

    def test_context_text(self):
        self.assertEqual(util.forward_context("使用者（在 #st-0）", "line a\nline b", "2026-10-13 15:28:13", 5, "https://x/5"),
                         util.FORWARDED + "\n[2026-10-13 15:28:13] 使用者（在 #st-0） (id 5)\n連結：https://x/5\n> line a\n> line b\n")
        self.assertEqual(util.forward_context(None, ""), util.FORWARDED + "\n別的頻道的訊息\n> （沒有文字）\n")
        long = util.forward_context("x", "字" * 7000, notes=["（貼圖：a）"])
        self.assertIn("\n（原訊息共 7000 字，這裡是前 6000 字；全文用上面的連結看）\n（貼圖：a）\n", long)
        self.assertEqual(long.count("字" * 6000 + "…"), 1)

    def test_forward_then_comment_is_one_prompt(self):
        async def go():
            await self.bot.forwarded(Msg("", [Snap("the original")]))  # attachments-free: nothing saved
            self.bot.hold_forward(Msg("", [Snap("the original")], 1), "FWD\n")
            await asyncio.sleep(0.1)
            text = "look at this"
            held = self.bot.forwards.pop(50, None)
            held[2].cancel()
            await self.bot.deliver(Msg(text, mid=2), await self.bot.held_text(held[1]) + text)
            await asyncio.sleep(0.5)
            self.assertEqual(self.got, [(2, "FWD\nlook at this")], "the forward is not sent on its own as well")
        asyncio.run(go())

    def test_forward_alone_goes_after_a_moment(self):
        async def go():
            self.bot.hold_forward(Msg("", [Snap("x")], 1), "FWD-A\n")
            self.bot.hold_forward(Msg("", [Snap("y")], 3), "FWD-B\n")  # several forwards in a row go together
            await asyncio.sleep(0.6)
            self.assertEqual(self.got, [(3, "FWD-A\nFWD-B")])
        asyncio.run(go())

    def test_forwarded_text_names_the_origin(self):
        text = asyncio.run(self.bot.forwarded(Msg("", [Snap("hello there")])))
        self.assertEqual(text, util.FORWARDED + "\n別的頻道 的訊息（查不到是誰說的：原訊息或它的頻道已經不在） (id 5)\n> hello there\n")
        self.assertEqual(asyncio.run(self.bot.forwarded(Msg("plain"))), "")


class Tools(unittest.TestCase):
    def test_dcctl_shows_forwards(self):
        dcctl = load(os.path.join(ROOT, "tools", "dcctl"), "dcctl")
        m = {"id": "1", "timestamp": "2026-10-03T10:50:11+00:00", "author": {"username": "owner"}, "content": "",
             "message_snapshots": [{"message": {"content": "the original", "attachments": [{"filename": "a.png", "url": "u"}]}}]}
        out = dcctl.format_message(m)
        self.assertIn("[轉發] the original", out)
        self.assertIn("[附件] a.png u", out)

    def test_dcread_arguments(self):
        dcread = load(os.path.join(os.path.dirname(ROOT), "altention", "dc", "read.py"), "dcread")
        self.assertEqual(dcread.dcctl_args(["#bot-test"]), ["history", "bot-test", "20", "--chars", "1500"])
        self.assertEqual(dcread.dcctl_args(["bot-test", "50"]), ["history", "bot-test", "50", "--chars", "1500"])
        self.assertEqual(dcread.dcctl_args(["bot-test", "--threads"]), ["threads", "bot-test"])
        self.assertEqual(dcread.dcctl_args(["bot-test", "--claude", "3"]), ["recap", "bot-test", "3"])
        self.assertEqual(dcread.dcctl_args(["bot-test", "--claude"]), ["recap", "bot-test", "5"])


if __name__ == "__main__":
    unittest.main()
