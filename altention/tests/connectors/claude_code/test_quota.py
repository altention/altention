"""C-99a: Claude Code's own wait at a usage limit, read off the screen (screen.quota_wait) and its Notifications
(hooks.normalize). The lines are the interactive-mode docs' and 2.1.310's strings, put under the box of a real idle
screen; the words quoted anywhere else on the screen (this project's channels talk of them) are no wait."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))))
import unittest  # noqa: E402

from altention.connectors.claude_code import hooks, screen  # noqa: E402
from altention.tests.connectors.claude_code import samples  # noqa: E402

ARMED = ("Usage limit reached · limit resets 5:00am · /usage for details",
         "Continuing automatically at 5:00am · esc to cancel · /usage-credits to continue now")
SOON = ("Usage limit reached · limit resets 5:00am", "Continuing shortly · esc to cancel")
STALE = ("Your usage limit has reset", "Press enter to continue")


def under(lines, state="idle-after-turns", which="nocolor-120x50"):
    """A real idle screen whose rows under the box are `lines` instead of its status line."""
    rows = samples.screen(state, which).rstrip("\n").split("\n")
    return "\n".join(rows[:-1] + ["  " + x for x in lines]) + "\n"


def above(lines, state="idle-after-turns", which="nocolor-120x50"):
    """The same screen with `lines` in the conversation, over the box."""
    rows = samples.screen(state, which).rstrip("\n").split("\n")
    span = screen.box_span("\n".join(rows), shell=True)
    at = span[0] - 1
    return "\n".join(rows[:at - len(lines)] + ["⏺ " + x for x in lines] + rows[at:]) + "\n"


class Screen(unittest.TestCase):
    def test_armed_soon_stale(self):
        self.assertEqual(screen.quota_wait(under(ARMED)), {"phase": "armed", "when": "at 5:00am", "resets": "5:00am"})
        self.assertEqual(screen.quota_wait(under(SOON)), {"phase": "soon", "when": "", "resets": "5:00am"})
        self.assertEqual(screen.quota_wait(under(STALE)), {"phase": "stale", "when": "", "resets": ""})
        self.assertEqual(screen.quota_wait(under(("Continuing automatically when it resets · esc to cancel",))),
                         {"phase": "armed", "when": "when it resets", "resets": ""})

    def test_also_on_the_small_screen(self):
        self.assertEqual(screen.quota_wait(under(ARMED, which="nocolor-80x24"))["phase"], "armed")

    def test_the_words_elsewhere_are_no_wait(self):
        for lines in (ARMED, SOON, STALE):
            self.assertIsNone(screen.quota_wait(above(lines)), lines)
        self.assertIsNone(screen.quota_wait(samples.screen("idle-after-turns", "nocolor-120x50")))
        self.assertIsNone(screen.quota_wait(under(("Press enter to continue",))), "without its first line: not the wait")
        self.assertIsNone(screen.quota_wait(""))

    def test_waiting_is_still_the_ordinary_box(self):
        """The bot types into the box only when it is the ordinary one; the wait's 'esc to cancel' is no panel."""
        self.assertEqual(screen.pane_state(under(ARMED)), "box")
        self.assertFalse(screen.panel_shown(under(ARMED)))


REAL = _os.path.join(samples.HERE, "2.1.310", "quota")


def real(name):
    with open(_os.path.join(REAL, name + ".txt"), encoding="utf-8") as f:
        return f.read()


class RealScreens(unittest.TestCase):
    """2.1.310 at a weekly limit, captured on 2026-10-26 (screens/2.1.310/quota/README.txt)."""

    def test_armed_and_soon_as_captured(self):
        self.assertEqual(screen.quota_wait(real("armed")), {"phase": "armed", "when": "at 5am", "resets": "5am"})
        self.assertEqual(screen.quota_wait(real("soon")), {"phase": "soon", "when": "", "resets": "5am"})

    def test_its_own_line_in_the_conversation_is_not_what_is_read(self):
        rows = real("armed").split("\n")
        box = next(i for i, r in enumerate(rows) if r.startswith("❯"))
        self.assertIsNone(screen.quota_wait("\n".join(rows[:box + 2]) + "\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"),
                          "the conversation's 'Usage limit reached · continuing automatically at 5am' alone: no wait read")

    def test_a_conversation_quoting_the_lines_is_no_wait(self):
        self.assertIsNone(screen.quota_wait(real("quoted")))

    def test_the_bot_may_type_while_it_waits(self):
        for name in ("armed", "soon"):
            self.assertEqual(screen.pane_state(real(name)), "box", name)
            self.assertFalse(screen.panel_shown(real(name)), name)
            self.assertFalse(screen.busy_shown(real(name)), name)


class Normalize(unittest.TestCase):
    def kind(self, ev):
        return hooks.normalize(dict(ev, hook_event_name="Notification"))["dc_kind"]

    def test_the_three_types(self):
        self.assertEqual(self.kind({"notification_type": "quota_auto_resume_fired"}), hooks.QUOTA_RESUMED)
        self.assertEqual(self.kind({"notification_type": "quota_auto_resume_stale"}), hooks.QUOTA_STALE)
        self.assertEqual(self.kind({"notification_type": "quota_auto_resume_disabled"}), hooks.QUOTA_STOPPED)

    def test_other_notifications_as_before(self):
        self.assertEqual(self.kind({"notification_type": "permission_prompt", "message": "Claude needs your permission"}), hooks.DIALOG)
        self.assertEqual(self.kind({"notification_type": "idle_prompt"}), hooks.IDLE)

    def test_its_prompt_at_the_reset_is_its_own_notice(self):
        ev = hooks.normalize({"hook_event_name": "UserPromptSubmit", "prompt": "Your claude.ai usage limit has reset. Continue the "
                              "task you were working on when the limit was reached; do not repeat work that is already complete."})
        self.assertEqual((ev["dc_kind"], ev["dc_from"]), (hooks.INPUT, hooks.NOTICE))
        ev = hooks.normalize({"hook_event_name": "UserPromptSubmit", "prompt": "Your claude.ai usage limit has a question"})
        self.assertEqual(ev["dc_from"], hooks.TYPED)

    def test_a_rate_limit_failure_is_still_a_failed_turn(self):
        ev = hooks.normalize({"hook_event_name": "StopFailure", "error": "rate_limit"})
        self.assertEqual((ev["dc_kind"], ev["dc_why"]), (hooks.TURN_FAILED, "rate_limit"))


if __name__ == "__main__":
    unittest.main()
