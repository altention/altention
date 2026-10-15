"""An event of a tool call waits in the spool for the row of that call (stage 3b step 1.6b): Claude Code calls the
hook first and writes the row, with what was said ahead of the call, a moment later. Left for the next round, the
event brings those words with it; before, they came one event late (after the tool had run), or not at all when the
turn was interrupted (#issues 26). What may wait and for how long is the connector's (hooks.awaits, held against
recorded runs there); here: the bot's round and the relay on another machine leave the event where it is, keep the
order of its conversation, and go on with every other one."""
import asyncio
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import hooks
from altention.tests.connectors.claude_code.test_hooks import line, said
from bot import ask, config, tmux_bridge
from tests.test_turn_end import Ch
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reply(text):
    """The row a turn ends with, as Claude Code writes it: with the reply's usage and its stop_reason (a turn's end
    waits for it since step 2.2f: hooks.awaits())."""
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                                        "usage": {"input_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}},
                      ensure_ascii=False, separators=(",", ":")) + "\n"


def call(tid, command="ls"):
    return line("assistant", [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}])


class TwoChannels(object):
    def __init__(self):
        self.chs, self.channels_state, self.audits = {}, {}, []

    def get_channel(self, cid):
        if cid not in self.chs:
            self.chs[cid] = Ch()
            self.chs[cid].id = cid
        return self.chs[cid]

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {"tmux": "s", "cwd": "/tmp"})

    def save(self):
        pass

    async def audit(self, text):
        self.audits.append(text)


