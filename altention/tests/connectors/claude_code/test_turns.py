"""Where a turn lies and what an event may read (hooks.enrich, stage 3b step 1.6a), held against recorded runs of
the real Claude Code 2.1.296 and 2.1.288 (turns/, played by replay.py) in three timings of the one who handles the
events: when each hook is called, just before the next, and piled up. Whatever the timing: every word Claude said
on the way comes once and with an event of its own turn, a turn begins at its own first row, and its end is the
next turn's beginning."""
import os
import tempfile
import unittest

from altention.connectors.claude_code import hooks, transcript

from . import replay


def played(version, name, timing, hold=None):
    """(events as enrich left them, the transcript's rows as (start, end, row), the path's folder kept alive).
    `hold`: seconds an event may wait for the row of its tool call (hooks.awaits), as the bot and the relay do
    since step 1.6b; None: nobody waits (step 1.6a)."""
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "s.jsonl")
    open(path, "w").close()
    pos = {}
    awaits = None if hold is None else (lambda ev, now: hooks.awaits(ev, pos, now=now, hold=hold))
    events = replay.play(replay.load(version, name), path, lambda ev: hooks.enrich(ev, pos), timing, awaits=awaits)
    return events, transcript.rows(path), path, tmp, pos


def turns_of(rows):
    """{promptId: byte of the first row said to Claude under it}, in the order of the file."""
    first = {}
    for start, _end, row in rows:
        pid = transcript.asked_as(row)
        if pid and pid not in first:
            first[pid] = start
    return first


def on_the_way(events):
    """[(prompt_id of the event, text)] for what the bot would post as said on the way: an event's texts without
    the reply its own Stop brings and without the reply of the turn before (the bot's rt.last_final)."""
    out, last_final = [], ""
    for ev in events:
        final = (ev.get("last_assistant_message") or "").strip() if ev["hook_event_name"] in hooks.ENDS else ""
        out += [(ev.get("prompt_id"), t) for t in ev.get("texts") or [] if t not in (final, last_final)]
        if ev["hook_event_name"] in hooks.ENDS and not ev.get("agent_id"):
            last_final = final
    return out


