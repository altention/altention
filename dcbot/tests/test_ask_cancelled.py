"""C-152: a question (or a plan) cancelled in Claude Code's terminal. Esc or Ctrl-C there (also through !key) ends the
hook that waits for the answer with SIGTERM, and Claude Code says nothing else to any hook (2.1.317, measured
2026-11-03). Until now the bot kept the question open, and took the next message typed in the channel for its answer.
Now the hook tells the bot before it ends (an AskCancelled event), and the bot closes the question: whichever of a
click and the cancel comes first counts, the later one changes nothing."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from bot import ask
from altention.connectors.claude_code import hooks as cc_hooks  # noqa: E402  (bot puts the engine on the path)
from tests import test_ask, tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_ask import Click, FakeMessage, Q1, Q2, ROOT  # (not Ask: a TestCase imported here runs here too)


class Base(unittest.TestCase):
    """test_ask's setting up and helpers, without its tests (a subclass of Ask would run them all again)."""
    A = test_ask.Ask
    setUp, tearDown, answer_file, run_hook = A.setUp, A.tearDown, A.answer_file, A.run_hook


class Hook(Base):
    """dchook --ask and --plan, run as Claude Code runs them, ended by a signal while they wait."""

    def spool(self):
        return os.path.join(self.tmp.name, "state", "events")

    def events(self):
        d = self.spool()
        names = sorted(os.listdir(d)) if os.path.isdir(d) else []
        self.assertEqual([n for n in names if n.startswith(".")], [], "never half an event: written aside, then renamed")
        out = []
        for n in names:
            with open(os.path.join(d, n)) as f:
                out.append(json.load(f))
        return out

    def ended_by(self, flag, payload, sig):
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "dchook"), flag],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, DCBOT_ROOT=self.tmp.name, DCBOT_CHANNEL="9"))
        try:
            proc.stdin.write(json.dumps(payload).encode())
            proc.stdin.close()
            for _ in range(200):
                if self.events():
                    break
                time.sleep(0.05)
            time.sleep(0.2)  # waiting for the answer now
            t0 = time.time()
            proc.send_signal(sig)
            proc.wait(10)
            took = time.time() - t0
            out, err = proc.stdout.read(), proc.stderr.read()
        finally:
            proc.kill()
            proc.stdout.close()
            proc.stderr.close()
        return proc.returncode, took, out, err

    def test_a_question_cancelled_tells_the_bot_and_ends_at_once(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s",
                   "tool_input": {"questions": [Q1]}}
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            self.tearDown()
            self.setUp()
            code, took, out, err = self.ended_by("--ask", payload, sig)
            self.assertEqual((code, out, err), (0, b"", b""), sig)
            self.assertLess(took, 2, "it ends itself, it is not waited on to be killed")
            asked, cancelled = self.events()
            self.assertEqual(asked["hook_event_name"], "Ask")
            self.assertEqual((cancelled["hook_event_name"], cancelled["ask_id"], cancelled["channel"], cancelled["session_id"]),
                             ("AskCancelled", asked["ask_id"], "9", "s"), sig)
            self.assertEqual(cancelled["dc_signal"], int(sig))

    def test_a_plan_cancelled_too(self):
        payload = {"hook_event_name": "PermissionRequest", "tool_name": "ExitPlanMode", "session_id": "s",
                   "tool_input": {"plan": "the plan"}}
        code, took, out, err = self.ended_by("--plan", payload, signal.SIGTERM)
        self.assertEqual((code, out, err), (0, b"", b""))
        planned, cancelled = self.events()
        self.assertEqual((planned["hook_event_name"], cancelled["hook_event_name"]), ("Plan", "AskCancelled"))
        self.assertEqual(cancelled["ask_id"], planned["ask_id"])

    def test_an_answer_is_given_as_before(self):
        out = self.run_hook({"answers": {"Which one?": "B"}})
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual([e["hook_event_name"] for e in self.events()], ["Ask"], "no AskCancelled after an answer")


