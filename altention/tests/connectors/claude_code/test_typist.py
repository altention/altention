"""typist.py: what is sent to type a message into Claude Code, and at what pace (3b 2.7a-1; the bot's tmuxio turns
the steps into tmux's send-keys: dcbot/tests/test_tmux.py)."""
import unittest

from altention.connectors.claude_code import screen, typist
from altention.tests.connectors.claude_code import samples


class Steps(unittest.TestCase):
    def test_lines_and_stretches(self):
        self.assertEqual(typist.steps('say "hi" $HOME'), [("text", 'say "hi" $HOME')])
        self.assertEqual(typist.steps("a\r\nb\n\nc\n"), [("text", "a"), ("newline",), ("text", "b"), ("newline",), ("newline",), ("text", "c")])
        self.assertEqual(typist.steps("a\nb"), [("text", "a"), ("newline",), ("text", "b")])
        long = typist.steps("x" * 1200)
        self.assertEqual([len(s[1]) for s in long], [typist.TYPE_CHUNK, typist.TYPE_CHUNK, 200])
        self.assertLess(typist.TYPE_CHUNK, screen.PASTE_AT, "a stretch is well under what Claude Code takes for a paste")

    def test_what_claude_code_would_take_otherwise(self):
        self.assertEqual(typist.steps("#note"), [("text", " #note")])
        for first in "!#&?":
            self.assertTrue(typist.steps(first + "x")[0][1].startswith(" "), first)
        self.assertEqual(typist.steps("see @file"), [("text", "see @file ")])
        self.assertEqual(typist.steps("path\\"), [("text", "path\\ ")])
        self.assertEqual(typist.steps("a\tb\x1b[0m\x03"), [("text", "a    b[0m")])
        self.assertEqual(typist.steps("a;b;"), [("text", "a;b;")], "a terminal's own escapes are its sender's")

    def test_pace(self):
        paced = typist.paced(typist.steps("x" * 1200))
        self.assertEqual([s[0] for s in paced], ["text", "pause", "text", "pause", "text"])
        self.assertEqual(typist.paced(typist.steps("x" * typist.TYPE_CHUNK)), [("text", "x" * typist.TYPE_CHUNK)], "one stretch: no pause")
        lines = typist.paced(typist.steps("\n".join(["y" * 300] * 3)))
        self.assertEqual([s[0] for s in lines], ["text", "newline", "pause", "text", "newline", "pause", "text"])
        burst = 0
        for s in lines:
            if s[0] == "pause":
                burst = 0
            elif s[0] == "text":
                burst += len(s[1])
                self.assertLessEqual(burst, typist.TYPE_CHUNK, "counted across lines")


class Vim(unittest.TestCase):
    def test_what_the_settings_say(self):
        mode = '"editorMode": "%s"'
        self.assertFalse(typist.says_vim("DONE\n"))
        self.assertFalse(typist.says_vim(mode % "normal" + "\nDONE\n"))
        for out in (mode % "vim" + "\nDONE\n", mode % "Vim" + "\nDONE\n", "UNREADABLE\nDONE\n", "", mode % "normal"):
            self.assertTrue(typist.says_vim(out), out)
        self.assertIn("%s", typist.EDITOR_MODE, "the folder's own settings go in there")


class Pane(object):
    """A pane as the connector drives one: screens shown in turn (the last stays), keys recorded."""

    def __init__(self, *views, cursor=None, scrolling=False, vim=False):
        self.views, self.pressed, self.at, self.scrolling, self.vim = list(views), [], cursor, scrolling, vim

    async def raw_screen(self):
        return self.views.pop(0) if len(self.views) > 1 else self.views[0]

    async def keys(self, *names):
        self.pressed.extend(names)

    async def escape(self, needed):
        if not needed(await self.raw_screen()):
            return False
        self.pressed.append("Escape")
        return True

    async def cursor(self):
        return self.at

    async def in_mode(self):
        return self.scrolling

    async def vim_mode(self):
        return self.vim


def box(row):
    rule = "─" * 60
    return "\n".join(["", rule, row, rule, "  ? for shortcuts", ""])


def run(coro):
    import asyncio
    from unittest import mock
    with mock.patch.object(asyncio, "sleep", mock.AsyncMock()):
        return asyncio.run(coro)