class Recorded(unittest.TestCase):
    def each(self):
        for version in replay.VERSIONS:
            for name in replay.names(version):
                for timing in replay.TIMINGS:
                    yield version, name, timing

    def test_there_are_runs_of_both_versions(self):
        self.assertEqual({v: replay.names(v) for v in replay.VERSIONS}, {"2.1.296": ["m1-M", "m2-C", "q1-A", "w3-F", "w4-R"], "2.1.288": ["m3-M", "m4-C"]})

    def test_every_word_on_the_way_once_and_in_its_own_turn(self):
        for version, name, timing in self.each():
            events, rows, _path, tmp, _pos = played(version, name, timing)
            with tmp:
                finals = set((ev.get("last_assistant_message") or "").strip() for ev in events if ev["hook_event_name"] in hooks.ENDS)
                want, turn = [], None
                for _start, _end, row in rows:  # the words in the file that are no turn's reply, each with its turn
                    turn = transcript.asked_as(row) or turn
                    want += [(turn, t) for t in transcript.said_in(row) if t not in finals]
                self.assertEqual(sorted(on_the_way(events)), sorted(want), (version, name, timing))

    def test_the_runs_do_hold_words_said_on_the_way(self):
        """So that the test above cannot pass on nothing: the text before one tool call (q1-A) and before three in
        one reply (m1-M, m3-M: all four rows written 1.5 s after the first call's hook)."""
        for version, name, text in (("2.1.296", "q1-A", "TEXT-BEFORE-THE-TOOL."), ("2.1.296", "m1-M", "TEXT-BEFORE-THREE-TOOLS."),
                                    ("2.1.288", "m3-M", "TEXT-BEFORE-THREE-TOOLS.")):
            for timing in replay.TIMINGS:
                events, _rows, _path, tmp, _pos = played(version, name, timing)
                with tmp:
                    self.assertEqual([t for _, t in on_the_way(events)].count(text), 1, (version, name, timing))

    def test_a_turn_begins_at_its_own_first_row(self):
        """By the end of each turn (its Stop or StopFailure), whenever its events were handled: also when the
        turn's first hook was called before the reply of the turn before was written (q1-A at 100.9 s, m1-M at
        37.9 s: Claude Code's own prompt after a background subagent, 0.06 s after the Stop)."""
        for version, name, timing in self.each():
            events, rows, _path, tmp, _pos = played(version, name, timing)
            with tmp:
                first = turns_of(rows)
                ends = [ev for ev in events if ev["hook_event_name"] in hooks.ENDS and not ev.get("agent_id")]
                self.assertGreaterEqual(len(ends), 2, (version, name))
                for ev in ends:
                    if ev["dc_turn_sure"]:
                        self.assertEqual(ev["dc_turn_start"], first[ev["prompt_id"]], (version, name, timing, ev["prompt_id"]))
                    else:  # handled before the turn's own row was written, and no later event of the turn: only a
                        # turn that failed at once (q1-A: the API refuses 0.1 s after the prompt), and only "live"
                        self.assertEqual((ev["hook_event_name"], timing), ("StopFailure", "live"), (version, name, ev["prompt_id"]))
                        self.assertLessEqual(ev["dc_turn_start"], first[ev["prompt_id"]])

    def test_a_turn_ends_where_the_next_begins(self):
        for version in replay.VERSIONS:
            for name in replay.names(version):
                _events, rows, path, tmp, _pos = played(version, name, "piled")
                with tmp:
                    starts = sorted(turns_of(rows).values())
                    self.assertGreaterEqual(len(starts), 2)
                    for start, following in zip(starts, starts[1:] + [None]):
                        self.assertEqual(transcript.turn_end(path, start), following, (version, name, start))
                    asked = [i for i in transcript.turn(path, starts[0], transcript.turn_end(path, starts[0])) if i[0] == "asked"]
                    self.assertEqual(len(asked), 1, "the first turn holds its own message and not the next one's")

    def test_nothing_is_left_unread_behind_a_row(self):
        """The reading never sticks: after the last event every turn that ended was read to its end."""
        for version, name, timing in self.each():
            events, rows, path, tmp, pos = played(version, name, timing)
            with tmp:
                last = [ev for ev in events if ev["hook_event_name"] in hooks.ENDS and not ev.get("agent_id")][-1]
                self.assertGreaterEqual(pos[path]["at"], turns_of(rows)[last["prompt_id"]], (version, name, timing))

    def test_events_of_a_subagent_neither_begin_a_turn_nor_read(self):
        """They carry the prompt_id of whatever turn the main conversation is in then (m1-M: started in one turn,
        its own tool call and its end in the next)."""
        for timing in replay.TIMINGS:
            events, _rows, _path, tmp, _pos = played("2.1.296", "m1-M", timing)
            with tmp:
                theirs = [ev for ev in events if ev.get("agent_id")]
                self.assertGreaterEqual(len(theirs), 4)
                for ev in theirs:
                    self.assertEqual([k for k in ev if k in ("texts", "dc_turn_start", "dc_turn_sure")], [], ev["hook_event_name"])

    def test_a_compaction_is_one_turn(self):
        """m2-C, m4-C: UserPromptSubmit, PreCompact, SubagentStop, SessionStart(compact), PostCompact, the reply and
        Stop under one prompt_id; the owner's row is written before the compact boundary and the row "This session
        is being continued…" carries the same promptId: the first one is the turn's. A PreCompact that nothing
        followed (the turn before) is part of its turn like any event."""
        for version, name in (("2.1.296", "m2-C"), ("2.1.288", "m4-C")):
            for timing in replay.TIMINGS:
                events, rows, _path, tmp, _pos = played(version, name, timing)
                with tmp:
                    compact = next(ev for ev in events if ev["hook_event_name"] == "SessionStart" and ev.get("source") == "compact")
                    same = [ev for ev in events if ev.get("prompt_id") == compact["prompt_id"]]
                    self.assertEqual([ev["hook_event_name"] for ev in same],
                                     ["UserPromptSubmit", "PreCompact", "SubagentStop", "SessionStart", "PostCompact", "Stop"])
                    mine = [start for start, _e, row in rows if transcript.asked_as(row) == compact["prompt_id"]]
                    self.assertEqual(len(mine), 2)
                    self.assertEqual((same[-1]["dc_turn_start"], same[-1]["dc_turn_sure"]), (mine[0], True), (version, timing))
                    alone = [ev for ev in events if ev["hook_event_name"] == "PreCompact" and ev["prompt_id"] != compact["prompt_id"]]
                    self.assertEqual(len(alone), 1, "the one that nothing followed")

    def test_an_interrupted_turn_ends_with_the_next_prompt(self):
        """m1-M: Escape before any reply (no event at all, the turn has only its own row) and Escape while a tool
        runs (no PostToolUse, no Stop; two rows written under the interrupted turn's promptId)."""
        for version, name in (("2.1.296", "m1-M"), ("2.1.288", "m3-M")):
            events, rows, path, tmp, _pos = played(version, name, "live")
            with tmp:
                first = turns_of(rows)
                stopped = set(ev["prompt_id"] for ev in events if ev["hook_event_name"] in hooks.ENDS)
                interrupted = [pid for pid in first if pid not in stopped]
                self.assertEqual(len(interrupted), 2, version)
                texts = {}
                for pid in interrupted:
                    items = transcript.turn(path, first[pid], transcript.turn_end(path, first[pid]))
                    texts[pid] = [i[1] for i in items if i[0] == "asked"]
                self.assertEqual(sorted(len(v) for v in texts.values()), [1, 2])
                self.assertTrue(any("[Request interrupted by user for tool use]" in t for v in texts.values() for t in v),
                                "the two rows of the interruption are the interrupted turn's")



