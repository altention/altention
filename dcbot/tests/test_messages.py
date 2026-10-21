"""bot/messages.py (step 2.6, backlog A-09): the words of a message that was not sent are looked up by a code.
The step changes where the words are, not the words: tests/samples/words/before-2.6.json holds every sentence as
the source said it before (taken out of tmux_bridge.py by program, "%s" where a value goes)."""
import contextlib
import io
import json
import os
import re
import tempfile
import time
import unittest
from unittest import mock

from bot import messages
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from altention import text as alt_text  # noqa: E402 (bot/ puts the engine on the path)

BEFORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "words", "before-2.6.json")


class Words(unittest.TestCase):
    SINCE = {"not_sent.machine_unreachable", "not_sent.machine_unreachable_unopened",  # codes that came after the move (2.6b, #issues 29): no sentence of before
             "notes.masked",  # (B-12)
             "notes.resend.done", "notes.resend.handed", "notes.resend.expired", "notes.resend.still",
             "notes.resend.given_up", "notes.resend.failed", "notes.resend.press_failed",
             "notes.resend.recovered"}  # (C-74)

    def setUp(self):
        with open(BEFORE, encoding="utf-8") as f:
            self.before = json.load(f)

    def test_every_code_says_word_for_word_what_was_said_before(self):
        now = {}
        for group, rows in messages.GROUPS.items():
            for code, row in rows.items():
                now["%s.%s" % (group, code)] = row[-1] if isinstance(row, tuple) else row
        now.update({"cleared.%s" % how: said for how, said in messages.CLEARED.items()})
        self.SINCE = self.SINCE | set(key for key in now if key.startswith("far."))  # (2.6f: a group of after the move)
        self.assertEqual(sorted(set(now) - self.SINCE), sorted(self.before), "a code without its sentence of before, or the other way round")
        self.assertTrue(self.SINCE <= set(now), "a code said to be new is not there")
        for key, said in now.items():
            if key in self.SINCE:
                continue
            self.assertEqual(re.sub(r"\{\w+\}", "%s", said), self.before[key].replace("%d", "%s"), key)

    def test_the_values_go_where_they_went(self):
        self.assertEqual(messages.not_sent("claude_code.owners_text", text="half"),
                         self.before["not_sent.claude_code.owners_text"] % "half")
        self.assertEqual(messages.not_sent("claude_code.blocked_command", command="/exit", why="會結束對話"),
                         self.before["not_sent.claude_code.blocked_command"] % ("/exit", "會結束對話"))
        self.assertEqual(messages.not_sent("claude_code.btw_too_long", chars=700, limit=500),
                         self.before["not_sent.claude_code.btw_too_long"] % (700, 500))
        self.assertEqual(messages.unconfirmed("claude_code.btw_no_answer", seconds=30, screen="S"),
                         self.before["unconfirmed.claude_code.btw_no_answer"] % (30, "S"))
        self.assertEqual(messages.unconfirmed("claude_code.stream_down", host="lap"), self.before["unconfirmed.claude_code.stream_down"] % "lap")
        self.assertEqual(messages.note("claude_code.new_conversation", session="abc"), self.before["notes.claude_code.new_conversation"] % "abc")
        self.assertEqual(messages.note("claude_code.resent"), self.before["notes.claude_code.resent"])
        self.assertEqual(messages.not_sent("not_started", error=ValueError("x {y}")), "出錯了：x {y}")
        for how in ("empty", "unchanged", "stuck"):
            self.assertEqual(messages.not_sent("claude_code.btw_pasted", cleared=how),
                             self.before["not_sent.claude_code.btw_pasted"] % self.before["cleared.%s" % how])
        self.assertEqual(messages.not_sent("claude_code.btw_not_command", cleared="whatever else"),
                         self.before["not_sent.claude_code.btw_not_command"] % self.before["cleared.stuck"], "as .get(cleared, …) did")

    def test_a_value_with_braces_or_percent_is_said_as_it_is(self):
        said = messages.not_sent("claude_code.owners_text", text="{0} %s {x}")
        self.assertIn("`{0} %s {x}`", said)


