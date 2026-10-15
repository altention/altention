"""What is read of a conversation Claude Code moved to a background process (stage 3b step 2.4d): its new transcript
begins with a copy of the old one. Held against recorded runs of the real Claude Code (forks/, README there), in two
timings of the one who handles the events, and with the reader started anew at every point of the run (a restart).
Before this, every turn from before the move was read as said in the first turn after it, and posted again."""
import datetime
import json
import os
import tempfile
import unittest

from altention.connectors.claude_code import hooks, transcript

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forks")
VERSIONS = ("2.1.296", "2.1.288")
TIMINGS = ("live", "late", 0.04, 0.08, 0.2, 0.6)  # each event handled when its hook is called; or just before the next
# hook is called; or so many seconds after its hook was called (the bot looks into its spool every half second, the
# relay about as often). 0.04 and 0.08 fall where #code-review-2's runs went wrong (two of five, an attached pane's
# conversation moved in mid-turn): the new file is given three rows that bear no time 0.02 to 0.03 s after the
# fork's SessionStart is called, and the rest, the copy, about 0.1 s after those.


def names(version):
    return sorted(n[:-len(".json")] for n in os.listdir(os.path.join(HERE, version)) if n.endswith(".json"))


def load(version, name):
    with open(os.path.join(HERE, version, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def play(run, folder, handle, timing="live", forget_before=None):
    """Write the run's rows into two files of `folder` and hand its events to handle(event), each with the time
    its hook was called as "ts" (as tools/dchook stamps it). Returns (events as handled, {"old": path, "new": path}).
    `forget_before`: the number of the event before which handle.pos is emptied (the reader was restarted)."""
    paths = {"old": os.path.join(folder, run["old"] + ".jsonl"), "new": os.path.join(folder, run["new"] + ".jsonl")}
    waiting, done = [], []

    def flush():
        while waiting:
            ev = waiting.pop(0)
            if forget_before is not None and len(done) == forget_before:
                handle.pos.clear()
            handle(ev)
            done.append(ev)
    lag = timing if isinstance(timing, float) else None
    for step in run["steps"]:
        if lag is not None:
            while waiting and waiting[0]["ts"] + lag <= step["t"]:
                held = waiting[1:]
                del waiting[1:]
                flush()
                waiting.extend(held)
        if "write" in step:
            with open(paths[step["into"]], "a", encoding="utf-8") as f:
                f.write(json.dumps(step["write"], ensure_ascii=False, separators=(",", ":")) + "\n")
        else:
            if timing == "late":
                flush()
            ev = json.loads(json.dumps(step["hook"]).replace("{old}", paths["old"]).replace("{new}", paths["new"]))
            waiting.append(dict(ev, ts=step["t"]))
            if timing == "live":
                flush()
    flush()
    return done, paths


def played(version, name, timing, forget_before=None):
    tmp = tempfile.TemporaryDirectory()
    pos = {}

    def handle(ev):
        hooks.enrich(ev, pos)
    handle.pos = pos
    events, paths = play(load(version, name), tmp.name, handle, timing, forget_before)
    return events, paths, tmp


def stamp(row):
    return datetime.datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp()


def moved_at(run):
    return next(step["t"] for step in run["steps"] if "hook" in step and step["hook"].get("source") == "fork")


def said_before(run):
    """Every text said in the conversation before it was moved (rows of the new transcript older than the move)."""
    when = moved_at(run)
    return [t for step in run["steps"] if step.get("into") == "new" and "timestamp" in step["write"] and stamp(step["write"]) < when
            for t in transcript.said_in(step["write"])]


def on_the_way(events):
    """What the bot would post as said on the way: an event's texts without the reply its own Stop brings and without
    the reply of the turn before (the bot's rt.last_final), as in test_turns."""
    out, last_final = [], ""
    for ev in events:
        final = (ev.get("last_assistant_message") or "").strip() if ev["hook_event_name"] in hooks.ENDS else ""
        out += [t for t in ev.get("texts") or [] if t not in (final, last_final)]
        if ev["hook_event_name"] in hooks.ENDS and not ev.get("agent_id"):
            last_final = final
    return out


class Forks(unittest.TestCase):
    def each(self):
        for version in VERSIONS:
            for name in names(version):
                for timing in TIMINGS:
                    yield version, name, timing

    def test_there_are_runs_of_both_versions_and_both_ways_of_moving(self):
        for version in VERSIONS:
            got = names(version)
            self.assertTrue([n for n in got if n.startswith("idle-")] and [n for n in got if n.startswith("busy-")], (version, got))

    def test_what_the_runs_show(self):
        """The facts the README states, on every run: the new file is not there at the fork's SessionStart, it
        begins with rows older than the move, and its copied message rows bear the id of a later turn or none."""
        for version in VERSIONS:
            for name in names(version):
                run = load(version, name)
                first_new = next(step["t"] for step in run["steps"] if step.get("into") == "new")
                self.assertGreater(first_new, moved_at(run), (version, name))
                self.assertTrue(said_before(run), (version, name))
                copied = [step["write"] for step in run["steps"] if step.get("into") == "new" and "timestamp" in step["write"]
                          and stamp(step["write"]) < moved_at(run) and step["write"].get("type") == "user"
                          and isinstance(transcript.content_of(step["write"]), str)]
                originals = {step["write"]["uuid"]: transcript.asked_as(step["write"]) for step in run["steps"] if step.get("into") == "old" and step["write"].get("uuid")}
                numbers = {step["write"]["uuid"]: transcript.turn_index(step["write"]) for step in run["steps"] if step.get("into") == "old" and step["write"].get("uuid")}
                self.assertTrue(copied)
                for row in copied:
                    self.assertIn(row["uuid"], originals, "the same row as in the old file")
                    self.assertEqual(transcript.turn_index(row), numbers[row["uuid"]], "each keeps the number of its own turn, or none")
                new_rows = [step["write"] for step in run["steps"] if step.get("into") == "new" and transcript.begins(step["write"])]
                if name == "resumed-plain":   # a conversation whose rows give no number gets none, before the move or after
                    self.assertEqual([transcript.turn_index(row) for row in new_rows], [None] * len(new_rows), version)
                else:
                    self.assertTrue(all(transcript.turn_index(row) is not None for row in new_rows), (version, name))
                self.assertTrue(any(transcript.asked_as(row) != originals[row["uuid"]] for row in copied) or all(transcript.asked_as(row) is None for row in copied),
                                "…and not the prompt id it had")

    def test_nothing_said_before_the_move_is_read_again(self):
        for version, name, timing in self.each():
            run = load(version, name)
            events, paths, tmp = played(version, name, timing)
            with tmp:
                old = set(said_before(run))
                again = [(ev["hook_event_name"], t) for ev in events if ev.get("transcript_path") == paths["new"] for t in ev.get("texts") or [] if t in old]
                self.assertEqual(again, [], (version, name, timing))

    def test_what_is_said_on_the_way_after_the_move_comes_once(self):
        for version, name, timing in self.each():
            run = load(version, name)
            events, paths, tmp = played(version, name, timing)
            with tmp:
                finals = set((ev.get("last_assistant_message") or "").strip() for ev in events if ev["hook_event_name"] in hooks.ENDS)
                after = [t for step in run["steps"] if step.get("into") == "new" and "timestamp" in step["write"] and stamp(step["write"]) >= moved_at(run)
                         for t in transcript.said_in(step["write"]) if t not in finals]
                got = on_the_way([ev for ev in events if ev.get("transcript_path") == paths["new"]])
                self.assertEqual(sorted(got), sorted(after), (version, name, timing))
                if name.startswith("idle-"):
                    self.assertTrue(after, "the run does hold words said on the way after the move")

    def test_a_turn_after_the_move_begins_at_its_own_row(self):
        """Not at the first row that bears its id: that is the copy of the conversation's first message."""
        for version, name, timing in self.each():
            events, paths, tmp = played(version, name, timing)
            with tmp:
                rows = transcript.rows(paths["new"])
                moved = moved_at(load(version, name))
                for ev in events:
                    if ev.get("transcript_path") != paths["new"] or ev["hook_event_name"] != "Stop" or not ev.get("prompt_id"):
                        continue
                    mine = [(start, row) for start, _end, row in rows if transcript.begins(row) == ev["prompt_id"]]
                    own = [start for start, row in mine if stamp(row) >= moved]
                    self.assertEqual(len(own), 1, (version, name))
                    self.assertEqual((ev["dc_turn_start"], ev["dc_turn_sure"]), (own[0], True), (version, name, timing, len(mine)))

    def test_a_reader_started_anew_at_any_point_reads_nothing_old_either(self):
        """The bot restarted, the relay reconnected: the place kept for the file is gone and the file is met again,
        at whichever event comes next. Then nothing before that event is read (as for any file), the words ahead of
        the tool call that is the first event are (step 2.2g), and never a copied reply's."""
        for version in VERSIONS:
            for name in names(version):
                run = load(version, name)
                total = sum(1 for step in run["steps"] if "hook" in step)
                old = set(said_before(run))
                for timing in TIMINGS:
                    for at in range(total):
                        events, paths, tmp = played(version, name, timing, forget_before=at)
                        with tmp:
                            again = [t for ev in events[at:] if ev.get("transcript_path") == paths["new"] for t in ev.get("texts") or [] if t in old]
                            self.assertEqual(again, [], (version, name, timing, at))
                            rows = transcript.rows(paths["new"])
                            for ev in events[at:]:
                                if ev.get("transcript_path") == paths["new"] and ev["hook_event_name"] == "Stop" and ev.get("prompt_id") and ev.get("dc_turn_start") is not None:
                                    row = next(r for start, _end, r in rows if start == ev["dc_turn_start"])
                                    self.assertGreaterEqual(stamp(row), moved_at(run) if "timestamp" in row else 0, (version, name, timing, at))


    def test_restarted_just_before_the_first_tool_call_after_the_move(self):
        """Step 2.2g on such a file: the reader is started anew and the first event it gets is the tool call of the
        first turn after the move, its row written already. The words ahead of that call are read, from its
        reply's first row; the copied replies before it, and their tool calls, set nothing off."""
        seen = 0
        for version in VERSIONS:
            for name in names(version):
                if not name.startswith("idle-"):
                    continue
                run = load(version, name)
                call = next(step for step in run["steps"] if "hook" in step and step["hook"]["transcript_path"] == "{new}"
                            and step["hook"]["hook_event_name"] == "PreToolUse")
                with tempfile.TemporaryDirectory() as folder:
                    path, written, texts = os.path.join(folder, run["new"] + ".jsonl"), False, []
                    with open(path, "w", encoding="utf-8") as f:
                        for step in run["steps"]:   # the file as it is once the call's own row is written
                            if step.get("into") != "new":
                                continue
                            f.write(json.dumps(step["write"], ensure_ascii=False, separators=(",", ":")) + "\n")
                            if stamp(step["write"]) >= moved_at(run) if "timestamp" in step["write"] else False:
                                texts += transcript.said_in(step["write"])
                            if transcript.calls(step["write"], call["hook"]["tool_use_id"]):
                                written = True
                                break
                    self.assertTrue(written and texts, (version, name))
                    ev = dict(call["hook"], transcript_path=path, ts=call["t"] + 5)
                    hooks.enrich(ev, {})
                    self.assertEqual(ev.get("texts"), texts, (version, name))
                    self.assertEqual(len(texts), 1)
                    seen += 1
        self.assertGreaterEqual(seen, 4)


    def test_the_runs_show_the_file_written_in_two_goes(self):
        """What the timings 0.04 and 0.08 are about: a conversation moved in mid-turn gets its new file's first rows,
        which bear no time, within 0.05 s of the fork's hook, and the first row that bears one 0.05 s or more
        after those."""
        seen = 0
        for version in VERSIONS:
            for name in names(version):
                if not name.startswith("busy-"):
                    continue
                run = load(version, name)
                new = [step for step in run["steps"] if step.get("into") == "new"]
                head = [step for step in new if "timestamp" not in step["write"] and step["t"] == new[0]["t"]]
                rest = next(step for step in new if "timestamp" in step["write"])
                self.assertEqual(len(head), 3, (version, name))
                self.assertLess(new[0]["t"] - moved_at(run), 0.05, (version, name))
                self.assertGreater(rest["t"] - new[0]["t"], 0.05, (version, name))
                seen += 1
        self.assertEqual(seen, 4)


class NoTurnNumbers(Forks):
    """#code-review on 186a5d4: the rows of a conversation begun before 2.1.284 give no turn number (and some rows
    since give none). Moved to the background, its copy bears the new turn's id and no numbers; told by the number
    alone, the copy's first row was the turn's. The same recordings with the turn number taken off every row older
    than the move (what such a conversation's copy is taken to look like: nobody has recorded one), and off every
    row altogether (a version that writes none): every test of Forks holds for both."""
    STRIP = ("old rows", "all rows")

    def setUp(self):
        global load
        self.real = real = load

        def stripped(version, name, which=None):
            run = real(version, name)
            which = which or self.which
            moved = moved_at(run)
            for step in run["steps"]:
                row = step.get("write")
                if row and "turnPosition" in row and (which == "all rows" or stamp(row) < moved):
                    del row["turnPosition"]
            return run
        self.which = "old rows"
        load = stripped
        self.addCleanup(self.restore)

    def restore(self):
        global load
        load = self.real

    def run(self, result=None):
        for which in self.STRIP:
            self.which_now = which
            super().run(result)

    def test_what_the_runs_show(self):
        """(Not of these: the facts of the recordings as they are.) Here: the numbers are indeed gone."""
        self.which = getattr(self, "which_now", "old rows")
        for version in VERSIONS:
            for name in names(version):
                run = load(version, name)
                old = [step["write"] for step in run["steps"] if "write" in step and "timestamp" in step["write"] and stamp(step["write"]) < moved_at(run)]
                self.assertTrue(old and not any("turnPosition" in row for row in old), (version, name))

    def test_the_runs_show_the_file_written_in_two_goes(self):
        pass  # (of the recordings as they are)


class Rules(unittest.TestCase):
    def test_the_turns_own_first_row_among_those_that_bear_its_id(self):
        own = lambda *found: transcript.own_first([f if isinstance(f, tuple) else (f, False) for f in found])  # noqa: E731
        self.assertIsNone(transcript.own_first([]))
        self.assertEqual(own(3), 0)
        self.assertEqual(own(1, 2, 3), 2, "copies of earlier turns, then the turn's own")
        self.assertEqual(own(1, 3, 3), 1, "a message added while the turn ran: its number, a later row")
        self.assertEqual(own(None, None), 0, "a version that numbers no turns: the first, as it always was")
        self.assertEqual(own(None, 1, None, None, None), 0, "rows that say nothing of their turn decide nothing")
        self.assertEqual(own(None, 1, 2), 2)
        self.assertEqual(own(2, 2), 0)
        self.assertEqual(own(None, (None, True), (4, True)), 2, "copies that give no number, each turn ended before the next: the last")
        self.assertEqual(own(None, (None, True), (None, True), None), 2, "…and a message added to the turn after its own first")
        self.assertEqual(own(4, (4, True)), 0, "the same number, though a turn's end stands between: one turn, Claude Code says so")
        self.assertEqual(own(3, (None, True)), 1, "an end and no number to say otherwise: taken as another turn")
        self.assertEqual(own(1, None, 2, None), 2, "a row without a number among numbered ones stays with what stands before it")
        for odd in ({}, {"turnPosition": 3}, {"turnPosition": {"turnIndex": "2"}}, {"turnPosition": {"turnIndex": True}}, None, []):
            self.assertIsNone(transcript.turn_index(odd), odd)
        self.assertEqual(transcript.turn_index({"turnPosition": {"promptIndex": 4, "turnIndex": 2}}), 2)
        reply = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": []}}
        self.assertTrue(transcript.ends_turn(reply))
        self.assertTrue(transcript.ends_turn({"type": "system", "subtype": "turn_duration"}))
        for no in (dict(reply, isSidechain=True), {"type": "assistant", "message": {"stop_reason": "tool_use"}}, {"type": "system", "subtype": "stop_hook_summary"},
                   {"type": "user", "message": {"stop_reason": "end_turn"}}, {"type": "assistant", "message": "x"}, None, [], "end_turn"):
            self.assertFalse(transcript.ends_turn(no), no)

    def write(self, rows):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        self.addCleanup(os.remove, tmp.name)
        with tmp:
            for row in rows:
                tmp.write(row if isinstance(row, str) else json.dumps(row) + "\n")
        return tmp.name

    def test_rows_older_than_a_moment(self):
        """The row's "timestamp" (ISO 8601, UTC, as Claude Code writes it: "…T11:07:13.524Z") against seconds since
        the epoch (the "ts" tools/dchook stamps on an event): both of the machine the file is on. Earlier counts as
        read; the same moment or later does not (nothing new is ever passed over for being on the line); a row
        with no timestamp, or one that cannot be read, decides nothing: among older rows it lies behind the place
        with them, after the last of them it is read as ever."""
        t = datetime.datetime(2026, 10, 13, 11, 7, 20, tzinfo=datetime.timezone.utc).timestamp()
        rows = [{"type": "mode"}, {"type": "user", "timestamp": "2026-10-13T11:07:13.524Z"}, {"type": "attachment"},
                {"type": "assistant", "timestamp": "2026-10-13T11:07:19.999Z"}, {"type": "last-prompt"},
                {"type": "user", "timestamp": "2026-10-13T11:07:20.000Z"}, {"type": "assistant", "timestamp": "2026-10-13T11:07:21.000Z"}]
        path = self.write(rows)
        ends = [end for _start, end, _row in transcript.rows(path)]
        self.assertEqual(transcript.before(path, t), ends[3], "after the last older row; the row with no time after it is read")
        self.assertEqual(transcript.before(path, t + 0.0005), ends[5], "the same moment is not older")
        self.assertEqual(transcript.before(path, t - 100), 0)
        self.assertEqual(transcript.before(path, t + 100), ends[6])
        self.assertEqual(transcript.before(os.path.join(os.path.dirname(path), "none.jsonl"), t), 0)
        odd = self.write(['{"timestamp": 5}\n', '{"timestamp": "yesterday"}\n', "[1]\n", "{not json\n", '{"timestamp": "2026-10-13T13:07:19+02:00"}\n', '{"timestamp": null}\n'])
        ends = [end for _start, end, _row in transcript.rows(odd)]
        self.assertEqual(transcript.before(odd, t), ends[4], "another way of writing the zone is read; what cannot be read decides nothing")
        self.assertEqual(transcript.before(self.write(['{"timestamp": "2026-10-13T11:07:19Z"}']), t), 0, "a row still being written")

    def test_a_file_that_was_there_from_the_start_is_not_judged_by_time(self):
        """Only a file that did not exist when its first event came is. A conversation resumed, or met in its
        middle, is read from its end as ever; one that begins has nothing older in it, whatever the clocks say."""
        path = self.write([{"type": "user", "promptId": "p", "timestamp": "2026-10-13T11:07:13.524Z", "message": {"role": "user", "content": "q"}},
                           {"type": "assistant", "timestamp": "2026-10-13T11:07:14.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "said"}]}}])
        pos = {}
        rec = hooks.place(pos, path, "p", "UserPromptSubmit", when=5.0)
        self.assertNotIn("unmet", rec)
        self.assertEqual(rec["at"], transcript.size(path))
        fresh = path + ".new"
        self.addCleanup(lambda: os.path.exists(fresh) and os.remove(fresh))
        rec = hooks.place(pos, fresh, None, "SessionStart", when=4e9)   # a clock far ahead: nothing written after can be "older"…
        self.assertEqual(rec["unmet"], 4e9)
        self.assertIsInstance(hooks.place({}, fresh + "x", None, "SessionStart")["unmet"], float)  # (no time given: now)
        with open(fresh, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "promptId": "p2", "timestamp": "2026-10-13T11:07:13.524Z", "message": {"role": "user", "content": "q"}}) + "\n")
        rec = hooks.place(pos, fresh, "p2", "UserPromptSubmit", when=4e9)
        self.assertNotIn("unmet", rec, "settled once the turn's own row is there")
        self.assertEqual(rec["at"], 0, "…and that row is never old, whatever the clocks say: a new conversation is read from its first row")


class FirstEventIsTheMessage(unittest.TestCase):
    """#code-review on 3477015: a transcript that is not there yet, and the first event this reader gets of it is a
    UserPromptSubmit (a conversation started or cleared before the bot restarted and spoken to after; a Claude
    Code attached before anything was said; the relay reconnected). The turn's own first row is stamped a moment
    before its hook is called, in every recorded turn there is."""

    def test_a_messages_row_is_stamped_before_its_hook_is_called(self):
        gaps = []
        for version in VERSIONS:
            for name in names(version):
                run = load(version, name)
                rows = {(step["into"], transcript.begins(step["write"])): step["write"] for step in run["steps"]
                        if "write" in step and transcript.begins(step["write"]) and stamp(step["write"]) >= (moved_at(run) if step["into"] == "new" else 0)}
                for step in run["steps"]:
                    if "hook" in step and step["hook"]["hook_event_name"] == "UserPromptSubmit":
                        into = "old" if step["hook"]["transcript_path"] == "{old}" else "new"
                        own = [row for (where, pid), row in rows.items() if where == into and pid == step["hook"]["prompt_id"]]
                        if own:
                            gaps.append(step["t"] - max(stamp(row) for row in own))
        self.assertGreaterEqual(len(gaps), 20)
        self.assertTrue(all(0 < gap < 0.2 for gap in gaps), (min(gaps), max(gaps)))

    def play(self, shift):
        """UserPromptSubmit at T with no file; the message's row stamped T+shift; words and a tool call; the reply."""
        t = datetime.datetime(2026, 10, 13, 11, 7, 20, tzinfo=datetime.timezone.utc).timestamp()
        iso = lambda secs: datetime.datetime.fromtimestamp(secs, datetime.timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path, pos = os.path.join(tmp.name, "s.jsonl"), {}

        def write(*rows):
            with open(path, "a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")

        def event(kind, ts, **more):
            ev = dict({"hook_event_name": kind, "transcript_path": path, "prompt_id": "p1", "ts": ts}, **more)
            hooks.enrich(ev, pos)
            return ev
        said = lambda text, mid, at: {"type": "assistant", "timestamp": iso(at), "message": {"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}]}}  # noqa: E731
        event("UserPromptSubmit", t)
        write({"type": "mode"}, {"type": "user", "promptId": "p1", "timestamp": iso(t + shift), "turnPosition": {"promptIndex": 1, "turnIndex": 1}, "message": {"role": "user", "content": "q"}},
              said("SAID-1", "m1", t + 1),
              {"type": "assistant", "timestamp": iso(t + 1), "message": {"id": "m1", "role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}})
        call = event("PreToolUse", t + 1.1, tool_use_id="t1")
        write({"type": "user", "promptId": "p1", "timestamp": iso(t + 2), "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}},
              said("ANS-1", "m2", t + 3))
        end = event("Stop", t + 3.1, last_assistant_message="ANS-1")
        first = next(start for start, _end, row in transcript.rows(path) if transcript.begins(row) == "p1")
        return call, end, first

    def test_nothing_is_settled_before_the_turns_own_row_is_there(self):
        """The file is written in two goes: a few rows that bear no time, then the rest a tenth of a second later.
        An event that comes between (another message; a relay catching up) must not fix the place at the first
        byte: the copy would lie ahead of it again."""
        run = load(VERSIONS[0], "idle-3")
        with tempfile.TemporaryDirectory() as folder:
            path, pos = os.path.join(folder, "n.jsonl"), {}
            new = [step["write"] for step in run["steps"] if step.get("into") == "new"]
            events = [dict(step["hook"], transcript_path=path, ts=step["t"]) for step in run["steps"] if "hook" in step and step["hook"]["transcript_path"] == "{new}"]
            start, first, call = events[0], events[1], next(ev for ev in events if ev["hook_event_name"] == "PreToolUse")
            hooks.enrich(start, pos)
            hooks.enrich(first, pos)
            head = next(i for i, row in enumerate(new) if "timestamp" in row)
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(json.dumps(row) + "\n" for row in new[:head]))
            again = dict(first)
            hooks.enrich(again, pos)   # between the two goes
            self.assertIsNotNone(pos[path].get("unmet"))
            with open(path, "a", encoding="utf-8") as f:
                for row in new[head:]:
                    f.write(json.dumps(row) + "\n")
                    if transcript.calls(row, call["tool_use_id"]):
                        break
            hooks.enrich(call, pos)
            self.assertNotIn("unmet", pos[path])
            self.assertEqual(len(call["texts"]), 1, call["texts"])
            self.assertNotIn(call["texts"][0], said_before(run))

    def test_every_recorded_turn_played_as_the_first_of_a_file_that_is_not_there(self):
        """Each turn of every recording, alone in a file of its own that does not exist when its UserPromptSubmit
        comes (which is then the reader's first event), its rows written at the moments they were: its words on
        the way come once, with the tool call's own event when that event is left until its row is written (as
        the bot and the relay leave it), and the turn begins at its first row, for sure."""
        turns = 0
        for version in VERSIONS:
            for name in names(version):
                run = load(version, name)
                for into, mark in (("old", "{old}"), ("new", "{new}")):
                    floor = moved_at(run) if into == "new" else 0
                    rows = [(step["t"], step["write"]) for step in run["steps"] if step.get("into") == into]
                    for ask in [step for step in run["steps"] if "hook" in step and step["hook"]["transcript_path"] == mark and step["hook"]["hook_event_name"] == "UserPromptSubmit"]:
                        pid = ask["hook"]["prompt_id"]
                        first = next(i for i, (_t, row) in enumerate(rows) if transcript.begins(row) == pid and stamp(row) >= floor)
                        last = next((i for i in range(first + 1, len(rows)) if transcript.begins(rows[i][1]) not in (None, pid)), len(rows))
                        steps = [(t, 0, row) for t, row in rows[first:last]] + [(step["t"], 1, step["hook"]) for step in run["steps"]
                                                                                 if "hook" in step and step["hook"]["transcript_path"] == mark and step["hook"].get("prompt_id") == pid
                                                                                 and step["hook"]["hook_event_name"] in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")]
                        self.assertGreater(ask["t"], stamp(rows[first][1]), "the fact this is about")
                        if not any(is_hook and what["hook_event_name"] == "Stop" for _t, is_hook, what in steps):
                            continue  # the turn that was moved while it ran: its end comes under another id, with none of its own
                        for hold in (None, 2.0):
                            with tempfile.TemporaryDirectory() as folder:
                                path, pos, waiting, done = os.path.join(folder, "t.jsonl"), {}, [], []

                                def flush(now):
                                    while waiting and not (hold is not None and hooks.awaits(waiting[0], pos, now=now, hold=hold)):
                                        ev = waiting.pop(0)
                                        hooks.enrich(ev, pos)
                                        done.append(ev)
                                for t, is_hook, what in sorted(steps, key=lambda s: (s[0], s[1])):
                                    if is_hook:
                                        waiting.append(dict(what, transcript_path=path, ts=t))
                                    else:
                                        with open(path, "a", encoding="utf-8") as f:
                                            f.write(json.dumps(what, ensure_ascii=False, separators=(",", ":")) + "\n")
                                    flush(t)
                                flush(steps[-1][0] + 10)
                                at = (version, name, into, pid[:6], hold)
                                stop = next(ev for ev in done if ev["hook_event_name"] == "Stop")
                                final = (stop.get("last_assistant_message") or "").strip()
                                want = [t for _t, row in rows[first:last] for t in transcript.said_in(row) if t != final]
                                got = [t for ev in done for t in ev.get("texts") or [] if t != final]
                                self.assertEqual(got, want, at)
                                self.assertEqual((stop["dc_turn_start"], stop["dc_turn_sure"]), (0, True), at)
                                calls = [ev for ev in done if ev["hook_event_name"] == "PreToolUse"]
                                if hold is not None and calls and want:
                                    self.assertEqual(calls[0]["texts"], want[:1], at)
                        turns += 1
        self.assertEqual(turns, 38, "every turn of the recordings that began and ended while they were recorded (42, less the four moved while they ran)")

    def test_the_turn_is_read_as_any_other(self):
        for shift in (-0.05, -0.012, 0.0, 0.05):
            call, end, first = self.play(shift)
            self.assertEqual((call["texts"], end["texts"]), (["SAID-1"], ["ANS-1"]), shift)
            self.assertEqual((call["dc_turn_start"], call["dc_turn_sure"]), (first, True), shift)
            self.assertEqual((end["dc_turn_start"], end["dc_turn_sure"]), (first, True), shift)

    def test_when_time_and_the_row_disagree_time_holds(self):
        """The two rules against each other: the fork's SessionStart was never seen, the first event is the first
        message after the move (so the turn's own row is a moment older than the event, as ever), and the copy's
        last turn was cut short: it has no end, and its rows give no turn number (a conversation of before 2.1.284),
        so nothing in the file tells its first row from the turn's own. The words said in that turn stand between,
        older than the event: the place is not moved back over them. Nothing old is read; the turn's own words come,
        with its end at the latest."""
        t = datetime.datetime(2026, 10, 13, 11, 7, 20, tzinfo=datetime.timezone.utc).timestamp()
        iso = lambda secs: datetime.datetime.fromtimestamp(secs, datetime.timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
        said = lambda text, at, why="tool_use": {"type": "assistant", "timestamp": iso(at), "message": {"role": "assistant", "stop_reason": why, "content": [{"type": "text", "text": text}]}}  # noqa: E731
        asked = lambda text, at: {"type": "user", "promptId": "p9", "timestamp": iso(at), "message": {"role": "user", "content": text}}  # noqa: E731
        for cut_short in (True, False):
            with tempfile.TemporaryDirectory() as folder:
                path, pos = os.path.join(folder, "n.jsonl"), {}
                first = {"hook_event_name": "UserPromptSubmit", "transcript_path": path, "prompt_id": "p9", "ts": t}
                hooks.enrich(first, pos)
                rows = [{"type": "mode"}, asked("q-old-1", t - 300), said("OLD-ANS-1", t - 299, "end_turn"),
                        asked("q-old-2", t - 200), said("OLD-SAID-2", t - 199)]
                if not cut_short:
                    rows.append(said("OLD-ANS-2", t - 198, "end_turn"))
                rows += [asked("q-new", t - 0.05), said("NEW-SAID", t + 1),
                         {"type": "assistant", "timestamp": iso(t + 1), "message": {"role": "assistant", "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}}]
                with open(path, "w", encoding="utf-8") as f:
                    f.write("".join(json.dumps(row) + "\n" for row in rows))
                call = dict(first, hook_event_name="PreToolUse", tool_use_id="t1", ts=t + 1.1)
                hooks.enrich(call, pos)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(said("NEW-ANS", t + 3, "end_turn")) + "\n")
                end = dict(first, hook_event_name="Stop", last_assistant_message="NEW-ANS", ts=t + 3.1)
                hooks.enrich(end, pos)
                got = call["texts"] + end["texts"]
                self.assertEqual([text for text in got if text.startswith("OLD")], [], cut_short)
                self.assertEqual(got, ["NEW-SAID", "NEW-ANS"], cut_short)
                if not cut_short:   # the copy's turns all ended: the turn's own row is told, and its words come with their event
                    self.assertEqual(call["texts"], ["NEW-SAID"])
                    self.assertEqual(end["dc_turn_sure"], True)

    def test_in_a_moved_conversation_too_when_its_start_was_never_seen(self):
        """The fork's SessionStart came while nobody read (a restart in those tenths of a second): the first event
        of the new file is the first message after the move, the file not written yet. Nothing old is read, and the
        turn begins at its own row."""
        for version in VERSIONS:
            for name in names(version):
                if not name.startswith("idle-"):
                    continue
                run = load(version, name)
                order = [step["hook"] for step in run["steps"] if "hook" in step]
                fork = next(i for i, ev in enumerate(order) if ev.get("source") == "fork")
                for timing in TIMINGS:
                    tmp = tempfile.TemporaryDirectory()
                    pos, count = {}, [0]

                    def handle(ev):
                        count[0] += 1
                        if count[0] - 1 == fork:
                            return   # never seen
                        hooks.enrich(ev, pos)
                    handle.pos = pos
                    events, paths = play(run, tmp.name, handle, timing)
                    with tmp:
                        old = set(said_before(run))
                        mine = [ev for ev in events if ev.get("transcript_path") == paths["new"]]
                        self.assertEqual([t for ev in mine for t in ev.get("texts") or [] if t in old], [], (version, name, timing))
                        stop = next(ev for ev in mine if ev["hook_event_name"] == "Stop")
                        rows = transcript.rows(paths["new"])
                        own = [start for start, _end, row in rows if transcript.begins(row) == stop["prompt_id"] and stamp(row) >= moved_at(run)]
                        self.assertEqual((stop["dc_turn_start"], stop["dc_turn_sure"]), (own[0], True), (version, name, timing))
                        got = on_the_way(mine)
                        self.assertEqual(len(got), 1, (version, name, timing, got))


if __name__ == "__main__":
    unittest.main()