class Pump(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM)
        self.state, config.STATE = config.STATE, os.path.join(self.tmp.name, "state")  # (what the bridge writes down)
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM = 42, True
        self.host = TwoChannels()
        self.bridge = tmux_bridge.TmuxBridge(self.host)
        self.bridge.last_reap, self.bridge.streams_checked = 1e18, 1e18

        async def nothing():
            return None
        self.bridge.agents.tick = nothing
        self.spool = os.path.join(self.tmp.name, "events")
        os.makedirs(self.spool)
        self.n = 0
        self.now = 1000.0

    def tearDown(self):
        ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM = self.saved
        config.STATE = self.state
        self.tmp.cleanup()

    def path(self, channel):
        return os.path.join(self.tmp.name, "s%s.jsonl" % channel)

    def write(self, channel, text):
        with open(self.path(channel), "a", encoding="utf-8") as f:
            f.write(text)

    def fire(self, channel, kind, **kw):
        """What tools/dchook spools: the event with the time its hook ran."""
        self.n += 1
        ev = dict({"channel": str(channel), "hook_event_name": kind, "session_id": "s%s" % channel,
                   "transcript_path": self.path(channel), "ts": self.now}, **kw)
        with open(os.path.join(self.spool, "%04d.json" % self.n), "w") as f:
            json.dump(ev, f)

    def round(self, later=0.0):
        """One round of the pump, `later` seconds after the last."""
        self.now += later

        async def go():
            await self.bridge.pump_once()
            for rt in self.bridge.rts.values():
                if rt.typing is not None:
                    rt.typing.cancel()
        with mock.patch.object(tmux_bridge, "SPOOL", self.spool), mock.patch.object(time, "time", lambda: self.now), \
                mock.patch.object(time, "sleep", side_effect=AssertionError("slept in the event loop")):
            asyncio.run(go())
        return sorted(os.listdir(self.spool))

    def posted(self, channel):
        return [m.content.split("\n")[0] for m in self.host.get_channel(channel).sent]

    def begin(self, channel, pid="p1"):
        self.write(channel, "")
        self.fire(channel, "SessionStart")
        self.fire(channel, "UserPromptSubmit", prompt="go", prompt_id=pid)
        self.assertEqual(self.round(), [])
        self.write(channel, line("user", "go", promptId=pid))

    def test_the_call_waits_for_its_row_and_brings_the_words_before_the_tool(self):
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        before = len(self.posted(5))
        self.assertEqual(len(self.round(0.05)), 1, "its row is not written: left in the spool")
        self.assertEqual((self.bridge.held, len(self.posted(5))), ({"5"}, before))
        self.assertEqual(len(self.round(0.1)), 1, "still not")
        self.write(5, said("I will look first") + call("t1"))
        self.assertEqual(self.round(0.1), [])
        self.assertEqual(self.bridge.held, set())
        new = self.posted(5)[before:]
        self.assertEqual(new[0], ">>> I will look first", "the words, and only then the tool's progress line")
        self.assertEqual(len([p for p in new if "I will look first" in p]), 1)
        self.assertEqual(self.bridge.rt(self.host.get_channel(5)).turn.tools, {"Bash": 1})

    def test_the_other_channels_go_on_and_the_held_ones_order_is_kept(self):
        self.begin(5)
        self.begin(6)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")      # waits for its row
        self.fire(6, "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id="u1")      # its row is there
        self.fire(5, "Stop", last_assistant_message="the reply of five", prompt_id="p1")     # behind the one that waits
        self.fire(6, "Stop", last_assistant_message="the reply of six", prompt_id="p1")
        self.write(6, said("six on the way") + call("u1") + reply("the reply of six"))
        left = self.round(0.05)
        self.assertEqual(len(left), 2, "both of channel 5: the call, and the Stop behind it")
        self.assertIn(">>> six on the way", self.posted(6))
        self.assertTrue(any(p.startswith("the reply of six") for p in self.posted(6)), "the other channel is not kept waiting")
        self.assertFalse(any("the reply of five" in p for p in self.posted(5)), "a turn's end is not posted ahead of its call")
        self.write(5, said("five on the way") + call("t1") + reply("the reply of five"))
        self.assertEqual(self.round(0.1), [])
        five = self.posted(5)
        self.assertLess(five.index(">>> five on the way"), next(i for i, p in enumerate(five) if p.startswith("the reply of five")))

    def test_a_row_that_never_comes_is_waited_for_only_so_long(self):
        """A call that was refused, a row later than was ever measured: handled with what there is. If the words do
        come later, the next event that reads brings them, once."""
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.assertEqual(len(self.round(hooks.HOLD_SECS - 0.2)), 1)
        self.assertEqual(self.round(0.3), [], "long enough")
        self.assertEqual([p for p in self.posted(5) if p.startswith(">>>")], [])
        self.write(5, said("written very late") + call("t1") + reply("the reply"))
        self.fire(5, "Stop", last_assistant_message="the reply", prompt_id="p1")
        self.assertEqual(self.round(0.1), [])
        self.assertEqual([p for p in self.posted(5) if p.startswith(">>>")], [">>> written very late"])

    def test_events_that_waited_while_the_bot_was_away_are_not_held(self):
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.fire(5, "Stop", last_assistant_message="the reply", prompt_id="p1")
        self.assertEqual(self.round(60.0), [], "a minute old: handled at once, in order")
        self.assertTrue(any(p.startswith("the reply") for p in self.posted(5)))

    def test_an_interrupted_tool_call_has_its_words_posted(self):
        """#issues 26: Escape while the tool runs. No PostToolUse, no Stop: the call's event is the last one of the turn,
        and it used to bring the words only when it happened to be handled after the row was written."""
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.assertEqual(len(self.round(0.02)), 1)
        self.write(5, said("about to run something long") + call("t1", "sleep 30"))
        self.assertEqual(self.round(0.1), [])
        self.assertIn(">>> about to run something long", self.posted(5))

    def test_a_question_waits_like_any_call_and_its_second_event_does_not_wait_again(self):
        self.begin(5)
        question = {"question": "Which?", "header": "Pick", "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}
        self.fire(5, "PreToolUse", tool_name="AskUserQuestion", prompt_id="p1", tool_use_id="t1")
        self.fire(5, "Ask", ask_id="77-1", questions=[question], prompt_id="p1", tool_use_id="t1")
        self.assertEqual(len(self.round(0.05)), 2)
        self.write(5, said("one thing before I go on") + call("t1"))
        with mock.patch.object(tmux_bridge.ask, "deliver_to", mock.AsyncMock()):
            self.assertEqual(self.round(0.1), [], "both in the same round: the row was read by the first")
        five = self.posted(5)
        self.assertEqual(len([p for p in five if "one thing before I go on" in p]), 1)
        self.assertLess(five.index(">>> one thing before I go on"), next(i for i, p in enumerate(five) if "Which?" in p))

    def test_calls_sent_at_once_reach_the_spool_out_of_order(self):
        """#code-review-2 on 41092b0, with the real Claude Code: a reply of words and three Reads with no pause between
        the blocks; their three hooks land in the spool in any order (1, 2, 0 …). In 27 of 36 replies the channel
        stood still for the whole limit and the reply was posted twice (once as words on the way). Every order: no
        event is left once the rows are written, the words once, the reply once and not quoted."""
        import itertools
        for order in itertools.permutations(("t0", "t1", "t2")):
            self.setUp()
            self.begin(5)
            for tid in order:
                self.fire(5, "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id=tid)
            self.assertEqual(len(self.round(0.02)), 3, "none of their rows is written: all three wait")
            self.write(5, said("I will read three files") + call("t0") + call("t1") + call("t2"))
            self.assertEqual(self.round(0.1), [], (order, "the rows are there: none is left, whichever comes first"))
            for tid in ("t2", "t1", "t0"):  # the results, in the order the tools end, and the reply: Reads are quick
                self.write(5, line("user", [{"type": "tool_result", "tool_use_id": tid, "content": "x"}], promptId="p1"))
            self.write(5, reply("ANSWER"))
            self.fire(5, "Stop", last_assistant_message="ANSWER", prompt_id="p1")
            self.assertEqual(self.round(0.3), [])
            posted = self.posted(5)
            self.assertEqual([p for p in posted if p.startswith(">>>")], [">>> I will read three files"], order)
            self.assertEqual(len([p for p in posted if p.startswith("ANSWER")]), 1, order)
            self.assertEqual(self.bridge.rt(self.host.get_channel(5)).turn, None)
            self.tearDown()
        self.setUp()

    def test_a_call_whose_row_was_passed_while_it_waited_reads_nothing_when_let_go(self):
        """The same fault without the luck of the rows coming together: one call's event is handled, reading past
        another's row, while that other one is still being handled later, with the reply already written."""
        self.begin(5)
        self.write(5, said("first words") + call("t0") + call("t1"))
        self.fire(5, "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id="t1")
        self.assertEqual(self.round(0.05), [])
        self.write(5, line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}], promptId="p1") + reply("ANSWER"))
        self.fire(5, "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id="t0")  # late, its row long passed
        self.assertEqual(self.round(0.05), [], "not held: its row was read")
        self.assertEqual([p for p in self.posted(5) if p.startswith(">>>")], [">>> first words"], "and it did not read on to the reply")
        self.fire(5, "Stop", last_assistant_message="ANSWER", prompt_id="p1")
        self.round(0.1)
        self.assertEqual(len([p for p in self.posted(5) if "ANSWER" in p]), 1)

    def test_the_rest_of_the_round_keeps_its_pace_while_an_event_waits(self):
        """The pump looks again after 0.1 s while an event waits; subagents' threads and dcpost keep their half second."""
        ticks = []

        async def tick():
            ticks.append(self.now)
        self.bridge.agents.tick = tick
        self.begin(5)
        ticks.clear()
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        for _ in range(10):
            self.round(0.1)
        self.assertEqual(len(ticks), 2, "ten early rounds in a second: two of them did the rest")
        self.write(5, call("t1"))
        self.round(0.1)
        self.round(0.5)
        self.round(0.5)
        self.assertEqual(len(ticks), 5, "nothing waits: every round, as ever")

    def test_after_a_restart_the_calls_of_a_reply_already_written_do_not_wait(self):
        """Code review of 2158420, through the bot's round: the bot comes up (its table empty) and finds in the spool
        the three calls of a reply whose rows are written; while they are handled the results and the reply are
        written. Every order of the three, their own included: none is left in the spool, the reply is posted once and
        not quoted; the words of before the restart are not relayed again."""
        import itertools
        for order in itertools.permutations(("t0", "t1", "t2")):
            self.setUp()
            self.begin(5)
            self.write(5, said("WORDS") + call("t0") + call("t1") + call("t2"))
            self.bridge = tmux_bridge.TmuxBridge(self.host)  # the restart: the channels' state stays, the tables go
            self.bridge.last_reap, self.bridge.streams_checked = 1e18, 1e18

            async def nothing():
                return None
            self.bridge.agents.tick = nothing
            before = len(self.posted(5))
            for tid in order:
                self.fire(5, "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id=tid)
            real = hooks.enrich
            first = []

            def enrich(ev, pos, **kw):
                out = real(ev, pos, **kw)
                if not first:  # while the first is handled: the tools end and the reply is written
                    first.append(1)
                    for tid in ("t2", "t1", "t0"):
                        self.write(5, line("user", [{"type": "tool_result", "tool_use_id": tid, "content": "x"}], promptId="p1"))
                    self.write(5, reply("ANSWER"))
                return out
            with mock.patch.object(hooks, "enrich", enrich):
                self.assertEqual(self.round(0.3), [], (order, "none waits for a row that lies behind"))
            self.fire(5, "Stop", last_assistant_message="ANSWER", prompt_id="p1")
            self.assertEqual(self.round(0.1), [])
            new = self.posted(5)[before:]
            self.assertEqual([p for p in new if p.startswith(">>>")], [], (order, new))
            self.assertEqual(len([p for p in new if p.startswith("ANSWER")]), 1, (order, new))
            self.tearDown()
        self.setUp()

    def restart(self):
        self.bridge = tmux_bridge.TmuxBridge(self.host)  # the channels' state stays, the tables go
        self.bridge.last_reap, self.bridge.streams_checked = 1e18, 1e18

        async def nothing():
            return None
        self.bridge.agents.tick = nothing

    def of_reply(self, mid, *blocks):
        """The rows of one reply as Claude Code writes them: a block a row, each with the reply's id."""
        return "".join(json.dumps({"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [
            {"type": "tool_use", "id": b, "name": "Bash", "input": {"command": "ls"}} if b.startswith("t") else {"type": "text", "text": b}]}},
            ensure_ascii=False) + "\n" for b in blocks)

    def test_step_2_2g_after_a_restart_the_words_ahead_of_a_call_are_posted_whenever_the_event_is_looked_at(self):
        """#code-review-2, 20 runs of a restart in mid-turn: the words said ahead of the first call after the restart
        were posted when its event was handled within 0.08 s of its hook (13 runs) and never when 0.18 s or more
        after it (7 runs): by then Claude Code had written the row, and a reader that begins at the end of the file
        took it for read. Both now, and once."""
        for looked_at, written in ((0.05, "after"), (0.2, "before"), (5.0, "before")):
            self.setUp()
            self.begin(5)
            self.write(5, self.of_reply("m1", "BEFORE THE RESTART", "t1") + line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}], promptId="p1"))
            self.restart()
            before = len(self.posted(5))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t2")
            if written == "before":
                self.write(5, self.of_reply("m2", "AHEAD OF THE SECOND", "t2"))
                self.assertEqual(self.round(looked_at), [], looked_at)
            else:
                self.assertEqual(len(self.round(looked_at)), 1, "the first event of the channel since the restart waits for its row too")
                self.assertEqual(self.bridge.held, {"5"})
                self.write(5, self.of_reply("m2", "AHEAD OF THE SECOND", "t2"))
                self.assertEqual(self.round(0.1), [])
            self.write(5, line("user", [{"type": "tool_result", "tool_use_id": "t2", "content": "x"}], promptId="p1") + reply("ANSWER"))
            self.fire(5, "Stop", last_assistant_message="ANSWER", prompt_id="p1")
            self.assertEqual(self.round(0.1), [])
            new = self.posted(5)[before:]
            self.assertEqual([p for p in new if p.startswith(">>>")], [">>> AHEAD OF THE SECOND"], (looked_at, new))
            self.assertEqual(len([p for p in new if p.startswith("ANSWER")]), 1, (looked_at, new))
            self.tearDown()
        self.setUp()

    def test_step_2_2g_not_twice_when_the_reply_made_a_call_before_this_one(self):
        """The manager's case: a reply with two calls; the first call's event was handled before the restart and
        the words ahead were posted then. The first event after the restart is the second call's: nothing again."""
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.write(5, self.of_reply("m1", "POSTED BEFORE THE RESTART", "t1", "t2"))
        self.assertEqual(self.round(0.1), [])
        self.assertEqual(len([p for p in self.posted(5) if "POSTED BEFORE THE RESTART" in p]), 1)
        self.restart()
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t2")
        self.assertEqual(self.round(0.2), [])
        self.write(5, reply("ANSWER"))
        self.fire(5, "Stop", last_assistant_message="ANSWER", prompt_id="p1")
        self.assertEqual(self.round(0.1), [])
        self.assertEqual(len([p for p in self.posted(5) if "POSTED BEFORE THE RESTART" in p]), 1, self.posted(5))
        self.assertEqual(len([p for p in self.posted(5) if p.startswith("ANSWER")]), 1)

    def test_step_2_2g_three_calls_found_in_the_spool_after_a_restart_in_every_order(self):
        """As the test above this group, with rows that bear their reply's id (as real ones do): the three events
        were handled by nobody. The words ahead come once when the first call's event is the first looked at, and
        not at all otherwise (a later call's event was first: told by the file, an earlier event went before it,
        and it cannot be known that nobody read it). Never twice, in any order; the reply once."""
        import itertools
        for order in itertools.permutations(("t0", "t1", "t2")):
            self.setUp()
            self.begin(5)
            self.write(5, self.of_reply("m1", "WORDS", "t0", "t1", "t2"))
            self.restart()
            before = len(self.posted(5))
            for tid in order:
                self.fire(5, "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id=tid)
            self.assertEqual(self.round(0.3), [], order)
            self.write(5, reply("ANSWER"))
            self.fire(5, "Stop", last_assistant_message="ANSWER", prompt_id="p1")
            self.assertEqual(self.round(0.1), [])
            new = self.posted(5)[before:]
            self.assertEqual([p for p in new if p.startswith(">>>")], [">>> WORDS"] if order[0] == "t0" else [], (order, new))
            self.assertEqual(len([p for p in new if p.startswith("ANSWER")]), 1, (order, new))
            self.tearDown()
        self.setUp()

    def test_an_event_that_is_not_the_channels_own_is_not_waited_for(self):
        """#code-review-2 on bc7582d: a window that is not the channel's any more still names the channel; its tool
        calls waited for their rows (2 s each, when its reply made 24 of them) and the channel's own events stood
        behind them. And a channel the bot does not know got a Runtime. By handle()'s own rules now: such an event
        is let go there, so it waits for nothing here."""
        own = {"socket": "/tmp/x/default", "id": "%2"}
        self.host.channels_state["5"] = {"cwd": "/tmp", "home": {"socket": "/tmp/x/default", "session": "main"}, "pane": dict(own)}
        other = os.path.join(self.tmp.name, "other.jsonl")
        with open(other, "w") as f:
            f.write(line("user", "go", promptId="q1"))
        base = {"channel": "5", "hook_event_name": "PreToolUse", "tool_name": "Bash", "prompt_id": "q1", "tool_use_id": "t9",
                "session_id": "other", "transcript_path": other, "ts": self.now}
        with mock.patch.object(time, "time", lambda: self.now + 0.05):
            self.assertTrue(self.bridge.waits(dict(base, dc_pane={"socket": "/tmp/x/default", "pane": "%2"})), "the channel's own window: waited for")
            self.assertFalse(self.bridge.waits(dict(base, dc_pane={"socket": "/tmp/x/default", "pane": "%1"})), "another window")
            self.bridge.rts["5"].pos[other] = {"at": 0, "id": "q1", "seen": ["q1"], "start": 0, "sure": True, "called": []}
            self.assertFalse(self.bridge.waits(dict(base, dc_pane={"socket": "/tmp/x/default", "pane": "%1"})), "also when the bot has read its file")
            self.host.channels_state["5"] = {"cwd": "/tmp", "archived": {"at": 1}}
            self.assertFalse(self.bridge.waits(dict(base)), "a channel put away")
            before = set(self.bridge.rts)
            self.assertFalse(self.bridge.waits(dict(base, channel="424242")), "a channel the bot does not know")
            self.assertFalse(self.bridge.waits({k: v for k, v in base.items() if k != "channel"}))
            self.assertEqual(set(self.bridge.rts), before, "and no Runtime is made for it")

    def test_the_channels_own_events_do_not_stand_behind_another_windows(self):
        """Through the bot's round, both kinds: the other window's file never read by the bot (it was cleared
        there), and read (it was the channel's conversation once; that kind was held before step 2.2g too). Its tool
        call, whose row is not written, goes straight to handle() and is let go; the channel's own event after it
        is handled in the same round, and nothing of the other window is posted."""
        for read_before in (False, True):
            self.setUp()
            self.host.channels_state["5"] = {"cwd": "/tmp", "home": {"socket": "/tmp/x/default", "session": "main"},
                                             "pane": {"socket": "/tmp/x/default", "id": "%2"}}
            self.begin(5)
            other = os.path.join(self.tmp.name, "other.jsonl")
            with open(other, "w") as f:
                f.write(line("user", "elsewhere", promptId="q1"))
            if read_before:
                self.bridge.rt(self.host.get_channel(5)).pos[other] = {"at": 0, "id": "q1", "seen": ["q1"], "start": 0, "sure": True, "called": []}
            before = len(self.posted(5))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="q1", tool_use_id="x1", session_id="other", transcript_path=other,
                      dc_pane={"socket": "/tmp/x/default", "pane": "%1"})
            self.write(5, said("OWN WORDS") + call("t1"))
            self.fire(5, "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id="t1")
            self.assertEqual(self.round(0.05), [], (read_before, "neither is left in the spool"))
            self.assertEqual(self.bridge.held, set())
            new = self.posted(5)[before:]
            self.assertIn(">>> OWN WORDS", new, (read_before, new))
            self.assertEqual(self.bridge.rt(self.host.get_channel(5)).turn.tools, {"Read": 1}, "the other window's call is not this turn's")
            self.assertEqual(self.host.channels_state["5"].get("session_id"), "s5", "nor its conversation the channel's")
            self.tearDown()
        self.setUp()

    def test_nothing_waits_when_the_words_on_the_way_are_not_posted(self):
        config.INTERIM = False
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.assertEqual(self.round(0.05), [])

    def test_looking_that_fails_does_not_keep_the_event(self):
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        with mock.patch.object(hooks, "awaits", side_effect=OSError("the disk")), mock.patch.object(tmux_bridge.traceback, "print_exc"):
            self.assertEqual(self.round(0.05), [])
        self.assertFalse(os.path.exists(self.spool + "-bad"))


class PumpEnds(Pump):
    """The end of a turn waits for the reply's own row (stage 3b step 2.2f): the footer's context is then the turn's
    own, and a conversation's first turn has one."""

    def footer(self, channel, reply_begins="the reply"):
        return next(m.content for m in self.host.get_channel(channel).sent if m.content.startswith(reply_begins)).split("\n")[-1]

    def test_a_turns_end_is_left_until_its_reply_is_written_and_its_context_is_its_own(self):
        self.begin(5)
        self.begin(6)
        self.fire(5, "Stop", last_assistant_message="the reply of five", prompt_id="p1")
        self.fire(6, "Stop", last_assistant_message="the reply of six", prompt_id="p1")
        self.write(6, reply("the reply of six"))
        self.assertEqual(len(self.round(0.03)), 1, "five's end waits; six's row is there")
        self.assertTrue(any(p.startswith("the reply of six") for p in self.posted(6)), "the other channel is not kept waiting")
        self.assertFalse(any("the reply of five" in p for p in self.posted(5)))
        self.fire(5, "UserPromptSubmit", prompt="and then", prompt_id="p2")  # the owner's next message, at once
        self.assertEqual(len(self.round(0.03)), 2, "it stays behind the end it follows")
        self.write(5, reply("the reply of five"))
        self.assertEqual(self.round(0.05), [])
        self.assertEqual(self.footer(5), "-# 0 秒 · context 0%", "the first turn of a conversation: its context is there")
        self.assertEqual(self.bridge.rt(self.host.get_channel(5)).turn.prompt, "and then", "and the next turn began after it")
        self.assertFalse(os.path.exists(os.path.join(config.STATE, tmux_bridge.LATE_ENDS)))

    def test_a_reply_that_never_comes_costs_the_limit_and_is_counted_and_said_once(self):
        printed = []
        with mock.patch("builtins.print", lambda *a, **k: printed.append(a[0])):
            for pid in ("p1", "p2"):
                self.write(5, "") if pid == "p1" else None
                self.fire(5, "SessionStart") if pid == "p1" else None
                self.fire(5, "UserPromptSubmit", prompt="go", prompt_id=pid)
                self.assertEqual(self.round(1.0), [])
                self.fire(5, "Stop", last_assistant_message="the reply " + pid, prompt_id=pid)
                self.assertEqual(len(self.round(hooks.END_HOLD_SECS - 0.1)), 1, "not yet")
                self.assertEqual(self.round(0.15), [], "the limit: handled with what there is")
                self.assertTrue(self.posted(5)[-1].startswith("the reply " + pid))
        self.assertEqual(len([p for p in printed if "without its reply's row" in p]), 1, "said once while the bot runs")
        with open(os.path.join(config.STATE, tmux_bridge.LATE_ENDS)) as f:
            self.assertEqual(json.load(f)["count"], 2, "and counted")
        self.assertEqual(self.bridge.late_ends, 2)

    def test_the_end_waits_also_when_no_words_on_the_way_are_posted(self):
        """INTERIM off: tool calls are not waited for (their rows bring only words that would not be posted); the
        turn's end is, the footer's context comes from its row."""
        config.INTERIM = False
        self.begin(5)
        self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.assertEqual(self.round(0.03), [], "the call goes at once")
        self.fire(5, "Stop", last_assistant_message="the reply", prompt_id="p1")
        self.assertEqual(len(self.round(0.03)), 1)
        self.write(5, call("t1") + reply("the reply"))
        self.assertEqual(self.round(0.05), [])
        self.assertTrue(self.footer(5).endswith("context 0%"), self.footer(5))

    def test_an_end_that_says_of_no_reply_and_a_failed_request_go_at_once(self):
        self.begin(5)
        self.fire(5, "Stop", prompt_id="p1")
        self.assertEqual(self.round(0.03), [])
        self.fire(5, "UserPromptSubmit", prompt="again", prompt_id="p2")
        self.fire(5, "StopFailure", error="rate_limit", last_assistant_message="API Error", prompt_id="p2")
        self.assertEqual(self.round(0.03), [])
        self.assertTrue(any("這一輪中斷了" in p for p in self.posted(5)))


for inherited in [n for n in dir(Pump) if n.startswith("test_") and n not in PumpEnds.__dict__]:
    setattr(PumpEnds, inherited, None)  # (Pump's world, not its tests a second time)


class Relay(unittest.TestCase):
    """tools/dcremote on another machine: the same, by conversation, and never asleep (that would hold back every
    channel of that machine)."""

    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("dcremote_hold", os.path.join(ROOT, "tools", "dcremote"))
        self.dcremote = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_hold", loader))
        loader.exec_module(self.dcremote)
        self.tmp = tempfile.TemporaryDirectory()
        self.spool = os.path.join(self.tmp.name, "events")
        os.makedirs(self.spool)
        self.pos, self.n, self.now = {}, 0, 2000.0

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.tmp.name, name + ".jsonl")

    def write(self, name, text):
        with open(self.path(name), "a", encoding="utf-8") as f:
            f.write(text)

    def fire(self, name, kind, **kw):
        self.n += 1
        with open(os.path.join(self.spool, "%04d.json" % self.n), "w") as f:
            json.dump(dict({"channel": name, "hook_event_name": kind, "transcript_path": self.path(name), "ts": self.now}, **kw), f)

    def relay(self, later=0.0):
        self.now += later
        out = io.StringIO()
        with mock.patch.object(time, "time", lambda: self.now), mock.patch.object(time, "sleep", side_effect=AssertionError("slept")):
            sent = self.dcremote.relay(self.spool, self.pos, out)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(sent, len(lines))
        return lines

    def test_held_by_conversation_the_others_sent_and_the_words_come_with_the_call(self):
        for name in ("a", "b"):
            self.write(name, "")
            self.fire(name, "UserPromptSubmit", prompt="go", prompt_id="p1")
        self.assertEqual(len(self.relay()), 2)
        for name in ("a", "b"):
            self.write(name, line("user", "go", promptId="p1"))
        self.fire("a", "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.fire("b", "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="u1")
        self.fire("a", "Stop", last_assistant_message="reply a", prompt_id="p1")
        self.write("b", said("b on the way") + call("u1"))
        sent = self.relay(0.05)
        self.assertEqual([(e["channel"], e["hook_event_name"], e.get("texts")) for e in sent], [("b", "PreToolUse", ["b on the way"])])
        self.assertEqual(len(os.listdir(self.spool)), 2, "a's call and the Stop behind it stay in the spool")
        self.assertEqual(self.relay(0.3), [])
        self.write("a", said("a on the way") + call("t1"))
        sent = self.relay(0.3)
        self.assertEqual([(e["hook_event_name"], e.get("texts")) for e in sent], [("PreToolUse", ["a on the way"]), ("Stop", [])])
        self.assertEqual(os.listdir(self.spool), [])

    def test_calls_sent_at_once_out_of_order(self):
        """The relay on another machine, the same fault: every order of three calls of one reply."""
        import itertools
        for order in itertools.permutations(("t0", "t1", "t2")):
            self.setUp()
            self.write("a", "")
            self.fire("a", "UserPromptSubmit", prompt="go", prompt_id="p1")
            self.relay()
            self.write("a", line("user", "go", promptId="p1"))
            for tid in order:
                self.fire("a", "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id=tid)
            self.assertEqual(self.relay(0.02), [], "none of their rows is written")
            self.write("a", said("I will read three files") + call("t0") + call("t1") + call("t2"))
            sent = self.relay(0.3)
            self.assertEqual([e["tool_use_id"] for e in sent], list(order), "all three go, in the order they came")
            self.assertEqual([e["texts"] for e in sent], [["I will read three files"], [], []], order)
            for tid in ("t2", "t1", "t0"):
                self.write("a", line("user", [{"type": "tool_result", "tool_use_id": tid, "content": "x"}], promptId="p1"))
            self.write("a", reply("ANSWER"))
            self.fire("a", "Stop", last_assistant_message="ANSWER", prompt_id="p1")
            sent = self.relay(0.3)
            self.assertEqual([(e["hook_event_name"], e["texts"]) for e in sent], [("Stop", ["ANSWER"])], "the bot drops the reply's own words there")
            self.assertEqual(os.listdir(self.spool), [])
            self.tearDown()
        self.setUp()

    def test_a_relay_just_started_does_not_hold_calls_whose_rows_are_behind(self):
        """The same with the relay's table empty (the stream reconnected): every order."""
        import itertools
        for order in itertools.permutations(("t0", "t1", "t2")):
            self.setUp()
            self.write("a", line("user", "go", promptId="p1") + said("WORDS") + call("t0") + call("t1") + call("t2"))
            for tid in order:
                self.fire("a", "PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id=tid)
            real = self.dcremote.enrich
            first = []

            def enrich(ev, pos, **kw):
                out = real(ev, pos, **kw)
                if not first:
                    first.append(1)
                    for tid in ("t2", "t1", "t0"):
                        self.write("a", line("user", [{"type": "tool_result", "tool_use_id": tid, "content": "x"}], promptId="p1"))
                    self.write("a", reply("ANSWER"))
                return out
            with mock.patch.object(self.dcremote, "enrich", enrich):
                sent = self.relay(0.3)
            self.assertEqual([(e["tool_use_id"], e["texts"]) for e in sent], [(tid, []) for tid in order], "all three at once, none brings the reply")
            self.fire("a", "Stop", last_assistant_message="ANSWER", prompt_id="p1")
            self.assertEqual([(e["hook_event_name"], e["texts"]) for e in self.relay(0.1)], [("Stop", ["ANSWER"])])
            self.assertEqual(os.listdir(self.spool), [])
            self.tearDown()
        self.setUp()

    def test_not_for_longer_than_the_limit_and_not_for_old_events(self):
        self.write("a", "")
        self.fire("a", "UserPromptSubmit", prompt="go", prompt_id="p1")
        self.relay()
        self.fire("a", "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        self.assertEqual(self.relay(self.dcremote.cc_hooks.HOLD_SECS - 0.1), [])
        self.assertEqual([e["hook_event_name"] for e in self.relay(0.2)], ["PreToolUse"])
        self.fire("a", "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t2")
        self.assertEqual(len(self.relay(30.0)), 1, "the line was down for half a minute: sent at once")

    def test_looking_that_fails_sends_the_event(self):
        self.write("a", "")
        self.fire("a", "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
        with mock.patch.object(self.dcremote.cc_hooks, "awaits", side_effect=ValueError("odd")):
            self.assertEqual(len(self.relay()), 1)


    def test_a_turns_end_waits_for_its_reply_there_too_and_the_stream_looks_again_soon(self):
        """The same rule where the transcript is (the connector's awaits()): the Stop stays in that machine's spool
        with what follows it; the event that goes brings the turn's own context."""
        self.write("a", "")
        self.fire("a", "UserPromptSubmit", prompt="go", prompt_id="p1")
        self.assertEqual(len(self.relay()), 1)
        self.assertFalse(self.dcremote.relay.held)
        self.write("a", line("user", "go", promptId="p1"))
        self.fire("a", "Stop", last_assistant_message="reply a", prompt_id="p1")
        self.fire("a", "UserPromptSubmit", prompt="next", prompt_id="p2")
        self.fire("b", "UserPromptSubmit", prompt="other", prompt_id="q1")
        sent = self.relay(0.03)
        self.assertEqual([e["channel"] for e in sent], ["b"], "the other conversation goes on")
        self.assertTrue(self.dcremote.relay.held, "stream() comes back in 0.1 s, not 0.3")
        self.write("a", reply("reply a"))
        sent = self.relay(0.1)
        self.assertEqual([(e["hook_event_name"], e.get("context")) for e in sent], [("Stop", [3, ""]), ("UserPromptSubmit", None)])
        self.assertFalse(self.dcremote.relay.held)
        self.assertEqual(os.listdir(self.spool), [])

    def test_a_reply_that_never_comes_goes_after_the_limit(self):
        self.write("a", line("user", "go", promptId="p1"))
        self.fire("a", "UserPromptSubmit", prompt="go", prompt_id="p1")
        self.relay()
        self.fire("a", "Stop", last_assistant_message="reply a", prompt_id="p1")
        self.assertEqual(self.relay(hooks.END_HOLD_SECS - 0.1), [])
        self.assertEqual([e["hook_event_name"] for e in self.relay(0.15)], ["Stop"])

    def test_the_streams_pace(self):
        with open(os.path.join(ROOT, "tools", "dcremote"), encoding="utf-8") as f:
            self.assertIn('time.sleep(0.1 if getattr(relay, "held", False) else 0.3)', f.read())


if __name__ == "__main__":
    unittest.main()
