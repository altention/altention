"""Safety fixes on the path every channel shares (2026-10-06, docs/refactor/interaction-inventory.md section 5):
- an Escape the bot decides on is never sent onto a screen that does not need it, nor within ESC_GAP of the last
  one (two in a row on an empty input box open Rewind);
- whatever the input box still holds is cleared before typing in the bot's own sessions (an interrupted /compact
  leaves "/compact " there), but in an attached pane only when it is the command the bot typed last;
- /logout is refused, and the commands that take the session out of its pane, end it or rewind it ask first."""
import asyncio
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import screen
from altention.connectors.claude_code import typist as cc_typing  # noqa: E402
from bot import tmux_bridge, tmuxio
from tests import fakepane
from tests.fakepane import RULE, Screens, box
from tests.test_turn_end import Ch, Host

PANEL = "\n".join(["", " Select model", " ❯ 1. Default", "   2. Opus", "", " Enter to confirm · Esc to cancel", ""])
REDRAWING = "\n".join(["", " …", ""])  # neither a panel's hint nor the input box: between two frames


def run(coro, fake, *more):
    quick = [mock.patch.object(tmux_bridge.cc_typing, "CLEAR_PAUSE", 0), mock.patch.object(tmux_bridge.cc_typing, "BOX_PAUSE", 0.01), mock.patch.object(tmux_bridge.cc_typing, "PANEL_PAUSE", 0.01),
             # what the bot notes for doctor (typed-as-paste, enter-differs) never goes into the real state folder
             mock.patch.object(tmux_bridge.config, "STATE", tempfile.mkdtemp(prefix="state "))]
    with fakepane.use(fake):  # also: a real tmux or ssh started from here on fails the test
        started = []
        try:
            for p in quick + list(more):
                p.start()
                started.append(p)
            return asyncio.run(coro)
        finally:
            for p in reversed(started):
                p.stop()


class Escape(unittest.TestCase):
    def setUp(self):
        tmuxio._esc_at.clear()

    def test_not_sent_when_nothing_needs_it(self):
        fake = Screens([box("❯ ")])
        self.assertFalse(run(tmuxio.escape("s", screen.panel_shown), fake))
        self.assertEqual(fake.escapes(), 0)

    def test_a_second_one_waits_the_gap_and_is_dropped_if_the_panel_is_gone(self):
        fake = Screens([PANEL, PANEL, box("❯ ")])
        with fakepane.replace("ESC_GAP", 0.1):
            self.assertTrue(run(tmuxio.escape("s", screen.panel_shown), fake))
            t0 = time.monotonic()
            self.assertFalse(run(tmuxio.escape("s", screen.panel_shown), fake), "the caller learns it was dropped")
        self.assertGreaterEqual(time.monotonic() - t0, 0.08)
        self.assertEqual(fake.escapes(), 1)

    def test_a_second_one_still_needed_after_the_gap_goes(self):
        fake = Screens([PANEL])
        with fakepane.replace("ESC_GAP", 0.1):
            run(tmuxio.escape("s", screen.panel_shown), fake)
            t0 = time.monotonic()
            self.assertTrue(run(tmuxio.escape("s", screen.panel_shown), fake))
        self.assertGreaterEqual(time.monotonic() - t0, 0.08, "never two within the gap")
        self.assertEqual(fake.escapes(), 2)

    def test_the_owners_own_escape_counts_for_the_gap(self):
        fake = Screens([PANEL])
        with fakepane.replace("ESC_GAP", 0.1):
            run(fake.keys("s", "esc"), fake)  # as `!key esc` does through tmuxio.keys
            t0 = time.monotonic()
            run(tmuxio.escape("s", screen.panel_shown), fake)
        self.assertGreaterEqual(time.monotonic() - t0, 0.08)

    def test_close_panel_waits_for_the_box_before_looking_again(self):
        """The case found on 2026-10-06: after the first Escape the box was not redrawn yet, the screen looked
        like a panel, and a second Escape 0.7 s later landed on an empty box."""
        fake = Screens([PANEL, PANEL, REDRAWING, REDRAWING, box("❯ ")])
        bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        with fakepane.replace("ESC_GAP", 0.1):
            self.assertTrue(run(bridge.close_panel({}, "s"), fake))
        self.assertEqual(fake.escapes(), 1)

    def test_close_panel_leaves_a_busy_session_alone(self):
        fake = Screens([PANEL])
        bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        run(bridge.close_panel({"busy": True}, "s"), fake)
        self.assertEqual(fake.escapes(), 0)


class Submit(unittest.TestCase):
    """submit() with a fake pane: what is typed, and in which order."""

    def setUp(self):
        tmuxio._esc_at.clear()
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))

    def send(self, text, screens, pane=False, typed_last=None, color=None):
        st = self.bridge.host.chan(self.ch)
        if pane:
            st["pane"] = {"socket": "/tmp/x", "id": "%1"}
        if typed_last:
            self.bridge.rt(self.ch).typed = [typed_last]
        fake = Screens(screens, color, echo=True)

        async def nothing(*a, **k):
            pass

        class Message(object):
            channel, id, content = self.ch, 1, text
        run(self.bridge.submit(Message(), text), fake, mock.patch.object(self.bridge, "ensure", nothing),
            mock.patch.object(self.bridge, "close_panel", nothing), mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0),
            mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01))
        return fake.log, "\n".join(m.content for m in self.ch.sent)

    def test_an_own_session_has_a_leftover_cleared_before_typing(self):
        log, _ = self.send("hello", [box("❯ /compact "), box("❯ /compact "), box("❯ ")])
        self.assertIn(("key", "C-u"), log)
        self.assertLess(log.index(("key", "C-u")), log.index(("typed", "hello")))
        self.assertIn(("key", "Enter"), log[log.index(("typed", "hello")):])

    def test_the_next_message_waits_for_the_receipt_of_this_one(self):
        """C-73: the channel's lock is held from typing to receipt. It was let go at Enter: the next was typed while
        this one's receipt came, and that cleared rt.entered under it (a message lost without a word)."""
        fake = Screens([box("❯ ")], echo=True)

        async def nothing(*a, **k):
            pass

        def message(text):
            return type("M", (), {"channel": self.ch, "id": 1, "content": text})()

        async def go():
            first = asyncio.ensure_future(self.bridge.submit(message("一"), "一"))
            await asyncio.sleep(0.05)
            second = asyncio.ensure_future(self.bridge.submit(message("二"), "二"))
            await asyncio.sleep(0.3)
            typed = [x for x in fake.log if x[0] == "typed"]
            rt = self.bridge.rt(self.ch)
            for p in list(rt.pending):
                if p.text == "一":
                    rt.pending.remove(p)
                    p.got.set()
            await asyncio.gather(first, second)
            return typed
        typed = run(go(), fake, mock.patch.object(self.bridge, "ensure", nothing),
                    mock.patch.object(self.bridge, "close_panel", nothing), mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0),
                    mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.5))
        self.assertEqual(typed, [("typed", "一")], "the second is not typed before the first's receipt")
        self.assertIn(("typed", "二"), fake.log)

    def test_a_posts_timing_gets_when_its_turn_came(self):
        """C-75: posts.Origin carries a dict; submit puts in it when it had the channel's lock."""
        from bot import posts
        fake = Screens([box("❯ ")], echo=True)

        async def nothing(*a, **k):
            pass
        timing = {}
        run(self.bridge.submit(posts.Origin(self.ch, timing), "hello"), fake, mock.patch.object(self.bridge, "ensure", nothing),
            mock.patch.object(self.bridge, "close_panel", nothing), mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0),
            mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01))
        self.assertIsInstance(timing.get("locked"), float)

    def test_an_empty_box_is_typed_into_without_clearing(self):
        log, _ = self.send("hello", [box("❯ ")])
        self.assertNotIn(("key", "C-u"), log)
        self.assertIn(("typed", "hello"), log)

    def test_an_attached_pane_with_the_owners_half_typed_text_is_left_alone(self):
        for leftover in ("我打到一半的", "/model"):
            self.ch.sent.clear()
            log, said = self.send("hello", [box("❯ " + leftover)], pane=True, typed_last="/compact")
            self.assertNotIn(("typed", "hello"), log, leftover)
            self.assertNotIn(("key", "C-u"), log, leftover)
            self.assertIn("沒有送出", said)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.no_colour")
            self.assertIn("/key C-u", said)

    def test_an_attached_pane_with_the_command_the_bot_typed_last_is_cleared(self):
        log, _ = self.send("hello", [box("❯ /compact "), box("❯ /compact "), box("❯ ")], pane=True, typed_last="/compact")
        self.assertIn(("key", "C-u"), log)
        self.assertIn(("typed", "hello"), log)

    def test_an_attached_pane_showing_grey_text_is_typed_into(self):
        log, _ = self.send("hello", [box("❯ 建議的下一句")], pane=True,
                           color="%s\n\x1b[39m❯\xa0 \x1b[2m建議的下一句\n%s" % (RULE, RULE))
        self.assertIn(("typed", "hello"), log)

    def test_a_message_not_sent_keeps_what_was_to_go_with_it(self):
        self.bridge.host.chan(self.ch)["note_next"] = "（附註）"
        log, _ = self.send("hello", [box("❯ 我打到一半的")], pane=True)
        self.assertEqual(self.bridge.host.chan(self.ch).get("note_next"), "（附註）")
        log, _ = self.send("hello", [box("❯ ")], pane=True)
        self.assertIn(("typed", "（附註）\nhello"), log)
        self.assertIsNone(self.bridge.host.chan(self.ch).get("note_next"))

    def test_a_leading_space_does_not_get_past_the_list(self):
        log, said = self.send(" /logout", [box("❯ ")])
        self.assertEqual([e for e in log if e[0] == "typed"], [])
        self.assertIn("不能從 Discord 送", said)

    def test_logout_is_refused(self):
        log, said = self.send("/logout", [box("❯ ")])
        self.assertEqual([e for e in log if e[0] == "typed"], [])
        self.assertIn("不能從 Discord 送", said)

    def test_commands_that_leave_the_pane_ask_first_and_go_on_the_button(self):
        log, _ = self.send("/exit", [box("❯ ")])
        self.assertEqual([e for e in log if e[0] == "typed"], [], "nothing typed before the button")
        asked = self.ch.sent[-1]
        self.assertIn("確定要送", asked.content)
        buttons = [c for c in asked.view.children if getattr(c, "label", "") == "送出"]
        self.assertEqual(len(buttons), 1)


