"""Three places still looked for fixed words on the whole screen (architecture review 2.3; #code-review-2 ran each
on the real Claude Code 2.1.296 on 2026-10-11): the /btw answer, "x to clear history", and the dialogs Claude Code
opens with. Each is now read only where Claude Code draws it. The screens here are the real ones of
screens/2.1.296 with the conversation over them, or the words of the answer, changed to what was run."""
import unittest

from altention.connectors.claude_code import screen

from . import samples

ANSWER = "The side answer: nothing is wrong."
WHOLE = ("The side answer: nothing is wrong.\n\nA second paragraph of the answer, long enough to wrap onto a second row of the "
         "overlay when the pane is 120 columns wide, and then some more words.")
LOOKALIKE = ["● The answer box looks like this:", "", "    /btw what is it?", "", "      An answer quoted in a reply.", "",
             "    ↑/↓ to scroll · c to copy · f to fork · Esc to close", ""]


def over(view, rows):
    """`view` with `rows` as the last of the conversation: over the answer box (its "▔" edge), over the input
    box's rule, or over the rule a dialog starts with. The screen keeps its height: the rows before them go."""
    lines = view.split("\n")
    edge = max([i for i, r in enumerate(lines) if r.startswith("▔▔▔")] or [0])
    span = screen.box_span(view)
    rules = [i for i, r in enumerate(lines) if screen.is_box_rule(r)]
    at = edge if edge else (span[0] - 1 if span else rules[-1])
    if at < len(rows):
        return None  # no room over it in this sample (a dialog that starts high on a small pane)
    return "\n".join(lines[:at - len(rows)] + list(rows) + lines[at:])


def each(state):
    for which in samples.SETS:
        yield which, samples.screen(state, which)


class AnswerBox(unittest.TestCase):
    def test_the_real_boxes(self):
        for which, view in each("btw-answer"):
            self.assertEqual(screen.btw_answer(view, int(which.split("-")[1].split("x")[0])), WHOLE, which)
        for state in ("btw-answering", "btw-answering-busy"):
            for which, view in each(state):
                self.assertEqual(screen.btw_answer(view), "", (state, which))
        for state in samples.STATES:
            if not state.startswith("btw-a"):
                for which, view in each(state):
                    self.assertIsNone(screen.btw_box(view), (state, which))

    def test_an_answer_that_says_answering_is_an_answer(self):
        """Was: taken for one still on its way until the time was up, then its box closed, the answer never posted."""
        for which, view in each("btw-answer"):
            said = view.replace(ANSWER, "While it waits the box shows · Answering…")
            self.assertTrue(screen.btw_answer(said).startswith("While it waits the box shows · Answering…"), which)
            row = view.replace("      " + ANSWER, "      · Answering…")  # a row of the answer that is the very row
            self.assertTrue(screen.btw_answer(row).startswith("· Answering…\n\nA second paragraph"), which)

    def test_an_answer_that_says_esc_to_close_is_read_to_its_end(self):
        """Was: cut at the first row holding the words (an attached pane, read off the screen)."""
        for which, view in each("btw-answer"):
            said = view.replace(ANSWER, "Press Esc to close the box.")
            answer = screen.btw_answer(said)
            self.assertTrue(answer.startswith("Press Esc to close the box.\n\nA second paragraph"), which)
            self.assertTrue(answer.endswith("words."), which)

    def test_a_conversation_that_quotes_the_box(self):
        """Was: the quoted rows were the answer ("（沒有內容）" a second after sending), and the real box, still on
        its way, was closed."""
        for which, view in each("btw-answering"):
            self.assertEqual(screen.btw_answer(over(view, LOOKALIKE)), "", which + ": the real one is still on its way")
        for which, view in each("btw-answer"):
            self.assertTrue(screen.btw_answer(over(view, LOOKALIKE)).startswith(ANSWER), which)
        for state in ("idle-after-turns", "busy", "idle-typed", "model-panel", "permission"):
            seen = [(which, over(view, LOOKALIKE)) for which, view in each(state)]
            self.assertTrue(any(v for _, v in seen), state)
            for which, quoted in seen:
                if quoted:
                    self.assertIn("An answer quoted in a reply.", quoted)
                    self.assertIsNone(screen.btw_answer(quoted), (state, which, "no answer box: nothing to read or close"))

    def test_a_message_of_the_owners_with_a_line_starting_btw(self):
        said = ["❯ two lines, the second one:", "  /btw is not a command here", "", "● Then the box would say Esc to close.", ""]
        for state in ("idle-after-turns", "busy"):
            for which, view in each(state):
                self.assertIsNone(screen.btw_answer(over(view, said)), (state, which))
        for which, view in each("btw-answering"):
            self.assertEqual(screen.btw_answer(over(view, said)), "", which)

    def test_a_row_starting_btw_over_the_box_is_not_part_of_the_answer(self):
        """Was: the answer posted with "/btw <question>" as its first line (an attached pane)."""
        for which, view in each("btw-answer"):
            self.assertTrue(screen.btw_answer(over(view, ["/btw quoted at the left edge", ""])).startswith(ANSWER), which)

    def test_an_edge_quoted_in_the_conversation(self):
        fake = ["▔" * 40, "", "    /btw quoted whole", "", "      A quoted answer.", "", "    c to copy · Esc to close", ""]
        for which, view in each("btw-answer"):
            self.assertTrue(screen.btw_answer(over(view, fake)).startswith(ANSWER), which + ": the last edge is the box's")
        for which, view in each("idle-after-turns"):
            self.assertIsNone(screen.btw_answer(over(view, fake)), which + ": the input box is there, so no answer box is")

    def test_the_hint_row_is_the_boxs_own(self):
        for state in samples.STATES:
            for which, view in each(state):
                clears = "x to clear history" in screen.btw_hint(view)
                self.assertEqual(clears, state in ("btw-answer-second", "btw-answering-busy", "btw-answer-busy"), (state, which))
        for which, view in each("btw-answer"):  # the first answer of a conversation: its hint does not offer it
            said = over(view.replace(ANSWER, "Press x to clear history."), ["● x to clear history is a key of the box.", ""])
            self.assertNotIn("x to clear history", screen.btw_hint(said), which)


