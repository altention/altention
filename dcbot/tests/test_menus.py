"""The buttons of a numbered menu (what reads the menu off the screen is the connector's: its tests are in
altention/tests/connectors/claude_code/test_panels.py since stage 3b step 1.3)."""
import unittest

from bot import menus
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Menus(unittest.TestCase):
    def test_buttons(self):
        v = menus.Menus(None).view("77", [(1, "Yes " * 40), (2, "No")])
        self.assertEqual([b.custom_id for b in v.children], ["key:77:1", "key:77:2", "key:77:text", "key:77:enter", "key:77:esc"])
        self.assertLessEqual(len(v.children[0].label), 80)



class Pressed(unittest.TestCase):
    """F3 (screen-change-impact.md, S9): a dialog's button presses its key only onto the dialog it was made for: read
    again first, the same choices and excerpt, or nothing is pressed and the message says so."""

    def setUp(self):
        import asyncio
        from unittest import mock
        from bot import tmux_bridge
        from tests import fakepane
        from tests.test_turn_end import Ch, Host
        from altention.tests.connectors.claude_code import samples
        self.asyncio, self.mock, self.fakepane = asyncio, mock, fakepane
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.bridge.host.chan(self.ch)["tmux"] = "s"
        self.menus = self.bridge.menus
        self.first = samples.screen("permission")
        self.views, self.pressed, self.edits = [], [], []
        edits = self.edits

        class Message(object):
            id = 5

            async def edit(self, content=None, view=None):
                edits.append((content, view))

        async def send(content, view=None, **kw):
            return Message()
        self.ch.send = send
        self.ch.get_partial_message = lambda mid: Message()

    def screens(self):
        async def raw_screen(t):
            return self.views.pop(0) if len(self.views) > 1 else self.views[0]

        async def keys(t, *names):
            self.pressed.extend(names)
        return self.fakepane.use(raw_screen=raw_screen, keys=keys, alive=self.mock.AsyncMock(return_value=True))

    def post(self):
        self.views = [self.first]
        with self.screens(), self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()):
            self.asyncio.run(self.menus.post(self.ch, self.bridge.host.chan(self.ch), {"message": "Claude needs your permission"}))
        return next(iter(self.menus.open))

    def press(self, token, what, views):
        test = self

        class Response(object):
            async def defer(self):
                pass

            async def edit_message(self, view=None, content=None):
                test.edits.append((content, view))

            async def send_message(self, text, ephemeral=False):
                test.edits.append((text, None))

        class Click(object):
            channel = self.ch
            response = Response()
        self.views = list(views)
        with self.screens(), self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()), \
                self.mock.patch.object(self.bridge.host, "audit", self.mock.AsyncMock(), create=True):
            self.asyncio.run(self.menus.act(Click(), token, what))

    def test_the_same_dialog_still_there_gets_the_key(self):
        token = self.post()
        self.press(token, "1", [self.first, self.first])
        self.assertEqual(self.pressed, ["1"])

    def test_another_dialog_in_its_place_gets_nothing(self):
        from altention.tests.connectors.claude_code import samples
        other = samples.screen("permission", "colour-80x24")
        self.assertNotEqual(other, self.first)
        for what in ("1", "2", "enter"):
            self.menus.open.clear()
            token = self.post()
            self.pressed.clear()
            self.press(token, what, [other])
            self.assertEqual(self.pressed, [], what)
            self.assertIn("選單已經變了，沒有送出", self.edits[-1][0])
            self.assertIn(token, self.menus.open, "still a dialog: its buttons are made anew for it")
            self.press(token, what, [other, other])
            self.assertEqual(self.pressed, [what if what != "enter" else "Enter"], "pressed again: the dialog shown now gets it")

    def test_the_same_choices_for_another_command_get_nothing(self):
        """(the manager: two permission dialogs in a row offer the same choices; the command is what tells them apart)"""
        self.assertIn("touch sample-file", self.first)
        other = self.first.replace("touch sample-file", "rm -rf sample-dir")
        from altention.connectors.claude_code import panels
        self.assertEqual(panels.parse_menu(other)[1], panels.parse_menu(self.first)[1], "the same choices")
        token = self.post()
        self.press(token, "1", [other])
        self.assertEqual(self.pressed, [])
        self.assertIn("選單已經變了", self.edits[-1][0])

    def test_the_same_question_with_new_output_above_gets_the_key(self):
        """What runs above the dialog (output, the timer) is not the question: the key goes."""
        self.assertIn("All done. Nothing else to report.", self.first)
        moved = self.first.replace("All done. Nothing else to report.", "Another line printed meanwhile.").replace("for 14s", "for 21s")
        self.assertNotEqual(moved, self.first)
        token = self.post()
        self.press(token, "1", [moved, moved])
        self.assertEqual(self.pressed, ["1"])

    def test_its_machine_not_answering_the_read_keeps_the_buttons(self):
        """(#code-review, 42bbbfa) alive() said yes, then the read again got no answer: an empty screen. Not "the menu
        changed": nothing pressed, the buttons and the dialog's record stay, the owner is told its machine did not answer."""
        from bot import tmuxio
        token = self.post()
        self.bridge.host.chan(self.ch)["tmux"] = {"host": "lap", "socket": "s", "id": "%1"}
        told, answers = [], iter([True, False])

        async def alive(t):
            ok = next(answers)
            tmuxio.asked(t, ok)
            return ok

        async def keys(t, *names):
            self.pressed.extend(names)

        class Followup(object):
            async def send(self, text, ephemeral=False):
                told.append((text, ephemeral))

        class Response(object):
            async def defer(self):
                pass

        class Click(object):
            channel = self.ch
            response = Response()
            followup = Followup()
        self.addCleanup(tmuxio.UNANSWERED.clear)
        with self.fakepane.use(raw_screen=self.mock.AsyncMock(return_value=""), keys=keys, alive=alive), \
                self.mock.patch("bot.remote.unreachable", lambda host: "%s 沒有回應" % host), \
                self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()), \
                self.mock.patch.object(self.bridge.host, "audit", self.mock.AsyncMock(), create=True):
            self.asyncio.run(self.menus.act(Click(), token, "1"))
        self.assertEqual(self.pressed, [])
        self.assertIn(token, self.menus.open, "the buttons stay")
        self.assertEqual(len(told), 1)
        self.assertTrue(told[0][1], "only to the one who pressed")
        self.assertIn("沒有送出", told[0][0])
        self.assertIn("lap 沒有回應", told[0][0])
        self.assertFalse(any("選單已經變了" in (c or "") for c, _ in self.edits))

    def test_what_a_dialog_asks(self):
        from bot import menus
        from altention.tests.connectors.claude_code import samples
        said = menus.asked(self.first)
        self.assertTrue(said.startswith("Bash command"), said)
        self.assertIn("touch sample-file", said)
        self.assertIn("Do you want to proceed?", said)
        self.assertNotIn("All done", said)
        self.assertTrue(menus.asked(samples.screen("model-panel")).startswith("Select model"))
        self.assertEqual(menus.asked("no dialog here\n❯ "), "")

    def test_the_dialog_gone_gets_nothing_and_the_buttons_go(self):
        from altention.tests.connectors.claude_code import samples
        token = self.post()
        self.press(token, "1", [samples.screen("idle-fresh")])
        self.assertEqual(self.pressed, [])
        self.assertIn("選單已經變了，沒有送出", self.edits[-1][0])
        self.assertIsNone(self.edits[-1][1], "no dialog any more: no buttons")
        self.assertNotIn(token, self.menus.open)

    def countdown(self, name):
        from altention.tests.connectors.claude_code import samples
        return samples.extra("P", name, version="2.1.302")

    def test_a_dialog_counting_down_to_its_own_denial_gets_the_key(self):
        """Manager 2026-10-18 (a remote channel, Claude Code 2.1.302): the countdown row changes every second; it is not
        what the dialog asks, so the key goes (real screens: one read when the buttons were made, one at the click;
        80 columns wrap the row)."""
        for which in ("colour-120x50", "colour-80x24", "nocolor-80x24"):
            with self.subTest(which=which):
                a, b = self.countdown("countdown-%s-a" % which), self.countdown("countdown-%s-b" % which)
                self.assertEqual(menus.asked(a), menus.asked(b))
                self.assertIn("rm -rf ~", menus.asked(a))
                self.menus.open.clear()
                self.pressed.clear()
                self.first = a
                token = self.post()
                self.press(token, "2", [b, b])
                self.assertEqual(self.pressed, ["2"])

    def test_the_deadline_is_said_and_after_it_nothing_is_pressed(self):
        import re
        import time
        self.first = self.countdown("countdown-colour-120x50-a")
        self.edits.clear()
        sent = []

        async def send(content, view=None, **kw):
            sent.append(content)
            return type("M", (), {"id": 5})()
        self.ch.send = send
        token = self.post()
        due = re.search(r"<t:(\d+):R> 自動拒絕", sent[-1])
        self.assertIsNotNone(due, sent[-1])
        self.assertAlmostEqual(int(due.group(1)), time.time() + 118, delta=5)
        self.assertNotIn("automatically deny", sent[-1], "a time read once would be wrong a second later")
        self.menus.open[token]["deadline"] = int(time.time()) - 1  # it ran out
        self.press(token, "1", [self.countdown("auto-denied-nocolor-80x24")])
        self.assertEqual(self.pressed, [], "nothing lands in the input box")
        self.assertIn("期限到了，Claude Code 已經自動拒絕", self.edits[-1][0])
        self.assertIsNone(self.edits[-1][1])

    def test_without_a_deadline_nothing_is_said_of_one(self):
        sent = []

        async def send(content, view=None, **kw):
            sent.append(content)
            return type("M", (), {"id": 5})()
        self.ch.send = send
        token = self.post()
        self.assertNotIn("自動拒絕", sent[-1])
        from altention.tests.connectors.claude_code import samples
        self.press(token, "1", [samples.screen("idle-fresh")])
        self.assertIn("選單已經變了", self.edits[-1][0], "gone before any deadline: said as before")

    def test_escape_is_left_as_it_was(self):
        """Escape already goes only onto a panel that is open (tmuxio.escape with screen.panel_shown)."""
        from altention.tests.connectors.claude_code import samples
        token = self.post()
        self.press(token, "esc", [samples.screen("idle-fresh")])
        self.assertEqual(self.pressed, [])


if __name__ == "__main__":
    unittest.main()