class Plain(Screens):
    """A fake pane without colours whose input box answers the keys the way Claude Code 2.1.295 was seen to
    (2026-10-09, NO_COLOR=1): a suggestion goes when anything is typed and is back when the box is empty again,
    with the cursor where the text starts; typed text takes a Space at the cursor and gives it back to a
    Backspace; in vim's NORMAL mode both keys only move the cursor. `on`: {number of keys pressed so far: a
    function called then}, for what happens in between."""

    def __init__(self, suggestion="", typed="", at=None, normal=False, below="  ? for shortcuts", on=None):
        Screens.__init__(self, [""])
        self.suggestion, self.typed, self.normal, self.below, self.on = suggestion, typed, normal, below, on or {}
        self.at, self.rows, self.gone, self.pressed = len(typed) if at is None else at, None, False, 0

    def view(self):
        if self.gone:
            return PANEL
        rows = self.rows or ["❯\xa0" + (self.typed or self.suggestion)]
        return "\n".join(["", RULE] + rows + [RULE, self.below, ""])

    async def raw_screen(self, name):
        self.last = self.view()
        return self.last

    async def cursor(self, name):
        return (2 + self.at, 2)

    async def type_text(self, name, text):
        self.log.append(("typed", text))
        self.typed, self.at, self.suggestion = self.typed[:self.at] + text + self.typed[self.at:], self.at + len(text), ""

    async def keys(self, name, *names):
        for n in names:
            self.log.append(("key", n))
            self.pressed += 1
            if n in ("Enter", "C-u"):
                self.typed, self.at = "", 0
            elif self.normal:
                self.at = max(0, min(len(self.typed) - 1, self.at + (1 if n == "Space" else -1)))
            elif n == "Space":
                self.typed, self.at = self.typed[:self.at] + " " + self.typed[self.at:], self.at + 1
            elif n == "BSpace" and self.at:
                self.typed, self.at = self.typed[:self.at - 1] + self.typed[self.at:], self.at - 1
            if self.pressed in self.on:
                self.on[self.pressed](self)


