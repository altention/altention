"""v0.6.1 item 4: altention dc post <channel> --reset --why "…" [--then "…"] (bot/resets.py, through posts.Posts.handle;
the bridge's turn end: after_turn())."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import types
import unittest

from bot import posts, resets, where
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan, Tmux

NOW = 1_800_000_000


class Closing(Tmux):
    def __init__(self):
        super().__init__()
        self.closed, self.rts = [], {}

    async def close(self, ch, kill=True):
        self.closed.append(ch.id)


class Base(unittest.TestCase):
    def setUp(self):
        self.bot = Bot()
        self.bot.tmux = Closing()
        self.a, self.b, self.notes = Chan(1, "st-1"), Chan(2, "st-0"), Chan(3, "notes")
        self.bot.chans = {1: self.a, 2: self.b, 3: self.notes}
        self.bot.guild.text_channels = [self.a, self.b, self.notes]
        self.bot.channels_state = {"1": {"tmux": "dc-a", "session_id": "aaaa-1", "cwd": "/w"},
                                   "2": {"tmux": "dc-x", "session_id": "bbbb-2", "cwd": "/w"},
                                   "3": {"plain": True}}
        self.p = posts.Posts(self.bot)

    def handled(self, **post):
        got = {"messages": []}
        post.setdefault("why", "context 滿了")
        post.setdefault("from_channel", "1")
        post.setdefault("wait", 30)

        async def go():
            posts.SENT.set(got)
            await self.p.handle(dict(post, reset=True))
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(go())
        return posts.receipt_of(got)

    def given_to(self, cid):
        return [t for c, t in self.bot.tmux.given if c == cid]


class AnotherChannel(Base):
    def test_an_idle_channel_gets_a_new_conversation_said_there_and_in_audit(self):
        r = self.handled(channel="st-0")
        self.assertTrue(r["ok"], r)
        self.assertIn("#st-0 開了新對話", r["text"])
        self.assertEqual(self.bot.tmux.closed, [2], "the session closed, as !reset")
        st = self.bot.channels_state["2"]
        self.assertNotIn("session_id", st)
        self.assertEqual(st["history"], ["bbbb-2"], "the old one kept for !resume")
        self.assertIn("🔄 由 #st-1 開了新對話：context 滿了", self.b.sent[-1])
        self.assertEqual(self.bot.audits, ["🔄 altention dc post --reset：#st-1 讓 #st-0 開了新對話（context 滿了）"])
        self.assertEqual(self.given_to(2), [], "no --then: nothing typed into the new one")
        rec = where.record(st, "st-0", "lap", "dcbot")
        self.assertIsNone(rec["agent"]["conversation"], "the C-201 record has no conversation now")

    def test_then_is_handed_as_the_senders_post(self):
        r = self.handled(channel="st-0", then="接續請讀 docs/h.md")
        self.assertTrue(r["ok"])
        self.assertIn("--then", r["text"])
        got = self.given_to(2)
        self.assertEqual(len(got), 1)
        self.assertIn("接續請讀 docs/h.md", got[0])
        self.assertIn("#st-1", got[0], "said as #st-1's post: its Claude can answer back")
        self.assertIn("接著交給新對話：接續請讀 docs/h.md", self.bot.audits[-1])

    def test_a_busy_channel_is_refused_with_why_and_nothing_happens(self):
        for field, value, said in (("busy", True, "正在跑一輪"), ("bg", 2, "有 2 個背景工作"),
                                   ("subs", {"x": {}}, "有 1 個子代理或 workflow")):
            with self.subTest(field=field):
                st = self.bot.channels_state["2"]
                st[field] = value
                r = self.handled(channel="st-0")
                self.assertFalse(r["ok"])
                self.assertIn(said, r["text"])
                self.assertIn("沒有強制", r["text"])
                st.pop(field)
        self.bot.tmux.rts["2"] = types.SimpleNamespace(pending=[object()])
        r = self.handled(channel="st-0")
        self.assertFalse(r["ok"])
        self.assertIn("還有 1 則訊息排著", r["text"])
        self.assertEqual((self.bot.tmux.closed, self.bot.audits, self.b.sent), ([], [], []))
        self.assertEqual(self.bot.channels_state["2"]["session_id"], "bbbb-2")

    def test_what_is_no_claude_or_the_owners_own_pane_is_refused(self):
        self.assertIn("不是有 Claude 對話的頻道", self.handled(channel="notes")["text"])
        self.bot.channels_state["2"] = {"pane": {"id": "%4"}, "session_id": "cccc"}  # attached: the owner's terminal
        r = self.handled(channel="st-0")
        self.assertFalse(r["ok"])
        self.assertIn("使用者自己在 tmux 開的對話", r["text"])
        self.assertEqual(self.bot.tmux.closed, [])

    def test_a_paused_channel_is_refused(self):
        self.bot.channels_state["2"]["paused"] = {"until": NOW + 3600}
        r = self.handled(channel="st-0")
        self.assertFalse(r["ok"])
        self.assertIn("暫停中", r["text"])
        self.assertEqual(self.bot.tmux.closed, [])


class OwnChannel(Base):
    def test_during_its_turn_it_waits_for_the_turn_to_end_then_the_handover(self):
        st = self.bot.channels_state["1"]
        st["busy"] = True
        r = self.handled(channel="st-1")
        self.assertTrue(r["ok"])
        self.assertIn("這一輪結束後", r["text"])
        self.assertEqual(self.bot.tmux.closed, [], "not in the middle of the turn that asked")
        self.assertEqual(st["reset_after"]["why"], "context 滿了")
        st["busy"] = False

        async def end():
            task = self.p.resets.after_turn(self.a, st)
            if task is not None:
                await task
        asyncio.run(end())
        self.assertEqual(self.bot.tmux.closed, [1])
        self.assertNotIn("reset_after", st)
        self.assertEqual(st["history"], ["aaaa-1"])
        self.assertIn("🔄 這個頻道的 Claude 開了新對話：context 滿了", self.a.sent[-1])
        got = self.given_to(1)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("（新對話）"), got)
        self.assertIn("交接檔", got[0])
        asyncio.run(end())
        self.assertEqual(self.bot.tmux.closed, [1], "once")

    def test_then_replaces_the_handover_words(self):
        st = self.bot.channels_state["1"]
        st["busy"] = True
        self.handled(channel="st-1", then="接續請讀 memory/handover.md")
        async def end():
            await self.p.resets.after_turn(self.a, st)
        asyncio.run(end())
        self.assertEqual(self.given_to(1), ["接續請讀 memory/handover.md"], "as it is: not framed as a post")

    def test_its_own_busy_channel_is_not_refused_and_idle_is_done_now(self):
        r = self.handled(channel="st-1")
        self.assertTrue(r["ok"])
        self.assertEqual(self.bot.tmux.closed, [1], "no turn running (a script): now")


from tests import test_calls  # noqa: E402


class ThroughTheBridge(test_calls.Base):
    """#code-review on 882c1062 (must-fix): the reset is tied to the turn that asked for it (its prompt_id). Esc sends
    no Stop: a new turn beginning first drops it, said quietly there and in #audit."""
    def setUp(self):
        super().setUp()
        host = self.bridge.host
        host.tmux = self.bridge
        host.posts = types.SimpleNamespace(bot=host)
        host.posts.resets = self.resets = resets.Resets(host.posts)
        host.channels_state["5"] = {"tmux": "dc-c", "session_id": "s1", "cwd": "/w"}
        self.closed, self.typed = [], []

        async def close(ch, kill=True):
            self.closed.append(ch.id)

        async def submit(origin, text, **kw):
            self.typed.append(text)
        self.bridge.close, self.bridge.submit = close, submit

    def asked_in(self, pid):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go", prompt_id=pid))
            ok, said = await self.resets.ask(self.ch, self.ch, "#c", {"why": "context 滿了", "from_channel": "5"})
            self.assertTrue(ok, said)
            self.assertIn("這一輪結束後", said)
        asyncio.run(go())
        return self.bridge.host.channels_state["5"]

    def test_a_new_turn_before_the_asking_one_ended_drops_it(self):
        st = self.asked_in("p1")
        self.assertEqual(st["reset_after"]["turn"], "p1")

        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="（使用者按了 Esc，給新的指示）", prompt_id="p2"))
            self.assertNotIn("reset_after", st)
            await self.bridge.handle(self.ev("Stop", prompt_id="p2", last_assistant_message="照新的指示做完了"))
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(go())
        self.assertEqual((self.closed, self.typed), ([], []), "the new turn's conversation stays")
        self.assertEqual(st.get("session_id"), "abc", "not closed (the events' own conversation id)")
        self.assertTrue(any("--reset 取消" in (m.content or "") for m in self.ch.sent), [m.content for m in self.ch.sent])
        self.assertTrue(any("--reset 取消" in a for a in self.bridge.host.audits), self.bridge.host.audits)

    def test_the_asking_turns_own_end_does_it(self):
        st = self.asked_in("p1")

        async def go():
            await self.bridge.handle(self.ev("Stop", prompt_id="p1", last_assistant_message="交接寫好了"))
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(go())
        self.assertEqual(self.closed, [5])
        self.assertNotIn("reset_after", st)
        self.assertEqual(len(self.typed), 1)
        self.assertIn("交接檔", self.typed[0])

    def test_another_turns_end_does_nothing(self):
        st = self.asked_in("p1")
        self.assertIsNone(self.resets.after_turn(self.ch, st, "p9"))
        self.assertIn("reset_after", st, "kept: the asking turn's own end, or a new turn, settles it")


class TheBridge(unittest.TestCase):
    def test_the_turn_end_calls_after_turn(self):
        """The bridge's TURN_END: a reset asked during the turn is done after the reply."""
        import inspect
        from bot import tmux_bridge
        src = inspect.getsource(tmux_bridge.TmuxBridge.handle)
        end = src.index("elif what == cc_hooks.TURN_FAILED")
        self.assertIn("self.host.posts.resets.after_turn(ch, st, ev.get(\"prompt_id\"))", src[src.rindex("post_reply", 0, end):end])

    def test_the_owners_reset_goes_the_same_way(self):
        import inspect
        from bot import main
        src = inspect.getsource(main.Bot.command)
        part = src[src.index('elif cmd == "!reset"'):src.index('elif cmd == "!resume"')]
        self.assertIn("resets.new_conversation(self, ch, st)", part)


if __name__ == "__main__":
    unittest.main()
