"""Questions, plans and subagents by kind (stage 3b step 2.2c): handle() and the agents module no longer look at a
hook's name; a subagent's start has one shape wherever its files were read (the connector's agent(): looked for once,
the waiting is the caller's). What is posted and when is as it was (held against the tree of before with every
measured event, a made-up list of each branch, and the relay's seven cases of a subagent's files)."""
import asyncio
import contextlib
import io
import json
import time
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import hooks
from bot import agents, ask, config, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests import test_agents
from tests.test_agents import line
from tests.test_turn_end import Ch, Host
from tests.test_whole_turn import dcremote

NAMES = hooks.EVENTS + ("PostToolUse", "PreCompact", "PostCompact", "Ask", "Plan", "SomethingNew")


class World(unittest.TestCase):
    """A channel with the agents module and a folder for subagents' files: test_agents.Threads' own, borrowed (not
    inherited: its tests are its own)."""

    def setUp(self):
        self.w = test_agents.Threads("setUp")
        self.w.setUp()
        self.addCleanup(self.w.tearDown)
        self.ch, self.st, self.agents = self.w.ch, self.w.st, self.w.agents
        self.ev, self.handle, self.agent_files = self.w.ev, self.w.handle, self.w.agent_files

    def posted(self):
        return [m.content for m in self.ch.msgs.values()]


class WhatTheAgentsModuleTakes(World):
    def test_with_threads_off_every_event_of_a_subagent_is_swallowed_as_it_was(self):
        """The switch AGENT_THREADS=0: taken, and nothing shown. As the hook names had it: anything that bears a
        subagent's id, and a subagent's or a workflow's own three."""
        with mock.patch.object(config, "AGENT_THREADS", False):
            for name in NAMES:
                for extra in ({}, {"agent_id": "a1"}, {"agent_id": "a1", "agent_type": "Explore"}, {"tool_name": "Workflow", "started": {"runId": "r"}}):
                    ev = self.ev(name, **extra)
                    was = bool(ev.get("agent_id")) or name in ("SubagentStart", "SubagentStop", "PostToolUse")
                    self.assertEqual(self.handle(ev), was, (name, extra))
        self.assertEqual((self.posted(), self.st.get("subs"), self.st.get("rooms")), ([], None, None))

    def test_with_threads_on_it_takes_its_own_four_and_a_subagents_failure(self):
        for name in NAMES:
            for extra in ({}, {"agent_id": "zz"}, {"tool_name": "Workflow"}):
                ev = self.ev(name, **extra)
                was = name in ("SubagentStop", "PostToolUse") or (name in ("PreToolUse", "StopFailure") and bool(ev.get("agent_id")))
                self.assertEqual(self.handle(ev), was, (name, extra))  # (a start with no agent_type, an unknown agent's tool call or failed
                #                                                         request (step 2.2e, #issues 30): taken, nothing shown)
        self.assertEqual(self.posted(), [])

    def test_a_kind_made_out_elsewhere_is_gone_by(self):
        """The bridge has made the kind out before the module is asked; an event handed straight to it gets its kind
        here."""
        ev = self.ev("PreToolUse", agent_id="zz", dc_kind="agent_progress")
        self.assertTrue(self.handle(ev))
        self.assertFalse(self.handle(self.ev("PreToolUse", agent_id="zz", dc_kind="progress")), "the kind it bears, not the hook's name")


class OneShapeOfAStart(World):
    """A subagent's start as the bot shows it: from an event of this machine (its files looked for here, by the
    connector's agent() and its count of waits) and from the same event of another machine (the three fields put on
    it over there by tools/dcremote): the same thread, the same task."""

    def shown(self, world, ev):
        """(the status message, the thread's name, what waits to go into the thread, how often it waited)"""
        with mock.patch.object(asyncio, "sleep", mock.AsyncMock()) as slept:
            world.handle(ev)
        room = next(iter(world.st["rooms"].values()))
        return world.ch.msgs[room["msg"]].content, world.ch.threads[room["thread"]].name, list(world.agents.buf.values()), slept.await_count

    def test_here_and_from_another_machine(self):
        self.agent_files("a1", {"description": "Look around", "agentType": "Explore"}, line("user", "find the thing"))
        here = self.shown(self.w, self.ev("SubagentStart", agent_id="a1", agent_type="Explore"))
        other = test_agents.Threads("setUp")
        other.setUp()
        self.addCleanup(other.tearDown)
        there = self.shown(other, other.ev("SubagentStart", agent_id="a1", agent_type="Explore", host="lap",
                                           agent_meta={"description": "Look around", "agentType": "Explore"}, agent_run=None, agent_prompt="find the thing"))
        self.assertEqual(here[:3], there[:3], "the status, the thread, the task in it")
        self.assertIn("find the thing", str(here[2]))
        self.assertEqual((here[3], there[3]), (0, 0), "all there at the first look; another machine's is never waited for here")

    def test_the_fields_are_put_on_the_event_of_this_machine_too(self):
        self.agent_files("w1", {"description": "One of a run"}, line("user", "[Workflow harness — user request] x")
                         + line("user", "[Workflow harness — computed task] y. The computed task text follows:\nCount the files."), run="wf_7")
        ev = self.ev("SubagentStart", agent_id="w1", agent_type="workflow-subagent")
        self.handle(ev)
        self.assertEqual((ev["agent_meta"], ev["agent_run"], ev["agent_prompt"]), ({"description": "One of a run"}, "wf_7", "Count the files."))

    def test_files_that_never_come_are_waited_for_by_the_connectors_count_and_the_thread_opens_all_the_same(self):
        with mock.patch.object(asyncio, "sleep", mock.AsyncMock()) as slept:
            self.handle(self.ev("SubagentStart", agent_id="never", agent_type="Explore"))
        self.assertEqual([c.args[0] for c in slept.await_args_list], [hooks.AGENT_GAP] * hooks.AGENT_TRIES)
        self.assertIn("never", self.st["subs"])
        with mock.patch.object(asyncio, "sleep", mock.AsyncMock()) as slept:
            self.handle(self.ev("SubagentStart", agent_id="far", agent_type="Explore", host="lap"))
        self.assertEqual(slept.await_count, 0, "another machine's files are not looked for here, with or without the fields")
        self.assertIn("far", self.st["subs"])