class NoColour(unittest.TestCase):
    """An attached pane whose screen has no colours (NO_COLOR=1): grey cannot be seen, so a suggestion is told
    from the owner's half-typed text by one Space and one Backspace, pressed only where they can do nothing
    else, and the owner's text is never cleared, never sent and left exactly as it was (#issues 2026-10-08)."""

    def setUp(self):
        tmuxio._esc_at.clear()
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.bridge.host.chan(self.ch)["pane"] = {"socket": "/tmp/x", "id": "%1"}

    def send(self, fake, text="hello"):
        async def nothing(*a, **k):
            pass

        class Message(object):
            channel, id, content = self.ch, 1, text
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run(self.bridge.submit(Message(), text), fake, mock.patch.object(self.bridge, "ensure", nothing),
                mock.patch.object(self.bridge, "close_panel", nothing),
                mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01),
                mock.patch.object(tmux_bridge.cc_typing, "PROBE_PAUSE", 0))
        return [k for kind, k in fake.log if kind == "key"], "\n".join(m.content for m in self.ch.sent), out.getvalue()

    def test_a_suggestion_is_found_and_the_message_goes(self):
        fake = Plain(suggestion="Yes, compare them with kiwi")
        keys, said, logged = self.send(fake)
        self.assertEqual(keys[:2], ["Space", "BSpace"])
        self.assertIn(("typed", "hello"), fake.log)
        self.assertNotIn("沒有送出", said)
        self.assertNotIn("建議字", said, "Discord hears nothing about the look when the message went")
        self.assertIn("[probe] %1: suggestion", logged)
        self.assertNotIn("kiwi", logged, "what the box held is not written to the log")

    def test_the_owners_text_with_the_cursor_at_its_start_is_put_back_and_nothing_is_sent(self):
        fake = Plain(typed="half typed", at=0)
        keys, said, logged = self.send(fake)
        self.assertEqual(keys, ["Space", "BSpace"])
        self.assertEqual((fake.typed, fake.at), ("half typed", 0))
        self.assertNotIn(("typed", "hello"), fake.log)
        self.assertIn("沒有送出", said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.no_colour")
        self.assertIn("Prompt suggestions", said)
        self.assertIn("[probe] %1: typed", logged)

    def test_the_owners_text_with_the_cursor_after_it_gets_no_key_at_all(self):
        for at in (None, 4):
            self.ch.sent.clear()
            fake = Plain(typed="half typed", at=at)
            keys, said, logged = self.send(fake)
            self.assertEqual(keys, [], at)
            self.assertNotIn(("typed", "hello"), fake.log)
            self.assertIn("沒有送出", said)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.no_colour")
            self.assertEqual(logged, "")

    def test_the_warning_says_what_clears_a_suggestion_and_that_ctrl_u_does_not(self):
        _, said, _ = self.send(Plain(typed="half typed"))
        self.assertIn("`/key C-u` 清不掉", said)
        self.assertIn("`/config` 關掉 Prompt suggestions", said)
        self.assertIn("NO_COLOR", said)

    def test_no_key_where_a_press_could_do_something_else(self):
        cases = {"vim mode": Plain(suggestion="建議", below="  -- INSERT -- ⏵⏵ auto mode on"),
                 "working": Plain(suggestion="建議", below="  esc to interrupt"),
                 "two rows": Plain(typed="line one", at=0),
                 "a wrapped suggestion": Plain(suggestion="建議")}
        cases["two rows"].rows = ["❯\xa0line one", "  line two"]
        cases["a wrapped suggestion"].rows = ["❯\xa0建議的第一列", "  第二列"]
        for why, fake in cases.items():
            self.ch.sent.clear()
            keys, said, logged = self.send(fake)
            self.assertEqual(keys, [], why)
            self.assertNotIn(("typed", "hello"), fake.log, why)
            self.assertIn("沒有送出", said, why)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.no_colour")

    def test_a_claude_code_that_may_be_in_vim_mode_gets_no_key(self):
        """#code-review-2 2026-10-09: in NORMAL mode, which no screen shows, the Space finished a d, r or c the
        owner had half typed and a character of theirs went. Its settings say vim, or cannot be read: no key."""
        for what in ({"suggestion": "建議"}, {"typed": "keep this text", "at": 0, "normal": True}):
            self.ch.sent.clear()
            fake = Plain(**what)
            fake.vim = True
            keys, said, logged = self.send(fake)
            self.assertEqual(keys, [])
            self.assertNotIn(("typed", "hello"), fake.log)
            self.assertIn("沒有送出", said)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.no_colour")
            self.assertIn("Prompt suggestions", said)
            self.assertEqual(logged, "")

    def test_vims_normal_mode_unknown_to_the_settings_still_leaves_the_text_as_it_was(self):
        """A --settings file on its command line is not seen: NORMAL mode with nothing half typed."""
        fake = Plain(typed="half typed", at=0, normal=True)
        keys, said, _ = self.send(fake)
        self.assertEqual(keys, ["Space", "BSpace"])
        self.assertEqual((fake.typed, fake.at), ("half typed", 0))
        self.assertNotIn(("typed", "hello"), fake.log)
        self.assertIn("沒有送出", said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.no_colour")

    def test_what_the_settings_say_of_vim(self):
        mode = '"editorMode": "%s"'
        self.assertFalse(cc_typing.says_vim("DONE\n"))
        self.assertFalse(cc_typing.says_vim(mode % "normal" + "\nDONE\n"))
        for out in (mode % "vim" + "\nDONE\n", mode % "normal" + "\n" + mode % "vim" + "\nDONE\n", '"editorMode":"VIM"\nDONE\n',
                    "UNREADABLE\nDONE\n", "", mode % "normal" + "\n"):
            self.assertTrue(cc_typing.says_vim(out), out)

    def test_the_settings_are_read_where_the_pane_is(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        where = os.path.join(home, "a folder", "proj")
        os.makedirs(os.path.join(where, ".claude"))
        os.makedirs(os.path.join(home, ".claude"))

        async def tmux(*args, **kw):
            return 0, where + "\n"

        def asked():
            with mock.patch.dict(os.environ, {"HOME": home}), fakepane.replace("tmux", tmux):
                return asyncio.run(tmuxio.vim_mode({"socket": "/tmp/x", "id": "%1"}))
        self.assertFalse(asked(), "no settings at all: Claude Code's own default")
        with open(os.path.join(home, ".claude", "settings.json"), "w") as f:
            f.write('{"editorMode": "normal"}')
        self.assertFalse(asked())
        with open(os.path.join(where, ".claude", "settings.local.json"), "w") as f:
            f.write('{\n "editorMode" : "vim"}')
        self.assertTrue(asked(), "the folder's own settings")
        os.remove(os.path.join(where, ".claude", "settings.local.json"))
        os.chmod(os.path.join(home, ".claude", "settings.json"), 0)
        self.assertTrue(asked(), "not readable is taken for vim")
        os.chmod(os.path.join(home, ".claude", "settings.json"), 0o600)
        os.remove(os.path.join(home, ".claude", "settings.json"))
        with open(os.path.join(home, ".claude.json"), "w") as f:
            f.write('{"editorMode":"vim"}')
        self.assertTrue(asked())

        async def no_pane(*args, **kw):
            return 1, ""
        with fakepane.replace("tmux", no_pane):
            self.assertTrue(asyncio.run(tmuxio.vim_mode({"socket": "/tmp/x", "id": "%1"})))

    def test_the_owner_typing_right_after_the_space_loses_no_character(self):
        """#code-review 2026-10-09: the Backspace took a character the owner typed in between; it is pressed only
        when the box is empty or as it was (manager)."""
        def types(word):
            def owner(pane):
                for c in word:
                    pane.typed, pane.at = pane.typed[:pane.at] + c + pane.typed[pane.at:], pane.at + 1
            return owner
        for what, word, left in (({"suggestion": "建議"}, "hello", " hello"), ({"suggestion": "建議"}, "h", " h"),
                                 ({"typed": "half typed", "at": 0}, "abc", " abchalf typed")):
            self.ch.sent.clear()
            fake = Plain(on={1: types(word)}, **what)
            keys, said, logged = self.send(fake)
            self.assertEqual(keys, ["Space"], "no Backspace, no other key")
            self.assertEqual(fake.typed, left, "every character of the owner's is there; a space may stay")
            self.assertNotIn(("typed", "hello"), fake.log)
            self.assertIn("/screen", said)
            self.assertIn("[probe] %1: changed", logged)

    def test_a_pane_being_scrolled_gets_no_key(self):
        fake = Plain(suggestion="建議")
        fake.scrolling = True
        keys, said, _ = self.send(fake)
        self.assertEqual(keys, [])
        self.assertNotIn(("typed", "hello"), fake.log)
        self.assertIn("捲動", said)
        self.assertIn("沒有送出", said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.scrolling")

    def test_a_new_suggestion_coming_up_between_the_two_keys_stops_everything(self):
        def turn_ends(pane):
            pane.suggestion = "另一句建議"
        fake = Plain(suggestion="Yes, compare them with kiwi", on={2: turn_ends})
        keys, said, logged = self.send(fake)
        self.assertEqual(keys, ["Space", "BSpace"])
        self.assertNotIn(("typed", "hello"), fake.log)
        self.assertIn("/screen", said)

    def test_the_cursor_elsewhere_after_the_backspace_stops_everything(self):
        def moved(pane):
            pane.at = 3
        fake = Plain(typed="half typed", at=0, on={2: moved})
        keys, said, _ = self.send(fake)
        self.assertEqual(keys, ["Space", "BSpace"])
        self.assertNotIn(("typed", "hello"), fake.log)
        self.assertIn("/screen", said)

    def test_a_panel_opening_after_the_space_gets_no_backspace(self):
        def panel(pane):
            pane.gone = True
        fake = Plain(suggestion="建議", on={1: panel})
        keys, said, _ = self.send(fake)
        self.assertEqual(keys, ["Space"], "a Backspace would go to whatever took the keyboard")
        self.assertNotIn(("typed", "hello"), fake.log)
        self.assertIn("/screen", said)

    def test_the_inverse_cell_under_the_cursor_is_no_colour(self):
        """#code-review-2 2026-10-09: with the focus on the terminal (or never reported), Claude Code under
        NO_COLOR draws the cursor's cell in inverse: one SGR on the screen, and nothing was ever probed."""
        for typed, said_or_sent in ((False, "sent"), (True, "said")):
            self.ch.sent.clear()
            fake = Plain(typed="half typed", at=0) if typed else Plain(suggestion="Yes, compare")
            word = "half typed" if typed else "Yes, compare"
            fake.color = "%s\n❯\xa0\x1b[7m%s\x1b[27m%s\n%s\n  \x1b[1mbold\x1b[22m" % (RULE, word[0], word[1:], RULE)
            keys, said, logged = self.send(fake)
            self.assertEqual(keys[:2], ["Space", "BSpace"])
            self.assertEqual(("typed", "hello") in fake.log, not typed)
            if typed:
                self.assertIn("Prompt suggestions", said)

    def test_colourless(self):
        self.assertTrue(screen.colourless("plain"))
        self.assertTrue(screen.colourless("❯ \x1b[7mY\x1b[27mes \x1b[1;4mx\x1b[0m\x1b[m"))
        for sgr in ("2", "90", "38;5;245", "38;2;1;2;3", "31", "39", "49", "7;2", "48;5;7"):
            self.assertFalse(screen.colourless("a \x1b[%smb" % sgr), sgr)

    def test_a_screen_with_colours_is_never_probed(self):
        fake = Plain(typed="half typed", at=0)
        fake.color = "%s\n\x1b[39m❯\xa0half typed\n%s" % (RULE, RULE)
        keys, said, _ = self.send(fake)
        self.assertEqual(keys, [])
        self.assertIn("沒有送出", said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.owners_text")
        self.assertNotIn("Prompt suggestions", said)

    def test_the_bots_own_session_is_never_probed(self):
        self.bridge.host.chan(self.ch).pop("pane")
        fake = Plain(typed="half typed", at=0)
        keys, _, logged = self.send(fake)
        self.assertNotIn("Space", keys)
        self.assertNotIn("[probe]", logged)


class MayProbe(unittest.TestCase):
    def test_only_a_one_row_box_with_the_cursor_where_the_text_starts(self):
        view = box("❯\xa0建議")
        self.assertTrue(screen.may_probe(view, (2, 2)))
        for cursor in (None, (3, 2), (2, 3), (2, 1)):
            self.assertFalse(screen.may_probe(view, cursor), cursor)
        self.assertFalse(screen.may_probe(PANEL, (2, 2)))
        self.assertFalse(screen.may_probe(box("❯\xa0建議").replace("? for shortcuts", "-- INSERT --"), (2, 2)))
        self.assertFalse(screen.may_probe(box("❯\xa0!ls"), (2, 2)), "shell mode")
        self.assertFalse(screen.may_probe(box("❯\xa0一\n  二"), (2, 2)), "two rows")


PERMISSION = "\n".join(["", " Bash command", "   touch /tmp/x", "", " Do you want to proceed?", " ❯ 1. Yes",
                        "   2. Yes, and always allow", "   3. No", "", " Esc to cancel · Tab to amend", ""])
SHELL = "\n".join(["", RULE, "! ls", RULE, "  ! for shell mode", ""])


class OwnersPane(unittest.TestCase):
    """An attached pane is typed into only when Claude Code's ordinary input box has the keyboard (manager
    2026-10-09). #code-review-2 saw, on main: a message joined to a shell command half typed in ! mode and run;
    a message and its Enter choosing "Yes" on a permission question."""

    def setUp(self):
        tmuxio._esc_at.clear()
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.st = self.bridge.host.chan(self.ch)
        self.st["pane"] = {"socket": "/tmp/x", "id": "%1"}

    def go(self, coro, fake):
        async def nothing(*a, **k):
            pass
        run(coro, fake, mock.patch.object(self.bridge, "ensure", nothing), mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0),
            mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01), mock.patch.object(tmux_bridge, "BTW_SECS", 1),
            fakepane.replace("ESC_GAP", 0.01))
        return fake.log, "\n".join(m.content for m in self.ch.sent)

    def send(self, screens, text="hello"):
        class Message(object):
            channel, id, content = self.ch, 1, text
        return self.go(self.bridge.submit(Message(), text), Screens(screens, echo=True))

    def test_what_has_the_keyboard(self):
        self.assertEqual(screen.pane_state(box("❯ ")), "box")
        self.assertEqual(screen.pane_state(box("❯ half typed")), "box")
        self.assertEqual(screen.pane_state(SHELL), "shell")
        self.assertEqual(screen.pane_state(PERMISSION), "dialog")
        self.assertEqual(screen.pane_state(PANEL), "dialog")
        self.assertEqual(screen.pane_state(REDRAWING), "dialog")
        self.assertEqual(screen.pane_state(box("❯ ") + "\n Esc to cancel"), "dialog", "an overlay over the box")
        self.assertEqual(screen.pane_state("Esc to cancel, it said\n" + box("❯ ") + "\n esc to interrupt"), "box",
                         "words in the conversation while a turn runs")

    def test_shell_mode_gets_no_key_and_nothing_is_sent(self):
        for busy in (False, True):
            self.ch.sent.clear()
            self.st["busy"] = busy
            log, said = self.send([SHELL])
            self.assertEqual(log, [], busy)
            self.assertIn("沒有送出", said)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.shell_mode")
            self.assertIn("shell", said)

    def test_a_permission_question_gets_no_key_and_nothing_is_sent(self):
        for busy in (True, False):  # a turn the bot knows of, and one it does not: no Enter, and no Escape either
            self.ch.sent.clear()
            self.st["busy"] = busy
            log, said = self.send([PERMISSION])
            self.assertEqual(log, [], busy)
            self.assertIn("沒有送出", said)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.dialog")
            self.assertIn("權限詢問", said)

    def test_a_message_from_another_channel_is_held_back_the_same(self):
        self.st["busy"] = True
        self.st["note_next"] = "（附註）"
        log, said = self.send([PERMISSION])
        self.assertEqual(log, [])
        self.assertEqual(self.st.get("note_next"), "（附註）", "what was to go with it waits for the next one")

    def test_a_panel_the_bots_own_command_opened_is_closed_first(self):
        self.st["own_panel"] = True
        log, said = self.send([PANEL, PANEL, PANEL, box("❯ ")])
        self.assertEqual(log.count(("key", "Escape")), 1)
        self.assertIn(("typed", "hello"), log)
        self.assertNotIn("own_panel", self.st)

    def test_a_turn_event_ends_the_bots_claim_on_what_is_open(self):
        for kind in ("PreToolUse", "UserPromptSubmit", "Stop"):
            self.ch.sent.clear()
            rt = self.bridge.rt(self.ch)
            self.st["own_panel"] = True

            async def event():
                try:
                    await self.bridge.handle({"channel": "5", "hook_event_name": kind, "session_id": "a"})
                except Exception:
                    pass  # whatever else the event needs here: the claim ends first
            asyncio.run(event())
            self.assertNotIn("own_panel", self.st, kind)
            self.st["busy"] = False
            log, said = self.send([PERMISSION])
            self.assertEqual(log, [], kind)

    def test_a_screen_between_two_frames_is_waited_for(self):
        log, _ = self.send([REDRAWING, REDRAWING, box("❯ ")])
        self.assertIn(("typed", "hello"), log)
        self.assertNotIn(("key", "Escape"), log)

    def test_btw_goes_through_the_same_checks(self):
        for screens, word in (([SHELL], "shell"), ([PERMISSION], "權限詢問"), ([box("❯ 我打到一半的")], "打到一半")):
            self.ch.sent.clear()
            fake = Screens(screens, color="\x1b[31mx")
            log, said = self.go(self.bridge.btw_ask(self.ch, None, "問題"), fake)
            self.assertEqual(log, [], word)
            self.assertIn("沒有送出", said)
            self.assertIn(word, said)

    def test_btw_clears_only_what_the_bot_typed(self):
        """Manager 2026-10-09: /btw pressed Ctrl-U on whatever the box held after typing."""
        log, said = self.go(self.bridge.btw_ask(self.ch, None, "問題"),
                            Screens([box("❯ "), box("❯ 我剛打的/btw 問題")]))
        self.assertEqual([k for k in log if k[0] == "key"], [], "the owner typed at that moment: nothing pressed")
        self.assertIn("沒有清", said)
        for theirs in ("[Pasted text #1 +3 lines]", "/btw 問", "e"):  # a mark a short question cannot have made;
            self.ch.sent.clear()                                        # a part of it; a letter that is in it
            log, said = self.go(self.bridge.btw_ask(self.ch, None, "問題 e"), Screens([box("❯ "), box("❯ " + theirs)]))
            self.assertEqual([k for k in log if k[0] == "key"], [], theirs)
            self.assertIn("沒有清", said)

    def after_enter(self, then, lose=0):
        """A pane that shows what is typed; after the first Enter that is taken it shows `then`. The first
        `lose` Enters are lost (the box still holds the message)."""
        fake = Screens([box("❯ ")], echo=True)
        plain_keys = fake.keys

        async def keys(name, *names):
            if "Enter" in names and fake.lost < lose:
                fake.lost += 1
                fake.log.extend(("key", n) for n in names)
                return
            await plain_keys(name, *names)
            if "Enter" in names:
                fake.shown = [then]
        fake.lost, fake.keys = 0, keys
        return fake

    def submit(self, fake, text="hello"):
        class Message(object):
            channel, id, content = self.ch, 1, text
        log, said = self.go(self.bridge.submit(Message(), text), fake)
        return [k for kind, k in log if kind == "key"], said

    def test_a_permission_question_that_came_up_after_the_check_gets_no_enter(self):
        """#code-review 2026-10-09: may_type looked before the typing only; up to three Enters followed unseen."""
        self.st["note_next"] = "（附註）"
        keys, said = self.submit(Screens([box("❯ "), PERMISSION]))
        self.assertEqual(keys, [])
        self.assertIn("沒有按 Enter", said)
        self.assertEqual(self.st.get("note_next"), "（附註）")
        self.assertEqual(self.bridge.rt(self.ch).pending, [])

    def test_the_turn_asking_permission_after_the_first_enter_gets_no_second_one(self):
        keys, said = self.submit(self.after_enter(PERMISSION))
        self.assertEqual(keys, ["Enter"], "no receipt, the bot thinks it idle: still no Enter onto the question")

    def test_an_enter_that_was_lost_is_pressed_again_on_the_message_alone(self):
        keys, _ = self.submit(self.after_enter(box("❯ "), lose=1))
        self.assertEqual(keys, ["Enter", "Enter"], "then the box is empty: nothing to press Enter on")

    def test_the_next_message_says_the_last_one_is_still_in_the_box(self):
        keys, said = self.submit(Screens([box("❯ "), PERMISSION]))
        self.assertIn("沒有按 Enter", said)
        self.ch.sent.clear()
        keys, said = self.submit(Screens([box("❯ hello")], color="\x1b[31mx"), "second")
        self.assertEqual(keys, [])
        self.assertIn("上一則 Discord 訊息", said)
        self.ch.sent.clear()
        self.submit(Screens([box("❯ ")], echo=True), "third")
        keys, said = self.submit(Screens([box("❯ hello")], color="\x1b[31mx"), "fourth")
        self.assertNotIn("上一則 Discord 訊息", said, "the box was seen empty since: this hello is the owner's")
        self.assertIn("沒有送出", said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.owners_text")

    def test_a_message_the_typing_itself_sent_gets_no_enter_and_is_not_called_unsent(self):
        """Seen on Claude Code 2.1.295: two lines of 900 characters were sent by the typing; the box was empty
        when the Enter came. Nothing to press, and the receipt tells."""
        fake = Screens([box("❯ ")], echo=True)
        typing = fake.type_text

        async def type_text(name, text):
            await typing(name, text)
            fake.typed_now = None
        fake.type_text = type_text
        keys, said = self.submit(fake)
        self.assertEqual(keys, [])
        self.assertNotIn("沒有按 Enter", said)
        self.assertIsNone(self.bridge.rt(self.ch).left)

    def test_more_marks_than_the_bot_can_have_made_are_not_its(self):
        """Manager 2026-10-09: the owner pasting while the bot types must not go out as the bot's."""
        two = "這是一則很長的訊息，" * 190
        self.assertEqual(screen.paste_marks(two), 3, "each 600 characters of a line could be one, should pacing fail")
        self.assertEqual(screen.paste_marks("字" * 500 + "\n" + "字" * 700), 1)
        self.assertEqual(screen.paste_marks("hello"), 0)
        marks = "❯\xa0[Pasted text #1][Pasted text #2]"
        self.assertTrue(screen.bot_typed(box(marks), two, 2))
        self.assertFalse(screen.bot_typed(box(marks + "[Pasted text #3 +4 lines]"), two, 2))
        self.assertFalse(screen.bot_typed(box("❯\xa0[Pasted text #1]hello"), "hello", 0))
        self.assertFalse(screen.bot_typed(box("❯\xa0[Pasted text #1]"), "hello there", 0))

    def test_what_claude_code_draws_differently_is_still_the_same_text(self):
        """#code-review-2 2026-10-09: a zero-width space is dropped, e and its accent are drawn as one, a blank
        follows the heart and the keycap's mark is gone: the message was left unsent in the owner's box."""
        for typed, shown in (("零\u200b寬\u200b空白", "零寬空白"), ("cafe\u0301", "caf\u00e9"),
                             ("好 ❤\ufe0f 1\ufe0f\u20e3 ok", "好 ❤\ufe0f  1\ufe0f ok"), ("👨\u200d👩\u200d👧", "👨\u200d👩\u200d👧")):
            self.assertTrue(screen.bot_typed(box("❯\xa0" + shown), typed, 0), typed)
        self.assertFalse(screen.bot_typed(box("❯\xa0café!"), "cafe\u0301", 0))

    def test_a_message_right_after_another_waits_for_that_one_to_leave_the_box(self):
        """#code-review-2 2026-10-09: the second of two sent together saw the first still in the box, its Enter
        just pressed, and was held as if the owner had typed it."""
        fake = Screens([box("❯ ")], echo=True)
        self.submit(fake, "first")
        fake = Screens([box("❯ first"), box("❯ first"), box("❯ ")], echo=True, color="\x1b[31mx")
        keys, said = self.submit(fake, "second")
        self.assertIn(("typed", "second"), fake.log)
        self.assertEqual(keys, ["Enter"])
        self.assertNotIn("沒有送出", said)

    def test_the_one_before_never_leaving_the_box_is_said_and_nothing_pressed(self):
        self.submit(Screens([box("❯ ")], echo=True), "first")
        self.ch.sent.clear()
        with mock.patch.object(tmux_bridge.cc_typing, "GONE_LOOKS", 3):
            keys, said = self.submit(Screens([box("❯ first")], color="\x1b[31mx"), "second")
        self.assertEqual(keys, [])
        self.assertIn("上一則 Discord 訊息按了 Enter", said)

    def test_the_owners_words_where_the_one_before_was_are_still_the_owners(self):
        self.submit(Screens([box("❯ ")], echo=True), "first")
        self.ch.sent.clear()
        keys, said = self.submit(Screens([box("❯ first"), box("❯ mine")], color="\x1b[31mx"), "second")
        self.assertEqual(keys, [])
        self.assertIn("打到一半", said)

    def test_the_hint_of_queued_messages_is_not_the_owners_text(self):
        """#code-review-2 2026-10-09, seen in #st-1 the same day: a pane without colours, a message in the queue,
        the next one held: "輸入框裡已經有字（Press up to edit queued messages）"."""
        hint = box("❯\xa0Press up to edit queued messages").replace("? for shortcuts", "esc to interrupt")
        self.assertEqual(screen.input_text(hint), "")
        self.assertEqual(screen.input_text(box("❯ Press up to edit queued messages now")), "Press up to edit queued messages now")
        self.st["busy"] = True
        fake = Screens([hint], echo=True)
        keys, said = self.submit(fake, "third")
        self.assertIn(("typed", "third"), fake.log)
        self.assertEqual(keys, ["Enter"])
        self.assertNotIn("沒有送出", said)

    def test_a_message_taken_and_called_back_by_the_owner_is_not_waited_for(self):
        """#code-review 2026-10-09 (low): the owner pressing the up key brought the last message back into the
        box; the next one waited five seconds and said the last one had not gone in."""
        self.submit(Screens([box("❯ ")], echo=True), "first")
        self.assertEqual(self.bridge.rt(self.ch).entered, "first")

        async def receipt():
            try:
                await self.bridge.handle({"channel": "5", "hook_event_name": "UserPromptSubmit", "session_id": "a", "prompt": "first"})
            except Exception:
                pass
        asyncio.run(receipt())
        self.assertIsNone(self.bridge.rt(self.ch).entered)
        self.ch.sent.clear()
        self.st["busy"] = False
        keys, said = self.submit(Screens([box("❯ first")], color="\x1b[31mx"), "second")
        self.assertEqual(keys, [])
        self.assertIn("打到一半", said)
        self.assertNotIn("按了 Enter 之後還留在", said)

    def test_an_earlier_messages_late_receipt_does_not_end_the_wait_for_the_last_one(self):
        """#code-review 2026-10-09: of three sent together, the first one's receipt came after the second's Enter
        and ended the wait for the second: the third was held as the owner's text."""
        self.submit(Screens([box("❯ ")], echo=True), "first")
        self.submit(Screens([box("❯ ")], echo=True), "second")

        async def receipt(prompt):
            try:
                await self.bridge.handle({"channel": "5", "hook_event_name": "UserPromptSubmit", "session_id": "a", "prompt": prompt})
            except Exception:
                pass
        asyncio.run(receipt("first"))
        self.assertEqual(self.bridge.rt(self.ch).entered, "second")
        self.ch.sent.clear()
        self.st["busy"] = True
        fake = Screens([box("❯ second"), box("❯ second"), box("❯ ")], echo=True, color="\x1b[31mx")
        keys, said = self.submit(fake, "third")
        self.assertIn(("typed", "third"), fake.log)
        self.assertNotIn("沒有送出", said)
        asyncio.run(receipt("second"))
        self.assertEqual(self.bridge.rt(self.ch).entered, "third", "the second's receipt is not the third's")
        asyncio.run(receipt("third"))
        self.assertIsNone(self.bridge.rt(self.ch).entered)

    def test_blanks_of_any_kind_do_not_count(self):
        """#code-review 2026-10-09 (low): a full-width blank at either end is cut off the box as it is read."""
        self.assertTrue(screen.bot_typed(box("❯ 全形　空白"), "\u3000全形\u3000空白\u3000"))
        self.assertTrue(screen.bot_typed(box("❯ a b"), "a\u2003b"))
        self.assertFalse(screen.bot_typed(box("❯ 全形空白x"), "\u3000全形空白"))

    def test_a_message_that_spells_a_mark_out_is_sent_like_any_other(self):
        """#code-review-2 2026-10-09: "框裡顯示 [Pasted text #1] 是什麼意思？" was typed into an attached pane and
        never sent: the words were taken for a paste mark, of which a short message may have none."""
        for text in ("框裡顯示 [Pasted text #1] 是什麼意思？", "框裡顯示 [Pasted text] 是什麼意思？",
                     "它寫 [Pasted text #3 +29 lines]，然後呢", "[Pasted text #1]", "兩個：[Pasted text #1][Pasted text #1]"):
            self.assertTrue(screen.bot_typed(box("❯ " + text), text, screen.paste_marks(text)), text)
            keys, said = self.submit(Screens([box("❯ ")], echo=True), text)
            self.assertEqual(keys, ["Enter"], text)
            self.assertNotIn("沒有按 Enter", said)
        asked = "框裡顯示 [Pasted text #1] 是什麼意思？"
        for theirs in (asked + "x", "x" + asked, asked + "[Pasted text #2 +5 lines]", asked + "[Pasted text #2]",
                       "框裡顯示 [Pasted text #7] 是什麼意思？"):
            self.assertFalse(screen.bot_typed(box("❯ " + theirs), asked, 0), theirs)
        three = "第一行說 [Pasted text #1]\n第二行說 [Pasted text #2 +3 lines]\n第三行"
        self.assertTrue(screen.bot_typed(box("❯ 第一行說 [Pasted text #1]\n  第二行說 [Pasted text #2 +3 lines]\n  第三行"), three, 0))
        long = "問：[Pasted text #1] 是什麼？" + "字" * 1500
        self.assertTrue(screen.bot_typed(box("❯ 問：[Pasted text #1] 是什麼？[Pasted text #4]"), long, screen.paste_marks(long)),
                        "its own words, then a stretch that was taken for a paste after all")
        self.assertFalse(screen.bot_typed(box("❯ 問：[Pasted text #1] 是什麼？[Pasted text #4 +9 lines]"), long, screen.paste_marks(long)))
        self.assertFalse(screen.bot_typed(box("❯ 問：[Pasted text #1] 是什麼？" + "[Pasted text #4]" * 4), long, screen.paste_marks(long)),
                         "more marks than its words and its long stretches can account for")

    def test_a_mark_that_counts_lines_is_never_the_bots(self):
        """Manager 2026-10-09: the bot types a line end as a key of its own; "+29 lines" is the owner's paste."""
        long = "0123456789" * 200
        self.assertTrue(screen.bot_typed(box("❯\xa0[Pasted text #1][Pasted text #2]"), long, 2))
        self.assertFalse(screen.bot_typed(box("❯\xa0[Pasted text #1][Pasted text #2 +29 lines]"), long, 2))
        self.assertFalse(screen.bot_typed(box("❯\xa0[Pasted text #1 +1 lines]"), long, 2))

    def test_the_owner_typing_after_the_message_gets_no_enter(self):
        fake = Screens([box("❯ ")], echo=True)
        typing = fake.type_text

        async def type_text(name, text):
            await typing(name, text)
            fake.typed_now = text + " and the owner's words"
        fake.type_text = type_text
        keys, said = self.submit(fake)
        self.assertEqual(keys, [])
        self.assertIn("沒有按 Enter", said)

    def test_no_second_enter_while_the_panes_events_cannot_come(self):
        self.st["pane"]["host"] = "lap"
        self.bridge.stream_down.add("lap")
        keys, said = self.submit(self.after_enter(box("❯ hello"), lose=5))
        self.assertEqual(keys, ["Enter"])
        self.assertIn("事件連線斷了", said)

    def test_no_escape_on_the_bots_own_panel_while_the_panes_events_cannot_come(self):
        """#code-review 2026-10-09: the claim is ended by events; with none coming it may be a turn's question."""
        self.st["pane"]["host"] = "lap"
        self.bridge.stream_down.add("lap")
        self.st["own_panel"] = True
        keys, said = self.submit(Screens([PERMISSION]))
        self.assertEqual(keys, [])
        self.assertIn("沒有送出", said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.dialog")

    def test_btw_gets_no_enter_with_the_owners_words_after_the_question(self):
        fake = Screens([box("❯ "), box("❯ /btw 問題 and the owner's words")])
        log, said = self.go(self.bridge.btw_ask(self.ch, None, "問題"), fake)
        self.assertEqual([k for k in log if k[0] == "key"], [], "no Enter, and not cleared")
        self.assertIn("沒有清", said)

    def test_all_of_what_the_bot_typed_and_nothing_else(self):
        typed = "（附註）\nhello there\tyou"
        for shown, whole in (("（附註） hello there    you", True), ("（附註）\n  hello there you", True),
                             ("[Pasted text #1 +2 lines]", False), ("（附註） hello there", False),
                             ("（附註） hello there you!", False), ("x（附註） hello there you", False), ("", False)):
            self.assertEqual(screen.bot_typed(box("❯ " + shown), typed), whole, shown)

    def test_a_long_message_shown_as_pasted_or_scrolled_is_still_the_bots(self):
        """Seen on Claude Code 2.1.295 (80×24): 1900 characters show as two "[Pasted text]" marks; of 40 lines
        the box shows the last seven. Both went before this batch and must go."""
        long = "這是一則很長的訊息，" * 190
        self.assertTrue(screen.bot_typed(box("❯\xa0[Pasted text #1][Pasted text #2]"), long, 2))
        self.assertTrue(screen.bot_typed(box("❯\xa0這是一則[Pasted text #1]訊息，"), long, 2))
        self.assertTrue(screen.bot_typed(box("❯\xa0[Pasted text #1]"), long, 2))
        self.assertFalse(screen.bot_typed(box("❯\xa0[Pasted text #1]"), long), "no mark can be the bot's unless said")
        self.assertFalse(screen.bot_typed(box("❯\xa0" + "這是一則很長的訊息，" * 190 + "[Pasted text #1]"), long, 2),
                         "a mark stands for a character at least")
        for theirs in ("x[Pasted text #1][Pasted text #2]", "[Pasted text #1][Pasted text #2]x", "[Pasted text #1] 別的字"):
            self.assertFalse(screen.bot_typed(box("❯\xa0" + theirs), long, 3), theirs)
        lines = "\n".join("第 %d 行 some words" % i for i in range(40))
        tail = "❯\xa0第 33 行 some words\n" + "\n".join("  第 %d 行 some words" % i for i in range(34, 40))
        self.assertTrue(screen.bot_typed(box(tail), lines))
        self.assertFalse(screen.bot_typed(box(tail + "x"), lines), "the owner's character after it")
        self.assertFalse(screen.bot_typed(box("❯\xa0第 39 行 some words"), lines), "one row is no scrolled box")
        self.assertFalse(screen.bot_typed(box("❯\xa0lo"), "hello"))

    def test_comparing_never_takes_long(self):
        """#code-review 2026-10-09: as a pattern, several marks that did not match ran for minutes, in the bot's
        own loop (every channel waiting). Piece by piece from the left; and a box past the limits is not compared."""
        long = "0123456789" * 200
        for marks in (2, 4, 6, 8):
            shown = "❯\xa0" + "0123[Pasted text #1]" * marks + "x"
            t0 = time.monotonic()
            self.assertFalse(screen.bot_typed(box(shown), long, 8), marks)
            self.assertLess(time.monotonic() - t0, 0.2, marks)
        t0 = time.monotonic()
        self.assertTrue(screen.bot_typed(box("❯\xa0" + "0123[Pasted text #1]" * 8 + "89"), long, 8))
        self.assertFalse(screen.bot_typed(box("❯\xa0" + "[Pasted text #1]" * 9), long, 99), "more marks than are ever compared")
        self.assertFalse(screen.bot_typed(box("❯\xa0" + "字" * 7000), "字" * 7000), "a box too long to compare")
        self.assertLess(time.monotonic() - t0, 0.2)

    def test_marks_in_order_each_for_a_part(self):
        ours = "abcdefghij" * 100
        for shown, same in (("abc[Pasted text #1]hij", True), ("[Pasted text #1]cdefghij", True), ("abc[Pasted text #1]", True),
                            ("abc[Pasted text #1]fgh[Pasted text #2]hij", True), ("abc[Pasted text #1]hij[Pasted text #2]abc", False),
                            ("bc[Pasted text #1]hij", False), ("abc[Pasted text #1]hi", False), ("abc[Pasted text #1]xyz[Pasted text #2]hij", False)):
            self.assertEqual(screen.bot_typed(box("❯\xa0" + shown), ours, 2), same, shown)
        self.assertFalse(screen.bot_typed(box("❯\xa0ab[Pasted text #1]ab"), "abab", 1), "nothing left for the mark")
        self.assertTrue(screen.bot_typed(box("❯\xa0ab[Pasted text #1]ab"), "abXab", 1))


class OwnSession(unittest.TestCase):
    """The bot's own sessions go by the same rule as an attached pane (manager 2026-10-09): the owner may sit at
    one through tmux attach, and a permission question comes up there too once the session is not in bypass mode.
    Typed into only when the ordinary input box has the keyboard; what is left in the box is cleared as before."""

    def setUp(self):
        tmuxio._esc_at.clear()
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.st = self.bridge.host.chan(self.ch)
        self.assertNotIn("pane", self.st)
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, True)

    def send(self, fake, text="hello"):
        async def nothing(*a, **k):
            pass

        class Message(object):
            channel, id, content = self.ch, 1, text
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run(self.bridge.submit(Message(), text), fake, mock.patch.object(self.bridge, "ensure", nothing),
                mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01),
                fakepane.replace("ESC_GAP", 0.01), mock.patch.object(tmux_bridge.config, "STATE", self.state))
        return fake.log, "\n".join(m.content for m in self.ch.sent), out.getvalue()

    def test_a_permission_question_or_shell_mode_gets_no_key(self):
        for screen, word in ((PERMISSION, "權限詢問"), (SHELL, "shell")):
            for busy in (True, False):
                self.ch.sent.clear()
                self.st["busy"] = busy
                log, said, _ = self.send(Screens([screen]))
                self.assertEqual(log, [], (word, busy))
                self.assertIn("沒有送出", said)
                self.assertIn(word, said)

    def test_a_panel_the_bots_own_command_opened_is_closed_and_the_message_goes(self):
        self.st["own_panel"] = True
        fake = Screens([PANEL, PANEL, PANEL, box("❯ ")], echo=True)
        log, said, _ = self.send(fake)
        self.assertEqual(log.count(("key", "Escape")), 1)
        self.assertIn(("typed", "hello"), log)
        self.assertEqual(log[-1], ("key", "Enter"))
        self.assertNotIn("own_panel", self.st)

    def test_the_claim_on_a_panel_is_kept_with_the_channel_not_in_memory(self):
        self.st["own_panel"] = True
        self.bridge = tmux_bridge.TmuxBridge(self.bridge.host)  # the bot restarted: what it held in memory is gone
        log, said, _ = self.send(Screens([PANEL, PANEL, PANEL, box("❯ ")], echo=True))
        self.assertEqual(log.count(("key", "Escape")), 1)
        self.assertIn(("typed", "hello"), log)

    def test_a_panel_nobody_claims_is_left_open_and_said(self):
        log, said, _ = self.send(Screens([PANEL]))
        self.assertEqual(log, [])
        self.assertIn("對話框或選單開著", said)

    def test_a_question_that_came_up_after_the_typing_gets_no_enter(self):
        log, said, _ = self.send(Screens([box("❯ "), PERMISSION]))
        self.assertEqual([k for k in log if k[0] == "key"], [])
        self.assertIn("沒有按 Enter", said)

    def test_a_leftover_is_cleared_as_before_and_the_message_goes(self):
        fake = Screens([box("❯ /compact "), box("❯ /compact "), box("❯ ")], echo=True)
        log, said, _ = self.send(fake)
        self.assertIn(("key", "C-u"), log)
        self.assertEqual(log[-2:], [("typed", "hello"), ("key", "Enter")])
        self.assertNotIn("沒有送出", said)

    def test_a_box_that_is_not_what_was_typed_still_gets_its_enter_and_a_line_in_the_log(self):
        """Unlike an attached pane: a text Claude Code draws in a way the comparison does not know must not keep
        every channel's messages from going."""
        fake = Screens([box("❯ ")], echo=True)
        typing = fake.type_text

        async def type_text(name, text):
            await typing(name, text)
            fake.typed_now = "drawn some other way"
        fake.type_text = type_text
        log, said, logged = self.send(fake)
        self.assertEqual(log[-1], ("key", "Enter"))
        self.assertIn("[enter] s: the box is not what was typed", logged)
        self.assertNotIn("沒有按 Enter", said)
        with open(os.path.join(self.state, "enter-differs.json")) as f:
            self.assertEqual(len(json.load(f)["times"]), 1, "counted for doctor (manager 2026-10-09)")
        fake = Screens([box("❯ ")], echo=True)
        log, said, logged = self.send(fake)
        self.assertEqual(logged, "", "a box that is what was typed is not counted")
        with open(os.path.join(self.state, "enter-differs.json")) as f:
            self.assertEqual(len(json.load(f)["times"]), 1)

    def test_the_one_before_still_in_the_box_is_sent_again_and_said(self):
        """Manager 2026-10-09: it used to be cleared as a leftover (and lost); in the bot's own session it is
        cleared, typed again and sent, and the channel hears of it; then this one goes."""
        self.send(Screens([box("❯ ")], echo=True), "first")
        self.ch.sent.clear()
        fake = Plain(typed="first", below="  ? for shortcuts")
        fake.color = "\x1b[31mx"
        with mock.patch.object(tmux_bridge.cc_typing, "GONE_LOOKS", 2):
            log, said, _ = self.send(fake, "second")
        self.assertEqual(log, [("key", "C-u"), ("typed", "first"), ("key", "Enter"), ("typed", "second"), ("key", "Enter")])
        self.assertIn("已經重送", said)
        self.assertNotIn("沒有送出", said)

    def test_the_one_sent_again_gets_its_second_enter_too(self):
        """#code-review-2 2026-10-09: a text whose first Enter Claude Code does not take (a zero-width space in
        it) stayed in the box after being sent again with one Enter, and both messages were lost."""
        self.send(Screens([box("❯ ")], echo=True), "first")
        self.ch.sent.clear()
        fake = Plain(typed="first", below="  ? for shortcuts")
        fake.color = "\x1b[31mx"
        plain_keys, deaf = fake.keys, []

        async def keys(name, *names):
            if "Enter" in names and fake.typed == "first" and not deaf:
                deaf.append(1)  # the first Enter on it is not taken
                fake.log.extend(("key", n) for n in names)
                return
            await plain_keys(name, *names)
        fake.keys = keys
        with mock.patch.object(tmux_bridge.cc_typing, "GONE_LOOKS", 2), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.03):
            log, said, _ = self.send(fake, "second")
        self.assertEqual(log, [("key", "C-u"), ("typed", "first"), ("key", "Enter"), ("key", "Enter"), ("typed", "second"), ("key", "Enter")])
        self.assertIn("已經重送", said)
        self.assertNotIn("沒有送出", said)

    def test_the_one_before_holding_those_words_is_sent_again_all_the_same(self):
        said_before = "面板的提示是 Esc to cancel，按 Enter to confirm"
        self.send(Screens([box("❯ ")], echo=True), said_before)
        self.ch.sent.clear()
        fake = Plain(typed=said_before, below="  ? for shortcuts")
        fake.color = "\x1b[31mx"
        with mock.patch.object(tmux_bridge.cc_typing, "GONE_LOOKS", 2):
            log, said, _ = self.send(fake, "second")
        self.assertEqual(log, [("key", "C-u"), ("typed", said_before), ("key", "Enter"), ("typed", "second"), ("key", "Enter")])
        self.assertIn("已經重送", said)

    def test_a_suggestion_coming_up_when_the_one_before_is_cleared_does_not_stop_the_resend(self):
        """#code-review-2 2026-10-09: with suggestions on, clearing the box brought the next suggestion up; the
        box was "not empty", and the resend gave up with both messages unsent."""
        for color in ("\x1b[31mx", ""):  # a screen with colours, and one without
            self.send(Screens([box("❯ ")], echo=True), "first")
            self.ch.sent.clear()
            fake = Plain(typed="first", suggestion="run the tests", below="  ? for shortcuts")
            fake.color = color or None
            with mock.patch.object(tmux_bridge.cc_typing, "GONE_LOOKS", 2):
                log, said, _ = self.send(fake, "second")
            self.assertEqual([e for e in log if e[0] == "typed"], [("typed", "first"), ("typed", "second")], repr(color))
            self.assertEqual(log.count(("key", "Enter")), 2)
            self.assertIn("已經重送", said)
            self.assertNotIn("沒有送出", said)

    def test_the_one_before_that_will_not_go_stops_this_one_and_says_so(self):
        self.send(Screens([box("❯ ")], echo=True), "first")
        self.ch.sent.clear()
        with mock.patch.object(tmux_bridge.cc_typing, "GONE_LOOKS", 2), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.03):
            log, said, _ = self.send(Screens([box("❯ first")]), "second")  # a box no key changes
        self.assertNotIn(("typed", "second"), log)
        self.assertIn("重送一次也還留在輸入框裡", said)

    def test_words_of_a_panel_in_the_conversation_are_only_words(self):
        """#code-review 2026-10-09: a reply that quoted "Esc to cancel" stayed on the screen of an idle channel and
        every message to it was held as if a panel were open; nothing could be answered to make it go."""
        for words in ("Esc to cancel", "Enter to confirm · Esc to cancel", "Esc to close", "esc to interrupt", "Esc to go back"):
            self.ch.sent.clear()
            self.st.pop("own_panel", None)
            quoted = "● 面板的提示是「%s」這幾個字\n  ❯ 1. Yes\n" % words + box("❯ ")
            self.assertEqual(screen.pane_state(quoted), "box", words)
            self.assertFalse(screen.panel_shown(quoted), words)
            log, said, _ = self.send(Screens([quoted], echo=True))
            self.assertEqual(log, [("typed", "hello"), ("key", "Enter")], words)
            self.assertEqual(said.count("沒有送出"), 0, words)
        for real in (PANEL, PERMISSION, box("❯ ") + "\n Esc to cancel", "● 回覆裡寫了 ❯ 和 Esc to cancel\n" + PERMISSION):
            self.assertEqual(screen.pane_state(real), "dialog")
            self.assertTrue(screen.panel_shown(real))
        over = "● 上一則說 esc to interrupt 是什麼\n" + PANEL  # seen on 2.1.295: /model opened under a reply that quoted the words
        self.assertEqual(screen.pane_state(over), "dialog")
        self.assertTrue(screen.panel_shown(over), "the bot's own panel is still seen as one, and closed")
        busy = "● 上一則說 Esc to cancel\n" + box("❯ ").replace("? for shortcuts", "esc to interrupt")
        self.assertEqual(screen.pane_state(busy), "box")
        self.assertFalse(screen.panel_shown(busy), "a turn runs: Escape would interrupt it")

    def test_a_message_that_says_those_words_itself_is_sent(self):
        """#code-review 2026-10-09: typed into the box, "…面板的提示是 Esc to cancel 那一行" made the screen look
        like a dialog to the look before Enter: no Enter, the message left in the box, the channel stuck."""
        for text in ("請看 pane_state：面板的提示是 Esc to cancel 那一行", "按 Enter to confirm 才會送出",
                     "第一行\n第二行寫到 Esc to close", "這一輪顯示 esc to interrupt", "Esc to go back"):
            self.ch.sent.clear()
            typed = box("❯ " + text.replace("\n", "\n  "))
            self.assertEqual(screen.pane_state(typed), "box", text)
            self.assertFalse(screen.panel_shown(typed), text)
            log, said, logged = self.send(Screens([box("❯ ")], echo=True), text)
            self.assertEqual(log, [("typed", text), ("key", "Enter")], text)
            self.assertEqual((said.count("沒有送出") + said.count("沒有按 Enter"), logged), (0, ""), text)
        tall = "\n".join(["", RULE, "❯ 很長的訊息，寫到 Esc to cancel"] + ["  第 %d 行" % i for i in range(30)])  # the box runs off the screen
        self.assertEqual(screen.pane_state(tall), "box")
        under = box("❯ 寫到 Esc to cancel").replace("? for shortcuts", "Esc to cancel")  # and a real hint under the box
        self.assertEqual(screen.pane_state(under), "dialog")
        shell = "\n".join(["", RULE, "! echo Esc to cancel", RULE, "  ! for shell mode", ""])
        self.assertEqual(screen.pane_state(shell), "shell")

    def test_a_message_with_a_rule_of_its_own_is_sent(self):
        """#code-review 2026-10-09: a message holding a row of "─" (a pasted screen, a divider): the first rule
        after "❯" was taken for the box's end, and the message's own words after it for the status line."""
        pasted = ["畫面是這樣：", RULE, "Esc to cancel · Enter to confirm", "這是面板的提示"]
        whole = ["引用整個畫面：", RULE, "❯ 打到一半", RULE, "  ⏵⏵ auto mode on · esc to interrupt"]
        for lines in (pasted, whole):
            self.ch.sent.clear()
            text = "\n".join(lines)
            typed = "\n".join(["", RULE, "❯ " + lines[0]] + ["  " + l if not l.startswith("─") else l for l in lines[1:]] +
                              [RULE, "  ? for shortcuts", ""])
            self.assertEqual(screen.pane_state(typed), "box", lines[0])
            self.assertFalse(screen.panel_shown(typed))
            self.assertFalse(screen.busy_shown(typed), "idle, whatever the message quotes")
            fake = Screens([box("❯ ")], echo=True)
            fake.raw_screen_plain = fake.raw_screen

            async def raw_screen(name, fake=fake, typed=typed):
                if fake.typed_now is not None:
                    fake.last = typed
                    return typed
                return await fake.raw_screen_plain(name)
            fake.raw_screen = raw_screen
            log, said, logged = self.send(fake, text)
            self.assertEqual([k for k in log if k[0] == "key"], [("key", "Enter")], lines[0])
            self.assertEqual(said.count("沒有送出") + said.count("沒有按 Enter"), 0)
        real = "\n".join(["", RULE, "❯ " + pasted[0], RULE, "  Esc to cancel", ""])  # a real hint under the last rule
        self.assertEqual(screen.pane_state(real), "dialog")
        self.assertEqual(screen.pane_state(PERMISSION), "dialog")
        running = "\n".join(["", RULE, "❯ ", RULE, "  ⏵⏵ auto mode on · esc to interrupt", ""])
        self.assertTrue(screen.busy_shown(running))

    def test_a_slash_command_on_such_a_screen_claims_no_panel(self):
        quoted = "● 面板的提示是 Esc to cancel\n" + box("❯ ")
        with mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()):
            self.send(Screens([quoted], echo=True), "/usage")
        self.assertFalse(self.st.get("own_panel"))

    def test_the_claim_on_a_panel_is_saved_when_it_is_made(self):
        saves = []
        self.bridge.host.save = lambda: saves.append(dict(self.st))
        with mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.bridge.menus, "show_panel", mock.AsyncMock(return_value=True)):
            fake = Screens([box("❯ ")], echo=True)
            plain_keys = fake.keys

            async def keys(name, *names):  # the command's panel comes up when its Enter is taken
                await plain_keys(name, *names)
                if "Enter" in names:
                    fake.shown = [PANEL]
            fake.keys = keys
            self.send(fake, "/model")
        self.assertTrue(self.st.get("own_panel"))
        self.assertTrue(saves and saves[-1].get("own_panel"), "a restart right after still knows the panel is the bot's")

    def test_no_receipt_and_a_suggestion_in_the_box_gets_no_second_enter_and_is_not_counted(self):
        """#code-review-2 2026-10-09: the message had gone in; the two Enters pressed again for want of a receipt
        landed on the next suggestion and were counted as "the box is not what was typed"."""
        fake = Screens([box("❯ ")], echo=True)
        plain_keys = fake.keys

        async def keys(name, *names):
            await plain_keys(name, *names)
            if "Enter" in names:
                fake.shown = [box("❯ run the tests")]  # taken; the next suggestion stands in the box
        fake.keys = keys
        log, said, logged = self.send(fake)
        self.assertEqual([k for k in log if k[0] == "key"], [("key", "Enter")])
        self.assertEqual(logged, "")
        self.assertFalse(os.path.exists(os.path.join(self.state, "enter-differs.json")))

    def test_an_enter_that_was_lost_is_pressed_again(self):
        fake = Screens([box("❯ ")], echo=True)
        plain_keys, lost = fake.keys, []

        async def keys(name, *names):
            if "Enter" in names and not lost:
                lost.append(1)
                fake.log.extend(("key", n) for n in names)
                return
            await plain_keys(name, *names)
        fake.keys = keys
        log, said, logged = self.send(fake)
        self.assertEqual([k for k in log if k[0] == "key"], [("key", "Enter"), ("key", "Enter")])
        self.assertEqual(logged, "")

    def test_the_second_of_two_waits_for_the_first_to_leave_the_box(self):
        self.send(Screens([box("❯ ")], echo=True), "first")
        self.ch.sent.clear()
        fake = Screens([box("❯ first"), box("❯ first"), box("❯ ")], echo=True)
        log, said, _ = self.send(fake, "second")
        self.assertNotIn(("key", "C-u"), log, "the first one, its Enter just pressed, is not cleared as a leftover")
        self.assertEqual(log, [("typed", "second"), ("key", "Enter")])


