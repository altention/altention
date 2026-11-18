"""C-103 (owner 2026-11-03): the owner is @-mentioned only when something needs them, once for each thing, in a line
that says what is needed first and mentions them last; the line ends with ✅ once that thing is over. What stops the
whole installation is said once, in the notification channel, and said over there without a mention."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import os
import tempfile
import time
import unittest
from unittest import mock

from bot import ask, calls, config, messages, tmux_bridge
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host

OWNER = 42
Q1 = {"question": "Which colour should the logo be?", "header": "Colour", "options": [{"label": "Red"}, {"label": "Blue"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, config.STATE)
        self.addCleanup(lambda: (setattr(ask, "ASKS", saved[0]), setattr(ask, "ANSWERS", saved[1]), setattr(config, "OWNER_ID", saved[2]),
                                 setattr(config, "INTERIM", saved[3]), setattr(config, "STATE", saved[4])))
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM, config.STATE = OWNER, 0, os.path.join(self.tmp.name, "state")
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.notify = Ch()
        self.notify.id, self.notify.name = 77, "notifications"

        async def notify_channel():
            return self.notify
        self.bridge.notify_channel = notify_channel

    def ev(self, kind, **kw):
        return dict({"channel": "5", "hook_event_name": kind, "session_id": "abc"}, **kw)

    def run_(self, *coros):
        async def go():
            for c in coros:
                await c
        asyncio.run(go())

    def mentions(self, ch=None):
        """The lines that ring the owner: their text, and what they were sent with."""
        return [m for m in (ch or self.ch).sent if "<@%d>" % OWNER in (m.content or "")]

    def rings(self, m):
        am = m.kw.get("allowed_mentions")
        return am is not None and am.users and [u.id for u in am.users] == [OWNER] and not am.everyone and not am.roles


class Line(unittest.TestCase):
    def test_purpose_first_mention_last_only_the_gist_cut(self):
        with mock.patch.object(config, "OWNER_ID", OWNER):
            text = calls.line("ask", "x" * 200)
        self.assertTrue(text.startswith("🔘 要你選：x"))
        self.assertTrue(text.endswith(" <@42>"))
        self.assertEqual(len(text.split("：", 1)[1].rsplit(" ", 1)[0]), calls.GIST, "the gist alone is cut, with …")
        self.assertTrue(text.split(" <@")[0].endswith("…"))

    def test_the_discussion_mark(self):
        self.assertEqual(calls.discussed("💬 要跟你討論：v0.5.1 要不要等 C-152\n\n細節…"), "v0.5.1 要不要等 C-152")
        self.assertIsNone(calls.discussed("做完了。\n💬 要跟你討論：後面才寫的不算"))
        self.assertIsNone(calls.discussed("💬 要跟你討論："), "nothing after it: nothing to say")
        self.assertIsNone(calls.discussed(""))


class InTheChannel(Base):
    def test_a_question_is_mentioned_under_it_once_and_marked_when_answered(self):
        async def go():
            await self.bridge.handle(self.ev("Ask", ask_id="1-1", questions=[Q1]))
            question, mention = self.ch.sent[-2:]
            self.assertEqual(mention.content, "🔘 要你選：Which colour should the logo be? <@42>")
            self.assertTrue(self.rings(mention))
            self.assertEqual(mention.kw["reference"].message_id, question.id, "a reply to the buttons: a tap leads there")
            self.assertFalse(mention.kw["mention_author"])
            await self.bridge.question_asked(self.ch, "1-1", "ask", "again", question.id)
            self.assertEqual(len(self.mentions()), 1, "once for each question")
            self.assertTrue(await self.bridge.asks.typed(self.ch, "Red"))
            self.assertEqual(mention.content, "🔘 要你選：Which colour should the logo be? <@42> ✅", "answered: ✅, no new ring")
        asyncio.run(go())

    def test_a_question_cancelled_in_the_terminal_marks_its_mention(self):
        async def go():
            await self.bridge.handle(self.ev("Ask", ask_id="1-3", questions=[Q1]))
            mention = self.ch.sent[-1]
            await self.bridge.handle(self.ev("AskCancelled", ask_id="1-3"))
            self.assertTrue(mention.content.endswith(" ✅"), "C-152's cancel closes it too")
            self.assertEqual(len(self.mentions()), 1)
        asyncio.run(go())

    def test_several_questions_say_how_many(self):
        self.run_(self.bridge.handle(self.ev("Ask", ask_id="1-2", questions=[Q1, dict(Q1, question="Which size?")])))
        self.assertEqual(self.ch.sent[-1].content, "🔘 要你選（共 2 題）：Which colour should the logo be? <@42>")

    def test_a_plan_its_first_line(self):
        async def go():
            await self.bridge.handle(self.ev("Plan", ask_id="2-1", plan="# Use plan B\n\n1. move the files\n2. test"))
            self.assertEqual(self.ch.sent[-1].content, "📝 要你核准計畫：Use plan B <@42>")
            await self.bridge.asks.cancel(self.ch)
            self.assertTrue(self.ch.sent[-1].content.endswith(" ✅"), "closed by the turn's end: ✅ too")
        asyncio.run(go())

    def test_a_failure_that_needs_the_owner_and_its_end(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
            await self.bridge.handle(self.ev("StopFailure", error="billing_error", last_assistant_message="no credit"))
            failure, mention = self.ch.sent[-2:]
            self.assertTrue(failure.content.startswith("⚠️ **這一輪中斷了"))
            self.assertEqual(mention.content, "⚠️ 這一輪中斷了，要你處理：帳務問題 <@42>")
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="try again"))
            self.assertTrue(mention.content.endswith(" ✅"), "it moves again")
        asyncio.run(go())

    def test_a_reply_that_asks_to_discuss_and_the_owners_answer(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
            await self.bridge.handle(self.ev("Stop", last_assistant_message="💬 要跟你討論：v0.5.1 要不要等 C-152\n\n兩條路…"))
            mention = self.ch.sent[-1]
            self.assertEqual(mention.content, "💬 要跟你討論：v0.5.1 要不要等 C-152 <@42>")
            self.assertNotIn("<@", self.ch.sent[-2].content, "the reply itself rings as a reply, no mention")
            await self.bridge.call_done(self.ch, prefix="discuss:")
            self.assertTrue(mention.content.endswith(" ✅"))
        asyncio.run(go())

    def test_what_does_not_mention(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
            await self.bridge.handle(self.ev("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}))
            self.bridge.rt(self.ch).turn.t0 = time.time() - 3600  # however long it took
            await self.bridge.handle(self.ev("Stop", last_assistant_message="做完了。之後可以做的事：\n- 加測試\n- 💬 要跟你討論：不在第一行"))
            self.assertEqual(self.mentions(), [], [m.content for m in self.ch.sent])
        asyncio.run(go())

    def test_a_start_that_failed_once_until_one_opens(self):
        async def go():
            st = self.bridge.host.chan(self.ch)
            e = tmux_bridge.NotOpened(tmux_bridge.NotOpened.NOT_STARTED, "x")
            await self.bridge.start_failed(self.ch, st, e)
            await self.bridge.start_failed(self.ch, st, e)
            self.assertEqual([m.content for m in self.mentions()], ["⚠️ Claude 沒有啟動成功，訊息沒有送出 <@42>"])
            for why in (tmux_bridge.NotOpened.UNREACHABLE, tmux_bridge.NotOpened.PANE_GONE):
                st.pop("start_failed", None)
                await self.bridge.start_failed(self.ch, st, tmux_bridge.NotOpened(why, "x"))
            self.assertEqual(len(self.mentions()), 1, "a machine out of reach is said for the machine; an attached pane is the owner's")
        asyncio.run(go())

    def test_a_thing_mentioned_before_a_restart_is_not_mentioned_again(self):
        async def go():
            st = self.bridge.host.chan(self.ch)
            await self.bridge.call(self.ch, "ask:9-9", "ask", "x")
            bridge = tmux_bridge.TmuxBridge(self.bridge.host)  # (the same channels' state, a new bridge)
            await bridge.call(self.ch, "ask:9-9", "ask", "x")
            self.assertEqual(len(self.mentions()), 1)
            self.assertIn("ask:9-9", st["calls"])
        asyncio.run(go())

    def test_old_records_are_forgotten(self):
        st = {"calls": {"old": [1, time.time() - calls.KEEP_DAYS * 86400 - 1, True, "x"], "new": [2, time.time(), False, "y"]}}
        self.assertFalse(calls.mentioned(st, "old"))
        self.assertTrue(calls.mentioned(st, "new"))


class PlanOrder(Base):
    """#code-review on 3c7ad8f: a plan approved or sent back answers Discord's click first (it has 3 s), and only then
    edits the mention's line to its ✅."""

    async def clicked(self, what, answer, order):
        from tests.test_ask import Click
        ask_id = "3-%s" % what
        await self.bridge.asks.post_plan(self.ch, {"ask_id": ask_id, "plan": "do it"})
        click = Click(self.ch, ask.custom_id(ask_id, 0, what), user=OWNER)
        real, closed = click.response.edit_message, self.bridge.asks.on_closed

        async def edit_message(content=None, view=None):
            order.append("response")
            await real(content, view)

        async def on_closed(ch, aid):
            order.append("closed")
            await closed(ch, aid)
        click.response.edit_message, self.bridge.asks.on_closed = edit_message, on_closed
        try:
            if answer is None:
                await self.bridge.asks.on_interaction(click)
            else:
                await self.bridge.asks.record(click, ask_id, 0, answer)
        finally:
            self.bridge.asks.on_closed = closed

    def test_the_click_is_answered_before_the_mention_is_marked(self):
        for what, answer in (("y", None), ("o", "change the order")):
            order = []
            asyncio.run(self.clicked(what, answer, order))
            self.assertEqual(order, ["response", "closed"], what)


