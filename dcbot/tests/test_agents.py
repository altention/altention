"""Subagents and workflows as threads: what the hook passes on, and what ends up in which thread."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bot import agents, config, events
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeThread(object):
    def __init__(self, tid, name):
        self.id, self.name, self.sent, self.archived = tid, name, [], False

    async def send(self, text, silent=False):
        self.sent.append(text)

    async def edit(self, archived=None, name=None):
        self.archived = self.archived if archived is None else archived
        self.name = name or self.name


class FakeMessage(object):
    def __init__(self, ch, mid, content):
        self.ch, self.id, self.content = ch, mid, content

    async def create_thread(self, name=None, auto_archive_duration=None):
        t = FakeThread(self.id + 5000, name)
        self.ch.threads[t.id] = t
        return t

    async def edit(self, content=None):
        self.content = content


class FakeChannel(object):
    id = 9

    def __init__(self):
        self.msgs, self.threads = {}, {}

    async def send(self, text, silent=False):
        m = FakeMessage(self, 100 + len(self.msgs), text)
        self.msgs[m.id] = m
        return m

    def get_partial_message(self, mid):
        return self.msgs[mid]


class FakeHost(object):
    def __init__(self, ch):
        self.ch, self.channels_state, self.audits = ch, {"9": {}}, []

    async def audit(self, text):
        self.audits.append(text)

    def get_channel(self, cid):
        return self.ch if cid == self.ch.id else self.ch.threads.get(cid)

    def save(self):
        pass


def line(kind, content, **extra):
    return json.dumps(dict({"type": kind, "isSidechain": True, "message": {"role": kind, "content": content}}, **extra),
                      separators=(",", ":")) + "\n"


class Threads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transcript = os.path.join(self.tmp.name, "sess.jsonl")
        self.subdir = os.path.join(self.tmp.name, "sess", "subagents")
        os.makedirs(self.subdir)
        self.ch = FakeChannel()
        self.host = FakeHost(self.ch)
        self.st = self.host.channels_state["9"]
        self.agents = agents.Agents(self.host)

    def tearDown(self):
        self.tmp.cleanup()

    def agent_files(self, aid, meta, lines, run=None):
        folder = os.path.join(self.subdir, "workflows", run) if run else self.subdir
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "agent-%s.meta.json" % aid), "w") as f:
            json.dump(meta, f)
        with open(os.path.join(folder, "agent-%s.jsonl" % aid), "a") as f:
            f.write(lines)
        return os.path.join(folder, "agent-%s.jsonl" % aid)

    def ev(self, kind, **kw):
        return dict({"hook_event_name": kind, "transcript_path": self.transcript, "channel": "9"}, **kw)

    def handle(self, ev):
        return asyncio.run(self.agents.handle(self.ch, self.st, ev))

    def flush_all(self):
        self.agents.flushed.clear()
        asyncio.run(self.agents.tick())

    def test_one_subagent_gets_a_thread_and_ends_archived(self):
        path = self.agent_files("a1", {"agentType": "general-purpose", "description": "Count the files"},
                                line("user", "Count the files in src and report."))
        self.assertTrue(self.handle(self.ev("SubagentStart", agent_id="a1", agent_type="general-purpose")))
        (msg,) = self.ch.msgs.values()
        (thread,) = self.ch.threads.values()
        self.assertIn("Count the files", msg.content)
        self.assertIn("⏳", msg.content)
        self.assertEqual(thread.name, "🤖 Count the files")
        with open(path, "a") as f:
            f.write(line("assistant", [{"type": "text", "text": "Looking at src now."}]))
        self.assertTrue(self.handle(self.ev("PreToolUse", agent_id="a1", agent_type="general-purpose",
                                            tool_name="Bash", hint="ls src | wc -l")))
        self.flush_all()
        self.assertEqual(len(thread.sent), 1, "progress is gathered into one message")
        self.assertIn("**任務**\n> Count the files in src and report.", thread.sent[0])
        self.assertIn("> Looking at src now.", thread.sent[0])
        self.assertIn("`Bash` ls src | wc -l", thread.sent[0])
        self.assertTrue(self.handle(self.ev("SubagentStop", agent_id="a1", agent_type="general-purpose",
                                            last_assistant_message="There are 12 files.")))
        self.assertIn("**結果**", thread.sent[-1])
        self.assertIn("There are 12 files.", thread.sent[-1])
        self.assertIn("✅ 完成", msg.content)
        self.assertIn("Bash×1", msg.content)
        self.assertTrue(thread.archived)
        self.assertEqual((self.st["subs"], self.st["rooms"]), ({}, {}))

    def test_a_subagent_whose_stop_event_never_came_ends_with_the_main_turn(self):
        path = self.agent_files("a3", {"description": "Quiet one"}, line("user", "do it"))
        self.handle(self.ev("SubagentStart", agent_id="a3", agent_type="general-purpose"))
        self.handle(self.ev("PreToolUse", agent_id="a3", agent_type="general-purpose", tool_name="Bash", hint="ls"))
        self.handle(self.ev("Stop", background_tasks=[{"id": "a3", "type": "subagent", "status": "running"}]))
        self.assertIn("a3", self.st["subs"], "still on the background list: still running")
        with open(path, "a") as f:
            f.write(line("assistant", [{"type": "text", "text": "All 10 files counted."}]))
        self.handle(self.ev("Stop", background_tasks=[]))
        (msg,) = self.ch.msgs.values()
        (thread,) = self.ch.threads.values()
        self.assertIn("✅ 完成", msg.content)
        self.assertIn("All 10 files counted.", thread.sent[-1])
        self.assertTrue(thread.archived)
        self.assertEqual(self.st["subs"], {})

    def test_helper_agents_and_main_events_are_left_alone(self):
        self.assertTrue(self.handle(self.ev("SubagentStop", agent_id="zz", agent_type="", last_assistant_message="/x")))
        self.assertEqual(self.ch.msgs, {})
        self.assertFalse(self.handle(self.ev("PreToolUse", tool_name="Bash", hint="ls")))
        self.assertFalse(self.handle(self.ev("Stop", last_assistant_message="done", background_tasks=[])))
        self.assertTrue(self.handle(self.ev("PostToolUse", tool_name="Agent", started={})))

    def test_a_workflow_shares_one_thread(self):
        self.assertTrue(self.handle(self.ev("PostToolUse", tool_name="Workflow", hint="tiny",
                                            started={"runId": "wf_1", "workflowName": "tiny", "summary": "two agents",
                                                     "taskId": "t9"})))
        for aid, prompt in (("w1", "Reply RED"), ("w2", "Reply BLUE")):
            self.agent_files(aid, {"agentType": "workflow-subagent"}, line("user", prompt), run="wf_1")
            self.handle(self.ev("SubagentStart", agent_id=aid, agent_type="workflow-subagent"))
        (msg,) = self.ch.msgs.values()
        (thread,) = self.ch.threads.values()
        self.assertIn("tiny：two agents", msg.content)
        self.assertIn("0／2", msg.content)
        self.handle(self.ev("PreToolUse", agent_id="w1", agent_type="workflow-subagent", tool_name="Bash", hint="echo red"))
        self.handle(self.ev("SubagentStop", agent_id="w2", agent_type="workflow-subagent", last_assistant_message="BLUE"))
        self.assertIn("1／2", msg.content)
        self.assertFalse(self.handle(self.ev("Stop", background_tasks=[{"id": "t9", "type": "workflow"}])))
        self.assertIn("⏳", msg.content, "still running: its task is in the background list")
        self.handle(self.ev("SubagentStop", agent_id="w1", agent_type="workflow-subagent", last_assistant_message="RED"))
        self.handle(self.ev("Stop", background_tasks=[]))
        text = "\n".join(thread.sent)
        self.assertIn("▶️ **Reply RED**", text)
        self.assertIn("**Reply RED** `Bash` echo red", text)
        self.assertIn("✅ **Reply BLUE**", text)
        self.assertIn("✅ 完成 · 2／2", msg.content)
        self.assertTrue(thread.archived)
        self.assertEqual(self.st["rooms"], {})

    def started_with(self, write_after, description="Count the files"):
        """A SubagentStart handled while the subagent's files are there and its first row is not: the row is written
        after `write_after` waits of the bot (None: never). Returns (how often it waited, what the thread says)."""
        path = self.agent_files("a1", {"agentType": "general-purpose", "description": description}, "")
        waits = []

        async def sleep(secs):
            waits.append(secs)
            if write_after is not None and len(waits) == write_after:
                with open(path, "a") as f:
                    f.write(line("user", "Count the files in src and report."))
        with mock.patch.object(agents.asyncio, "sleep", sleep):
            self.assertTrue(self.handle(self.ev("SubagentStart", agent_id="a1", agent_type="general-purpose")))
        self.assertTrue(self.handle(self.ev("SubagentStop", agent_id="a1", agent_type="general-purpose", last_assistant_message="12 files.")))
        (thread,) = self.ch.threads.values()
        return waits, "\n".join(thread.sent)

    def test_the_task_is_waited_for_when_the_subagents_first_row_is_not_written_yet(self):
        """#issues 24: the files are there at SubagentStart, the first row comes a moment later (the hook is called
        before the row is written): the thread used to have no task then."""
        waits, said = self.started_with(write_after=2)
        self.assertEqual(waits, [0.25, 0.25])
        self.assertEqual(said.count("**任務**\n> Count the files in src and report."), 1, said)

    def test_a_first_row_that_never_comes_does_not_hold_the_thread_back_for_longer(self):
        waits, said = self.started_with(write_after=None)
        self.assertEqual(waits, [0.25] * 6, "1.5 s at most, as it was for the files")
        self.assertNotIn("**任務**", said)
        self.assertIn("**結果**", said)

    def test_a_workflow_agents_thread_shows_its_own_task(self):
        """#issues 28: under Claude Code 2.1.296 the first thing said to a workflow's agent is the harness relaying
        the owner's request; its task comes second. The thread showed the relay, the same for every agent."""
        relay = ("[Workflow harness — user request] The harness relays, verbatim and indented below, the user request that "
                 "triggered this workflow run. …\n  the owner's whole message")
        task = "[Workflow harness — computed task] The task text below was computed… The computed task text follows:\n  Reply with one line: %s"
        self.assertTrue(self.handle(self.ev("PostToolUse", tool_name="Workflow", started={"runId": "wf_1", "workflowName": "two", "taskId": "t"})))
        for aid, mark in (("w1", "WF-A"), ("w2", "WF-B")):
            self.agent_files(aid, {"agentType": "workflow-subagent", "description": mark.lower()}, line("user", relay) + line("user", task % mark), run="wf_1")
            self.assertTrue(self.handle(self.ev("SubagentStart", agent_id=aid, agent_type="workflow-subagent")))
        self.flush_all()
        (thread,) = self.ch.threads.values()
        said = "\n".join(thread.sent)
        self.assertIn("Reply with one line: WF-A", said)
        self.assertIn("Reply with one line: WF-B", said)
        self.assertNotIn("Workflow harness", said)
        self.assertNotIn("the owner's whole message", said)

    FORK = [{"type": "tool_result", "tool_use_id": "t", "content": "ok"},
            {"type": "text", "text": "<fork-boilerplate>you are a fork of the conversation</fork-boilerplate>"}]

    def test_a_forked_agent_is_not_waited_for(self):
        """Code review of 8b2800f: its first row leaves no task text; waiting for a text held the bot's round (every
        channel) for 1.5 s for each fork. Its row is there: no waiting, and the thread is as it was (no task)."""
        self.agent_files("f1", {"agentType": "fork", "description": "A fork"}, line("user", self.FORK))
        with mock.patch.object(agents.asyncio, "sleep", side_effect=AssertionError("waited")):
            self.assertTrue(self.handle(self.ev("SubagentStart", agent_id="f1", agent_type="fork")))
        self.assertTrue(self.handle(self.ev("SubagentStop", agent_id="f1", agent_type="fork", last_assistant_message="done")))
        (thread,) = self.ch.threads.values()
        self.assertNotIn("**任務**", "\n".join(thread.sent))

    def test_four_agents_started_at_once_hold_the_round_for_one_wait(self):
        """Their files appear within 0.08 s of their hooks (measured): one wait of 0.25 s for all four, as before
        this step; never 1.5 s each."""
        waits = []

        async def sleep(secs):
            waits.append(secs)
            if len(waits) == 1:
                for n in range(4):
                    self.agent_files("a%d" % n, {"description": "d%d" % n}, line("user", self.FORK if n % 2 else "task %d" % n))
        with mock.patch.object(agents.asyncio, "sleep", sleep):
            for n in range(4):
                self.assertTrue(self.handle(self.ev("SubagentStart", agent_id="a%d" % n, agent_type="general-purpose")))
        self.assertEqual(waits, [0.25])
        self.assertEqual(len(self.st["subs"]), 4)

    def test_a_workflow_whose_harness_is_worded_otherwise_holds_nothing_back(self):
        """A version of Claude Code that words the harness's rows anew: the task row is not known for one. An agent
        that has begun to answer is not waited for (it was, 1.5 s each, with the bot's round standing still); its
        task shows as its first row says."""
        relay = "[Workflow harness — user request] The harness relays … the user request:\n  the owner's message"
        other = "[Workflow harness — computed task] … Here is the task, worded anew:\n  Count the files."
        for aid in ("w1", "w2", "w3"):
            self.agent_files(aid, {"agentType": "workflow-subagent", "description": aid}, line("user", relay) + line("user", other)
                             + line("assistant", [{"type": "text", "text": "counting"}]), run="wf_1")
        with mock.patch.object(agents.asyncio, "sleep", side_effect=AssertionError("waited")):
            for aid in ("w1", "w2", "w3"):
                self.assertTrue(self.handle(self.ev("SubagentStart", agent_id=aid, agent_type="workflow-subagent")))
        self.assertEqual(len(self.st["subs"]), 3)

    def test_a_task_row_worded_otherwise_is_not_waited_for_when_the_agent_has_just_started(self):
        """Code review of 5a21a14: at the moment of its SubagentStart an agent has not answered yet. Its two rows are
        there: no waiting, whatever the second says."""
        relay = "[Workflow harness — user request] The harness relays … the user request:\n  the owner's message"
        other = "[Workflow harness — computed task] … Here is the task, worded anew:\n  Count the files."
        for aid in ("w1", "w2", "w3"):
            self.agent_files(aid, {"agentType": "workflow-subagent", "description": aid}, line("user", relay) + line("user", other), run="wf_1")
        with mock.patch.object(agents.asyncio, "sleep", side_effect=AssertionError("waited")):
            for aid in ("w1", "w2", "w3"):
                self.assertTrue(self.handle(self.ev("SubagentStart", agent_id=aid, agent_type="workflow-subagent")))
        self.assertEqual(len(self.st["subs"]), 3)

    def failure(self, aid, kind="general-purpose", **kw):
        return self.ev("StopFailure", agent_id=aid, agent_type=kind, error="unknown", last_assistant_message="API Error: 400 refused", **kw)

    def test_a_subagent_whose_request_failed_says_so_in_its_thread_and_ends(self):
        """#issues 30: Claude Code tells it as a StopFailure that bears the subagent's id; no SubagentStop follows."""
        self.agent_files("a1", {"description": "Count the files"}, line("user", "Count."))
        self.handle(self.ev("SubagentStart", agent_id="a1", agent_type="general-purpose"))
        (msg,) = self.ch.msgs.values()
        (thread,) = self.ch.threads.values()
        self.handle(self.ev("PreToolUse", agent_id="a1", agent_type="general-purpose", tool_name="Bash", hint="ls"))
        self.assertTrue(self.handle(self.failure("a1")), "taken: nothing of it is the conversation's own turn")
        self.assertIn("**失敗**", thread.sent[-1])
        self.assertIn("⚠️ API 錯誤\n> API Error: 400 refused", thread.sent[-1])
        self.assertNotIn("**結果**", "\n".join(thread.sent))
        self.assertIn("❌ 失敗（API 錯誤）", msg.content)
        self.assertIn("Bash×1", msg.content)
        self.assertNotIn("✅", msg.content)
        self.assertTrue(thread.archived)
        self.assertEqual((self.st["subs"], self.st["rooms"], len(self.ch.msgs)), ({}, {}, 1), "ended as at a stop; no new message in the channel")
        self.assertEqual(self.host.audits, ["#DM：子代理 Count the files → 失敗（API 錯誤）"])
        self.assertFalse(self.handle(self.ev("Stop", background_tasks=[])), "the conversation's own turn ends as ever")
        self.assertEqual(len(self.ch.msgs), 1)

    def test_the_words_go_by_the_error_code(self):
        self.agent_files("a1", {"description": "Count"}, line("user", "Count."))
        self.handle(self.ev("SubagentStart", agent_id="a1", agent_type="general-purpose"))
        self.handle(self.ev("StopFailure", agent_id="a1", agent_type="general-purpose", error="rate_limit"))
        (thread,) = self.ch.threads.values()
        self.assertTrue(thread.sent[-1].endswith("⚠️ 用量達到上限"), thread.sent[-1])
        self.assertIn("❌ 失敗（用量達到上限）", next(iter(self.ch.msgs.values())).content)

    def test_a_workflows_agent_that_failed_is_counted_as_failed(self):
        self.handle(self.ev("PostToolUse", tool_name="Workflow", hint="tiny", started={"runId": "wf_1", "workflowName": "tiny", "summary": "two", "taskId": "t9"}))
        for aid, prompt in (("w1", "Reply RED"), ("w2", "Reply BLUE")):
            self.agent_files(aid, {"agentType": "workflow-subagent"}, line("user", prompt), run="wf_1")
            self.handle(self.ev("SubagentStart", agent_id=aid, agent_type="workflow-subagent"))
        (msg,) = self.ch.msgs.values()
        (thread,) = self.ch.threads.values()
        self.assertTrue(self.handle(self.failure("w1", "workflow-subagent")))
        self.assertIn("0／2 個代理完成、1 個失敗", msg.content)
        self.assertIn("⏳", msg.content)
        self.handle(self.ev("SubagentStop", agent_id="w2", agent_type="workflow-subagent", last_assistant_message="BLUE"))
        self.assertIn("1／2 個代理完成、1 個失敗", msg.content)
        self.handle(self.ev("Stop", background_tasks=[]))
        text = "\n".join(thread.sent)
        self.assertIn("❌ **Reply RED**", text)
        self.assertIn("⚠️ API 錯誤\n> API Error: 400 refused", text)
        self.assertIn("✅ **Reply BLUE**", text)
        self.assertIn("✅ 完成 · 1／2 個代理完成、1 個失敗", msg.content, "the run is over: both ended")
        self.assertEqual(len(self.ch.msgs), 1)

    def test_a_failure_of_a_subagent_with_no_thread_goes_nowhere_but_the_audit(self):
        """Its thread is over already (the turn's end closed it), it never had one, or threads are off."""
        self.assertTrue(self.handle(self.failure("gone", "Explore")))
        self.assertEqual((self.ch.msgs, self.ch.threads, self.host.audits), ({}, {}, ["#DM：子代理 Explore → 失敗（API 錯誤）"]))
        with mock.patch.object(config, "AGENT_THREADS", False):
            self.assertTrue(self.handle(self.failure("gone", "Explore")), "as before: swallowed")
        self.assertEqual((self.ch.msgs, len(self.host.audits)), ({}, 1))

    def test_a_failure_that_bears_no_subagents_id_is_the_conversations_own(self):
        self.assertFalse(self.handle(self.ev("StopFailure", error="rate_limit")))
        self.assertEqual(self.host.audits, [])

    def test_a_subagent_of_another_machine_fails_the_same_way(self):
        self.handle(self.ev("SubagentStart", agent_id="r1", agent_type="Explore", host="lap", agent_meta={"description": "Far away"}, agent_run=None, agent_prompt="look"))
        (thread,) = self.ch.threads.values()
        self.assertTrue(self.handle(self.failure("r1", "Explore", host="lap")))
        self.assertIn("**失敗**", thread.sent[-1])
        self.assertTrue(thread.archived)

    def test_an_agent_first_seen_midway_is_adopted(self):
        self.agent_files("a2", {"description": "Late one"}, line("user", "do it"))
        self.assertTrue(self.handle(self.ev("PreToolUse", agent_id="a2", agent_type="Explore", tool_name="Read", hint="x.py")))
        self.assertIn("a2", self.st["subs"])
        self.assertEqual(len(self.ch.threads), 1)

    def test_status_texts_and_prune(self):
        sub = {"label": "L", "type": "Explore", "t0": 100, "tools": {"Read": 2, "Bash": 5}}
        self.assertEqual(agents.agent_status(sub, True, now=142), "🤖 **子代理**（Explore） · L\n✅ 完成 · 42 秒 · Bash×5, Read×2")
        self.st["subs"] = {"old": {"t0": 1}, "new": {"t0": 9e12}}
        self.agents.prune()
        self.assertEqual(list(self.st["subs"]), ["new"])


