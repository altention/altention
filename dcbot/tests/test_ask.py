"""Claude's questions as Discord buttons: what is shown, what a click means, and the hand-off to the hook."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import discord

from bot import ask, config
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q1 = {"question": "Which one?", "header": "Pick", "multiSelect": False,
      "options": [{"label": "A", "description": "first"}, {"label": "B", "description": "second"}]}
Q2 = {"question": "Which ones?", "header": "Many", "multiSelect": True,
      "options": [{"label": "X"}, {"label": "Y"}, {"label": "Z"}]}


class FakeMessage(object):
    def __init__(self, mid, content, view):
        self.id, self.content, self.view = mid, content, view

    async def edit(self, content=None, view=None):
        self.content, self.view = content, view


class FakeChannel(object):
    id, name = 9, "c"

    def __init__(self):
        self.sent = []

    async def send(self, content, view=None):
        self.sent.append(FakeMessage(100 + len(self.sent), content, view))
        return self.sent[-1]

    def get_partial_message(self, mid):
        return next(m for m in self.sent if m.id == mid)


class FakeResponse(object):
    def __init__(self):
        self.edited, self.said, self.modal = None, None, None

    async def edit_message(self, content=None, view=None):
        self.edited = content

    async def send_message(self, text, ephemeral=False):
        self.said = text

    async def send_modal(self, modal):
        self.modal = modal


class Click(object):
    type = discord.InteractionType.component

    def __init__(self, channel, cid, values=None, user=111):
        self.channel, self.data = channel, {"custom_id": cid, "values": values or []}
        self.user = type("U", (), {"id": user})()
        self.response = FakeResponse()


class Ask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID)
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID = 111
        self.audits = []

        async def audit(text):
            self.audits.append(text)
        self.asker, self.ch = ask.Asker(audit), FakeChannel()

    def tearDown(self):
        ask.ASKS, ask.ANSWERS, config.OWNER_ID = self.saved
        self.tmp.cleanup()

    def answer_file(self, ask_id):
        try:
            with open(os.path.join(ask.ANSWERS, ask_id + ".json")) as f:
                return json.load(f)
        except OSError:
            return None

    def test_text_and_ids(self):
        text = ask.question_text(Q2, 1, 2)
        self.assertIn("**Many**（2/2）", text)
        self.assertIn("（可複選）", text)
        self.assertIn("- **X**", text)
        self.assertIn("- **A**：first", ask.question_text(Q1, 0, 1))
        self.assertEqual(ask.parse_id(ask.custom_id("12-3", 1, "s")), ("12-3", 1, "s"))
        self.assertIsNone(ask.parse_id("other:1:2:3"))
        self.assertIsNone(ask.parse_id(None))
        self.assertEqual(ask.chosen(Q1, "1"), "B")
        self.assertEqual(ask.chosen(Q2, "s", ["0", "2"]), "X, Z")
        self.assertIsNone(ask.chosen(Q1, "7"))

    def test_views_fit_discord(self):
        kinds = lambda q: [type(i).__name__ for i in ask.view_for("1", 0, q).children]  # noqa: E731
        self.assertEqual(kinds(Q1), ["Button", "Button", "Button"])  # A, B, 其他…
        self.assertEqual(kinds(Q2), ["Select", "Button"])
        select = ask.view_for("1", 0, Q2).children[0]
        self.assertEqual((select.min_values, select.max_values, len(select.options)), (1, 3, 3))
        long = dict(Q1, options=[{"label": "L" * 200, "description": "D" * 300}] * 2)
        self.assertLessEqual(len(ask.view_for("1", 0, long).children[0].label), 80)

    def test_two_questions_answered_then_delivered(self):
        async def go():
            self.assertTrue(await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1, Q2]}))
            self.assertEqual(len(self.ch.sent), 2)
            self.assertEqual([n for n, _ in ask.waiting(9)], ["7-1"])
            stranger = Click(self.ch, ask.custom_id("7-1", 0, "1"), user=5)
            await self.asker.on_interaction(stranger)
            self.assertIn("擁有者", stranger.response.said)
            first = Click(self.ch, ask.custom_id("7-1", 0, "1"))
            self.assertTrue(await self.asker.on_interaction(first))
            self.assertIn("→ B", first.response.edited)
            self.assertIsNone(self.answer_file("7-1"), "not delivered until every question is answered")
            other = Click(self.ch, ask.custom_id("7-1", 1, "o"))
            await self.asker.on_interaction(other)
            self.assertIsInstance(other.response.modal, ask.OtherModal)
            await self.asker.record(other, "7-1", 1, "something else")
            self.assertEqual(self.answer_file("7-1"), {"answers": {"Which one?": "B", "Which ones?": "something else"}})
            self.assertEqual(ask.waiting(), [])
            late = Click(self.ch, ask.custom_id("7-1", 0, "0"))
            await self.asker.on_interaction(late)
            self.assertIn("失效", late.response.edited)
            self.assertEqual(len(self.audits), 1)
            self.assertFalse(await self.asker.on_interaction(Click(self.ch, "something:else")))
        asyncio.run(go())

    def test_typing_answers_the_one_open_question(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "9-1", "questions": [Q1, Q2]})
            self.assertFalse(await self.asker.typed(self.ch, "B"), "two questions open: no guessing")
            await self.asker.on_interaction(Click(self.ch, ask.custom_id("9-1", 0, "0")))
            self.assertTrue(await self.asker.typed(self.ch, "  my own  "))
            self.assertIn("→ my own", self.ch.sent[1].content)
            self.assertIsNone(self.ch.sent[1].view)
            self.assertEqual(self.answer_file("9-1"), {"answers": {"Which one?": "A", "Which ones?": "my own"}})
            self.assertFalse(await self.asker.typed(self.ch, "nothing open now"))
            await self.asker.post_plan(self.ch, {"ask_id": "9-2", "plan": "do it"})
            self.assertFalse(await self.asker.typed(self.ch, "ok"), "a plan is answered with its buttons")
        asyncio.run(go())

    def test_cancel_closes_open_questions_and_tells_the_hook(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "8-1", "questions": [Q1]})
            await self.asker.cancel(self.ch)
            self.assertIn("已取消", self.ch.sent[0].content)
            self.assertIn("reason", self.answer_file("8-1"))
            self.assertEqual(ask.waiting(), [])
            self.assertFalse(await self.asker.post(self.ch, {"ask_id": "8-2", "questions": []}))
            self.assertIn("reason", self.answer_file("8-2"))
        asyncio.run(go())

    def test_plan_is_approved_or_sent_back(self):
        async def go():
            self.assertTrue(await self.asker.post_plan(self.ch, {"ask_id": "9-1", "plan": "1. do it\n2. done"}))
            self.assertIn("1. do it", self.ch.sent[0].content)
            buttons = [(b.label, b.custom_id) for b in self.ch.sent[-1].view.children]
            self.assertEqual(buttons, [("核准，開始執行", "ask:9-1:0:y"), ("要修改…", "ask:9-1:0:o")])
            yes = Click(self.ch, "ask:9-1:0:y")
            await self.asker.on_interaction(yes)
            self.assertIn("已核准", yes.response.edited)
            self.assertEqual(self.answer_file("9-1"), {"approve": True})
            await self.asker.post_plan(self.ch, {"ask_id": "9-2", "plan": "p"})
            change = Click(self.ch, "ask:9-2:0:o")
            await self.asker.on_interaction(change)
            self.assertIsInstance(change.response.modal, ask.OtherModal)
            await self.asker.record(change, "9-2", 0, "use goodbye.txt")
            self.assertIn("use goodbye.txt", self.answer_file("9-2")["reason"])
            self.assertIn("退回修改", change.response.edited)
            await self.asker.post_plan(self.ch, {"ask_id": "9-3", "plan": "p"})
            await self.asker.cancel(self.ch)
            self.assertIn("reason", self.answer_file("9-3"))
            self.assertEqual(ask.waiting(), [])
        asyncio.run(go())

    def run_plan_hook(self, result):
        root = self.tmp.name
        ask.ANSWERS = os.path.join(root, "state", "answers")
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "dchook"), "--plan"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, DCBOT_ROOT=root, DCBOT_CHANNEL="9"))

        def answer():
            spool = os.path.join(root, "state", "events")
            for _ in range(100):
                names = os.listdir(spool) if os.path.isdir(spool) else []
                if names:
                    with open(os.path.join(spool, names[0])) as f:
                        ev = json.load(f)
                    os.remove(os.path.join(spool, names[0]))  # the next hook's event must be the only one
                    self.assertEqual((ev["hook_event_name"], ev["plan"]), ("Plan", "the plan"))
                    return ask.deliver(ev["ask_id"], result)
                time.sleep(0.05)
        t = threading.Thread(target=answer)
        t.start()
        try:
            out, err = proc.communicate(json.dumps({"hook_event_name": "PermissionRequest", "tool_name": "ExitPlanMode",
                                                    "tool_input": {"plan": "the plan", "planFilePath": "/p"}}).encode(), 20)
        finally:
            proc.kill()  # a hook left waiting would wait for hours
        t.join()
        self.assertEqual((proc.returncode, err), (0, b""))
        return json.loads(out.decode())["hookSpecificOutput"]

    def test_plan_hook_decisions(self):
        out = self.run_plan_hook({"approve": True})
        self.assertEqual(out, {"hookEventName": "PermissionRequest", "decision": {
            "behavior": "allow", "updatedInput": {"plan": "the plan", "planFilePath": "/p"}}})
        out = self.run_plan_hook({"reason": "change it"})
        self.assertEqual(out["decision"], {"behavior": "deny", "message": "change it"})

    def spooled(self, flag, payload):
        """The event `dchook --ask` or `--plan` makes of what Claude Code hands it; the hook is then let go."""
        root = self.tmp.name
        ask.ANSWERS = os.path.join(root, "state", "answers")
        spool = os.path.join(root, "state", "events")
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "dchook"), flag],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, DCBOT_ROOT=root, DCBOT_CHANNEL="9"))
        try:
            proc.stdin.write(json.dumps(payload).encode())
            proc.stdin.close()
            ev = None
            for _ in range(200):
                names = os.listdir(spool) if os.path.isdir(spool) else []
                if names:
                    with open(os.path.join(spool, names[0])) as f:
                        ev = json.load(f)
                    os.remove(os.path.join(spool, names[0]))
                    ask.deliver(ev["ask_id"], {"reason": "not now"})
                    break
                time.sleep(0.05)
            proc.wait(20)
        finally:
            proc.kill()
            proc.stdout.close()
            proc.stderr.close()
        return ev

    def test_a_question_and_a_plan_say_where_the_conversation_is_and_which_turn_and_call(self):
        """Claude Code's own three fields, under its names and as they came (stage 3b step 1.5): what Claude said
        before asking is read with them, where the transcript is. Nothing else of the hook's input goes on."""
        theirs = {"transcript_path": "/p/s 1.jsonl", "prompt_id": "c3d0…", "tool_use_id": "toolu_01"}
        for flag, kind, inp in (("--ask", "Ask", {"questions": [Q1]}), ("--plan", "Plan", {"plan": "the plan"})):
            payload = dict(theirs, hook_event_name="PreToolUse", session_id="s", cwd="/somewhere", permission_mode="plan",
                           tool_name="X", tool_input=inp)
            ev = self.spooled(flag, payload)
            self.assertEqual(ev["hook_event_name"], kind)
            self.assertEqual({k: ev.get(k) for k in theirs}, theirs, kind)
            self.assertEqual(sorted(set(ev) & {"cwd", "permission_mode", "tool_name", "tool_input"}), [], kind)
            bare = self.spooled(flag, {"hook_event_name": "PreToolUse", "session_id": "s", "tool_input": inp, "prompt_id": None})
            self.assertEqual(sorted(set(bare) & set(theirs)), [], "what Claude Code did not send is not made up")

    def run_hook(self, result):
        """Run `dchook --ask` like Claude Code would, and answer it the way the bot does."""
        root = self.tmp.name
        ask.ANSWERS = os.path.join(root, "state", "answers")
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "dchook"), "--ask"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, DCBOT_ROOT=root, DCBOT_CHANNEL="9"))

        def answer():
            spool = os.path.join(root, "state", "events")
            for _ in range(100):
                names = os.listdir(spool) if os.path.isdir(spool) else []
                if names:
                    with open(os.path.join(spool, names[0])) as f:
                        ev = json.load(f)
                    self.assertEqual((ev["hook_event_name"], ev["channel"], ev["questions"]), ("Ask", "9", [Q1]))
                    return ask.deliver(ev["ask_id"], result)
                time.sleep(0.05)
        t = threading.Thread(target=answer)
        t.start()
        try:
            out, err = proc.communicate(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                                                    "session_id": "s", "tool_input": {"questions": [Q1]}}).encode(), 20)
        finally:
            proc.kill()
        t.join()
        self.assertEqual((proc.returncode, err), (0, b""))
        return json.loads(out.decode())["hookSpecificOutput"]

    def test_hook_returns_the_answers_as_tool_input(self):
        out = self.run_hook({"answers": {"Which one?": "B"}})
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(out["updatedInput"], {"questions": [Q1], "answers": {"Which one?": "B"}})

    def test_hook_denies_with_the_reason_when_cancelled(self):
        out = self.run_hook({"reason": "stopped"})
        self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"]), ("deny", "stopped"))


if __name__ == "__main__":
    unittest.main()