class Menus(Base):
    def test_a_permission_and_another_choice(self):
        from tests import fakepane
        from altention.tests.connectors.claude_code import samples
        view = samples.screen("permission")

        async def raw_screen(t):
            return view
        self.bridge.host.chan(self.ch)["tmux"] = "s"
        with fakepane.use(raw_screen=raw_screen, alive=mock.AsyncMock(return_value=True)), \
                mock.patch.object(asyncio, "sleep", mock.AsyncMock()):
            self.run_(self.bridge.menus.post(self.ch, self.bridge.host.chan(self.ch),
                                             {"message": "Claude needs your permission to use Bash", "notification_type": "permission_prompt"}))
            menu, mention = self.ch.sent[-2:]
            self.assertTrue(mention.content.startswith("🔐 要你允許："), mention.content)
            self.assertEqual(mention.kw["reference"].message_id, menu.id)
            self.run_(self.bridge.menus.post(self.ch, self.bridge.host.chan(self.ch), {"message": "Claude wants you to pick a model"}))
            self.assertEqual(self.ch.sent[-1].content, "🔘 要你選：Claude wants you to pick a model <@42>")
            self.run_(self.bridge.menus.resolve(self.ch))
        self.assertTrue(mention.content.endswith(" ✅"), "the session moved on: the menus are over")