class Held(unittest.TestCase):
    """Step 1.6b: an event of a tool call is left until the row of that call is written (hooks.awaits), for at most
    HOLD_SECS. The same runs, handled by one who looks again after every step."""

    def test_the_rules_of_step_1_6a_hold_with_waiting_too(self):
        for version in replay.VERSIONS:
            for name in replay.names(version):
                for timing in replay.TIMINGS:
                    events, rows, _path, tmp, _pos = played(version, name, timing, hold=hooks.HOLD_SECS)
                    with tmp:
                        finals = set((ev.get("last_assistant_message") or "").strip() for ev in events if ev["hook_event_name"] in hooks.ENDS)
                        want, turn = [], None
                        for _start, _end, row in rows:
                            turn = transcript.asked_as(row) or turn
                            want += [(turn, t) for t in transcript.said_in(row) if t not in finals]
                        self.assertEqual(sorted(on_the_way(events)), sorted(want), (version, name, timing))
                        self.assertEqual([ev["hook_event_name"] for ev in events],
                                         [s["hook"]["hook_event_name"] for s in replay.load(version, name)["steps"] if "hook" in s],
                                         "the order of the events is the order the hooks were called in")

    def test_the_words_before_a_call_come_with_that_call(self):
        """Not one event late (after the tool has run): q1-A's text before one call (its row 0.1 s after the hook),
        and the text before three calls in one reply (m1-M, m3-M: the rows 1.5 s after the first call's hook)."""
        for version, name, text in (("2.1.296", "q1-A", "TEXT-BEFORE-THE-TOOL."), ("2.1.296", "m1-M", "TEXT-BEFORE-THREE-TOOLS."),
                                    ("2.1.288", "m3-M", "TEXT-BEFORE-THREE-TOOLS.")):
            events, _rows, _path, tmp, _pos = played(version, name, "live", hold=hooks.HOLD_SECS)
            with tmp:
                bringing = [ev for ev in events if text in (ev.get("texts") or [])]
                self.assertEqual([ev["hook_event_name"] for ev in bringing], ["PreToolUse"], (version, name))
                calls = [ev for ev in events if ev["hook_event_name"] == "PreToolUse" and ev.get("prompt_id") == bringing[0]["prompt_id"]
                         and not ev.get("agent_id")]
                self.assertIs(bringing[0], calls[0], "the first call of that reply: the words stand before its tool")
            without, _rows, _path, tmp, _pos = played(version, name, "live")
            with tmp:
                self.assertEqual([ev["hook_event_name"] for ev in without if text in (ev.get("texts") or [])], ["Stop"],
                                 "and with nobody waiting they came with the turn's end")

    def test_a_row_later_than_the_wait_costs_one_event_not_the_words(self):
        """A longer reply than was measured: with a wait of 1 s the three calls' rows (1.5 s) come too late. The first
        call is handled without the words; they come once, with a later event of the turn. Nothing is lost or said
        twice, and the events keep their order."""
        for version, name in (("2.1.296", "m1-M"), ("2.1.288", "m3-M")):
            events, _rows, _path, tmp, _pos = played(version, name, "live", hold=1.0)
            with tmp:
                text = "TEXT-BEFORE-THREE-TOOLS."
                bringing = [ev for ev in events if text in (ev.get("texts") or [])]
                self.assertEqual(len(bringing), 1, version)
                first = next(ev for ev in events if ev["hook_event_name"] == "PreToolUse" and ev.get("prompt_id") == bringing[0]["prompt_id"])
                self.assertIsNot(bringing[0], first)
                self.assertEqual(first["texts"], [])

    def test_an_interrupted_tool_call_still_brings_what_was_said_before_it(self):
        """#issues 26: a turn interrupted while its tool runs has no later event. Its words came only if the call's
        event happened to be handled after the row was written. m1-M's interrupted call has no words before it; the
        row itself is what is waited for: the event is handed over only once it is there."""
        run = replay.load("2.1.296", "m1-M")
        steps = run["steps"]
        at = next(i for i, s in enumerate(steps) if s.get("mark", "").startswith("M3b a tool of 30 s"))
        hook = next(s for s in steps[at:] if "hook" in s and s["hook"]["hook_event_name"] == "PreToolUse")
        row = next(s for s in steps[at:] if "write" in s and transcript.calls(s["write"], hook["hook"]["tool_use_id"]))
        self.assertGreater(row["t"], hook["t"], "the row is written after its hook")
        self.assertLess(row["t"] - hook["t"], hooks.HOLD_SECS)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            open(path, "w").close()
            pos, read_then = {}, {}

            def handle(ev):
                hooks.enrich(ev, pos)
                if ev.get("tool_use_id"):
                    read_then[ev["tool_use_id"]] = list(pos[path].get("called") or [])
            replay.play(run, path, handle, "live", awaits=lambda ev, now: hooks.awaits(ev, pos, now=now, hold=hooks.HOLD_SECS))
            call = hook["hook"]["tool_use_id"]
            self.assertIn(call, read_then[call], "its event was handled with its row read, not before")