class NarrowPane(unittest.TestCase):
    """Measured on 2.1.296 (2026-10-11, after #code-review asked): the hint of a second answer is 78 columns with
    its indent; at 70 columns it is drawn "… x to clear history ·" / "Esc to close", at 60 "… x to clear" /
    "history · Esc to close", the second row at the first one's indent. The screens here are the real 80x24 ones
    with that row drawn as it was seen there."""

    def narrow(self, state, first, second):
        rows = samples.screen(state, "colour-80x24").split("\n")
        last = max(i for i, r in enumerate(rows) if r.strip())
        self.assertEqual(rows[last].strip(), first + " " + second, "the sample's hint, whole on one row")
        return "\n".join(rows[1:last] + ["    " + first, "    " + second] + rows[last + 1:])

    def test_the_hint_on_two_rows(self):
        for first, second in (("⇧←/→ to browse · c to copy · f to fork · x to clear history ·", "Esc to close"),
                              ("⇧←/→ to browse · c to copy · f to fork · x to clear", "history · Esc to close"),
                              ("⇧←/→ to browse · c to copy · f to fork · x to clear history · Esc to", "close")):
            view = self.narrow("btw-answer-second", first, second)
            self.assertTrue(screen.btw_answer(view).startswith(ANSWER), first)
            self.assertTrue(screen.btw_answer(view).endswith("words."), "the hint's first row is not the answer's last")
            self.assertIn("x to clear history", screen.btw_hint(view), first)
            self.assertTrue(screen.btw_hint(view).endswith("Esc to close"))

    def test_still_answering_on_two_rows(self):
        view = self.narrow("btw-answering-busy", "⇧←/→ to browse · x to clear history ·", "Esc to close")
        self.assertEqual(screen.btw_answer(view), "")

    def test_an_answer_whose_last_row_is_followed_by_no_empty_one_is_no_box(self):
        rows = samples.screen("btw-answer", "colour-80x24").split("\n")
        last = max(i for i, r in enumerate(rows) if r.strip())
        del rows[last - 1]  # never seen: the row between the answer and the hint is always there
        self.assertIsNone(screen.btw_answer("\n".join(rows)), "its rows are not taken for the hint, nor the hint for its rows")


class Waiting(unittest.TestCase):
    def test_the_spinner_row_whatever_its_glyph(self):
        """Measured on 2.1.296: "· Answering…", "✽ Answering…", "* Answering…" at 2, 12 and 22 seconds; no time
        after it."""
        for glyph in ("·", "✽", "*", "✢", "✶"):
            for which, view in each("btw-answering"):
                self.assertEqual(screen.btw_answer(view.replace("· Answering…", glyph + " Answering…")), "", (glyph, which))

if __name__ == "__main__":
    unittest.main()