class TheInstallation(Base):
    def other(self, cid, name):
        ch = Ch()
        ch.id, ch.name = cid, name
        return ch

    def test_a_usage_limit_once_for_every_channel_and_its_end_without_a_mention(self):
        from bot import blocked
        async def go():
            a, b = self.ch, self.other(6, "d")
            for ch in (a, b):
                st = self.bridge.host.chan(ch)
                blocked.put(st, "usage_limit", until=time.time() + 3600)
                await self.bridge.usage_limited(ch, "18:00")
            said = self.notify.sent
            self.assertEqual(len(said), 1, "once, whatever the number of channels")
            self.assertEqual(said[0].content.split("\n")[0], "⛔ 用量到上限，所有頻道暫停，約 18:00 恢復後自己接著做 <@42>")
            self.assertEqual(said[0].content.split("\n")[1], "受影響的頻道：<#5>、<#6>", "the second channel added to the same message")
            self.assertEqual(self.mentions(a) + self.mentions(b), [], "the channels show their own state, no mention")
            blocked.clear(self.bridge.host.chan(a))
            await self.bridge.usage_maybe_back()
            self.assertEqual(len(said), 1, "one channel still waits")
            blocked.clear(self.bridge.host.chan(b))
            await self.bridge.usage_maybe_back()
            self.assertEqual(said[-1].content, "✅ 用量恢復了，各頻道接著做")
            self.assertTrue(said[-1].silent)
            self.assertEqual(said[-1].kw["allowed_mentions"].users, False, "the line that says it is over mentions nobody")
            blocked.put(self.bridge.host.chan(a), "usage_limit")
            await self.bridge.usage_limited(a)
            self.assertEqual(said[-1].content.split("\n")[0], "⛔ 用量到上限，所有頻道暫停，恢復時間不確定 <@42>", "another time: again")
        asyncio.run(go())

    def test_the_reset_time_said_is_the_first_channels(self):
        """#code-review-2 on 3c7ad8f: a later channel's own time took the first one's place when it was listed."""
        from bot import blocked
        async def go():
            a, b = self.ch, self.other(6, "d")
            for ch, when in ((a, "15:00"), (b, "17:30")):
                blocked.put(self.bridge.host.chan(ch), "usage_limit")
                await self.bridge.usage_limited(ch, when)
            first = self.notify.sent[0].content.split("\n")
            self.assertEqual(first, ["⛔ 用量到上限，所有頻道暫停，約 15:00 恢復後自己接著做 <@42>", "受影響的頻道：<#5>、<#6>"])
        asyncio.run(go())

    def test_a_lost_login_once_for_its_machine(self):
        async def go():
            with mock.patch.object(tmux_bridge.remote, "inventory", lambda: {"machines": {"box": {}, "lap": {}}}), \
                    mock.patch.object(tmux_bridge.remote, "this_machine", lambda: "box"):
                for _ in range(2):
                    await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
                    await self.bridge.handle(self.ev("StopFailure", error="authentication_failed"))
                said = self.notify.sent
                self.assertEqual(len(said), 1)
                self.assertEqual(said[0].content.split("\n")[:2], ["🔑 box 的 Claude 要重新登入，那台的頻道都停住 <@42>",
                                                                   "在 box 的終端機打開 claude，輸入 /login 照畫面登入"])
                self.assertEqual(self.mentions(), [], "no mention in the channel: the machine's is the one")
                await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
                await self.bridge.handle(self.ev("Stop", last_assistant_message="ok"))
                self.assertEqual(said[-1].content, "✅ box 的登入恢復了")
            with mock.patch.object(tmux_bridge.remote, "inventory", lambda: {"machines": {"box": {}}}), \
                    mock.patch.object(tmux_bridge.remote, "this_machine", lambda: "box"):
                await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
                await self.bridge.handle(self.ev("StopFailure", error="authentication_failed"))
                self.assertEqual(said[-1].content.split("\n")[0], "🔑 Claude 要重新登入，頻道都停住 <@42>", "one machine: no name")
        asyncio.run(go())

    def down(self, setup):
        """`lap` goes down with this channel on it, as `setup` leaves the channel; its watch before and after
        MACHINE_DOWN_SECS. What the notification channel and #audit got."""
        async def go():
            st = self.bridge.host.chan(self.ch)
            st["home"] = {"host": "lap", "socket": "/s", "session": "w"}
            setup(st, self.bridge.rt(self.ch))
            t0 = time.time()
            self.bridge.went_down("lap")
            self.bridge.went_down("empty")  # (no channel on it)
            await self.bridge.machine_watch(t0 + tmux_bridge.MACHINE_DOWN_SECS - 5)
            self.assertEqual(self.notify.sent, [], "not before it has been down that long")
            await self.bridge.machine_watch(t0 + tmux_bridge.MACHINE_DOWN_SECS + 5)
            await self.bridge.machine_watch(t0 + tmux_bridge.MACHINE_DOWN_SECS + 120)
            said = [m.content for m in self.notify.sent]
            self.bridge.stream_down.discard("lap")
            await self.bridge.machine_back("lap")
            return said, [m.content for m in self.notify.sent][len(said):], list(self.bridge.host.audits)
        return asyncio.run(go())

    def test_a_machine_down_with_its_channels_idle_is_no_mention(self):
        """Manager 2026-11-03: the mac, turned off after use, is not said; one line in #audit."""
        said, back, audits = self.down(lambda st, rt: None)
        self.assertEqual((said, back), ([], []), "no mention, and no 'back' after none")
        self.assertEqual(audits, ["lap 連不上，那台的頻道都閒著，沒有 @ 使用者"], "once")

    def test_a_machine_down_in_the_middle_of_a_turn(self):
        def busy(st, rt):
            st["busy"] = True
        said, back, audits = self.down(busy)
        self.assertEqual([m.split("\n") for m in said], [["📡 lap 連不上，那台的 1 個頻道停住 <@42>", "受影響的頻道：<#5>"]], "once")
        self.assertEqual(back, ["✅ lap 連回來了"])

    def test_a_machine_down_across_a_restart_is_said_once(self):
        """#code-review-2 on d988a6b: the bot restarted while a machine was down noted a new time for it, and said it
        again; what was said is in state/calls-system.json, which a restart keeps."""
        async def go():
            st = self.bridge.host.chan(self.ch)
            st["home"] = {"host": "lap", "socket": "/s", "session": "w"}
            st["unsent"] = {"1": {"text": "hello"}}
            t0 = time.time()
            self.bridge.went_down("lap")
            await self.bridge.machine_watch(t0 + tmux_bridge.MACHINE_DOWN_SECS + 5)
            bridge = tmux_bridge.TmuxBridge(self.bridge.host)  # the restart: same channels, same state folder
            bridge.notify_channel = self.bridge.notify_channel
            with mock.patch.object(tmux_bridge.time, "time", return_value=t0 + 60):  # (a minute later: another time)
                bridge.went_down("lap")
            await bridge.machine_watch(t0 + 60 + tmux_bridge.MACHINE_DOWN_SECS + 5)
            self.assertEqual(len(self.notify.sent), 1, [m.content for m in self.notify.sent])
            bridge.stream_down.discard("lap")
            await bridge.machine_back("lap")
            self.assertEqual(self.notify.sent[-1].content, "✅ lap 連回來了", "and its end is said by the new one")
        asyncio.run(go())

    def test_a_machine_down_with_messages_waiting_for_it(self):
        def queued(st, rt):
            st["unsent"] = {"1": {"text": "hello"}}
        said, back, audits = self.down(queued)
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("📡 lap 連不上"))

    def test_a_usage_limit_claude_code_does_not_wait_out_is_one_too(self):
        """#code-review on 3c7ad8f: a rate_limit failure (blocked: api_error) was not counted as waiting, so another
        channel's next event said it was over, and the next channel at the same limit mentioned again."""
        async def go():
            other = self.other(7, "e")
            self.bridge.host.get_channel = lambda cid: {5: self.ch, 7: other}.get(int(cid))
            for ch in (self.ch, other):
                self.bridge.host.chan(ch)
            await self.bridge.failed(self.ch, {"dc_why": "rate_limit", "error": "rate_limit"}, None)
            self.assertEqual(self.bridge.host.chan(self.ch)["blocked"]["kind"], "api_error", "as before (C-99b)")
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go", channel="7"))  # another channel moves
            await self.bridge.failed(other, {"dc_why": "rate_limit", "error": "rate_limit"}, None)
            said = [m.content.split("\n")[0] for m in self.notify.sent]
            self.assertEqual(said, ["⛔ 用量到上限，所有頻道暫停，恢復時間不確定 <@42>"], "once, and never 'back' meanwhile")
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="again"))
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="again", channel="7"))
            self.assertEqual(self.notify.sent[-1].content, "✅ 用量恢復了，各頻道接著做", "both moved: it is over")
        asyncio.run(go())

    def test_nowhere_to_say_it_keeps_nothing(self):
        async def nowhere():
            return None
        self.bridge.notify_channel = nowhere
        self.run_(self.bridge.usage_limited(self.ch, "18:00"))
        self.assertIsNone(calls.open_episode("usage_limit"), "the next try says it")


class Words(unittest.TestCase):
    def test_every_line_in_the_table_of_text(self):
        from altention import text as alt_text
        for code in messages.CALLS:
            self.assertIn("dcbot.calls." + code, alt_text.TEXT[alt_text.DEFAULT], code)


if __name__ == "__main__":
    unittest.main()
