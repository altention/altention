"""Claude Code's hooks as the connector knows them (hooks.py): the one set of hooks that report, and what is added
to an event where its transcript can be read. The first two tests came from the bot's test_agents and
test_turn_end as they were (stage 3b step 1.5); those of enrich hold its rules one by one (turns by prompt_id, reading up
to an event's own tool call: step 1.6a); the recorded runs of the real program are in test_turns."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from altention.connectors.claude_code import hooks


def line(kind, content, **extra):
    return json.dumps(dict({"type": kind, "message": {"role": kind, "content": content}}, **extra),
                      ensure_ascii=False, separators=(",", ":")) + "\n"


def said(text):
    return line("assistant", [{"type": "text", "text": text}])


class HookSet(unittest.TestCase):
    def test_bot_sessions_and_global_install_use_the_same_hook_set(self):
        spec = hooks.hooks("'x'")
        self.assertEqual(sorted(spec), sorted(hooks.EVENTS + ("PostToolUse", "PermissionRequest")))
        self.assertEqual(spec["PermissionRequest"][0]["matcher"], "ExitPlanMode")
        self.assertTrue(spec["PermissionRequest"][0]["hooks"][0]["command"].endswith("--plan"))
        self.assertEqual([g.get("matcher") for g in spec["PreToolUse"]], [None, "AskUserQuestion", None])
        self.assertEqual(spec["PreToolUse"][2]["hooks"][0]["command"].split()[-1], "--guard", "the secrets guard, for every tool (K-03)")
        self.assertEqual(spec["PreToolUse"][2]["hooks"][0]["timeout"], hooks.GUARD_SECS)
        self.assertEqual(spec["PreToolUse"][1]["hooks"][0], {"type": "command", "command": "'x' --ask",
                                                             "timeout": hooks.ASK_SECS})
        self.assertNotIn("timeout", spec["Stop"][0]["hooks"][0])

    def test_stop_failure_is_hooked(self):
        self.assertIn("StopFailure", hooks.EVENTS)
        self.assertIn("StopFailure", hooks.hooks("x"))


class Enrich(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.path = os.path.join(self.tmp, "s1.jsonl")
        self.write("")

    def write(self, text, path=None):
        with open(path or self.path, "a", encoding="utf-8") as f:
            f.write(text)

    def ev(self, kind, **kw):
        return dict({"hook_event_name": kind, "transcript_path": self.path}, **kw)

    def through(self, pos, kind, **kw):
        """An event as the one who reads it where its transcript is leaves it: enrich(), and for a subagent's start
        its details, waited for as tools/dcremote waits (agent_waits(), asleep). `agents` False: not looked for."""
        opts = dict((k, kw.pop(k)) for k in ("texts",) if k in kw)
        agents = kw.pop("agents", True)
        ev = self.ev(kind, **kw)
        hooks.enrich(ev, pos, **opts)
        if agents and kind == "SubagentStart" and ev.get("agent_id"):
            for gap in hooks.agent_waits(ev):
                time.sleep(gap)
        return ev

    def asked(self, pid, text="go"):
        return line("user", text, promptId=pid)

    def call(self, tid):
        return line("assistant", [{"type": "tool_use", "id": tid, "name": "Bash", "input": {}}])

    def result(self, pid, tid):
        return line("user", [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}], promptId=pid)

    def size(self):
        return os.path.getsize(self.path)

    def test_a_turn_from_its_own_row_and_each_call_up_to_its_own_row(self):
        pos = {}
        self.through(pos, "SessionStart")
        self.write(said("the last reply of a turn before, written late"))
        ev = self.through(pos, "UserPromptSubmit", prompt_id="p1")  # the hook first: the owner's row is not written yet
        self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (0, False), "for now: where the reading stood")
        start = self.size()
        self.write(self.asked("p1") + said("one") + self.call("t1") + self.result("p1", "t1") + said("two") + self.call("t2")
                   + said("the reply, written before the first call was handled"))
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t1")
        self.assertEqual((ev["texts"], ev["dc_turn_start"], ev["dc_turn_sure"]), (["one"], start, True),
                         "from the turn's own row: what lay before it was the last turn's")
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t2")
        self.assertEqual(ev["texts"], ["two"], "however late: not past its own call (#issues 19)")
        ev = self.through(pos, "Stop", prompt_id="p1")
        self.assertEqual((ev["texts"], ev["context"]), (["the reply, written before the first call was handled"], None))
        self.assertEqual(pos[self.path]["at"], self.size())

    def test_several_calls_in_one_reply(self):
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("before all three") + self.call("t1") + self.call("t2") + self.call("t3"))
        got = [self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id=t)["texts"] for t in ("t1", "t2", "t3")]
        self.assertEqual(got, [["before all three"], [], []])

    def test_a_call_whose_row_is_not_written_yet(self):
        """Step 1.6a does not wait for it (1.6b will): what is in the file lies before the call, and is brought;
        the words written with the call come with the next event that reads, once."""
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1"))
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t1")["texts"], [])
        self.write(said("said with the first call") + self.call("t1") + self.result("p1", "t1"))
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t2")["texts"], ["said with the first call"])
        self.write(self.call("t2"))
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], [])

    def test_a_message_sent_meanwhile_is_the_same_turn(self):
        """It comes with the running turn's prompt_id (measured), here and on another machine alike (#issues 8)."""
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("said before the second message"))
        ev = self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.assertNotIn("texts", ev)
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["said before the second message"])

    def test_a_turn_that_ended_with_no_event_leaves_nothing_for_the_next(self):
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("said, then Escape") + self.call("t1"))
        self.through(pos, "UserPromptSubmit", prompt_id="p2")
        self.write(self.result("p1", "t1") + self.asked("p1", "[Request interrupted by user for tool use]") + self.asked("p2") + said("of the new turn"))
        ev = self.through(pos, "Stop", prompt_id="p2")
        self.assertEqual((ev["texts"], ev["dc_turn_sure"]), (["of the new turn"], True))

    def test_a_file_with_no_record_is_read_from_its_end_and_the_turn_found_by_its_id(self):
        """The first event after a restart, a conversation attached midway. What the turn said so far is not read
        again (it was relayed before the restart, or was never to be). Where the turn lies is found by its id: its
        own first row. (In the first version of this step the beginning was the end of the file too: past the
        turn's last row under its own id, so that 📋 took the NEXT turn's id for its own and showed that turn:
        #code-review-2's rehearsal of 120c38a+e34f0bc, a restart in mid-turn.)"""
        earlier = self.asked("p0", "a turn before") + said("its reply")
        self.write(earlier + self.asked("p1") + said("relayed before the restart") + self.call("t1") + self.result("p1", "t1"))
        pos = {}
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t2")
        self.assertEqual((ev["texts"], ev["dc_turn_start"], ev["dc_turn_sure"]), ([], len(earlier.encode()), True))
        self.write(said("after it") + self.call("t2"))
        ev = self.through(pos, "Stop", prompt_id="p1")
        self.assertEqual((ev["texts"], ev["dc_turn_start"]), (["after it"], len(earlier.encode())))

    def reply(self, mid, *blocks):
        """The rows of one reply, as Claude Code writes them: one block a row, each with the reply's id."""
        out = ""
        for b in blocks:
            block = {"type": "text", "text": b} if not b.startswith("t") else {"type": "tool_use", "id": b, "name": "Bash", "input": {}}
            out += json.dumps({"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [block]}}) + "\n"
        return out

    def test_step_2_2g_a_file_first_met_with_a_call_whose_row_is_written_already(self):
        """A restart in mid-turn: the first event the new reader sees is a tool call's, and Claude Code wrote the
        call's row (with what was said ahead of it) in the 0.1 s before the event was looked at. Until this step
        that row lay before the end of the file and so counted as read: the words were never posted (#code-review-2:
        7 runs of 20, each time the event was handled 0.18 s or more after its hook)."""
        earlier = self.asked("p0", "a turn before") + said("its reply")
        before = earlier + self.asked("p1") + self.reply("m1", "relayed before the restart", "t1") + self.result("p1", "t1")
        self.write(before + self.reply("m2", "said ahead of the second call", "t2"))
        pos = {}
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t2")
        self.assertEqual(ev["texts"], ["said ahead of the second call"], "its own reply's words, and nothing from before it")
        self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (len(earlier.encode()), True))
        self.assertEqual(pos[self.path]["at"], self.size(), "read up to the call's row")
        self.assertIn("t1", pos[self.path]["called"])
        self.write(self.result("p1", "t2") + said("the reply"))
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["the reply"], "and not a second time at the end")

    def test_step_2_2g_not_for_a_call_that_is_not_its_replys_first(self):
        """The manager's case: one reply, two calls. The first call's event was handled before the restart (the
        words ahead of it were posted then); the first event after it is the second call's. Told by the file alone:
        the reply makes another call before this one, so an event came before this one and the words went with it.
        Rather none than twice."""
        self.write(self.asked("p1") + self.reply("m1", "posted before the restart", "t1", "t2"))
        pos = {}
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t2")
        self.assertEqual(ev["texts"], [])
        self.write(self.result("p1", "t1") + self.result("p1", "t2") + said("the reply"))
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["the reply"])
        both = json.dumps({"type": "assistant", "message": {"id": "m3", "content": [
            {"type": "text", "text": "one row, two calls"}, {"type": "tool_use", "id": "t3", "name": "Bash", "input": {}},
            {"type": "tool_use", "id": "t4", "name": "Bash", "input": {}}]}}) + "\n"
        for call, texts in (("t4", []), ("t3", ["one row, two calls"])):
            self.setUp()
            self.write(self.asked("p1") + both)
            self.assertEqual(self.through({}, "PreToolUse", prompt_id="p1", tool_use_id=call)["texts"], texts, call)

    def test_step_2_2g_the_first_call_of_a_reply_of_several_brings_the_words_once(self):
        self.write(self.asked("p1") + self.reply("m1", "ahead of both", "t1", "t2"))
        pos = {}
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t1")["texts"], ["ahead of both"])
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t2")["texts"], [])

    def test_step_2_2g_what_is_not_read_back(self):
        """Only that reply: not an earlier reply of the turn, not another turn's rows, not a side chain's, and
        nothing at all for an event that is no tool call's (the end of a turn first met: from the end, as ever)."""
        from altention.connectors.claude_code import transcript
        side = json.dumps({"type": "assistant", "isSidechain": True, "message": {"id": "m9", "content": [{"type": "text", "text": "a subagent's"}]}}) + "\n"
        self.write(self.asked("p1") + self.reply("m1", "an earlier reply's words") + side + self.reply("m2", "t5"))
        pos = {}
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t5")["texts"], [], "a call with nothing said ahead")
        self.setUp()
        self.write(self.asked("p1") + self.reply("m1", "said", "t1"))
        self.assertEqual(self.through({}, "Stop", prompt_id="p1")["texts"], [], "an end: from the end of the file")
        self.assertEqual(self.through({}, "PostToolUse", prompt_id="p1", tool_use_id="t1").get("texts"), None)
        self.assertIsNone(transcript.reply_of_first_call(self.path, self.size(), "t-none"))
        self.assertIsNone(transcript.reply_of_first_call(os.path.join(self.tmp, "missing.jsonl"), 10, "t1"))
        whole = self.size()
        self.assertEqual(transcript.reply_of_first_call(self.path, whole, "t1"), len(self.asked("p1").encode()))
        self.assertIsNone(transcript.reply_of_first_call(self.path, whole - 5, "t1"), "its row lies past the place asked about")
        self.assertIsNone(transcript.reply_of_first_call(self.path, whole, "t1", tail_bytes=40), "nor in the part looked through")

    def test_step_2_2g_a_call_in_a_file_not_met_is_waited_for_too(self):
        """So that the row is there when the place is put: within the limit, while the row is not written."""
        self.write(self.asked("p1"))
        ev = self.ev("PreToolUse", prompt_id="p1", tool_use_id="t1", ts=100.0)
        self.assertTrue(hooks.awaits(ev, {}, now=100.1))
        self.assertFalse(hooks.awaits(ev, {}, now=100.0 + hooks.HOLD_SECS), "not past the limit")
        self.assertFalse(hooks.awaits(ev, {}, now=99.0), "nor an event from the future")
        self.assertFalse(hooks.awaits(ev, {}, now=100.1, calls=False), "nor when the words on the way are not posted")
        self.write(self.reply("m1", "said ahead", "t1"))
        self.assertFalse(hooks.awaits(ev, {}, now=100.1), "written: handled")
        pos = {}
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t1")["texts"], ["said ahead"])
        self.assertFalse(hooks.awaits(dict(ev, transcript_path=os.path.join(self.tmp, "missing.jsonl")), {}, now=100.0 + hooks.HOLD_SECS))

    def test_a_turn_met_midway_whose_first_row_is_not_found_has_no_known_beginning(self):
        """Not in the part of the file that is looked through, or under no message row at all: None, and the bot
        records nothing for that turn, rather than a place that is another turn's."""
        from altention.connectors.claude_code import transcript
        self.write(self.asked("p1") + said("x" * 3000) + self.call("t1") + self.result("p1", "t1"))
        self.assertEqual(transcript.find_begin(self.path, "p1"), 0)
        self.assertIsNone(transcript.find_begin(self.path, "p1", tail_bytes=500), "its first row lies before the part looked through")
        self.assertIsNone(transcript.find_begin(self.path, "p-unknown"))
        self.assertIsNone(transcript.find_begin(self.path, None))
        self.assertIsNone(transcript.find_begin(os.path.join(self.tmp, "missing.jsonl"), "p1"))
        self.write(self.asked("p2", "the next") + said("y"))
        self.assertEqual(transcript.find_begin(self.path, "p2", tail_bytes=200), self.size() - len((self.asked("p2", "the next") + said("y")).encode()),
                         "a piece that begins in the middle of a row: from the next whole row on")
        with mock.patch.object(transcript, "find_begin", return_value=None):
            pos = {}
            ev = self.through(pos, "Stop", prompt_id="p1")
        self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"], ev["texts"]), (None, False, []))
        later = self.through(pos, "Notification", prompt_id="p1")
        self.assertIsNone(later["dc_turn_start"], "it stays unknown for that turn")
        self.write(self.result("p1", "t1") + said("more of it"))
        ev = self.through(pos, "Stop", prompt_id="p1")
        self.assertEqual((ev["dc_turn_start"], ev["texts"]), (None, ["more of it"]), "to its end: a tool's result is not its beginning either")
        start = self.size()
        self.through(pos, "UserPromptSubmit", prompt_id="p3")
        self.write(self.asked("p3") + said("of the turn after"))
        ev = self.through(pos, "Stop", prompt_id="p3")
        self.assertEqual((ev["dc_turn_start"], ev["texts"]), (start, ["of the turn after"]), "the next turn is known as ever")

    def test_a_file_first_met_with_the_first_event_of_a_new_turn(self):
        """After a restart the first event of a channel is mostly the prompt of a new turn, and the hook comes before
        its row: not in the file yet. That is no turn met in its middle: its beginning is mended when the row is
        read, as for any new turn (the third commit of this step fixed it as unknown for good, and the turn's reply
        had no 📋: code review of 1f14111)."""
        self.write(self.asked("p0", "a turn before") + said("its reply"))
        pos = {}
        ev = self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (None, False), "not known yet: nobody may take the end of the file for it")
        start = self.size()
        self.write(self.asked("p1") + said("on the way") + self.call("t1"))
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t1")
        self.assertEqual((ev["texts"], ev["dc_turn_start"], ev["dc_turn_sure"]), (["on the way"], start, True))
        ev = self.through(pos, "Stop", prompt_id="p1")
        self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (start, True))
        pos = {}  # the same with the row already written when the event is handled: found at once
        ev = self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (start, True))

    def test_a_file_first_met_with_no_ids_begins_at_its_end_as_before(self):
        self.write(line("user", "x") + said("before"))
        ev = self.through({}, "Stop")
        self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"], ev["texts"]), (self.size(), True, []))

    def test_an_id_met_before_does_not_begin_a_turn_again(self):
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("of the first"))
        self.through(pos, "Stop", prompt_id="p1")
        self.through(pos, "UserPromptSubmit", prompt_id="p2")
        self.write(self.asked("p2") + said("of the second"))
        late = self.through(pos, "Notification", prompt_id="p1")  # an event of the turn before, come late
        self.assertEqual(pos[self.path]["id"], "p2")
        self.assertNotIn("texts", late)
        self.assertEqual(self.through(pos, "Stop", prompt_id="p2")["texts"], ["of the second"])

    def test_the_end_of_a_turn_does_not_read_the_next_one(self):
        """Events piled up: the next turn (a queued message, sent in with no UserPromptSubmit) is already written."""
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("of the first") + said("the first's reply") + self.asked("p2") + said("of the second"))
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["of the first", "the first's reply"])
        ev = self.through(pos, "Stop", prompt_id="p2")
        self.assertEqual((ev["texts"], ev["dc_turn_sure"]), (["of the second"], True), "a new id with no UserPromptSubmit: a turn")
        self.assertEqual(ev["dc_turn_start"], self.size() - len((self.asked("p2") + said("of the second")).encode()))

    def test_a_turn_whose_row_never_shows_its_id_comes_out_at_its_end(self):
        """The way out (the architecture review's last note): no row said to Claude carries this turn's id (a kind
        of turn not measured, a version that does not write it). Nothing is brought while that might still be the
        last turn's; at the turn's end all of it is, and the bot's own filter drops the last turn's reply."""
        pos = {}
        self.through(pos, "SessionStart")
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(line("user", "a row with no promptId") + said("on the way") + self.call("t1"))
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t1")["texts"], [])
        ev = self.through(pos, "Stop", prompt_id="p1")
        self.assertEqual((ev["texts"], ev["dc_turn_start"], ev["dc_turn_sure"]), (["on the way"], 0, False))
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], [], "once")

    def test_events_that_carry_no_prompt_id(self):
        """A Claude Code that sends none: every UserPromptSubmit moves the place to the end, as tools/dcremote always
        did. Once an id was met for the file, an event without one (SessionStart) changes nothing."""
        pos = {}
        self.through(pos, "UserPromptSubmit")
        self.write(line("user", "x") + said("one"))
        self.assertEqual(self.through(pos, "PreToolUse")["texts"], ["one"], "no tool_use_id either: to the end of the file")
        self.write(said("left of a turn that ended with no event"))
        self.through(pos, "UserPromptSubmit")
        self.assertEqual(self.through(pos, "Stop")["texts"], [])
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("kept"))
        self.through(pos, "SessionStart", source="compact")
        self.through(pos, "UserPromptSubmit")
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["kept"])

    def test_a_call_whose_id_is_not_in_the_file_reads_to_the_end_of_the_turn(self):
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("one") + self.call("another-id") + self.asked("p2") + said("of the next turn"))
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t-missing")["texts"], ["one"])

    def test_no_transcript_named_nothing_added(self):
        bare = {"hook_event_name": "Stop", "prompt_id": "p1"}
        hooks.enrich(bare, {})
        self.assertEqual(bare, {"hook_event_name": "Stop", "prompt_id": "p1"})

    def test_a_subagents_own_events_read_nothing_of_the_main_conversation(self):
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("the main one"))
        ev = self.through(pos, "PreToolUse", agent_id="a1", prompt_id="p9", tool_use_id="t9")
        self.assertEqual([k for k in ev if k.startswith("dc_") or k == "texts"], [])
        self.assertEqual(pos[self.path]["id"], "p1", "the id it carries is the main conversation's of that moment: no turn of its own")
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["the main one"])

    def test_with_the_words_not_wanted_the_reading_goes_on_all_the_same(self):
        """texts=False (the bot with its words-on-the-way turned off): nothing is brought, but the place moves on
        and each turn's beginning is found. It did not in the first version of this step: the place stayed where
        the file was first met, and every turn after began there (code review of 120c38a)."""
        pos = {}
        self.through(pos, "SessionStart")
        starts = []
        for pid in ("p1", "p2", "p3"):
            self.through(pos, "UserPromptSubmit", prompt_id=pid, texts=False)
            starts.append(self.size())
            self.write(self.asked(pid) + said("on the way in " + pid) + self.call("t-" + pid) + self.result(pid, "t-" + pid))
            ev = self.through(pos, "PreToolUse", prompt_id=pid, tool_use_id="t-" + pid, texts=False)
            self.assertNotIn("texts", ev)
            self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (starts[-1], True), pid)
            ev = self.through(pos, "Stop", prompt_id=pid, texts=False)
            self.write(said("the reply of " + pid))
            self.assertNotIn("texts", ev)
            self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (starts[-1], True), pid)
        self.assertEqual(len(set(starts)), 3)
        self.assertEqual(self.through(pos, "Stop", prompt_id="p3")["texts"], ["the reply of p3"], "and the place went with it")

    def test_a_tools_result_is_not_where_a_turn_begins(self):
        """It carries its turn's promptId too. A turn whose events carry an id that no message in the file has (the
        case the code review of 120c38a ran: a background task's turn "bg-1" with only its tool result under that
        id) took its first tool result for its beginning: the words before the first call were dropped as the last
        turn's, and 📋 began at the result. Now: no message row, so the beginning stays where the reading stood,
        nothing is dropped, and the turn's end brings all of it."""
        pos = {}
        self.through(pos, "SessionStart")
        self.through(pos, "PreToolUse", prompt_id="bg-1", tool_use_id="t1")
        self.write(said("a background task ended") + self.call("t1") + self.result("bg-1", "t1"))
        ev = self.through(pos, "Stop", prompt_id="bg-1")
        self.assertEqual((ev["texts"], ev["dc_turn_start"], ev["dc_turn_sure"]), (["a background task ended"], 0, False))
        self.assertEqual(self.through(pos, "Stop", prompt_id="bg-1")["texts"], [], "once")

    def test_a_turn_begins_with_any_message_row_of_its_id(self):
        """A scheduled wake-up, another session's message: their rows are isMeta (measured) and begin a turn like the
        owner's; so does a message in blocks. A tool's result does not."""
        from altention.connectors.claude_code import transcript
        self.assertEqual(transcript.begins(json.loads(line("user", "wake", promptId="p1", isMeta=True))), "p1")
        self.assertEqual(transcript.begins(json.loads(line("user", [{"type": "text", "text": "x"}], promptId="p1"))), "p1")
        self.assertEqual(transcript.begins(json.loads(line("user", [{"type": "image"}, {"type": "tool_result", "tool_use_id": "t"}], promptId="p1"))), "p1")
        self.assertIsNone(transcript.begins(json.loads(self.result("p1", "t1"))))
        self.assertIsNone(transcript.begins(json.loads(line("user", "x", promptId="p1", isSidechain=True))))
        self.assertIsNone(transcript.begins(json.loads(line("user", "no id"))))
        self.assertIsNone(transcript.begins({"type": "user", "promptId": "p1"}))
        self.assertIsNone(transcript.begins(None))
        pos = {}
        self.through(pos, "SessionStart")
        self.write(said("the last turn's reply, late"))
        self.through(pos, "UserPromptSubmit", prompt_id="w1")
        start = self.size()
        self.write(line("user", "the scheduled prompt", promptId="w1", isMeta=True) + said("woken") + self.call("t1"))
        ev = self.through(pos, "PreToolUse", prompt_id="w1", tool_use_id="t1")
        self.assertEqual((ev["texts"], ev["dc_turn_start"], ev["dc_turn_sure"]), (["woken"], start, True))

    def test_an_event_waits_for_the_row_of_its_own_call(self):
        """awaits(): asked before the event is handled, changes nothing."""
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1"))
        call = self.ev("PreToolUse", prompt_id="p1", tool_use_id="t1", ts=100.0)
        before = (json.dumps(pos, sort_keys=True), dict(call))
        self.assertTrue(hooks.awaits(call, pos, now=100.2), "its row is not written yet")
        self.assertTrue(hooks.awaits(call, pos, now=100.0 + hooks.HOLD_SECS - 0.01))
        self.assertFalse(hooks.awaits(call, pos, now=100.0 + hooks.HOLD_SECS), "long enough: handled with what there is")
        self.assertFalse(hooks.awaits(call, pos, now=5000.0), "an event that waited while nobody looked is not held")
        self.assertFalse(hooks.awaits(call, pos, now=99.0), "a clock that went back: not held")
        self.assertTrue(hooks.awaits(call, pos, now=100.5, hold=1.0))
        self.assertFalse(hooks.awaits(call, pos, now=101.0, hold=1.0))
        self.assertEqual((json.dumps(pos, sort_keys=True), call), before, "asking changes nothing")
        self.write(said("before the call") + self.call("another"))
        self.assertTrue(hooks.awaits(call, pos, now=100.2), "another call's row is not its own")
        self.write(self.call("t1"))
        self.assertFalse(hooks.awaits(call, pos, now=100.2), "there it is")
        self.assertEqual(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="another")["texts"], ["before the call"])

    def test_what_never_waits(self):
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1"))
        held = self.ev("PreToolUse", prompt_id="p1", tool_use_id="t1", ts=100.0)
        self.assertTrue(hooks.awaits(held, pos, now=100.1))
        for name, ev in (("the end of a turn", self.ev("Stop", prompt_id="p1", tool_use_id="t1", ts=100.0)),
                         ("a prompt", self.ev("UserPromptSubmit", prompt_id="p1", ts=100.0)),
                         ("a subagent's call", dict(held, agent_id="a1")),
                         ("no tool_use_id", {k: v for k, v in held.items() if k != "tool_use_id"}),
                         ("no prompt_id (a Claude Code that sends none)", {k: v for k, v in held.items() if k != "prompt_id"}),
                         ("no time on it", {k: v for k, v in held.items() if k != "ts"}),
                         ("a time that is none", dict(held, ts="soon")),
                         ("no transcript named", {k: v for k, v in held.items() if k != "transcript_path"}),
                         ("the end of a turn in a transcript not met yet", self.ev("Stop", prompt_id="p1", ts=100.0, last_assistant_message="x",
                                                                                  transcript_path=os.path.join(self.tmp, "other.jsonl")))):
            self.assertFalse(hooks.awaits(ev, pos, now=100.1), name)
        for kind in ("Ask", "Plan"):
            self.assertTrue(hooks.awaits(dict(held, hook_event_name=kind), pos, now=100.1), "a question or a plan is a tool call too")

    def test_two_events_of_one_call_the_second_does_not_wait_for_a_row_already_read(self):
        """AskUserQuestion fires the plain PreToolUse hook and the question's own (Ask), with one tool_use_id: the first
        to be handled reads up to the call's row, which then lies behind the place. The other must not wait for it."""
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("one thing first") + self.call("t1"))
        first = self.ev("PreToolUse", prompt_id="p1", tool_use_id="t1", ts=100.0)
        self.assertFalse(hooks.awaits(first, pos, now=100.1))
        hooks.enrich(first, pos)
        self.assertEqual(first["texts"], ["one thing first"])
        second = self.ev("Ask", prompt_id="p1", tool_use_id="t1", ts=100.0)
        self.assertFalse(hooks.awaits(second, pos, now=100.1))
        hooks.enrich(second, pos)
        self.assertEqual(second["texts"], [])
        for n in range(60):  # the calls read are remembered, not for ever
            self.write(self.call("c%d" % n))
            hooks.enrich(self.ev("PreToolUse", prompt_id="p1", tool_use_id="c%d" % n), pos)
        self.assertLessEqual(len(pos[self.path]["called"]), hooks.CALLED_KEPT)

    def test_the_three_states_of_a_calls_own_row(self):
        """Read already: nothing more is read. In the file ahead: read up to it. Not in the file yet: all that is
        there (it lies before the call)."""
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + said("before the first"))
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t1")  # not in the file yet
        self.assertEqual(ev["texts"], ["before the first"])
        self.assertEqual(pos[self.path]["at"], self.size())
        self.write(self.call("t1") + self.result("p1", "t1") + said("before the second") + self.call("t2") + self.result("p1", "t2")
                   + said("the reply"))
        ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id="t2")  # in the file, ahead of the place
        self.assertEqual(ev["texts"], ["before the second"])
        self.assertEqual(pos[self.path]["called"], ["t1", "t2"], "every call gone past is remembered, not only its own")
        here = pos[self.path]["at"]
        for late in ("t1", "t2"):  # read already (the other event of the same call, or one that came out of order)
            ev = self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id=late)
            self.assertEqual((ev["texts"], pos[self.path]["at"]), ([], here), "nothing: what lies after is not this call's")
            self.assertFalse(hooks.awaits(self.ev("PreToolUse", prompt_id="p1", tool_use_id=late, ts=100.0), pos, now=100.1))
        self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["the reply"], "the reply comes with the turn's end, once")

    def test_the_calls_of_one_reply_in_any_order(self):
        """#code-review-2's rehearsal of 41092b0: three calls sent out at once (three Reads, no pause between the
        blocks) reach the spool in an order that is often not theirs (1, 2, 0). The one handled first reads past
        the others' rows; the one whose row was passed then found no row of its own: it waited out the whole limit
        and, let go, read to the end of the file, where the reply was written by then: the reply was posted as words
        on the way and again as the reply. Every order of the three: the words once, with the first event handled;
        no event waits once the rows are written; the reply only with the Stop."""
        import itertools
        rows = self.asked("p1") + said("before all three") + self.call("t0") + self.call("t1") + self.call("t2")
        after = self.result("p1", "t2") + self.result("p1", "t1") + self.result("p1", "t0") + said("the reply")
        for order in itertools.permutations(("t0", "t1", "t2")):
            for written_before in (False, True):  # the results and the reply already in the file, or not yet
                with open(self.path, "w"):
                    pass
                pos = {}
                self.through(pos, "UserPromptSubmit", prompt_id="p1")
                self.write(rows + (after if written_before else ""))
                brought = []
                for n, tid in enumerate(order):
                    self.assertFalse(hooks.awaits(self.ev("PreToolUse", prompt_id="p1", tool_use_id=tid, ts=100.0), pos, now=100.1), (order, tid))
                    brought.append(self.through(pos, "PreToolUse", prompt_id="p1", tool_use_id=tid)["texts"])
                self.assertEqual(brought, [["before all three"], [], []], (order, written_before))
                if not written_before:
                    self.write(after)
                self.assertEqual(self.through(pos, "Stop", prompt_id="p1")["texts"], ["the reply"], (order, written_before))

    def test_a_file_first_met_what_lies_behind_the_place_counts_as_read(self):
        """Code review of 2158420: the table is empty (the bot or the relay has just started, a conversation attached
        midway) and the reply's rows are written: its words and three calls. The first event puts the place at the
        end of the file; the other two calls' rows lie behind it. They are not to wait for them (they did, for the
        whole limit: also in the calls' own order) nor, let go, to read on to the reply written meanwhile. Every
        order; the results and the reply written after the first event is handled, as in that review."""
        import itertools
        rows = self.asked("p1") + said("WORDS") + self.call("t0") + self.call("t1") + self.call("t2")
        after = self.result("p1", "t2") + self.result("p1", "t1") + self.result("p1", "t0") + said("ANSWER")
        for order in itertools.permutations(("t0", "t1", "t2")):
            with open(self.path, "w"):
                pass
            self.write(self.asked("p0", "a turn before") + self.call("old") + self.result("p0", "old") + said("its reply") + rows)
            pos = {}  # nothing known of the file
            brought, waited = [], []
            for n, tid in enumerate(order):
                ev = self.ev("PreToolUse", prompt_id="p1", tool_use_id=tid, ts=100.0)
                waited.append(hooks.awaits(ev, pos, now=100.1))
                hooks.enrich(ev, pos)
                brought.append(ev["texts"])
                if n == 0:
                    self.write(after)
            self.assertEqual(waited, [False, False, False], order)
            self.assertEqual(brought, [[], [], []], (order, "the words were said before the file was met: not relayed, as ever"))
            stop = self.through(pos, "Stop", prompt_id="p1")
            self.assertEqual(stop["texts"], ["ANSWER"], (order, "the reply comes with the turn's end only"))
            self.assertEqual(pos[self.path]["called"][:4], ["old", "t0", "t1", "t2"], "the calls behind the place, as if read")
            nxt = self.through(pos, "UserPromptSubmit", prompt_id="p2")
            self.assertEqual(pos[self.path]["called"], [], "and they end with the turn like any")
            self.assertIsNotNone(nxt)

    def test_a_question_first_met_does_not_wait_for_the_row_behind(self):
        """The two events of one call (PreToolUse and Ask) with an empty table: the second used to wait out the limit
        (the question came two seconds late)."""
        self.write(self.asked("p1") + said("one thing first") + self.call("t1"))
        pos = {}
        for kind in ("PreToolUse", "Ask"):
            ev = self.ev(kind, prompt_id="p1", tool_use_id="t1", ts=100.0)
            self.assertFalse(hooks.awaits(ev, pos, now=100.1), kind)
            hooks.enrich(ev, pos)
            self.assertEqual(ev["texts"], [], kind)

    def test_the_calls_behind_a_place(self):
        from altention.connectors.claude_code import transcript
        first = self.asked("p1") + self.call("a") + line("assistant", [{"type": "tool_use", "id": "s", "name": "X", "input": {}}], isSidechain=True)
        self.write(first + "not json but says \"tool_use\"\n" + self.call("b") + self.call("c"))
        self.assertEqual(transcript.calls_before(self.path, self.size()), ["a", "b", "c"])
        self.assertEqual(transcript.calls_before(self.path, len(first.encode())), ["a"], "only what lies before that byte")
        self.assertEqual(transcript.calls_before(self.path, self.size(), tail_bytes=len(self.call("c").encode()) + 5), ["c"],
                         "a stretch that begins in the middle of a row: from the next whole row on")
        self.assertEqual(transcript.calls_before(self.path, 0), [])
        self.assertEqual(transcript.calls_before(os.path.join(self.tmp, "missing.jsonl"), 100), [])
        self.assertEqual(transcript.calls_before(None, 100), [])

    def test_the_calls_remembered_end_with_the_turn(self):
        pos = {}
        self.through(pos, "UserPromptSubmit", prompt_id="p1")
        self.write(self.asked("p1") + "".join(self.call("c%d" % n) for n in range(hooks.CALLED_KEPT + 20)))
        self.through(pos, "Stop", prompt_id="p1")
        self.assertEqual(len(pos[self.path]["called"]), hooks.CALLED_KEPT, "within a turn: at most so many")
        self.through(pos, "UserPromptSubmit", prompt_id="p2")
        self.assertEqual(pos[self.path]["called"], [], "a turn begins: they were the last turn's")
        self.write(self.asked("p2") + said("again") + self.call("c1"))
        ev = self.through(pos, "PreToolUse", prompt_id="p2", tool_use_id="c1")
        self.assertEqual(ev["texts"], ["again"], "an id seen in a turn before is this turn's own call now")

    def subagent(self, aid, meta, lines, run=None):
        folder = os.path.join(self.tmp, "s1", "subagents", *(["workflows", run] if run else []))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "agent-%s.meta.json" % aid), "w") as f:
            json.dump(meta, f)
        self.write(lines, os.path.join(folder, "agent-%s.jsonl" % aid))

    def test_a_subagents_details(self):
        self.subagent("a1", {"description": "Look around"}, line("user", "find the thing"))
        ev = self.through({}, "SubagentStart", agent_id="a1")
        self.assertEqual((ev["agent_meta"], ev["agent_run"], ev["agent_prompt"]), ({"description": "Look around"}, None, "find the thing"))
        self.subagent("w1", {"description": "One of a run"}, line("user", "its task"), run="wf_7")
        ev = self.through({}, "SubagentStart", agent_id="w1")
        self.assertEqual((ev["agent_run"], ev["agent_prompt"]), ("wf_7", "its task"))

    def test_enrich_itself_looks_for_no_subagent_and_never_sleeps(self):
        """Since stage 3b step 2.2c: a subagent's details are another function's (agent(), which looks once), and
        the waiting is the caller's."""
        self.subagent("a1", {"description": "Look around"}, line("user", "find the thing"))
        with mock.patch.object(time, "sleep", side_effect=AssertionError("slept")):
            t0 = time.time()
            for aid in ("a1", "not-written-yet"):
                ev = self.through({}, "SubagentStart", agent_id=aid, agents=False)
                self.assertEqual([k for k in ev if k.startswith("agent_") and k != "agent_id"], [])
            self.assertLess(time.time() - t0, 0.1)

    def test_one_look_says_whether_all_is_there_and_never_sleeps(self):
        with mock.patch.object(time, "sleep", side_effect=AssertionError("slept")):
            nothing = self.ev("SubagentStart", agent_id="not-written-yet")
            self.assertFalse(hooks.agent(nothing))
            self.assertEqual([k for k in nothing if k.startswith("agent_") and k != "agent_id"], [], "no files: nothing put on the event")
            self.subagent("a1", {"description": "Look around"}, "")  # its files, and no row yet
            early = self.ev("SubagentStart", agent_id="a1")
            self.assertFalse(hooks.agent(early), "not all there: the row that asks it is not written")
            self.assertEqual((early["agent_meta"], early["agent_run"], early["agent_prompt"]), ({"description": "Look around"}, None, ""))
            self.subagent("a1", {"description": "Look around"}, line("user", "find the thing"))
            self.assertTrue(hooks.agent(early))
            self.assertEqual(early["agent_prompt"], "find the thing")
            self.assertEqual(list(hooks.agent_waits(early)), [], "all there: no wait")
            self.assertEqual(list(hooks.agent_waits(self.ev("SubagentStart", agent_id="never"))), [0.25] * 6, "the count and the gaps are the connector's")
            self.assertFalse(hooks.agent({"hook_event_name": "SubagentStart"}), "no id, no file: no")

    def test_files_not_there_yet_are_waited_for(self):
        slept = []
        with mock.patch.object(time, "sleep", slept.append):
            ev = self.through({}, "SubagentStart", agent_id="never")
        self.assertEqual(slept, [0.25] * 6)
        self.assertNotIn("agent_meta", ev)

    def test_what_a_subagent_was_asked_is_waited_for_too(self):
        """#issues 24: the subagent's files are there when its SubagentStart comes, its first row is not yet (Claude
        Code writes it a moment after the hook, as with every row): the thread had no task in 3 of 6 tries on another
        machine. The wait that was there for the files goes on until the first row can be read."""
        self.subagent("a1", {"description": "Look around"}, "")
        slept = []

        def sleep(secs):
            slept.append(secs)
            if len(slept) == 2:  # the row is written half a second after the event
                self.subagent("a1", {"description": "Look around"}, line("user", "find the thing"))
        with mock.patch.object(time, "sleep", sleep):
            ev = self.through({}, "SubagentStart", agent_id="a1")
        self.assertEqual((ev["agent_prompt"], ev["agent_meta"], slept), ("find the thing", {"description": "Look around"}, [0.25, 0.25]))

    def test_a_subagent_whose_first_row_never_comes_is_not_waited_for_longer_than_its_files_were(self):
        self.subagent("a1", {"description": "Look around"}, "")
        slept = []
        with mock.patch.object(time, "sleep", slept.append):
            ev = self.through({}, "SubagentStart", agent_id="a1")
        self.assertEqual((ev["agent_prompt"], ev["agent_meta"], slept), ("", {"description": "Look around"}, [0.25] * 6),
                         "the files were found: the event says so, with no task; 1.5 s at most, as before")

    def test_a_subagent_ready_at_once_costs_no_wait(self):
        self.subagent("a1", {}, line("user", "x"))
        with mock.patch.object(time, "sleep", side_effect=AssertionError("slept")):
            self.assertEqual(self.through({}, "SubagentStart", agent_id="a1")["agent_prompt"], "x")

    FORK = [{"type": "tool_result", "tool_use_id": "t", "content": "ok"},
            {"type": "text", "text": "<fork-boilerplate>you are a fork of the conversation</fork-boilerplate>"}]

    def test_a_forked_agent_whose_first_row_leaves_no_text_is_not_waited_for(self):
        """Code review of 8b2800f: a forked agent's first row is a tool's result and a notice in <…>; nothing of it is
        a task text. Waiting for a text waited the whole 1.5 s for each such agent, with every other conversation of
        the machine standing still (four of them: 6 s). The row is there: that is all there is to wait for."""
        self.subagent("f1", {"agentType": "fork", "description": "A fork"}, line("user", self.FORK))
        with mock.patch.object(time, "sleep", side_effect=AssertionError("slept")):
            ev = self.through({}, "SubagentStart", agent_id="f1")
        self.assertEqual((ev["agent_prompt"], ev["agent_meta"]["description"]), ("", "A fork"))

    def test_four_agents_started_at_once_cost_one_wait_together(self):
        """Their files appear 0.02 to 0.08 s after their hooks (measured): by the end of the first wait all are there."""
        slept = []

        def sleep(secs):
            slept.append(secs)
            if len(slept) == 1:
                for n in range(4):
                    self.subagent("a%d" % n, {}, line("user", self.FORK if n % 2 else "task %d" % n))
        with mock.patch.object(time, "sleep", sleep):
            prompts = [self.through({}, "SubagentStart", agent_id="a%d" % n)["agent_prompt"] for n in range(4)]
        self.assertEqual((prompts, slept), (["task 0", "", "task 2", ""], [0.25]))

    RELAY = "[Workflow harness — user request] The harness relays … the user request:\n  do the thing"
    TASK = "[Workflow harness — computed task] … The computed task text follows:\n  Count the files."

    def test_a_workflow_agent_with_only_the_relay_written_waits_for_its_task(self):
        """Its two rows came together in the one run recorded; should they not: the relay alone is no task yet."""
        self.subagent("w1", {"description": "wf-a"}, line("user", self.RELAY), run="wf_1")
        slept = []

        def sleep(secs):
            slept.append(secs)
            if len(slept) == 2:
                self.subagent("w1", {"description": "wf-a"}, line("user", self.TASK), run="wf_1")
        with mock.patch.object(time, "sleep", sleep):
            ev = self.through({}, "SubagentStart", agent_id="w1")
        self.assertEqual((ev["agent_prompt"], ev["agent_run"], slept), ("Count the files.", "wf_1", [0.25, 0.25]))

    def test_a_task_row_that_never_follows_the_relay_is_waited_for_no_longer_than_ever(self):
        self.subagent("w1", {"description": "wf-a"}, line("user", self.RELAY), run="wf_1")
        slept = []
        with mock.patch.object(time, "sleep", slept.append):
            ev = self.through({}, "SubagentStart", agent_id="w1")
        self.assertEqual((ev["agent_prompt"], slept), (self.RELAY, [0.25] * 6), "the limit, then what the first row said, as before")

    def test_a_harness_worded_otherwise_does_not_hold_every_agent_to_the_limit(self):
        """Code review of c6c96a4: should a version of Claude Code word the harness's task row otherwise, the row is
        not known for a task, and every agent of a workflow was waited for 1.5 s (a workflow starts many), every
        conversation standing still meanwhile. An agent that has begun to answer is waited for no longer; its task
        shows as its first row says, as it did before these rows were told apart."""
        other = "[Workflow harness — computed task] … Here is the task, worded anew:\n  Count the files."
        self.subagent("w1", {"description": "wf-a"}, line("user", self.RELAY) + line("user", other)
                      + line("assistant", [{"type": "text", "text": "counting"}]), run="wf_1")
        with mock.patch.object(time, "sleep", side_effect=AssertionError("slept")):
            ev = self.through({}, "SubagentStart", agent_id="w1")
        self.assertEqual((ev["agent_prompt"], ev["agent_run"]), (self.RELAY, "wf_1"))

    def test_a_task_row_worded_otherwise_is_not_waited_for_at_the_moment_of_the_event(self):
        """Code review of 5a21a14: the SubagentStart is handled when the agent has just started: its rows are being
        written and its first answer is seconds away. With the task row worded anew, waiting for an answer was
        waiting to the limit all the same. The second row said to it is there: that is enough, whatever it says."""
        other = "[Workflow harness — computed task] … Here is the task, worded anew:\n  Count the files."
        self.subagent("w1", {"description": "wf-a"}, line("user", self.RELAY) + line("user", other), run="wf_1")
        with mock.patch.object(time, "sleep", side_effect=AssertionError("slept")):
            ev = self.through({}, "SubagentStart", agent_id="w1")
        self.assertEqual((ev["agent_prompt"], ev["agent_run"]), (self.RELAY, "wf_1"), "shown as the first row says: the worst of it")

    def test_when_a_subagent_has_been_told(self):
        from altention.connectors.claude_code import transcript
        cases = (("", False, "nothing written"),
                 (line("assistant", [{"type": "text", "text": "x"}]), False, "nothing said to it yet"),
                 (line("user", "a task"), True, "a task"),
                 (line("user", ""), True, "an empty message is a row all the same"),
                 (line("user", self.FORK), True, "a fork's first row"),
                 (line("user", self.RELAY), False, "a workflow's relay alone"),
                 (line("user", self.RELAY) + line("user", self.TASK), True, "the relay and the task"),
                 (line("user", self.RELAY) + line("user", "something else"), True, "the relay and something else: taken as it is"),
                 (line("user", self.TASK), True, "the task in the first row"),
                 (line("user", self.RELAY) + line("assistant", [{"type": "text", "text": "on it"}]), True,
                  "the relay alone, and the agent already answering: a harness worded otherwise, nothing more to wait for"),
                 (line("assistant", [{"type": "text", "text": "x"}]) + line("user", self.RELAY), False,
                  "an answer before anything was said to it does not count"),
                 (line("user", self.RELAY) + line("user", "[Workflow harness — computed task] … worded anew:\n  Count."), True,
                  "the relay and a second harness row that is not known for a task: two rows said to it, enough"),
                 (line("user", self.RELAY) + line("user", self.FORK), True, "a second row of any shape"))
        for n, (rows, want, why) in enumerate(cases):
            path = os.path.join(self.tmp, "case%d.jsonl" % n)
            with open(path, "w", encoding="utf-8") as f:
                f.write(rows)
            self.assertEqual(transcript.asked_yet(path), want, why)
        self.assertFalse(transcript.asked_yet(os.path.join(self.tmp, "missing.jsonl")))

    def test_only_the_names_it_says_it_adds(self):
        self.subagent("a1", {}, line("user", "x"))
        self.write(said("x"))
        for kind in hooks.EVENTS + ("Ask", "Plan", "PostToolUse"):
            before = self.ev(kind, agent_id="a1") if kind == "SubagentStart" else self.ev(kind, prompt_id="p1")
            ev = dict(before)
            hooks.enrich(ev, {})
            self.assertEqual({k: ev[k] for k in before}, before, kind)
            self.assertLessEqual(set(ev) - set(before), set(hooks.ADDED), kind)

if __name__ == "__main__":
    unittest.main()
