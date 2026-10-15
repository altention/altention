"""The end of a turn waits for the reply's own row (stage 3b step 2.2f). Claude Code calls the Stop hook first and
writes the reply's row about 0.1 s later (measured, both versions; turn_ends/: every hook event with when
its hook began, every transcript row with when it appeared). Handled at once, the turn's end read the usage of the
turn before (the footer's context one turn late) or none at all (a conversation's first turn). The measured runs are
played here by their own clock: rows appear when they appeared, events are looked at as a reader that polls would."""
import json
import os
import tempfile
import unittest

from altention.connectors.claude_code import hooks, transcript

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turn_ends")
VERSIONS = ("2.1.296", "2.1.288")
STAMP = 0.02   # tools/dchook stamps its event this long after the hook began (measured: 0.014 to 0.027 s)
LOOK = 0.02    # how often the reader looks again while an event waits


def written(row):
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"  # as Claude Code writes a row


def tokens(row):
    u = row["message"]["usage"]
    return sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


class Played(object):
    """One version's run, played into one transcript (the cases follow one another in one conversation, as they were
    measured). For each event: how long it waited after its stamp, and the event as enrich() left it."""

    def __init__(self, version, tmp, hold=None, drop=lambda case, row: False):
        with open(os.path.join(HERE, version + ".json"), encoding="utf-8") as f:
            self.cases = json.load(f)["cases"]
        self.path, self.pos, self.out, self.hold = os.path.join(tmp, "s.jsonl"), {}, {}, hold
        open(self.path, "w").close()
        for case in self.cases:
            rows = sorted([r for r in case["rows"] if not drop(case["name"], r["row"])], key=lambda r: r["at"])
            done, got = 0, []
            for e in case["events"]:
                ev = dict(e["event"], transcript_path=self.path, ts=e["at"] + STAMP)
                now, waited = ev["ts"], 0.0
                while True:
                    while done < len(rows) and rows[done]["at"] <= now:
                        with open(self.path, "a", encoding="utf-8") as f:
                            f.write(written(rows[done]["row"]))
                        done += 1
                    kw = {} if hold is None else {"end_hold": hold}
                    if not hooks.awaits(ev, self.pos, now=now, **kw):
                        break
                    now, waited = now + LOOK, waited + LOOK
                    assert waited < 5, "held for ever"
                hooks.enrich(ev, self.pos)
                got.append((round(waited, 2), ev))
            with open(self.path, "a", encoding="utf-8") as f:  # what was written after the case's last event
                f.write("".join(written(r["row"]) for r in rows[done:]))
            self.out[case["name"]] = got

    def ends(self, name):
        return [(w, ev) for w, ev in self.out[name] if ev["hook_event_name"] in ("Stop", "StopFailure")]

    def last_reply(self, name, nth=-1):
        case = next(c for c in self.cases if c["name"] == name)
        return [r["row"] for r in case["rows"] if hooks.last_reply(r["row"])][nth]


class MeasuredRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def play(self, version, **kw):
        return Played(version, tempfile.mkdtemp(dir=self.tmp.name), **kw)

    def test_a_reply_is_waited_for_and_the_context_is_the_turns_own(self):
        """Without a tool, with one, two text blocks, a thinking block, ends in blanks, 60000 characters, cut by the
        output limit or refused once (Claude Code asks again; the Stop comes after the second reply), a message sent
        in along the way: the Stop waits about 0.1 s, and what it then reads is the usage of the turn's own reply."""
        for version in VERSIONS:
            run = self.play(version)
            for name in ("plain", "tool", "twotext", "thinking", "space", "long", "maxtokens", "refusal", "queuedtool"):
                ((waited, ev),) = run.ends(name)
                self.assertIn("last_assistant_message", ev, (version, name))
                self.assertTrue(0.04 <= waited <= 0.2, (version, name, waited))  # (the 60000 characters: 0.16 s on 2.1.288)
                reply = run.last_reply(name)
                self.assertEqual(ev["context"], [tokens(reply), reply["message"]["model"]], (version, name))
            self.assertEqual(hooks.ends_late(run.pos), 0, version)

    def test_the_first_turn_of_a_conversation_has_its_context(self):
        """Measured before this step: none of the transcript's rows bears a usage when the first Stop is handled, and
        the reply's footer said no context (8 of 8 first turns at a fast reader)."""
        for version in VERSIONS:
            run = self.play(version)
            ((waited, ev),) = run.ends("plain")
            self.assertTrue(ev["context"] and ev["context"][0] > 0, version)
            at_once = self.play(version, hold=0.0)  # as before the step: no waiting at all
            self.assertIsNone(at_once.ends("plain")[0][1]["context"], "the sample does show what was measured")
            self.assertEqual(at_once.ends("plain")[0][0], 0.0)

    def test_a_stop_that_says_of_no_reply_is_not_waited_for(self):
        """The model answered with nothing (no block, or one empty text): Claude Code asks again, and when it gets
        nothing again the Stop bears no last_assistant_message: there is no reply row to wait for."""
        for version in VERSIONS:
            run = self.play(version)
            for name in ("toolend", "empty", "emptytext"):
                ((waited, ev),) = run.ends(name)
                self.assertNotIn("last_assistant_message", ev, (version, name))
                self.assertEqual(waited, 0.0, (version, name))

    def test_a_failed_request_is_not_waited_for(self):
        """StopFailure: its row (isApiErrorMessage) is written about 0.1 s after the hook too; this step leaves it:
        the message of a failed turn shows no context."""
        for version in VERSIONS:
            run = self.play(version)
            for name in ("fail", "failtool"):
                ((waited, ev),) = run.ends(name)
                self.assertEqual((ev["hook_event_name"], waited), ("StopFailure", 0.0), (version, name))

    def test_a_reply_cut_or_refused_is_not_the_turns_last(self):
        for version in VERSIONS:
            run = self.play(version)
            for name, reason in (("maxtokens", "max_tokens"), ("refusal", "refusal")):
                case = next(c for c in run.cases if c["name"] == name)
                cut = [r["row"] for r in case["rows"] if r["row"].get("type") == "assistant" and r["row"]["message"].get("stop_reason") == reason]
                self.assertTrue(cut, (version, name))
                self.assertFalse(any(hooks.last_reply(r) for r in cut))
                self.assertEqual(len([r for r in case["rows"] if hooks.last_reply(r["row"])]), 1, "only the second reply ends the turn")

    def test_a_message_that_waited_its_turn_out(self):
        """Typed while a turn ran, it starts a turn of its own after it. Measured: its UserPromptSubmit comes at once,
        with the running turn's id; the first turn's reply is written before its row; the second Stop bears a new
        id. Each Stop waits for its own reply and reads its own usage."""
        for version in VERSIONS:
            run = self.play(version)
            (w1, first), (w2, second) = run.ends("queued")
            self.assertNotEqual(first["prompt_id"], second["prompt_id"])
            for waited, ev, nth in ((w1, first, 0), (w2, second, 1)):
                self.assertTrue(0.04 <= waited <= 0.14, (version, waited))
                reply = run.last_reply("queued", nth)
                self.assertEqual("".join(b.get("text", "") for b in reply["message"]["content"]).strip(), ev["last_assistant_message"])
                self.assertEqual(ev["context"], [tokens(reply), reply["message"]["model"]])

    def test_a_reply_whose_row_never_comes_costs_the_limit_once_and_is_counted(self):
        for version in VERSIONS:
            run = self.play(version, drop=lambda case, row: case == "tool" and hooks.last_reply(row))
            ((waited, ev),) = run.ends("tool")
            self.assertTrue(hooks.END_HOLD_SECS <= waited <= hooks.END_HOLD_SECS + 2 * LOOK, (version, waited))
            self.assertEqual(hooks.ends_late(run.pos), 1, "counted, once")
            self.assertTrue(0.04 <= run.ends("twotext")[0][0] <= 0.14, "the turn after it waits for its own as ever")
            self.assertEqual(run.ends("twotext")[0][1]["context"][0], tokens(run.last_reply("twotext")))