class Workflow(unittest.TestCase):
    """Runs with a workflow (real sessions and the real API, 2026-10-12; two agents, one line each): w3-F beside the
    bot on its own machine, w4-R on another machine (recorded there: the events as tools/dchook spooled them, before
    tools/dcremote's relay added anything, which is what enrich does here). A workflow's agents' files lie one folder
    deeper than a plain subagent's, <session>/subagents/workflows/<run id>/, and are not there when the agent's
    SubagentStart hook is called: the meta file 0.02 to 0.04 s later, the transcript 0.04 to 0.11 s later, with its
    first two rows at once. tools/dcremote waits for them asleep; here its sleep lets the run go on."""
    # each run with what is its own: the agents' labels; how often tools/dcremote would wait in each timing; the
    # reply that ends the second turn; how many events carry an agent_id (w4-R: also the ends of two helper agents)
    RUNS = (("w3-F", ["wf-a", "wf-b"], {"live": [0.25, 0.25], "late": [0.25], "piled": []}, "WORKFLOW done", 4),
            ("w4-R", ["agent-A", "agent-B"], {"live": [0.25, 0.25], "late": [], "piled": []}, "WORKFLOW-2 done", 6))
    TASKS = ["Reply with exactly one line: WF-A. Do not use any tools. Do not read or write any files.",
             "Reply with exactly one line: WF-B. Do not use any tools. Do not read or write any files."]

    def started(self, name, timing):
        import time
        from unittest import mock
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "s.jsonl")
        open(path, "w").close()
        pos, slept = {}, []

        def sleep(secs):
            slept.append(secs)
            replay.play.advance(secs)
        def as_the_relay(ev):  # tools/dcremote's enrich(): the connector's, then a subagent's details, waited for asleep
            hooks.enrich(ev, pos)
            if ev.get("hook_event_name") == "SubagentStart" and ev.get("agent_id"):
                for gap in hooks.agent_waits(ev):
                    time.sleep(gap)
        with mock.patch.object(time, "sleep", sleep):
            events = replay.play(replay.load("2.1.296", name), path, as_the_relay, timing)
        return events, slept, path, tmp

    def test_each_agent_is_found_with_its_run_and_what_it_was_asked(self):
        for name, labels, waits, _last, _theirs in self.RUNS:
            for timing in replay.TIMINGS:
                events, slept, path, tmp = self.started(name, timing)
                where = (name, timing)
                with tmp:
                    run = next(ev["started"]["runId"] for ev in events if ev["hook_event_name"] == "PostToolUse")
                    starts = [ev for ev in events if ev["hook_event_name"] == "SubagentStart"]
                    self.assertEqual([ev["agent_meta"]["description"] for ev in starts], labels, where)
                    self.assertEqual([ev["agent_run"] for ev in starts], [run, run], where)
                    self.assertEqual([ev["agent_prompt"] for ev in starts], self.TASKS,
                                     (where, "#issues 24: never a thread with no task; #issues 28: each agent's own task, not the "
                                             "harness's relay of the owner's request"))
                    for ev in starts:
                        folder = os.path.join(path[:-len(".jsonl")], "subagents", "workflows", run)
                        self.assertTrue(os.path.isfile(os.path.join(folder, "agent-%s.jsonl" % ev["agent_id"])), where)
                    self.assertEqual(slept, waits[timing],
                                     (where, "handled when the hook is called: the files are not there, one wait each; piled up: "
                                             "they are written, none; just before the next hook: an agent whose next hook follows "
                                             "at once has no files yet (w3-F's second), one whose files are there has (w4-R's)"))

    def test_the_agents_events_begin_no_turn_and_the_run_is_two_turns(self):
        """The main turn ends while the workflow still runs; Claude Code prompts itself when it is done. Claude Code's
        own helper agents end in between (SubagentStop with no agent_type and no SubagentStart: w4-R has two)."""
        for name, _labels, _waits, last, count in self.RUNS:
            events, _slept, path, tmp = self.started(name, "live")
            with tmp:
                ends = [ev for ev in events if ev["hook_event_name"] == "Stop"]
                self.assertEqual([ev["last_assistant_message"] for ev in ends], ["WORKFLOW STARTED.", last], name)
                self.assertNotEqual(ends[0]["prompt_id"], ends[1]["prompt_id"], name)
                theirs = [ev for ev in events if ev.get("agent_id")]
                self.assertEqual(len(theirs), count, name)
                self.assertEqual([k for ev in theirs for k in ev if k in ("texts", "dc_turn_start")], [], name)
                named = [ev for ev in theirs if ev.get("agent_type")]
                self.assertEqual(len(named), 4, (name, "two agents: a start and an end each"))
                self.assertEqual(set(ev["prompt_id"] for ev in named if ev["hook_event_name"] == "SubagentStart"), {ends[0]["prompt_id"]},
                                 (name, "they carry the main turn's id of that moment"))


if __name__ == "__main__":
    unittest.main()