class Bot(Base):
    """What the bot does with the AskCancelled event (Asker.cancelled)."""

    def setUp(self):
        Base.setUp(self)
        self.done = []

        async def on_done(ch, ask_id, how):
            self.done.append((ask_id, how))
        self.asker.on_done = on_done

    def gone(self, ask_id):
        return {"hook_event_name": "AskCancelled", "ask_id": ask_id, "channel": "9", "session_id": "s"}

    def test_the_question_closes_and_the_next_message_goes_to_claude(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "1-1", "questions": [Q1]})
            await self.asker.cancelled(self.ch, self.gone("1-1"))
            self.assertEqual(self.ch.sent[0].content, "⌛ **Pick**　Which one?\n→ （在終端機取消了，沒有回答）")
            self.assertIsNone(self.ch.sent[0].view)
            self.assertEqual(ask.waiting(), [])
            self.assertIsNone(self.answer_file("1-1"), "nothing handed: the hook is no more")
            self.assertEqual(self.done, [("1-1", "已取消")], "the sending channel's ⏸️ line")
            self.assertEqual(self.audits, ["#c 的提問在終端機取消了（Esc、Ctrl-C 或 !key），按鈕已關掉"])
            self.assertFalse(await self.asker.typed(self.ch, "this goes to Claude"))
            late = Click(self.ch, ask.custom_id("1-1", 0, "0"))
            await self.asker.on_interaction(late)
            self.assertIn("失效", late.response.edited)
        asyncio.run(go())

    def test_a_plan_cancelled(self):
        async def go():
            await self.asker.post_plan(self.ch, {"ask_id": "2-1", "plan": "do it"})
            await self.asker.cancelled(self.ch, self.gone("2-1"))
            self.assertEqual(self.ch.sent[-1].content, "⌛ **計畫**　Claude 寫好計畫了，要照這個執行嗎？\n→ （在終端機取消了，沒有回答）")
            self.assertEqual(ask.waiting(), [])
            approve = Click(self.ch, ask.custom_id("2-1", 0, "y"))
            await self.asker.on_interaction(approve)
            self.assertIn("失效", approve.response.edited)
            self.assertIsNone(self.answer_file("2-1"))
        asyncio.run(go())

    def test_of_several_questions_the_answered_ones_stay_as_they_are(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "3-1", "questions": [Q1, Q2]})
            await self.asker.on_interaction(Click(self.ch, ask.custom_id("3-1", 0, "1")))
            self.ch.sent[0].content = "answered"  # (the click's response edits it in Discord)
            await self.asker.cancelled(self.ch, self.gone("3-1"))
            self.assertEqual(self.ch.sent[0].content, "answered")
            self.assertIn("在終端機取消了", self.ch.sent[1].content)
            self.assertEqual(ask.waiting(), [])
        asyncio.run(go())

    def test_whichever_comes_first_counts_a_click_before_the_cancel(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "4-1", "questions": [Q1]})
            click = Click(self.ch, ask.custom_id("4-1", 0, "1"))
            await self.asker.on_interaction(click)
            before = self.ch.sent[0].content
            del self.audits[:]
            await self.asker.cancelled(self.ch, self.gone("4-1"))
            self.assertEqual(self.ch.sent[0].content, before, "nothing changed")
            self.assertEqual(self.answer_file("4-1"), {"answers": {"Which one?": "B"}}, "the answer stands")
            self.assertEqual(self.audits, ["#c 的提問在終端機取消了，但它已經回答或關掉，沒有改任何東西"])
            self.assertEqual(self.done, [("4-1", "已回答")])
        asyncio.run(go())

    def test_closed_owed_another_channels_or_not_known_change_nothing(self):
        async def go():
            await self.asker.cancelled(self.ch, self.gone("no-such"))
            await self.asker.cancelled(self.ch, {"hook_event_name": "AskCancelled", "channel": "9"})
            await self.asker.post(self.ch, {"ask_id": "5-1", "questions": [Q1]})
            await self.asker.cancel(self.ch)  # the turn ended first
            await self.asker.cancelled(self.ch, self.gone("5-1"))
            self.assertIn("已取消，沒有回答", self.ch.sent[0].content, "as the turn's end left it")
            ask.save("5-2", {"channel": "9", "questions": [Q1], "answers": {}, "messages": {}, "owed": {"reason": "x"},
                             "host": "lap"})
            await self.asker.cancelled(self.ch, self.gone("5-2"))
            self.assertIsNotNone(ask.read("5-2"), "closed already, its result on the way to its machine: left as it is")
            ask.save("5-3", {"channel": "77", "questions": [Q1], "answers": {}, "messages": {}})
            await self.asker.cancelled(self.ch, self.gone("5-3"))
            self.assertIsNotNone(ask.read("5-3"), "another channel's: not this event's to close")
            self.assertEqual(self.audits[-4:], ["#c 的提問在終端機取消了，但它已經回答或關掉，沒有改任何東西"] * 4)
            self.assertEqual(self.audits[:2], ["#c 的提問在終端機取消了，但它已經回答或關掉，沒有改任何東西"] * 2)
        asyncio.run(go())

    def test_whichever_comes_first_counts_a_cancel_while_a_typed_answer_is_on_its_way(self):
        """The message typed is edited into the question first, then the answer settled: a cancel that comes in
        between wins, and the message goes to Claude (typed() says False), nothing is handed to the gone hook."""
        async def go():
            await self.asker.post(self.ch, {"ask_id": "6-1", "questions": [Q1]})
            msg, real_edit = self.ch.sent[0], FakeMessage.edit

            async def edit(content=None, view=None):
                await real_edit(msg, content, view)
                if content and "→ typed" in content:  # the typed answer's edit: the cancel comes meanwhile
                    await self.asker.cancelled(self.ch, self.gone("6-1"))
            with mock.patch.object(msg, "edit", edit):
                self.assertFalse(await self.asker.typed(self.ch, "typed"))
            self.assertIn("在終端機取消了", msg.content)
            self.assertIsNone(self.answer_file("6-1"))
            self.assertEqual(ask.waiting(), [])
            self.assertEqual(self.done, [("6-1", "已取消")], "not 已回答 as well")
        asyncio.run(go())

    def test_whichever_comes_first_counts_a_cancel_while_a_click_is_answered(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1]})
            click = Click(self.ch, ask.custom_id("7-1", 0, "0"))
            real = click.response.edit_message

            async def edit_message(content=None, view=None):
                await real(content, view)
                await self.asker.cancelled(self.ch, self.gone("7-1"))  # Discord's answer to the click takes a moment
            click.response.edit_message = edit_message
            await self.asker.on_interaction(click)
            self.assertIsNone(self.answer_file("7-1"), "the cancel came first: the click's answer goes nowhere")
            self.assertEqual(self.done, [("7-1", "已取消")])
        asyncio.run(go())


