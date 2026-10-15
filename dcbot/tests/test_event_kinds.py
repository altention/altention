"""The kinds the connector gives events (hooks.normalize, stage 3b step 2.1) say what the bot's own recognisers say
today: handle() is not changed in this step, so each of them is held against the kind that will replace it in 2.2.
And every event that reaches handle() bears a kind, by either road."""
import asyncio
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import hooks
from bot import events, peers, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_whole_turn import dcremote

PROMPTS = ["整理一下 notes", "", "   ", "/model opus", "[see the file] and tell me", "[Cross-session idle notice] st-1 is idle",
           "<task-notification>\n<task-id>t</task-id>\n<summary>done</summary>\n</task-notification>", "<system-reminder>x</system-reminder>",
           '<cross-session-message from-name="st-1">done with it</cross-session-message>', "<cross-session-message>hi</cross-session-message>",
           '  <cross-session-message from-name="a b" other="1">\nline one\nline two\n</cross-session-message>\n',
           "<cross-session-message>hi</cross-session-message> and more", "a <cross-session-message>x</cross-session-message>", "<", "[x"]


class AsTheBotSaysToday(unittest.TestCase):
    def test_who_an_input_is_from(self):
        for prompt in PROMPTS:
            ev = hooks.normalize({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
            got = peers.incoming(prompt)
            self.assertEqual(ev["dc_from"] == hooks.PEER, got is not None, prompt)
            if got:
                self.assertEqual((ev["dc_peer"] or "另一個對話", ev["dc_text"]), got, prompt)
            self.assertEqual(ev["dc_from"] == hooks.TYPED and bool(prompt.strip()), events.from_terminal(prompt, ()), prompt)

    def test_idle_notices_and_failures(self):
        for ev in ({"notification_type": "idle_prompt"}, {"message": "Claude is waiting for your input"}, {"message": "Claude needs your permission"},
                   {"notification_type": "permission_prompt", "message": "x"}, {}, {"message": None}):
            kind = hooks.normalize(dict(ev, hook_event_name="Notification"))["dc_kind"]
            self.assertEqual(kind == hooks.IDLE, events.is_idle_notice(ev), ev)
            self.assertIn(kind, (hooks.IDLE, hooks.DIALOG))
        for code in list(tmux_bridge.FAILURES) + ["something_new", None]:
            why = hooks.normalize({"hook_event_name": "StopFailure", "error": code})["dc_why"]
            self.assertEqual(tmux_bridge.FAILURES.get(why, "API 錯誤（%s）" % why),
                             tmux_bridge.FAILURES.get(code or "unknown", "API 錯誤（%s）" % code), code)

    def test_a_message_sent_to_another_conversation(self):
        for ev in ({"tool_name": "SendMessage", "send_to": "st-1", "send_text": "done"}, {"tool_name": "SendMessage", "send_to": "st-1", "send_text": " \n"},
                   {"tool_name": "SendMessage"}, {"tool_name": "Bash", "send_text": "x"}):
            shown_today = ev.get("tool_name") == "SendMessage" and bool((ev.get("send_text") or "").strip())  # (and the name known: the bot's)
            self.assertEqual(hooks.normalize(dict(ev, hook_event_name="PreToolUse"))["dc_kind"] == hooks.PEER_SENT, shown_today, ev)

    def test_what_the_agents_module_takes_is_an_agent_kind(self):
        """As bot/agents.py takes them when subagents have threads: the result of the Agent, Task or Workflow tool, a
        subagent's start and stop, and a tool call that bears a subagent's id. Any other event goes on to the
        bridge's own branches, with or without such an id (a question and a plan are asked before either)."""
        def takes(ev):
            name = ev.get("hook_event_name")
            return name in ("SubagentStart", "SubagentStop", "PostToolUse") or (name == "PreToolUse" and bool(ev.get("agent_id")))
        agent_kinds = (hooks.AGENT_START, hooks.AGENT_STOP, hooks.AGENT_PROGRESS, hooks.AGENT_LAUNCHED, hooks.WORKFLOW_START)
        for name in hooks.EVENTS + ("PostToolUse", "PreCompact", "PostCompact"):
            for extra in ({}, {"agent_id": "a1"}, {"agent_id": "a1", "agent_type": "Explore"}, {"tool_name": "Workflow"}):
                ev = dict(extra, hook_event_name=name)
                self.assertEqual(hooks.normalize(dict(ev))["dc_kind"] in agent_kinds, takes(ev), ev)
        for name in ("Ask", "Plan"):
            self.assertEqual(hooks.normalize({"hook_event_name": name, "agent_id": "a1"})["dc_kind"], name.lower())


class OnEveryRoad(unittest.TestCase):
    def test_the_relay_on_another_machine_says_the_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            spool = os.path.join(tmp, "events")
            os.makedirs(spool)
            for i, ev in enumerate([{"channel": "1", "hook_event_name": "SessionStart", "source": "startup"},
                                    {"channel": "1", "hook_event_name": "UserPromptSubmit", "prompt": "hi"},
                                    {"channel": "1", "hook_event_name": "SessionEnd", "reason": "other"}]):
                with open(os.path.join(spool, "%02d.json" % i), "w") as f:
                    json.dump(ev, f)
            out = io.StringIO()
            self.assertEqual(dcremote.relay(spool, {}, out), 3)
            sent = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual([(ev["dc_kind"], ev.get("dc_why") or ev.get("dc_from")) for ev in sent],
                             [("ready", None), ("input", "typed"), ("session_ended", "closed")])
            self.assertLessEqual(set(hooks.NORMALIZED), set(dcremote.ADDED), "counted among what the relay adds")

    def test_an_event_of_a_machine_a_step_behind_gets_its_kind_from_the_bot(self):
        """Its dcremote sends no kind. The bot's stream makes it out before handle()."""
        handled = []
        lines = [b'{"heartbeat": 1}\n', b'{"channel": "5", "hook_event_name": "Stop"}\n',
                 b'{"channel": "5", "hook_event_name": "Stop", "dc_kind": "said_over_there"}\n', b""]

        class Line(object):
            def __init__(self):
                self.stdout = self

            async def readline(self):
                return lines.pop(0) if lines else b""

            def kill(self):
                pass

        async def spawn(*a, **kw):
            return Line()

        async def handle(ev):
            handled.append((ev["host"], ev["dc_kind"]))
            if len(handled) == 2:
                raise asyncio.CancelledError()

        from bot import remote
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        bridge.stream_down, bridge.streams, bridge.handle = set(), {}, handle
        bridge.host = mock.Mock()
        with mock.patch.object(remote, "reach", mock.AsyncMock(return_value="LAP")), mock.patch.object(remote, "stream_command", return_value="x"), \
                mock.patch.object(remote, "argv", lambda host, cmd, alias: ["x"]), mock.patch.object(asyncio, "create_subprocess_exec", spawn):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(bridge.stream("lap"))
        self.assertEqual(handled, [("lap", "turn_end"), ("lap", "said_over_there")])


if __name__ == "__main__":
    unittest.main()
