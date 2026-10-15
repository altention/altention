"""An event of this machine gets what tools/dcremote adds to one of another, from the same function of the connector
(hooks.enrich, stage 3b step 1.5), inside handle() once the event is known to be the channel's. The whole turns are
held by test_whole_turn on both roads, the rules of turns by the connector's own tests (test_hooks, test_turns: step
1.6a); here what the bot does with them: the turn's place for 📋 as the last event told it, a failure of the reading
that does not lose the event, nothing asleep in the event loop, the words said before a question above it."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import hooks, transcript
from altention.tests.connectors.claude_code.test_hooks import line, said
from bot import ask, config, process, tmux_bridge
from tests.test_turn_end import Ch, Host
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

USAGE = {"input_tokens": 100000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


class LocalEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM)
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM = 42, True
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.rt = self.bridge.rt(self.ch)
        self.path = os.path.join(self.tmp.name, "s1.jsonl")
        self.write("")

    def tearDown(self):
        ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM = self.saved
        self.tmp.cleanup()

    def write(self, text, path=None):
        with open(path or self.path, "a", encoding="utf-8") as f:
            f.write(text)

    def size(self, path=None):
        return os.path.getsize(path or self.path)

    def ev(self, kind, **kw):
        return dict({"channel": "5", "hook_event_name": kind, "session_id": "abc", "transcript_path": self.path}, **kw)

    def handle(self, *evs):
        async def go():
            for ev in evs:
                await self.bridge.handle(ev)
            if self.rt.typing is not None:
                self.rt.typing.cancel()  # the "typing…" watcher of a running turn: not this loop's to finish
        asyncio.run(go())
        return evs[-1]

    def row(self, pid, text):
        """The row of something said to Claude in the turn `pid`."""
        return line("user", text, promptId=pid)

    def pieces(self, reply):
        """What 📋 on this reply shows (process.pieces_of: the record, the turn's own id kept beside it, the end found now)."""
        st = self.bridge.host.channels_state["5"]
        path, start, end, _ = st["turns"][str(reply.id)]
        self.assertEqual((path, end), (self.path, None), "the end is looked for when 📋 is asked")
        return process.pieces_of(st, reply.id)

    def quotes(self):
        return [m.content for m in self.ch.sent if m.content.startswith(">>> ")]

    def test_a_turn_what_was_said_the_context_and_where_it_lies(self):
        self.handle(self.ev("SessionStart"))
        self.write(said("an earlier turn's reply, written late"))
        self.handle(self.ev("UserPromptSubmit", prompt="do it", prompt_id="p1"))
        start = self.size()
        self.write(self.row("p1", "do it") + said("looking first"))
        pre = self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1"))
        self.assertEqual(self.quotes(), [">>> looking first"])
        self.assertEqual((pre["texts"], pre["dc_turn_start"], pre["dc_turn_sure"]), (["looking first"], start, True))
        stop = self.handle(self.ev("Stop", last_assistant_message="all done", prompt_id="p1"))
        self.write(line("assistant", [{"type": "text", "text": "all done"}]).replace(
            '"role":"assistant"', '"role":"assistant","model":"claude-haiku-4-5","usage":%s' % json.dumps(USAGE)))  # after its hook
        self.assertEqual(self.quotes(), [">>> looking first"])
        self.assertIsNone(stop["context"], "the reply's own row is written after the Stop hook: the context is the one before")
        reply = self.ch.sent[-1]
        self.assertTrue(reply.content.startswith("all done\n-# Bash×1"), reply.content)
        self.assertEqual(self.pieces(reply), ["🧑 **你**\ndo it", "💬 looking first", "💬 all done"],
                         "📋: from the turn's own row, with its own reply and none of the turn before")
        self.handle(self.ev("UserPromptSubmit", prompt="next", prompt_id="p2"))
        self.write(self.row("p2", "next") + said("of the next turn"))
        self.assertEqual(self.pieces(reply), ["🧑 **你**\ndo it", "💬 looking first", "💬 all done"], "and it ends where the next begins")

    def test_the_turns_place_is_what_the_last_event_told(self):
        """A turn's own first row is written after its first hook: where it begins is first only where the reading
        stood, and mended by a later event. A turn can also begin with a tool call (a queued message goes in with no
        UserPromptSubmit of its own)."""
        self.handle(self.ev("SessionStart"))
        self.write(said("the last reply of the turn before"))
        self.handle(self.ev("PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id="t1"))
        self.assertEqual((self.rt.turn.src, self.rt.where), ((self.path, 0), (self.path, 0)), "for now")
        own = self.size()
        self.write(self.row("p1", "queued, then sent") + said("on it"))
        self.handle(self.ev("PreToolUse", tool_name="Read", prompt_id="p1", tool_use_id="t2"))
        self.assertEqual(self.rt.turn.src, (self.path, own), "mended: the turn's own row")
        self.assertEqual(self.quotes(), [">>> on it"], "and what lay before that row was not this turn's")
        self.handle(self.ev("Stop", last_assistant_message="done", prompt_id="p1"))
        self.assertEqual(self.bridge.host.channels_state["5"]["turns"][str(self.ch.sent[-1].id)][:3], [self.path, own, None])

    def test_another_transcript_and_back(self):
        """/clear, !resume: the channel's conversation moves to another file and may come back. Each file has its
        own record; a turn is told by its id in either."""
        other = os.path.join(self.tmp.name, "s2.jsonl")
        self.handle(self.ev("UserPromptSubmit", prompt="one", prompt_id="p1"))
        self.write(self.row("p1", "one") + said("said in the first, never relayed"))
        self.write(said("already there in the second"), other)
        self.handle(self.ev("SessionStart", transcript_path=other, session_id="def"))
        self.write(self.row("p2", "two") + said("in the second"), other)
        self.handle(self.ev("PreToolUse", tool_name="Bash", transcript_path=other, session_id="def", prompt_id="p2", tool_use_id="t1"))
        self.assertEqual(self.quotes(), [">>> in the second"], "from the second file's end on, when it was first seen")
        self.assertEqual(self.rt.where[0], other)
        self.handle(self.ev("UserPromptSubmit", prompt="three", prompt_id="p3"))  # back in the first
        self.write(self.row("p3", "three") + said("back in the first"))
        self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p3", tool_use_id="t2"))
        self.assertEqual(self.quotes(), [">>> in the second", ">>> back in the first"], "not what the turn before left unread there")
        self.assertEqual(sorted(self.rt.pos), sorted([self.path, other]))

    def test_a_message_sent_meanwhile_keeps_what_was_said_before_it(self):
        self.handle(self.ev("UserPromptSubmit", prompt="one", prompt_id="p1"))
        self.write(self.row("p1", "one") + said("said before the second message"))
        self.handle(self.ev("UserPromptSubmit", prompt="two", prompt_id="p1"),  # the running turn's id, as measured
                    self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1"))
        self.assertEqual(self.quotes(), [">>> said before the second message"])

    def test_a_turn_that_ended_with_no_event_leaves_nothing_for_the_next(self):
        """An interrupt: no Stop. The next prompt's id is the only border, whether or not the bot has noticed that
        the turn is over (rt.turn is still set here)."""
        self.handle(self.ev("UserPromptSubmit", prompt="one", prompt_id="p1"))
        self.write(self.row("p1", "one") + said("said, then the owner pressed Esc"))
        self.handle(self.ev("UserPromptSubmit", prompt="two", prompt_id="p2"))
        self.write(self.row("p2", "two") + said("of the new turn"))
        self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p2", tool_use_id="t1"))
        self.assertEqual(self.quotes(), [">>> of the new turn"])
        self.assertEqual(self.rt.turn.src[1], self.size() - len((self.row("p2", "two") + said("of the new turn")).encode()),
                         "and 📋 of the turn still marked as running moves to the new one's row")

    QUESTION = {"question": "Which one?", "header": "Pick", "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}

    def asked(self, **kw):
        """What tools/dchook --ask (or --plan) spools: since this step with Claude Code's transcript_path."""
        return self.ev(kw.pop("kind", "Ask"), ask_id="77-%d" % len(self.ch.sent), questions=[self.QUESTION], plan="1. do it", **kw)

    def test_what_was_said_before_a_question_stands_above_it(self):
        self.handle(self.ev("UserPromptSubmit", prompt="do it"))
        self.write(said("before I go on, one thing"))
        with mock.patch.object(tmux_bridge.ask, "deliver_to", mock.AsyncMock()):
            self.handle(self.asked())
        said_at = [i for i, m in enumerate(self.ch.sent) if m.content == ">>> before I go on, one thing"]
        asked_at = [i for i, m in enumerate(self.ch.sent) if "Which one?" in m.content]
        self.assertEqual((len(said_at), len(asked_at)), (1, 1), [m.content for m in self.ch.sent])
        self.assertLess(said_at[0], asked_at[0])
        self.write(said("and the plan is ready"))
        with mock.patch.object(tmux_bridge.ask, "deliver_to", mock.AsyncMock()):
            self.handle(self.asked(kind="Plan"))
        contents = [m.content for m in self.ch.sent]
        self.assertLess(contents.index(">>> and the plan is ready"), max(i for i, c in enumerate(contents) if "1. do it" in c))

    def test_on_another_machine_too(self):
        """There tools/dcremote reads it (the same enrich; the connector's own tests hold that an Ask event that
        names its transcript gets `texts`), and the bot posts what the event brings."""
        with mock.patch.object(tmux_bridge.ask, "deliver_to", mock.AsyncMock()):
            self.handle(self.asked(host="lap", texts=["said over there, before asking"], transcript_path="/over/there.jsonl"))
        contents = [m.content for m in self.ch.sent]
        self.assertLess(contents.index(">>> said over there, before asking"), max(i for i, c in enumerate(contents) if "Which one?" in c))
        self.assertEqual(self.rt.pos, {})

    def test_a_question_that_names_no_transcript_reads_nothing(self):
        """An event spooled by the dchook of before this step (the moment of a deploy), or by a Claude Code that
        sends no transcript_path: the question is asked all the same."""
        self.handle(self.ev("UserPromptSubmit", prompt="do it"))
        self.write(said("lost for this one question"))
        ev = self.asked()
        del ev["transcript_path"]
        with mock.patch.object(tmux_bridge.ask, "deliver_to", mock.AsyncMock()):
            self.handle(ev)
        self.assertEqual(self.quotes(), [])
        self.assertTrue(any("Which one?" in m.content for m in self.ch.sent))
        self.handle(self.ev("PreToolUse", tool_name="Bash"))
        self.assertEqual(self.quotes(), [">>> lost for this one question"], "not lost: the next event that reads brings it")

    def test_claude_codes_own_fields_stay_as_they_came(self):
        """The real payloads of 2.1.296 (the connector's samples), each through fill(): nothing of Claude Code's
        is changed or dropped, and what is added is only what the connector says it adds."""
        folder = os.path.join(os.path.dirname(os.path.abspath(hooks.__file__)), "..", "..", "tests", "connectors", "claude_code",
                              "hook_payloads", "2.1.296")
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
        self.assertGreaterEqual(len(names), 8)
        for name in names:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                payload = json.load(f)
            ev = dict(payload, transcript_path=self.path, channel="5")
            before = json.loads(json.dumps(ev))
            self.bridge.fill(self.rt, ev)
            self.assertEqual({k: ev[k] for k in before}, before, name)
            self.assertLessEqual(set(ev) - set(before), set(hooks.ADDED), name)
            self.assertIn("dc_turn_start", ev, name)

    def test_recorded_runs_through_the_bot_each_reply_leads_to_its_own_turn(self):
        """The runs of the real Claude Code (the connector's turns/), their events through handle() in three
        timings: 📋 on every reply shows that turn and no other: it ends with the reply itself (#issues 22: the
        reply's row is written after the Stop hook and used to fall into the next turn's) and, where the turn began
        with something the owner said, begins with it (#issues 18)."""
        from altention.tests.connectors.claude_code import replay
        saved = config.AGENT_THREADS
        config.AGENT_THREADS = False  # a subagent's events: taken by agents.handle, shown nowhere here
        self.addCleanup(setattr, config, "AGENT_THREADS", saved)
        checked = began = 0
        for version in replay.VERSIONS:
            for name in replay.names(version):
                for timing in replay.TIMINGS:
                    self.setUp()
                    replies = []

                    def handle(ev):
                        ev["channel"] = "5"
                        before = len(self.ch.sent)
                        self.handle(ev)
                        if ev["hook_event_name"] == "Stop" and not ev.get("agent_id"):
                            replies.append((ev, self.ch.sent[-1], [m.content for m in self.ch.sent[before:]]))
                    with mock.patch.object(tmux_bridge.TmuxBridge, "failed", mock.AsyncMock()):
                        replay.play(replay.load(version, name), self.path, handle, timing)
                    finals = [(ev.get("last_assistant_message") or "").strip() for ev, _, _ in replies]
                    self.assertGreaterEqual(len(replies), 2, (version, name))
                    for ev, reply, _posted in replies:
                        final = (ev.get("last_assistant_message") or "").strip()
                        where = (version, name, timing, ev["prompt_id"][:8])
                        self.assertIn(str(reply.id), self.bridge.host.channels_state["5"].get("turns", {}), (where, _posted))
                        pieces = self.pieces(reply)
                        self.assertEqual(pieces[-1], "💬 " + final, where)
                        said = [p[2:].strip() for p in pieces if p.startswith("💬 ")]
                        self.assertEqual([t for t in said[:-1] if t in finals and t != final], [], where)
                        own = next(row for _, _, row in transcript.rows(self.path) if transcript.asked_as(row) == ev["prompt_id"])
                        words = own["message"]["content"]
                        if isinstance(words, str) and not words.lstrip().startswith("<"):  # the owner's own message
                            self.assertEqual(pieces[0], "🧑 **你**\n" + words.strip(), where)
                            began += 1
                        checked += 1
                    quoted = [m.content[4:] for m in self.ch.sent if m.content.startswith(">>> ")]
                    self.assertEqual(sorted(quoted), sorted(set(quoted)), (version, name, timing, "each word on the way once"))
                    self.assertEqual([q for q in quoted if q in finals], [], (version, name, timing, "no reply quoted as a word on the way"))
                    self.tearDown()
        self.setUp()  # for the tearDown unittest still owes
        self.assertGreaterEqual(checked, 60)
        self.assertGreaterEqual(began, 45, "turns that begin with the owner's message")

    def restart(self):
        """What a restart of the bot leaves: the channel's state, and nothing of what was in memory."""
        state = self.bridge.host.channels_state
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.bridge.host.channels_state = state
        self.rt = self.bridge.rt(self.ch)

    def test_a_restart_in_mid_turn_and_the_reply_leads_to_its_own_turn(self):
        """Every deploy does this to the turns that are running. Before stage 3b such a reply had no 📋 at all; in
        the first version of step 1.6a it had one that showed the NEXT turn (#code-review-2, #code-review: the
        turn's beginning was put at the end of the file as it was after the restart, past the last row under the
        turn's own id). Both ways the turn can go on after the restart."""
        for more_after in (False, True):
            self.setUp()
            self.handle(self.ev("SessionStart"))
            self.handle(self.ev("UserPromptSubmit", prompt="the first turn", prompt_id="p1"))
            self.write(self.row("p1", "the first turn") + said("before the restart")
                       + line("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sleep 8"}}]))
            self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1"))
            self.assertEqual(self.quotes(), [">>> before the restart"])
            self.restart()
            self.write(line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "slept"}], promptId="p1"))
            if more_after:  # the hook first, then its rows, as Claude Code does (rows already written when the bot
                # comes back are past the end it starts from: what was said while it was away is not relayed, as ever)
                self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t2"))
                self.write(said("after the restart") + line("assistant", [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}])
                           + line("user", [{"type": "tool_result", "tool_use_id": "t2", "content": "a"}], promptId="p1"))
            self.handle(self.ev("Stop", last_assistant_message="final of the first", prompt_id="p1"))
            self.write(said("final of the first"))
            first = self.ch.sent[-1]
            self.handle(self.ev("UserPromptSubmit", prompt="the next turn", prompt_id="p2"))
            self.write(self.row("p2", "the next turn") + said("N"))
            self.handle(self.ev("Stop", last_assistant_message="final of the next", prompt_id="p2"))
            self.write(said("final of the next"))
            second = self.ch.sent[-1]
            self.handle(self.ev("UserPromptSubmit", prompt="a third", prompt_id="p3"))
            self.write(self.row("p3", "a third"))
            self.assertEqual(self.quotes(), [">>> before the restart"] + ([">>> after the restart"] if more_after else []) + [">>> N"],
                             "nothing relayed before the restart is relayed again (N: the next turn's own word on the way)")
            pieces = self.pieces(first)
            self.assertEqual((pieces[0], pieces[1], pieces[-1]), ("🧑 **你**\nthe first turn", "💬 before the restart", "💬 final of the first"), more_after)
            self.assertFalse(any("next" in p or p == "💬 N" for p in pieces), pieces)
            self.assertEqual(self.pieces(second), ["🧑 **你**\nthe next turn", "💬 N", "💬 final of the next"])
            self.tearDown()
        self.setUp()

    CALL = staticmethod(lambda tid, cmd: line("assistant", [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]))
    DONE = staticmethod(lambda pid, tid: line("user", [{"type": "tool_result", "tool_use_id": tid, "content": "done"}], promptId=pid))

    def around_a_restart(self, when, order):
        """One case of the table: a turn "p1" around a restart of the bot, then a turn "p2" and the beginning of
        "p3". `when`: "idle" (the restart, then p1 begins), "mid" (p1 is running a tool; after the restart another
        call, then its end), "stop" (after the restart only p1's Stop is left). `order`: "event first" (each event
        after the restart is handled before its rows are written, as when the bot is quick) or "rows first".
        Returns (what 📋 on p1's reply shows or None, what 📋 on p2's shows, the quotes posted)."""
        def step(rows, *evs):
            if order == "rows first":
                self.write(rows)
                self.handle(*evs)
            else:
                self.handle(*evs)
                self.write(rows)
        ask1, ask2 = self.row("p1", "the first turn"), self.row("p2", "the next turn")
        self.write(self.row("p0", "long before") + said("an old reply"))
        self.handle(self.ev("SessionStart"))
        if when == "idle":
            self.restart()
            step(ask1, self.ev("UserPromptSubmit", prompt="the first turn", prompt_id="p1"))
            step(said("on the way") + self.CALL("t1", "ls"), self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1"))
            self.write(self.DONE("p1", "t1"))
        else:
            self.handle(self.ev("UserPromptSubmit", prompt="the first turn", prompt_id="p1"))
            self.write(ask1)
            self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1"))
            self.write(said("on the way") + self.CALL("t1", "sleep 8"))
            self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1b"))  # a second call: it
            self.write(self.CALL("t1b", "true") + self.DONE("p1", "t1b"))  # brings "on the way", relayed before the restart
            self.write(self.DONE("p1", "t1"))
            self.restart()
            if when == "mid":
                step(said("after the restart") + self.CALL("t2", "ls") + self.DONE("p1", "t2"),
                     self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t2"))
        step(said("final of the first"), self.ev("Stop", last_assistant_message="final of the first", prompt_id="p1"))
        first = self.ch.sent[-1]
        step(ask2, self.ev("UserPromptSubmit", prompt="the next turn", prompt_id="p2"))
        step(said("N") + self.CALL("t3", "ls") + self.DONE("p2", "t3"), self.ev("PreToolUse", tool_name="Bash", prompt_id="p2", tool_use_id="t3"))
        step(said("final of the next"), self.ev("Stop", last_assistant_message="final of the next", prompt_id="p2"))
        second = self.ch.sent[-1]
        step(self.row("p3", "a third"), self.ev("UserPromptSubmit", prompt="a third", prompt_id="p3"))
        st = self.bridge.host.channels_state["5"]
        return process.pieces_of(st, first.id), process.pieces_of(st, second.id), self.quotes()

    def test_around_a_restart_a_record_is_the_turns_own_or_there_is_none(self):
        """Every deploy restarts the bot under the channels: idle ones (their next turn is the first the new process
        sees of the file), and ones in a turn. Six cases: when the restart falls × whether the bot gets to an event
        before or after its rows are written. In each: 📋 on the reply of the turn around the restart shows that turn,
        whole (from the owner's message to its own reply) and nothing of another; so does the next turn's; nothing
        relayed before the restart is relayed again."""
        for when in ("idle", "mid", "stop"):
            for order in ("event first", "rows first"):
                self.setUp()
                first, second, quotes = self.around_a_restart(when, order)
                where = (when, order)
                self.assertIsNotNone(first, where)
                self.assertEqual((first[0], first[-1]), ("🧑 **你**\nthe first turn", "💬 final of the first"), where)
                self.assertIn("💬 on the way", first, where)
                self.assertEqual([p for p in first if "next" in p or p == "💬 N" or "old reply" in p or "long before" in p], [], where)
                self.assertEqual((second[0], second[1], second[-1]), ("🧑 **你**\nthe next turn", "💬 N", "💬 final of the next"), where)
                self.assertEqual([p for p in second if "first" in p or "third" in p], [], where)
                self.assertEqual(quotes.count(">>> on the way"), 1, where)
                self.assertEqual(len(quotes), len(set(quotes)), where)
                self.tearDown()
        self.setUp()

    def test_a_conversation_attached_in_the_middle_of_a_long_turn(self):
        """The turn's first row lies further back than is looked through: its beginning is not known, to its end. No
        record for its reply (as before stage 3b), and the turn after has its own."""
        self.write(self.row("p1", "long ago") + said("x" * 2000) + self.CALL("t1", "ls") + self.DONE("p1", "t1"))
        real = transcript.find_begin
        with mock.patch.object(transcript, "find_begin", lambda path, pid: real(path, pid, tail_bytes=300)):
            self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t2"))
            self.write(said("said after the attach") + self.CALL("t2", "ls") + self.DONE("p1", "t2"))
            self.handle(self.ev("Stop", last_assistant_message="final", prompt_id="p1"))
            self.write(said("final"))
        self.assertEqual(self.quotes(), [">>> said after the attach"])
        st = self.bridge.host.channels_state["5"]
        self.assertEqual(st.get("turns") or {}, {}, "not a record that begins in the middle of the turn")
        self.handle(self.ev("UserPromptSubmit", prompt="next", prompt_id="p2"))
        self.write(self.row("p2", "next"))
        self.handle(self.ev("Stop", last_assistant_message="final two", prompt_id="p2"))
        self.write(said("final two"))
        self.assertEqual(self.pieces(self.ch.sent[-1]), ["🧑 **你**\nnext", "💬 final two"])

    def test_a_turn_whose_beginning_is_not_known_gets_no_record(self):
        """Met midway and its first row not found (too far back in a very long turn): as before stage 3b, 📋 on
        its reply says nothing was recorded. Never a record that leads into another turn."""
        self.write(self.row("p1", "long ago") + said("x"))
        with mock.patch.object(transcript, "find_begin", return_value=None):
            self.handle(self.ev("Stop", last_assistant_message="final", prompt_id="p1"))
        self.assertTrue(self.ch.sent[-1].content.startswith("final"))
        self.assertEqual(self.bridge.host.channels_state["5"].get("turns") or {}, {})
        self.handle(self.ev("UserPromptSubmit", prompt="next", prompt_id="p2"))
        self.write(self.row("p2", "next"))
        self.handle(self.ev("Stop", last_assistant_message="final two", prompt_id="p2"))
        self.write(said("final two"))
        self.assertEqual(self.pieces(self.ch.sent[-1]), ["🧑 **你**\nnext", "💬 final two"])

    def test_a_claude_code_that_sends_no_prompt_id_keeps_the_end_it_had(self):
        self.handle(self.ev("UserPromptSubmit", prompt="one"))
        self.write(line("user", "one") + said("on the way"))
        self.handle(self.ev("Stop", last_assistant_message="final"))
        rec = self.bridge.host.channels_state["5"]["turns"][str(self.ch.sent[-1].id)]
        self.assertEqual(rec[:3], [self.path, 0, self.size()], "no ids to find the end by: the size of the file at the Stop, as it was")

    def test_reading_that_fails_does_not_lose_the_event(self):
        self.handle(self.ev("UserPromptSubmit", prompt="do it"))
        with mock.patch.object(hooks, "enrich", side_effect=ValueError("a line no reader expected")), \
                mock.patch.object(tmux_bridge.traceback, "print_exc"):
            stop = self.handle(self.ev("Stop", last_assistant_message="the reply"))
        self.assertEqual(stop["relay_error"], "ValueError: a line no reader expected")
        self.assertTrue(self.ch.sent[-1].content.startswith("the reply"))
        self.assertFalse(self.bridge.host.channels_state["5"]["busy"], "the turn ended")

    def test_through_the_pump_such_an_event_is_done_not_set_aside(self):
        spool = os.path.join(self.tmp.name, "events")
        os.makedirs(spool)
        with open(os.path.join(spool, "01.json"), "w") as f:
            json.dump(self.ev("Stop", last_assistant_message="the reply"), f)
        self.bridge.last_reap, self.bridge.streams_checked = 1e18, 1e18

        async def nothing():
            return None
        self.bridge.agents.tick = nothing
        with mock.patch.object(tmux_bridge, "SPOOL", spool), mock.patch.object(tmux_bridge.traceback, "print_exc"), \
                mock.patch.object(hooks, "enrich", side_effect=OSError("the disk")):
            asyncio.run(self.bridge.pump_once())
        self.assertEqual(os.listdir(spool), [])
        self.assertFalse(os.path.exists(spool + "-bad"), "an event whose reading failed is not a bad event")
        self.assertTrue(self.ch.sent[-1].content.startswith("the reply"))

    def test_a_subagents_files_are_not_looked_for_here(self):
        """tools/dcremote waits for them asleep, up to 1.5 s: in the bot that would stop every channel."""
        with mock.patch.object(time, "sleep", side_effect=AssertionError("slept in the event loop")):
            t0 = time.time()
            ev = self.ev("SubagentStart", agent_id="not-written-yet", agent_type="Explore")
            self.bridge.fill(self.rt, ev)
            self.assertLess(time.time() - t0, 0.1)
        self.assertEqual([k for k in ev if k in ("agent_meta", "agent_run", "agent_prompt")], [], "agents.py reads them, its own way")

    def test_an_event_of_another_machine_is_left_as_it_came(self):
        ev = self.ev("PreToolUse", host="lap", texts=["read over there"])
        before = dict(ev)
        self.bridge.fill(self.rt, ev)
        self.assertEqual(ev, before)
        self.assertEqual((self.rt.pos, self.rt.where), ({}, None), "the bot never opens the other machine's file")

    def test_with_the_words_on_the_way_turned_off_each_reply_still_leads_to_its_own_turn(self):
        """INTERIM=0: nothing said on the way is posted, and 📋 on each reply is that turn (in the first version of
        step 1.6a every reply led to where the file was first met: code review of 120c38a)."""
        config.INTERIM = False
        self.handle(self.ev("SessionStart"))
        replies = []
        for pid in ("p1", "p2", "p3"):
            self.handle(self.ev("UserPromptSubmit", prompt="prompt " + pid, prompt_id=pid))
            self.write(self.row(pid, "prompt " + pid) + said("on the way in " + pid))
            ev = self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id=pid, tool_use_id="t-" + pid))
            self.assertNotIn("texts", ev)
            self.handle(self.ev("Stop", last_assistant_message="final " + pid, prompt_id=pid))
            self.write(said("final " + pid))
            replies.append(self.ch.sent[-1])
        self.assertEqual(self.quotes(), [])
        for pid, reply in zip(("p1", "p2", "p3"), replies):
            self.assertEqual(self.pieces(reply), ["🧑 **你**\nprompt " + pid, "💬 on the way in " + pid, "💬 final " + pid], pid)

    def test_a_turn_with_no_message_row_of_its_id_loses_nothing(self):
        """The case the code review of 120c38a ran: a turn whose events carry "bg-1" and whose only row under that
        id is a tool's result. Its words come (at its end at the latest) and 📋 has all four pieces."""
        self.handle(self.ev("SessionStart"))
        self.handle(self.ev("PreToolUse", tool_name="Bash", prompt_id="bg-1", tool_use_id="t1"))
        self.write(said("a background task ended")
                   + line("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls", "description": "List"}}])
                   + line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "a.txt"}], promptId="bg-1"))
        self.handle(self.ev("Stop", last_assistant_message="the last reply", prompt_id="bg-1"))
        self.write(said("the last reply"))
        self.assertEqual(self.quotes(), [">>> a background task ended"])
        pieces = self.pieces(self.ch.sent[-1])
        self.assertEqual([p.split("\n")[0] for p in pieces], ["💬 a background task ended", "🔧 **Bash**（List）", "```", "💬 the last reply"])


if __name__ == "__main__":
    unittest.main()
