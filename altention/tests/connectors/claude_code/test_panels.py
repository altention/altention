"""A panel's hint line and its rows read as data (panels.py): only the keys the line names, never numbers; and the
numbered menu of a dialog."""
import unittest

from altention.connectors.claude_code import panels

MODEL = "Enter to set as default · s to use this session only · Esc to cancel"
CONFIG = "Type to filter · Enter/↓ to select · ↑ to tabs · Esc to clear"
PERMISSIONS = "←/→ to switch · ↓ to select · Esc to cancel"
RESUME = "Ctrl+A to show all projects · Ctrl+B to only show current branch · Type to search · Esc to cancel"
RULE = "─" * 60


def panel(*rows):
    return "\n".join([""] + list(rows) + [""])


class Hint(unittest.TestCase):
    def keys(self, line):
        return [a["keys"] for a in panels.hint_actions(line)]

    def test_real_hint_lines(self):
        self.assertEqual(self.keys(MODEL), [["Enter"], ["s"], ["Escape"]])
        self.assertEqual(self.keys(CONFIG), [["Enter"], ["Up"], ["Escape"]], "Type is not a key; Enter/↓ is one action")
        self.assertEqual(self.keys(PERMISSIONS), [["Left"], ["Right"], ["Down"], ["Escape"]])
        self.assertEqual(self.keys(RESUME), [["C-a"], ["C-b"], ["Escape"]])
        self.assertEqual(panels.hint_actions(MODEL)[1]["label"], "s use this session only")

    def test_not_hint_lines(self):
        for line in ("  1. Default (recommended)", "↓ 14 more", "Push when actions required   false", ""):
            self.assertEqual(panels.hint_actions(line), [], line)

    def test_numbers_are_never_offered(self):
        view = panel(" Select model", " ❯ 1. Default", "   2. Opus", "", MODEL)
        keys = [k for a in panels.parse_panel(view, False)["actions"] for k in a["keys"]]
        self.assertFalse(any(k.isdigit() for k in keys))


MODEL_SCREEN = """   Select model
   Switch between Claude models. Your pick becomes the default for new sessions. For other/previous model names,
   ❯ 1.  Default (recommended) ✔  Opus 5.5 · Best for everyday, complex tasks
     2.  Opus 5.5                 For complex work and everyday tasks
     5.  Haiku 4.5                Fastest for quick answers
   ↓ 10. Opus 4.7                 Best for everyday, complex tasks
      … +2 models
   ◐ Medium effort (default) ←/→ to adjust
   Use /fast to turn on Fast mode (Opus 5.5).
   Enter to set as default · s to use this session only · Esc to cancel
"""  # captured 2026-10-06 (Claude Code 2.1.291), rows trimmed


class Panel(unittest.TestCase):
    def test_the_model_picker_can_be_worked_with_buttons_alone(self):
        """Move the cursor, adjust effort, confirm either way, cancel: all from the screen itself."""
        keys = [a["keys"] for a in panels.parse_panel(MODEL_SCREEN, False)["actions"]]
        self.assertEqual(keys, [["Up"], ["Down"], ["Left"], ["Right"], ["Enter"], ["s"], ["Escape"]])

    def test_arrows_in_running_text_are_left_alone(self):
        self.assertEqual(panels.arrow_tail("   ◐ Medium effort (default) ←/→ to adjust"),
                         [{"label": "← adjust", "keys": ["Left"]}, {"label": "→ adjust", "keys": ["Right"]}])
        self.assertEqual(panels.arrow_tail("Opus 5.5 · Best for everyday, complex tasks"), [])
        self.assertEqual(panels.arrow_tail("we want to go to the shop"), [])

    def test_a_list_without_a_cursor_gets_no_moves(self):
        content = panels.parse_panel(panel(" Pick", "   1. a", "   2. b", "", "Esc to cancel"), False)
        self.assertEqual([a["keys"] for a in content["actions"]], [["Escape"]])

    def test_moves_named_by_the_hint_are_not_added_twice(self):
        content = panels.parse_panel(panel(" ❯ Tab one", "", PERMISSIONS), False)
        self.assertEqual([a["keys"] for a in content["actions"]], [["Up"], ["Left"], ["Right"], ["Down"], ["Escape"]])

    def test_a_panel_with_its_hint(self):
        content = panels.parse_panel(panel(" Select model", " ❯ 1. Default", "", MODEL), False)
        self.assertIn("Select model", content["text"])
        self.assertEqual(len(content["actions"]), 5, "the hint's three, and ↑ ↓ for the cursor row")

    def test_the_input_box_on_screen_is_no_panel_whatever_the_status_line_says(self):
        self.assertIsNone(panels.parse_panel(panel(RULE, "❯ ", RULE, "  ? for shortcuts"), True))
        status = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · 1 shell · ↓ to manage"
        self.assertIsNone(panels.parse_panel(panel("Esc to cancel, said Claude", RULE, "❯ ", RULE, status), True))

    def test_the_owners_messages_above_a_panel_are_no_cursor(self):
        """Seen on 2026-10-06: "❯ 幫我看一下用量" (a message in the conversation) above /usage gave ↑ ↓."""
        view = panel("❯ 幫我看一下用量", "● 好的", RULE, " Usage", " Current session  3%", "", "Esc to cancel")
        self.assertEqual([a["keys"] for a in panels.parse_panel(view, False)["actions"]], [["Escape"]])

    def test_claude_at_work_is_no_panel(self):
        self.assertIsNone(panels.parse_panel(panel("✻ Working… (esc to interrupt)", "Esc to cancel"), False))

    def test_a_tall_panel_without_its_hint_has_no_actions(self):
        content = panels.parse_panel(panel("Usage", "Skills, subagents, plugins", "No attribution data yet"), False)
        self.assertEqual(content["actions"], [])


