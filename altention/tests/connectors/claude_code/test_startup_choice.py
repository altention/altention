"""screen.startup_choice() and keys_for() on real screens of Claude Code 2.1.303 (screens/2.1.303/extra/I/): the
external-imports dialog it opens with is told, with its two answers and which one is picked; the dialogs the bot
answers itself (startup_key: trust, bypass), a numbered menu, a conversation and the same words inside one are not."""
import os
import unittest

from altention.connectors.claude_code import screen
from altention.tests.connectors.claude_code import samples

SETS = ("colour-120x50", "colour-80x24", "nocolor-120x50", "nocolor-80x24")
NO, YES = "No, disable external imports", "Yes, allow external imports"


def real(state):
    with open(os.path.join(samples.HERE, "2.1.303", "extra", "I", state + ".txt"), encoding="utf-8") as f:
        return f.read()


class StartupChoice(unittest.TestCase):
    def test_the_real_dialog_on_every_size(self):
        for which in SETS:
            with self.subTest(which=which):
                c = screen.startup_choice(real("imports-" + which))
                self.assertEqual((c["title"], c["options"], c["picked"]), ("Allow external CLAUDE.md file imports?", [NO, YES], 0))
                self.assertIn("outside the current working directory", " ".join(c["text"].split()))
                self.assertEqual(screen.keys_for(c, 1), ["Down", "Enter"])
                self.assertEqual(screen.keys_for(c, 0), ["Enter"])
                self.assertIsNone(screen.startup_key(real("imports-" + which)), "not one the bot answers itself")
                y = screen.startup_choice(real("imports-yes-picked-" + which))
                self.assertEqual((y["options"], y["picked"]), ([NO, YES], 1))
                self.assertEqual(screen.keys_for(y, 0), ["Up", "Enter"])
                self.assertIsNone(screen.startup_choice(real("imports-answered-" + which)))

    def test_not_the_others(self):
        for which in SETS:
            for state in ("trust-dialog", "bypass-dialog", "permission", "idle-fresh", "busy", "model-panel"):
                self.assertIsNone(screen.startup_choice(samples.screen(state, which)), (state, which))

    def test_the_words_inside_a_conversation_are_text(self):
        """(review-words-on-screen-detectors) A reply quoting the dialog, the input box below it: no dialog."""
        quoted = "\n".join(["● It asked:", "  Allow external CLAUDE.md file imports?", "  ❯ No, disable external imports",
                            "    Yes, allow external imports", "  Enter to confirm · Esc to cancel"])
        idle = samples.screen("idle-fresh")
        self.assertIsNone(screen.startup_choice(quoted + "\n" + idle))