class Codes(unittest.TestCase):
    def test_every_code_names_a_common_one_and_whether_again_helps(self):
        for code, (common, retry, said) in messages.NOT_SENT.items():
            self.assertIn(common, messages.COMMON, code)
            self.assertIn(retry, (messages.TEMPORARY, messages.PERMANENT), code)
            self.assertEqual((messages.common(code), messages.retry(code)), (common, retry))
            self.assertTrue(code in messages.COMMON or "." in code or code in ("not_started", "machine_unreachable", "machine_unreachable_unopened"), code)
        self.assertEqual(messages.retry("claude_code.dialog"), messages.TEMPORARY)
        self.assertEqual(messages.retry("claude_code.blocked_command"), messages.PERMANENT)
        self.assertEqual(messages.common("needs_owner"), "needs_owner")

    def test_a_code_nobody_here_knows_is_said_with_one_general_sentence(self):
        """A new connector, or a dialog a new Claude Code grew: nothing in bot/ has to change for it."""
        said = messages.not_sent("other_tool.strange_box", row=3, text="a`b")
        self.assertIn("沒有送出", said)
        self.assertIn("`other_tool.strange_box`", said)
        self.assertIn("row=3", said)
        self.assertIn("text=a'b", said)
        self.assertEqual((messages.common("other_tool.strange_box"), messages.retry("other_tool.strange_box")), ("unknown", messages.PERMANENT))
        self.assertIn("`x.y`", messages.not_sent("x.y"))
        self.assertIn("`x.note`", messages.note("x.note"))
        self.assertIn("`x.late`", messages.unconfirmed("x.late", host="lap"))

    def test_the_words_are_the_engines_table_of_text(self):
        """Taken through altention.text: what stands in that table for the key is what is said."""
        key = "dcbot.not_sent.claude_code.dialog"
        self.assertEqual(alt_text.TEXT[alt_text.DEFAULT][key], messages.NOT_SENT["claude_code.dialog"][2])
        with mock.patch.dict(alt_text.TEXT[alt_text.DEFAULT], {key: "另一種說法 {x}"}):
            self.assertEqual(messages.not_sent("claude_code.dialog", x=1), "另一種說法 1")

    def test_taking_a_sentence_reads_nothing_from_disk(self):
        """#code-review on 3f8e783: every sentence went through the engine's settings (a file read; 0.4 s of waits
        in the event loop when it is broken). A sentence is taken from memory."""
        no = AssertionError("read while a sentence was taken")
        with mock.patch.object(alt_text.config, "setting", side_effect=no), mock.patch.object(alt_text.config, "load", side_effect=no), \
                mock.patch.object(alt_text.config, "instance_dir", side_effect=no), mock.patch("builtins.open", side_effect=no), \
                mock.patch.object(messages.os, "stat", side_effect=no):
            self.assertIn("沒有送出", messages.not_sent("claude_code.dialog"))
            self.assertIn("貼上的內容", messages.not_sent("claude_code.btw_pasted", cleared="empty"))
            self.assertIn("已經重送", messages.note("claude_code.resent"))
            self.assertIn("lap", messages.unconfirmed("claude_code.stream_down", host="lap"))
            self.assertIn("`x.y`", messages.not_sent("x.y"))