PLAN = """● Earlier output
────────────────────────────────────────
 Ready to code?
 Here is Claude's plan:
╌╌╌╌╌╌╌╌╌╌╌╌
 1. Create hello.txt
 2. Write hi into it
╌╌╌╌╌╌╌╌╌╌╌╌
────────────────────────────────────────
 Claude has written up a plan and is ready to execute. Would you like to proceed?
 ❯ 1. Yes, and switch to BYPASS PERMISSIONS (no further prompts) for this session
   2. Yes, manually approve edits
   3. Tell Claude what to change
      shift+tab to approve with this feedback
 ctrl+g to edit in Vim · ~/.claude/plans/x.md

"""


class Menu(unittest.TestCase):
    def test_the_dialog_options_not_the_plan_steps(self):
        excerpt, options = panels.parse_menu(PLAN)
        self.assertEqual(options, [(1, "Yes, and switch to BYPASS PERMISSIONS (no further prompts) for this session"),
                                   (2, "Yes, manually approve edits"), (3, "Tell Claude what to change")])
        self.assertIn("Would you like to proceed?", excerpt)
        self.assertNotIn("──", excerpt)

    def test_no_menu(self):
        excerpt, options = panels.parse_menu("● done\n────\n❯ \n────\n  ⏵⏵ bypass permissions on\n")
        self.assertEqual(options, [])
        self.assertIn("bypass permissions on", excerpt)
        self.assertEqual(panels.parse_menu("  2. only a second item\n  3. and a third\n")[1], [])
        reply = "● Plan:\n  1. Create bye.txt\n  2. Reply done\n" + "─" * 30 + "\n❯ \n" + "─" * 30 + "\n  ⏵⏵ bypass permissions on\n"
        self.assertEqual(panels.parse_menu(reply)[1], [], "a numbered list in Claude's reply is not a menu")

