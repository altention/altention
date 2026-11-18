"""Claude Code's input box when what is in it is taller than the box (C-202): 2.1.326 scrolls it, and the rule over
it reads "─── ↑ N more ───" while the row under that rule is the message's own (no "❯"). Before C-202 no box was
found there, the screen was read as a dialog and the bot's long message (1723 characters, 40 rows and more) was left
in the box unsent. The screens are real captures of 2.1.326 at 120x40 and 80x24 (screens/2.1.326/extra/M/, each
with its meta.json saying how it was taken), unchanged."""
import unittest

from altention.connectors.claude_code import screen
from altention.tests.connectors.claude_code import samples

VERSION = "2.1.326"
SIZES = ("120x40", "80x24")
TYPED = "\n".join("第 %d 行：這是一段很長的跨頻道訊息內容，用來重現輸入框捲動的情況 abc def" % i for i in range(45))  # what was typed


def shown(state, size):
    return samples.extra("M", "more-%s-%s" % (state, size), version=VERSION)


class Scrolled(unittest.TestCase):
    def test_every_sample_says_how_it_was_taken(self):
        names = samples.extra_states("M", version=VERSION)
        self.assertEqual(len(names), 8)
        for name in names:
            meta = samples.extra_meta("M", name, version=VERSION)
            self.assertTrue(meta["claude_code"].startswith(VERSION), name)
            self.assertNotIn("/tmp/ccd.", samples.extra("M", name, version=VERSION))

    def test_a_box_scrolled_to_its_end_is_the_box_with_the_message_typed(self):
        for size in SIZES:
            view = shown("typed", size)
            self.assertIn("↑", view)
            self.assertEqual(screen.pane_state(view), "box", size)
            self.assertTrue(screen.has_prompt(view), size)
            self.assertIn("第 44 行", screen.input_text(view), size)
            self.assertTrue(screen.bot_typed(view, TYPED, screen.paste_marks(TYPED)), size)

    def test_what_stands_under_the_box_is_claude_codes_own_rows_only(self):
        """Read as no box, under_box() was the message's rows: a message saying "Esc to cancel" would have been a
        dialog's hint."""
        for size in SIZES:
            for state in ("typed", "cursor-middle", "cursor-top"):
                under = screen.under_box(shown(state, size))
                self.assertNotIn("第", under, (state, size))
                self.assertIn("manual mode on", under, (state, size))

    def test_rules_saying_more_over_and_under_it(self):
        for size in SIZES:
            view = shown("cursor-middle", size)
            self.assertIn("↓", view)
            self.assertEqual(screen.pane_state(view), "box", size)
            self.assertFalse(screen.bot_typed(view, TYPED, screen.paste_marks(TYPED)),
                             "its end is not on the screen: not shown to be the bot's text")

    def test_its_first_rows_shown_and_after_enter(self):
        for size in SIZES:
            self.assertEqual(screen.pane_state(shown("cursor-top", size)), "box", size)
            self.assertTrue(screen.input_text(shown("cursor-top", size)).startswith("第 0 行"), size)
            self.assertEqual(screen.input_text(shown("entered", size)), "", size)

    def test_only_the_rule_at_the_screens_edge(self):
        """A message in the box quoting such a rule stands indented: no box starts under it (the real box's own
        "❯" row above is the box)."""
        rule = "─" * 60
        view = "\n".join(["", rule, "❯ look:", "  ─── ↑ 3 more ──────────────────────", "  next row", rule, "  ? for shortcuts", ""])
        self.assertEqual(screen.box_span(view)[0], 2)
        self.assertFalse(screen.scrolled_rule("  ─── ↑ 3 more ──────────────────────"))
        self.assertTrue(screen.scrolled_rule("─── ↑ 3 more ──────────────────────"))
        self.assertFalse(screen.scrolled_rule("─── ↓ 3 more ──────────────────────"), "the rule under it: the closing one")
        self.assertFalse(screen.scrolled_rule("─── ↑ 3 more ─ and words after it"),
                         "at the edge, but a row of text, not a rule (#code-review on cf23197b)")


if __name__ == "__main__":
    unittest.main()
