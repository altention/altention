"""Buttons from a panel's hint line (the connector's panels.py, the generic layer): only the keys the line names, never numbers;
an Escape goes through the gate. The hint lines are real ones, captured from Claude Code on 2026-10-06."""
import asyncio
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.tests.connectors.claude_code.test_panels import MODEL, RULE, panel
from bot import menus, tmuxio
from tests import fakepane
from tests.test_turn_end import Ch, Host


class Buttons(unittest.TestCase):
    """Menus.show_panel and a click on one of its buttons, with a fake pane and a fake Discord."""

    def setUp(self):
        tmuxio._esc_at.clear()
        self.ch = Ch()
        self.host = Host(self.ch)
        self.host.audits = []

        async def audit(text):
            self.host.audits.append(text)
        self.host.audit = audit

        class Bridge(object):
            host = self.host
        self.m = menus.Menus(Bridge())
        self.sent = []

    def run_with(self, screens, coro):
        shown = list(screens)

        async def raw_screen(t):
            return shown.pop(0) if len(shown) > 1 else shown[0]

        async def keys(t, *names):
            self.sent.extend(names)

        with fakepane.use(raw_screen=raw_screen, keys=keys, alive=mock.AsyncMock(return_value=True)), \
                mock.patch.object(asyncio, "sleep", mock.AsyncMock()):
            return asyncio.run(coro)

    def show(self, screen):
        async def send(text, view=None):
            return await self.ch.send(text, view=view)
        return self.run_with([screen], self.m.show_panel(self.ch, "s", send, "/model"))

    def test_shown_with_one_button_per_hint_key(self):
        self.assertTrue(self.show(panel(" Select model", " ❯ 1. Default", "", MODEL)))
        labels = [b.label for b in self.ch.sent[-1].view.children]
        self.assertEqual(labels, ["↑ 上一個", "↓ 下一個", "Enter set as default", "s use this session only", "Esc cancel"])

    def test_no_panel_is_not_shown(self):
        self.assertFalse(self.show(panel(RULE, "❯ ", RULE)))
        self.assertEqual(self.ch.sent, [])

    def press(self, key, now, after):
        """A click on the button for `key`: the screen reads `now` when it is pressed, then `after`."""
        token = next(iter(self.m.open))
        m = self.m.open[token]

        class Interaction(object):
            channel = self.ch

        class Partial(object):
            async def edit(inner, content=None, view=None):
                self.edited = (content, view)
        self.ch.get_partial_message = lambda mid: Partial()
        self.run_with([now] + list(after), self.m.act_panel(Interaction(), token, m, "s", key))

    def test_button_ids_carry_the_key_not_a_position(self):
        self.show(panel(" Select model", "", MODEL))
        ids = [b.custom_id.split(":")[2] for b in self.ch.sent[-1].view.children]
        self.assertEqual(ids, ["kEnter", "ks", "kEscape"])

    def test_a_press_sends_that_key_and_the_panel_closing_drops_the_buttons(self):
        open_ = panel(" Select model", "", MODEL)
        self.show(open_)
        self.press("s", open_, [panel(RULE, "❯ ", RULE)])
        self.assertEqual(self.sent, ["s"])
        self.assertIsNone(self.edited[1], "the panel is gone: no buttons left")
        self.assertEqual(self.m.open, {})

    def test_nothing_is_sent_once_the_panel_is_gone(self):
        """A second click, or the panel closed at the terminal: a stale key would land on the session."""
        self.show(panel(" Select model", "", MODEL))
        self.press("s", panel(RULE, "❯ ", RULE), [])
        self.assertEqual(self.sent, [])
        self.assertIn("面板已經變了", self.edited[0])

    def test_nothing_is_sent_when_the_panel_now_offers_other_keys(self):
        self.show(panel(" Select model", "", MODEL))
        self.press("s", panel(" Another", "", "Enter to confirm · Esc to cancel"), [])
        self.assertEqual(self.sent, [])

    def test_nothing_is_sent_while_claude_works(self):
        self.show(panel(" Select model", "", MODEL))
        self.press("s", panel("✻ Thinking… (esc to interrupt)", RULE, "❯ ", RULE), [])
        self.assertEqual(self.sent, [])

    def test_escape_goes_through_the_gate(self):
        open_ = panel(" Select model", "", MODEL)
        self.show(open_)
        self.press("Escape", open_, [panel(RULE, "❯ ", RULE)])  # the gate reads again: closed by then
        self.assertEqual(self.sent, [])
        self.assertIn("沒有送 Esc", self.edited[0])


if __name__ == "__main__":
    unittest.main()