class SendNow(unittest.TestCase):
    """The send-now keys are pressed only when Claude Code's own hint stands where it draws it (manager
    2026-10-09). With nothing queued, Ctrl+X Ctrl+S sends off whatever stands in the input box (seen on 2.1.295),
    and "to send now" in a reply, or in a queued message, used to look like the hint."""

    def screen(self, over, in_box="Press up to edit queued messages", under="  ⏵⏵ auto mode on · esc to interrupt"):
        return "\n".join(list(over) + [RULE, "❯ " + in_box, RULE, under, ""])

    def test_the_hint_over_the_box_and_nothing_else(self):
        real = ["● Rig · 4s", "  ⎿  $ sleep 12 (4s)", "❯ queued one", "  ctrl+x ctrl+s to send now", "✻ Razzmatazzing… (5s · ↓ 11 tokens)",
                " " * 90 + "◐ medium"]
        self.assertTrue(screen.send_now_shown(self.screen(real)))
        two = real[:3] + ["❯ queued two, a longer one that says to send now in it"] + real[3:]
        self.assertTrue(screen.send_now_shown(self.screen(two)))
        for over, why in ((["● The hint under a queued message reads: ctrl+x ctrl+s to send now.", "✻ Working…"], "a reply that quotes it"),
                          (["● reply", "❯ a queued message: press ctrl+x ctrl+s to send now", "✻ Working…"], "a queued message saying so, no hint row"),
                          (["  ctrl+x ctrl+s to send now"] + ["● a long reply, row %d" % i for i in range(12)], "the words far up the screen"),
                          (["❯ ctrl+x ctrl+s to send now", "✻ Working…"], "the owner's own message"),
                          (["● idle"], "nothing queued")):
            self.assertFalse(screen.send_now_shown(self.screen(over, in_box="")), why)
        self.assertFalse(screen.send_now_shown(self.screen(["● x"], in_box="ctrl+x ctrl+s to send now")), "typed into the box")
        self.assertFalse(screen.send_now_shown(PANEL + "\n  ctrl+x ctrl+s to send now"), "no input box at all")

    def test_no_keys_when_only_the_words_are_on_the_screen(self):
        pressed = []

        async def raw_screen(t):
            return self.screen(["● The hint reads: ctrl+x ctrl+s to send now.", "● idle now"], in_box="half typed by the owner")

        async def keys(t, *names):
            pressed.append(names)
        bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        with fakepane.replace("raw_screen", raw_screen), fakepane.replace("keys", keys), \
                mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()):
            self.assertFalse(asyncio.run(bridge.send_now(mock.Mock(), {"socket": "/tmp/x", "id": "%1"})))
        self.assertEqual(pressed, [], "the owner's half-typed text is not sent off")


