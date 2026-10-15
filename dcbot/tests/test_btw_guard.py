"""/btw never sends what Claude Code would not take as /btw (a long line becomes a paste and goes to the main
conversation): too long is refused before typing, and the input box is read before Enter (2026-10-06)."""
import asyncio
import tempfile
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import btw, screen
from bot import cc_btw, tmux_bridge
from tests.test_turn_end import Ch, Host
from tests import fakepane

RULE = "─" * 60


def box(row):
    return "\n".join(["", RULE, row, RULE, "  ? for shortcuts", ""])


class BtwGuard(unittest.TestCase):
    def setUp(self):
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.typed, self.keys = [], []

    def run_ask(self, question, screens, pairs=()):
        async def ensure(*a, **k):
            pass

        async def no_panel(*a, **k):
            pass

        async def type_text(name, text):
            self.typed.append(text)

        async def keys(name, *names):
            self.keys.extend(names)

        shown = [box("❯ ")] + list(screens)  # the look before anything is typed: an empty box

        async def raw_screen(name):
            return shown.pop(0) if len(shown) > 1 else shown[0]
        st = self.bridge.host.chan(self.ch)
        st.setdefault("btw", {})[str(self.ch.id)] = {"pairs": [list(p) for p in pairs], "t0": 0}
        with fakepane.guard(), mock.patch.object(self.bridge, "ensure", ensure), mock.patch.object(self.bridge, "close_panel", no_panel), \
                fakepane.replace("type_text", type_text), fakepane.replace("keys", keys), \
                fakepane.replace("raw_screen", raw_screen), mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), \
                mock.patch.object(tmux_bridge, "BTW_SECS", 1), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.05):
            asyncio.run(self.bridge.btw_ask(self.ch, None, question))
        return self.ch.sent[-1].content

    def test_a_question_too_long_alone_is_refused_before_typing(self):
        said = self.run_ask("問" * 700, [box("❯ ")])
        self.assertIn("太長", said)
        self.assertEqual(self.typed, [])

    def test_taken_for_a_paste_is_cleared_and_never_sent(self):
        said = self.run_ask("q", [box("❯ [Pasted text #1]"), box("❯ [Pasted text #1]"), box("❯ ")])
        self.assertIn("沒有送出", said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.btw_pasted")
        self.assertIn("貼上的內容", said)
        self.assertNotIn("Enter", self.keys)
        self.assertIn("C-u", self.keys, "the input box was cleared")

    def test_what_was_noted_before_the_typing_is_said_once(self):
        """#code-review on 3f8e783: told() is called for what may_type() came to and again when the /btw then fails
        (taken for a paste, not a /btw line): "sent again" would have been posted twice."""
        from bot import messages
        real = self.bridge.may_type

        async def noted(st, name, rt):
            r = await real(st, name, rt)
            r.note("claude_code.resent")
            return r
        for screens, code in (([box("❯ [Pasted text #1]"), box("❯ [Pasted text #1]"), box("❯ ")], "claude_code.btw_pasted"),
                              ([box("❯ something else"), box("❯ something else"), box("❯ ")], "claude_code.btw_not_command")):
            self.ch.sent.clear()
            with mock.patch.object(self.bridge, "may_type", noted):
                self.run_ask("q", screens)
            said = "\n".join(m.content for m in self.ch.sent)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, code)
            self.assertEqual(said.count(messages.note("claude_code.resent")), 1, (code, said))
            self.assertEqual(said.count("沒有送出"), 1, (code, said))

    def test_a_fresh_thread_counts_its_preamble_in_the_length_it_reports(self):
        self.bridge.rt(self.ch).btw_from = 999  # the last /btw came from another thread
        said = self.run_ask("問" * 540, [box("❯ ")])
        self.assertIn("一共 %d 字" % (540 + len("（這是新的旁支問題，和先前其他 /btw 的問答無關）")), said)

    def test_clearing_stops_when_presses_change_nothing(self):
        said = self.run_ask("q", [box("❯ [Pasted text #1]"), box("❯ 灰色的建議文字")])
        self.assertNotIn("已經清掉", said, "said as it is: the screen stopped changing, not that the box is empty")
        self.assertIn("畫面不再變化", said)
        self.assertLessEqual(self.keys.count("C-u"), 4)

    def test_btw_gone_from_the_start_is_not_sent_either(self):
        said = self.run_ask("q", [box("❯ 問問問"), box("❯ ")])
        self.assertIn("開頭不是 /btw", said)
        self.assertNotIn("Enter", self.keys)

    def test_a_proper_btw_line_is_sent_and_a_missing_answer_said_as_it_is(self):
        pairs = [["中文的問題" * 30, "很長的中文回答" * 100]] * 6
        said = self.run_ask("再問一個", [box("❯ /btw " + "問" * 100)], pairs)
        self.assertIn("Enter", self.keys)
        self.assertLessEqual(len(self.typed[0]), 5 + screen.BTW_MAX)
        self.assertIn("沒有看到答案框", said)


    def overlay(self, answer):
        return "\n".join(["", "▔" * 60, "", "    /btw q", "", "      " + answer, "", "    ↑/↓ to scroll · c to copy · Esc to close"])

    def answered(self, copied, clipboard):
        async def btw_copy(name):
            return copied

        async def option(t):
            return clipboard
        with mock.patch.object(self.bridge, "btw_copy", btw_copy), fakepane.replace("clipboard", option):
            return self.run_ask("q", [box("❯ /btw q"), self.overlay("畫面上的答案")])

    def test_an_answer_read_off_the_screen_says_so_in_one_line(self):
        """DESIGN 5.1 (2026-10-07): the line and the judging are the connector's (cc_btw), the core posts it."""
        said = self.answered(None, "external")
        self.assertIn("畫面上的答案", said)
        self.assertIn("-# " + cc_btw.screen_note(), said)
        self.assertNotIn("set-clipboard", said, "how to turn it on is told only when the owner asks")
        said = self.answered("| 完整 | 表格 |", "on")
        self.assertIn("| 完整 | 表格 |", said)
        self.assertNotIn(cc_btw.screen_note(), said)
        pairs = self.bridge.host.chan(self.ch)["btw"][str(self.ch.id)]["pairs"]
        self.assertNotIn(cc_btw.screen_note(), pairs[0][1], "the note is not part of the answer kept for follow-ups")

    def test_a_key_in_the_answer_is_masked_before_posting(self):
        """B-12 (the manager 2026-10-14): the answer is Claude's words, read off the screen or copied."""
        from bot import messages
        key = "sk-ant-" + "a" * 30  # made up, put together here
        said = self.answered("the key is %s" % key, "on")
        self.assertNotIn(key, said)
        self.assertIn("the key is [REDACTED]", said)
        self.assertIn(messages.note("masked", n=1), said)
        said = self.answered("an ordinary answer, commit aa79535", "on")
        self.assertIn("an ordinary answer, commit aa79535", said)
        self.assertNotIn(messages.note("masked", n=1), said)