class HookPayload(unittest.TestCase):
    def fire(self, ev):
        with tempfile.TemporaryDirectory() as root:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook")], input=json.dumps(ev).encode(),
                               env=dict(os.environ, DCBOT_ROOT=root, DCBOT_CHANNEL="7"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
            (got,) = events.drain(os.path.join(root, "state", "events"))
            return got

    def test_workflow_and_agent_tool_events_are_trimmed(self):
        got = self.fire({"hook_event_name": "PostToolUse", "tool_name": "Workflow",
                         "tool_input": {"script": "export const meta = { name: 'tiny-test', description: 'd' }\n" + "x" * 9000},
                         "tool_response": {"runId": "wf_1", "workflowName": "tiny-test", "taskId": "t", "summary": "s",
                                           "scriptPath": "/long", "status": "async_launched", "extra": {"a": 1}}})
        self.assertEqual(got["hint"], "tiny-test")
        self.assertEqual(got["started"], {"runId": "wf_1", "workflowName": "tiny-test", "taskId": "t", "summary": "s",
                                          "status": "async_launched"})
        self.assertNotIn("tool_input", got)
        self.assertNotIn("tool_response", got)
        got = self.fire({"hook_event_name": "PreToolUse", "tool_name": "Bash", "agent_id": "a1", "agent_type": "Explore",
                         "tool_input": {"command": "ls", "description": "List"}})
        self.assertEqual((got["agent_id"], got["agent_type"], got["hint"]), ("a1", "Explore", "List"))
        got = self.fire({"hook_event_name": "PreToolUse", "tool_name": "SendMessage",
                         "tool_input": {"to": "b-26", "message": "Please look at the project", "summary": "s"}})
        self.assertEqual((got["send_to"], got["send_text"], got["hint"]), ("b-26", "Please look at the project", "→ b-26"))


if __name__ == "__main__":
    unittest.main()