class RulesInTheMessage(unittest.TestCase):
    """#code-review-2 2026-10-09: a message holding a row of ten or more "─" (a divider, a pasted screen). The
    box was read up to its first rule, so what the bot typed was never "all of it": no Enter in an attached pane
    (the text left in the owner's box), and a count against the bot's own session each time."""

    CASES = (["上半段", RULE, "下半段"], ["畫面是這樣：", RULE, "Esc to cancel · Enter to confirm", "這是面板的提示"],
             ["引用整個畫面：", RULE, "❯ 打到一半", RULE, "  ⏵⏵ auto mode on · esc to interrupt"],
             ["引用面板：", " Select model", " ❯ 1. Default", RULE, " Enter to confirm · Esc to cancel"])

    def shown(self, lines):
        return "\n".join(["", RULE, "❯ " + lines[0]] + [l if l.startswith("─") else "  " + l for l in lines[1:]] + [RULE, "  ? for shortcuts", ""])

    def test_where_the_box_is(self):
        view = self.shown(self.CASES[1])
        rows = view.split("\n")
        start, end = screen.box_span(view)
        self.assertEqual((rows[start][:4], rows[end + 1].strip()), ("❯ 畫面", "? for shortcuts"))
        self.assertEqual(len(screen.input_rows(view)[1]), 4, "the whole message, its own rule in it")
        self.assertIsNone(screen.box_span(PANEL))
        self.assertIsNone(screen.box_span(PERMISSION))
        self.assertEqual(screen.box_span(SHELL), None)
        self.assertIsNotNone(screen.box_span(SHELL, shell=True))
        none = "\n".join(["", RULE, "❯ 很長"] + ["  第 %d 行" % i for i in range(30)])
        self.assertEqual(screen.box_span(none)[1], None, "no rule after the first row: the end is not on the screen")
        menu = "\n".join(["", RULE, "❯ /review", RULE] + ["  /review-%d   a skill" % i for i in range(30)] + ["  ? for shortcuts"])
        self.assertEqual(screen.input_text(menu), "/review", "#code-review 2026-10-09: a long menu under the box is not the message")
        self.assertTrue(screen.bot_typed(menu, "/review", 0))
        self.assertEqual(screen.input_text("\n".join(["", RULE, "❯ ", RULE] + ["  row %d" % i for i in range(9)])), "")
        self.assertTrue(screen.has_prompt(view))
        self.assertFalse(screen.has_prompt(PANEL))
        quoting = self.shown(self.CASES[2])  # a "❯" under a rule inside the message is not where the box starts
        self.assertEqual(quoting.split("\n")[screen.box_span(quoting)[0]][:6], "❯ 引用整個")
        self.assertEqual(len(screen.input_rows(quoting)[1]), 5)

    def test_such_a_message_is_the_bots_own_and_is_sent_in_both_kinds_of_pane(self):
        for lines in self.CASES:
            text = "\n".join(lines)
            view = self.shown(lines)
            self.assertTrue(screen.bot_typed(view, text, screen.paste_marks(text)), lines[0])
            self.assertFalse(screen.bot_typed(self.shown(lines + ["使用者加的一行"]), text, 0), lines[0])
            for pane in (True, False):
                ch = Ch()
                bridge = tmux_bridge.TmuxBridge(Host(ch))
                if pane:
                    bridge.host.chan(ch)["pane"] = {"socket": "/tmp/x", "id": "%1"}
                fake = Screens([box("❯ ")], echo=True)
                plain = fake.raw_screen

                async def raw_screen(name, fake=fake, plain=plain, view=view):
                    if fake.typed_now is not None:
                        fake.last = view
                        return view
                    return await plain(name)
                fake.raw_screen = raw_screen

                async def nothing(*a, **k):
                    pass

                class Message(object):
                    channel, id, content = ch, 1, text
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    run(bridge.submit(Message(), text), fake, mock.patch.object(bridge, "ensure", nothing),
                        mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01))
                said = "\n".join(m.content for m in ch.sent)
                self.assertEqual([k for k in fake.log if k[0] == "key"], [("key", "Enter")], (lines[0], pane))
                self.assertEqual(said.count("沒有按 Enter") + said.count("沒有送出"), 0, (lines[0], pane))
                self.assertEqual(out.getvalue(), "", "not counted as a box that differed")