class Road(unittest.TestCase):
    """The event as the bridge gets it (from the spool, or from another machine's stream): handle() closes the question."""

    def test_handle_closes_the_question_a_question_event_opened(self):
        from bot import config, tmux_bridge
        from tests.test_ask import FakeChannel
        from tests.test_channel_gone import World
        from tests.test_turn_end import Host
        World.setUp(self)
        ch = FakeChannel()

        async def go():
            host = Host(ch)
            host.get_channel = lambda cid: ch if int(cid) == ch.id else None
            bridge = tmux_bridge.TmuxBridge(host)
            bridge.last_reap = 1e12
            await bridge.handle({"hook_event_name": "Ask", "ask_id": "1-1", "questions": [Q1], "session_id": "s1", "channel": "9"})
            self.assertEqual([a for a, _ in ask.waiting(9)], ["1-1"])
            await bridge.handle({"hook_event_name": "AskCancelled", "ask_id": "1-1", "session_id": "s1", "channel": "9"})
            return host
        asyncio.run(go())
        self.assertEqual(ask.waiting(), [])
        self.assertIn("在終端機取消了", ch.sent[-1].content)
        self.assertEqual(config.STATE, self.state)


class Kind(unittest.TestCase):
    def test_its_kind(self):
        ev = cc_hooks.normalize({"hook_event_name": "AskCancelled", "ask_id": "1-1", "channel": "9"})
        self.assertEqual(ev["dc_kind"], cc_hooks.ASK_CANCELLED)


if __name__ == "__main__":
    unittest.main()
