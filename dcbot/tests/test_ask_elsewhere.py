"""C-167: a question whose tool call never shows in the conversation's file (Claude Code's background memory pass:
a fork run with skipTranscript, asking with the conversation's session and file and no agent_id) gets no card; the
hook leaves it to the terminal and tells the bot (AskElsewhere, one #audit line). A question the conversation does ask
is never kept from Discord on this: its line is waited for (one reply with several tools writes it seconds late)."""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()

import asyncio
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401
from bot import ask
from altention.connectors.claude_code import hooks as cc_hooks  # (after bot: test_connector_rules)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q1 = {"question": "Which one?", "header": "pick", "multiSelect": False,
      "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}


def load_hook():
    loader = importlib.machinery.SourceFileLoader("dchook_elsewhere", os.path.join(HERE, "tools", "dchook"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def line(kind, **kw):
    return json.dumps(dict({"type": kind, "sessionId": "s"}, **kw)) + "\n"


def tool_use(tid, name):
    return line("assistant", message={"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": {}}]})


class Hook(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        self.root = tempfile.mkdtemp()
        self.file = os.path.join(self.root, "conv.jsonl")
        with open(self.file, "w") as f:
            f.write(line("user", message={"role": "user", "content": "do three things, then ask me"}))

    def events(self):
        d = os.path.join(self.root, "state", "events")
        out = []
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            with open(os.path.join(d, n)) as f:
                out.append(json.load(f))
        return out

    def payload(self, tid="toolu_ask"):
        return {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s", "tool_use_id": tid,
                "transcript_path": self.file, "prompt_id": "p1", "tool_input": {"questions": [Q1]}}

    def run_ask(self, ev, answer=None, wait=None):
        out = io.StringIO()
        waited = mock.Mock(return_value=answer if answer is not None else {"answers": {"Which one?": "B"}})
        with mock.patch.object(self.hook, "waited", waited), contextlib.redirect_stdout(out):
            if wait is not None:
                with mock.patch.object(self.hook, "ASK_LINE_SECS", wait):
                    self.hook.ask(self.root, "9", ev)
            else:
                self.hook.ask(self.root, "9", ev)
        return out.getvalue(), waited.called

    def test_three_tools_in_one_reply_the_line_late_still_a_card(self):
        """The reverse case (manager 11-11): one reply with three tools, its lines written 3.5 s after the hook was
        called (the whole reply streamed first). Waited for: the card is made, the answer goes to Claude."""
        def later():
            time.sleep(3.5)
            with open(self.file, "a") as f:
                for tid, name in (("toolu_1", "Bash"), ("toolu_2", "Read"), ("toolu_ask", "AskUserQuestion")):
                    f.write(tool_use(tid, name))
        t = threading.Thread(target=later)
        t0 = time.time()
        t.start()
        out, asked = self.run_ask(self.payload())
        t.join()
        took = time.time() - t0
        self.assertTrue(asked, "the answer was waited for")
        self.assertEqual([e["hook_event_name"] for e in self.events()], ["Ask"])
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertGreaterEqual(took, 3.4)
        self.assertLess(took, self.hook.ASK_LINE_SECS, "not waited out: on as soon as the line showed")

    def test_a_call_written_nowhere_gets_no_card(self):
        """The memory pass's question: its call never reaches the file. No card, no decision (the terminal takes it),
        the bot told."""
        out, asked = self.run_ask(self.payload("toolu_fork"), wait=0.5)
        self.assertEqual((out, asked), ("", False), "no decision printed, no answer waited for")
        (ev,) = self.events()
        self.assertEqual((ev["hook_event_name"], ev["channel"], ev["tool_use_id"], ev["dc_question"]),
                         ("AskElsewhere", "9", "toolu_fork", "Which one?"))
        self.assertEqual(cc_hooks.normalize(ev)["dc_kind"], cc_hooks.ASK_ELSEWHERE)

    def streamed_on(self, line_at):
        """The reviews' case: the question is not the reply's last block; what follows streams on, and the reply's
        lines (the question's among them) are written only `line_at` seconds after the hook was called. A clock of
        the test's own: no real waiting."""
        now = [0.0]

        def sleep(s):
            now[0] += s
            if now[0] >= line_at and not getattr(sleep, "done", False):
                sleep.done = True
                with open(self.file, "a") as f:
                    for tid, name in (("toolu_ask", "AskUserQuestion"), ("toolu_1", "Bash"), ("toolu_2", "Write")):
                        f.write(tool_use(tid, name))
        return self.hook.in_the_conversation(self.payload(), sleep=sleep, clock=lambda: now[0]), now[0]

    def test_more_streamed_after_the_question_within_the_limit_still_a_card(self):
        self.assertEqual(self.hook.ASK_LINE_SECS, 60)
        found, at = self.streamed_on(25.5)
        self.assertTrue(found, "25 s of streaming after the question: within the limit, the card is made")
        self.assertLess(at, 26, "on as soon as the line showed")

    def test_more_streamed_than_the_limit_left_to_the_terminal(self):
        found, at = self.streamed_on(61)
        self.assertFalse(found, "past the limit: left to the terminal (Claude Code's own menu, posted as buttons)")
        self.assertGreaterEqual(at, 60)

    def test_already_there_at_once(self):
        with open(self.file, "a") as f:
            f.write(tool_use("toolu_ask", "AskUserQuestion"))
        t0 = time.time()
        out, asked = self.run_ask(self.payload())
        self.assertTrue(asked)
        self.assertLess(time.time() - t0, 1)

    def test_nothing_to_tell_by_as_before(self):
        """No file named, no id, or a file that is not there: every question goes as it went before."""
        for drop in ("transcript_path", "tool_use_id"):
            ev = self.payload()
            ev.pop(drop)
            self.assertTrue(self.hook.in_the_conversation(ev, wait=0.2), drop)
        self.assertTrue(self.hook.in_the_conversation(dict(self.payload(), transcript_path=self.file + ".gone"), wait=0.2))

    def test_a_subagents_question_as_before(self):
        """A subagent's call is written in its own file, never the conversation's: it is not looked for there."""
        t0 = time.time()
        out, asked = self.run_ask(dict(self.payload("toolu_sub"), agent_id="a1", agent_type="general-purpose"))
        self.assertTrue(asked)
        self.assertEqual([e["hook_event_name"] for e in self.events()], ["Ask"])
        self.assertLess(time.time() - t0, 1, "not waited for")

    def test_an_id_cut_across_two_reads_is_found(self):
        with open(self.file, "a") as f:
            f.write('{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_')
        def later():
            time.sleep(0.5)
            with open(self.file, "a") as f:
                f.write('ask", "name": "AskUserQuestion"}]}}\n')
        t = threading.Thread(target=later)
        t.start()
        self.assertTrue(self.hook.in_the_conversation(self.payload(), wait=3))
        t.join()


class Bot(unittest.TestCase):
    def test_one_audit_line_nothing_else(self):
        said = []

        async def audit(text):
            said.append(text)
        asker = types.SimpleNamespace(audit=audit)
        asyncio.run(ask.Asker.elsewhere(asker, types.SimpleNamespace(name="secretary"), {"hook_event_name": "AskElsewhere"}))
        self.assertEqual(said, [ask.ELSEWHERE_AUDIT % "secretary"])
        self.assertNotIn("記憶", said[0], "neutral: a question of the conversation itself can come here too (written late)")


if __name__ == "__main__":
    unittest.main()