class SendNowKnown(unittest.TestCase):
    """Manager 2026-10-09: the keys only when the screen shows the hint AND the bot knows a message of its own
    waits there. A reply ending in the hint's very words, on an idle session, had Ctrl+X Ctrl+S pressed, which
    sent off the owner's half-typed text."""

    QUEUED = "\n".join(["● working", "❯ queued one", "  ctrl+x ctrl+s to send now", "✻ Working… (5s)", RULE,
                        "❯ Press up to edit queued messages", RULE, "  esc to interrupt", ""])
    WORDS = "\n".join(["● the hint reads:", "  ctrl+x ctrl+s to send now", RULE, "❯ half typed by the owner", RULE, "  ? for shortcuts", ""])

    def pressed(self, screen, was_busy, got=False):
        keys = []

        async def raw_screen(t):
            return screen

        async def press(t, *names):
            keys.append(names)
        p = mock.Mock()
        p.got.is_set.return_value = got
        bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        with fakepane.replace("raw_screen", raw_screen), fakepane.replace("keys", press), \
                mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(bridge.send_now(mock.AsyncMock(), "s", p, was_busy))
        return keys

    def test_both_must_hold(self):
        self.assertTrue(screen.send_now_shown(self.WORDS), "the screen alone cannot tell this one")
        self.assertEqual(self.pressed(self.WORDS, was_busy=False), [], "idle when it was sent: nothing of the bot's waits")
        self.assertEqual(self.pressed(self.QUEUED, was_busy=False), [])
        self.assertEqual(self.pressed(self.QUEUED, was_busy=True, got=True), [], "its receipt came: taken already")
        self.assertEqual(self.pressed(self.QUEUED, was_busy=True), [("C-x", "C-s")])
        self.assertEqual(self.pressed(box("❯ "), was_busy=True), [], "the bot has one waiting, the screen shows no hint yet")

    def test_submit_tells_send_now_whether_a_turn_ran(self):
        for busy in (True, False):
            ch = Ch()
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            bridge.host.chan(ch)["busy"] = busy
            seen = []

            async def send_now(message, name, p=None, was_busy=None):
                seen.append(was_busy)

            async def nothing(*a, **k):
                pass

            class Message(object):
                channel, id, content = ch, 1, "hello"
            fake = Screens([box("❯ ")], echo=True)
            with mock.patch.object(bridge, "send_now", send_now):
                run(bridge.submit(Message(), "hello", now=True), fake, mock.patch.object(bridge, "ensure", nothing),
                    mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01))
            self.assertEqual(seen, [busy])