class TheRule(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "s.jsonl")
        self.pos = {}
        self.write({"type": "user", "promptId": "p1", "message": {"role": "user", "content": "go"}})
        hooks.enrich(self.ev("UserPromptSubmit", prompt="go"), self.pos)

    def write(self, *rows):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("".join(written(r) for r in rows))

    def ev(self, kind, **kw):
        return dict({"hook_event_name": kind, "transcript_path": self.path, "prompt_id": "p1", "ts": 100.0}, **kw)

    def stop(self, **kw):
        return self.ev("Stop", **dict({"last_assistant_message": "done."}, **kw))

    @staticmethod
    def reply(text="done.", **message):
        return {"type": "assistant", "message": dict({"role": "assistant", "model": "claude-x", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
                                                      "usage": {"input_tokens": 7, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}, **message)}

    def test_waited_for_until_it_is_there_and_no_longer_than_the_limit(self):
        self.assertTrue(hooks.awaits(self.stop(), self.pos, now=100.0))
        self.assertTrue(hooks.awaits(self.stop(), self.pos, now=100.0 + hooks.END_HOLD_SECS - 0.01))
        self.assertEqual(hooks.ends_late(self.pos), 0)
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.0 + hooks.END_HOLD_SECS), "the limit")
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.9))
        self.assertEqual(hooks.ends_late(self.pos), 1, "counted once for the turn, however often it is asked")
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=5000.0), "an event that waited while nobody looked is not held")
        self.assertEqual(hooks.ends_late(self.pos), 1)
        self.assertTrue(hooks.awaits(self.stop(), self.pos, now=100.2, end_hold=0.3))
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.2, end_hold=0.1))
        self.write(self.reply())
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.05), "its row is there")
        self.assertEqual(hooks.END_HOLD_SECS, 0.5)

    def test_a_row_that_is_there_before_the_hooks_stamp_is_not_waited_for(self):
        """Measured on the real API: once the row was in the file 0.139 s before the stamp (a hook slow to start)."""
        self.write(self.reply())
        self.assertFalse(hooks.awaits(self.stop(ts=100.139), self.pos, now=100.14))
        self.assertFalse(hooks.awaits(self.stop(ts=100.139), self.pos, now=100.0), "nor an event stamped ahead of the clock")
        self.assertEqual(hooks.ends_late(self.pos), 0)

    def test_which_rows_end_the_turn(self):
        for what, row in (("a tool call", self.reply(stop_reason="tool_use", content=[{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}])),
                          ("cut by the output limit", self.reply(stop_reason="max_tokens")), ("refused", self.reply(stop_reason="refusal")),
                          ("a failed request's notice", dict(self.reply(stop_reason="stop_sequence"), isApiErrorMessage=True)),
                          ("one that ended on a stop sequence with other words", self.reply("something else", stop_reason="stop_sequence")),
                          ("a subagent's", dict(self.reply(), isSidechain=True)), ("one with no usage", self.reply(usage=None)),
                          ("the user's", {"type": "user", "message": {"role": "user", "content": "done."}}), ("no object", "a row")):
            self.assertFalse(hooks.last_reply(row), what)
            self.write(row)
            self.assertTrue(hooks.awaits(self.stop(), self.pos, now=100.1), what)
        for what, row in (("the thinking block of the reply", self.reply(content=[{"type": "thinking", "thinking": "hm"}])),
                          ("a text block that is not the last", self.reply("FIRST-BLOCK."))):
            self.assertTrue(hooks.last_reply(row), what)
        for reason in ("tool_use", "max_tokens", "refusal"):
            self.assertFalse(hooks.last_reply(self.reply(stop_reason=reason), "done."), "the same words do not make it the last: " + reason)
        self.write(self.reply(content=[{"type": "thinking", "thinking": "hm"}]))
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.1), "any row of the reply will do: they are written at once")

    def test_a_reply_that_ended_otherwise_than_end_turn_is_known_by_its_words(self):
        """#code-review-2 made one: a reply that stops on a stop sequence (an ordinary reply, no failure). Its row was
        not taken for the turn's last, and every such turn waited out the whole limit and was counted. Now: an
        assistant row with a usage whose text is what the Stop says, and whose stop_reason is not one the turn goes
        on after."""
        for reason in ("stop_sequence", None, "pause_turn", "something_new"):
            self.setUp()
            self.assertTrue(hooks.awaits(self.stop(), self.pos, now=100.1), reason)
            self.write(self.reply("  done. \n", stop_reason=reason))
            self.assertTrue(hooks.last_reply(self.reply("done.", stop_reason=reason), "done."), reason)
            self.assertFalse(hooks.last_reply(self.reply("done.", stop_reason=reason)), "not without the words to know it by")
            self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.1), reason)
            self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.9), reason)
            self.assertEqual(hooks.ends_late(self.pos), 0, reason)

    def test_an_empty_reply_text_is_not_waited_for(self):
        for said in ("", "   ", "\n"):
            self.assertFalse(hooks.awaits(self.stop(last_assistant_message=said), self.pos, now=100.1), repr(said))
            self.assertFalse(hooks.awaits(self.stop(last_assistant_message=said), self.pos, now=100.6), repr(said))
        self.assertEqual(hooks.ends_late(self.pos), 0)

    def test_an_end_first_looked_at_long_after_its_hook_is_not_counted(self):
        """Counted: an end that was waited for and whose row did not come within the limit (first seen past the limit
        by a moment). Not: one that lay in the spool while the reader was away (90 s old; #code-review)."""
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=190.0))
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.0 + hooks.END_HOLD_SECS + hooks.LATE_WITHIN))
        self.assertEqual(hooks.ends_late(self.pos), 0)
        self.assertFalse(hooks.awaits(self.stop(), self.pos, now=100.0 + hooks.END_HOLD_SECS + hooks.LATE_WITHIN - 0.01))
        self.assertEqual(hooks.ends_late(self.pos), 1)

    def test_only_a_reply_of_this_turn(self):
        """The reply of the turn before, still ahead of where the reading stands (its Stop was handled before its row
        came), is not this turn's."""
        self.write(self.reply("the answer before"))  # p1's, never read: the reading stands before it
        self.write({"type": "user", "promptId": "p2", "message": {"role": "user", "content": "next"}})
        hooks.enrich(self.ev("UserPromptSubmit", prompt="next", prompt_id="p2"), self.pos)
        stop = self.stop(prompt_id="p2", last_assistant_message="the next answer")
        self.assertTrue(hooks.awaits(stop, self.pos, now=100.1), "p1's reply is no reply to p2")
        self.write(self.reply("  the next answer \n"))
        self.assertFalse(hooks.awaits(stop, self.pos, now=100.1))

    def test_a_turn_whose_first_row_is_not_to_be_found_goes_by_the_text(self):
        """Its events bear an id no message in the file has: the row is known by what the event says the reply was
        (the reply's last text block, ends trimmed)."""
        hooks.enrich(self.ev("PreToolUse", prompt_id="p9", tool_name="Bash", tool_use_id="t9"), self.pos)
        stop = self.stop(prompt_id="p9", last_assistant_message="SECOND-BLOCK.")
        self.write(self.reply("something else"))
        self.assertTrue(hooks.awaits(stop, self.pos, now=100.1))
        self.write(self.reply("FIRST-BLOCK."), self.reply("\n\nSECOND-BLOCK.  \n"))
        self.assertFalse(hooks.awaits(stop, self.pos, now=100.1))

    def test_what_is_never_waited_for(self):
        for what, ev in (("no reply said", self.ev("Stop")), ("a reply that is no text", self.stop(last_assistant_message=None)),
                         ("a failed request", self.ev("StopFailure", last_assistant_message="API Error")), ("a subagent's", self.stop(agent_id="a1")),
                         ("no prompt_id", self.stop(prompt_id=None)), ("no stamp", self.stop(ts=None)), ("a stamp that is no number", self.stop(ts="soon")),
                         ("no transcript", self.stop(transcript_path="")), ("a transcript not met", self.stop(transcript_path=self.path + ".other")),
                         ("a subagent's stop", self.ev("SubagentStop", last_assistant_message="x"))):
            self.assertFalse(hooks.awaits(ev, self.pos, now=100.1), what)
        self.assertEqual(hooks.ends_late(self.pos), 0)

    def test_tool_calls_wait_as_ever_and_only_when_asked_to(self):
        call = self.ev("PreToolUse", tool_name="Bash", tool_use_id="t1")
        self.assertTrue(hooks.awaits(call, self.pos, now=100.1))
        self.assertTrue(hooks.awaits(call, self.pos, now=101.9), "by its own limit, not the end's")
        self.assertFalse(hooks.awaits(call, self.pos, now=100.1, calls=False), "a reader that posts no words on the way")
        self.assertTrue(hooks.awaits(self.stop(), self.pos, now=100.1, calls=False), "the end is waited for all the same")

    def test_nothing_is_changed_by_asking_but_the_count(self):
        before = json.dumps(self.pos, sort_keys=True)
        hooks.awaits(self.stop(), self.pos, now=100.1)
        self.assertEqual(json.dumps(self.pos, sort_keys=True), before)
        hooks.awaits(self.stop(), self.pos, now=100.6)
        rec = dict(self.pos[self.path])
        self.assertEqual((rec.pop("late"), rec.pop("late_for")), (1, "p1"), "the count, and which turn it was")
        self.assertEqual(json.dumps({self.path: rec}, sort_keys=True), before, "and nothing else")


if __name__ == "__main__":
    unittest.main()