class Clipboard(unittest.TestCase):
    def test_only_on_keeps_copies(self):
        self.assertTrue(btw.copies("on"))
        self.assertTrue(btw.copies("on\n"))
        for value in ("external", "off", "", None):
            self.assertFalse(btw.copies(value))

    def test_btw_copy_reads_the_screen_unless_the_server_keeps_copies(self):
        bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        pressed = []

        async def keys(name, *names):
            pressed.extend(names)

        async def option(t):
            return "external"

        async def buffers(t):
            return []
        with fakepane.replace("clipboard", option), fakepane.replace("keys", keys), \
                fakepane.replace("buffers", buffers):
            self.assertIsNone(asyncio.run(bridge.btw_copy({"host": None, "pane": "%1"})))
        self.assertEqual(pressed, [], "nothing pressed: the owner's server keeps no copies")


if __name__ == "__main__":
    unittest.main()


class PlainMessagesAlwaysGoOut(unittest.TestCase):
    """The path every channel shares: a message is typed and then Enter is pressed, whatever the screen shows.
    A check inserted there by mistake (2026-10-06, the /btw guard's first draft) must fail a test."""

    def test_typed_then_enter_whatever_the_screen(self):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        events_seen = []

        async def ensure(*a, **k):
            pass

        async def no_panel(*a, **k):
            pass

        async def type_text(name, text):
            events_seen.append(("typed", text))

        async def keys(name, *names):
            events_seen.extend(("key", n) for n in names)

        async def raw_screen(name):
            return box("❯ [Pasted text #1]")  # whatever it shows, a plain message is sent

        class Message(object):
            channel, id, content = ch, 1, "hello"

        for text in ("hello", "很長的訊息" * 300):
            events_seen.clear()
            with mock.patch.object(bridge, "ensure", ensure), mock.patch.object(bridge, "close_panel", no_panel), \
                    fakepane.replace("type_text", type_text), fakepane.replace("keys", keys), \
                    fakepane.replace("raw_screen", raw_screen), mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), \
                    mock.patch.object(tmux_bridge.config, "STATE", tempfile.mkdtemp()), \
                    mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.05), mock.patch.object(tmux_bridge.cc_typing, "CLEAR_PAUSE", 0):
                asyncio.run(bridge.submit(Message(), text))
            self.assertIn(("typed", text), events_seen)
            typed_at = events_seen.index(("typed", text))
            self.assertIn(("key", "Enter"), events_seen[typed_at:], "Enter follows the typing")