class CursorCell(unittest.TestCase):
    def test_the_cell_under_the_cursor_is_not_what_tells_grey(self):
        """#code-review-2 2026-10-09: with the focus on the terminal the suggestion's first character sits in the
        inverse cell of the cursor, not grey: the suggestion was taken for typed text and the message held."""
        for row, dim in (("❯\xa0\x1b[7mY\x1b[27m\x1b[2mes, compare", True), ("❯\xa0\x1b[7mY\x1b[0m\x1b[2mes, compare", True),
                         ("❯\xa0\x1b[2m\x1b[7mY\x1b[27mes, compare", True), ("❯\xa0\x1b[7mY\x1b[0m\x1b[38;5;245mes", True),
                         ("❯\xa0\x1b[7mh\x1b[27malf typed", False), ("❯\xa0\x1b[7mh\x1b[0malf typed", False),
                         ("❯\xa0half typed\x1b[7m \x1b[27m", False), ("❯\xa0\x1b[7mY\x1b[27m", False),
                         ("❯\xa0\x1b[2;7mY\x1b[27mes", True), ("❯\xa0\x1b[7mA\x1b[27m \x1b[2mplan", True)):
            self.assertEqual(screen.input_dim("%s\n%s\n%s" % (RULE, row, RULE)), dim, repr(row))

    def test_a_suggestion_under_the_cursor_is_typed_over_in_an_attached_pane(self):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        bridge.host.chan(ch)["pane"] = {"socket": "/tmp/x", "id": "%1"}

        async def nothing(*a, **k):
            pass

        class Message(object):
            channel, id, content = ch, 1, "hello"
        fake = Screens([box("❯ Yes, compare")], color="%s\n\x1b[39m❯\xa0\x1b[7mY\x1b[27m\x1b[2mes, compare\n%s" % (RULE, RULE),
                       echo=True)
        run(bridge.submit(Message(), "hello"), fake, mock.patch.object(bridge, "ensure", nothing),
            mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01))
        self.assertIn(("typed", "hello"), fake.log)
        self.assertNotIn("沒有送出", "\n".join(m.content for m in ch.sent))