class InTheBox(unittest.TestCase):
    """What is done to the input box around a message (3b 2.7a-2; the bot's own tests of the same, through its
    fake pane, are dcbot/tests/test_safety.py and the key baseline)."""

    def test_enter_only_on_the_ordinary_box(self):
        pane = Pane(box("❯ hello"))
        self.assertEqual(run(typist.enter(pane, "hello")), ("pressed", False))
        self.assertEqual(pane.pressed, ["Enter"])
        self.assertEqual(run(typist.enter(Pane(box("❯ ")), "hello")), ("empty", False))
        dialog = samples.screen("permission")
        pane = Pane(dialog)
        self.assertEqual(run(typist.enter(pane, "hello")), (None, False))
        self.assertEqual(pane.pressed, [])

    def test_enter_on_a_box_that_differs(self):
        pane = Pane(box("❯ hello and more"))
        self.assertEqual(run(typist.enter(pane, "hello")), ("pressed", True), "a session nobody else types at: pressed, and said")
        for others, again in ((True, False), (False, True)):
            pane = Pane(box("❯ hello and more"))
            self.assertEqual(run(typist.enter(pane, "hello", again=again, others_type=others)), (None, False))
            self.assertEqual(pane.pressed, [])

    def test_clear_input(self):
        pane = Pane(box("❯ /compact "), box("❯ /compact "), box("❯ "))
        self.assertEqual(run(typist.clear_input(pane)), "empty")
        self.assertEqual(pane.pressed, ["C-u", "C-u"])
        pane = Pane(box("❯ grey"))
        self.assertEqual(run(typist.clear_input(pane)), "unchanged")
        self.assertEqual(pane.pressed, ["C-u", "C-u"])

    def test_probe_presses_nothing_where_a_key_could_do_something_else(self):
        self.assertEqual(run(typist.probe(Pane(box("❯ x"), scrolling=True), box("❯ x"))), "scrolling")
        pane = Pane(box("❯ x"), vim=True)
        self.assertIsNone(run(typist.probe(pane, box("❯ x"))))
        self.assertEqual(pane.pressed, [])

    def test_close_panel_only_while_idle(self):
        panel = samples.screen("model-panel")
        pane = Pane(panel)
        self.assertTrue(run(typist.close_panel(pane, busy=lambda: True)))
        self.assertEqual(pane.pressed, [])
        pane = Pane(panel, panel, box("❯ "))   # (looked at, looked at again by the Escape, then gone)
        self.assertTrue(run(typist.close_panel(pane, busy=lambda: False)))
        self.assertEqual(pane.pressed, ["Escape"])

    def test_close_panel_stops_when_a_turn_begins_between_two_escapes(self):
        """#code-review on 44c7069: busy was read once, before the first Escape; it was read before each one."""
        panel = samples.screen("model-panel")
        pane, state = Pane(panel), {"busy": False}

        async def escape(needed):
            pane.pressed.append("Escape")
            state["busy"] = True   # a turn began while the box was coming back
            return True
        pane.escape = escape
        self.assertTrue(run(typist.close_panel(pane, busy=lambda: state["busy"])))
        self.assertEqual(pane.pressed, ["Escape"], "no second Escape once a turn runs")

    def test_taken_for_a_paste(self):
        long = "x" * screen.PASTE_AT
        self.assertTrue(typist.taken_for_a_paste(long, "[Pasted text #1 +3 lines]"))
        self.assertFalse(typist.taken_for_a_paste(long, long[:80]))
        self.assertFalse(typist.taken_for_a_paste("short", "[Pasted text #1 +3 lines]"), "only a long line is asked of")
        said = long + " [Pasted text #1]"
        self.assertFalse(typist.taken_for_a_paste(said, "… [Pasted text #1]"), "the marks the text itself spells out")


class Memory(object):
    def __init__(self, entered=None, left=None, typed=(), own_panel=None):
        self.entered, self.left, self.typed, self.own_panel = entered, left, list(typed), own_panel


class Colour(Pane):
    """A pane whose colours say the box's text is typed (not grey)."""

    async def color_screen(self):
        view = self.views[0]
        return view.replace("❯ ", "❯ \x1b[39m"), 120

    async def type_text(self, text):
        self.pressed.append(("typed", text))


