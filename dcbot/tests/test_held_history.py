"""Owner's report 2026-10-20: a conversation that went on in a channel all along had its whole history posted again
by `altention dc post <channel> --resume <id>`, after a !cwd. A conversation begun in the channel was noted as the
channel's own (st["history"]) only when the channel left it by !reset or !resume, and !cwd noted it not at all.
Now: every conversation the channel's Claude starts or takes up is the channel's own at once; !cwd notes the one it
leaves; and dcpost --resume takes --quiet, as !resume does."""
import asyncio
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

from bot import ask, config, history, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SessionStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = (ask.ASKS, ask.ANSWERS, config.INTERIM)
        ask.ASKS, ask.ANSWERS, config.INTERIM = os.path.join(self.tmp.name, "a"), os.path.join(self.tmp.name, "b"), 0
        self.addCleanup(lambda: (setattr(ask, "ASKS", saved[0]), setattr(ask, "ANSWERS", saved[1]), setattr(config, "INTERIM", saved[2])))
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.st = self.bridge.host.chan(self.ch)

    def ev(self, sid, source="startup"):
        return {"channel": "5", "hook_event_name": "SessionStart", "session_id": sid, "source": source}

    def test_a_conversation_begun_in_the_channel_is_its_own_at_once(self):
        asyncio.run(self.bridge.handle(self.ev("f0f9fc73-1")))
        self.assertEqual(self.st["history"], ["f0f9fc73-1"])
        asyncio.run(self.bridge.handle(self.ev("new-2", "clear")))
        self.assertEqual(self.st["history"], ["f0f9fc73-1", "new-2"])

    def test_taken_up_at_the_terminal_it_is_told_what_the_channel_held_before(self):
        self.st["session_id"] = "old"
        held = []

        async def show(bot, ch, st, sid, path, force=False, held_=None, **kw):
            held.append(kw.get("held", held_))
            return True
        with mock.patch.object(history, "show", lambda *a, **k: show(*a, **k)):
            async def go():
                await self.bridge.handle(self.ev("taken-up", "resume"))
                await asyncio.sleep(0)
            asyncio.run(go())
        self.assertEqual(held, [["old"]], "the one taken up is not among them unless it was before")
        self.assertIn("taken-up", self.st["history"])


class Cwd(unittest.TestCase):
    def test_cwd_notes_the_conversation_it_leaves(self):
        from bot import main as botmain
        bot = botmain.Bot.__new__(botmain.Bot)
        st = {"cwd": "/w", "session_id": "f0f9fc73-1", "mode": "tmux"}
        bot.channels_state = {"7": st}
        bot.save = lambda: None

        async def nothing(*a, **k):
            return None
        bot.audit = nothing
        bot.tmux = type("T", (), {"close": staticmethod(nothing)})()
        said = []

        async def send(text, view=None):
            said.append(text)
        ch = type("C", (), {"id": 7, "name": "c", "category": None})()
        with tempfile.TemporaryDirectory() as there:
            asyncio.run(botmain.Bot.command(bot, ch, "!cwd " + there, send))
        self.assertNotIn("session_id", st)
        self.assertEqual(st["history"], ["f0f9fc73-1"], "still the channel's own: taken up later, only its last turns")


class DcpostQuiet(unittest.TestCase):
    def load(self):
        path = os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py")
        loader = importlib.machinery.SourceFileLoader("dcpost_q", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcpost_q", loader))
        loader.exec_module(mod)
        return mod

    def test_the_command_line(self):
        mod = self.load()
        p = mod.build(["secretary", "--resume", "f0f9fc73", "--quiet"], "", {}, "h", "/x")
        self.assertEqual((p["resume"], p.get("quiet"), p["text"]), ("f0f9fc73", True, ""))
        self.assertNotIn("quiet", mod.build(["secretary", "--resume", "f0f9fc73"], "", {}, "h", "/x"))

    def test_the_bot_switches_quietly(self):
        from bot import posts
        from tests.test_posts import Bot, Chan
        b = Bot()
        c = Chan(2, "secretary")
        b.chans, b.guild.text_channels = {2: c}, [c]
        b.channels_state = {"2": {"tmux": "dc-s", "cwd": "/w"}}
        asked = []

        async def resume(ch, st, ref):
            asked.append(ref)
            return "已切到對話"
        b.resume = resume
        asyncio.run(posts.Posts(b).handle({"channel": "2", "resume": "f0f9fc73", "quiet": True, "from_name": "x"}))
        asyncio.run(posts.Posts(b).handle({"channel": "2", "resume": "f0f9fc73", "from_name": "x"}))
        self.assertEqual(asked, ["f0f9fc73 --quiet", "f0f9fc73"])


if __name__ == "__main__":
    unittest.main()