class Installation(unittest.TestCase):
    """What the installation says of the words (its language, its own wordings) is read by load(), off the event
    loop, into memory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.dict(os.environ, {"ALT_INSTANCE": self.tmp.name}),
                  mock.patch.dict(messages.loaded, {"lang": alt_text.DEFAULT, "own": {}, "stamp": False})):
            p.start()
            self.addCleanup(p.stop)
        self.path = os.path.join(self.tmp.name, "plugins.json")

    def settings(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))
        os.utime(self.path, ns=(time.time_ns(), time.time_ns() + len(os.listdir(self.tmp.name)) * 10 ** 6 + hash(str(text)) % 10 ** 6))

    def load(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            read = messages.load()
        return read, out.getvalue()

    def test_a_sentence_worded_the_installations_own_way(self):
        default = messages.not_sent("claude_code.dialog")
        self.settings({"dcbot": {"text": {"not_sent.claude_code.dialog": "有對話框開著，沒送。", "not_sent.claude_code.owners_text": "框裡有字：{text}",
                                          "cleared.empty": "清了", "notes.claude_code.resent": 5, "not_sent.x": ""}}})
        self.assertEqual(self.load(), (True, ""))
        self.assertEqual(messages.not_sent("claude_code.dialog"), "有對話框開著，沒送。")
        self.assertEqual(messages.not_sent("claude_code.owners_text", text="half"), "框裡有字：half")
        self.assertIn("輸入框把它當成了貼上的內容，清了。", messages.not_sent("claude_code.btw_pasted", cleared="empty"))
        self.assertIn("已經重送", messages.note("claude_code.resent"), "a wording that is not text is not one")
        self.assertEqual(messages.not_sent("claude_code.shell_mode"), messages.NOT_SENT["claude_code.shell_mode"][2])
        self.settings({})
        self.assertEqual(self.load(), (True, ""))
        self.assertEqual(messages.not_sent("claude_code.dialog"), default, "taken out of the settings: the default again")

    def test_a_wording_that_cannot_take_the_values_is_said_as_it_stands(self):
        self.settings({"dcbot": {"text": {"not_sent.claude_code.owners_text": "框裡有字 {nothing_of_that_name} {"}}})
        self.load()
        self.assertEqual(messages.not_sent("claude_code.owners_text", text="half"), "框裡有字 {nothing_of_that_name} {")

    def test_another_language_when_the_table_has_it(self):
        self.settings({"core": {"language": "xx"}})
        self.load()
        key = "dcbot.not_sent.claude_code.dialog"
        self.assertEqual(messages.not_sent("claude_code.dialog"), messages.NOT_SENT["claude_code.dialog"][2], "no such language: the default's")
        with mock.patch.dict(alt_text.TEXT, {"xx": {key: "a dialog is open"}}):
            self.assertEqual(messages.not_sent("claude_code.dialog"), "a dialog is open")
            self.assertEqual(messages.not_sent("claude_code.shell_mode"), messages.NOT_SENT["claude_code.shell_mode"][2])

    def test_the_talk_limit_between_claudes(self):
        """dcbot.talk_limit is read here too (off the event loop): a whole number of 0 or more, else none said."""
        from bot import posts
        self.settings({"dcbot": {"talk_limit": 30}})
        self.load()
        self.assertEqual((messages.loaded["talk_limit"], posts.default_limit()), (30, 30))
        self.settings({"dcbot": {"talk_limit": 0}})
        self.load()
        self.assertEqual(posts.default_limit(), 0, "0: no limit")
        for bad in ("30", True, -1, 2.5, None, [10]):
            self.settings({"dcbot": {"talk_limit": bad}})
            self.load()
            self.assertEqual(posts.default_limit(), posts.LOOP_LIMIT, bad)
        self.settings({"dcbot": {"talk_limit": 30}})
        self.load()
        self.settings("{broken")
        self.assertEqual(self.load()[0], True)
        self.assertEqual(posts.default_limit(), posts.LOOP_LIMIT, "settings that cannot be read: the default")

    def test_the_file_is_read_again_only_when_it_changed(self):
        self.settings({"dcbot": {"text": {"not_sent.claude_code.dialog": "一"}}})
        self.assertTrue(self.load()[0])
        with mock.patch.object(alt_text.config, "setting", side_effect=AssertionError("read again")):
            self.assertEqual(self.load(), (False, ""))
        self.settings({"dcbot": {"text": {"not_sent.claude_code.dialog": "二二"}}})
        self.assertTrue(self.load()[0])
        self.assertEqual(messages.not_sent("claude_code.dialog"), "二二")
        os.remove(self.path)
        self.assertTrue(self.load()[0])
        self.assertEqual(messages.not_sent("claude_code.dialog"), messages.NOT_SENT["claude_code.dialog"][2])
        self.assertEqual(self.load(), (False, ""), "no settings file: nothing to read, and not read again")

    def test_settings_that_cannot_be_read_are_the_default_words_and_one_line(self):
        self.settings({"dcbot": {"text": {"not_sent.claude_code.dialog": "一"}}})
        self.load()
        self.settings("{ this is not JSON")
        read, logged = self.load()
        self.assertTrue(read)
        self.assertEqual(len(logged.splitlines()), 1, logged)
        self.assertIn("messages: the installation's settings cannot be read", logged)
        self.assertEqual(messages.not_sent("claude_code.dialog"), messages.NOT_SENT["claude_code.dialog"][2], "the default words, not the last ones read")
        self.assertEqual(self.load(), (False, ""), "not read again, nor said again, until the file changes")

    def test_a_sentence_takes_no_time_while_the_settings_are_broken(self):
        """The engine waits 0.4 s on a broken settings file before it gives up; that was in the event loop, once
        every five seconds. Reading is load()'s, in a thread; taking a sentence is a few lookups."""
        self.settings("{ this is not JSON")
        began = time.perf_counter()
        for _ in range(200):
            self.assertIn("沒有送出", messages.not_sent("claude_code.owners_text", text="half"))
        each = (time.perf_counter() - began) / 200
        self.assertLess(each, 0.005, "%.4f s a sentence" % each)
        began = time.perf_counter()
        self.load()
        self.assertGreater(time.perf_counter() - began, 0.1, "the wait is there, and it is load()'s")