class Checks(unittest.TestCase):
    """The checks before typing (3b 2.7b-1; the bot's own tests of them, through its fake pane and the key baseline,
    are dcbot/tests/test_safety.py, test_parked.py and test_keys_same.py)."""

    def check(self, pane, memory=None, **policy):
        memory = memory or Memory()
        return run(typist.may_type(pane, memory, typist.Policy(**policy))), memory

    def test_a_dialog_or_the_shell_mode_gets_no_key(self):
        for state, code in (("permission", "claude_code.dialog"),):
            pane = Pane(samples.screen(state))
            r, _ = self.check(pane)
            self.assertEqual((bool(r), r.state, r.code), (False, "not_sent", code))
            self.assertEqual(pane.pressed, [])

    def test_an_empty_box_goes_and_forgets_what_was_left(self):
        memory = Memory(entered="old", left="older", own_panel=True)
        r, memory = self.check(Pane(box("❯ ")), memory)
        self.assertTrue(r)
        self.assertEqual((memory.entered, memory.left, memory.own_panel), (None, None, None))

    def test_a_leftover_in_a_session_nobody_else_uses_is_cleared(self):
        pane = Pane(box("❯ /compact "), box("❯ /compact "), box("❯ "))
        r, _ = self.check(pane)
        self.assertTrue(r)
        self.assertEqual(pane.pressed, ["C-u"])

    def test_text_where_others_type_is_left_alone(self):
        pane = Colour(box("❯ half typed"))
        r, _ = self.check(pane, others_type=True)
        self.assertEqual((r.code, r.data), ("claude_code.owners_text", {"text": "half typed"}))
        self.assertEqual(pane.pressed, [])
        pane = Colour(box("❯ /compact"))
        r, _ = self.check(pane, Memory(typed=["/compact"]), others_type=True)
        self.assertTrue(r, "the slash command typed there last: cleared")

    def test_the_message_before_still_in_the_box(self):
        pane = Pane(box("❯ the one before"))
        with self.subTest("others type there: said, no key"):
            r, _ = self.check(pane, Memory(entered="the one before"), others_type=True)
            self.assertEqual(r.code, "claude_code.previous_entered")
            self.assertEqual(pane.pressed, [])
        # a session nobody else uses: it stays through every look, is cleared, typed again, and leaves the box
        pane = Colour(box("❯ "))
        pane.views = [box("❯ the one before")] * (typist.GONE_LOOKS + 2) + [box("❯ ")] * 2 + [box("❯ the one before")] * 2 + [box("❯ ")]
        r, _ = self.check(pane, Memory(entered="the one before"), receipt_secs=0.2, submit_delay=0)
        self.assertTrue(r)
        self.assertEqual(r.notes, [("claude_code.resent", {})])
        self.assertIn(("typed", "the one before"), pane.pressed)

    def test_the_agents_list(self):
        listed = samples.agents("list-one-row", "colour-120x50")
        pane = Pane(listed)
        r, _ = self.check(pane, others_type=True, bot_window=lambda: False)
        self.assertEqual(r.code, "claude_code.agents_list")
        self.assertEqual(pane.pressed, [])
        typed = samples.agents("list-typed", "colour-120x50")
        r, _ = self.check(Pane(typed))
        self.assertEqual(r.code, "claude_code.list_has_text")

    def test_what_changes_while_the_checks_wait_is_asked_when_it_matters(self):
        """#code-review on 8278773: parked and events_down were read once, before seconds of waiting; before the move
        they were read where they were used. A conversation moved to the background while the checks waited, the pane
        then showing one opened from the list: refused, as before."""
        waiting = samples.agents("left-while-busy-waiting", "colour-120x50")
        opened = samples.agents("opened-from-list", "colour-120x50")
        moved = {"now": False}

        class Waits(Pane):
            async def raw_screen(self):
                view = await Pane.raw_screen(self)
                if view is opened:
                    moved["now"] = True   # the event saying so was handled while the checks waited
                return view
        pane = Waits(waiting, waiting, opened)
        r, _ = self.check(pane, parked=lambda: moved["now"])
        self.assertEqual(r.code, "claude_code.other_conversation")
        closes, down = [], {"now": True}
        panel = samples.screen("model-panel")

        class Back(Pane):
            async def raw_screen(self):
                view = await Pane.raw_screen(self)
                down["now"] = len(self.views) > 2   # the stream comes back after the first look
                return view

        async def close():
            closes.append(1)
            return True
        self.check(Back(panel, panel, panel, box("❯ ")), Memory(own_panel=True), events_down=lambda: down["now"], close_panel=close)
        self.assertEqual(closes, [1], "the panel the bot's own command opened is closed once the stream is back")

    def test_send_now_only_on_the_hint(self):
        pane = Pane(box("❯ "))
        self.assertFalse(run(typist.send_now(pane, lambda: False)))
        self.assertEqual(pane.pressed, [])
        self.assertFalse(run(typist.send_now(Pane(box("❯ ")), lambda: True)))


if __name__ == "__main__":
    unittest.main()
