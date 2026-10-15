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

if __name__ == "__main__":
    unittest.main()