class TheRelay(unittest.TestCase):
    def test_enrich_takes_no_agents_switch_and_the_tool_waits_by_the_connectors_count(self):
        with self.assertRaises(TypeError):
            hooks.enrich({"hook_event_name": "Stop"}, {}, agents=False)
        slept = []
        ev = {"hook_event_name": "SubagentStart", "agent_id": "never", "transcript_path": "/nowhere/s.jsonl"}
        with mock.patch.object(time, "sleep", slept.append):
            dcremote.enrich(ev, {})
            dcremote.enrich({"hook_event_name": "SubagentStart", "transcript_path": "/nowhere/s.jsonl"}, {})
            dcremote.enrich({"hook_event_name": "PreToolUse", "agent_id": "never", "transcript_path": "/nowhere/s.jsonl"}, {})
        self.assertEqual(slept, [hooks.AGENT_GAP] * hooks.AGENT_TRIES, "only a start that bears an id is waited for")
        self.assertNotIn("agent_meta", ev)


class AskedAndLetGo(unittest.TestCase):
    def play(self, events, state):
        async def go():
            ch = Ch()
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            bridge.last_reap = 1e12
            st = bridge.host.chan(ch)
            st.clear()
            st.update(state)
            did = []

            async def post(c, ev):
                did.append("question %s" % ev.get("ask_id"))
                return True

            async def post_plan(c, ev):
                did.append("plan %s" % ev.get("ask_id"))
                return True

            async def deliver(host, ask_id, answer):
                did.append("handed back %s %s" % (ask_id, sorted(answer)))
            with mock.patch.object(bridge, "watch", lambda *a, **k: None), mock.patch.object(bridge.asks, "post", post), \
                    mock.patch.object(bridge.asks, "post_plan", post_plan), mock.patch.object(ask, "deliver_to", deliver), \
                    mock.patch.object(ask, "load", lambda ask_id: None):
                for ev in events:
                    await bridge.handle(dict(ev, channel=str(ch.id)))
            return did, bridge.host.audits, ch.sent
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            return asyncio.run(go()) + (printed.getvalue(),)

    EVENTS = [{"hook_event_name": "Ask", "ask_id": "5-1", "session_id": "s1"}, {"hook_event_name": "Plan", "ask_id": "5-2", "session_id": "s1"},
              {"hook_event_name": "Ask", "ask_id": "5-3", "session_id": "s1", "agent_id": "a1"}]

    def test_a_question_and_a_plan_are_shown(self):
        did, audits, sent, printed = self.play(self.EVENTS, {"tmux": "dc-x-1", "cwd": "/w"})
        self.assertEqual((did, printed), (["question 5-1", "plan 5-2", "question 5-3"], ""), "a subagent's question is a question")

    def test_of_a_channel_put_away_they_are_handed_straight_back(self):
        away = {"archived": {"pane": {"socket": "/s", "id": "%1"}, "session_id": "s1"}, "cwd": "/w"}
        did, audits, sent, printed = self.play(self.EVENTS + [{"hook_event_name": "Ask", "session_id": "s1"}, {"hook_event_name": "Stop", "session_id": "s1"}], away)
        self.assertEqual(did, ["handed back 5-1 ['reason']", "handed back 5-2 []", "handed back 5-3 ['reason']"],
                         "a question: told why; a plan: left to the terminal's own dialog; one with no id, and a turn's end: let go")
        self.assertEqual((sent, len(audits), printed), ([], 1, ""), "nothing posted, #audit told once")


if __name__ == "__main__":
    unittest.main()