class TypedAsPaste(unittest.TestCase):
    """Manager 2026-10-09: should a new Claude Code take the paced typing for a paste again, the bot's log and
    doctor say so; nothing else changes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        self.file = os.path.join(self.tmp, "state", "typed-as-paste.json")

    def noted(self, text, shown):
        fake = Screens([shown])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):  # run() stops every patch when it is done: the state folder is patched each time
            run(self.bridge.note_paste("s", text), fake, mock.patch.object(tmux_bridge.config, "STATE", os.path.join(self.tmp, "state")))
        return out.getvalue()

    def test_a_long_line_shown_as_a_paste_is_logged_and_counted(self):
        logged = self.noted("x" * 900 + "\nend", box("❯ [Pasted text #1]   end"))
        self.assertIn("[paste] typed text taken for a paste (904 characters, longest line 900", logged)
        with open(self.file) as f:
            seen = json.load(f)
        self.assertEqual((seen["count"], seen["chars"]), (1, 904))
        self.noted("x" * 900, box("❯ [Pasted text #2]"))
        with open(self.file) as f:
            self.assertEqual(json.load(f)["count"], 2)

    def test_typed_as_typed_or_short_says_nothing(self):
        self.assertEqual(self.noted("x" * 900, box("❯ " + "x" * 50)), "")
        self.assertEqual(self.noted("short", box("❯ [Pasted text #1]")), "", "a paste of the owner's beside a short message")
        self.assertEqual(self.noted("many\n" * 300, box("❯ [Pasted text #1 +3 lines]")), "", "no line long enough to be taken so")
        self.assertFalse(os.path.exists(self.file))

    def test_a_message_that_spells_the_mark_out_is_not_taken_for_one(self):
        """2026-10-09, the first long message after the pacing went live asked "…或顯示成 [Pasted text]": the
        check looked for those words anywhere in the box and counted a paste that never was."""
        asked = "請回覆有沒有顯示成 [Pasted text] 或 [Pasted text #1]：" + "字" * 900
        self.assertEqual(self.noted(asked, box("❯ " + asked[:100])), "")
        self.assertEqual(self.noted(asked, box("❯ 請回覆有沒有顯示成 [Pasted text] 或 [Pasted text #1]：字字字")), "")
        self.assertFalse(os.path.exists(self.file))
        self.assertIn("[paste]", self.noted(asked, box("❯ 請回覆有沒有顯示成 [Pasted text] 或 [Pasted text #1]：[Pasted text #2]")),
                      "one more mark than the message spells out is Claude Code's own")
        self.assertEqual(self.noted("x" * 900, box("❯ [Pasted text] x")), "", "not Claude Code's mark: it is numbered")

    def test_it_never_stops_the_message(self):
        with fakepane.replace("raw_screen", side_effect=RuntimeError("boom")), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(tmux_bridge.config, "STATE", os.path.join(self.tmp, "state")):
            asyncio.run(self.bridge.note_paste("s", "x" * 900))


class ClearInput(unittest.TestCase):
    def clear(self, screens):
        fake = Screens(screens)
        bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        with mock.patch.object(asyncio, "sleep", mock.AsyncMock()):
            return run(bridge.clear_input("s"), fake)

    def test_three_results(self):
        self.assertEqual(self.clear([box("❯ abc"), box("❯ ")]), "empty")
        self.assertEqual(self.clear([box("❯ 灰色")]), "unchanged")
        self.assertEqual(self.clear([box("❯ %d" % i) for i in range(100)]), "stuck")


class InputBox(unittest.TestCase):
    def test_input_text(self):
        self.assertIsNone(screen.input_text(PANEL))
        self.assertEqual(screen.input_text(box("❯ ")), "")
        self.assertEqual(screen.input_text(box('❯ Try "fix the bug"')), "")
        self.assertEqual(screen.input_text(box("❯ /compact ")), "/compact")

    def test_colour_values_are_not_taken_for_attributes(self):
        """2 and 90 inside a colour (true colour 38;2;R;G;B, 256-colour 2 and 90, a background) are not dim."""
        for sgr in ("38;2;255;255;255", "38;2;80;120;255", "38;5;2", "38;5;90", "48;2;10;20;30"):
            self.assertFalse(screen.input_dim("❯ \x1b[%sm/compact" % sgr), sgr)
        self.assertTrue(screen.input_dim("❯ \x1b[38;2;153;153;153m建議"), "true-colour grey")
        self.assertTrue(screen.input_dim("❯ \x1b[2;39m建議"), "dim stays through a default colour")

    def test_real_input_rows_from_a_mac_pane(self):
        """Captured with `tmux capture-pane -e` on the Mac, 2026-10-06, Claude Code in tmux with COLORTERM=truecolor
        and without (the same codes both times: inside tmux it draws with 256 colours)."""
        empty = '\x1b[39m❯\xa0 \x1b[2mTry "how does <filepath> work?"\x1b[0m'
        compact = ("\x1b[39m❯\xa0 \x1b[38;5;153m/compact\x1b[39m  "
                   "\x1b[38;5;246m<optional custom summarization instructions>\x1b[39m")
        typed = "\x1b[39m❯\xa0 half typed text"
        self.assertTrue(screen.input_dim(empty))
        self.assertFalse(screen.input_dim(compact), "a typed slash command is coloured, not grey")
        self.assertFalse(screen.input_dim(typed))

    def test_grey_text(self):
        self.assertTrue(screen.input_dim('\x1b[39m❯\xa0 \x1b[2mTry "x"'))
        self.assertTrue(screen.input_dim("❯ \x1b[38;5;245m建議"))
        self.assertFalse(screen.input_dim("\x1b[39m❯\xa0 \x1b[38;5;153m/compact\x1b[39m"))
        self.assertFalse(screen.input_dim("❯ 打的字"))


class Tables(unittest.TestCase):
    """The two tables of harmful slash commands, built since stage 3b step 2.1 from the connector's names and codes
    and the bot's words: to the letter what they were when they were written out here."""

    def test_the_tables_are_what_they_were(self):
        self.assertEqual(tmux_bridge.BLOCKED, {"/logout": "會登出這台機器上所有的 Claude Code 對話"})
        self.assertEqual(tmux_bridge.CONFIRM, {
            "/exit": "會結束這個對話（頻道會對著一個空的終端機）", "/quit": "會結束這個對話（頻道會對著一個空的終端機）",
            "/background": "對話會離開這個 tmux 窗格，頻道會對著一個空的終端機",
            "/fork": "對話會離開這個 tmux 窗格，頻道會對著一個空的終端機",
            "/teleport": "對話會離開這個 tmux 窗格，頻道會對著一個空的終端機",
            "/remote-control": "對話會離開這個 tmux 窗格，頻道會對著一個空的終端機",
            "/rewind": "會打開「還原對話和程式碼」的面板（選錯會蓋掉檔案）",
            "/checkpoint": "會打開「還原對話和程式碼」的面板（選錯會蓋掉檔案）"})
        self.assertEqual(list(tmux_bridge.CONFIRM), ["/exit", "/quit", "/background", "/fork", "/teleport", "/remote-control", "/rewind", "/checkpoint"])

    def test_every_code_has_words_and_one_that_has_none_is_shown_as_it_is(self):
        from altention.connectors.claude_code import commands
        self.assertEqual(set(commands.REFUSED.values()) | set(commands.CONFIRM.values()), set(tmux_bridge.HARM))
        with mock.patch.dict(commands.CONFIRM, {"/newone": "eats_the_disk"}), mock.patch.dict(commands.REFUSED, {"/worse": "x_y"}):
            blocked, confirm = tmux_bridge.harm_tables()
        self.assertEqual((confirm["/newone"], blocked["/worse"]), ("（eats_the_disk）", "（x_y）"), "a connector ahead of the bot: still refused, still asked first")
        self.assertEqual(tmux_bridge.harm_tables(), (tmux_bridge.BLOCKED, tmux_bridge.CONFIRM))


if __name__ == "__main__":
    unittest.main()