class AutoDeny(unittest.TestCase):
    """Claude Code 2.1.302 (real screens, screens/2.1.302/extra/P/): in auto mode a dangerous removal is asked about with a
    two-minute countdown row that changes every second (manager 2026-10-18: the bot's buttons never went out)."""
    SETS = ("colour-120x50", "colour-80x24", "nocolor-120x50", "nocolor-80x24")

    def screen(self, name):
        from altention.tests.connectors.claude_code import samples
        return samples.extra("P", name, version="2.1.302")

    def test_the_real_countdown_on_every_size_read_and_left_out(self):
        for which in self.SETS:
            with self.subTest(which=which):
                a, b = self.screen("countdown-%s-a" % which), self.screen("countdown-%s-b" % which)
                self.assertNotEqual(a, b, "the countdown moved between the two")
                left_a, left_b = panels.auto_deny(a), panels.auto_deny(b)
                self.assertTrue(100 <= left_b < left_a <= 120, (left_a, left_b))
                ea, oa = panels.parse_menu(a)
                eb, ob = panels.parse_menu(b)
                self.assertEqual((ea, oa), (eb, ob), "the excerpt leaves the countdown out")
                self.assertEqual([n for n, _ in oa], [1, 2])
                self.assertIn("Do you want to proceed?", ea)
                self.assertNotIn("automatically deny", ea)
                self.assertNotIn("unattended session", ea, "both rows of a wrapped countdown")
                self.assertIn("rm -rf ~", ea)

    def test_no_countdown_after_it_ran_out_or_on_other_dialogs(self):
        from altention.tests.connectors.claude_code import samples
        self.assertIsNone(panels.auto_deny(self.screen("auto-denied-nocolor-80x24")))
        for which in self.SETS:
            self.assertIsNone(panels.auto_deny(samples.screen("permission", which)), which)

    def test_a_narrow_pane_wraps_it_onto_three_rows(self):
        """2.1.310 at 52 columns (#activity-dev 2026-10-27): the reason in a │ box above, the row wrapped after "request",
        so its first row has no "in" (the buttons never went out: each second the dialog looked changed)."""
        from altention.tests.connectors.claude_code import samples
        a, b = (samples.extra("P", "countdown-colour-52x28-" + w, version="2.1.310") for w in "ab")
        self.assertIn("deny this request\n in 1:58,", a)
        self.assertEqual((panels.auto_deny(a), panels.auto_deny(b)), (118, 115))
        (ea, oa), (eb, ob) = panels.parse_menu(a), panels.parse_menu(b)
        self.assertEqual((ea, oa), (eb, ob), "the excerpt leaves all three rows out")
        self.assertEqual(oa, [(1, "Yes"), (2, "No")])
        self.assertNotIn("unattended session", ea)
        self.assertIn("│ Dangerous rm operation on", ea, "the reason's box stays")
        self.assertIsNone(panels.auto_deny(samples.extra("P", "auto-denied-colour-52x28", version="2.1.310")))

    def test_wrapped_at_any_width(self):
        import textwrap
        sentence = "⚠ Claude Code will automatically deny this request in 0:56, to avoid blocking progress on an unattended session"
        for width in range(30, 130):
            rows = textwrap.wrap(sentence, width)
            view = "\n".join(["─" * width, " Bash command", " " + "╌" * 20, " rm -rf ~", " " + "╌" * 20, " │ a reason"]
                             + [" " + r for r in rows] + ["", " Do you want to proceed?", " ❯ 1. Yes", "   2. No", "",
                                                          " Esc to cancel · Tab to amend", ""])
            self.assertEqual(panels.auto_deny(view), 56, (width, rows))
            self.assertNotIn("unattended", panels.parse_menu(view)[0], width)

    def test_the_screen_reader_wording_and_the_seconds(self):
        def dialog(row):
            return "\n".join(["─" * 40, " Bash command", " " + "╌" * 30, " rm -rf ~", " " + "╌" * 30, row,
                              " Do you want to proceed?", " ❯ 1. Yes", "   2. No", ""])
        tail = ", to avoid blocking progress on an unattended session"
        for when, secs in (("1:21", 81), ("0:05", 5), ("12:00", 720), ("about 2 minutes", 120), ("about 1 minute", 60),
                           ("about 30 seconds", 30), ("about 1 second", 1)):
            self.assertEqual(panels.auto_deny(dialog(" ⚠ Claude Code will automatically deny this request in " + when + tail)), secs, when)
        for row in (" ⚠ Claude Code will automatically deny this request in a while" + tail,  # no time in it
                    " ⚠ Claude Code will automatically deny this request in 1:99" + tail,
                    " Claude Code will automatically deny this request in 1:21" + tail,  # not the warning row
                    " ⚠ Claude Code will automatically deny this request in 1:21" + tail + " (quoted)"):
            self.assertIsNone(panels.auto_deny(dialog(row)), row)
            self.assertIn(row.strip(), panels.parse_menu(dialog(row))[0], "anything else is left where it is")

    def test_a_reply_above_a_dialog_that_quotes_the_words_is_no_deadline(self):
        """#code-review of ac72a14: a dialog with no dashed rules (the trust and bypass dialogs, real 2.1.296 screens),
        Claude's reply above it quoting the sentence on a row of its own: only rows below the dialog's top rule count."""
        from altention.tests.connectors.claude_code import samples
        quote = ["⏺ The dialog said:", "  ⚠ Claude Code will automatically deny this request in 1:59, to avoid blocking progress on an unattended session"]
        for state in ("trust-dialog", "bypass-dialog"):
            for which in self.SETS:
                view = samples.screen(state, which)
                self.assertIsNone(panels.auto_deny(view))
                quoted = "\n".join(quote + view.split("\n"))
                self.assertIsNone(panels.auto_deny(quoted), (state, which))
                self.assertEqual(panels.parse_menu(quoted), panels.parse_menu("\n".join(quote[:1] + [quote[1]] + view.split("\n"))))
        self.assertIsNone(panels.auto_deny("\n".join(quote + ["❯ 1. Yes", "  2. No"])), "no dialog rule at all: not taken")

    def test_a_command_that_quotes_the_words_keeps_them(self):
        """(review-words-on-screen-detectors) The words in the command itself, between its dashed rules, are the
        command: read as it is, never taken for the countdown."""
        quoted = " ⚠ Claude Code will automatically deny this request in 1:21, to avoid blocking progress on an unattended session"
        view = "\n".join(["─" * 40, " Bash command", " " + "╌" * 30, " printf '%s\\n' \\", quoted, " " + "╌" * 30,
                          " Do you want to proceed?", " ❯ 1. Yes", "   2. No", ""])
        self.assertIsNone(panels.auto_deny(view))
        self.assertIn(quoted.strip(), panels.parse_menu(view)[0])
        real = self.screen("countdown-colour-120x50-a")
        rows = real.split("\n")
        at = [i for i, r in enumerate(rows) if "rm -rf ~" in r and not r.lstrip().startswith("⎿")][-1]
        both = "\n".join(rows[:at + 1] + [quoted] + rows[at + 1:])  # the command quoting it, the real countdown below
        self.assertEqual(panels.auto_deny(both), panels.auto_deny(real))
        self.assertIn(quoted.strip(), panels.parse_menu(both)[0], "the quoted words stay")
        self.assertEqual(panels.parse_menu(both)[0].count("automatically deny"), 1, "only the countdown goes")


if __name__ == "__main__":
    unittest.main()