class Told(unittest.TestCase):
    """The checks before typing say nothing themselves: they come to a Typing, and told() says it."""

    def told(self, r):
        from bot import tmux_bridge
        said = []

        async def say(body):
            said.append(body)
        import asyncio
        return asyncio.run(tmux_bridge.TmuxBridge.told(None, say, r)), said

    def test_what_was_noted_along_the_way_first_then_why_it_was_not_sent(self):
        from bot import tmux_bridge
        r = tmux_bridge.Typing()
        self.assertTrue(r)
        self.assertEqual((r.state, r.code), (None, None))
        self.assertEqual(self.told(r), (True, []))
        r.note("claude_code.resent")
        self.assertEqual(self.told(r), (True, [messages.note("claude_code.resent")]))
        self.assertFalse(r.refuse("claude_code.owners_text", text="half"), "False: what a check returns as it stops")
        self.assertFalse(r)
        self.assertEqual((r.state, r.code, r.data), ("not_sent", "claude_code.owners_text", {"text": "half"}))
        self.assertEqual(self.told(r), (False, [messages.not_sent("claude_code.owners_text", text="half")]), "the note was said above")
        r = tmux_bridge.Typing()
        r.note("claude_code.resent")
        r.refuse("claude_code.owners_text", text="half")
        self.assertEqual(self.told(r), (False, [messages.note("claude_code.resent"), messages.not_sent("claude_code.owners_text", text="half")]))

    def test_a_note_is_said_once_however_often_the_same_result_is_told(self):
        """submit() and btw_ask() tell what may_type() came to, and tell the same Typing again when the message
        fails after it was typed."""
        from bot import tmux_bridge
        r = tmux_bridge.Typing()
        r.note("claude_code.resent")
        self.assertEqual(self.told(r), (True, [messages.note("claude_code.resent")]))
        r.refuse("claude_code.box_differs")
        self.assertEqual(self.told(r), (False, [messages.not_sent("claude_code.box_differs")]))
        self.assertEqual(self.told(r), (False, [messages.not_sent("claude_code.box_differs")]), "the refusal is what it came to: said whenever asked")

    def bridge(self):
        from bot import tmux_bridge
        from tests.test_turn_end import Ch, Host
        ch = Ch()
        return tmux_bridge.TmuxBridge(Host(ch)), ch

    def test_sent_again_is_said_once_when_this_one_then_fails_after_it_was_typed(self):
        """#code-review on 3f8e783, through submit(): the one before sent again, then this one typed and the box
        not what was typed. main said "sent again" once; 3f8e783 twice."""
        from bot import tmux_bridge
        from tests import fakepane
        bridge, ch = self.bridge()

        async def noted(st, name, rt):
            rt.outcome = r = tmux_bridge.Typing()
            r.note("claude_code.resent")
            return r

        class Message(object):
            channel, id, content = ch, 1, "second"

        async def go():
            nothing = mock.AsyncMock(return_value=None)
            with fakepane.guard(), mock.patch.object(bridge, "ensure", nothing), mock.patch.object(bridge, "may_type", noted), \
                    mock.patch.object(bridge, "enter", nothing), mock.patch.object(bridge, "note_paste", nothing), \
                    fakepane.replace("type_text", nothing), mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0):
                await bridge.submit(Message(), "second")
        import asyncio
        asyncio.run(go())
        self.assertEqual([m.content for m in ch.sent], [messages.note("claude_code.resent"), messages.not_sent("claude_code.box_differs")])
        self.assertEqual((bridge.rt(ch).outcome.state, bridge.rt(ch).outcome.code), ("typed_not_entered", "claude_code.box_differs"))

    def test_sent_again_is_said_even_when_the_checks_then_fail_with_an_error(self):
        """Manager on 3f8e783: main said it the moment the sending again had worked; a message that did go must not
        go unsaid because tmux failed a moment later."""
        from bot import tmux_bridge
        bridge, ch = self.bridge()
        said = []

        async def say(body):
            said.append(body)

        async def breaks(st, name, rt):
            rt.outcome = r = tmux_bridge.Typing()
            r.note("claude_code.resent")
            raise OSError("tmux is gone")

        async def go():
            with mock.patch.object(bridge, "may_type", breaks):
                await bridge.checked(say, {}, "s", bridge.rt(ch))
        import asyncio
        with self.assertRaises(OSError):
            asyncio.run(go())
        self.assertEqual(said, [messages.note("claude_code.resent")])

    def test_the_round_of_events_does_not_wait_for_the_settings_to_be_read(self):
        """The reading is a thread's (a broken file is waited for there); pump_once() only starts it, one at a time."""
        import asyncio
        import threading
        bridge, ch = self.bridge()
        release, calls = threading.Event(), []

        def slow():
            calls.append(1)
            release.wait(5)

        async def go():
            with mock.patch.object(messages, "load", slow):
                began = time.perf_counter()
                first = bridge.read_words()
                again = bridge.read_words()
                took = time.perf_counter() - began
                await asyncio.sleep(0.05)
                self.assertFalse(first.done())
                release.set()
                await first
                third = bridge.read_words()
                await third
                return first is again, took, third is first
        same, took, reused = asyncio.run(go())
        self.assertTrue(same, "one reading at a time")
        self.assertLess(took, 0.05)
        self.assertEqual(len(calls), 2, "and a new one once the last is done")
        self.assertFalse(reused)
        import inspect
        from bot import tmux_bridge
        source = inspect.getsource(tmux_bridge.TmuxBridge.pump_round)  # pump_once() times it (C-73)
        self.assertIn("self.read_words()", source)
        self.assertNotIn("await self.read_words()", source)

    def test_the_checks_take_nothing_to_speak_with(self):
        import inspect
        from bot import tmux_bridge
        from altention.connectors.claude_code import typist
        for name in ("may_type", "enter"):
            self.assertNotIn("say", inspect.signature(getattr(tmux_bridge.TmuxBridge, name)).parameters, name)
        for name in ("may_type", "leave_list", "resend", "clear_leftover", "enter"):  # (the connector's since 2.7b-1)
            self.assertNotIn("say", inspect.signature(getattr(typist, name)).parameters, name)


if __name__ == "__main__":
    unittest.main()
